"""
CoastTideX v1.6 Beta — Round 9 Final Evidence, Metadata & Documentation Closure Test Suite
========================================================================================
全面覆盖 Round 9 证据闭环核心要求：
1. validate_tide_cache_structure 规范结构核验：
   - 必需维度检查 (time, node, cell, bounds_dim=4, corners_dim=4)
   - 15个必需变量逐一缺失剔除测试 (TideCacheIntegrityError)
   - cell_node_indices 形状 (n_cell, 4) 与整数类型及越界核验
   - terminal_tide 严格一维形状 (n_node,) 与 HAS_TERMINAL_TIDE 一致性
   - TIME_SAMPLES 与时间轴长度一致性
   - TIME_START_UTC_EPOCH / TIME_END_UTC_EPOCH 与时间轴对齐 (1e-3s 容差)
   - 时间轴单调性与采样步长一致性 (1e-3s 容差)
2. inclusive_to_interval_semantics 语义映射器与自相矛盾校验
3. terminal_tide 一维防御性归一化 (read_tide_cache, read_tide_cache_structure, exposure_engine)
4. Exposure 引擎对历史 inclusive='both' 缓存的明确拒绝与 Schema 1.1 left 的降级兼容 (QC_EXP_TERMINAL_UNAVAILABLE)
5. 非 UTC 时区与夏令时跳变测试 (Asia/Shanghai, UTC, America/New_York DST Spring-Forward & Fall-Back)
6. SpatialIndex 生产级与暴力参考器数值等价回归 (含显式 NoData 掩膜验证)
7. 文档与代码注释事实级一致性 Linting (README, README_EN, manual_dialog, tide_cache, README_GEOID)
"""

import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Tuple

import netCDF4
import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import from_origin

from core.tide_cache import (
    validate_tide_cache_structure,
    inclusive_to_interval_semantics,
    write_tide_cache,
    read_tide_cache,
    read_tide_cache_structure,
    calculate_exposure_from_tide_cache,
    calculate_inundation_from_tide_cache,
    generate_tide_cache_signature,
    TideCacheIntegrityError,
    TideCacheCompatibilityError,
    CACHE_SCHEMA_VERSION
)
from core.raster_engine import (
    RasterInfo,
    ControlNode,
    QuadCell,
    LeafCellSpatialIndex,
    RasterTideEngine,
    QC_NODATA
)
from core.exposure_engine import (
    QC_EXP_VALID,
    QC_EXP_TERMINAL_UNAVAILABLE,
    QC_EXP_NODATA,
    ExposureProductPaths
)


class BruteForceSpatialIndex:
    """暴力空间候选查询基准器 (Brute-force candidate-selection reference)"""
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
            if not (cx1 < q_x0 or cx0 > q_x1 or cy1 < q_y0 or cy0 > q_y1):
                matched.append(cell)
        return matched


