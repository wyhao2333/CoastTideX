"""
CoastTideX 控制网格潮汐缓存模块 (Tide Cache Module v1.5 Alpha Hardened)
支持自适应四叉树控制节点潮位时序的高效 NetCDF4 序列化、流式压缩存储、完整性校验、
全要素兼容性签名 (Compatibility Signature) 与轻量元数据检视，以及
基于 Tide Cache 的二阶段天文潮潜在淹没频率解算 (零 FES 重复调用)。
"""

import os
import json
import time
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any, Callable, Set
from collections import defaultdict

import numpy as np
import pandas as pd
import netCDF4
import rasterio
import rasterio.windows
from rasterio.windows import Window

from .raster_engine import (
    RasterInfo, ControlNode, QuadCell, RasterResultSummary,
    QC_BIT_VALID, QC_BIT_FES_EXTRAPOLATED, QC_BIT_SPATIAL_FALLBACK,
    QC_BIT_INSUFFICIENT_NODES, QC_BIT_DATUM_INVALID, QC_BIT_DATUM_SOURCE_APPROX,
    QC_BIT_MIN_SPACING_REACHED, QC_BIT_CONNECTIVITY_FALLBACK,
    QC_BIT_FES_VALIDITY_BOUNDARY, QC_BIT_MAX_REFINEMENT_REACHED,
    QC_NODATA, RasterCalculationCancelled,
    build_support_topology, stream_inundation_frequency_interpolation
)
from .utils import compute_inundation_frequency

COASTTIDEX_VERSION = "1.5-alpha"
CACHE_SCHEMA_VERSION = "1.1"
CACHE_SIGNATURE_ALGORITHM = "sha256"


class ExistingOutputError(FileExistsError):
    """目标正式产物已存在且当前策略不允许覆盖时抛出"""
    pass


class TideCacheCompatibilityError(ValueError):
    """Tide Cache 与目标 DEM 或解算参数不兼容异常"""
    pass


def estimate_tide_cache_size(node_count: int, time_samples: int) -> Dict[str, Any]:
    """
    估算 Tide Cache 控制节点潮位数组原始内存与未压缩数据量 (Raw Array Estimate)。
    """
    raw_bytes = int(node_count) * int(time_samples) * 4  # float32 = 4 bytes
    raw_mb = raw_bytes / (1024.0 * 1024.0)

    if raw_mb < 1.0:
        fmt = f"{raw_bytes / 1024.0:.1f} KB"
    elif raw_mb < 1024.0:
        fmt = f"{raw_mb:.2f} MB"
    else:
        fmt = f"{raw_mb / 1024.0:.2f} GB"

    return {
        "node_count": int(node_count),
        "time_samples": int(time_samples),
        "raw_bytes": raw_bytes,
        "raw_mb": raw_mb,
        "formatted_size": fmt,
        "note": "此估算为 float32 密集矩阵未压缩原始大小，实际 NetCDF 采用 zlib 压缩后通常为该数值的 20%~50%"
    }


def is_cache_complete(cache_path: str) -> bool:
    """
    检查指定的 Tide Cache NetCDF 文件是否存在且写入完整 (带有 CACHE_COMPLETE=true 标记)。
    """
    if not os.path.exists(cache_path):
        return False
    try:
        with netCDF4.Dataset(cache_path, mode="r") as ds:
            complete_attr = getattr(ds, "CACHE_COMPLETE", None)
            return str(complete_attr).lower() == "true"
    except Exception:
        return False


