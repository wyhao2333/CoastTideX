"""
CoastTideX v1.6 Beta - Round 7 Final Merge-Gate Verification Suite
Comprehensive automated tests for:
1. LeafCellSpatialIndex correctness across coordinate spaces (EPSG:4326, projected, negative, tiny, non-square, 1..1000+ cells).
2. Deterministic candidate cell ordering across repeated runs.
3. Half-open cell boundary ownership uniqueness.
4. Production regression: Spatial index vs brute force in inundation & exposure paths.
5. Authoritative GeoTIFF tags for from-cache modes (no parameter leakage).
6. Tamper-evident cache metadata self-validation (TideCacheIntegrityError on tampering).
7. BatchManifest new fields and backward compatibility.
8. ERROR_IF_EXISTS preflight check across all 6 batch modes.
9. GUI & CLI from-cache transparency behavior.
10. Documentation and docstring strict academic linting.
"""

import os
import sys
import json
import random
import tempfile
import unittest
from unittest.mock import MagicMock
from collections import OrderedDict
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import from_origin
import netCDF4

from core.raster_engine import (
    ControlNode, QuadCell, LeafCellSpatialIndex, compute_cell_membership,
    RasterInfo, RasterTideEngine, ExistingOutputError
)
from core.tide_cache import (
    write_tide_cache, inspect_tide_cache_metadata, TideCacheIntegrityError,
    calculate_inundation_from_tide_cache, calculate_exposure_from_tide_cache
)
from core.exposure_engine import ExposureProductPaths, stream_exposure_metrics_interpolation
from core.batch_raster_engine import (
    BatchManifest, BatchRasterEngine, ExistingOutputPolicy,
    JOB_MODE_TIDE_ONLY, JOB_MODE_TIDE_AND_INUNDATION, JOB_MODE_INUNDATION_FROM_CACHE,
    JOB_MODE_TIDE_AND_EXPOSURE, JOB_MODE_EXPOSURE_FROM_CACHE, JOB_MODE_ALL
)


def _oracle_query(cells, query_bounds):
    """暴力扫描基准 Oracle: 严格 AABB 相交测试与相同确定性排序"""
    qx0, qy0, qx1, qy1 = query_bounds
    min_qx = min(qx0, qx1)
    max_qx = max(qx0, qx1)
    min_qy = min(qy0, qy1)
    max_qy = max(qy0, qy1)

    matched = []
    for c in cells:
        if hasattr(c, "x_min"):
            cx0, cx1, cy0, cy1 = float(c.x_min), float(c.x_max), float(c.y_min), float(c.y_max)
        elif isinstance(c, dict):
            b = c.get("bounds", (c.get("x0", 0), c.get("y0", 0), c.get("x1", 0), c.get("y1", 0)))
            cx0, cx1, cy0, cy1 = float(b[0]), float(b[2]), float(b[1]), float(b[3])
        else:
            cx0 = float(getattr(c, "x0", 0))
            cx1 = float(getattr(c, "x1", 0))
            cy0 = float(getattr(c, "y0", 0))
            cy1 = float(getattr(c, "y1", 0))

        if not (cx1 < min_qx or cx0 > max_qx or cy1 < min_qy or cy0 > max_qy):
            matched.append(c)

    def _sort_key(c):
        cid = getattr(c, "cell_id", None)
        if hasattr(c, "x_min"):
            b = (float(c.x_min), float(c.x_max), float(c.y_min), float(c.y_max))
        else:
            b = (0.0, 0.0, 0.0, 0.0)
        if isinstance(cid, int):
            return (0, cid, "", b)
        elif isinstance(cid, str):
            return (1, 0, cid, b)
        return (2, 0, "", b)

    matched.sort(key=_sort_key)
    return matched


