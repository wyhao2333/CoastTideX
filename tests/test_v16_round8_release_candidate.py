"""
CoastTideX v1.6 Beta — Round 8 Release Candidate & Merge-Gate Hardening Tests
验证以下核心特性:
1. 非 UTC 时区 (Asia/Shanghai, America/New_York) 与 UTC 下 Inundation 与 Exposure GeoTIFF 元数据标签的严密统一与时区溯源;
2. 真实 Production Raster Pipeline 中 LeafCellSpatialIndex 与 Brute-Force Candidate Selector 的全产物数值等价回归 (Inundation 2大栅格 + Exposure 7大栅格);
3. Tide Cache 结构完整性轻量校验函数 validate_tide_cache_structure 的完备防御测试 (维度、变量、单调时轴、步长一致性、矩阵形状、拓扑节点索引越界);
4. Schema 1.1 遗留兼容边界测试 (拒绝 inclusive != 'left'; 对 inclusive == 'left' 且缺失 terminal sample 规范降级并置位 QC bit 3);
5. 科学文档与代码注释的事实级静态一致性审查 (禁词、FES 掩膜规格、基准科学术语)。
"""

import os
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import netCDF4
import rasterio
from rasterio.transform import from_origin

from core.tide_cache import (
    write_tide_cache,
    read_tide_cache,
    read_tide_cache_structure,
    calculate_inundation_from_tide_cache,
    calculate_exposure_from_tide_cache,
    validate_tide_cache_structure,
    inspect_tide_cache_metadata,
    TideCacheIntegrityError,
    TideCacheCompatibilityError,
    ExistingOutputError
)
from core.raster_engine import (
    RasterTideEngine,
    ControlNode,
    QuadCell,
    LeafCellSpatialIndex,
    QC_NODATA
)
from core.exposure_engine import (
    ExposureProductPaths,
    QC_EXP_VALID,
    QC_EXP_TERMINAL_UNAVAILABLE,
    QC_EXP_PARTIAL_VALID_TIME,
    QC_EXP_PERMANENTLY_SUBMERGED,
    QC_EXP_PERMANENTLY_EXPOSED,
    QC_EXP_NODATA
)


class BruteForceSpatialIndex:
    """暴力空间候选查询基准器 (Ground-Truth Oracle)"""
    def __init__(self, leaf_cells, bounds=None):
        self.leaf_cells = list(leaf_cells)
        self.bounds = bounds

    def query_intersecting_cells(self, query_bbox):
        q_x0, q_y0, q_x1, q_y1 = query_bbox
        matched = []
        for cell in self.leaf_cells:
            cx0 = getattr(cell, "x0", getattr(cell, "x_min", 0.0))
            cx1 = getattr(cell, "x1", getattr(cell, "x_max", 0.0))
            cy0 = getattr(cell, "y0", getattr(cell, "y_min", 0.0))
            cy1 = getattr(cell, "y1", getattr(cell, "y_max", 0.0))
            # 严格 AABB 相交判定
            if not (cx1 < q_x0 or cx0 > q_x1 or cy1 < q_y0 or cy0 > q_y1):
                matched.append(cell)
        return matched