def generate_tide_cache_signature(
    info: Optional[RasterInfo] = None,
    start_time: str = "",
    end_time: str = "",
    freq: str = "",
    source_tz: str = "UTC",
    inclusive: str = "both",
    time_samples: int = 0,
    dem_datum: str = "egm2008",
    constituents: str = "all",
    target_mode: str = "intertidal",
    initial_control_spacing_m: float = 4000.0,
    min_control_spacing_m: float = 500.0,
    inundation_error_tolerance_pct: float = 1.0,
    fes_model: str = "FES2022b",
    fes_source_type: str = "native_lgp2",
    topology_max_resolution_m: float = 200.0,
    source_width: Optional[int] = None,
    source_height: Optional[int] = None,
    source_crs: Optional[str] = None,
    source_transform = None,
    source_bounds = None,
    source_resolution = None,
    source_nodata = None,
    **kwargs
) -> Tuple[str, str]:
    """
    生成确定性的 Tide Cache 兼容性规范签名 (SHA256)。
    支持通过 RasterInfo 对象或直接通过字典关键字参数生成。
    """
    if info is not None:
        w_val = int(info.width)
        h_val = int(info.height)
        c_val = str(info.crs).strip()
        t_vals = [round(float(v), 8) for v in list(info.transform)[:6]]
        b_vals = [round(float(v), 5) for v in info.bounds]
        nodata_val = float(info.nodata) if info.nodata is not None and np.isfinite(info.nodata) else None
    else:
        w_val = int(source_width or 0)
        h_val = int(source_height or 0)
        c_val = str(source_crs or "").strip()
        raw_t = source_transform if source_transform is not None else []
        t_vals = [round(float(v), 8) for v in list(raw_t)[:6]]
        raw_b = source_bounds if source_bounds is not None else []
        b_vals = [round(float(v), 5) for v in list(raw_b)[:4]]
        nodata_val = float(source_nodata) if source_nodata is not None and np.isfinite(source_nodata) else None

    canonical_dict = {
        "source": {
            "width": w_val,
            "height": h_val,
            "crs": c_val,
            "transform": t_vals,
            "bounds": b_vals,
            "nodata": nodata_val
        },
        "time": {
            "start_time": str(start_time),
            "end_time": str(end_time),
            "freq": str(freq),
            "source_tz": str(source_tz),
            "inclusive": str(inclusive),
            "time_samples": int(time_samples)
        },
        "fes": {
            "model": str(fes_model),
            "source_type": str(fes_source_type),
            "constituents": str(constituents)
        },
        "datum": {
            "dem_datum": str(dem_datum).lower()
        },
        "algorithm": {
            "target_mode": str(target_mode).lower(),
            "initial_control_spacing_m": round(float(initial_control_spacing_m), 2),
            "min_control_spacing_m": round(float(min_control_spacing_m), 2),
            "inundation_error_tolerance_pct": round(float(inundation_error_tolerance_pct), 3),
            "topology_max_resolution_m": round(float(topology_max_resolution_m), 2)
        },
        "cache": {
            "schema_version": CACHE_SCHEMA_VERSION,
            "signature_algorithm": CACHE_SIGNATURE_ALGORITHM
        }
    }

    json_str = json.dumps(canonical_dict, sort_keys=True, separators=(',', ':'))
    sig = hashlib.sha256(json_str.encode("utf-8")).hexdigest()
    return sig, json_str


def inspect_tide_cache_metadata(cache_path: str) -> Dict[str, Any]:
    """
    轻量读取 Tide Cache NetCDF 全局属性、维度与签名，严禁读取 tide_msl_m 大矩阵。
    """
    if not os.path.exists(cache_path):
        raise FileNotFoundError(f"未找到指定的 Tide Cache 文件: {cache_path}")

    with netCDF4.Dataset(cache_path, mode="r") as ds:
        attrs = {attr: getattr(ds, attr) for attr in ds.ncattrs()}

        num_nodes = len(ds.dimensions["node"]) if "node" in ds.dimensions else int(attrs.get("CONTROL_NODE_COUNT", 0))
        time_samples = len(ds.dimensions["time"]) if "time" in ds.dimensions else int(attrs.get("TIME_SAMPLES", 0))
        num_cells = len(ds.dimensions["cell"]) if "cell" in ds.dimensions else int(attrs.get("LEAF_CELL_COUNT", 0))

        is_complete = str(attrs.get("CACHE_COMPLETE", "false")).lower() == "true"
        signature = str(attrs.get("CACHE_SIGNATURE", ""))

        return {
            "metadata": attrs,
            "num_nodes": num_nodes,
            "num_cells": num_cells,
            "time_samples": time_samples,
            "is_complete": is_complete,
            "signature": signature,
            "cache_path": str(cache_path)
        }