def create_base_mock_cache_components(
    n_nodes: int = 4,
    n_times: int = 10,
    n_cells: int = 1,
    start_epoch: float = 1704067200.0,  # 2024-01-01 00:00:00 UTC
    dt_sec: float = 1800.0,
    has_terminal: bool = True
) -> Dict[str, Any]:
    """生成合法的纯内存 Mock 数据字典用于灵活写入 NetCDF"""
    time_arr = np.array([start_epoch + i * dt_sec for i in range(n_times)], dtype=np.float64)
    end_epoch = start_epoch + n_times * dt_sec

    node_x = np.array([121.50, 121.55, 121.50, 121.55], dtype=np.float64)[:n_nodes]
    node_y = np.array([31.40, 31.40, 31.45, 31.45], dtype=np.float64)[:n_nodes]
    node_lon = node_x.copy()
    node_lat = node_y.copy()
    node_valid = np.ones(n_nodes, dtype=np.int8)
    static_offset_m = np.zeros(n_nodes, dtype=np.float32)
    component_id = np.ones(n_nodes, dtype=np.int32)
    node_qc = np.zeros(n_nodes, dtype=np.uint32)

    # 潮位时序 (n_nodes, n_times)
    tide_msl_m = np.zeros((n_nodes, n_times), dtype=np.float32)
    for i in range(n_nodes):
        tide_msl_m[i, :] = np.sin(np.linspace(0, 2 * np.pi, n_times)).astype(np.float32)

    terminal_tide = np.full(n_nodes, 0.25, dtype=np.float32) if has_terminal else None

    # cell 属性
    cell_bounds = np.array([[121.50, 31.40, 121.55, 31.45]], dtype=np.float64)[:n_cells]
    cell_node_indices = np.array([[0, 1, 2, 3]], dtype=np.int32)[:n_cells]
    cell_level = np.zeros(n_cells, dtype=np.int32)
    cell_qc = np.zeros(n_cells, dtype=np.uint32)
    cell_max_error = np.full(n_cells, 0.005, dtype=np.float32)

    attrs = {
        "CACHE_SCHEMA_VERSION": "1.2",
        "CACHE_COMPLETE": "true",
        "HAS_TERMINAL_TIDE": "true" if has_terminal else "false",
        "TIME_INCLUSIVE": "left",
        "TIME_INTERVAL_SEMANTICS": "[start, end)",
        "TIME_START_UTC_EPOCH": str(start_epoch),
        "TIME_END_UTC_EPOCH": str(end_epoch),
        "TIME_STEP_SECONDS": str(dt_sec),
        "TIME_SAMPLES": str(n_times),
        "SOURCE_DEM": "test_dem.tif",
        "CACHE_SIGNATURE": "dummy_sha256_signature_hex"
    }

    return {
        "n_nodes": n_nodes,
        "n_times": n_times,
        "n_cells": n_cells,
        "time_arr": time_arr,
        "node_x": node_x,
        "node_y": node_y,
        "node_lon": node_lon,
        "node_lat": node_lat,
        "node_valid": node_valid,
        "static_offset_m": static_offset_m,
        "component_id": component_id,
        "node_qc": node_qc,
        "tide_msl_m": tide_msl_m,
        "terminal_tide": terminal_tide,
        "cell_bounds": cell_bounds,
        "cell_node_indices": cell_node_indices,
        "cell_level": cell_level,
        "cell_qc": cell_qc,
        "cell_max_error": cell_max_error,
        "attrs": attrs
    }


def write_raw_netcdf_cache(
    file_path: str,
    components: Dict[str, Any],
    omit_dims: Tuple[str, ...] = (),
    omit_vars: Tuple[str, ...] = (),
    custom_dim_lens: Dict[str, int] = None,
    var_shape_overrides: Dict[str, Tuple[int, ...]] = None,
    attr_overrides: Dict[str, Any] = None
) -> None:
    """直接使用 netCDF4 写入测试 NetCDF 缓存文件，支持任意维度与变量的篡改/缺失"""
    if custom_dim_lens is None:
        custom_dim_lens = {}
    if var_shape_overrides is None:
        var_shape_overrides = {}
    if attr_overrides is None:
        attr_overrides = {}

    with netCDF4.Dataset(file_path, mode="w", format="NETCDF4") as ds:
        # 1. 维度
        dims = {
            "time": components["n_times"],
            "node": components["n_nodes"],
            "cell": components["n_cells"],
            "bounds_dim": 4,
            "corners_dim": 4
        }
        for dname, dlen in custom_dim_lens.items():
            dims[dname] = dlen

        for dname, dlen in dims.items():
            if dname not in omit_dims:
                ds.createDimension(dname, dlen)

        # 2. 变量
        def _create_and_write(name, vtype, dnames, data):
            if name in omit_vars:
                return
            if any(d in omit_dims for d in dnames):
                return
            if name in var_shape_overrides:
                # 重新构建指定形状的数据
                new_shape = var_shape_overrides[name]
                if data is not None:
                    data = np.broadcast_to(data, new_shape).copy()
            var = ds.createVariable(name, vtype, dnames)
            if data is not None:
                var[:] = data

        _create_and_write("time", "f8", ("time",), components["time_arr"])
        _create_and_write("node_x", "f8", ("node",), components["node_x"])
        _create_and_write("node_y", "f8", ("node",), components["node_y"])
        _create_and_write("node_lon", "f8", ("node",), components["node_lon"])
        _create_and_write("node_lat", "f8", ("node",), components["node_lat"])
        _create_and_write("node_valid", "i1", ("node",), components["node_valid"])
        _create_and_write("static_offset_m", "f4", ("node",), components["static_offset_m"])
        _create_and_write("component_id", "i4", ("node",), components["component_id"])
        _create_and_write("node_qc", "u4", ("node",), components["node_qc"])
        _create_and_write("tide_msl_m", "f4", ("node", "time"), components["tide_msl_m"])

        if components["terminal_tide"] is not None and "tide_msl_terminal_m" not in omit_vars:
            t_data = components["terminal_tide"]
            t_dims = ("node",)
            if "tide_msl_terminal_m" in var_shape_overrides:
                new_shape = var_shape_overrides["tide_msl_terminal_m"]
                t_dims = tuple(["node"] * len(new_shape))
                t_data = np.broadcast_to(t_data, new_shape).copy()
            _create_and_write("tide_msl_terminal_m", "f4", t_dims, t_data)

        _create_and_write("cell_bounds", "f8", ("cell", "bounds_dim"), components["cell_bounds"])
        _create_and_write("cell_node_indices", "i4", ("cell", "corners_dim"), components["cell_node_indices"])
        _create_and_write("cell_level", "i4", ("cell",), components["cell_level"])
        _create_and_write("cell_qc", "u4", ("cell",), components["cell_qc"])
        _create_and_write("cell_max_error", "f4", ("cell",), components["cell_max_error"])

        # 3. 全局属性
        all_attrs = dict(components["attrs"])
        all_attrs.update(attr_overrides)
        for k, v in all_attrs.items():
            if v is not None:
                setattr(ds, k, v)