class TestNonUtcProvenanceUnification(unittest.TestCase):
    """1. 非 UTC 时区与 UTC 时区下元数据统一性测试"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="cx_test_provenance_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_mock_dem_and_cache(self, start_str: str, end_str: str, tz_str: str, freq: str = "30min", inclusive: str = "left"):
        dem_path = os.path.join(self.temp_dir, f"test_dem_{tz_str.replace('/', '_')}.tif")
        w, h = 60, 60
        transform = from_origin(121.5, 31.5, 0.001, 0.001)
        data = np.full((h, w), 0.5, dtype=np.float32)
        profile = {
            "driver": "GTiff",
            "height": h,
            "width": w,
            "count": 1,
            "dtype": rasterio.float32,
            "crs": "EPSG:4326",
            "transform": transform,
            "nodata": -9999.0
        }
        with rasterio.open(dem_path, "w", **profile) as dst:
            dst.write(data, 1)

        engine = RasterTideEngine()
        info = engine.inspect_raster(dem_path)

        dr = pd.date_range(start_str, end_str, freq=freq, inclusive=inclusive, tz=tz_str)
        n_times = len(dr)
        times_utc = dr.tz_convert("UTC")

        # 构造 4 个覆盖全图的角点控制节点
        n0 = ControlNode(node_id=0, x=121.50, y=31.44, lon=121.50, lat=31.44, valid=True, static_offset_m=0.0)
        n1 = ControlNode(node_id=1, x=121.56, y=31.44, lon=121.56, lat=31.44, valid=True, static_offset_m=0.0)
        n2 = ControlNode(node_id=2, x=121.50, y=31.50, lon=121.50, lat=31.50, valid=True, static_offset_m=0.0)
        n3 = ControlNode(node_id=3, x=121.56, y=31.50, lon=121.56, lat=31.50, valid=True, static_offset_m=0.0)
        for n in (n0, n1, n2, n3):
            # 简谐潮波时序
            t_steps = np.linspace(0, 4 * np.pi, n_times, endpoint=False)
            series = 1.0 * np.sin(t_steps).astype(np.float32)
            n.tide_msl_raw = series
            n.water_levels_sorted = np.sort(series)

        cell = QuadCell(
            cell_id=0,
            x_min=121.50, y_min=31.44, x_max=121.56, y_max=31.50,
            level=0, node_a=n0, node_b=n1, node_c=n2, node_d=n3
        )

        term_tide = np.array([0.2, 0.2, 0.2, 0.2], dtype=np.float32)
        cache_path = os.path.join(self.temp_dir, f"cache_{tz_str.replace('/', '_')}_tide.nc")

        cache_meta = {
            "start_time": start_str,
            "end_time": end_str,
            "freq": freq,
            "source_tz": tz_str,
            "inclusive": inclusive,
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

        nodes_dict = {(int(n.x * 1000), int(n.y * 1000)): n for n in (n0, n1, n2, n3)}
        write_tide_cache(
            cache_path=cache_path,
            info=info,
            leaf_cells=[cell],
            node_cache=nodes_dict,
            time_index=times_utc,
            metadata=cache_meta,
            tide_msl_terminal=term_tide,
            schema_version="1.2"
        )
        return dem_path, cache_path

    def test_asia_shanghai_inundation_and_exposure_tags_alignment(self):
        """测试 Asia/Shanghai 时区下 Inundation 与 Exposure 的 GeoTIFF 元数据完全对齐"""
        start_cst = "2024-01-01 08:00:00"
        end_cst = "2024-01-01 18:00:00"
        dem_path, cache_path = self._create_mock_dem_and_cache(start_cst, end_cst, "Asia/Shanghai")

        # 1. 解算 Inundation from Cache
        inund_out = os.path.join(self.temp_dir, "inund_cst.tif")
        calculate_inundation_from_tide_cache(
            dem_path=dem_path,
            cache_path=cache_path,
            output_path=inund_out
        )

        # 2. 解算 Exposure from Cache
        exp_dir = os.path.join(self.temp_dir, "exp_cst")
        exp_res = calculate_exposure_from_tide_cache(
            dem_path=dem_path,
            cache_path=cache_path,
            output_dir=exp_dir
        )

        with rasterio.open(inund_out) as src_inund:
            inund_tags = src_inund.tags()

        with rasterio.open(exp_res["products"].exposure_duration_h_path) as src_exp:
            exp_tags = src_exp.tags()

        # 验证 Inundation 标签
        self.assertEqual(inund_tags.get("REQUESTED_TIME_START"), "2024-01-01 08:00:00")
        self.assertEqual(inund_tags.get("REQUESTED_TIME_END"), "2024-01-01 18:00:00")
        self.assertEqual(inund_tags.get("TIME_START"), "2024-01-01 08:00:00")
        self.assertEqual(inund_tags.get("TIME_END"), "2024-01-01 18:00:00")
        self.assertEqual(inund_tags.get("TIMEZONE"), "Asia/Shanghai")
        self.assertTrue(inund_tags.get("TIME_START_UTC", "").startswith("2024-01-01T00:00:00"))
        self.assertTrue(inund_tags.get("TIME_END_UTC", "").startswith("2024-01-01T10:00:00"))
        self.assertEqual(inund_tags.get("TIME_START_UTC_EPOCH"), "1704067200.0")
        self.assertEqual(inund_tags.get("TIME_END_UTC_EPOCH"), "1704103200.0")
        self.assertEqual(inund_tags.get("TIME_INTERVAL_SEMANTICS"), "[start, end)")

        # 验证 Exposure 标签与 Inundation 完全对齐
        self.assertEqual(exp_tags.get("REQUESTED_TIME_START"), inund_tags.get("REQUESTED_TIME_START"))
        self.assertEqual(exp_tags.get("REQUESTED_TIME_END"), inund_tags.get("REQUESTED_TIME_END"))
        self.assertEqual(exp_tags.get("TIME_START"), inund_tags.get("TIME_START"))
        self.assertEqual(exp_tags.get("TIME_END"), inund_tags.get("TIME_END"))
        self.assertEqual(exp_tags.get("TIMEZONE"), inund_tags.get("TIMEZONE"))
        self.assertEqual(exp_tags.get("TIME_START_UTC"), inund_tags.get("TIME_START_UTC"))
        self.assertEqual(exp_tags.get("TIME_END_UTC"), inund_tags.get("TIME_END_UTC"))
        self.assertEqual(exp_tags.get("TIME_START_UTC_EPOCH"), inund_tags.get("TIME_START_UTC_EPOCH"))
        self.assertEqual(exp_tags.get("TIME_END_UTC_EPOCH"), inund_tags.get("TIME_END_UTC_EPOCH"))
        self.assertEqual(exp_tags.get("TIME_INTERVAL_SEMANTICS"), "[start, end)")

    def test_utc_timezone_provenance_consistency(self):
        """测试 UTC 时区下 Inundation 与 Exposure 的元数据严格一致"""
        start_utc = "2024-01-01 00:00:00"
        end_utc = "2024-01-01 12:00:00"
        dem_path, cache_path = self._create_mock_dem_and_cache(start_utc, end_utc, "UTC")

        inund_out = os.path.join(self.temp_dir, "inund_utc.tif")
        calculate_inundation_from_tide_cache(dem_path=dem_path, cache_path=cache_path, output_path=inund_out)

        exp_dir = os.path.join(self.temp_dir, "exp_utc")
        exp_res = calculate_exposure_from_tide_cache(dem_path=dem_path, cache_path=cache_path, output_dir=exp_dir)

        with rasterio.open(inund_out) as src:
            t_in = src.tags()
        with rasterio.open(exp_res["products"].exposure_fraction_path) as src:
            t_exp = src.tags()

        self.assertEqual(t_in.get("REQUESTED_TIME_START"), "2024-01-01 00:00:00")
        self.assertEqual(t_in.get("TIME_START"), "2024-01-01 00:00:00")
        self.assertEqual(t_in.get("TIMEZONE"), "UTC")
        self.assertEqual(t_exp.get("REQUESTED_TIME_START"), "2024-01-01 00:00:00")
        self.assertEqual(t_exp.get("TIME_START"), "2024-01-01 00:00:00")
        self.assertEqual(t_exp.get("TIMEZONE"), "UTC")
        self.assertEqual(t_exp.get("TIME_INTERVAL_SEMANTICS"), "[start, end)")


class TestProductionSpatialIndexRasterEquivalence(unittest.TestCase):
    """2. 真实生产级管线下 LeafCellSpatialIndex 与 Brute-Force Selector 数值等价回归"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="cx_test_spatial_equiv_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _build_multicell_dem_and_cache(self):
        dem_path = os.path.join(self.temp_dir, "spatial_dem.tif")
        # 宽 120 x 高 120，分 4 个 QuadCell
        w, h = 120, 120
        transform = from_origin(120.0, 30.0, 0.001, 0.001)
        # 地形高程倾斜渐变
        ys, xs = np.mgrid[0:h, 0:w]
        dem_data = -1.0 + (xs / w) * 1.5 + (ys / h) * 0.8
        dem_data = dem_data.astype(np.float32)

        profile = {
            "driver": "GTiff",
            "height": h,
            "width": w,
            "count": 1,
            "dtype": rasterio.float32,
            "crs": "EPSG:4326",
            "transform": transform,
            "nodata": -9999.0
        }
        with rasterio.open(dem_path, "w", **profile) as dst:
            dst.write(dem_data, 1)

        engine = RasterTideEngine()
        info = engine.inspect_raster(dem_path)

        dr = pd.date_range("2024-01-01 00:00:00", "2024-01-01 12:00:00", freq="30min", inclusive="left", tz="UTC")
        n_times = len(dr)

        # 9 个规则分布节点构成 4 个单元 (2x2 网格)
        xs_c = [120.0, 120.06, 120.12]
        ys_c = [29.88, 29.94, 30.00]
        nodes = []
        nid = 0
        for y_v in ys_c:
            for x_v in xs_c:
                t_arr = np.sin(np.linspace(0, 3 * np.pi, n_times) + nid * 0.3).astype(np.float32)
                node = ControlNode(
                    node_id=nid, x=x_v, y=y_v, lon=x_v, lat=y_v, valid=True, static_offset_m=0.0,
                    water_levels_sorted=np.sort(t_arr), tide_msl_raw=t_arr
                )
                nodes.append(node)
                nid += 1

        leaf_cells = [
            QuadCell(cell_id=0, x_min=120.0, y_min=29.88, x_max=120.06, y_max=29.94, level=0,
                     node_a=nodes[0], node_b=nodes[1], node_c=nodes[3], node_d=nodes[4]),
            QuadCell(cell_id=1, x_min=120.06, y_min=29.88, x_max=120.12, y_max=29.94, level=0,
                     node_a=nodes[1], node_b=nodes[2], node_c=nodes[4], node_d=nodes[5]),
            QuadCell(cell_id=2, x_min=120.0, y_min=29.94, x_max=120.06, y_max=30.00, level=0,
                     node_a=nodes[3], node_b=nodes[4], node_c=nodes[6], node_d=nodes[7]),
            QuadCell(cell_id=3, x_min=120.06, y_min=29.94, x_max=120.12, y_max=30.00, level=0,
                     node_a=nodes[4], node_b=nodes[5], node_c=nodes[7], node_d=nodes[8]),
        ]

        term = np.full(len(nodes), 0.1, dtype=np.float32)
        cache_path = os.path.join(self.temp_dir, "spatial_equiv_tide.nc")
        nodes_dict = {(int(n.x * 1000), int(n.y * 1000)): n for n in nodes}

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
            "topology_valid_fraction_threshold": 0.5,
            "fes_model": "FES2022b",
            "fes_source_type": "native_lgp2"
        }

        write_tide_cache(
            cache_path=cache_path,
            info=info,
            leaf_cells=leaf_cells,
            node_cache=nodes_dict,
            time_index=dr,
            metadata=cache_meta,
            tide_msl_terminal=term,
            schema_version="1.2"
        )
        return dem_path, cache_path, leaf_cells, info.bounds

    def test_inundation_and_exposure_spatial_index_vs_bruteforce_equivalence(self):
        """对比 LeafCellSpatialIndex 与 BruteForceSpatialIndex 在真实产物 GeoTIFF 上的数值等价性"""
        dem_path, cache_path, leaf_cells, bounds = self._build_multicell_dem_and_cache()
        bf_index = BruteForceSpatialIndex(leaf_cells, bounds=bounds)

        # 1. Inundation: 生产索引 vs 暴力基准
        inund_prod = os.path.join(self.temp_dir, "inund_prod.tif")
        qc_prod = os.path.join(self.temp_dir, "inund_prod_qc.tif")
        inund_bf = os.path.join(self.temp_dir, "inund_bf.tif")
        qc_bf = os.path.join(self.temp_dir, "inund_bf_qc.tif")

        calculate_inundation_from_tide_cache(
            dem_path=dem_path, cache_path=cache_path,
            output_path=inund_prod, qc_output_path=qc_prod
        )
        calculate_inundation_from_tide_cache(
            dem_path=dem_path, cache_path=cache_path,
            output_path=inund_bf, qc_output_path=qc_bf,
            spatial_index=bf_index
        )

        with rasterio.open(inund_prod) as s_p, rasterio.open(inund_bf) as s_bf:
            arr_p = s_p.read(1)
            arr_bf = s_bf.read(1)
            np.testing.assert_allclose(arr_p, arr_bf, atol=1e-6, err_msg="Inundation 栅格值与暴力选择器不一致！")

        with rasterio.open(qc_prod) as s_p, rasterio.open(qc_bf) as s_bf:
            np.testing.assert_array_equal(s_p.read(1), s_bf.read(1), err_msg="Inundation QC 栅格与暴力选择器不一致！")

        # 2. Exposure: 生产索引 vs 暴力基准 (7 大产物全覆盖)
        exp_dir_prod = os.path.join(self.temp_dir, "exp_prod")
        exp_dir_bf = os.path.join(self.temp_dir, "exp_bf")

        res_p = calculate_exposure_from_tide_cache(dem_path=dem_path, cache_path=cache_path, output_dir=exp_dir_prod)
        res_bf = calculate_exposure_from_tide_cache(dem_path=dem_path, cache_path=cache_path, output_dir=exp_dir_bf, spatial_index=bf_index)

        prod_paths = res_p["products"]
        bf_paths = res_bf["products"]

        float_products = [
            ("exposure_fraction", prod_paths.exposure_fraction_path, bf_paths.exposure_fraction_path),
            ("exposure_duration_h", prod_paths.exposure_duration_h_path, bf_paths.exposure_duration_h_path),
            ("exposure_max_continuous_h", prod_paths.exposure_max_continuous_h_path, bf_paths.exposure_max_continuous_h_path),
            ("exposure_mean_event_h", prod_paths.exposure_mean_event_h_path, bf_paths.exposure_mean_event_h_path),
            ("exposure_valid_time_fraction", prod_paths.exposure_valid_time_fraction_path, bf_paths.exposure_valid_time_fraction_path),
        ]

        for name, p_path, bf_path in float_products:
            with rasterio.open(p_path) as s1, rasterio.open(bf_path) as s2:
                a1 = s1.read(1)
                a2 = s2.read(1)
                np.testing.assert_allclose(a1, a2, atol=1e-6, equal_nan=True, err_msg=f"Exposure 产物 {name} 与暴力基准数值不一致！")

        with rasterio.open(prod_paths.exposure_event_count_path) as s1, rasterio.open(bf_paths.exposure_event_count_path) as s2:
            np.testing.assert_array_equal(s1.read(1), s2.read(1), err_msg="exposure_event_count 产物与暴力基准不一致！")

        with rasterio.open(prod_paths.exposure_qc_path) as s1, rasterio.open(bf_paths.exposure_qc_path) as s2:
            np.testing.assert_array_equal(s1.read(1), s2.read(1), err_msg="exposure_qc 产物与暴力基准不一致！")