def validate_tide_cache_compatibility(
    cache_path: str,
    expected_spec: Dict[str, Any]
) -> Tuple[bool, List[str]]:
    """
    验证已有 Tide Cache NetCDF 文件与当前 DEM 空间规格及计算参数是否严格兼容。
    """
    if not os.path.exists(cache_path):
        return False, [f"Tide Cache 文件不存在: {cache_path}"]

    try:
        info = inspect_tide_cache_metadata(cache_path)
    except Exception as ex:
        return False, [f"无法读取 Tide Cache 元数据: {str(ex)}"]

    if not info["is_complete"]:
        return False, ["Tide Cache 未完整写入 (缺少 CACHE_COMPLETE=true 标记)"]

    attrs = info["metadata"]
    reasons: List[str] = []

    # 1. 栅格尺寸校验 (Width / Height)
    if "width" in expected_spec:
        exp_w = int(expected_spec["width"])
        c_w = int(attrs.get("SOURCE_WIDTH", 0))
        if c_w != exp_w:
            reasons.append(f"栅格宽度不匹配: Cache 为 {c_w} 像元，当前 DEM 为 {exp_w} 像元")

    if "height" in expected_spec:
        exp_h = int(expected_spec["height"])
        c_h = int(attrs.get("SOURCE_HEIGHT", 0))
        if c_h != exp_h:
            reasons.append(f"栅格高度不匹配: Cache 为 {c_h} 像元，当前 DEM 为 {exp_h} 像元")

    # 2. 坐标参考系 (CRS) 校验
    if "crs" in expected_spec:
        exp_crs = str(expected_spec["crs"]).strip()
        c_crs = str(attrs.get("SOURCE_CRS", "")).strip()
        if exp_crs and c_crs and exp_crs != c_crs:
            exp_epsg = exp_crs.split(":")[-1] if "EPSG" in exp_crs.upper() else ""
            c_epsg = c_crs.split(":")[-1] if "EPSG" in c_crs.upper() else ""
            if not (exp_epsg and c_epsg and exp_epsg == c_epsg):
                reasons.append(f"坐标系统 (CRS) 不匹配: Cache 为 '{c_crs}'，当前 DEM 为 '{exp_crs}'")

    # 3. 仿射变换矩阵 (Transform) 校验
    if "transform" in expected_spec:
        exp_t = list(expected_spec["transform"])[:6]
        c_t_raw = attrs.get("SOURCE_TRANSFORM")
        if c_t_raw is not None:
            if isinstance(c_t_raw, str):
                try:
                    c_t = json.loads(c_t_raw.replace("'", '"'))
                except Exception:
                    try:
                        c_t = [float(x.strip(" []")) for x in c_t_raw.split(",")[:6]]
                    except Exception:
                        c_t = []
            else:
                c_t = list(c_t_raw)[:6]

            if len(c_t) == 6 and len(exp_t) == 6:
                diffs = [abs(float(a) - float(b)) for a, b in zip(c_t, exp_t)]
                if any(d > 1e-4 for d in diffs):
                    reasons.append(f"仿射变换 (Transform) 不匹配: Cache 为 {c_t}，当前 DEM 为 {exp_t}")

    # 4. 高程基准面校验
    if "dem_datum" in expected_spec:
        exp_datum = str(expected_spec["dem_datum"]).strip().lower()
        c_datum = str(attrs.get("DEM_DATUM", "")).strip().lower()
        if exp_datum and c_datum and exp_datum != c_datum:
            reasons.append(f"高程基准面不匹配: Cache 为 '{c_datum}'，当前请求为 '{exp_datum}'")

    # 5. 时间采样点数与步长校验
    if "time_samples" in expected_spec:
        exp_samples = int(expected_spec["time_samples"])
        c_samples = int(info["time_samples"])
        if c_samples != exp_samples:
            reasons.append(f"时间样本点数不匹配: Cache 为 {c_samples} 点，当前请求为 {exp_samples} 点")

    if "freq" in expected_spec:
        exp_freq = str(expected_spec["freq"]).strip()
        c_freq = str(attrs.get("TIME_STEP", "")).strip()
        if exp_freq and c_freq and exp_freq != c_freq:
            reasons.append(f"采样步长不匹配: Cache 为 '{c_freq}'，当前请求为 '{exp_freq}'")

    if "target_mode" in expected_spec:
        exp_mode = str(expected_spec["target_mode"]).strip().lower()
        c_mode = str(attrs.get("TARGET_MODE", "")).strip().lower()
        if exp_mode and c_mode and exp_mode != c_mode:
            reasons.append(f"目标区域模式不匹配: Cache 为 '{c_mode}'，当前请求为 '{exp_mode}'")

    if "constituents" in expected_spec:
        exp_const = str(expected_spec["constituents"]).strip().lower()
        c_const = str(attrs.get("CONSTITUENTS", "")).strip().lower()
        if exp_const and c_const and exp_const != c_const:
            reasons.append(f"分潮集合不匹配: Cache 为 '{c_const}'，当前请求为 '{exp_const}'")

    # 6. 签名自校验与篡改防御 (Tamper-evidence Verification)
    stored_sig = str(attrs.get("CACHE_SIGNATURE", "")).strip()
    if stored_sig:
        try:
            expected_c_sig, _ = generate_tide_cache_signature(
                source_path=attrs.get("SOURCE_DEM_PATH", ""),
                source_width=int(attrs.get("SOURCE_WIDTH", 0)),
                source_height=int(attrs.get("SOURCE_HEIGHT", 0)),
                source_crs=attrs.get("SOURCE_CRS", ""),
                source_transform=json.loads(attrs.get("SOURCE_TRANSFORM", "[]")) if isinstance(attrs.get("SOURCE_TRANSFORM"), str) else attrs.get("SOURCE_TRANSFORM", []),
                source_bounds=json.loads(attrs.get("SOURCE_BOUNDS", "[]")) if isinstance(attrs.get("SOURCE_BOUNDS"), str) else attrs.get("SOURCE_BOUNDS", []),
                source_resolution=json.loads(attrs.get("SOURCE_RESOLUTION", "[]")) if isinstance(attrs.get("SOURCE_RESOLUTION"), str) else attrs.get("SOURCE_RESOLUTION", []),
                source_nodata=float(attrs.get("SOURCE_NODATA")) if attrs.get("SOURCE_NODATA") is not None and str(attrs.get("SOURCE_NODATA")).lower() != "nan" else np.nan,
                start_time=attrs.get("TIME_START", ""),
                end_time=attrs.get("TIME_END", ""),
                freq=attrs.get("TIME_STEP", ""),
                source_tz=attrs.get("TIMEZONE", "UTC"),
                inclusive=attrs.get("TIME_INCLUSIVE", "both"),
                time_samples=int(attrs.get("TIME_SAMPLES", info["time_samples"])),
                fes_model=attrs.get("TIDE_MODEL", "FES2022b"),
                fes_source_type=attrs.get("FES_SOURCE_TYPE", "native_mesh"),
                constituents=attrs.get("CONSTITUENTS", "all"),
                dem_datum=attrs.get("DEM_DATUM", "egm2008"),
                target_mode=attrs.get("TARGET_MODE", "intertidal"),
                initial_control_spacing_m=float(attrs.get("INITIAL_CONTROL_SPACING_M", 4000.0)),
                min_control_spacing_m=float(attrs.get("MIN_CONTROL_SPACING_M", 500.0)),
                inundation_error_tolerance_pct=float(attrs.get("ERROR_TOLERANCE_PCT", 1.0)),
                topology_max_resolution_m=float(attrs.get("TOPOLOGY_RESOLUTION_M", 200.0))
            )
            if stored_sig != expected_c_sig:
                reasons.append(f"Tide Cache 元数据已被篡改或损坏 (签名不一致: {stored_sig[:12]}... != {expected_c_sig[:12]}...)")
        except Exception as ex:
            reasons.append(f"Tide Cache 签名校验异常: {str(ex)}")

    if "signature" in expected_spec:
        exp_sig = str(expected_spec["signature"]).strip()
        c_sig = info["signature"].strip()
        if exp_sig and c_sig and exp_sig != c_sig:
            reasons.append(f"全要素规范签名不匹配: Cache 为 '{c_sig[:16]}...'，当前规格为 '{exp_sig[:16]}...'")

    return len(reasons) == 0, reasons


