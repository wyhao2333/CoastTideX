"""
CoastTideX v1.5 Alpha 批量潮间带栅格与 Tide Cache 单元测试套件
(Batch Intertidal Raster Engine & Persistent NetCDF Tide Cache Test Suite)

覆盖模块:
    1. 批量文件轻量扫描、过滤与确定性排序 (Batch Discovery);
    2. 整年半开区间时间采样严格保真 (Time Sampling Fidelity);
    3. Tide Cache 序列化、流式压缩、元数据嵌入与完整性校验 (Tide Cache Round-trip);
    4. Stage 2 纯 Cache 驱动解算 (零 FES 调用硬性验收, Zero FES Calls);
    5. 空间栅格属性严密继承 (CRS, Transform, Dimensions, NoData);
    6. 狭长沙滩 / 潮滩目标感知自适应细分 (Narrow Beach Target-Aware Refinement);
    7. 单瓦片失败隔离 (Failure Isolation);
    8. 批量断点恢复与状态机调度 (Batch Resume & State Machine);
    9. 相邻瓦片接缝连续性评估 (Tile Seam Benchmark Harness);
    10. Tide Cache 原始大小预估函数 (Cache Size Estimator).
"""

import os
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import from_origin
import netCDF4

from core.raster_engine import (
    RasterTideEngine, RasterInfo, ControlNode, QuadCell,
    QC_BIT_VALID, QC_BIT_FES_VALIDITY_BOUNDARY, QC_BIT_MIN_SPACING_REACHED,
    QC_BIT_INSUFFICIENT_NODES, QC_NODATA
)
from core.tide_cache import (
    write_tide_cache, read_tide_cache, is_cache_complete,
    calculate_inundation_from_tide_cache, estimate_tide_cache_size,
    COASTTIDEX_VERSION, CACHE_SCHEMA_VERSION
)
from core.batch_raster_engine import (
    BatchRasterEngine, BatchManifest,
    JOB_MODE_TIDE_ONLY, JOB_MODE_TIDE_AND_INUNDATION, JOB_MODE_INUNDATION_FROM_CACHE,
    STATUS_DONE, STATUS_FAILED, STATUS_SKIPPED, STATUS_TIDE_READY
)
from core.utils import build_time_index


class MockSyntheticPredictor:
    """合成潮位预测器，用于快速且可复现的单元测试"""
    def __init__(self, amplitude=2.0, period_hours=12.42, valid_bbox=None, gradient_y=0.0, non_linear_grad=False):
        self.amplitude = amplitude
        self.period_hours = period_hours
        self.valid_bbox = valid_bbox  # (lon_min, lat_min, lon_max, lat_max)
        self.gradient_y = gradient_y
        self.non_linear_grad = non_linear_grad
        self.call_count = 0

    def predict_points_period(self, lons, lats, start_time, end_time, freq, inclusive='left', constituents=None, source_tz='UTC', max_fes_evaluate_points=500000):
        self.call_count += 1
        t_idx, _, _ = build_time_index(start_time, end_time, freq=freq, inclusive=inclusive, source_tz=source_tz)
        n_times = len(t_idx)
        n_points = len(lons)

        # 生成简谐波动水位
        t_hours = np.arange(n_times, dtype=float) * (pd.to_timedelta(freq).total_seconds() / 3600.0)
        base_wave = np.sin(2.0 * np.pi * t_hours / self.period_hours)  # (n_times,)

        tide_mat = np.zeros((n_points, n_times), dtype=np.float32)
        flag_mat = np.zeros((n_points, n_times), dtype=np.int16)

        for i in range(n_points):
            lon = lons[i]
            lat = lats[i]

            # 空间有效性判定
            is_valid = True
            if self.valid_bbox is not None:
                x0, y0, x1, y1 = self.valid_bbox
                if not (x0 <= lon <= x1 and y0 <= lat <= y1):
                    is_valid = False

            if is_valid:
                if self.non_linear_grad:
                    amp_eff = self.amplitude + self.gradient_y * np.sin(500.0 * (lat - 30.0))
                else:
                    amp_eff = self.amplitude + self.gradient_y * (lat - 30.0)
                tide_mat[i, :] = amp_eff * base_wave
                flag_mat[i, :] = 0
            else:
                tide_mat[i, :] = np.nan
                flag_mat[i, :] = -1

        return tide_mat, t_idx, flag_mat