class TestTideCacheStructuralIntegrity(unittest.TestCase):
    """3. Tide Cache 物理与拓扑结构完整性深度核查测试"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="cx_test_integrity_")
        self.valid_cache = os.path.join(self.temp_dir, "valid_tide.nc")
        self._generate_minimal_valid_cache(self.valid_cache)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _generate_minimal_valid_cache(self, path: str):
        n_node = 4
        n_time = 10
        n_cell = 1
        with netCDF4.Dataset(path, mode="w", format="NETCDF4") as ds:
            ds.createDimension("time", n_time)
            ds.createDimension("node", n_node)
            ds.createDimension("cell", n_cell)
            ds.createDimension("bounds_dim", 4)
            ds.createDimension("corners_dim", 4)

            ds.setncattr("CACHE_COMPLETE", "true")
            ds.setncattr("CACHE_SCHEMA_VERSION", "1.2")
            ds.setncattr("CONTROL_NODE_COUNT", n_node)
            ds.setncattr("TIME_SAMPLES", n_time)
            ds.setncattr("LEAF_CELL_COUNT", n_cell)
            ds.setncattr("TIME_STEP_SECONDS", 1800.0)

            vt = ds.createVariable("time", "f8", ("time",))
            vt[:] = np.arange(1704067200, 1704067200 + n_time * 1800, 1800, dtype=np.float64)

            vx = ds.createVariable("node_x", "f8", ("node",))
            vx[:] = [121.0, 121.1, 121.0, 121.1]
            vy = ds.createVariable("node_y", "f8", ("node",))
            vy[:] = [31.0, 31.0, 31.1, 31.1]
            vlon = ds.createVariable("node_lon", "f8", ("node",))
            vlon[:] = [121.0, 121.1, 121.0, 121.1]
            vlat = ds.createVariable("node_lat", "f8", ("node",))
            vlat[:] = [31.0, 31.0, 31.1, 31.1]
            vval = ds.createVariable("node_valid", "u1", ("node",))
            vval[:] = [1, 1, 1, 1]
            voff = ds.createVariable("static_offset_m", "f4", ("node",))
            voff[:] = [0.0, 0.0, 0.0, 0.0]
            vcomp = ds.createVariable("component_id", "i4", ("node",))
            vcomp[:] = [1, 1, 1, 1]
            vqc = ds.createVariable("node_qc", "u4", ("node",))
            vqc[:] = [0, 0, 0, 0]

            vtide = ds.createVariable("tide_msl_m", "f4", ("node", "time"))
            vtide[:] = np.zeros((n_node, n_time), dtype=np.float32)

            vterm = ds.createVariable("tide_msl_terminal_m", "f4", ("node",))
            vterm[:] = np.zeros(n_node, dtype=np.float32)

            cbounds = ds.createVariable("cell_bounds", "f8", ("cell", "bounds_dim"))
            cbounds[:] = [[121.0, 31.0, 121.1, 31.1]]
            cnodes = ds.createVariable("cell_node_indices", "i4", ("cell", "corners_dim"))
            cnodes[:] = [[0, 1, 2, 3]]
            clvl = ds.createVariable("cell_level", "i4", ("cell",))
            clvl[:] = [0]
            cqc = ds.createVariable("cell_qc", "u4", ("cell",))
            cqc[:] = [0]
            cerr = ds.createVariable("cell_max_error", "f4", ("cell",))
            cerr[:] = [0.0]

    def test_valid_cache_passes_validation(self):
        """合法 cache 顺利通过 validate_tide_cache_structure"""
        validate_tide_cache_structure(self.valid_cache)

    def test_corrupt_missing_required_dimensions(self):
        """破坏测试 1: 缺少必需维度时抛出 TideCacheIntegrityError"""
        bad_cache = os.path.join(self.temp_dir, "bad_nodim.nc")
        with netCDF4.Dataset(self.valid_cache, "r") as src, netCDF4.Dataset(bad_cache, "w") as dst:
            for dimname, dim in src.dimensions.items():
                if dimname != "node":  # 移除 node 维度
                    dst.createDimension(dimname, len(dim))
            for varname, var in src.variables.items():
                if "node" not in var.dimensions:
                    dst.createVariable(varname, var.dtype, var.dimensions)[:] = var[:]

        with self.assertRaises(TideCacheIntegrityError):
            validate_tide_cache_structure(bad_cache)

    def test_corrupt_missing_required_variables(self):
        """破坏测试 2: 缺少必需变量 (如 cell_node_indices) 时抛出 TideCacheIntegrityError"""
        bad_cache = os.path.join(self.temp_dir, "bad_novar.nc")
        with netCDF4.Dataset(self.valid_cache, "r") as src, netCDF4.Dataset(bad_cache, "w") as dst:
            for dimname, dim in src.dimensions.items():
                dst.createDimension(dimname, len(dim))
            for varname, var in src.variables.items():
                if varname != "cell_node_indices":  # 移除 cell_node_indices 变量
                    v = dst.createVariable(varname, var.dtype, var.dimensions)
                    v[:] = var[:]

        with self.assertRaises(TideCacheIntegrityError):
            validate_tide_cache_structure(bad_cache)

    def test_corrupt_non_monotonic_time_axis(self):
        """破坏测试 3: 时间轴非单调递增或包含负步长时抛出 TideCacheIntegrityError"""
        bad_cache = os.path.join(self.temp_dir, "bad_time.nc")
        shutil.copy2(self.valid_cache, bad_cache)
        with netCDF4.Dataset(bad_cache, mode="a") as ds:
            times = ds.variables["time"][:]
            times[3] = times[2] - 100.0  # 篡改为回退时间
            ds.variables["time"][:] = times

        with self.assertRaises(TideCacheIntegrityError):
            validate_tide_cache_structure(bad_cache)

    def test_corrupt_time_cadence_mismatch(self):
        """破坏测试 4: 时间步长与 TIME_STEP_SECONDS 冲突时抛出 TideCacheIntegrityError"""
        bad_cache = os.path.join(self.temp_dir, "bad_cadence.nc")
        shutil.copy2(self.valid_cache, bad_cache)
        with netCDF4.Dataset(bad_cache, mode="a") as ds:
            times = ds.variables["time"][:]
            times[1] = times[0] + 60.0  # 单步 60s，与声明的 1800s 严重不符
            ds.variables["time"][:] = times

        with self.assertRaises(TideCacheIntegrityError):
            validate_tide_cache_structure(bad_cache)

    def test_corrupt_tide_msl_shape_mismatch(self):
        """破坏测试 5: tide_msl_m 变量形状与维度不符时抛出 TideCacheIntegrityError"""
        bad_cache = os.path.join(self.temp_dir, "bad_shape.nc")
        with netCDF4.Dataset(self.valid_cache, "r") as src, netCDF4.Dataset(bad_cache, "w") as dst:
            for dimname, dim in src.dimensions.items():
                dst.createDimension(dimname, len(dim))
            dst.createDimension("wrong_dim", 2)
            for varname, var in src.variables.items():
                if varname == "tide_msl_m":
                    v = dst.createVariable("tide_msl_m", "f4", ("node", "wrong_dim"))
                    v[:] = np.zeros((4, 2), dtype=np.float32)
                else:
                    v = dst.createVariable(varname, var.dtype, var.dimensions)
                    v[:] = var[:]

        with self.assertRaises(TideCacheIntegrityError):
            validate_tide_cache_structure(bad_cache)

    def test_corrupt_cell_node_index_out_of_bounds(self):
        """破坏测试 6: cell_node_indices 引用越界节点时抛出 TideCacheIntegrityError"""
        bad_cache = os.path.join(self.temp_dir, "bad_cell_idx.nc")
        shutil.copy2(self.valid_cache, bad_cache)
        with netCDF4.Dataset(bad_cache, mode="a") as ds:
            nodes = ds.variables["cell_node_indices"][:]
            nodes[0, 2] = 999  # 越界节点 (有效范围 0..3)
            ds.variables["cell_node_indices"][:] = nodes

        with self.assertRaises(TideCacheIntegrityError):
            validate_tide_cache_structure(bad_cache)


class TestSchema11BoundaryAndCompatibility(unittest.TestCase):
    """4. Schema 1.1 遗留兼容边界与降级行为测试"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="cx_test_schema11_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_synthetic_dem(self):
        dem_path = os.path.join(self.temp_dir, "dem_schema11.tif")
        w, h = 40, 40
        transform = from_origin(120.0, 30.0, 0.001, 0.001)
        data = np.full((h, w), 0.0, dtype=np.float32)  # 高程 0m
        profile = {
            "driver": "GTiff",
            "height": h,
            "width": w,
            "count": 1,
            "dtype": rasterio.float32,
            "crs": "EPSG:4326",
            "transform": transform,
            "nodata": -9999.0
        }
        with rasterio.open(dem_path, "w", **profile) as dst:
            dst.write(data, 1)
        return dem_path

    def test_schema_11_inclusive_both_rejected_by_exposure(self):
        """当 Tide Cache 为 inclusive='both' 时，Exposure 计算直接抛出 TideCacheCompatibilityError"""
        dem_path = self._create_synthetic_dem()
        engine = RasterTideEngine()
        info = engine.inspect_raster(dem_path)

        dr = pd.date_range("2024-01-01 00:00:00", "2024-01-01 02:00:00", freq="30min", inclusive="both", tz="UTC")
        n_times = len(dr)

        n0 = ControlNode(0, 120.0, 29.96, 120.0, 29.96, valid=True, static_offset_m=0.0,
                         water_levels_sorted=np.zeros(n_times, dtype=np.float32), tide_msl_raw=np.zeros(n_times, dtype=np.float32))
        n1 = ControlNode(1, 120.04, 29.96, 120.04, 29.96, valid=True, static_offset_m=0.0,
                         water_levels_sorted=np.zeros(n_times, dtype=np.float32), tide_msl_raw=np.zeros(n_times, dtype=np.float32))
        n2 = ControlNode(2, 120.0, 30.0, 120.0, 30.0, valid=True, static_offset_m=0.0,
                         water_levels_sorted=np.zeros(n_times, dtype=np.float32), tide_msl_raw=np.zeros(n_times, dtype=np.float32))
        n3 = ControlNode(3, 120.04, 30.0, 120.04, 30.0, valid=True, static_offset_m=0.0,
                         water_levels_sorted=np.zeros(n_times, dtype=np.float32), tide_msl_raw=np.zeros(n_times, dtype=np.float32))

        cell = QuadCell(0, 120.0, 29.96, 120.04, 30.0, 0, n0, n1, n2, n3)
        cache_path = os.path.join(self.temp_dir, "cache_both_tide.nc")
        nodes_dict = {(int(n.x * 1000), int(n.y * 1000)): n for n in (n0, n1, n2, n3)}

        write_tide_cache(
            cache_path=cache_path,
            info=info,
            leaf_cells=[cell],
            node_cache=nodes_dict,
            time_index=dr,
            metadata={
                "start_time": "2024-01-01 00:00:00",
                "end_time": "2024-01-01 02:00:00",
                "freq": "30min",
                "source_tz": "UTC",
                "inclusive": "both",
                "dem_datum": "egm2008",
                "target_mode": "intertidal",
                "topology_max_resolution_m": 100.0,
                "topology_valid_fraction_threshold": 0.5,
                "initial_control_spacing_m": 4000.0,
                "min_control_spacing_m": 500.0,
                "inundation_error_tolerance_pct": 1.0,
                "fes_model": "FES2022b",
                "fes_source_type": "native_lgp2"
            },
            schema_version="1.1"
        )

        with self.assertRaises(TideCacheCompatibilityError) as cm:
            calculate_exposure_from_tide_cache(dem_path=dem_path, cache_path=cache_path)
        self.assertIn("TIME_INCLUSIVE='both'", str(cm.exception))

    def test_schema_11_inclusive_left_without_terminal_degradation(self):
        """当 Tide Cache 为 inclusive='left' 但无 terminal 采样时，Exposure 排除末端区间并置位 QC bit 3"""
        dem_path = self._create_synthetic_dem()
        engine = RasterTideEngine()
        info = engine.inspect_raster(dem_path)

        dr = pd.date_range("2024-01-01 00:00:00", "2024-01-01 02:00:00", freq="30min", inclusive="left", tz="UTC")
        n_times = len(dr)  # 4 个样本点: 00:00, 00:30, 01:00, 01:30

        # 水位常时在 -1.0m (露出状态，H <= z)
        t_arr = np.full(n_times, -1.0, dtype=np.float32)
        n0 = ControlNode(0, 120.0, 29.96, 120.0, 29.96, valid=True, static_offset_m=0.0,
                         water_levels_sorted=np.sort(t_arr), tide_msl_raw=t_arr)
        n1 = ControlNode(1, 120.04, 29.96, 120.04, 29.96, valid=True, static_offset_m=0.0,
                         water_levels_sorted=np.sort(t_arr), tide_msl_raw=t_arr)
        n2 = ControlNode(2, 120.0, 30.0, 120.0, 30.0, valid=True, static_offset_m=0.0,
                         water_levels_sorted=np.sort(t_arr), tide_msl_raw=t_arr)
        n3 = ControlNode(3, 120.04, 30.0, 120.04, 30.0, valid=True, static_offset_m=0.0,
                         water_levels_sorted=np.sort(t_arr), tide_msl_raw=t_arr)

        cell = QuadCell(0, 120.0, 29.96, 120.04, 30.0, 0, n0, n1, n2, n3)
        cache_path = os.path.join(self.temp_dir, "cache_s11_left_tide.nc")
        nodes_dict = {(int(n.x * 1000), int(n.y * 1000)): n for n in (n0, n1, n2, n3)}

        # Schema 1.1: 无 tide_msl_terminal_m
        write_tide_cache(
            cache_path=cache_path,
            info=info,
            leaf_cells=[cell],
            node_cache=nodes_dict,
            time_index=dr,
            metadata={
                "start_time": "2024-01-01 00:00:00",
                "end_time": "2024-01-01 02:00:00",
                "freq": "30min",
                "source_tz": "UTC",
                "inclusive": "left",
                "dem_datum": "egm2008",
                "target_mode": "intertidal",
                "topology_max_resolution_m": 100.0,
                "topology_valid_fraction_threshold": 0.5,
                "initial_control_spacing_m": 4000.0,
                "min_control_spacing_m": 500.0,
                "inundation_error_tolerance_pct": 1.0,
                "fes_model": "FES2022b",
                "fes_source_type": "native_lgp2"
            },
            schema_version="1.1",
            tide_msl_terminal=None
        )

        exp_dir = os.path.join(self.temp_dir, "exp_s11")
        res = calculate_exposure_from_tide_cache(dem_path=dem_path, cache_path=cache_path, output_dir=exp_dir)

        # 检查有效时长: 请求总时长为 2.0 小时 (00:00~02:00)
        # 缺失 terminal 采样时，最后 01:30~02:00 区间无法积分，积分时长为 1.5 小时
        with rasterio.open(res["products"].exposure_duration_h_path) as src:
            dur_arr = src.read(1)
            self.assertAlmostEqual(float(dur_arr[10, 10]), 1.5, places=3)

        # 检查 valid_time_fraction: 1.5h / 2.0h = 75.0%
        with rasterio.open(res["products"].exposure_valid_time_fraction_path) as src:
            val_frac = src.read(1)
            self.assertAlmostEqual(float(val_frac[10, 10]), 75.0, places=2)

        # 检查 QC 掩膜包含 bit 3 (QC_EXP_TERMINAL_UNAVAILABLE = 8)
        with rasterio.open(res["products"].exposure_qc_path) as src:
            qc_arr = src.read(1)
            self.assertTrue(bool(int(qc_arr[10, 10]) & QC_EXP_TERMINAL_UNAVAILABLE))