def write_tide_cache(
    cache_path: str,
    info: RasterInfo,
    leaf_cells: List[QuadCell],
    node_cache: Dict[Tuple[int, int], ControlNode],
    time_index: pd.DatetimeIndex,
    metadata: Dict[str, Any],
    allow_overwrite: bool = True,
    cancel_event = None
) -> str:
    """
    将自适应控制网格及其节点潮位时序原子级写入 NetCDF4 Tide Cache (*_tide.nc)。
    """
    if cancel_event is not None and cancel_event.is_set():
        raise RasterCalculationCancelled("用户取消了 Tide Cache 写入。")

    if os.path.exists(cache_path) and not allow_overwrite:
        raise ExistingOutputError(f"Tide Cache 文件已存在且未开启覆盖权限 (OVERWRITE): {cache_path}")

    out_dir = os.path.dirname(os.path.abspath(cache_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    tmp_cache_path = f"{cache_path}.tmp.nc"
    if os.path.exists(tmp_cache_path):
        try:
            os.remove(tmp_cache_path)
        except Exception:
            pass

    # 规范化节点编号并建立映射
    all_nodes: List[ControlNode] = list(node_cache.values())
    node_id_map: Dict[int, int] = {node.node_id: idx for idx, node in enumerate(all_nodes)}

    num_nodes = len(all_nodes)
    num_times = len(time_index)
    num_cells = len(leaf_cells)

    # 计算兼容性签名 (Signature)
    sig_hex, sig_payload = generate_tide_cache_signature(
        info=info,
        start_time=str(metadata.get("start_time", time_index[0].isoformat())),
        end_time=str(metadata.get("end_time", time_index[-1].isoformat())),
        freq=str(metadata.get("freq", "30min")),
        source_tz=str(metadata.get("source_tz", "UTC")),
        inclusive=str(metadata.get("inclusive", "left")),
        time_samples=num_times,
        dem_datum=str(metadata.get("dem_datum", "egm2008")),
        constituents=str(metadata.get("constituents", "all")),
        target_mode=str(metadata.get("target_mode", "intertidal")),
        initial_control_spacing_m=float(metadata.get("initial_control_spacing_m", 4000.0)),
        min_control_spacing_m=float(metadata.get("min_control_spacing_m", 500.0)),
        inundation_error_tolerance_pct=float(metadata.get("inundation_error_tolerance_pct", 1.0)),
        fes_model="FES2022b",
        fes_source_type="native_lgp2",
        topology_max_resolution_m=float(metadata.get("topology_max_resolution_m", 200.0))
    )

    time_epochs = (time_index.astype("int64") // 10**9).to_numpy(dtype=np.float64)

    try:
        with netCDF4.Dataset(tmp_cache_path, mode="w", format="NETCDF4") as ds:
            ds.createDimension("node", num_nodes)
            ds.createDimension("time", num_times)
            ds.createDimension("cell", num_cells)
            ds.createDimension("bounds_dim", 4)
            ds.createDimension("corners_dim", 4)

            ds.setncattr("COASTTIDEX_VERSION", COASTTIDEX_VERSION)
            ds.setncattr("CACHE_SCHEMA_VERSION", CACHE_SCHEMA_VERSION)
            ds.setncattr("CACHE_SIGNATURE", sig_hex)
            ds.setncattr("CACHE_SIGNATURE_ALGORITHM", CACHE_SIGNATURE_ALGORITHM)
            ds.setncattr("CACHE_SIGNATURE_PAYLOAD", sig_payload)
            ds.setncattr("SOURCE_RASTER_NAME", os.path.basename(info.path))
            ds.setncattr("SOURCE_RASTER_PATH", str(info.path))
            ds.setncattr("SOURCE_WIDTH", int(info.width))
            ds.setncattr("SOURCE_HEIGHT", int(info.height))
            ds.setncattr("SOURCE_CRS", str(info.crs))
            ds.setncattr("SOURCE_TRANSFORM", json.dumps([round(float(v), 8) for v in list(info.transform)[:6]]))
            ds.setncattr("SOURCE_BOUNDS", json.dumps([round(float(v), 5) for v in info.bounds]))
            ds.setncattr("SOURCE_NODATA", float(info.nodata) if info.nodata is not None and np.isfinite(info.nodata) else np.nan)
            ds.setncattr("TIME_START", str(metadata.get("start_time", time_index[0].isoformat())))
            ds.setncattr("TIME_END", str(metadata.get("end_time", time_index[-1].isoformat())))
            ds.setncattr("TIME_STEP", str(metadata.get("freq", "30min")))
            ds.setncattr("TIMEZONE", str(metadata.get("source_tz", "UTC")))
            ds.setncattr("TIME_INCLUSIVE", str(metadata.get("inclusive", "left")))
            ds.setncattr("TIME_SAMPLES", int(num_times))
            ds.setncattr("FES_MODEL", "FES2022b")
            ds.setncattr("FES_SOURCE_TYPE", "native_lgp2")
            ds.setncattr("CONSTITUENTS", str(metadata.get("constituents", "all")))
            ds.setncattr("DEM_DATUM", str(metadata.get("dem_datum", "egm2008")))
            ds.setncattr("INITIAL_CONTROL_SPACING_M", float(metadata.get("initial_control_spacing_m", 4000.0)))
            ds.setncattr("MIN_CONTROL_SPACING_M", float(metadata.get("min_control_spacing_m", 500.0)))
            ds.setncattr("ERROR_TOLERANCE_PCT", float(metadata.get("inundation_error_tolerance_pct", 1.0)))
            ds.setncattr("TARGET_MODE", str(metadata.get("target_mode", "intertidal")))
            ds.setncattr("CONTROL_NODE_COUNT", int(num_nodes))
            ds.setncattr("ACTIVE_CONTROL_NODE_COUNT", int(sum(1 for n in all_nodes if n.valid)))
            ds.setncattr("LEAF_CELL_COUNT", int(num_cells))
            ds.setncattr("CREATED_AT", datetime.now(timezone.utc).isoformat())

            var_time = ds.createVariable("time", "f8", ("time",))
            var_time.units = "seconds since 1970-01-01 00:00:00 UTC"
            var_time.calendar = "proleptic_gregorian"
            var_time[:] = time_epochs

            var_node_x = ds.createVariable("node_x", "f8", ("node",))
            var_node_y = ds.createVariable("node_y", "f8", ("node",))
            var_node_lon = ds.createVariable("node_lon", "f8", ("node",))
            var_node_lat = ds.createVariable("node_lat", "f8", ("node",))
            var_node_valid = ds.createVariable("node_valid", "i1", ("node",))
            var_static_offset = ds.createVariable("static_offset_m", "f4", ("node",))
            var_comp_id = ds.createVariable("component_id", "i4", ("node",))
            var_node_qc = ds.createVariable("node_qc", "i4", ("node",))

            node_x_arr = np.array([n.x for n in all_nodes], dtype=np.float64)
            node_y_arr = np.array([n.y for n in all_nodes], dtype=np.float64)
            node_lon_arr = np.array([n.lon for n in all_nodes], dtype=np.float64)
            node_lat_arr = np.array([n.lat for n in all_nodes], dtype=np.float64)
            node_val_arr = np.array([1 if n.valid else 0 for n in all_nodes], dtype=np.int8)
            node_off_arr = np.array([n.static_offset_m for n in all_nodes], dtype=np.float32)
            node_cid_arr = np.array([n.component_id for n in all_nodes], dtype=np.int32)
            node_qc_arr = np.array([n.qc_bitmask for n in all_nodes], dtype=np.int32)

            var_node_x[:] = node_x_arr
            var_node_y[:] = node_y_arr
            var_node_lon[:] = node_lon_arr
            var_node_lat[:] = node_lat_arr
            var_node_valid[:] = node_val_arr
            var_static_offset[:] = node_off_arr
            var_comp_id[:] = node_cid_arr
            var_node_qc[:] = node_qc_arr

            chunk_nodes = min(500, max(1, num_nodes))
            chunk_times = min(1000, max(1, num_times))
            var_tide = ds.createVariable(
                "tide_msl_m", "f4", ("node", "time"),
                chunksizes=(chunk_nodes, chunk_times),
                zlib=True, complevel=4, shuffle=True,
                fill_value=np.nan
            )
            var_tide.units = "metres"
            var_tide.long_name = "Tide elevation relative to Mean Sea Level (MSL)"

            tide_block = np.full((chunk_nodes, num_times), np.nan, dtype=np.float32)
            block_fill = 0
            block_start_idx = 0

            for idx, node in enumerate(all_nodes):
                if cancel_event is not None and cancel_event.is_set():
                    raise RasterCalculationCancelled("用户取消了 Tide Cache 写入。")

                if getattr(node, "tide_msl_raw", None) is not None and len(node.tide_msl_raw) == num_times:
                    tide_block[block_fill, :] = node.tide_msl_raw
                elif node.valid and len(node.water_levels_sorted) == num_times:
                    tide_block[block_fill, :] = (node.water_levels_sorted - node.static_offset_m)
                else:
                    tide_block[block_fill, :] = np.nan

                block_fill += 1

                if block_fill == chunk_nodes or idx == num_nodes - 1:
                    var_tide[block_start_idx:block_start_idx + block_fill, :] = tide_block[:block_fill, :]
                    block_start_idx = idx + 1
                    block_fill = 0

            var_cell_bounds = ds.createVariable("cell_bounds", "f8", ("cell", "bounds_dim"))
            var_cell_bounds.description = "Bounding box: [x_min, y_min, x_max, y_max]"

            var_cell_nodes = ds.createVariable("cell_node_indices", "i4", ("cell", "corners_dim"))
            var_cell_nodes.description = "Corner node indices: [node_a, node_b, node_c, node_d]"

            var_cell_lvl = ds.createVariable("cell_level", "i4", ("cell",))
            var_cell_qc = ds.createVariable("cell_qc", "i4", ("cell",))
            var_cell_err = ds.createVariable("cell_max_error", "f4", ("cell",))

            cell_bounds_mat = np.zeros((num_cells, 4), dtype=np.float64)
            cell_nodes_mat = np.zeros((num_cells, 4), dtype=np.int32)
            cell_lvl_arr = np.zeros(num_cells, dtype=np.int32)
            cell_qc_arr = np.zeros(num_cells, dtype=np.int32)
            cell_err_arr = np.zeros(num_cells, dtype=np.float32)

            for c_i, cell in enumerate(leaf_cells):
                cell_bounds_mat[c_i] = [cell.x_min, cell.y_min, cell.x_max, cell.y_max]
                na_idx = node_id_map.get(cell.node_a.node_id, 0)
                nb_idx = node_id_map.get(cell.node_b.node_id, 0)
                nc_idx = node_id_map.get(cell.node_c.node_id, 0)
                nd_idx = node_id_map.get(cell.node_d.node_id, 0)
                cell_nodes_mat[c_i] = [na_idx, nb_idx, nc_idx, nd_idx]
                cell_lvl_arr[c_i] = cell.level
                cell_err_arr[c_i] = getattr(cell, "max_error_pct", 0.0)

                c_qc = 0
                if getattr(cell, "qc_min_spacing_reached", False):
                    c_qc |= QC_BIT_MIN_SPACING_REACHED
                if getattr(cell, "qc_max_refinement_reached", False):
                    c_qc |= QC_BIT_MAX_REFINEMENT_REACHED
                if getattr(cell, "qc_validity_boundary", False):
                    c_qc |= QC_BIT_FES_VALIDITY_BOUNDARY
                cell_qc_arr[c_i] = c_qc

            var_cell_bounds[:] = cell_bounds_mat
            var_cell_nodes[:] = cell_nodes_mat
            var_cell_lvl[:] = cell_lvl_arr
            var_cell_qc[:] = cell_qc_arr
            var_cell_err[:] = cell_err_arr

            ds.setncattr("CACHE_COMPLETE", "true")

        os.replace(tmp_cache_path, cache_path)
        return cache_path

    except Exception:
        if os.path.exists(tmp_cache_path):
            try:
                os.remove(tmp_cache_path)
            except Exception:
                pass
        raise


def read_tide_cache(cache_path: str) -> Dict[str, Any]:
    """
    读取 Tide Cache NetCDF 文件并重建自适应控制节点与叶单元拓扑。
    """
    if not os.path.exists(cache_path):
        raise FileNotFoundError(f"未找到指定的 Tide Cache 文件: {cache_path}")

    with netCDF4.Dataset(cache_path, mode="r") as ds:
        is_complete = str(getattr(ds, "CACHE_COMPLETE", "false")).lower() == "true"
        if not is_complete:
            raise ValueError(f"Tide Cache 文件未完整写入 (缺少 CACHE_COMPLETE 标记): {cache_path}")

        attrs = {attr: getattr(ds, attr) for attr in ds.ncattrs()}

        node_x = ds.variables["node_x"][:]
        node_y = ds.variables["node_y"][:]
        node_lon = ds.variables["node_lon"][:]
        node_lat = ds.variables["node_lat"][:]
        node_val = ds.variables["node_valid"][:]
        static_offset = ds.variables["static_offset_m"][:]
        comp_id = ds.variables["component_id"][:]
        node_qc = ds.variables["node_qc"][:]

        num_nodes = len(node_x)
        nodes: List[ControlNode] = []

        tide_mat = ds.variables["tide_msl_m"][:]  # (node, time)

        for i in range(num_nodes):
            is_valid = bool(node_val[i])
            off_m = float(static_offset[i])

            if is_valid:
                raw_t = np.array(tide_mat[i], dtype=np.float32)
                valid_t = raw_t[np.isfinite(raw_t)]
                if len(valid_t) > 0:
                    w_sorted = valid_t + off_m
                    w_sorted.sort()
                else:
                    w_sorted = np.array([], dtype=np.float32)
                    is_valid = False
            else:
                w_sorted = np.array([], dtype=np.float32)

            node = ControlNode(
                node_id=i,
                x=float(node_x[i]),
                y=float(node_y[i]),
                lon=float(node_lon[i]),
                lat=float(node_lat[i]),
                water_levels_sorted=w_sorted,
                valid=is_valid,
                static_offset_m=off_m,
                component_id=int(comp_id[i]),
                qc_bitmask=int(node_qc[i]),
                qc_code=int(node_qc[i])
            )
            nodes.append(node)

        cell_bounds = ds.variables["cell_bounds"][:]
        cell_nodes = ds.variables["cell_node_indices"][:]
        cell_lvl = ds.variables["cell_level"][:]
        cell_qc = ds.variables["cell_qc"][:]
        cell_err = ds.variables["cell_max_error"][:]

        num_cells = len(cell_lvl)
        leaf_cells: List[QuadCell] = []

        for c in range(num_cells):
            na = nodes[int(cell_nodes[c, 0])]
            nb = nodes[int(cell_nodes[c, 1])]
            nc = nodes[int(cell_nodes[c, 2])]
            nd = nodes[int(cell_nodes[c, 3])]
            qc_c = int(cell_qc[c])

            cell = QuadCell(
                cell_id=c,
                x_min=float(cell_bounds[c, 0]),
                y_min=float(cell_bounds[c, 1]),
                x_max=float(cell_bounds[c, 2]),
                y_max=float(cell_bounds[c, 3]),
                level=int(cell_lvl[c]),
                node_a=na,
                node_b=nb,
                node_c=nc,
                node_d=nd,
                qc_min_spacing_reached=bool(qc_c & QC_BIT_MIN_SPACING_REACHED),
                qc_max_refinement_reached=bool(qc_c & QC_BIT_MAX_REFINEMENT_REACHED),
                qc_validity_boundary=bool(qc_c & QC_BIT_FES_VALIDITY_BOUNDARY),
                max_error_pct=float(cell_err[c])
            )
            leaf_cells.append(cell)

        time_epochs = ds.variables["time"][:]
        time_index = pd.to_datetime(time_epochs, unit="s", utc=True)

        return {
            "metadata": attrs,
            "nodes": nodes,
            "leaf_cells": leaf_cells,
            "time_index": time_index,
            "num_nodes": num_nodes,
            "num_cells": num_cells,
            "time_samples": len(time_epochs)
        }


def calculate_inundation_from_tide_cache(
    dem_path: str,
    cache_path: str,
    output_path: str,
    qc_output_path: Optional[str] = None,
    block_size: Optional[int] = 512,
    allow_overwrite: bool = True,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    cancel_event = None
) -> RasterResultSummary:
    """
    【Stage 2 核心解算器】基于已有 Tide Cache NetCDF 与原始 DEM 解算潜在天文潮淹没频率 GeoTIFF。
    """
    t_start = time.time()
    if progress_callback:
        progress_callback(0, f"正在验证 Tide Cache 兼容性: {os.path.basename(cache_path)}...")

    from .raster_engine import RasterTideEngine
    tmp_engine = RasterTideEngine()
    info = tmp_engine.inspect_raster(dem_path, compute_valid_count=False)

    expected_spec = {
        "width": info.width,
        "height": info.height,
        "crs": info.crs,
        "transform": list(info.transform)[:6]
    }
    compatible, reasons = validate_tide_cache_compatibility(cache_path, expected_spec)
    if not compatible:
        raise TideCacheCompatibilityError(
            f"Tide Cache 与目标 DEM 不兼容，无法执行 Stage 2 解算: {'; '.join(reasons)}"
        )

    if progress_callback:
        progress_callback(5, "Tide Cache 兼容性通过，正在重建控制网格拓扑...")

    cache_data = read_tide_cache(cache_path)
    leaf_cells: List[QuadCell] = cache_data["leaf_cells"]
    nodes: List[ControlNode] = cache_data["nodes"]
    meta: Dict[str, Any] = cache_data["metadata"]

    if qc_output_path is None:
        base, ext = os.path.splitext(output_path)
        qc_output_path = f"{base}_qc{ext}"

    if block_size is None:
        block_size = 512

    top_res_m = float(meta.get("TOPOLOGY_RESOLUTION_M", 200.0))
    labeled_coarse, num_features, downsample_factor, h_coarse, w_coarse, _, input_valid_count = build_support_topology(
        info=info,
        topology_max_resolution_m=top_res_m,
        topology_valid_fraction_threshold=0.20,
        block_size=block_size,
        cancel_event=cancel_event
    )

    provenance_tags = {
        "COASTTIDEX_VERSION": COASTTIDEX_VERSION,
        "DATA_PRODUCT": "Potential Astronomical Tidal Inundation Frequency",
        "DEFINITION": "P(H(t) > z) under fixed representative terrain",
        "SOURCE_DEM": os.path.basename(dem_path),
        "SOURCE_TIDE_CACHE": os.path.basename(cache_path),
        "CACHE_SIGNATURE": str(meta.get("CACHE_SIGNATURE", "")),
        "TIME_SAMPLES": str(meta.get("TIME_SAMPLES", "")),
        "DEM_DATUM": str(meta.get("DEM_DATUM", "egm2008")),
        "TARGET_MODE": str(meta.get("TARGET_MODE", "intertidal")),
        "STAGE": "Stage 2 (Zero FES calls)",
        "TOPOLOGY_GUARD": "valid_mask_topology_aware",
        "QC_ENCODING": "UInt16 bitmask: bit0=FES_extrapolated, bit1=spatial_fallback, bit2=insufficient_nodes, bit3=datum_invalid, bit4=datum_source_approx, bit5=min_spacing_reached, bit6=connectivity_fallback, bit7=fes_validity_boundary, bit8=max_refinement_reached"
    }

    def _stage2_prog(pct_val, msg_val):
        if progress_callback:
            progress_callback(10 + int(pct_val * 0.9), msg_val)

    summary = stream_inundation_frequency_interpolation(
        info=info,
        leaf_cells=leaf_cells,
        labeled_coarse=labeled_coarse,
        downsample_factor=downsample_factor,
        h_coarse=h_coarse,
        w_coarse=w_coarse,
        input_valid_count=input_valid_count,
        output_path=output_path,
        qc_output_path=qc_output_path,
        block_size=block_size,
        metadata_tags=provenance_tags,
        allow_overwrite=allow_overwrite,
        progress_callback=_stage2_prog,
        cancel_event=cancel_event
    )
    summary.mode = "tide_cache_inundation"
    summary.metadata = meta

    if progress_callback:
        progress_callback(100, f"Stage 2 淹没频率解算完毕 (耗时 {time.time() - t_start:.2f}s)！")

    return summary
