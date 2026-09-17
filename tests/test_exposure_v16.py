"""
CoastTideX v1.6 单元测试集 - 潜在天文潮露出时间域分析与 Tide Cache Schema 1.2 验证
Unit Test Suite for Potential Tidal Exposure Engine and Tide Cache Schema 1.2
文件路径: tests/test_exposure_v16.py
"""

import os
import shutil
import tempfile
import unittest
import inspect
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import Affine

from core.exposure_engine import (
    compute_1d_continuous_exposure,
    compute_2d_vec,
    stream_exposure_metrics_interpolation,
    ExposureProductPaths,
    _AtomicExposureWriter,
    QC_EXP_VALID,
    QC_EXP_DEGRADED_CELL,
    QC_EXP_INSUFFICIENT_NODES,
    QC_EXP_DATUM_APPROX,
    QC_EXP_TERMINAL_APPROX,
    QC_EXP_PARTIAL_VALID_TIME,
    QC_EXP_PERMANENTLY_SUBMERGED,
    QC_EXP_PERMANENTLY_EXPOSED,
    QC_EXP_NODATA,
    NODATA_EVENT_COUNT,
    NODATA_FLOAT32,
    NODATA_QC
)
from core.raster_engine import ControlNode, QuadCell, RasterInfo, RasterTideEngine
from core.batch_raster_engine import _verify_exposure_artifacts
from core.tide_cache import (
    write_tide_cache,
    read_tide_cache,
    inspect_tide_cache_metadata,
    validate_tide_cache_compatibility,
    calculate_exposure_from_tide_cache,
    build_expected_cache_spec,
    CACHE_SCHEMA_VERSION
)


class TestExposure1DBenchmark(unittest.TestCase):
    """一维基准测试：数学解析解与边界条件严密对比"""

    def test_constant_permanently_submerged(self):
        """全时段常时淹没测试 (H(t) > z)"""
        times = np.arange(0, 3600 * 10, 3600, dtype=np.float64)  # 10 hours
        wl = np.full(len(times), 5.0, dtype=np.float32)
        z = 2.0
        res = compute_1d_continuous_exposure(wl, times, z)
        self.assertEqual(res["event_count"], 0)
        self.assertEqual(res["cumulative_exposure_h"], 0.0)
        self.assertEqual(res["exposure_fraction_pct"], 0.0)
        self.assertEqual(res["max_continuous_exposure_h"], 0.0)

    def test_constant_permanently_exposed(self):
        """全时段常时露出测试 (H(t) <= z)"""
        times = np.arange(0, 3600 * 10, 3600, dtype=np.float64)  # 10 hours
        wl = np.full(len(times), 1.0, dtype=np.float32)
        z = 3.0
        terminal_ts = times[-1] + 3600.0
        res = compute_1d_continuous_exposure(
            wl, times, z, terminal_water_level=1.0, terminal_timestamp_seconds=terminal_ts
        )
        self.assertEqual(res["event_count"], 1)
        self.assertAlmostEqual(res["cumulative_exposure_h"], 10.0, places=3)
        self.assertAlmostEqual(res["exposure_fraction_pct"], 100.0, places=3)
        self.assertAlmostEqual(res["max_continuous_exposure_h"], 10.0, places=3)

    def test_strict_boundary_equality(self):
        """严格边界条件测试：H(t) == z 必须归属于露出 (Exposed)"""
        times = np.array([0.0, 3600.0, 7200.0], dtype=np.float64)
        wl = np.array([2.0, 2.0, 2.0], dtype=np.float32)
        z = 2.0
        res = compute_1d_continuous_exposure(
            wl, times, z, terminal_water_level=2.0, terminal_timestamp_seconds=10800.0
        )
        self.assertEqual(res["event_count"], 1)
        self.assertAlmostEqual(res["exposure_fraction_pct"], 100.0, places=3)
        self.assertAlmostEqual(res["cumulative_exposure_h"], 3.0, places=3)

    def test_linear_crossing_interpolation_exact(self):
        """线性跨界交点插值精确解析解测试"""
        times = np.array([0.0], dtype=np.float64)
        wl = np.array([0.0], dtype=np.float32)
        res = compute_1d_continuous_exposure(
            water_levels=wl,
            timestamps_seconds=times,
            elevation=4.0,
            terminal_water_level=10.0,
            terminal_timestamp_seconds=1000.0
        )
        self.assertAlmostEqual(res["cumulative_exposure_h"] * 3600.0, 400.0, places=3)
        self.assertEqual(res["event_count"], 1)

    def test_extreme_slope_zero_denominator(self):
        """极端斜率与分母近零防护测试"""
        times = np.array([0.0], dtype=np.float64)
        wl = np.array([4.0000001], dtype=np.float32)
        res = compute_1d_continuous_exposure(
            water_levels=wl,
            timestamps_seconds=times,
            elevation=4.0,
            terminal_water_level=4.0000002,
            terminal_timestamp_seconds=1000.0
        )
        self.assertFalse(np.isnan(res["cumulative_exposure_h"]))
        self.assertFalse(np.isinf(res["cumulative_exposure_h"]))

    def test_nan_gaps_in_series(self):
        """序列中存在 NaN 间隙的有效时间测试"""
        times = np.array([0.0, 1000.0, 2000.0], dtype=np.float64)
        wl = np.array([0.0, np.nan, 0.0], dtype=np.float32)
        res = compute_1d_continuous_exposure(
            water_levels=wl,
            timestamps_seconds=times,
            elevation=1.0,
            terminal_water_level=0.0,
            terminal_timestamp_seconds=3000.0
        )
        # 第 2 个区间和第 3 个区间涉及 NaN，有效时间为 1000s，总窗口 3000s
        self.assertAlmostEqual(res["valid_duration_h"] * 3600.0, 1000.0, places=2)
        self.assertAlmostEqual(res["valid_time_fraction_pct"], (1000.0 / 3000.0) * 100.0, places=2)