class TestDocumentationAndDocstringIntegrity(unittest.TestCase):
    """5. 文档、用户手册与代码注释静态防夸大审查"""

    def test_forbidden_promotional_phrases(self):
        """严禁出现生产就绪、无条件厘米级、最强、极致等夸大修辞"""
        project_root = Path(__file__).resolve().parent.parent
        doc_files = [
            project_root / "README.md",
            project_root / "README_EN.md",
            project_root / "gui" / "manual_dialog.py",
            project_root / "CHANGELOG.md",
            project_root / "data" / "geoid" / "README_GEOID.md",
            project_root / "docs" / "V1_5_BETA_REAL_FES_VALIDATION.md"
        ]

        forbidden_patterns = [
            "全球最高精度",
            "极致",
            "工业级可靠性",
            "production ready",
            "瞬时平均海平面"
        ]

        for p in doc_files:
            if not p.exists():
                continue
            content = p.read_text(encoding="utf-8")
            for term in forbidden_patterns:
                # 检查若出现，必须是在被否定、警告或说明历史演进的语境中
                if term in content:
                    lines = [ln for ln in content.splitlines() if term in ln]
                    for ln in lines:
                        # 允许作为否定句 ("严禁宣称", "非", "not", "不代表")
                        is_negated = any(neg in ln for neg in ("严禁", "非", "不代表", "并非", "not", "Not", "NOT", "无法", "避免"))
                        self.assertTrue(
                            is_negated,
                            f"文件 {p.name} 中发现了未被否定的夸大/不规范词汇 '{term}':\n  -> {ln}"
                        )


if __name__ == "__main__":
    unittest.main()