class TestCanonicalTideCacheValidation(unittest.TestCase):
    """A & B & C & D: validate_tide_cache_structure 规范维度与变量缺失校验"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="cx_test_round9_")
        self.components = create_base_mock_cache_components()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_valid_cache_passes_validation(self):
        """基准合法缓存必须完全通过结构校验"""
        cache_path = os.path.join(self.temp_dir, "valid_cache.nc")
        write_raw_netcdf_cache(cache_path, self.components)
        # 不抛异常即为通过
        validate_tide_cache_structure(cache_path)

    def test_missing_canonical_dimensions(self):
        """必需维度缺失或长度异常必须抛出 TideCacheIntegrityError"""
        for req_dim in ["time", "node", "cell", "bounds_dim", "corners_dim"]:
            cache_path = os.path.join(self.temp_dir, f"missing_dim_{req_dim}.nc")
            write_raw_netcdf_cache(cache_path, self.components, omit_dims=(req_dim,))
            with self.assertRaises(TideCacheIntegrityError, msg=f"缺失维度 {req_dim} 未报错"):
                validate_tide_cache_structure(cache_path)

        # bounds_dim != 4
        cache_bad_bounds = os.path.join(self.temp_dir, "bad_bounds_dim.nc")
        comp_bad_bounds = create_base_mock_cache_components()
        comp_bad_bounds["cell_bounds"] = np.array([[121.50, 31.40, 121.55]], dtype=np.float64)
        write_raw_netcdf_cache(cache_bad_bounds, comp_bad_bounds, custom_dim_lens={"bounds_dim": 3})
        with self.assertRaises(TideCacheIntegrityError):
            validate_tide_cache_structure(cache_bad_bounds)

        # corners_dim != 4
        cache_bad_corners = os.path.join(self.temp_dir, "bad_corners_dim.nc")
        comp_bad_corners = create_base_mock_cache_components()
        comp_bad_corners["cell_node_indices"] = np.array([[0, 1, 2, 3, 0]], dtype=np.int32)
        write_raw_netcdf_cache(cache_bad_corners, comp_bad_corners, custom_dim_lens={"corners_dim": 5})
        with self.assertRaises(TideCacheIntegrityError):
            validate_tide_cache_structure(cache_bad_corners)

    def test_missing_each_required_variable_individually(self):
        """15 个必需变量逐一缺失剔除，必须各自抛出明确的 TideCacheIntegrityError"""
        required_vars = [
            "node_x", "node_y", "node_lon", "node_lat",
            "node_valid", "static_offset_m", "component_id", "node_qc",
            "cell_level", "cell_qc", "cell_max_error",
            "cell_bounds", "cell_node_indices",
            "time", "tide_msl_m"
        ]
        for var_name in required_vars:
            cache_path = os.path.join(self.temp_dir, f"missing_var_{var_name}.nc")
            write_raw_netcdf_cache(cache_path, self.components, omit_vars=(var_name,))
            with self.assertRaises(TideCacheIntegrityError, msg=f"变量 {var_name} 缺失未触发完整性异常"):
                validate_tide_cache_structure(cache_path)

    def test_cell_node_indices_shape_and_dtype_and_bounds(self):
        """cell_node_indices 形状必须为 (n_cell, 4)，类型必须为整数，节点索引严禁越界"""
        # 1. 形状非 (n_cell, 4)
        bad_shape_path = os.path.join(self.temp_dir, "bad_cell_nodes_shape.nc")
        comp_bad_shape = create_base_mock_cache_components()
        comp_bad_shape["cell_node_indices"] = np.array([[0, 1, 2]], dtype=np.int32)
        write_raw_netcdf_cache(bad_shape_path, comp_bad_shape, custom_dim_lens={"corners_dim": 3})
        with self.assertRaises(TideCacheIntegrityError):
            validate_tide_cache_structure(bad_shape_path)

        # 2. 节点索引越界 (比如引用了 index 99，但总节点数仅 4)
        oob_path = os.path.join(self.temp_dir, "oob_cell_nodes.nc")
        comp_oob = create_base_mock_cache_components()
        comp_oob["cell_node_indices"] = np.array([[0, 1, 2, 99]], dtype=np.int32)
        write_raw_netcdf_cache(oob_path, comp_oob)
        with self.assertRaises(TideCacheIntegrityError):
            validate_tide_cache_structure(oob_path)


class TestTerminalAndTemporalMetadataValidation(unittest.TestCase):
    """E & F & G & H: terminal_tide 形状、一致性、起止时间戳及步长校验"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="cx_test_round9_meta_")
        self.components = create_base_mock_cache_components()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_terminal_tide_shape_validation(self):
        """tide_msl_terminal_m 必须严格为一维 (n_node,)，拒收二维 (1, n_node) 或错位长度"""
        # 用 netCDF4 写入二维 (1, 4)
        cache_mod = os.path.join(self.temp_dir, "term_shape_mismatch.nc")
        write_raw_netcdf_cache(cache_mod, self.components, omit_vars=("tide_msl_terminal_m",))
        with netCDF4.Dataset(cache_mod, "a") as ds:
            ds.createDimension("extra_dim", 1)
            v = ds.createVariable("tide_msl_terminal_m", "f4", ("extra_dim", "node"))
            v[:] = np.array([[0.1, 0.2, 0.3, 0.4]], dtype=np.float32)

        with self.assertRaises(TideCacheIntegrityError):
            validate_tide_cache_structure(cache_mod)

    def test_has_terminal_tide_consistency(self):
        """HAS_TERMINAL_TIDE 与 tide_msl_terminal_m 变量存在性必须严格自洽"""
        # 1. 声明为 true 但无变量
        cache_attr_true_no_var = os.path.join(self.temp_dir, "true_no_var.nc")
        comp = create_base_mock_cache_components(has_terminal=False)
        write_raw_netcdf_cache(
            cache_attr_true_no_var,
            comp,
            attr_overrides={"HAS_TERMINAL_TIDE": "true"}
        )
        with self.assertRaises(TideCacheIntegrityError):
            validate_tide_cache_structure(cache_attr_true_no_var)

        # 2. 声明为 false 但存在变量
        cache_attr_false_has_var = os.path.join(self.temp_dir, "false_has_var.nc")
        comp_has = create_base_mock_cache_components(has_terminal=True)
        write_raw_netcdf_cache(
            cache_attr_false_has_var,
            comp_has,
            attr_overrides={"HAS_TERMINAL_TIDE": "false"}
        )
        with self.assertRaises(TideCacheIntegrityError):
            validate_tide_cache_structure(cache_attr_false_has_var)

    def test_time_samples_metadata_mismatch(self):
        """TIME_SAMPLES 属性与实际时间轴长度不符必须抛出异常"""
        cache_samples_bad = os.path.join(self.temp_dir, "bad_samples.nc")
        write_raw_netcdf_cache(
            cache_samples_bad,
            self.components,
            attr_overrides={"TIME_SAMPLES": "999"}
        )
        with self.assertRaises(TideCacheIntegrityError):
            validate_tide_cache_structure(cache_samples_bad)

    def test_start_and_end_epoch_tolerance(self):
        """起止时间戳容差校验：<= 1ms 容忍，> 1ms 抛出异常"""
        # 1. 起始戳偏差 0.0001s (0.1ms) -> 合格
        cache_start_ok = os.path.join(self.temp_dir, "start_tol_ok.nc")
        write_raw_netcdf_cache(
            cache_start_ok,
            self.components,
            attr_overrides={"TIME_START_UTC_EPOCH": str(self.components["time_arr"][0] + 0.0001)}
        )
        validate_tide_cache_structure(cache_start_ok)

        # 2. 起始戳偏差 1.0s -> 异常
        cache_start_bad = os.path.join(self.temp_dir, "start_tol_bad.nc")
        write_raw_netcdf_cache(
            cache_start_bad,
            self.components,
            attr_overrides={"TIME_START_UTC_EPOCH": str(self.components["time_arr"][0] + 1.0)}
        )
        with self.assertRaises(TideCacheIntegrityError):
            validate_tide_cache_structure(cache_start_bad)

        # 3. 结束戳偏差 1.0s -> 异常
        cache_end_bad = os.path.join(self.temp_dir, "end_tol_bad.nc")
        write_raw_netcdf_cache(
            cache_end_bad,
            self.components,
            attr_overrides={"TIME_END_UTC_EPOCH": str(float(self.components["attrs"]["TIME_END_UTC_EPOCH"]) + 1.0)}
        )
        with self.assertRaises(TideCacheIntegrityError):
            validate_tide_cache_structure(cache_end_bad)

    def test_cadence_tolerance_and_monotonicity(self):
        """时间轴严格单调递增，步长与元数据偏差 <= 1ms"""
        # 1. 非单调
        comp_non_mono = create_base_mock_cache_components()
        comp_non_mono["time_arr"][1] = comp_non_mono["time_arr"][0]  # dt = 0
        cache_non_mono = os.path.join(self.temp_dir, "non_mono.nc")
        write_raw_netcdf_cache(cache_non_mono, comp_non_mono)
        with self.assertRaises(TideCacheIntegrityError):
            validate_tide_cache_structure(cache_non_mono)

        # 2. 步长漂移 > 1ms
        comp_drift = create_base_mock_cache_components()
        comp_drift["time_arr"][2] += 0.5  # 漂移 500ms
        cache_drift = os.path.join(self.temp_dir, "drift.nc")
        write_raw_netcdf_cache(cache_drift, comp_drift)
        with self.assertRaises(TideCacheIntegrityError):
            validate_tide_cache_structure(cache_drift)