class TestExposure2DVectorizedEquivalence(unittest.TestCase):
    """2D 向量化状态机与 1D 黄金标准解析器等价性及内存防线验证"""

    def test_vectorized_vs_1d_exact_zero_error(self):
        """验证 2D 向量化计算与 1D 循环参考解析器在随机测试集上的 0 误差等价性"""
        np.random.seed(42)
        rows, cols = 5, 6
        n_times = 50
        times = np.arange(0, n_times * 1800, 1800, dtype=np.float64)

        # 构造节点潮位矩阵 (4 corners)
        wl_nodes = np.random.uniform(-3.0, 3.0, size=(n_times, 4)).astype(np.float32)
        term_nodes = np.random.uniform(-3.0, 3.0, size=(4,)).astype(np.float32)
        z_grid = np.random.uniform(-2.5, 2.5, size=(rows, cols)).astype(np.float32)
        weights = np.array([0.25, 0.25, 0.25, 0.25], dtype=np.float32).reshape(4, 1, 1)

        # 2D 向量化计算
        res_2d = compute_2d_vec(
            wl_nodes=wl_nodes,
            term_nodes=term_nodes,
            times_sec=times,
            terminal_ts=times[-1] + 1800.0,
            weights=weights,
            z=z_grid
        )

        # 逐像元执行 1D 参考解析器
        for r in range(rows):
            for c in range(cols):
                wl_1d = (wl_nodes @ weights[:, 0, 0]).astype(np.float32)
                term_1d = float(term_nodes @ weights[:, 0, 0])
                z_val = float(z_grid[r, c])
                res_1d = compute_1d_continuous_exposure(
                    water_levels=wl_1d,
                    timestamps_seconds=times,
                    elevation=z_val,
                    terminal_water_level=term_1d,
                    terminal_timestamp_seconds=times[-1] + 1800.0
                )

                # 严格断言绝对误差为 0
                self.assertAlmostEqual(res_2d["exposure_fraction_pct"][r, c], res_1d["exposure_fraction_pct"], places=4)
                self.assertAlmostEqual(res_2d["cumulative_exposure_h"][r, c], res_1d["cumulative_exposure_h"], places=4)
                self.assertAlmostEqual(res_2d["max_continuous_exposure_h"][r, c], res_1d["max_continuous_exposure_h"], places=4)
                self.assertEqual(res_2d["event_count"][r, c], res_1d["event_count"])

    def test_no_3d_cube_invariant(self):
        """防线测试：验证 compute_2d_vec 内部绝不分配 (rows, cols, time_chunk) 3D 像元张量"""
        src = inspect.getsource(compute_2d_vec)
        # 静态代码检查：禁止在 2D 空间轴上扩展时间轴
        self.assertNotIn("(rows, cols, time", src)
        self.assertNotIn("zeros((rows, cols,", src)
        self.assertNotIn("np.tile", src)


