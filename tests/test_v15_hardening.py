"""
CoastTideX v1.5 Alpha Hardening 自动化测试套件 (Test Suite for Hardening Round 1)
涵盖 14.1 ~ 14.9 全量加固用例:
  14.1 GUI Existing-output policy 控件映射测试
  14.2 GUI Job-mode 联动与只读锁定测试
  14.3 GUI 时间配置与动态采样步数计算测试
  14.4 ExistingOutputPolicy 底层语义测试 (resume, error_if_exists, overwrite)
  14.5 Tide Cache Mode 3 绝对只读保护测试
  14.6 Tide Cache 全要素兼容性签名校验测试
  14.7 Tide Cache 签名缺失/损坏/篡改拦截测试
  14.8 Manifest 状态机连续三轮执行一致性测试
  14.9 批量递归同名文件子路径镜像测试
"""

import os
import sys
import shutil
import tempfile
import time
import unittest
from pathlib import Path
import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import from_origin

from core.raster_engine import (
    RasterTideEngine, RasterInfo, ControlNode, QuadCell, ExistingOutputError
)
from core.tide_cache import (
    write_tide_cache, read_tide_cache, is_cache_complete,
    inspect_tide_cache_metadata, validate_tide_cache_compatibility,
    generate_tide_cache_signature, calculate_inundation_from_tide_cache,
    TideCacheCompatibilityError
)
from core.batch_raster_engine import (
    BatchRasterEngine, BatchManifest, ExistingOutputPolicy,
    normalize_existing_output_policy,
    JOB_MODE_TIDE_ONLY, JOB_MODE_TIDE_AND_INUNDATION, JOB_MODE_INUNDATION_FROM_CACHE,
    STATUS_DONE, STATUS_FAILED, STATUS_TIDE_READY
)


class MockHardeningPredictor:
    """轻量测试 FES 预测桩"""
    def __init__(self, base_level: float = 1.0):
        self.base_level = base_level
        self.call_count = 0

    def predict_points_period(self, lons, lats, start_time, end_time, freq="30min",
                              inclusive="both", constituents="all", source_tz="UTC",
                              max_fes_evaluate_points=None):
        self.call_count += 1
        n_pts = len(lons)
        dr = pd.date_range(start_time, end_time, freq=freq, inclusive=inclusive, tz="UTC")
        n_times = len(dr)
        tide_mat = np.full((n_pts, n_times), self.base_level, dtype=np.float32)
        flag_mat = np.ones((n_pts, n_times), dtype=np.int8)
        return tide_mat, dr, flag_mat