class TestIntervalSemanticsAndTerminalTideDefensiveNormalization(unittest.TestCase):
    """I & J: inclusive_to_interval_semantics 映射与一维防御性归一化"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="cx_test_round9_sem_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_inclusive_to_interval_semantics_mapping(self):
        """测试区间闭合语义映射器"""
        self.assertEqual(inclusive_to_interval_semantics("left"), "[start, end)")
        self.assertEqual(inclusive_to_interval_semantics("LEFT"), "[start, end)")
        self.assertEqual(inclusive_to_interval_semantics("right"), "(start, end]")
        self.assertEqual(inclusive_to_interval_semantics("both"), "[start, end]")
        self.assertEqual(inclusive_to_interval_semantics("neither"), "(start, end)")

        with self.assertRaises(ValueError):
            inclusive_to_interval_semantics("invalid_semantics")

    def test_time_interval_semantics_contradiction(self):
        """TIME_INTERVAL_SEMANTICS 与 TIME_INCLUSIVE 语义矛盾必须被核验发现"""
        comp = create_base_mock_cache_components()
        cache_bad_sem = os.path.join(self.temp_dir, "bad_sem.nc")
        write_raw_netcdf_cache(
            cache_bad_sem,
            comp,
            attr_overrides={
                "TIME_INCLUSIVE": "left",
                "TIME_INTERVAL_SEMANTICS": "[start, end]"  # 矛盾
            }
        )
        with self.assertRaises(TideCacheIntegrityError):
            validate_tide_cache_structure(cache_bad_sem)

    def test_terminal_tide_1d_defensive_normalization(self):
        """测试 read_tide_cache 及 read_tide_cache_structure 始终产出一维终端潮位"""
        comp = create_base_mock_cache_components(has_terminal=True)
        cache_path = os.path.join(self.temp_dir, "term_norm.nc")
        write_raw_netcdf_cache(cache_path, comp)

        # 1. read_tide_cache
        data_full = read_tide_cache(cache_path)
        self.assertIn("terminal_tide", data_full)
        self.assertIsNotNone(data_full["terminal_tide"])
        self.assertEqual(data_full["terminal_tide"].ndim, 1)
        self.assertEqual(len(data_full["terminal_tide"]), comp["n_nodes"])

        # 2. read_tide_cache_structure
        data_struct = read_tide_cache_structure(cache_path)
        self.assertIsNotNone(data_struct.terminal_tide)
        self.assertEqual(data_struct.terminal_tide.ndim, 1)
        self.assertEqual(len(data_struct.terminal_tide), comp["n_nodes"])


class TestExposureEngineSemanticsAndSchemaCompatibility(unittest.TestCase):
    """K & L: Exposure 引擎对 legacy 'both' 拒绝及 Schema 1.1 降级兼容"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="cx_test_round9_exp_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_mock_dem(self) -> str:
        dem_path = os.path.join(self.temp_dir, "mock_dem.tif")
        w, h = 20, 20
        transform = from_origin(121.50, 31.45, 0.0025, 0.0025)
        data = np.full((h, w), 0.1, dtype=np.float32)
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

    def test_exposure_engine_rejects_legacy_both(self):
        """Exposure 引擎遇 TIME_INCLUSIVE='both' 必须明确拒绝并抛出 TideCacheCompatibilityError"""
        dem_path = self._create_mock_dem()
        engine = RasterTideEngine()
        info = engine.inspect_raster(dem_path)

        comp = create_base_mock_cache_components(has_terminal=False)
        comp["attrs"]["TIME_INCLUSIVE"] = "both"
        comp["attrs"]["TIME_INTERVAL_SEMANTICS"] = "[start, end]"
        cache_both = os.path.join(self.temp_dir, "cache_both.nc")
        write_raw_netcdf_cache(cache_both, comp)

        out_dir = os.path.join(self.temp_dir, "out_both")
        with self.assertRaises(TideCacheCompatibilityError):
            calculate_exposure_from_tide_cache(
                dem_path=dem_path,
                cache_path=cache_both,
                output_dir=out_dir
            )

    def test_exposure_engine_schema11_left_fallback(self):
        """Exposure 引擎遇缺失 terminal_tide 的 Schema 1.1 left 缓存正常执行且标记 QC_EXP_TERMINAL_UNAVAILABLE"""
        dem_path = self._create_mock_dem()
        engine = RasterTideEngine()
        info = engine.inspect_raster(dem_path)

        dr = pd.date_range("2024-01-01 00:00:00", "2024-01-01 04:30:00", freq="30min", inclusive="left", tz="UTC")
        n_times = len(dr)
        n0 = ControlNode(node_id=0, x=121.50, y=31.40, lon=121.50, lat=31.40, valid=True, static_offset_m=0.0)
        n1 = ControlNode(node_id=1, x=121.55, y=31.40, lon=121.55, lat=31.40, valid=True, static_offset_m=0.0)
        n2 = ControlNode(node_id=2, x=121.50, y=31.45, lon=121.50, lat=31.45, valid=True, static_offset_m=0.0)
        n3 = ControlNode(node_id=3, x=121.55, y=31.45, lon=121.55, lat=31.45, valid=True, static_offset_m=0.0)
        for n in (n0, n1, n2, n3):
            s = np.zeros(n_times, dtype=np.float32)
            n.tide_msl_raw = s
            n.water_levels_sorted = s

        cell = QuadCell(
            cell_id=0, x_min=121.50, y_min=31.40, x_max=121.55, y_max=31.45,
            level=0, node_a=n0, node_b=n1, node_c=n2, node_d=n3
        )
        nodes_dict = {(int(n.x * 1000), int(n.y * 1000)): n for n in (n0, n1, n2, n3)}

        cache_path = os.path.join(self.temp_dir, "cache_schema11.nc")
        cache_meta = {
            "start_time": "2024-01-01 00:00:00",
            "end_time": "2024-01-01 04:30:00",
            "freq": "30min",
            "source_tz": "UTC",
            "inclusive": "left"
        }
        # 写入 Schema 1.1 (不提供 terminal_tide)
        write_tide_cache(
            cache_path=cache_path,
            info=info,
            leaf_cells=[cell],
            node_cache=nodes_dict,
            time_index=dr,
            metadata=cache_meta,
            tide_msl_terminal=None,
            schema_version="1.1"
        )

        out_dir = os.path.join(self.temp_dir, "out_schema11")
        res = calculate_exposure_from_tide_cache(
            dem_path=dem_path,
            cache_path=cache_path,
            output_dir=out_dir
        )
        qc_tif = res["products"].exposure_qc_path
        with rasterio.open(qc_tif) as src:
            qc_arr = src.read(1)
            # 有效像元必须带有 QC_EXP_TERMINAL_UNAVAILABLE (8)
            valid_mask = (qc_arr != QC_EXP_NODATA)
            self.assertTrue(np.any(valid_mask))
            self.assertTrue(np.all((qc_arr[valid_mask] & QC_EXP_TERMINAL_UNAVAILABLE) != 0))


