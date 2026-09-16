"""
CoastTideX v1.6 单元测试集 - 潜在天文潮露出时间域分析与 Tide Cache Schema 1.2 验证
Unit Test Suite for Potential Tidal Exposure Engine and Tide Cache Schema 1.2
文件路径: tests/test_exposure_v16.py
"""

import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import Affine

from core.exposure_engine import (
    compute_1d_continuous_exposure,
    stream_exposure_metrics_interpolation,
    ExposureProductPaths,
    QC_EXP_VALID,
    QC_EXP_PERMANENTLY_SUBMERGED,
    QC_EXP_PERMANENTLY_EXPOSED,
    QC_EXP_TERMINAL_APPROX,
    QC_EXP_NODATA
)
from core.raster_engine import ControlNode, QuadCell, RasterInfo
from core.tide_cache import (
    write_tide_cache,
    read_tide_cache,
    inspect_tide_cache_metadata,
    calculate_exposure_from_tide_cache,
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
        """全时段常时露出测试 (H(t) < z)"""
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
        # 水位从 0m 线性上升到 10m，耗时 1000秒；z = 4m
        # 水位 <= 4m 为露出，对应 [0, 400s]，露出时长精确为 400s
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

    def test_periodic_triangle_wave_events(self):
        """对称三角波周期性露出事件与频次统计"""
        # 构造周期 2 小时的三角波: 0 -> 2m -> 0 -> 2m
        # 时间采样 10 分钟 (600s)
        t = np.arange(0, 7200 + 600, 600, dtype=np.float64)
        wl = np.where(t <= 3600, 2.0 * (t / 3600.0), 2.0 * (1.0 - (t - 3600.0) / 3600.0)).astype(np.float32)
        z = 1.0  # 中线 1.0m
        # 预期露出发生在 [0, 1800s] 与 [5400s, 7200s]
        res = compute_1d_continuous_exposure(wl, t, z)
        self.assertAlmostEqual(res["cumulative_exposure_h"], 1.0, places=2)
        self.assertAlmostEqual(res["exposure_fraction_pct"], 50.0, places=1)


class TestExposure2DAndTideCacheSchema12(unittest.TestCase):
    """二维空间流式累加与 Tide Cache Schema 1.2 端到端集成测试"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_synthetic_dem(self, filename="synthetic_dem.tif", width=30, height=30):
        """生成合成测试 DEM 栅格"""
        path = os.path.join(self.temp_dir, filename)
        trans = Affine(100.0, 0.0, 500000.0, 0.0, -100.0, 3500000.0)
        elev = np.linspace(-2.0, 4.0, width * height, dtype=np.float32).reshape((height, width))
        elev[0, 0] = -9999.0  # 注入 NoData 点

        profile = {
            "driver": "GTiff",
            "height": height,
            "width": width,
            "count": 1,
            "dtype": "float32",
            "crs": "EPSG:32651",
            "transform": trans,
            "nodata": -9999.0
        }
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(elev, 1)
        return path

    def test_schema_12_write_read_and_exposure_integration(self):
        """验证 Schema 1.2 写入、终端潮位持久化以及零 FES 露出产物生成"""
        dem_path = self._create_synthetic_dem()

        # 构造自适应四叉树 4 节点合成控制网格
        n_times = 48  # 48 个半小时 (24小时)
        dr = pd.date_range("2024-01-01 00:00:00", periods=n_times, freq="30min", tz="UTC")
        t_series = np.sin(np.linspace(0, 4 * np.pi, n_times)).astype(np.float32) * 2.0  # 振幅 +-2m

        n0 = ControlNode(node_id=0, x=500000.0, y=3497000.0, lon=122.0, lat=31.0, valid=True,
                         water_levels_sorted=np.sort(t_series), static_offset_m=0.0, tide_msl_raw=t_series)
        n1 = ControlNode(node_id=1, x=503000.0, y=3497000.0, lon=122.03, lat=31.0, valid=True,
                         water_levels_sorted=np.sort(t_series), static_offset_m=0.0, tide_msl_raw=t_series)
        n2 = ControlNode(node_id=2, x=500000.0, y=3500000.0, lon=122.0, lat=31.03, valid=True,
                         water_levels_sorted=np.sort(t_series), static_offset_m=0.0, tide_msl_raw=t_series)
        n3 = ControlNode(node_id=3, x=503000.0, y=3500000.0, lon=122.03, lat=31.03, valid=True,
                         water_levels_sorted=np.sort(t_series), static_offset_m=0.0, tide_msl_raw=t_series)

        cell = QuadCell(
            cell_id=0,
            x_min=500000.0, y_min=3497000.0, x_max=503000.0, y_max=3500000.0,
            level=0, node_a=n0, node_b=n1, node_c=n2, node_d=n3
        )

        from core.raster_engine import RasterTideEngine
        info = RasterTideEngine().inspect_raster(dem_path, compute_valid_count=True)

        cache_path = os.path.join(self.temp_dir, "test_cache_tide.nc")
        terminal_wl = np.array([1.5, 1.5, 1.5, 1.5], dtype=np.float32)

        cache_meta = {
            "start_time": "2024-01-01 00:00:00",
            "end_time": "2024-01-02 00:00:00",
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

        # 1. 写入 Schema 1.2 Tide Cache
        write_tide_cache(
            cache_path=cache_path,
            info=info,
            leaf_cells=[cell],
            node_cache={(0, 0): n0, (1, 0): n1, (0, 1): n2, (1, 1): n3},
            time_index=dr,
            metadata=cache_meta,
            tide_msl_terminal=terminal_wl
        )

        # 2. 验证元数据与终端潮位存在
        meta_info = inspect_tide_cache_metadata(cache_path)
        self.assertTrue(meta_info["is_complete"])
        self.assertTrue(meta_info["has_terminal_tide"])
        self.assertEqual(meta_info["schema_version"], "1.2")

        # 3. 读取缓存并校验终端潮位
        loaded = read_tide_cache(cache_path, load_raw_tide=True)
        self.assertIsNotNone(loaded["terminal_tide"])
        self.assertEqual(len(loaded["terminal_tide"]), 4)
        self.assertAlmostEqual(float(loaded["terminal_tide"][0]), 1.5, places=3)
        self.assertIsNotNone(loaded["nodes"][0].tide_msl_raw)

        # 4. 执行零 FES 潜在露出时间域栅格反演
        res = calculate_exposure_from_tide_cache(
            dem_path=dem_path,
            cache_path=cache_path,
            output_dir=self.temp_dir,
            base_name="synth",
            block_size=16,
            time_chunk_size=20
        )
        self.assertEqual(res["status"], "COMPLETED")
        prods: ExposureProductPaths = res["products"]

        # 5. 校验 7 大产物文件均已成功生成
        for p in [
            prods.exposure_fraction_path,
            prods.exposure_duration_h_path,
            prods.exposure_max_continuous_h_path,
            prods.exposure_mean_event_h_path,
            prods.exposure_event_count_path,
            prods.exposure_valid_time_fraction_path,
            prods.exposure_qc_path
        ]:
            self.assertTrue(os.path.exists(p), f"产物文件不存在: {p}")
            with rasterio.open(p) as src_p:
                self.assertEqual(src_p.width, 30)
                self.assertEqual(src_p.height, 30)

        # 6. 检查 NoData 掩膜与物理合理性
        with rasterio.open(prods.exposure_qc_path) as src_qc:
            qc_data = src_qc.read(1)
            self.assertEqual(qc_data[0, 0], QC_EXP_NODATA)

        with rasterio.open(prods.exposure_fraction_path) as src_frac:
            frac_data = src_frac.read(1)
            val_fracs = frac_data[frac_data >= 0.0]
            self.assertTrue(np.all(val_fracs >= 0.0))
            self.assertTrue(np.all(val_fracs <= 100.0))


if __name__ == "__main__":
    unittest.main()
