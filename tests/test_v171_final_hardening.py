"""
CoastTideX v1.7.1 单元测试套件:
最终科学与生产级加固验证 (Final Scientific & Production Hardening Test Suite)

覆盖内容:
1. 距离配置与严格校验 (0-500 km, 默认 100 km, 0 km 原生 MDT 模式, 非法值报错)
2. 动态 MDT 空间索引外包框与高纬度经度膨胀 (Dynamic support window & high-latitude lon pad)
3. 跨越 180° 经线 (Antimeridian / International Date Line) 最小区间与空间插值连续性
4. 多线程并发安全性 (Thread-safety with workers > 1, thread-local converter isolation)
5. 质量控制掩膜输出优化 (write_qc 开关与磁盘 I/O 节省)
6. 严格断点恢复验证 (Resume multi-factor integrity verification)
7. 输出元数据一致性与科学引用溯源 (Metadata consistency & Adapted citation)
"""

import os
import sys
import time
import math
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from unittest.mock import patch, MagicMock
import numpy as np

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

try:
    import rasterio
    from rasterio.transform import from_bounds, from_origin
    HAS_RASTERIO = True
except ImportError:
    HAS_RASTERIO = False

from core.dem_datum_converter import (
    DEMDatumConverter,
    convert_dem_to_msl,
    validate_mdt_extrapolation_distance,
    compute_minimal_circular_longitude_interval,
    DEFAULT_MDT_EXTRAPOLATION_DISTANCE_KM,
    MIN_MDT_EXTRAPOLATION_DISTANCE_KM,
    MAX_ALLOWED_MDT_EXTRAPOLATION_DISTANCE_KM,
    QC_MDT_NATIVE,
    QC_MDT_EXTRAPOLATED,
    QC_MDT_NODATA,
    DEMConversionSummary,
)
from core.batch_datum_converter import (
    BatchDEMDatumConverter,
    BatchConversionManifest,
    compute_conversion_signature,
    verify_resume_skip,
    STATUS_SUCCESS,
    STATUS_SKIPPED_RESUME,
    STATUS_SKIPPED_MSL,
    STATUS_FAILED,
)


class TestDistanceValidation(unittest.TestCase):
    """测试 1: 外推距离配置与严格校验逻辑 (0.0 - 500.0 km)"""

    def test_default_and_boundaries(self):
        self.assertEqual(DEFAULT_MDT_EXTRAPOLATION_DISTANCE_KM, 100.0)
        self.assertEqual(MIN_MDT_EXTRAPOLATION_DISTANCE_KM, 0.0)
        self.assertEqual(MAX_ALLOWED_MDT_EXTRAPOLATION_DISTANCE_KM, 500.0)

        # 边界与典型合法值
        self.assertEqual(validate_mdt_extrapolation_distance(0.0), 0.0)
        self.assertEqual(validate_mdt_extrapolation_distance(0), 0.0)
        self.assertEqual(validate_mdt_extrapolation_distance(10.0), 10.0)
        self.assertEqual(validate_mdt_extrapolation_distance(50.0), 50.0)
        self.assertEqual(validate_mdt_extrapolation_distance(100.0), 100.0)
        self.assertEqual(validate_mdt_extrapolation_distance(250.5), 250.5)
        self.assertEqual(validate_mdt_extrapolation_distance(500.0), 500.0)
        # None 安全缺省为 100.0 km
        self.assertEqual(validate_mdt_extrapolation_distance(None), 100.0)

    def test_out_of_range_raises_value_error(self):
        # 负数必须严格报错，严禁默默截断
        with self.assertRaises(ValueError):
            validate_mdt_extrapolation_distance(-0.001)

        with self.assertRaises(ValueError):
            validate_mdt_extrapolation_distance(-100.0)

        # 超过 500 km 必须严格报错
        with self.assertRaises(ValueError):
            validate_mdt_extrapolation_distance(500.001)

        with self.assertRaises(ValueError):
            validate_mdt_extrapolation_distance(1000.0)

    def test_nan_inf_and_invalid_types_raise_value_error(self):
        with self.assertRaises(ValueError):
            validate_mdt_extrapolation_distance(float('nan'))

        with self.assertRaises(ValueError):
            validate_mdt_extrapolation_distance(float('inf'))

        with self.assertRaises(ValueError):
            validate_mdt_extrapolation_distance(float('-inf'))

        with self.assertRaises(ValueError):
            validate_mdt_extrapolation_distance("not_a_number")


class TestZeroKmExtrapolation(unittest.TestCase):
    """测试 2: 0 km 模式 (仅原生大洋 MDT，不执行 IDW 外推)"""

    def test_zero_km_disables_idw(self):
        converter = DEMDatumConverter(max_extrapolation_distance_km=0.0)
        self.assertEqual(converter.max_extrapolation_distance_km, 0.0)
        self.assertEqual(converter.max_extrapolation_distance_m, 0.0)

        mock_lons = np.array([124.0, 121.0])
        mock_lats = np.array([31.0, 31.0])

        with patch.object(converter, '_ensure_mdt_spatial_index'):
            converter._crosses_antimeridian = False
            converter._mdt_interp = MagicMock(return_value=np.array([0.5, np.nan], dtype=np.float32))
            converter._ocean_kdtree = MagicMock()
            converter._ocean_mdt_values = np.array([0.5], dtype=np.float32)

            mdt_out, qc_out = converter.evaluate_mdt_with_idw(mock_lons, mock_lats)

            # 点 0: 原生大洋点 (0.5m, QC=0)
            self.assertAlmostEqual(mdt_out[0], 0.5, places=4)
            self.assertEqual(qc_out[0], QC_MDT_NATIVE)

            # 点 1: 陆地/无原生点，0 km 门禁下绝不进行 IDW 外推，保持 NaN 和 QC_MDT_NODATA (2)
            self.assertTrue(np.isnan(mdt_out[1]))
            self.assertEqual(qc_out[1], QC_MDT_NODATA)