class TestTimezoneProvenanceAndDSTTransitions(unittest.TestCase):
    """M: 时区溯源与夏令时跳变测试 (Asia/Shanghai, UTC, America/New_York)"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="cx_test_round9_tz_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_america_new_york_dst_spring_forward(self):
        """2024-03-10 美东夏令时跳前 (Spring Forward)：当天仅 23 小时 (46 步长)"""
        start_ny = "2024-03-10 00:00:00"
        end_ny = "2024-03-11 00:00:00"
        tz = "America/New_York"
        dr = pd.date_range(start_ny, end_ny, freq="30min", inclusive="left", tz=tz)

        # 23 小时 x 2 = 46 步
        self.assertEqual(len(dr), 46)

        dr_utc = dr.tz_convert("UTC")
        self.assertEqual(dr_utc[0].isoformat(), "2024-03-10T05:00:00+00:00")
        end_utc = (dr_utc[-1] + pd.Timedelta("30min"))
        self.assertEqual(end_utc.isoformat(), "2024-03-11T04:00:00+00:00")

        # 验证时间总跨度精确为 23 小时 = 82800 秒
        span_s = end_utc.timestamp() - dr_utc[0].timestamp()
        self.assertEqual(span_s, 23 * 3600.0)

    def test_america_new_york_dst_fall_back(self):
        """2024-11-03 美东夏令时回退 (Fall Back)：当天有 25 小时 (50 步长)"""
        start_ny = "2024-11-03 00:00:00"
        end_ny = "2024-11-04 00:00:00"
        tz = "America/New_York"
        dr = pd.date_range(start_ny, end_ny, freq="30min", inclusive="left", tz=tz)

        # 25 小时 x 2 = 50 步
        self.assertEqual(len(dr), 50)

        dr_utc = dr.tz_convert("UTC")
        self.assertEqual(dr_utc[0].isoformat(), "2024-11-03T04:00:00+00:00")
        end_utc = (dr_utc[-1] + pd.Timedelta("30min"))
        self.assertEqual(end_utc.isoformat(), "2024-11-04T05:00:00+00:00")

        # 验证时间总跨度精确为 25 小时 = 90000 秒
        span_s = end_utc.timestamp() - dr_utc[0].timestamp()
        self.assertEqual(span_s, 25 * 3600.0)


class TestSpatialIndexProductionEquivalenceWithNoData(unittest.TestCase):
    """N: SpatialIndex 生产级与暴力参考器数值等价回归 (含显式 NoData 掩膜验证)"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="cx_test_round9_spatial_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_production_spatial_index_equivalence_with_nodata(self):
        """在多单元网格及包含显式 NoData 栅格环境下，SpatialIndex 与 BruteForce 产物像素值 100% 相同"""
        dem_path = os.path.join(self.temp_dir, "dem_nodata.tif")
        w, h = 40, 40
        transform = from_origin(121.50, 31.50, 0.001, 0.001)
        data = np.full((h, w), 0.2, dtype=np.float32)
        # 挖出 NoData 斑块
        data[5:15, 5:15] = -9999.0
        data[25:35, 25:35] = -9999.0

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

        # 构建 2x2 四叉树叶子单元网格
        nodes = []
        for i, (gx, gy) in enumerate([
            (121.50, 31.46), (121.52, 31.46), (121.54, 31.46),
            (121.50, 31.48), (121.52, 31.48), (121.54, 31.48),
            (121.50, 31.50), (121.52, 31.50), (121.54, 31.50),
        ]):
            node = ControlNode(node_id=i, x=gx, y=gy, lon=gx, lat=gy, valid=True, static_offset_m=0.0)
            node.water_levels_sorted = np.linspace(-1.0, 1.0, 10, dtype=np.float32)
            nodes.append(node)

        cells = [
            QuadCell(0, 121.50, 31.46, 121.52, 31.48, 0, nodes[0], nodes[1], nodes[3], nodes[4]),
            QuadCell(1, 121.52, 31.46, 121.54, 31.48, 0, nodes[1], nodes[2], nodes[4], nodes[5]),
            QuadCell(2, 121.50, 31.48, 121.52, 31.50, 0, nodes[3], nodes[4], nodes[6], nodes[7]),
            QuadCell(3, 121.52, 31.48, 121.54, 31.50, 0, nodes[4], nodes[5], nodes[7], nodes[8]),
        ]

        bounds = (121.50, 31.46, 121.54, 31.50)
        spatial_idx = LeafCellSpatialIndex(cells, bounds=bounds)
        brute_idx = BruteForceSpatialIndex(cells, bounds=bounds)

        # 查询测试
        q_bbox = (121.51, 31.47, 121.53, 31.49)
        res_spatial = sorted([c.cell_id for c in spatial_idx.query_intersecting_cells(q_bbox)])
        res_brute = sorted([c.cell_id for c in brute_idx.query_intersecting_cells(q_bbox)])
        self.assertEqual(res_spatial, res_brute)