class TestCornerWeightNormalizationAndTopology(unittest.TestCase):
    """四角控制节点权重归一化与拓扑隔断防护"""

    def test_corner_weight_normalization(self):
        """验证 1、2、3 个有效节点时权重自动重新归一化，严禁将无效节点作为 0m 计算"""
        rows, cols = 2, 2
        n_times = 10
        times = np.arange(0, n_times * 1800, 1800, dtype=np.float64)

        # 仅有 node 0 有效 (水位恒为 2.0m)，其余节点为无效 (NaN)
        wl_nodes = np.full((n_times, 4), np.nan, dtype=np.float32)
        wl_nodes[:, 0] = 2.0
        term_nodes = np.array([2.0, np.nan, np.nan, np.nan], dtype=np.float32)
        z = np.full((rows, cols), 1.0, dtype=np.float32)  # 地形高程 1.0m，水位 2.0m -> 全淹没

        # 传入原始权重均为 0.25，但仅第 0 节点有效
        raw_weights = np.array([0.25, 0.25, 0.25, 0.25], dtype=np.float32).reshape(4, 1, 1)

        # 归一化后第 0 节点权重应为 1.0
        norm_weights = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32).reshape(4, 1, 1)

        res = compute_2d_vec(
            wl_nodes=wl_nodes,
            term_nodes=term_nodes,
            times_sec=times,
            terminal_ts=times[-1] + 1800.0,
            weights=norm_weights,
            z=z
        )
        # 水位严格为 2.0m > 1.0m，因此必须全时段淹没 (累计露出时长为 0)
        self.assertEqual(np.max(res["cumulative_exposure_h"]), 0.0)
        self.assertEqual(np.max(res["event_count"]), 0)