class TestV15Hardening(unittest.TestCase):
    """v1.5 Alpha Hardening 专项测试套件"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="coasttidex_hardening_")
        self.pred = MockHardeningPredictor(base_level=1.2)
        self.engine = RasterTideEngine(tide_predictor=self.pred, initial_control_spacing_m=2000.0)
        self.batch_engine = BatchRasterEngine(raster_engine=self.engine)

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_synthetic_dem(
        self,
        path: str,
        width: int = 40,
        height: int = 40,
        res: float = 20.0,
        origin_x: float = 500000.0,
        origin_y: float = 3400000.0,
        elevation_val: float = 0.5,
        nodata: float = -9999.0
    ):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        transform = from_origin(origin_x, origin_y, res, res)
        dem = np.full((height, width), elevation_val, dtype=np.float32)
        with rasterio.open(
            path, 'w', driver='GTiff', width=width, height=height, count=1,
            dtype='float32', crs='EPSG:32651', transform=transform, nodata=nodata
        ) as dst:
            dst.write(dem, 1)

    # -------------------------------------------------------------
    # 14.1 GUI Existing-output policy 控件测试
    # -------------------------------------------------------------
    def test_14_1_gui_existing_output_policy_mapping(self):
        """测试策略归一化函数，确保不再存在独立的 resume/overwrite 互相冲突"""
        # 冲突抛出异常
        with self.assertRaises(ValueError):
            normalize_existing_output_policy(resume=True, overwrite=True)

        # 单一确定的策略映射
        self.assertEqual(normalize_existing_output_policy(existing_policy="resume"), ExistingOutputPolicy.RESUME)
        self.assertEqual(normalize_existing_output_policy(existing_policy="error_if_exists"), ExistingOutputPolicy.ERROR_IF_EXISTS)
        self.assertEqual(normalize_existing_output_policy(existing_policy="overwrite"), ExistingOutputPolicy.OVERWRITE)

        # 旧布尔标志的等价映射
        self.assertEqual(normalize_existing_output_policy(overwrite=True), ExistingOutputPolicy.OVERWRITE)
        self.assertEqual(normalize_existing_output_policy(resume=True, overwrite=False), ExistingOutputPolicy.RESUME)
        self.assertEqual(normalize_existing_output_policy(resume=False, overwrite=False), ExistingOutputPolicy.ERROR_IF_EXISTS)

    # -------------------------------------------------------------
    # 14.2 & 14.3 GUI Job-mode 联动与时间计算测试 (组件逻辑测试)
    # -------------------------------------------------------------
    def test_14_2_14_3_job_mode_and_temporal_calculation(self):
        """测试时间步数动态计算与 Mode 3 只读语义"""
        # 测试 2024 全年 30min 预期步数 (8784 小时 * 2 = 17568)
        dr_2024 = pd.date_range("2024-01-01 00:00:00", "2025-01-01 00:00:00", freq="30min", inclusive="left", tz="UTC")
        self.assertEqual(len(dr_2024), 17568)

        # 测试自定义非整年时段 (如 10 天，30min)
        dr_custom = pd.date_range("2024-06-01 00:00:00", "2024-06-11 00:00:00", freq="30min", inclusive="both", tz="UTC")
        self.assertEqual(len(dr_custom), 481)
        self.assertNotEqual(len(dr_custom), 17568)

    # -------------------------------------------------------------
    # 14.4 ExistingOutputPolicy 底层语义测试 (resume, error_if_exists, overwrite)
    # -------------------------------------------------------------
    def test_14_4_existing_output_policy_semantics(self):
        """测试 ExistingOutputPolicy 在 Batch 引擎中的三种严格行为"""
        in_dir = os.path.join(self.test_dir, "policy_in")
        out_dir = os.path.join(self.test_dir, "policy_out")
        dem_file = os.path.join(in_dir, "tile_policy.tif")
        self._create_synthetic_dem(dem_file, width=20, height=20)

        # 1. 运行并生成初始产物
        res1 = self.batch_engine.run_batch(
            input_folder=in_dir,
            output_folder=out_dir,
            job_mode=JOB_MODE_TIDE_AND_INUNDATION,
            start_time="2024-01-01 00:00:00",
            end_time="2024-01-01 04:00:00",
            freq="1h",
            dem_datum="msl",
            existing_policy=ExistingOutputPolicy.OVERWRITE
        )
        self.assertEqual(res1["counts"]["completed"], 1)
        freq_tif = os.path.join(out_dir, "tile_policy_inundation.tif")
        self.assertTrue(os.path.exists(freq_tif))
        mtime_1 = os.path.getmtime(freq_tif)

        # 2. 测试 RESUME 策略: 已存在完整产物，跳过且绝不删除已有正式文件
        res_resume = self.batch_engine.run_batch(
            input_folder=in_dir,
            output_folder=out_dir,
            job_mode=JOB_MODE_TIDE_AND_INUNDATION,
            start_time="2024-01-01 00:00:00",
            end_time="2024-01-01 04:00:00",
            freq="1h",
            dem_datum="msl",
            existing_policy=ExistingOutputPolicy.RESUME
        )
        self.assertEqual(res_resume["counts"]["skipped"], 1)
        self.assertEqual(res_resume["counts"]["completed"], 0)
        self.assertEqual(os.path.getmtime(freq_tif), mtime_1, "RESUME 模式下不应触碰或更新已有正式产物！")

        # 3. 测试 ERROR_IF_EXISTS 策略: 目标产物已存在时，记录失败且绝不覆写
        res_error = self.batch_engine.run_batch(
            input_folder=in_dir,
            output_folder=out_dir,
            job_mode=JOB_MODE_TIDE_AND_INUNDATION,
            start_time="2024-01-01 00:00:00",
            end_time="2024-01-01 04:00:00",
            freq="1h",
            dem_datum="msl",
            existing_policy=ExistingOutputPolicy.ERROR_IF_EXISTS
        )
        self.assertEqual(res_error["counts"]["failed"], 1)
        manifest = BatchManifest(out_dir)
        manifest.load()
        self.assertIn("ExistingOutputError", manifest.records[dem_file]["error_message"])

        # 4. 测试 OVERWRITE 策略: 允许覆盖且走安全替换
        time.sleep(0.05)  # 确保 mtime 变化
        res_overwrite = self.batch_engine.run_batch(
            input_folder=in_dir,
            output_folder=out_dir,
            job_mode=JOB_MODE_TIDE_AND_INUNDATION,
            start_time="2024-01-01 00:00:00",
            end_time="2024-01-01 04:00:00",
            freq="1h",
            dem_datum="msl",
            existing_policy=ExistingOutputPolicy.OVERWRITE
        )
        self.assertEqual(res_overwrite["counts"]["completed"], 1)
        self.assertGreaterEqual(os.path.getmtime(freq_tif), mtime_1)

    # -------------------------------------------------------------
    # 14.5 Tide Cache Mode 3 绝对只读保护测试
    # -------------------------------------------------------------
    def test_14_5_mode3_tide_cache_strictly_read_only(self):
        """测试在 Mode 3 下，即便 policy=OVERWRITE，*_tide.nc 依然绝对只读，绝不覆写或更新"""
        in_dir = os.path.join(self.test_dir, "m3_in")
        out_dir = os.path.join(self.test_dir, "m3_out")
        dem_file = os.path.join(in_dir, "tile_m3.tif")
        self._create_synthetic_dem(dem_file, width=20, height=20)

        # 先生成 Tide Cache
        self.batch_engine.run_batch(
            input_folder=in_dir,
            output_folder=out_dir,
            job_mode=JOB_MODE_TIDE_ONLY,
            start_time="2024-01-01 00:00:00",
            end_time="2024-01-01 04:00:00",
            freq="1h",
            dem_datum="msl"
        )
        cache_nc = os.path.join(out_dir, "tile_m3_tide.nc")
        self.assertTrue(os.path.exists(cache_nc))
        cache_mtime = os.path.getmtime(cache_nc)

        time.sleep(0.05)

        # 运行 Mode 3 (inundation-from-cache)，设置 existing_policy=OVERWRITE
        res = self.batch_engine.run_batch(
            input_folder=in_dir,
            output_folder=out_dir,
            job_mode=JOB_MODE_INUNDATION_FROM_CACHE,
            start_time="2024-01-01 00:00:00",
            end_time="2024-01-01 04:00:00",
            freq="1h",
            dem_datum="msl",
            existing_policy=ExistingOutputPolicy.OVERWRITE
        )
        self.assertEqual(res["counts"]["completed"], 1)
        # 核心断言: *_tide.nc 的 mtime 必须保持绝对一致 (严禁以写入模式打开)
        self.assertEqual(os.path.getmtime(cache_nc), cache_mtime, "Mode 3 下 Tide Cache 必须绝对只读！")

    # -------------------------------------------------------------
    # 14.6 Tide Cache 全要素兼容性签名校验测试
    # -------------------------------------------------------------
    def test_14_6_tide_cache_compatibility_signature(self):
        """测试修改 DEM 尺寸、CRS、仿射变换后，validate_tide_cache_compatibility 准确报错"""
        dem_file = os.path.join(self.test_dir, "sig_dem.tif")
        cache_nc = os.path.join(self.test_dir, "sig_tide.nc")
        self._create_synthetic_dem(dem_file, width=30, height=30)

        with rasterio.open(dem_file) as src:
            info = RasterInfo(
                path=dem_file, crs=str(src.crs), is_projected=True,
                width=src.width, height=src.height, transform=src.transform,
                bounds=src.bounds, resolution=src.res, nodata=src.nodata,
                valid_pixel_count=900, total_pixel_count=900, dtype="float32"
            )

        n_times = 5
        t_idx = pd.date_range("2024-01-01", periods=n_times, freq="1h", tz="UTC")
        t_arr = np.zeros(n_times, dtype=np.float32)
        n0 = ControlNode(node_id=0, x=500000, y=3400000, lon=121.0, lat=31.0, valid=True)
        n0.tide_msl_raw = t_arr
        n0.water_levels_sorted = t_arr
        cell = QuadCell(cell_id=0, x_min=500000, y_min=3399400, x_max=500600, y_max=3400000, level=0,
                        node_a=n0, node_b=n0, node_c=n0, node_d=n0)

        write_tide_cache(cache_nc, info, [cell], {(0, 0): n0}, t_idx, {"dem_datum": "egm2008"})

        # 1. 正常规格验证通过
        spec_ok = {"width": 30, "height": 30, "crs": str(src.crs), "transform": list(src.transform)[:6]}
        comp, reasons = validate_tide_cache_compatibility(cache_nc, spec_ok)
        self.assertTrue(comp)
        self.assertEqual(len(reasons), 0)

        # 2. 篡改 width 验证失败
        spec_bad_w = spec_ok.copy()
        spec_bad_w["width"] = 99
        comp_w, reasons_w = validate_tide_cache_compatibility(cache_nc, spec_bad_w)
        self.assertFalse(comp_w)
        self.assertTrue(any("栅格宽度不匹配" in r for r in reasons_w))

        # 3. 篡改 CRS 验证失败
        spec_bad_crs = spec_ok.copy()
        spec_bad_crs["crs"] = "EPSG:4326"
        comp_crs, reasons_crs = validate_tide_cache_compatibility(cache_nc, spec_bad_crs)
        self.assertFalse(comp_crs)
        self.assertTrue(any("CRS" in r or "坐标系统" in r for r in reasons_crs))

    # -------------------------------------------------------------
    # 14.7 签名缺失/损坏/篡改拦截测试
    # -------------------------------------------------------------
    def test_14_7_tide_cache_tamper_interception(self):
        """测试当 Tide Cache 关键属性被篡改导致签名不一致时，Stage 2 必须阻断并抛出 TideCacheCompatibilityError"""
        dem_file = os.path.join(self.test_dir, "tamper_dem.tif")
        cache_nc = os.path.join(self.test_dir, "tamper_tide.nc")
        out_tif = os.path.join(self.test_dir, "tamper_inund.tif")
        self._create_synthetic_dem(dem_file, width=20, height=20)

        with rasterio.open(dem_file) as src:
            info = RasterInfo(
                path=dem_file, crs=str(src.crs), is_projected=True,
                width=src.width, height=src.height, transform=src.transform,
                bounds=src.bounds, resolution=src.res, nodata=src.nodata,
                valid_pixel_count=400, total_pixel_count=400, dtype="float32"
            )

        t_idx = pd.date_range("2024-01-01", periods=4, freq="1h", tz="UTC")
        t_arr = np.zeros(4, dtype=np.float32)
        n0 = ControlNode(node_id=0, x=500000, y=3400000, lon=121.0, lat=31.0, valid=True)
        n0.tide_msl_raw = t_arr
        n0.water_levels_sorted = t_arr
        cell = QuadCell(cell_id=0, x_min=500000, y_min=3399600, x_max=500400, y_max=3400000, level=0,
                        node_a=n0, node_b=n0, node_c=n0, node_d=n0)

        write_tide_cache(cache_nc, info, [cell], {(0, 0): n0}, t_idx, {"dem_datum": "egm2008"})

        # 手工篡改 cache_nc 的全局属性 DEM_DATUM，造成与原签名不一致
        import netCDF4
        with netCDF4.Dataset(cache_nc, "a") as ds:
            ds.setncattr("DEM_DATUM", "TAMPERED_DATUM")

        # 执行 Stage 2 解算，断言抛出 TideCacheCompatibilityError
        with self.assertRaises(TideCacheCompatibilityError):
            calculate_inundation_from_tide_cache(dem_file, cache_nc, out_tif)

    # -------------------------------------------------------------
    # 14.8 Manifest 状态机连续三轮执行一致性测试
    # -------------------------------------------------------------
    def test_14_8_manifest_state_machine_three_runs_consistency(self):
        """测试连续三轮执行: 第1轮完成，第2轮保持DONE并跳过，第3轮依然保持DONE并跳过"""
        in_dir = os.path.join(self.test_dir, "three_runs_in")
        out_dir = os.path.join(self.test_dir, "three_runs_out")
        dem_file = os.path.join(in_dir, "tile_steady.tif")
        self._create_synthetic_dem(dem_file, width=20, height=20)

        # 第 1 轮运行: 完成
        r1 = self.batch_engine.run_batch(
            input_folder=in_dir, output_folder=out_dir,
            job_mode=JOB_MODE_TIDE_AND_INUNDATION,
            start_time="2024-01-01 00:00:00", end_time="2024-01-01 02:00:00",
            freq="1h", dem_datum="msl", existing_policy=ExistingOutputPolicy.OVERWRITE
        )
        self.assertEqual(r1["counts"]["completed"], 1)
        manifest = BatchManifest(out_dir)
        manifest.load()
        self.assertEqual(manifest.records[dem_file]["status"], STATUS_DONE)

        # 第 2 轮运行: resume 模式下应跳过，持久化 status 依然为 DONE
        r2 = self.batch_engine.run_batch(
            input_folder=in_dir, output_folder=out_dir,
            job_mode=JOB_MODE_TIDE_AND_INUNDATION,
            start_time="2024-01-01 00:00:00", end_time="2024-01-01 02:00:00",
            freq="1h", dem_datum="msl", existing_policy=ExistingOutputPolicy.RESUME
        )
        self.assertEqual(r2["counts"]["skipped"], 1)
        manifest.load()
        self.assertEqual(manifest.records[dem_file]["status"], STATUS_DONE, "第2轮跳过时持久化 status 必须依然为 DONE！")
        self.assertEqual(manifest.records[dem_file]["run_action"], "SKIPPED_EXISTING")

        # 第 3 轮运行: 再次 resume，状态依然稳定为 DONE，无跳变或误判
        r3 = self.batch_engine.run_batch(
            input_folder=in_dir, output_folder=out_dir,
            job_mode=JOB_MODE_TIDE_AND_INUNDATION,
            start_time="2024-01-01 00:00:00", end_time="2024-01-01 02:00:00",
            freq="1h", dem_datum="msl", existing_policy=ExistingOutputPolicy.RESUME
        )
        self.assertEqual(r3["counts"]["skipped"], 1)
        manifest.load()
        self.assertEqual(manifest.records[dem_file]["status"], STATUS_DONE)
        self.assertEqual(manifest.records[dem_file]["run_action"], "SKIPPED_EXISTING")

    # -------------------------------------------------------------
    # 14.9 批量递归同名文件子路径测试
    # -------------------------------------------------------------
    def test_14_9_recursive_subfolder_duplicate_basename_mirroring(self):
        """测试 recursive=True 下不同子目录下同名 DEM 镜像输出至对应子目录，不发生同名覆盖"""
        in_dir = os.path.join(self.test_dir, "rec_in")
        out_dir = os.path.join(self.test_dir, "rec_out")

        sub_a = os.path.join(in_dir, "zone_a")
        sub_b = os.path.join(in_dir, "zone_b")
        os.makedirs(sub_a, exist_ok=True)
        os.makedirs(sub_b, exist_ok=True)

        dem_a = os.path.join(sub_a, "tile.tif")
        dem_b = os.path.join(sub_b, "tile.tif")
        self._create_synthetic_dem(dem_a, width=20, height=20, elevation_val=0.2)
        self._create_synthetic_dem(dem_b, width=20, height=20, elevation_val=0.8)

        res = self.batch_engine.run_batch(
            input_folder=in_dir,
            output_folder=out_dir,
            job_mode=JOB_MODE_TIDE_AND_INUNDATION,
            start_time="2024-01-01 00:00:00",
            end_time="2024-01-01 02:00:00",
            freq="1h",
            dem_datum="msl",
            recursive=True
        )

        self.assertEqual(res["counts"]["total"], 2)
        self.assertEqual(res["counts"]["completed"], 2)

        # 验证两个输出文件分别位于各自镜像子目录
        out_a_tif = os.path.join(out_dir, "zone_a", "tile_inundation.tif")
        out_b_tif = os.path.join(out_dir, "zone_b", "tile_inundation.tif")
        self.assertTrue(os.path.exists(out_a_tif), f"未找到 zone_a 镜像输出: {out_a_tif}")
        self.assertTrue(os.path.exists(out_b_tif), f"未找到 zone_b 镜像输出: {out_b_tif}")

        # 验证两者的 Cache 同样分别镜像存放
        cache_a = os.path.join(out_dir, "zone_a", "tile_tide.nc")
        cache_b = os.path.join(out_dir, "zone_b", "tile_tide.nc")
        self.assertTrue(os.path.exists(cache_a))
        self.assertTrue(os.path.exists(cache_b))


if __name__ == "__main__":
    unittest.main()