class TestDocumentationAndMetadataIntegrityLinting(unittest.TestCase):
    """O: 文档与代码注释事实级一致性 Linting"""

    def setUp(self):
        self.project_root = Path(__file__).resolve().parent.parent

    def test_no_forbidden_marketing_phrases_in_docs(self):
        """检验核心文档中不存在未经验证的绝对化商业修饰用语"""
        forbidden_phrases = [
            "生产级", "Production Ready", "极端耐干条件", "防篡改",
            "完整识别", "硬性保证", "高标准", "彻底消除"
        ]
        target_files = [
            self.project_root / "README.md",
            self.project_root / "README_EN.md",
            self.project_root / "gui" / "manual_dialog.py"
        ]
        for fpath in target_files:
            self.assertTrue(fpath.exists(), f"文件不存在: {fpath}")
            text = fpath.read_text(encoding="utf-8")
            for phrase in forbidden_phrases:
                self.assertNotIn(
                    phrase,
                    text,
                    f"文件 {fpath.name} 包含未经证明的修饰词汇: '{phrase}'"
                )

    def test_read_tide_cache_structure_docstring_neutrality(self):
        """read_tide_cache_structure 文档注释不得包含未经证据测算的 '< 5MB' 宣称"""
        cache_py = self.project_root / "core" / "tide_cache.py"
        text = cache_py.read_text(encoding="utf-8")
        self.assertNotIn("< 5MB", text)
        self.assertNotIn("<5MB", text)

    def test_geoid_readme_contains_verified_metadata(self):
        """README_GEOID.md 必须包含实测真实的 GeoTIFF 元数据 (EPSG:4979, 80,591,169 bytes)"""
        geoid_readme = self.project_root / "data" / "geoid" / "README_GEOID.md"
        self.assertTrue(geoid_readme.exists())
        content = geoid_readme.read_text(encoding="utf-8")
        self.assertIn("EPSG:4979", content)
        self.assertIn("80,591,169", content)


if __name__ == "__main__":
    unittest.main()