class TestDynamicSupportWindowAndAntimeridian(unittest.TestCase):
    """测试 3: 动态支持窗口与 180° 经线跨越"""

    def test_minimal_circular_longitude_interval(self):
        # 1. 正常东经区间 [120, 125]
        min_l, max_l, crosses = compute_minimal_circular_longitude_interval([120.0, 125.0])
        self.assertFalse(crosses)
        self.assertAlmostEqual(min_l, 120.0)
        self.assertAlmostEqual(max_l, 125.0)

        # 2. 正常西经区间 [-125, -120]
        min_l, max_l, crosses = compute_minimal_circular_longitude_interval([-125.0, -120.0])
        self.assertFalse(crosses)
        self.assertAlmostEqual(min_l, -125.0)
        self.assertAlmostEqual(max_l, -120.0)

        # 3. 跨越 180° (如斐济/汤加: 178°E 到 -178°W)
        min_l, max_l, crosses = compute_minimal_circular_longitude_interval([178.0, -178.0])
        self.assertTrue(crosses)
        self.assertAlmostEqual(min_l, 178.0)
        self.assertAlmostEqual(max_l, 182.0)  # -178° + 360° = 182° unwrapped
        self.assertAlmostEqual(max_l - min_l, 4.0)

        # 4. 单点
        min_l, max_l, crosses = compute_minimal_circular_longitude_interval([180.0])
        self.assertFalse(crosses)

    def test_dynamic_support_window_expansion(self):
        converter = DEMDatumConverter(max_extrapolation_distance_km=100.0)
        w_deg_100, s_100, e_deg_100, n_100 = converter._compute_dynamic_support_window(
            (120.0, 30.0, 121.0, 31.0), 100.0
        )

        converter_500 = DEMDatumConverter(max_extrapolation_distance_km=500.0)
        w_deg_500, s_500, e_deg_500, n_500 = converter_500._compute_dynamic_support_window(
            (120.0, 30.0, 121.0, 31.0), 500.0
        )

        # 500 km 支持窗口在空间上必然显著大于 100 km 支持窗口
        self.assertLess(w_deg_500, w_deg_100)
        self.assertGreater(e_deg_500, e_deg_100)
        self.assertLess(s_500, s_100)
        self.assertGreater(n_500, n_100)

        # 高纬度经度膨胀测试: 在纬度 60° (cos = 0.5)，经度扩展角应约为赤道 (cos = 1.0) 的 2 倍
        _, _, e_eq, _ = converter._compute_dynamic_support_window((0.0, 0.0, 0.0, 0.0), 100.0)
        _, _, e_high, _ = converter._compute_dynamic_support_window((0.0, 60.0, 0.0, 60.0), 100.0)
        self.assertGreater(e_high, e_eq * 1.5)

    def test_end_to_end_synthetic_mdt_antimeridian_continuity(self):
        """E9 (B1): 端到端跨 180° 日界线合成 MDT NetCDF 解算与外推测试 (不 mock 空间索引与 KDTree)"""
        import xarray as xr
        with tempfile.TemporaryDirectory() as tmp_dir:
            nc_path = Path(tmp_dir) / "synthetic_mdt_antimeridian.nc"
            lons = np.arange(-180.0, 180.1, 0.5)
            lats = np.arange(-25.0, -15.0, 0.5)
            shape = (1, len(lats), len(lons))
            mdt_data = np.full(shape, 0.85, dtype=np.float64)
            # 在 180° 经线附近构造模拟岛屿缺失区 (NaN)，验证跨日界线 IDW 空间邻近检索
            lon_indices = np.where((lons >= 179.5) | (lons <= -179.5))[0]
            lat_indices = np.where((lats >= -20.5) & (lats <= -19.5))[0]
            for r in lat_indices:
                for c in lon_indices:
                    mdt_data[0, r, c] = np.nan

            ds = xr.Dataset(
                data_vars={"mdt": (("time", "latitude", "longitude"), mdt_data)},
                coords={"time": [np.datetime64("2003-01-01")], "latitude": lats, "longitude": lons}
            )
            ds.to_netcdf(str(nc_path))

            # 使用真实未经 mock 的 DEMDatumConverter
            conv = DEMDatumConverter(mdt_path=str(nc_path), max_extrapolation_distance_km=100.0)

            # 点 1 & 2: 原生海洋点 (178.5°E, -20.0°S) 与 (-178.5°W, -20.0°S)
            # 点 3 & 4: 跨越 180° 日界线两侧的近岸岛屿点 (179.9°E, -20.0°S) 与 (-179.9°W, -20.0°S)
            test_lons = np.array([178.5, -178.5, 179.9, -179.9])
            test_lats = np.array([-20.0, -20.0, -20.0, -20.0])

            mdt_vals, qc_flags = conv.evaluate_mdt_with_idw(test_lons, test_lats)

            self.assertTrue(conv._crosses_antimeridian)
            self.assertEqual(qc_flags[0], QC_MDT_NATIVE)
            self.assertEqual(qc_flags[1], QC_MDT_NATIVE)
            self.assertEqual(qc_flags[2], QC_MDT_EXTRAPOLATED)
            self.assertEqual(qc_flags[3], QC_MDT_EXTRAPOLATED)
            np.testing.assert_allclose(mdt_vals, 0.85, atol=1e-4)