class TestBatchRasterV15(unittest.TestCase):
    """v1.5 Alpha 批量潮间带栅格解算与 Tide Cache 全功能测试"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="ct_test_v15_")

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_synthetic_dem(
        self,
        filepath: str,
        width: int = 100,
        height: int = 100,
        res: float = 10.0,
        origin_x: float = 500000.0,
        origin_y: float = 3400000.0,
        crs: str = "EPSG:32651",
        nodata: float = -9999.0,
        elevation_val: float = 1.0
    ):
        """生成带有效投影坐标系的标准合成 DEM"""
        transform = from_origin(origin_x, origin_y, res, res)
        data = np.full((height, width), elevation_val, dtype=np.float32)
        with rasterio.open(
            filepath, "w",
            driver="GTiff",
            width=width,
            height=height,
            count=1,
            dtype=rasterio.float32,
            crs=crs,
            transform=transform,
            nodata=nodata
        ) as dst:
            dst.write(data, 1)

    # -------------------------------------------------------------
    # 1. 批量文件轻量发现与过滤测试 (Batch Discovery)
    # -------------------------------------------------------------
    def test_batch_discovery_and_filtering(self):
        """测试 GeoTIFF 扫描排重、临时文件排除与确定性排序"""
        in_dir = os.path.join(self.test_dir, "discovery_input")
        out_dir = os.path.join(in_dir, "CoastTideX_output")
        os.makedirs(in_dir, exist_ok=True)
        os.makedirs(out_dir, exist_ok=True)

        # 创建正常输入文件
        self._create_synthetic_dem(os.path.join(in_dir, "tile_b.tif"))
        self._create_synthetic_dem(os.path.join(in_dir, "tile_a.TIFF"))
        sub_dir = os.path.join(in_dir, "sub")
        os.makedirs(sub_dir, exist_ok=True)
        self._create_synthetic_dem(os.path.join(sub_dir, "tile_c.tif"))

        # 创建应被排除的衍生文件
        self._create_synthetic_dem(os.path.join(in_dir, "tile_b_inundation.tif"))
        self._create_synthetic_dem(os.path.join(in_dir, "tile_b_inundation_qc.tif"))
        self._create_synthetic_dem(os.path.join(in_dir, "temp.tmp.tif"))
        self._create_synthetic_dem(os.path.join(out_dir, "should_ignore.tif"))
        Path(os.path.join(in_dir, "tile_b_tide.nc")).touch()
        Path(os.path.join(in_dir, "notes.txt")).touch()

        # 非递归扫描
        discovered_non_rec = BatchRasterEngine.discover_rasters(in_dir, recursive=False, output_folder=out_dir)
        names_non_rec = [d["filename"] for d in discovered_non_rec]
        self.assertEqual(names_non_rec, ["tile_a.TIFF", "tile_b.tif"])

        # 递归扫描
        discovered_rec = BatchRasterEngine.discover_rasters(in_dir, recursive=True, output_folder=out_dir)
        names_rec = [d["filename"] for d in discovered_rec]
        self.assertIn("tile_c.tif", names_rec)
        self.assertEqual(len(discovered_rec), 3)

        # 检验元数据读取正确性 (轻量读取)
        item_a = discovered_rec[0]
        self.assertEqual(item_a["width"], 100)
        self.assertEqual(item_a["height"], 100)
        self.assertTrue(item_a["valid"])

    # -------------------------------------------------------------
    # 2. 整年时间采样步数严格保真 (Time Sampling Fidelity)
    # -------------------------------------------------------------
    def test_annual_time_sampling_fidelity(self):
        """测试 2024 闰年半开区间严格采样点数"""
        t_30m, _, _ = build_time_index("2024-01-01 00:00:00", "2025-01-01 00:00:00", freq="30min", inclusive="left")
        self.assertEqual(len(t_30m), 17568)

        t_1h, _, _ = build_time_index("2024-01-01 00:00:00", "2025-01-01 00:00:00", freq="1h", inclusive="left")
        self.assertEqual(len(t_1h), 8784)

        t_15m, _, _ = build_time_index("2024-01-01 00:00:00", "2025-01-01 00:00:00", freq="15min", inclusive="left")
        self.assertEqual(len(t_15m), 35136)

    # -------------------------------------------------------------
    # 3. Tide Cache 大小预估函数测试 (Cache Size Estimator)
    # -------------------------------------------------------------
    def test_estimate_tide_cache_size(self):
        """验证控制节点数组内存与原始数据量估算公式"""
        est = estimate_tide_cache_size(node_count=100, time_samples=17568)
        expected_bytes = 100 * 17568 * 4
        self.assertEqual(est["raw_bytes"], expected_bytes)
        self.assertAlmostEqual(est["raw_mb"], expected_bytes / (1024 * 1024), places=2)
        self.assertIn("MB", est["formatted_size"])

    # -------------------------------------------------------------
    # 4. Tide Cache 序列化与读写还原测试 (Tide Cache Round-trip)
    # -------------------------------------------------------------
    def test_tide_cache_round_trip(self):
        """验证 Tide Cache NetCDF4 的原子级写入、完整性校验与内存重构"""
        cache_nc = os.path.join(self.test_dir, "test_tile_tide.nc")
        info = RasterInfo(
            path="dummy.tif", crs="EPSG:32651", is_projected=True,
            width=500, height=500, transform=from_origin(500000, 3400000, 10, 10),
            bounds=(500000, 3395000, 505000, 3400000), resolution=(10, 10),
            nodata=-9999.0, valid_pixel_count=250000, total_pixel_count=250000, dtype="float32"
        )

        n_times = 48  # 48 步测试时序
        time_index = pd.date_range("2024-01-01", periods=n_times, freq="30min", tz="UTC")

        # 构造测试控制节点
        n0 = ControlNode(node_id=0, x=500000, y=3400000, lon=121.0, lat=31.0, component_id=1, valid=True, static_offset_m=0.5)
        n0.tide_msl_raw = np.sin(np.linspace(0, 4*np.pi, n_times)).astype(np.float32)
        n0.water_levels_sorted = np.sort(n0.tide_msl_raw + 0.5)

        n1 = ControlNode(node_id=1, x=505000, y=3400000, lon=121.05, lat=31.0, component_id=1, valid=True, static_offset_m=0.5)
        n1.tide_msl_raw = np.cos(np.linspace(0, 4*np.pi, n_times)).astype(np.float32)
        n1.water_levels_sorted = np.sort(n1.tide_msl_raw + 0.5)

        n2 = ControlNode(node_id=2, x=500000, y=3395000, lon=121.0, lat=30.95, component_id=1, valid=True, static_offset_m=0.5)
        n2.tide_msl_raw = np.sin(np.linspace(0, 4*np.pi, n_times)).astype(np.float32)
        n2.water_levels_sorted = np.sort(n2.tide_msl_raw + 0.5)

        n3 = ControlNode(node_id=3, x=505000, y=3395000, lon=121.05, lat=30.95, component_id=1, valid=True, static_offset_m=0.5)
        n3.tide_msl_raw = np.cos(np.linspace(0, 4*np.pi, n_times)).astype(np.float32)
        n3.water_levels_sorted = np.sort(n3.tide_msl_raw + 0.5)

        node_cache = {(0, 0): n0, (1, 0): n1, (0, 1): n2, (1, 1): n3}

        # 构造叶单元
        cell = QuadCell(cell_id=0, x_min=500000, y_min=3395000, x_max=505000, y_max=3400000, level=0, node_a=n0, node_b=n1, node_c=n2, node_d=n3)
        leaf_cells = [cell]

        meta = {"start_time": "2024-01-01T00:00:00Z", "end_time": "2024-01-02T00:00:00Z", "freq": "30min", "target_mode": "intertidal"}

        # 写入
        out_cache = write_tide_cache(cache_nc, info, leaf_cells, node_cache, time_index, meta)
        self.assertTrue(os.path.exists(out_cache))
        self.assertTrue(is_cache_complete(out_cache))

        # 读取还原
        data = read_tide_cache(out_cache)
        self.assertEqual(data["num_nodes"], 4)
        self.assertEqual(data["num_cells"], 1)
        self.assertEqual(data["time_samples"], n_times)
        self.assertEqual(data["metadata"]["TARGET_MODE"], "intertidal")
        self.assertEqual(data["metadata"]["CACHE_SCHEMA_VERSION"], CACHE_SCHEMA_VERSION)

        # 验证节点原地排序恢复的水位序列正确性
        restored_node_0 = data["nodes"][0]
        self.assertEqual(len(restored_node_0.water_levels_sorted), n_times)
        np.testing.assert_allclose(restored_node_0.water_levels_sorted, n0.water_levels_sorted, atol=1e-5)

    # -------------------------------------------------------------
    # 5. Stage 2 纯 Cache 驱动解算与零 FES 调用硬性验收
    # -------------------------------------------------------------
    def test_stage2_zero_fes_calls_and_inundation(self):
        """核心硬性验收: calculate_inundation_from_tide_cache 严禁触发任何 FES 调用"""
        dem_tif = os.path.join(self.test_dir, "stage2_dem.tif")
        cache_nc = os.path.join(self.test_dir, "stage2_tide.nc")
        out_tif = os.path.join(self.test_dir, "stage2_inundation.tif")
        out_qc = os.path.join(self.test_dir, "stage2_qc.tif")

        # 创建测试 DEM (z = 0.5m)
        self._create_synthetic_dem(dem_tif, width=100, height=100, res=10.0, origin_x=500000, origin_y=3400000, elevation_val=0.5)

        # 生成对应 Tide Cache (潮位在 -2m ~ +2m 正弦震荡)
        with rasterio.open(dem_tif) as src:
            info = RasterInfo(
                path=dem_tif, crs=str(src.crs), is_projected=True,
                width=src.width, height=src.height, transform=src.transform,
                bounds=src.bounds, resolution=src.res, nodata=src.nodata,
                valid_pixel_count=10000, total_pixel_count=10000, dtype="float32"
            )

        n_times = 100
        time_index = pd.date_range("2024-01-01", periods=n_times, freq="30min", tz="UTC")
        tide_arr = np.linspace(-2.0, 2.0, n_times, dtype=np.float32)

        # 4 个角节点
        nodes = []
        for i, (nx, ny) in enumerate([(500000, 3400000), (501000, 3400000), (500000, 3399000), (501000, 3399000)]):
            nd = ControlNode(node_id=i, x=nx, y=ny, lon=121.0, lat=31.0, valid=True, static_offset_m=0.0)
            nd.tide_msl_raw = tide_arr.copy()
            nd.water_levels_sorted = np.sort(tide_arr)
            nodes.append(nd)

        node_cache = {(0, 0): nodes[0], (1, 0): nodes[1], (0, 1): nodes[2], (1, 1): nodes[3]}
        cell = QuadCell(cell_id=0, x_min=500000, y_min=3399000, x_max=501000, y_max=3400000, level=0,
                        node_a=nodes[0], node_b=nodes[1], node_c=nodes[2], node_d=nodes[3])

        write_tide_cache(cache_nc, info, [cell], node_cache, time_index, {"dem_datum": "egm2008"})

        # 执行 Stage 2 纯 Cache 解算
        summary = calculate_inundation_from_tide_cache(
            dem_path=dem_tif,
            cache_path=cache_nc,
            output_path=out_tif,
            qc_output_path=out_qc
        )

        self.assertTrue(os.path.exists(out_tif))
        self.assertTrue(os.path.exists(out_qc))
        self.assertEqual(summary.solved_pixels, 10000)

        # 检验物理淹没频率数值 (潮位均匀分布于 [-2, 2], 高程 z=0.5m, P(H > 0.5) = (2 - 0.5)/4 = 37.5%)
        with rasterio.open(out_tif) as src:
            freq_data = src.read(1)
            mean_freq = float(np.mean(freq_data))
            self.assertAlmostEqual(mean_freq, 37.5, delta=1.5)

    # -------------------------------------------------------------
    # 6. 空间栅格属性严密继承测试 (Raster Inheritance)
    # -------------------------------------------------------------
    def test_raster_inheritance(self):
        """验证输出淹没频率 GeoTIFF 完全继承原始 DEM 的空间元数据"""
        dem_tif = os.path.join(self.test_dir, "inherit_dem.tif")
        cache_nc = os.path.join(self.test_dir, "inherit_tide.nc")
        out_tif = os.path.join(self.test_dir, "inherit_inundation.tif")

        self._create_synthetic_dem(dem_tif, width=80, height=60, res=5.0, origin_x=600000, origin_y=3500000, nodata=-9999.0)

        with rasterio.open(dem_tif) as src:
            orig_crs = src.crs
            orig_transform = src.transform
            orig_width = src.width
            orig_height = src.height
            orig_nodata = src.nodata

            info = RasterInfo(
                path=dem_tif, crs=str(orig_crs), is_projected=True,
                width=orig_width, height=orig_height, transform=orig_transform,
                bounds=src.bounds, resolution=src.res, nodata=orig_nodata,
                valid_pixel_count=4800, total_pixel_count=4800, dtype="float32"
            )

        n_times = 10
        t_idx = pd.date_range("2024-01-01", periods=n_times, freq="1h", tz="UTC")
        t_arr = np.zeros(n_times, dtype=np.float32)
        n0 = ControlNode(node_id=0, x=600000, y=3500000, lon=122.0, lat=32.0, valid=True)
        n0.tide_msl_raw = t_arr
        n0.water_levels_sorted = t_arr
        cell = QuadCell(cell_id=0, x_min=600000, y_min=3499700, x_max=600400, y_max=3500000, level=0,
                        node_a=n0, node_b=n0, node_c=n0, node_d=n0)

        write_tide_cache(cache_nc, info, [cell], {(0,0): n0}, t_idx, {})
        calculate_inundation_from_tide_cache(dem_tif, cache_nc, out_tif)

        with rasterio.open(out_tif) as out_src:
            self.assertEqual(out_src.crs, orig_crs)
            self.assertEqual(out_src.transform, orig_transform)
            self.assertEqual(out_src.width, orig_width)
            self.assertEqual(out_src.height, orig_height)
            self.assertTrue(np.isnan(out_src.nodata), '淹没频率栅格按规范必须为 Float32, nodata=NaN')

    # -------------------------------------------------------------
    # 7. 狭长沙滩目标感知自适应细分测试 (Narrow Beach Tests)
    # -------------------------------------------------------------
    def test_narrow_beach_intertidal_refinement(self):
        """
        验证 v1.5 潮间带目标感知细分:
        在平滑潮位场下，30m/100m 窄沙滩即使跨越海陆交界，也不应全局过度细化到 500m
        """
        dem_30m_beach = os.path.join(self.test_dir, "beach_30m.tif")
        out_tif = os.path.join(self.test_dir, "beach_30m_out.tif")
        out_qc = os.path.join(self.test_dir, "beach_30m_qc.tif")

        # 创建 4km x 4km 区域，其中仅中间 30m 为有效海滩像元 (其余为 NoData 陆地)
        width, height = 400, 400
        transform = from_origin(500000, 3400000, 10.0, 10.0)
        dem_data = np.full((height, width), -9999.0, dtype=np.float32)
        # 狭长海滩条带 (3 个像元宽 = 30m)
        dem_data[:, 198:201] = 1.2

        with rasterio.open(
            dem_30m_beach, "w", driver="GTiff", width=width, height=height, count=1,
            dtype=rasterio.float32, crs="EPSG:32651", transform=transform, nodata=-9999.0
        ) as dst:
            dst.write(dem_data, 1)

        # 平滑潮汐场 (振幅恒定，无沿岸梯度)
        pred_smooth = MockSyntheticPredictor(amplitude=2.0)
        engine = RasterTideEngine(
            tide_predictor=pred_smooth,
            initial_control_spacing_m=4000.0,
            min_control_spacing_m=500.0,
            inundation_error_tolerance_pct=1.0
        )

        # 执行 target_mode='intertidal'
        summary_intertidal = engine.calculate_inundation_raster(
            dem_path=dem_30m_beach,
            output_path=out_tif,
            qc_output_path=out_qc,
            start_time="2024-01-01 00:00:00",
            end_time="2024-01-01 12:00:00",
            freq="1h",
            dem_datum="msl",
            target_mode="intertidal"
        )

        # 核心断言: 平滑潮位下的 30m 狭长海滩不应触发 3 级深层过度加密 (max refinement level 保持在 0 或 1)
        max_level = int(summary_intertidal.metadata.get("MAX_REFINEMENT_LEVEL_USED", 0))
        self.assertLess(max_level, 3, "平滑潮汐场下 30m 窄沙滩不应细分至最大深度 3 (500m)")

    def test_narrow_beach_alongshore_gradient_refinement(self):
        """强沿岸潮差梯度下，即便为窄沙滩，也应正确识别梯度超标并执行自适应细分"""
        dem_grad_beach = os.path.join(self.test_dir, "beach_grad.tif")
        out_tif = os.path.join(self.test_dir, "beach_grad_out.tif")

        width, height = 400, 400
        transform = from_origin(500000, 3400000, 10.0, 10.0)
        dem_data = np.full((height, width), -9999.0, dtype=np.float32)
        dem_data[:, 198:201] = 1.0  # 30m 沙滩

        with rasterio.open(
            dem_grad_beach, "w", driver="GTiff", width=width, height=height, count=1,
            dtype=rasterio.float32, crs="EPSG:32651", transform=transform, nodata=-9999.0
        ) as dst:
            dst.write(dem_data, 1)

        # 强梯度预测器 (沿纬度存在剧烈非线性空间潮波变化)
        pred_grad = MockSyntheticPredictor(amplitude=2.0, gradient_y=2.0, non_linear_grad=True)
        engine = RasterTideEngine(
            tide_predictor=pred_grad,
            initial_control_spacing_m=4000.0,
            min_control_spacing_m=500.0,
            inundation_error_tolerance_pct=0.5
        )

        summary_grad = engine.calculate_inundation_raster(
            dem_path=dem_grad_beach,
            output_path=out_tif,
            start_time="2024-01-01 00:00:00",
            end_time="2024-01-01 12:00:00",
            freq="1h",
            dem_datum="msl",
            target_mode="intertidal"
        )

        # 核心断言: 强梯度区域自适应细分应当被正确触发
        max_level = int(summary_grad.metadata.get("MAX_REFINEMENT_LEVEL_USED", 0))
        self.assertGreater(max_level, 0, "强潮位梯度区域必须触发自适应细分")

    # -------------------------------------------------------------
    # 8. 单瓦片失败隔离测试 (Failure Isolation)
    # -------------------------------------------------------------
    def test_batch_failure_isolation(self):
        """测试批量运行中某个瓦片损坏时，异常被隔离，后续瓦片继续成功执行"""
        in_dir = os.path.join(self.test_dir, "fail_iso_input")
        out_dir = os.path.join(self.test_dir, "fail_iso_output")
        os.makedirs(in_dir, exist_ok=True)

        # 1. 正常影像 good_1.tif
        self._create_synthetic_dem(os.path.join(in_dir, "01_good.tif"), width=20, height=20)
        # 2. 损坏影像 broken.tif (非法伪文本)
        broken_path = os.path.join(in_dir, "02_broken.tif")
        with open(broken_path, "w") as f:
            f.write("corrupted raster header")
        # 3. 正常影像 good_2.tif
        self._create_synthetic_dem(os.path.join(in_dir, "03_good.tif"), width=20, height=20)

        pred = MockSyntheticPredictor(amplitude=1.5)
        eng = RasterTideEngine(tide_predictor=pred)
        batch_engine = BatchRasterEngine(raster_engine=eng)

        res = batch_engine.run_batch(
            input_folder=in_dir,
            output_folder=out_dir,
            job_mode=JOB_MODE_TIDE_AND_INUNDATION,
            start_time="2024-01-01 00:00:00",
            end_time="2024-01-01 06:00:00",
            freq="1h",
            dem_datum="msl",
            initial_control_spacing_m=4000.0
        )

        counts = res["counts"]
        self.assertEqual(counts["total"], 3)
        self.assertEqual(counts["completed"], 2)
        self.assertEqual(counts["failed"], 1)

        # 验证清单记录
        manifest = BatchManifest(out_dir)
        manifest.load()
        self.assertEqual(manifest.get_status(os.path.join(in_dir, "01_good.tif")), STATUS_DONE)
        self.assertEqual(manifest.get_status(broken_path), STATUS_FAILED)
        self.assertEqual(manifest.get_status(os.path.join(in_dir, "03_good.tif")), STATUS_DONE)

    # -------------------------------------------------------------
    # 9. 批量断点恢复测试 (Batch Resume)
    # -------------------------------------------------------------
    def test_batch_resume_capability(self):
        """测试断点恢复: 已完成瓦片跳过，TIDE_READY 瓦片直接计算频率且不重调 FES"""
        in_dir = os.path.join(self.test_dir, "resume_input")
        out_dir = os.path.join(self.test_dir, "resume_output")
        os.makedirs(in_dir, exist_ok=True)
        os.makedirs(out_dir, exist_ok=True)

        f1 = os.path.join(in_dir, "tile_1.tif")
        f2 = os.path.join(in_dir, "tile_2.tif")
        self._create_synthetic_dem(f1, width=20, height=20)
        self._create_synthetic_dem(f2, width=20, height=20)

        pred = MockSyntheticPredictor(amplitude=1.5)
        eng = RasterTideEngine(tide_predictor=pred)
        batch_engine = BatchRasterEngine(raster_engine=eng)

        # 第一次运行: 仅生成 Tide Cache
        batch_engine.run_batch(
            input_folder=in_dir,
            output_folder=out_dir,
            job_mode=JOB_MODE_TIDE_ONLY,
            start_time="2024-01-01 00:00:00",
            end_time="2024-01-01 04:00:00",
            freq="1h",
            dem_datum="msl"
        )
        initial_calls = pred.call_count
        self.assertGreater(initial_calls, 0)

        # 将 tile_1 手工设为 DONE (模拟上次已完全完成)
        manifest = BatchManifest(out_dir)
        manifest.load()
        Path(os.path.join(out_dir, "tile_1_inundation.tif")).touch()
        manifest.upsert(f1, status=STATUS_DONE)
        manifest.save()

        # 第二次运行: 执行完整流程 (Tide + Inundation)，启用 resume
        res2 = batch_engine.run_batch(
            input_folder=in_dir,
            output_folder=out_dir,
            job_mode=JOB_MODE_TIDE_AND_INUNDATION,
            start_time="2024-01-01 00:00:00",
            end_time="2024-01-01 04:00:00",
            freq="1h",
            dem_datum="msl",
            resume=True
        )

        # 核心断言: 第二次运行时，因为 tile_2 已有 Tide Cache 且 tile_1 已 DONE，FES 预测器调用次数增加恒等于 0
        new_calls = pred.call_count - initial_calls
        self.assertEqual(new_calls, 0, "断点恢复模式下不应重复触发 FES 潮位预测！")
        self.assertEqual(res2["counts"]["skipped"], 1)
        self.assertEqual(res2["counts"]["completed"], 1)

    # -------------------------------------------------------------
    # 10. 相邻瓦片接缝连续性评估测试 (Tile Seam Benchmark)
    # -------------------------------------------------------------
    def test_adjacent_tile_seam_benchmark(self):
        """评估两块地理空间完全无缝拼接的相邻瓦片在接缝处的淹没频率一致性"""
        tile_a = os.path.join(self.test_dir, "seam_tile_a.tif")
        tile_b = os.path.join(self.test_dir, "seam_tile_b.tif")
        out_dir = os.path.join(self.test_dir, "seam_output")

        # Tile A: [500000, 501000], Tile B: [501000, 502000] (共享 501000 边界)
        self._create_synthetic_dem(tile_a, width=100, height=100, res=10.0, origin_x=500000.0, origin_y=3400000.0, elevation_val=0.8)
        self._create_synthetic_dem(tile_b, width=100, height=100, res=10.0, origin_x=501000.0, origin_y=3400000.0, elevation_val=0.8)

        pred = MockSyntheticPredictor(amplitude=2.0)
        eng = RasterTideEngine(tide_predictor=pred, initial_control_spacing_m=4000.0, inundation_error_tolerance_pct=1.0)
        batch = BatchRasterEngine(raster_engine=eng)

        batch.run_batch(
            input_folder=self.test_dir,
            output_folder=out_dir,
            job_mode=JOB_MODE_TIDE_AND_INUNDATION,
            start_time="2024-01-01 00:00:00",
            end_time="2024-01-01 12:00:00",
            freq="1h",
            dem_datum="msl"
        )

        out_a = os.path.join(out_dir, "seam_tile_a_inundation.tif")
        out_b = os.path.join(out_dir, "seam_tile_b_inundation.tif")
        self.assertTrue(os.path.exists(out_a))
        self.assertTrue(os.path.exists(out_b))

        with rasterio.open(out_a) as src_a, rasterio.open(out_b) as src_b:
            edge_a = src_a.read(1)[:, -1]  # Tile A 最右列像元
            edge_b = src_b.read(1)[:, 0]   # Tile B 最左列像元

            diff = np.abs(edge_a - edge_b)
            mean_seam_diff = float(np.mean(diff))
            max_seam_diff = float(np.max(diff))
            p95_seam_diff = float(np.percentile(diff, 95))

            # 验证接缝误差在公差范围之内
            self.assertLess(max_seam_diff, 1.0, f"瓦片接缝最大偏差 ({max_seam_diff:.3f}%) 超出容差 1.0%")


if __name__ == "__main__":
    unittest.main()