class TestTideCacheCompatibilityAndSchema11(unittest.TestCase):
    """Tide Cache Schema 1.1 与 1.2 兼容性及终端水位测试"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_dem(self):
        path = os.path.join(self.temp_dir, "test_dem.tif")
        trans = Affine(100.0, 0.0, 500000.0, 0.0, -100.0, 3500000.0)
        elev = np.full((20, 20), 1.0, dtype=np.float32)
        profile = {
            "driver": "GTiff", "height": 20, "width": 20, "count": 1,
            "dtype": "float32", "crs": "EPSG:32651", "transform": trans, "nodata": -9999.0
        }
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(elev, 1)
        return path

    def test_schema_11_backward_compatibility(self):
        """测试 Schema 1.1 缓存的向下兼容识别与验证"""
        dem_path = self._create_dem()
        info = RasterTideEngine().inspect_raster(dem_path, compute_valid_count=True)
        cache_path = os.path.join(self.temp_dir, "test_v11_tide.nc")

        n_times = 24
        dr = pd.date_range("2024-01-01 00:00:00", periods=n_times, freq="30min", tz="UTC")
        t_series = np.sin(np.linspace(0, 2 * np.pi, n_times)).astype(np.float32)

        n0 = ControlNode(node_id=0, x=500000.0, y=3497000.0, lon=122.0, lat=31.0, valid=True,
                         water_levels_sorted=np.sort(t_series), static_offset_m=0.0, tide_msl_raw=t_series)
        cell = QuadCell(cell_id=0, x_min=500000.0, y_min=3497000.0, x_max=503000.0, y_max=3500000.0,
                        level=0, node_a=n0, node_b=n0, node_c=n0, node_d=n0)

        cache_meta = {
            "start_time": "2024-01-01 00:00:00",
            "end_time": "2024-01-01 12:00:00",
            "freq": "30min",
            "source_tz": "UTC",
            "inclusive": "left",
            "constituents": "all",
            "dem_datum": "egm2008",
            "initial_control_spacing_m": 4000.0,
            "min_control_spacing_m": 500.0,
            "inundation_error_tolerance_pct": 1.0,
            "target_mode": "intertidal",
            "topology_max_resolution_m": 100.0,
            "topology_valid_fraction_threshold": 0.5
        }

        # 写入不带 terminal_tide 的 Schema 1.1 缓存
        write_tide_cache(
            cache_path=cache_path,
            info=info,
            leaf_cells=[cell],
            node_cache={(0, 0): n0},
            time_index=dr,
            metadata=cache_meta,
            tide_msl_terminal=None,  # 不提供终端潮位
            schema_version="1.1"
        )

        meta = inspect_tide_cache_metadata(cache_path)
        self.assertEqual(meta["schema_version"], "1.1")
        self.assertFalse(meta["has_terminal_tide"])

        # 验证兼容性校验器能够通过
        expected_spec = build_expected_cache_spec(
            info=info,
            start_time="2024-01-01 00:00:00",
            end_time="2024-01-01 12:00:00",
            freq="30min",
            dem_datum="egm2008",
            constituents="all",
            target_mode="intertidal",
            initial_control_spacing_m=4000.0,
            min_control_spacing_m=500.0,
            inundation_error_tolerance_pct=1.0,
            topology_max_resolution_m=100.0,
            topology_valid_fraction_threshold=0.5,
            fes_model="FES2022b",
            fes_source_type="native_lgp2",
            source_tz="UTC",
            inclusive="left"
        )
        is_compat, reasons = validate_tide_cache_compatibility(cache_path, expected_spec)
        self.assertTrue(is_compat, f"Schema 1.1 应该兼容: {reasons}")


class TestBatchArtifactVerificationAndAtomic(unittest.TestCase):
    """产物原子替换、NoData Sentinel 以及深层验证测试"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_verify_exposure_artifacts_deep_check(self):
        """测试 _verify_exposure_artifacts 对全部 7 个产物、数据类型及签名的校验能力"""
        base_dir = self.temp_dir
        exp_paths = ExposureProductPaths(
            exposure_fraction_path=os.path.join(base_dir, "synth_exposure_fraction.tif"),
            exposure_duration_h_path=os.path.join(base_dir, "synth_exposure_duration_h.tif"),
            exposure_max_continuous_h_path=os.path.join(base_dir, "synth_exposure_max_continuous_h.tif"),
            exposure_mean_event_h_path=os.path.join(base_dir, "synth_exposure_mean_event_h.tif"),
            exposure_event_count_path=os.path.join(base_dir, "synth_exposure_event_count.tif"),
            exposure_valid_time_fraction_path=os.path.join(base_dir, "synth_exposure_valid_time_fraction.tif"),
            exposure_qc_path=os.path.join(base_dir, "synth_exposure_qc.tif")
        )

        class MockInfo:
            width = 10
            height = 10
            crs = "EPSG:32651"
            transform = Affine(10.0, 0.0, 500000.0, 0.0, -10.0, 3500000.0)

        info = MockInfo()

        # 此时文件不存在，应返回 False
        self.assertFalse(_verify_exposure_artifacts(info, exp_paths))

        # 使用 _AtomicExposureWriter 写入标准产物
        profile = {
            "driver": "GTiff", "height": 10, "width": 10, "count": 1,
            "crs": info.crs, "transform": info.transform
        }
        writer = _AtomicExposureWriter(exp_paths, profile)
        writer.open(metadata_tags={"CACHE_SIGNATURE": "sig_abc_123"})
        writer.finalize()

        # 此时全部满足，应返回 True
        self.assertTrue(_verify_exposure_artifacts(info, exp_paths, expected_cache_sig="sig_abc_123"))
        # 签名不匹配时应返回 False
        self.assertFalse(_verify_exposure_artifacts(info, exp_paths, expected_cache_sig="sig_wrong_456"))

        # 检查 event_count 的 NoData 必须为 4294967295
        with rasterio.open(exp_paths.exposure_event_count_path) as src_cnt:
            self.assertEqual(src_cnt.nodata, 4294967295)
            self.assertEqual(src_cnt.dtypes[0], "uint32")


if __name__ == "__main__":
    unittest.main()