@unittest.skipUnless(HAS_RASTERIO, "需要 rasterio 环境")
class TestThreadSafetyAndBatchConversion(unittest.TestCase):
    """测试 4: 批量转换多线程并发安全性与 Workers > 1 隔离性"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)
        self.in_dir = self.tmp_path / "in"
        self.out_dir_w1 = self.tmp_path / "out_w1"
        self.out_dir_w2 = self.tmp_path / "out_w2"
        self.in_dir.mkdir(parents=True, exist_ok=True)
        self.out_dir_w1.mkdir(parents=True, exist_ok=True)
        self.out_dir_w2.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def _create_synthetic_tile(self, name: str, min_lon: float, min_lat: float, nodata: float = -9999.0) -> Path:
        p = self.in_dir / name
        transform = from_bounds(min_lon, min_lat, min_lon + 0.1, min_lat + 0.1, 16, 16)
        data = np.ones((1, 16, 16), dtype=np.float32) * 8.0
        data[0, 0:2, 0:2] = nodata
        with rasterio.open(
            str(p), "w",
            driver="GTiff",
            height=16,
            width=16,
            count=1,
            dtype=rasterio.float32,
            crs="EPSG:4326",
            transform=transform,
            nodata=nodata
        ) as dst:
            dst.write(data)
            dst.update_tags(DATUM="EGM2008")
        return p

    def test_worker_isolation_and_lock_protection(self):
        batch = BatchDEMDatumConverter(max_extrapolation_distance_km=100.0)

        # 单线程模式下返回同一个 converter
        c1 = batch._get_worker_converter(100.0, workers=1)
        c2 = batch._get_worker_converter(100.0, workers=1)
        self.assertIs(c1, c2)

        # 多线程模式下各线程独立实例
        import threading
        thread_converters = []

        def worker_fn():
            c = batch._get_worker_converter(100.0, workers=2)
            thread_converters.append(c)

        t1 = threading.Thread(target=worker_fn)
        t2 = threading.Thread(target=worker_fn)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(len(thread_converters), 2)
        self.assertIsNot(thread_converters[0], thread_converters[1], "多线程环境下必须使用 thread-local 独立 Converter 实例")

    def test_workers_1_vs_workers_2_numerical_consistency(self):
        """对比 workers=1 与 workers=2 批量执行结果严格一致 (max diff <= 1e-7)"""
        # 生成跨两个地理区域的 4 个瓦片
        self._create_synthetic_tile("tile_e1.tif", 121.0, 31.0)
        self._create_synthetic_tile("tile_e2.tif", 121.5, 31.5)
        self._create_synthetic_tile("tile_s1.tif", 151.0, -33.0)
        self._create_synthetic_tile("tile_s2.tif", 151.5, -33.5)

        def mock_convert_raster(self, input_dem_path, output_msl_path, **kwargs):
            with rasterio.open(input_dem_path) as src:
                arr = src.read(1)
                profile = src.profile.copy()
            res = arr.copy()
            valid = (arr != -9999.0)
            res[valid] = arr[valid] - 0.52345
            with rasterio.open(output_msl_path, "w", **profile) as dst:
                dst.write(res, 1)
                dst.update_tags(
                    DATUM="MSL",
                    TARGET_VERTICAL_DATUM="MSL",
                    SOURCE_VERTICAL_DATUM="EGM2008",
                    SOFTWARE="CoastTideX v1.7.1"
                )
            return DEMConversionSummary(
                input_path=input_dem_path,
                output_path=output_msl_path,
                qc_output_path="",
                width=16, height=16, total_pixels=256,
                valid_dem_pixels=int(np.sum(valid)),
                native_mdt_pixels=int(np.sum(valid)),
                extrapolated_mdt_pixels=0,
                nodata_pixels=int(np.sum(~valid)),
                elapsed_seconds=0.01,
                max_extrapolation_distance_km=100.0,
                metadata={}
            )

        with patch.object(DEMDatumConverter, "convert_raster", mock_convert_raster):
            batch1 = BatchDEMDatumConverter(max_extrapolation_distance_km=100.0)
            s1 = batch1.run_batch(str(self.in_dir), str(self.out_dir_w1), workers=1, resume=False)

            batch2 = BatchDEMDatumConverter(max_extrapolation_distance_km=100.0)
            s2 = batch2.run_batch(str(self.in_dir), str(self.out_dir_w2), workers=2, resume=False)

            self.assertEqual(s1.success_count, 4)
            self.assertEqual(s2.success_count, 4)

            # 逐像元验证两个目录结果数值完全一致
            for f in self.in_dir.glob("*.tif"):
                stem = f.stem
                out1 = self.out_dir_w1 / f"{stem}_MSL.tif"
                out2 = self.out_dir_w2 / f"{stem}_MSL.tif"
                self.assertTrue(out1.exists())
                self.assertTrue(out2.exists())
                with rasterio.open(str(out1)) as d1, rasterio.open(str(out2)) as d2:
                    a1 = d1.read(1)
                    a2 = d2.read(1)
                    np.testing.assert_allclose(a1, a2, rtol=1e-7, atol=1e-7)

    def test_real_multi_worker_numerical_equivalence_unmocked(self):
        """E10 (B2): 真实无 mock convert_raster 的 multi-worker (1 vs 2 vs 4) 数值严密等价性测试"""
        import xarray as xr
        in_d = self.tmp_path / "in_real_b2"
        out_w1 = self.tmp_path / "out_real_w1"
        out_w2 = self.tmp_path / "out_real_w2"
        out_w4 = self.tmp_path / "out_real_w4"
        in_d.mkdir(parents=True, exist_ok=True)
        out_w1.mkdir(parents=True, exist_ok=True)
        out_w2.mkdir(parents=True, exist_ok=True)
        out_w4.mkdir(parents=True, exist_ok=True)

        nc_path = self.tmp_path / "synthetic_mdt_b2.nc"
        lons = np.arange(121.0, 122.5, 0.1)
        lats = np.arange(30.5, 32.0, 0.1)
        mdt_data = np.full((1, len(lats), len(lons)), 0.65, dtype=np.float64)
        mdt_data[0, :3, :3] = np.nan
        ds = xr.Dataset(
            data_vars={"mdt": (("time", "latitude", "longitude"), mdt_data)},
            coords={"time": [np.datetime64("2003-01-01")], "latitude": lats, "longitude": lons}
        )
        ds.to_netcdf(str(nc_path))

        coords = [
            (121.5, 31.0),
            (121.6, 31.0),
            (121.5, 31.1),
            (121.6, 31.1),
        ]
        for idx, (lon, lat) in enumerate(coords):
            tile_path = in_d / f"tile_real_{idx}.tif"
            tf = from_bounds(lon, lat, lon + 0.05, lat + 0.05, 16, 16)
            data = np.ones((1, 16, 16), dtype=np.float32) * (5.0 + idx)
            data[0, 0:2, 0:2] = -9999.0
            with rasterio.open(
                str(tile_path), "w", driver="GTiff", height=16, width=16, count=1,
                dtype=rasterio.float32, crs="EPSG:4326", transform=tf, nodata=-9999.0
            ) as dst:
                dst.write(data)
                dst.update_tags(DATUM="EGM2008")

        batch = BatchDEMDatumConverter(mdt_path=str(nc_path), max_extrapolation_distance_km=100.0)
        s1 = batch.run_batch(str(in_d), str(out_w1), workers=1, resume=False, write_qc=True)
        s2 = batch.run_batch(str(in_d), str(out_w2), workers=2, resume=False, write_qc=True)
        s4 = batch.run_batch(str(in_d), str(out_w4), workers=4, resume=False, write_qc=True)

        self.assertEqual(s1.success_count, 4)
        self.assertEqual(s2.success_count, 4)
        self.assertEqual(s4.success_count, 4)

        for f in in_d.glob("*.tif"):
            f_msl = f"{f.stem}_MSL.tif"
            f_qc = f"{f.stem}_MSL_conversion_qc.tif"
            with rasterio.open(str(out_w1 / f_msl)) as d1, \
                 rasterio.open(str(out_w2 / f_msl)) as d2, \
                 rasterio.open(str(out_w4 / f_msl)) as d4:
                a1 = d1.read(1)
                a2 = d2.read(1)
                a4 = d4.read(1)
                np.testing.assert_allclose(a1, a2, rtol=1e-7, atol=1e-7)
                np.testing.assert_allclose(a1, a4, rtol=1e-7, atol=1e-7)
            with rasterio.open(str(out_w1 / f_qc)) as q1, \
                 rasterio.open(str(out_w2 / f_qc)) as q2, \
                 rasterio.open(str(out_w4 / f_qc)) as q4:
                np.testing.assert_array_equal(q1.read(1), q2.read(1))
                np.testing.assert_array_equal(q1.read(1), q4.read(1))


@unittest.skipUnless(HAS_RASTERIO, "需要 rasterio 环境")
class TestQCOptimizationAndResumeIntegrity(unittest.TestCase):
    """测试 5: QC 质量控制输出控制与断点恢复多因子完整性校验"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)
        self.in_dir = self.tmp_path / "in"
        self.out_dir = self.tmp_path / "out"
        self.in_dir.mkdir(parents=True, exist_ok=True)
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def _create_synthetic_dem(self, name: str, target_dir: Optional[Path] = None) -> Path:
        p = (target_dir or self.in_dir) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        transform = from_bounds(121.0, 31.0, 121.1, 31.1, 10, 10)
        data = np.ones((1, 10, 10), dtype=np.float32) * 5.0
        with rasterio.open(
            str(p), "w",
            driver="GTiff",
            height=10,
            width=10,
            count=1,
            dtype=rasterio.float32,
            crs="EPSG:4326",
            transform=transform,
            nodata=-9999.0
        ) as dst:
            dst.write(data)
            dst.update_tags(DATUM="EGM2008")
        return p

    def test_qc_toggle_write_qc_false_vs_true(self):
        """验证 write_qc 开关对磁盘输出与清单字段的影响"""
        self._create_synthetic_dem("tile_test_input.tif")

        def fake_convert(self, input_dem_path, output_msl_path, output_qc_path=None, write_qc=False, **kwargs):
            out_p = Path(output_msl_path)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            transform = from_bounds(121.0, 31.0, 121.1, 31.1, 10, 10)
            data = np.ones((1, 10, 10), dtype=np.float32) * 4.5
            with rasterio.open(
                str(out_p), "w", driver="GTiff", height=10, width=10, count=1,
                dtype=rasterio.float32, crs="EPSG:4326", transform=transform, nodata=-9999.0
            ) as dst:
                dst.write(data)
                dst.update_tags(
                    DATUM="MSL",
                    TARGET_VERTICAL_DATUM="MSL",
                    SOURCE_VERTICAL_DATUM="EGM2008",
                    SOFTWARE="CoastTideX v1.7.1"
                )

            qc_written = ""
            if write_qc:
                if not output_qc_path:
                    base, ext = os.path.splitext(output_msl_path)
                    output_qc_path = f"{base}_qc{ext}"
                qc_p = Path(output_qc_path)
                with rasterio.open(
                    str(qc_p), "w", driver="GTiff", height=10, width=10, count=1,
                    dtype=rasterio.uint8, crs="EPSG:4326", transform=transform, nodata=255
                ) as dst_qc:
                    dst_qc.write(np.zeros((1, 10, 10), dtype=np.uint8))
                qc_written = str(qc_p)

            return DEMConversionSummary(
                input_path=input_dem_path,
                output_path=output_msl_path,
                qc_output_path=qc_written,
                width=10, height=10, total_pixels=100,
                valid_dem_pixels=100, native_mdt_pixels=100,
                extrapolated_mdt_pixels=0, nodata_pixels=0,
                elapsed_seconds=0.01,
                max_extrapolation_distance_km=100.0,
                metadata={}
            )

        with patch.object(DEMDatumConverter, "convert_raster", fake_convert):
            # 1. 默认 write_qc=False: 不生成 QC 文件
            b_no_qc = BatchDEMDatumConverter()
            s_no = b_no_qc.run_batch(str(self.in_dir), str(self.out_dir / "no_qc"), write_qc=False)
            self.assertEqual(s_no.success_count, 1)
            msl_files_no = list((self.out_dir / "no_qc").glob("*_MSL.tif"))
            qc_files_no = list((self.out_dir / "no_qc").glob("*_qc.tif"))
            self.assertEqual(len(msl_files_no), 1)
            self.assertEqual(len(qc_files_no), 0, "write_qc=False 时绝对不应生成 QC GeoTIFF")

            # 2. 开启 write_qc=True: 生成 QC 文件
            b_qc = BatchDEMDatumConverter()
            s_qc = b_qc.run_batch(str(self.in_dir), str(self.out_dir / "with_qc"), write_qc=True)
            self.assertEqual(s_qc.success_count, 1)
            msl_files_qc = list((self.out_dir / "with_qc").glob("*_MSL.tif"))
            qc_files_qc = list((self.out_dir / "with_qc").glob("*_qc.tif"))
            self.assertEqual(len(msl_files_qc), 1)
            self.assertEqual(len(qc_files_qc), 1, "write_qc=True 时必须成功生成 QC GeoTIFF")

    def test_resume_signature_mismatch_forces_recompute(self):
        """参数变动 (如 max_dist_km: 100 -> 50 km) 导致特征签名不符时，断点恢复必须重算"""
        t = self._create_synthetic_dem("tile_sig.tif")
        call_count = 0

        def fake_convert(self, input_dem_path, output_msl_path, max_extrapolation_distance_km=100.0, **kwargs):
            nonlocal call_count
            call_count += 1
            out_p = Path(output_msl_path)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            transform = from_bounds(121.0, 31.0, 121.1, 31.1, 10, 10)
            data = np.ones((1, 10, 10), dtype=np.float32) * 4.5
            with rasterio.open(
                str(out_p), "w", driver="GTiff", height=10, width=10, count=1,
                dtype=rasterio.float32, crs="EPSG:4326", transform=transform, nodata=-9999.0
            ) as dst:
                dst.write(data)
                dst.update_tags(
                    DATUM="MSL",
                    TARGET_VERTICAL_DATUM="MSL",
                    SOURCE_VERTICAL_DATUM="EGM2008",
                    MAX_EXTRAPOLATION_DISTANCE_KM=str(max_extrapolation_distance_km)
                )
            return DEMConversionSummary(
                input_path=input_dem_path, output_path=output_msl_path, qc_output_path="",
                width=10, height=10, total_pixels=100, valid_dem_pixels=100, native_mdt_pixels=100,
                extrapolated_mdt_pixels=0, nodata_pixels=0, elapsed_seconds=0.01,
                max_extrapolation_distance_km=max_extrapolation_distance_km, metadata={}
            )

        with patch.object(DEMDatumConverter, "convert_raster", fake_convert):
            # 第一轮：100 km 运行
            b1 = BatchDEMDatumConverter(max_extrapolation_distance_km=100.0)
            s1 = b1.run_batch(str(self.in_dir), str(self.out_dir), max_dist_km=100.0, resume=True)
            self.assertEqual(s1.success_count, 1)
            self.assertEqual(call_count, 1)

            # 第二轮：参数完全相同 -> 断点跳过
            s2 = b1.run_batch(str(self.in_dir), str(self.out_dir), max_dist_km=100.0, resume=True)
            self.assertEqual(s2.skipped_resume_count, 1)
            self.assertEqual(call_count, 1)

            # 第三轮：max_dist_km 变更为 50.0 km -> 签名不匹配，必须触发重算
            b3 = BatchDEMDatumConverter(max_extrapolation_distance_km=50.0)
            s3 = b3.run_batch(str(self.in_dir), str(self.out_dir), max_dist_km=50.0, resume=True)
            self.assertEqual(s3.success_count, 1)
            self.assertEqual(call_count, 2, "参数发生变化时，断点恢复必须触发重算，不能误跳过！")

    def test_corrupted_output_file_triggers_recompute(self):
        """若输出文件被损坏或截断为非 GeoTIFF，断点恢复安全校验必须阻断并重算"""
        t = self._create_synthetic_dem("tile_corrupt.tif")
        call_count = 0

        def fake_convert(self, input_dem_path, output_msl_path, **kwargs):
            nonlocal call_count
            call_count += 1
            out_p = Path(output_msl_path)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            transform = from_bounds(121.0, 31.0, 121.1, 31.1, 10, 10)
            data = np.ones((1, 10, 10), dtype=np.float32) * 4.5
            with rasterio.open(
                str(out_p), "w", driver="GTiff", height=10, width=10, count=1,
                dtype=rasterio.float32, crs="EPSG:4326", transform=transform, nodata=-9999.0
            ) as dst:
                dst.write(data)
                dst.update_tags(
                    DATUM="MSL",
                    TARGET_VERTICAL_DATUM="MSL",
                    SOURCE_VERTICAL_DATUM="EGM2008",
                    MAX_EXTRAPOLATION_DISTANCE_KM="100.0"
                )
            return DEMConversionSummary(
                input_path=input_dem_path, output_path=output_msl_path, qc_output_path="",
                width=10, height=10, total_pixels=100, valid_dem_pixels=100, native_mdt_pixels=100,
                extrapolated_mdt_pixels=0, nodata_pixels=0, elapsed_seconds=0.01,
                max_extrapolation_distance_km=100.0, metadata={}
            )

        with patch.object(DEMDatumConverter, "convert_raster", fake_convert):
            b = BatchDEMDatumConverter()
            s1 = b.run_batch(str(self.in_dir), str(self.out_dir), resume=True)
            self.assertEqual(s1.success_count, 1)
            self.assertEqual(call_count, 1)

            # 人为损坏输出文件 (截断为 10 字节垃圾数据)
            out_p = self.out_dir / "tile_corrupt_MSL.tif"
            out_p.write_bytes(b"corrupt!!!")

            # 再次运行断点恢复 -> 必须检测到文件损坏并自动重新生成
            s2 = b.run_batch(str(self.in_dir), str(self.out_dir), resume=True)
            self.assertEqual(s2.success_count, 1)
            self.assertEqual(call_count, 2, "损坏的输出文件必须触发重算以保证数据完备性")

    def test_resume_write_qc_false_then_true_recomputes(self):
        """E1: 第一次 write_qc=False, 第二次 write_qc=True 时必须触发重算生成 QC，不能跳过"""
        in_d = self.tmp_path / "in_e1"
        out_d = self.tmp_path / "out_e1"
        in_d.mkdir(parents=True, exist_ok=True)
        out_d.mkdir(parents=True, exist_ok=True)
        t = self._create_synthetic_dem("tile_resume_qctest.tif", target_dir=in_d)
        call_count = 0

        def fake_convert(self, input_dem_path, output_msl_path, output_qc_path=None, write_qc=False, **kwargs):
            nonlocal call_count
            call_count += 1
            out_p = Path(output_msl_path)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            transform = from_bounds(121.0, 31.0, 121.1, 31.1, 10, 10)
            data = np.ones((1, 10, 10), dtype=np.float32) * 4.5
            with rasterio.open(
                str(out_p), "w", driver="GTiff", height=10, width=10, count=1,
                dtype=rasterio.float32, crs="EPSG:4326", transform=transform, nodata=-9999.0
            ) as dst:
                dst.write(data)
                dst.update_tags(
                    DATUM="MSL", TARGET_VERTICAL_DATUM="MSL",
                    SOURCE_VERTICAL_DATUM="EGM2008", MAX_EXTRAPOLATION_DISTANCE_KM="100.0"
                )

            qc_written = ""
            if write_qc:
                if not output_qc_path:
                    base, ext = os.path.splitext(output_msl_path)
                    output_qc_path = f"{base}_qc{ext}"
                qc_p = Path(output_qc_path)
                with rasterio.open(
                    str(qc_p), "w", driver="GTiff", height=10, width=10, count=1,
                    dtype=rasterio.uint8, crs="EPSG:4326", transform=transform, nodata=255
                ) as dst_qc:
                    dst_qc.write(np.zeros((1, 10, 10), dtype=np.uint8))
                qc_written = str(qc_p)

            return DEMConversionSummary(
                input_path=input_dem_path, output_path=output_msl_path, qc_output_path=qc_written,
                width=10, height=10, total_pixels=100, valid_dem_pixels=100, native_mdt_pixels=100,
                extrapolated_mdt_pixels=0, nodata_pixels=0, elapsed_seconds=0.01,
                max_extrapolation_distance_km=100.0, metadata={}
            )

        with patch.object(DEMDatumConverter, "convert_raster", fake_convert):
            b = BatchDEMDatumConverter()
            # 第 1 次运行: write_qc=False
            s1 = b.run_batch(str(in_d), str(out_d), resume=True, write_qc=False)
            self.assertEqual(s1.success_count, 1)
            self.assertEqual(call_count, 1)
            qc_p = out_d / "tile_resume_qctest_MSL_qc.tif"
            self.assertFalse(qc_p.exists())

            # 第 2 次运行: resume=True, write_qc=True -> 必须重算生成 QC，绝不能错误跳过
            s2 = b.run_batch(str(in_d), str(out_d), resume=True, write_qc=True)
            self.assertEqual(s2.success_count, 1, "当请求 write_qc=True 但缺少 QC 产物时必须重算")
            self.assertEqual(call_count, 2)
            self.assertTrue(qc_p.exists())

    def test_resume_write_qc_true_then_true_skips(self):
        """E2: 当 MSL 与 QC 均完整存在且健康时，write_qc=True 的断点恢复必须成功跳过"""
        in_d = self.tmp_path / "in_e2"
        out_d = self.tmp_path / "out_e2"
        in_d.mkdir(parents=True, exist_ok=True)
        out_d.mkdir(parents=True, exist_ok=True)
        t = self._create_synthetic_dem("tile_skip_qctest.tif", target_dir=in_d)
        call_count = 0

        def fake_convert(self, input_dem_path, output_msl_path, output_qc_path=None, write_qc=False, **kwargs):
            nonlocal call_count
            call_count += 1
            out_p = Path(output_msl_path)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            transform = from_bounds(121.0, 31.0, 121.1, 31.1, 10, 10)
            data = np.ones((1, 10, 10), dtype=np.float32) * 4.5
            with rasterio.open(
                str(out_p), "w", driver="GTiff", height=10, width=10, count=1,
                dtype=rasterio.float32, crs="EPSG:4326", transform=transform, nodata=-9999.0
            ) as dst:
                dst.write(data)
                dst.update_tags(
                    DATUM="MSL", TARGET_VERTICAL_DATUM="MSL",
                    SOURCE_VERTICAL_DATUM="EGM2008", MAX_EXTRAPOLATION_DISTANCE_KM="100.0"
                )

            qc_written = ""
            if write_qc:
                if not output_qc_path:
                    base, ext = os.path.splitext(output_msl_path)
                    output_qc_path = f"{base}_qc{ext}"
                qc_p = Path(output_qc_path)
                with rasterio.open(
                    str(qc_p), "w", driver="GTiff", height=10, width=10, count=1,
                    dtype=rasterio.uint8, crs="EPSG:4326", transform=transform, nodata=255
                ) as dst_qc:
                    dst_qc.write(np.zeros((1, 10, 10), dtype=np.uint8))
                qc_written = str(qc_p)

            return DEMConversionSummary(
                input_path=input_dem_path, output_path=output_msl_path, qc_output_path=qc_written,
                width=10, height=10, total_pixels=100, valid_dem_pixels=100, native_mdt_pixels=100,
                extrapolated_mdt_pixels=0, nodata_pixels=0, elapsed_seconds=0.01,
                max_extrapolation_distance_km=100.0, metadata={}
            )

        with patch.object(DEMDatumConverter, "convert_raster", fake_convert):
            b = BatchDEMDatumConverter()
            # 第 1 轮生成 MSL + QC
            s1 = b.run_batch(str(in_d), str(out_d), resume=True, write_qc=True)
            self.assertEqual(s1.success_count, 1)
            self.assertEqual(call_count, 1)

            # 第 2 轮 resume=True, write_qc=True -> 均完整，必须跳过
            s2 = b.run_batch(str(in_d), str(out_d), resume=True, write_qc=True)
            self.assertEqual(s2.skipped_resume_count, 1)
            self.assertEqual(s2.success_count, 0)
            self.assertEqual(call_count, 1)

            # 第 3 轮 resume=True, write_qc=False -> 历史存有 QC，但当前不需要，允许安全跳过且不删历史 QC
            s3 = b.run_batch(str(in_d), str(out_d), resume=True, write_qc=False)
            self.assertEqual(s3.skipped_resume_count, 1)
            self.assertEqual(call_count, 1)

    def test_resume_missing_required_qc_recomputes(self):
        """E3: 历史任务成功生成 QC，但后续 QC 文件被人工删除时，write_qc=True 必须触发重算"""
        in_d = self.tmp_path / "in_e3"
        out_d = self.tmp_path / "out_e3"
        in_d.mkdir(parents=True, exist_ok=True)
        out_d.mkdir(parents=True, exist_ok=True)
        t = self._create_synthetic_dem("tile_del_qctest.tif", target_dir=in_d)
        call_count = 0

        def fake_convert(self, input_dem_path, output_msl_path, output_qc_path=None, write_qc=False, **kwargs):
            nonlocal call_count
            call_count += 1
            out_p = Path(output_msl_path)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            transform = from_bounds(121.0, 31.0, 121.1, 31.1, 10, 10)
            data = np.ones((1, 10, 10), dtype=np.float32) * 4.5
            with rasterio.open(
                str(out_p), "w", driver="GTiff", height=10, width=10, count=1,
                dtype=rasterio.float32, crs="EPSG:4326", transform=transform, nodata=-9999.0
            ) as dst:
                dst.write(data)
                dst.update_tags(
                    DATUM="MSL", TARGET_VERTICAL_DATUM="MSL",
                    SOURCE_VERTICAL_DATUM="EGM2008", MAX_EXTRAPOLATION_DISTANCE_KM="100.0"
                )

            qc_written = ""
            if write_qc:
                if not output_qc_path:
                    base, ext = os.path.splitext(output_msl_path)
                    output_qc_path = f"{base}_qc{ext}"
                qc_p = Path(output_qc_path)
                with rasterio.open(
                    str(qc_p), "w", driver="GTiff", height=10, width=10, count=1,
                    dtype=rasterio.uint8, crs="EPSG:4326", transform=transform, nodata=255
                ) as dst_qc:
                    dst_qc.write(np.zeros((1, 10, 10), dtype=np.uint8))
                qc_written = str(qc_p)

            return DEMConversionSummary(
                input_path=input_dem_path, output_path=output_msl_path, qc_output_path=qc_written,
                width=10, height=10, total_pixels=100, valid_dem_pixels=100, native_mdt_pixels=100,
                extrapolated_mdt_pixels=0, nodata_pixels=0, elapsed_seconds=0.01,
                max_extrapolation_distance_km=100.0, metadata={}
            )

        with patch.object(DEMDatumConverter, "convert_raster", fake_convert):
            b = BatchDEMDatumConverter()
            s1 = b.run_batch(str(in_d), str(out_d), resume=True, write_qc=True)
            self.assertEqual(s1.success_count, 1)
            self.assertEqual(call_count, 1)

            qc_p = out_d / "tile_del_qctest_MSL_qc.tif"
            self.assertTrue(qc_p.exists())
            # 人工删除 QC 文件
            qc_p.unlink()
            self.assertFalse(qc_p.exists())

            # 再次运行断点恢复，要求 write_qc=True -> 必须重算并补全 QC
            s2 = b.run_batch(str(in_d), str(out_d), resume=True, write_qc=True)
            self.assertEqual(s2.success_count, 1, "QC 文件被误删时必须触发重算补全")
            self.assertEqual(call_count, 2)
            self.assertTrue(qc_p.exists())

    def test_resume_corrupt_required_qc_recomputes(self):
        """E4: QC 文件损坏时，write_qc=True 必须触发重算"""
        in_d = self.tmp_path / "in_e4"
        out_d = self.tmp_path / "out_e4"
        in_d.mkdir(parents=True, exist_ok=True)
        out_d.mkdir(parents=True, exist_ok=True)
        t = self._create_synthetic_dem("tile_corrupt_qctest.tif", target_dir=in_d)
        call_count = 0

        def fake_convert(self, input_dem_path, output_msl_path, output_qc_path=None, write_qc=False, **kwargs):
            nonlocal call_count
            call_count += 1
            out_p = Path(output_msl_path)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            transform = from_bounds(121.0, 31.0, 121.1, 31.1, 10, 10)
            data = np.ones((1, 10, 10), dtype=np.float32) * 4.5
            with rasterio.open(
                str(out_p), "w", driver="GTiff", height=10, width=10, count=1,
                dtype=rasterio.float32, crs="EPSG:4326", transform=transform, nodata=-9999.0
            ) as dst:
                dst.write(data)
                dst.update_tags(
                    DATUM="MSL", TARGET_VERTICAL_DATUM="MSL",
                    SOURCE_VERTICAL_DATUM="EGM2008", MAX_EXTRAPOLATION_DISTANCE_KM="100.0"
                )

            qc_written = ""
            if write_qc:
                if not output_qc_path:
                    base, ext = os.path.splitext(output_msl_path)
                    output_qc_path = f"{base}_qc{ext}"
                qc_p = Path(output_qc_path)
                with rasterio.open(
                    str(qc_p), "w", driver="GTiff", height=10, width=10, count=1,
                    dtype=rasterio.uint8, crs="EPSG:4326", transform=transform, nodata=255
                ) as dst_qc:
                    dst_qc.write(np.zeros((1, 10, 10), dtype=np.uint8))
                qc_written = str(qc_p)

            return DEMConversionSummary(
                input_path=input_dem_path, output_path=output_msl_path, qc_output_path=qc_written,
                width=10, height=10, total_pixels=100, valid_dem_pixels=100, native_mdt_pixels=100,
                extrapolated_mdt_pixels=0, nodata_pixels=0, elapsed_seconds=0.01,
                max_extrapolation_distance_km=100.0, metadata={}
            )

        with patch.object(DEMDatumConverter, "convert_raster", fake_convert):
            b = BatchDEMDatumConverter()
            s1 = b.run_batch(str(in_d), str(out_d), resume=True, write_qc=True)
            self.assertEqual(s1.success_count, 1)
            self.assertEqual(call_count, 1)

            qc_p = out_d / "tile_corrupt_qctest_MSL_qc.tif"
            self.assertTrue(qc_p.exists())
            # 人工损坏 QC 文件
            qc_p.write_bytes(b"bad_qc_data")

            # 再次运行断点恢复，要求 write_qc=True -> 必须重算
            s2 = b.run_batch(str(in_d), str(out_d), resume=True, write_qc=True)
            self.assertEqual(s2.success_count, 1, "QC 文件损坏时必须触发重算")
            self.assertEqual(call_count, 2)

    def test_batch_rejects_zero_workers(self):
        """E5: BatchDEMDatumConverter.run_batch 必须拒绝 workers=0"""
        b = BatchDEMDatumConverter()
        with self.assertRaises(ValueError) as ctx:
            b.run_batch(str(self.in_dir), str(self.out_dir), workers=0)
        self.assertIn("workers 必须 >= 1", str(ctx.exception))

    def test_batch_rejects_negative_workers(self):
        """E6: BatchDEMDatumConverter.run_batch 必须拒绝负数 workers"""
        b = BatchDEMDatumConverter()
        with self.assertRaises(ValueError) as ctx:
            b.run_batch(str(self.in_dir), str(self.out_dir), workers=-2)
        self.assertIn("workers 必须 >= 1", str(ctx.exception))

    def test_cli_rejects_invalid_workers(self):
        """E7: CLI 命令 convert-dem-batch 遇到 --workers 0 必须以状态码 2 退出"""
        import subprocess
        cmd = [
            sys.executable,
            "cli.py",
            "convert-dem-batch",
            "--input-dir", str(self.in_dir),
            "--output-dir", str(self.out_dir),
            "--workers", "0"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent))
        self.assertEqual(res.returncode, 2)
        self.assertIn("workers 必须 >= 1", res.stderr)

    def test_gui_stale_100km_text_removed(self):
        """E8: 校验 gui/main_window.py 中杜绝硬编码 '上限 100 km' 与 '超出100km'"""
        gui_file = Path(__file__).resolve().parent.parent / "gui" / "main_window.py"
        content = gui_file.read_text(encoding="utf-8")
        self.assertNotIn("上限 100 km", content)
        self.assertNotIn("超出100km", content)