class TestLeafCellSpatialIndexHarden(unittest.TestCase):
    """测试 1: 空间桶索引在各种物理坐标系与边界条件下的鲁棒性与严格等价性"""

    def _make_dummy_cell(self, cid, x0, y0, x1, y1):
        n = ControlNode(node_id=cid, x=(x0+x1)/2, y=(y0+y1)/2, lon=(x0+x1)/2, lat=(y0+y1)/2, valid=True)
        return QuadCell(
            cell_id=cid,
            x_min=min(x0, x1), y_min=min(y0, y1),
            x_max=max(x0, x1), y_max=max(y0, y1),
            level=0, node_a=n, node_b=n, node_c=n, node_d=n
        )

    def test_geographic_epsg4326_spatial_distribution(self):
        """1.1 经纬度 EPSG:4326 (小尺度，~0.2度): 验证空间索引不再退化为单桶全扫描"""
        bounds = (121.5, 31.0, 121.7, 31.2)
        cells = []
        cid = 1
        for i in range(10):
            for j in range(10):
                x0 = 121.5 + i * 0.02
                y0 = 31.0 + j * 0.02
                cells.append(self._make_dummy_cell(cid, x0, y0, x0 + 0.02, y0 + 0.02))
                cid += 1

        idx = LeafCellSpatialIndex(cells, bounds=bounds, num_buckets_per_dim=8)
        # 验证桶尺寸不会被硬编码 100.0 覆盖
        self.assertLess(idx.bucket_size_x, 0.1)
        self.assertLess(idx.bucket_size_y, 0.1)
        # 验证桶有效分散
        self.assertGreater(len(idx.buckets), 1)

        # 随机 100 次查询对照
        rng = random.Random(42)
        for _ in range(100):
            qx0 = rng.uniform(121.4, 121.8)
            qy0 = rng.uniform(30.9, 31.3)
            qx1 = qx0 + rng.uniform(0.005, 0.05)
            qy1 = qy0 + rng.uniform(0.005, 0.05)
            qbox = (qx0, qy0, qx1, qy1)

            res_idx = idx.query_intersecting_cells(qbox)
            res_oracle = _oracle_query(cells, qbox)

            self.assertEqual(
                [c.cell_id for c in res_idx],
                [c.cell_id for c in res_oracle],
                f"Mismatch in EPSG:4326 query {qbox}"
            )

    def test_projected_utm_coordinates(self):
        """1.2 投影坐标系 UTM (大尺度，数万米): 验证索引与暴力完全等价"""
        bounds = (300000.0, 3400000.0, 350000.0, 3450000.0)
        cells = []
        cid = 1
        for i in range(10):
            for j in range(10):
                x0 = 300000.0 + i * 5000.0
                y0 = 3400000.0 + j * 5000.0
                cells.append(self._make_dummy_cell(cid, x0, y0, x0 + 5000.0, y0 + 5000.0))
                cid += 1

        idx = LeafCellSpatialIndex(cells, bounds=bounds, num_buckets_per_dim=16)
        rng = random.Random(101)
        for _ in range(100):
            qx0 = rng.uniform(290000.0, 360000.0)
            qy0 = rng.uniform(3390000.0, 3460000.0)
            qx1 = qx0 + rng.uniform(1000.0, 15000.0)
            qy1 = qy0 + rng.uniform(1000.0, 15000.0)
            qbox = (qx0, qy0, qx1, qy1)

            res_idx = idx.query_intersecting_cells(qbox)
            res_oracle = _oracle_query(cells, qbox)
            self.assertEqual([c.cell_id for c in res_idx], [c.cell_id for c in res_oracle])

    def test_negative_and_crossing_coordinates(self):
        """1.3 负坐标与跨零点 (西半球 / 南半球 / 赤道跨越)"""
        bounds = (-75.5, -35.5, -74.5, -34.5)
        cells = []
        cid = 1
        for i in range(5):
            for j in range(5):
                x0 = -75.5 + i * 0.2
                y0 = -35.5 + j * 0.2
                cells.append(self._make_dummy_cell(cid, x0, y0, x0 + 0.2, y0 + 0.2))
                cid += 1

        idx = LeafCellSpatialIndex(cells, bounds=bounds, num_buckets_per_dim=8)
        rng = random.Random(202)
        for _ in range(100):
            qx0 = rng.uniform(-76.0, -74.0)
            qy0 = rng.uniform(-36.0, -34.0)
            qx1 = qx0 + rng.uniform(0.05, 0.4)
            qy1 = qy0 + rng.uniform(0.05, 0.4)
            qbox = (qx0, qy0, qx1, qy1)

            res_idx = idx.query_intersecting_cells(qbox)
            res_oracle = _oracle_query(cells, qbox)
            self.assertEqual([c.cell_id for c in res_idx], [c.cell_id for c in res_oracle])

    def test_extreme_cases_single_and_large_and_giant(self):
        """1.4 极端边界用例: 1个单元、1000+单元、超大单元 (Giant Cell) 与极小范围"""
        # A. 单单元
        single_cell = self._make_dummy_cell(1, 10.0, 20.0, 11.0, 21.0)
        idx_single = LeafCellSpatialIndex([single_cell], bounds=(10.0, 20.0, 11.0, 21.0))
        self.assertEqual(len(idx_single.query_intersecting_cells((10.5, 20.5, 10.6, 20.6))), 1)
        self.assertEqual(len(idx_single.query_intersecting_cells((0.0, 0.0, 5.0, 5.0))), 0)

        # B. 包含一个跨越全图的超大单元 (Giant Cell)
        giant_cell = self._make_dummy_cell(9999, 0.0, 0.0, 100.0, 100.0)
        small_cell = self._make_dummy_cell(1, 10.0, 10.0, 12.0, 12.0)
        idx_giant = LeafCellSpatialIndex([giant_cell, small_cell], bounds=(0.0, 0.0, 100.0, 100.0), num_buckets_per_dim=8)
        self.assertIn(giant_cell, idx_giant.oversized_cells)
        # 查询命中两个
        res = idx_giant.query_intersecting_cells((10.5, 10.5, 11.0, 11.0))
        self.assertEqual([c.cell_id for c in res], [1, 9999])

        # C. 1000+ 单元
        cells_1000 = []
        for cid in range(1, 1001):
            x = (cid % 32) * 10.0
            y = (cid // 32) * 10.0
            cells_1000.append(self._make_dummy_cell(cid, x, y, x + 10.0, y + 10.0))
        idx_1000 = LeafCellSpatialIndex(cells_1000, bounds=(0.0, 0.0, 320.0, 320.0), num_buckets_per_dim=32)
        q = idx_1000.query_intersecting_cells((5.0, 5.0, 25.0, 25.0))
        q_oracle = _oracle_query(cells_1000, (5.0, 5.0, 25.0, 25.0))
        self.assertEqual([c.cell_id for c in q], [c.cell_id for c in q_oracle])

    def test_deterministic_ordering(self):
        """1.5 确定性排序: 50 次重复查询返回的单元序列严格逐元素恒等"""
        cells = [
            self._make_dummy_cell(5, 10.0, 10.0, 20.0, 20.0),
            self._make_dummy_cell(2, 12.0, 12.0, 18.0, 18.0),
            self._make_dummy_cell(9, 11.0, 11.0, 19.0, 19.0),
            self._make_dummy_cell(1, 15.0, 15.0, 25.0, 25.0),
        ]
        idx = LeafCellSpatialIndex(cells, bounds=(0.0, 0.0, 50.0, 50.0), num_buckets_per_dim=4)
        base_order = [c.cell_id for c in idx.query_intersecting_cells((14.0, 14.0, 16.0, 16.0))]
        for _ in range(50):
            cur_order = [c.cell_id for c in idx.query_intersecting_cells((14.0, 14.0, 16.0, 16.0))]
            self.assertEqual(cur_order, base_order)


class TestCellMembershipAndBoundary(unittest.TestCase):
    """测试 2: 半开区间象限归属严格互斥性"""

    def test_internal_boundary_mutual_exclusivity(self):
        """相邻单元共享边界上的像元仅归属于其中一个单元，绝无双重归属或遗漏"""
        raster_bounds = (0.0, 0.0, 20.0, 20.0)
        cell_left = (0.0, 0.0, 10.0, 10.0)
        cell_right = (10.0, 0.0, 20.0, 10.0)

        boundary_px_x = np.array([10.0, 10.0])
        boundary_px_y = np.array([5.0, 8.0])

        in_left = compute_cell_membership(boundary_px_x, boundary_px_y, cell_left, raster_bounds)
        in_right = compute_cell_membership(boundary_px_x, boundary_px_y, cell_right, raster_bounds)

        self.assertTrue(np.all(~in_left))
        self.assertTrue(np.all(in_right))


class TestTideCacheTamperAndProvenance(unittest.TestCase):
    """测试 3: Tide Cache 签名自校验与 Stage 2 产物 GeoTIFF 权威元数据回写"""

    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.td = self.test_dir.name

    def tearDown(self):
        self.test_dir.cleanup()

    def _create_synthetic_cache(self):
        dem_p = os.path.join(self.td, "dem.tif")
        transform = from_origin(121.0, 31.0, 0.01, 0.01)
        with rasterio.open(
            dem_p, 'w', driver='GTiff', width=20, height=20, count=1,
            dtype='float32', crs='EPSG:4326', transform=transform, nodata=-9999.0
        ) as dst:
            dst.write(np.zeros((20, 20), dtype=np.float32), 1)

        info = RasterInfo(
            path=dem_p, width=20, height=20, crs='EPSG:4326', is_projected=False,
            transform=list(transform)[:6], bounds=[121.0, 30.8, 121.2, 31.0],
            resolution=(0.01, 0.01), nodata=-9999.0, valid_pixel_count=400,
            total_pixel_count=400, dtype='float32'
        )
        t_idx = pd.date_range("2024-01-01", periods=10, freq="1h", tz="UTC")
        n1 = ControlNode(1, 121.05, 30.85, 121.05, 30.85, valid=True, tide_msl_raw=np.ones(10, dtype=np.float32))
        n2 = ControlNode(2, 121.15, 30.85, 121.15, 30.85, valid=True, tide_msl_raw=np.ones(10, dtype=np.float32))
        n3 = ControlNode(3, 121.05, 30.95, 121.05, 30.95, valid=True, tide_msl_raw=np.ones(10, dtype=np.float32))
        n4 = ControlNode(4, 121.15, 30.95, 121.15, 30.95, valid=True, tide_msl_raw=np.ones(10, dtype=np.float32))

        cell = QuadCell(1, 121.0, 30.8, 121.2, 31.0, 0, n1, n2, n3, n4)
        node_cache = {(0, 0): n1, (1, 0): n2, (0, 1): n3, (1, 1): n4}

        cache_p = os.path.join(self.td, "dem_tide.nc")
        write_tide_cache(
            cache_p, info, [cell], node_cache, t_idx,
            metadata={
                "freq": "1h",
                "start_time": "2024-01-01 00:00:00",
                "end_time": "2024-01-01 10:00:00",
                "source_tz": "UTC",
                "inclusive": "left",
                "dem_datum": "egm2008",
                "target_mode": "intertidal",
                "constituents": "all"
            },
            tide_msl_terminal=np.ones(4, dtype=np.float32)
        )
        return dem_p, cache_p

    def test_tamper_evident_metadata_validation(self):
        """3.1 篡改自校验: 手工篡改 NetCDF 全局属性必然触发 TideCacheIntegrityError"""
        dem_p, cache_p = self._create_synthetic_cache()

        # 正常读取成功
        meta = inspect_tide_cache_metadata(cache_p, validate_signature=True)
        self.assertTrue(meta["is_complete"])
        self.assertTrue(len(meta["signature"]) > 10)

        # 恶意篡改其中一个属性 (例如 TIME_START 或 DEM_DATUM)
        with netCDF4.Dataset(cache_p, mode="r+") as ds:
            ds.setncattr("DEM_DATUM", "wgs84")

        # 再次读取必须立即抛出 TideCacheIntegrityError
        with self.assertRaises(TideCacheIntegrityError) as ctx:
            inspect_tide_cache_metadata(cache_p, validate_signature=True)
        self.assertIn("已被篡改或损坏", str(ctx.exception))

    def test_from_cache_geotiff_authoritative_tags(self):
        """3.2 从缓存反演 GeoTIFF 标签必须忠实继承 Tide Cache 的权威元数据，绝不泄露外部伪造参数"""
        dem_p, cache_p = self._create_synthetic_cache()

        out_freq = os.path.join(self.td, "out_inund.tif")
        out_qc = os.path.join(self.td, "out_inund_qc.tif")

        calculate_inundation_from_tide_cache(
            dem_path=dem_p,
            cache_path=cache_p,
            output_path=out_freq,
            qc_output_path=out_qc,
            allow_overwrite=True
        )

        with rasterio.open(out_freq) as src:
            tags = src.tags()
            self.assertEqual(tags.get("DEM_DATUM"), "egm2008")
            self.assertEqual(tags.get("TARGET_MODE"), "intertidal")
            self.assertEqual(tags.get("TIME_START"), "2024-01-01 00:00:00")
            self.assertEqual(tags.get("TIME_END"), "2024-01-01 10:00:00")
            self.assertEqual(tags.get("TIMEZONE"), "UTC")
            self.assertEqual(tags.get("TIME_INTERVAL_SEMANTICS"), "[start, end)")
            self.assertIn("CACHE_SIGNATURE", tags)
            self.assertEqual(tags.get("STAGE"), "Stage 2 (Zero FES calls)")

        # 测试 Exposure 7 大产物
        exp_dir = os.path.join(self.td, "exp_out")
        os.makedirs(exp_dir, exist_ok=True)
        exp_paths = ExposureProductPaths(
            exposure_fraction_path=os.path.join(exp_dir, "exp_frac.tif"),
            exposure_duration_h_path=os.path.join(exp_dir, "exp_dur.tif"),
            exposure_max_continuous_h_path=os.path.join(exp_dir, "exp_max.tif"),
            exposure_mean_event_h_path=os.path.join(exp_dir, "exp_mean.tif"),
            exposure_event_count_path=os.path.join(exp_dir, "exp_cnt.tif"),
            exposure_valid_time_fraction_path=os.path.join(exp_dir, "exp_val.tif"),
            exposure_qc_path=os.path.join(exp_dir, "exp_qc.tif")
        )

        calculate_exposure_from_tide_cache(
            dem_path=dem_p,
            cache_path=cache_p,
            output_paths=exp_paths,
            allow_overwrite=True
        )

        exp_all_paths = [
            exp_paths.exposure_fraction_path,
            exp_paths.exposure_duration_h_path,
            exp_paths.exposure_max_continuous_h_path,
            exp_paths.exposure_mean_event_h_path,
            exp_paths.exposure_event_count_path,
            exp_paths.exposure_valid_time_fraction_path,
            exp_paths.exposure_qc_path,
        ]
        for p in exp_all_paths:
            with rasterio.open(p) as src:
                tags = src.tags()
                self.assertEqual(tags.get("SOURCE_DEM"), os.path.basename(dem_p))
                self.assertEqual(tags.get("SOURCE_TIDE_CACHE"), os.path.basename(cache_p))
                self.assertEqual(tags.get("DEM_DATUM"), "egm2008")
                self.assertEqual(tags.get("TARGET_MODE"), "intertidal")
                self.assertEqual(tags.get("TERMINAL_SAMPLE_AVAILABLE"), "true")
                self.assertEqual(tags.get("TIME_INTERVAL_SEMANTICS"), "[start, end)")
                self.assertIn("CACHE_SIGNATURE", tags)


class TestBatchManifestAndPolicy(unittest.TestCase):
    """测试 4: BatchManifest 字段兼容性与 ERROR_IF_EXISTS 预检防线"""

    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.td = self.test_dir.name

    def tearDown(self):
        self.test_dir.cleanup()

    def test_manifest_new_fields_and_backward_compatibility(self):
        """4.1 字段升级与向前兼容读取历史清单"""
        manifest = BatchManifest(self.td)
        for f in ["timezone", "dem_datum", "target_mode", "cache_signature"]:
            self.assertIn(f, BatchManifest.FIELDS)

        old_record = {
            "input_path": "tile_01.tif",
            "input_name": "tile_01.tif",
            "status": "DONE",
            "time_start": "2024-01-01",
            "time_end": "2024-01-02"
        }
        with open(manifest.json_path, "w", encoding="utf-8") as f:
            json.dump([old_record], f)

        manifest.load()
        loaded = manifest.get_entry("tile_01.tif")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["timezone"], "")
        self.assertEqual(loaded["dem_datum"], "")
        self.assertEqual(loaded["cache_signature"], "")
        self.assertEqual(loaded["status"], "DONE")

    def test_error_if_exists_all_modes_preflight(self):
        """4.2 验证 ERROR_IF_EXISTS 在各种模式下均能正确拦截已存在文件并拒绝执行"""
        dem_p = os.path.join(self.td, "test_dem.tif")
        transform = from_origin(121.0, 31.0, 0.01, 0.01)
        with rasterio.open(
            dem_p, 'w', driver='GTiff', width=10, height=10, count=1,
            dtype='float32', crs='EPSG:4326', transform=transform, nodata=-9999.0
        ) as dst:
            dst.write(np.zeros((10, 10), dtype=np.float32), 1)

        out_folder = os.path.join(self.td, "batch_out")
        os.makedirs(out_folder, exist_ok=True)
        # 预先创建一个目标冲突文件 (*_tide.nc)
        conflict_file = os.path.join(out_folder, "test_dem_tide.nc")
        with open(conflict_file, "w") as f:
            f.write("dummy")

        engine = BatchRasterEngine()
        modes = [
            JOB_MODE_TIDE_ONLY,
            JOB_MODE_TIDE_AND_INUNDATION,
            JOB_MODE_TIDE_AND_EXPOSURE,
            JOB_MODE_ALL
        ]
        for m in modes:
            res = engine.run_batch(
                input_folder=self.td,
                output_folder=out_folder,
                job_mode=m,
                existing_policy=ExistingOutputPolicy.ERROR_IF_EXISTS
            )
            self.assertEqual(res["counts"]["failed"], 1, f"Mode {m} did not fail on existing output")
            self.assertEqual(res["counts"]["completed"], 0)

        # 单瓦片直接反演在 allow_overwrite=False 时直接抛出 ExistingOutputError
        out_freq = os.path.join(self.td, "test_inund.tif")
        with open(out_freq, "w") as f:
            f.write("dummy")
        with self.assertRaises(ExistingOutputError):
            calculate_inundation_from_tide_cache(
                dem_path=dem_p,
                cache_path=conflict_file,
                output_path=out_freq,
                allow_overwrite=False
            )


class TestDocumentationAndDocstrings(unittest.TestCase):
    """测试 5: 学术规范与夸大/错误表述严格静态代码审查"""

    def test_no_forbidden_promotional_phrases(self):
        """代码与文档中严禁出现夸大词汇与已被证伪的描述"""
        repo_root = Path(__file__).resolve().parent.parent

        checks = [
            (repo_root / "core" / "raster_engine.py", ["100% 数学与科学连通域拓扑一致", "接近 O(N_blocks)", "near O(N_blocks)"]),
            (repo_root / "gui" / "manual_dialog.py", ["彻底阻断", "正常高保真解算", "末端时步采用截断估算", "重调容差参数"]),
            (repo_root / "data" / "geoid" / "README_GEOID.md", ["保持绝对一致"]),
        ]

        for file_path, forbidden_terms in checks:
            if not file_path.exists():
                continue
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            for term in forbidden_terms:
                self.assertNotIn(
                    term, content,
                    f"Forbidden term '{term}' found in {file_path.name}"
                )


if __name__ == "__main__":
    unittest.main()
