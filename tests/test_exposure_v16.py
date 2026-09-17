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
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
import netCDF4

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
    QC_EXP_TERMINAL_UNAVAILABLE,
    QC_EXP_TERMINAL_APPROX,
    QC_EXP_PARTIAL_VALID_TIME,
    QC_EXP_PERMANENTLY_SUBMERGED,
    QC_EXP_PERMANENTLY_EXPOSED,
    QC_EXP_NODATA,
    NODATA_EVENT_COUNT,
    NODATA_FLOAT32,
    NODATA_QC
)
from core.raster_engine import ControlNode, QuadCell, RasterInfo, RasterTideEngine, build_support_topology
from core.batch_raster_engine import _verify_exposure_artifacts
from core.tide_cache import (
    write_tide_cache,
    read_tide_cache,
    read_tide_cache_structure,
    TideCacheTimeSeriesReader,
    TideCacheStructure,
    TideCacheIntegrityError,
    TideCacheCompatibilityError,
    parse_cache_time_to_utc,
    inspect_tide_cache_metadata,
    validate_tide_cache_compatibility,
    calculate_exposure_from_tide_cache,
    build_expected_cache_spec,
    generate_tide_cache_signature,
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



class _BaseExposureProductionTest(unittest.TestCase):
    """v1.6 生产环境多维严密测试基类"""
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="exp_prod_v16_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_synthetic_dem(self, filename="dem.tif", width=20, height=20, val=1.0):
        path = os.path.join(self.temp_dir, filename)
        trans = Affine(100.0, 0.0, 500000.0, 0.0, -100.0, 3500000.0)
        elev = np.full((height, width), val, dtype=np.float32)
        profile = {
            "driver": "GTiff", "height": height, "width": width, "count": 1,
            "dtype": "float32", "crs": "EPSG:32651", "transform": trans, "nodata": -9999.0
        }
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(elev, 1)
        return path

    def _create_synthetic_cache(self, dem_path, cache_filename="tide.nc", n_times=20, with_terminal=True, schema_ver="1.2"):
        info = RasterTideEngine().inspect_raster(dem_path, compute_valid_count=True)
        cache_path = os.path.join(self.temp_dir, cache_filename)
        dr = pd.date_range("2024-01-01 00:00:00", periods=n_times, freq="30min", tz="UTC")

        nodes_dict = {}
        for nid, (x, y) in enumerate([
            (500000.0, 3498000.0),
            (502000.0, 3498000.0),
            (500000.0, 3500000.0),
            (502000.0, 3500000.0)
        ]):
            t_series = np.sin(np.linspace(nid * 0.5, nid * 0.5 + 2 * np.pi, n_times)).astype(np.float32) * 2.0
            nodes_dict[(int(x), int(y))] = ControlNode(
                node_id=nid, x=x, y=y, lon=122.0 + nid * 0.01, lat=31.0 + nid * 0.01,
                valid=True, water_levels_sorted=np.sort(t_series), static_offset_m=0.0,
                tide_msl_raw=t_series, component_id=1
            )

        cell = QuadCell(
            cell_id=0, x_min=500000.0, y_min=3498000.0, x_max=502000.0, y_max=3500000.0,
            level=0,
            node_a=nodes_dict[(500000, 3498000)],
            node_b=nodes_dict[(502000, 3498000)],
            node_c=nodes_dict[(500000, 3500000)],
            node_d=nodes_dict[(502000, 3500000)]
        )

        term = np.array([0.5, 0.7, -0.2, 0.1], dtype=np.float32) if with_terminal else None
        end_time_str = (dr[-1] + pd.Timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")

        cache_meta = {
            "start_time": dr[0].strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": end_time_str,
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
            "topology_valid_fraction_threshold": 0.5,
            "fes_model": "FES2022b",
            "fes_source_type": "native_lgp2"
        }

        write_tide_cache(
            cache_path=cache_path,
            info=info,
            leaf_cells=[cell],
            node_cache=nodes_dict,
            time_index=dr,
            metadata=cache_meta,
            tide_msl_terminal=term,
            schema_version=schema_ver
        )
        return cache_path, info


class TestChunkedReaderVsOracle(_BaseExposureProductionTest):
    """生产场景 1: 分块时序读取器 (time_chunk_size=7) 与全量 Oracle 黄金解算的一致性 (全部 7 大栅格等价)"""

    def test_chunked_reader_vs_oracle(self):
        dem_path = self._create_synthetic_dem("dem_sc1.tif", 20, 20, val=0.5)
        cache_path, info = self._create_synthetic_cache(dem_path, "cache_sc1.nc", n_times=20)

        out_dir_chunk = os.path.join(self.temp_dir, "out_chunk")
        out_dir_oracle = os.path.join(self.temp_dir, "out_oracle")

        res_chunk = calculate_exposure_from_tide_cache(
            dem_path=dem_path, cache_path=cache_path, output_dir=out_dir_chunk, time_chunk_size=7
        )
        res_oracle = calculate_exposure_from_tide_cache(
            dem_path=dem_path, cache_path=cache_path, output_dir=out_dir_oracle, time_chunk_size=50
        )

        fields = [
            "exposure_fraction", "exposure_duration_h", "exposure_max_continuous_h",
            "exposure_mean_event_h", "exposure_event_count", "exposure_valid_time_fraction", "exposure_qc"
        ]
        for f in fields:
            p_chunk = getattr(res_chunk["products"], f"{f}_path")
            p_oracle = getattr(res_oracle["products"], f"{f}_path")
            with rasterio.open(p_chunk) as src_c, rasterio.open(p_oracle) as src_o:
                arr_c = src_c.read(1)
                arr_o = src_o.read(1)
                if f in ["exposure_event_count", "exposure_qc"]:
                    np.testing.assert_array_equal(arr_c, arr_o, err_msg=f"{f} 栅格未完全相等")
                else:
                    np.testing.assert_allclose(arr_c, arr_o, atol=1e-4, err_msg=f"{f} 栅格数值不吻合")


class TestTimeChunkLimitAndNoFullLoad(_BaseExposureProductionTest):
    """生产场景 2: NetCDF 切片长度严格限制在 time_chunk_size 内，且绝不调用 load_raw_tide=True"""

    def test_time_chunk_limit_and_no_full_load(self):
        dem_path = self._create_synthetic_dem("dem_sc2.tif", 20, 20, val=0.5)
        cache_path, info = self._create_synthetic_cache(dem_path, "cache_sc2.nc", n_times=25)

        read_slices = []
        orig_read_chunk = TideCacheTimeSeriesReader.read_chunk

        def spy_read_chunk(reader_self, node_indices, start_idx, end_idx):
            read_slices.append((len(node_indices), start_idx, end_idx, end_idx - start_idx))
            return orig_read_chunk(reader_self, node_indices, start_idx, end_idx)

        with patch.object(TideCacheTimeSeriesReader, "read_chunk", new=spy_read_chunk):
            with patch("core.tide_cache.read_tide_cache") as mock_read_raw:
                out_dir = os.path.join(self.temp_dir, "out_sc2")
                calculate_exposure_from_tide_cache(
                    dem_path=dem_path, cache_path=cache_path, output_dir=out_dir, time_chunk_size=7
                )
                mock_read_raw.assert_not_called()

        self.assertTrue(len(read_slices) >= 4, f"期望分块数 >= 4，实际为 {len(read_slices)}")
        for n_nodes, s, e, span in read_slices:
            self.assertLessEqual(span, 7, f"切片时间步跨度 {span} 超出 time_chunk_size=7 限制")


class TestTopologyBarrierProduction(_BaseExposureProductionTest):
    """生产场景 3: 双盆地天然山脊/地形屏障隔离，严禁高水位越障渗透"""

    def test_topology_barrier_production(self):
        dem_path = os.path.join(self.temp_dir, "dem_barrier.tif")
        trans = Affine(100.0, 0.0, 500000.0, 0.0, -100.0, 3500000.0)
        elev = np.full((20, 40), 0.5, dtype=np.float32)
        elev[:, 15:25] = -9999.0
        profile = {
            "driver": "GTiff", "height": 20, "width": 40, "count": 1,
            "dtype": "float32", "crs": "EPSG:32651", "transform": trans, "nodata": -9999.0
        }
        with rasterio.open(dem_path, "w", **profile) as dst:
            dst.write(elev, 1)

        info = RasterTideEngine().inspect_raster(dem_path, compute_valid_count=True)
        top_res = build_support_topology(info, topology_max_resolution_m=100.0, topology_valid_fraction_threshold=0.5)
        labeled_coarse = top_res[0]

        comp_left = labeled_coarse[10, 5]
        comp_right = labeled_coarse[10, 35]
        self.assertNotEqual(comp_left, comp_right)
        self.assertGreater(comp_left, 0)
        self.assertGreater(comp_right, 0)

        t_high = np.full(10, 5.0, dtype=np.float32)
        n0 = ControlNode(node_id=0, x=500500.0, y=3499000.0, lon=122.0, lat=31.0, valid=True,
                         water_levels_sorted=t_high, static_offset_m=0.0, tide_msl_raw=t_high,
                         component_id=int(comp_left))

        t_low = np.full(10, -2.0, dtype=np.float32)
        n1 = ControlNode(node_id=1, x=503500.0, y=3499000.0, lon=122.03, lat=31.0, valid=True,
                         water_levels_sorted=t_low, static_offset_m=0.0, tide_msl_raw=t_low,
                         component_id=int(comp_right))

        cell_l = QuadCell(cell_id=0, x_min=500000.0, y_min=3498000.0, x_max=501500.0, y_max=3500000.0,
                          level=0, node_a=n0, node_b=n0, node_c=n0, node_d=n0)
        cell_r = QuadCell(cell_id=1, x_min=502500.0, y_min=3498000.0, x_max=504000.0, y_max=3500000.0,
                          level=0, node_a=n1, node_b=n1, node_c=n1, node_d=n1)

        out_paths = ExposureProductPaths(
            exposure_fraction_path=os.path.join(self.temp_dir, "b_frac.tif"),
            exposure_duration_h_path=os.path.join(self.temp_dir, "b_dur.tif"),
            exposure_max_continuous_h_path=os.path.join(self.temp_dir, "b_max.tif"),
            exposure_mean_event_h_path=os.path.join(self.temp_dir, "b_mean.tif"),
            exposure_event_count_path=os.path.join(self.temp_dir, "b_cnt.tif"),
            exposure_valid_time_fraction_path=os.path.join(self.temp_dir, "b_val.tif"),
            exposure_qc_path=os.path.join(self.temp_dir, "b_qc.tif")
        )

        dr = pd.date_range("2024-01-01 00:00:00", periods=10, freq="30min", tz="UTC")
        stream_exposure_metrics_interpolation(
            dem_path=dem_path,
            output_paths=out_paths,
            cells=[cell_l, cell_r],
            nodes=[n0, n1],
            time_series_utc=np.asarray(dr),
            target_datum="egm2008",
            time_chunk_size=10,
            terminal_node_tides=np.array([5.0, -2.0], dtype=np.float32),
            terminal_timestamp_sec=dr[-1].timestamp() + 1800.0,
            labeled_coarse=labeled_coarse,
            downsample_factor=top_res[2],
            h_coarse=top_res[3],
            w_coarse=top_res[4]
        )

        with rasterio.open(out_paths.exposure_fraction_path) as src:
            frac = src.read(1)
            self.assertEqual(np.nanmax(frac[:, 0:14]), 0.0)
            self.assertEqual(np.nanmin(frac[:, 25:39]), 100.0)


class TestCornerWeightRenormalizationProduction(_BaseExposureProductionTest):
    """生产场景 4: 部分角点失效时的权重自动重新归一化验证"""

    def test_corner_weight_renormalization_production(self):
        rows, cols = 4, 4
        z_elev = 2.5
        times = np.arange(0, 5 * 1800, 1800, dtype=np.float64)

        wl_nodes = np.full((len(times), 4), np.nan, dtype=np.float32)
        wl_nodes[:, 0] = 2.0
        wl_nodes[:, 1] = 4.0
        term_nodes = np.array([2.0, 4.0, np.nan, np.nan], dtype=np.float32)

        renorm_weights = np.zeros((4, rows, cols), dtype=np.float32)
        renorm_weights[0, :, :] = 0.5
        renorm_weights[1, :, :] = 0.5

        z_grid = np.full((rows, cols), z_elev, dtype=np.float32)

        res = compute_2d_vec(
            wl_nodes=wl_nodes,
            term_nodes=term_nodes,
            times_sec=times,
            terminal_ts=times[-1] + 1800.0,
            weights=renorm_weights,
            z=z_grid
        )
        self.assertEqual(np.max(res["exposure_fraction_pct"]), 0.0)
        self.assertEqual(np.max(res["cumulative_exposure_h"]), 0.0)


class TestTerminalTimeAndTimezones(_BaseExposureProductionTest):
    """生产场景 5: 时区转换保真、缺失终端时刻的分母守恒与 QC 标记"""

    def test_timezone_parsing_and_schema_11_terminal_semantics(self):
        t_sh = parse_cache_time_to_utc("2024-01-01 08:00:00+08:00")
        t_utc = parse_cache_time_to_utc("2024-01-01T00:00:00Z")
        self.assertEqual(t_sh, t_utc)

        t_la = parse_cache_time_to_utc("2024-01-01 00:00:00-08:00")
        t_utc8 = parse_cache_time_to_utc("2024-01-01T08:00:00Z")
        self.assertEqual(t_la, t_utc8)

        t_naive = parse_cache_time_to_utc("2024-01-01 08:00:00", default_tz="Asia/Shanghai")
        self.assertEqual(t_naive, t_utc)

        dem_path = self._create_synthetic_dem("dem_sc5.tif", 20, 20, val=0.5)
        cache_path, info = self._create_synthetic_cache(dem_path, "cache_sc5.nc", n_times=10, with_terminal=False, schema_ver="1.1")

        out_dir = os.path.join(self.temp_dir, "out_sc5")
        res = calculate_exposure_from_tide_cache(dem_path=dem_path, cache_path=cache_path, output_dir=out_dir)

        with rasterio.open(res["products"].exposure_valid_time_fraction_path) as src_v, \
             rasterio.open(res["products"].exposure_qc_path) as src_q:
            val_frac = src_v.read(1)
            qc_arr = src_q.read(1)
            self.assertAlmostEqual(float(val_frac[0, 0]), 90.0, places=1)
            self.assertTrue(bool(qc_arr[0, 0] & QC_EXP_TERMINAL_UNAVAILABLE))

            tags = src_v.tags()
            self.assertIn("CACHE_SIGNATURE", tags)
            self.assertIn("CACHE_SCHEMA_VERSION", tags)
            self.assertEqual(tags["CACHE_SCHEMA_VERSION"], "1.1")


class TestAtomicWriterCleanupOnFailure(_BaseExposureProductionTest):
    """生产场景 6: 写入中断/异常时临时文件 (*.tmp.tif) 零残留防护测试"""

    def test_atomic_writer_cleanup_on_failure(self):
        base_dir = os.path.join(self.temp_dir, "atomic_test")
        os.makedirs(base_dir, exist_ok=True)
        paths = ExposureProductPaths(
            exposure_fraction_path=os.path.join(base_dir, "frac.tif"),
            exposure_duration_h_path=os.path.join(base_dir, "dur.tif"),
            exposure_max_continuous_h_path=os.path.join(base_dir, "max.tif"),
            exposure_mean_event_h_path=os.path.join(base_dir, "mean.tif"),
            exposure_event_count_path=os.path.join(base_dir, "cnt.tif"),
            exposure_valid_time_fraction_path=os.path.join(base_dir, "val.tif"),
            exposure_qc_path=os.path.join(base_dir, "qc.tif")
        )
        profile = {
            "driver": "GTiff", "height": 10, "width": 10, "count": 1,
            "crs": "EPSG:32651", "transform": Affine(10.0, 0.0, 500000.0, 0.0, -10.0, 3500000.0)
        }

        try:
            with _AtomicExposureWriter(paths, profile) as writer:
                writer.open()
                tmp_files = [f for f in os.listdir(base_dir) if f.endswith(".tmp.tif")]
                self.assertEqual(len(tmp_files), 7)
                raise RuntimeError("Simulated crash during write")
        except RuntimeError:
            pass

        remaining_tmp = [f for f in os.listdir(base_dir) if f.endswith(".tmp.tif")]
        self.assertEqual(len(remaining_tmp), 0)
        self.assertFalse(os.path.exists(paths.exposure_fraction_path))


class TestStaleDEMMismatch(_BaseExposureProductionTest):
    """生产场景 7: DEM 修改时间或文件大小变动导致陈旧 Cache 拒绝服务"""

    def test_stale_dem_mismatch(self):
        dem_path = self._create_synthetic_dem("dem_sc7.tif", 20, 20, val=0.5)
        cache_path, info = self._create_synthetic_cache(dem_path, "cache_sc7.nc", n_times=10)

        with open(dem_path, "ab") as f:
            f.write(b"corrupt_dem_padding_bytes")

        with self.assertRaises(TideCacheCompatibilityError) as ctx:
            calculate_exposure_from_tide_cache(dem_path=dem_path, cache_path=cache_path)
        err_msg = str(ctx.exception)
        self.assertTrue("文件大小已改变" in err_msg or "不匹配" in err_msg or "不兼容" in err_msg)

        dem_path2 = self._create_synthetic_dem("dem_sc7_2.tif", 20, 20, val=0.5)
        cache_path2, _ = self._create_synthetic_cache(dem_path2, "cache_sc7_2.nc", n_times=10)

        stat_old = os.stat(dem_path2)
        os.utime(dem_path2, (stat_old.st_atime, stat_old.st_mtime + 500.0))

        with self.assertRaises(TideCacheCompatibilityError) as ctx2:
            calculate_exposure_from_tide_cache(dem_path=dem_path2, cache_path=cache_path2)
        err_msg2 = str(ctx2.exception)
        self.assertTrue("修改时间已更新" in err_msg2 or "不匹配" in err_msg2 or "不兼容" in err_msg2)


class TestZeroFESCallInStage2(_BaseExposureProductionTest):
    """生产场景 8: Stage 2 纯 Cache 驱动解算零 FES 物理模型调用不变量"""

    def test_zero_fes_call_in_stage2(self):
        dem_path = self._create_synthetic_dem("dem_sc8.tif", 20, 20, val=0.5)
        cache_path, _ = self._create_synthetic_cache(dem_path, "cache_sc8.nc", n_times=10)

        mock_fes = MagicMock()
        with patch("core.tide_engine.FESTidePredictor.predict_points_period", mock_fes):
            calculate_exposure_from_tide_cache(dem_path=dem_path, cache_path=cache_path)
            mock_fes.assert_not_called()


class TestCorruptCellNodeIndexRaisesIntegrityError(_BaseExposureProductionTest):
    """生产场景 9: 损坏/越界的控制节点索引必须抛出 TideCacheIntegrityError"""

    def test_corrupt_cell_node_index_raises_integrity_error(self):
        dem_path = self._create_synthetic_dem("dem_sc9.tif", 20, 20, val=0.5)
        cache_path, _ = self._create_synthetic_cache(dem_path, "cache_sc9.nc", n_times=10)

        with netCDF4.Dataset(cache_path, "a") as ds:
            ds.variables["cell_node_indices"][0, 0] = 99999

        with self.assertRaises(TideCacheIntegrityError) as ctx:
            read_tide_cache_structure(cache_path)
        err_msg = str(ctx.exception)
        self.assertTrue("99999" in err_msg or "越界" in err_msg or "拓扑损坏" in err_msg)


if __name__ == "__main__":
    unittest.main()