@unittest.skipUnless(HAS_RASTERIO, "需要 rasterio 环境")
class TestMetadataConsistencyAndCitation(unittest.TestCase):
    """测试 6: 输出栅格元数据、软件版本与科学溯源标记一致性"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_metadata_tags_written_to_geotiff(self):
        dem_p = self.tmp_path / "meta_test.tif"
        out_p = self.tmp_path / "meta_test_MSL.tif"
        qc_p = self.tmp_path / "meta_test_qc.tif"

        transform = from_bounds(121.0, 31.0, 121.05, 31.05, 8, 8)
        data = np.ones((1, 8, 8), dtype=np.float32) * 6.0
        with rasterio.open(
            str(dem_p), "w", driver="GTiff", height=8, width=8, count=1,
            dtype=rasterio.float32, crs="EPSG:4326", transform=transform, nodata=-9999.0
        ) as dst:
            dst.write(data)
            dst.update_tags(DATUM="EGM2008")

        converter = DEMDatumConverter(max_extrapolation_distance_km=80.0)

        def mock_convert_points(lons, lats, z_egm):
            n = len(z_egm)
            return (
                z_egm - 0.5,
                np.full(n, 0.5, dtype=np.float32),
                np.zeros(n, dtype=np.float32),
                np.zeros(n, dtype=np.uint8)
            )

        with patch.object(converter, "convert_points", side_effect=mock_convert_points), \
             patch.object(converter, "_ensure_mdt_spatial_index"):
            summary = converter.convert_raster(
                input_dem_path=str(dem_p),
                output_msl_path=str(out_p),
                output_qc_path=str(qc_p),
                max_extrapolation_distance_km=80.0,
                write_qc=True
            )

            self.assertTrue(out_p.exists())
            self.assertTrue(qc_p.exists())

            with rasterio.open(str(out_p)) as ds_msl:
                tags = ds_msl.tags()
                self.assertEqual(tags.get("DATUM"), "MSL")
                self.assertEqual(tags.get("TARGET_VERTICAL_DATUM"), "MSL")
                self.assertEqual(tags.get("SOURCE_VERTICAL_DATUM"), "EGM2008")
                self.assertIn("v1.7.1", tags.get("SOFTWARE", ""))
                self.assertIn("Adapted from Seeger & Minderhoud", tags.get("METHOD_RELATION", ""))
                self.assertEqual(tags.get("MAX_EXTRAPOLATION_DISTANCE_KM"), "80.0")

            with rasterio.open(str(qc_p)) as ds_qc:
                qc_tags = ds_qc.tags()
                self.assertIn("0=native_mdt", qc_tags.get("QC_ENCODING", ""))
                self.assertIn("1=idw_extrapolated", qc_tags.get("QC_ENCODING", ""))
                self.assertIn("2=nodata", qc_tags.get("QC_ENCODING", ""))
                self.assertNotIn("100km", qc_tags.get("QC_ENCODING", ""))


if __name__ == "__main__":
    unittest.main()
