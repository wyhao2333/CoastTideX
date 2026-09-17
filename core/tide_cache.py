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
from dataclasses import dataclass
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

COASTTIDEX_VERSION = "1.6"
CACHE_SCHEMA_VERSION = "1.2"
CACHE_SIGNATURE_ALGORITHM = "sha256"


class ExistingOutputError(FileExistsError):
    """目标正式产物已存在且当前策略不允许覆盖时抛出"""
    pass


class TideCacheCompatibilityError(ValueError):
    """Tide Cache 与目标 DEM 或解算参数不兼容异常"""
    pass


class TideCacheIntegrityError(ValueError):
    """Tide Cache 数据完整性损坏异常 (如单元引用的控制节点编号越界或不存在)"""
    pass


def parse_cache_time_to_utc(time_str: str, default_tz: str = "UTC") -> float:
    """
    安全解析时间字符串为 UTC epoch 纪元秒浮点数。
    支持 UTC 格式、带时区偏移格式 (如 '+08:00') 以及指定 default_tz 的 naive 字符串。
    """
    ts = pd.Timestamp(time_str)
    if ts.tzinfo is None:
        ts = ts.tz_localize(default_tz)
    ts_utc = ts.tz_convert("UTC")
    return float(ts_utc.timestamp())


@dataclass
class TideCacheStructure:
    """
    轻量级 Tide Cache 网格结构体 (不包含常驻 node x time 时序巨幅矩阵)。
    专用于二阶段流式露出分析按需分块读取与拓扑重建。
    """
    metadata: Dict[str, Any]
    nodes: List[ControlNode]
    leaf_cells: List[QuadCell]
    time_index: pd.DatetimeIndex
    num_nodes: int
    num_cells: int
    time_samples: int
    terminal_tide: Optional[np.ndarray] = None

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


def estimate_tide_cache_size(
    node_count: int,
    time_samples: int,
    resident_array_count: int = 2
) -> Dict[str, Any]:
    """
    估算 Tide Cache 控制节点潮位数组峰值驻留内存 (Raw MSL + Sorted Water Levels).
    按 float32 (4 字节) × resident_array_count (默认 2，即 8 字节/样本/节点) 计算。
    """
    bytes_per_sample_per_node = 4 * int(resident_array_count)
    raw_bytes = int(node_count) * int(time_samples) * bytes_per_sample_per_node
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
        "resident_array_count": int(resident_array_count),
        "bytes_per_sample_per_node": bytes_per_sample_per_node,
        "raw_bytes": raw_bytes,
        "raw_mb": raw_mb,
        "formatted_size": fmt,
        "note": f"包含控制网格常驻 {resident_array_count} 个时序数组 (raw MSL + sorted water levels = {bytes_per_sample_per_node} 字节/样本/节点) 峰值内存"
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
    inclusive: str = "left",
    time_samples: int = 0,
    dem_datum: str = "egm2008",
    constituents: str = "all",
    target_mode: str = "intertidal",
    initial_control_spacing_m: float = 4000.0,
    min_control_spacing_m: float = 500.0,
    inundation_error_tolerance_pct: float = 1.0,
    fes_model: str = "FES2022b",
    fes_source_type: str = "native_lgp2",
    topology_max_resolution_m: float = 100.0,
    topology_valid_fraction_threshold: float = 0.5,
    schema_version: str = CACHE_SCHEMA_VERSION,
    source_width: Optional[int] = None,
    source_height: Optional[int] = None,
    source_crs: Optional[str] = None,
    source_transform = None,
    source_bounds = None,
    source_resolution = None,
    source_nodata = None,
    source_file_size_bytes: Optional[int] = None,
    source_mtime_ns: Optional[int] = None,
    **kwargs
) -> Tuple[str, str]:
    """
    生成确定性的 Tide Cache 兼容性规范签名 (SHA256)。
    包含源数据几何、文件身份 (大小/mtime)、时间区间、FES模型、垂直基准及自适应网格/拓扑参数。
    """
    fsize_val = source_file_size_bytes
    mtime_val = source_mtime_ns

    if info is not None:
        w_val = int(info.width)
        h_val = int(info.height)
        c_val = str(info.crs).strip()
        t_vals = [round(float(v), 8) for v in list(info.transform)[:6]]
        b_vals = [round(float(v), 5) for v in info.bounds]
        nodata_val = float(info.nodata) if info.nodata is not None and np.isfinite(info.nodata) else None
        if fsize_val is None:
            fsize_val = getattr(info, "file_size_bytes", None)
        if mtime_val is None:
            mtime_val = getattr(info, "mtime_ns", None)
        if fsize_val is None and getattr(info, "path", None) and os.path.exists(info.path):
            try:
                st = os.stat(info.path)
                fsize_val = st.st_size
                mtime_val = st.st_mtime_ns
            except Exception:
                pass
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
            "nodata": nodata_val,
            "file_size_bytes": int(fsize_val) if fsize_val is not None else None,
            "mtime_ns": int(mtime_val) if mtime_val is not None else None
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
            "topology_max_resolution_m": round(float(topology_max_resolution_m), 2),
            "topology_valid_fraction_threshold": round(float(topology_valid_fraction_threshold), 4)
        },
        "cache": {
            "schema_version": str(schema_version),
            "signature_algorithm": CACHE_SIGNATURE_ALGORITHM
        }
    }

    json_str = json.dumps(canonical_dict, sort_keys=True, separators=(',', ':'))
    sig = hashlib.sha256(json_str.encode("utf-8")).hexdigest()
    return sig, json_str


def build_expected_cache_spec(
    info: RasterInfo,
    start_time: str,
    end_time: str,
    freq: str,
    dem_datum: str = "egm2008",
    constituents: str = "all",
    target_mode: str = "intertidal",
    initial_control_spacing_m: float = 4000.0,
    min_control_spacing_m: float = 500.0,
    inundation_error_tolerance_pct: float = 1.0,
    topology_max_resolution_m: float = 100.0,
    topology_valid_fraction_threshold: float = 0.5,
    fes_model: str = "FES2022b",
    fes_source_type: str = "native_lgp2",
    source_tz: str = "UTC",
    inclusive: str = "left",
    time_samples: Optional[int] = None
) -> Dict[str, Any]:
    """
    构建当前批量任务所预期的 Tide Cache 完整规格字典 (Expected Cache Spec)，
    用于 Resume 断点恢复时进行严格一致性校验。
    """
    if time_samples is None or time_samples <= 0:
        dr = pd.date_range(start_time, end_time, freq=freq, inclusive=inclusive, tz=source_tz)
        calc_samples = len(dr)
    else:
        calc_samples = int(time_samples)

    fsize = getattr(info, "file_size_bytes", None)
    mtime = getattr(info, "mtime_ns", None)
    if (fsize is None or mtime is None) and getattr(info, "path", None) and os.path.exists(info.path):
        try:
            st = os.stat(info.path)
            fsize = st.st_size
            mtime = st.st_mtime_ns
        except Exception:
            pass

    sig_hex, _ = generate_tide_cache_signature(
        info=info,
        start_time=start_time,
        end_time=end_time,
        freq=freq,
        source_tz=source_tz,
        inclusive=inclusive,
        time_samples=calc_samples,
        dem_datum=dem_datum,
        constituents=constituents,
        target_mode=target_mode,
        initial_control_spacing_m=initial_control_spacing_m,
        min_control_spacing_m=min_control_spacing_m,
        inundation_error_tolerance_pct=inundation_error_tolerance_pct,
        fes_model=fes_model,
        fes_source_type=fes_source_type,
        topology_max_resolution_m=topology_max_resolution_m,
        topology_valid_fraction_threshold=topology_valid_fraction_threshold,
        source_file_size_bytes=fsize,
        source_mtime_ns=mtime
    )

    return {
        "width": int(info.width),
        "height": int(info.height),
        "crs": str(info.crs),
        "transform": list(info.transform)[:6],
        "bounds": list(info.bounds)[:4],
        "nodata": float(info.nodata) if info.nodata is not None and np.isfinite(info.nodata) else None,
        "file_size_bytes": fsize,
        "mtime_ns": mtime,
        "start_time": start_time,
        "end_time": end_time,
        "freq": freq,
        "inclusive": inclusive,
        "time_samples": calc_samples,
        "source_tz": source_tz,
        "fes_model": fes_model,
        "fes_source_type": fes_source_type,
        "constituents": str(constituents).lower(),
        "dem_datum": str(dem_datum).lower(),
        "target_mode": str(target_mode).lower(),
        "initial_control_spacing_m": float(initial_control_spacing_m),
        "min_control_spacing_m": float(min_control_spacing_m),
        "inundation_error_tolerance_pct": float(inundation_error_tolerance_pct),
        "topology_max_resolution_m": float(topology_max_resolution_m),
        "topology_valid_fraction_threshold": float(topology_valid_fraction_threshold),
        "signature": sig_hex,
        "schema_version": CACHE_SCHEMA_VERSION
    }


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

        has_term = ("tide_msl_terminal_m" in ds.variables) or (str(attrs.get("HAS_TERMINAL_TIDE", "false")).lower() == "true")
        schema_v = str(attrs.get("CACHE_SCHEMA_VERSION", "1.1"))

        return {
            "metadata": attrs,
            "num_nodes": num_nodes,
            "num_cells": num_cells,
            "time_samples": time_samples,
            "is_complete": is_complete,
            "signature": signature,
            "cache_path": str(cache_path),
            "has_terminal_tide": has_term,
            "schema_version": schema_v
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

    if "start_time" in expected_spec and expected_spec["start_time"]:
        exp_st = str(expected_spec["start_time"]).strip()
        c_st = str(attrs.get("TIME_START", "")).strip()
        if exp_st and c_st and exp_st != c_st:
            reasons.append(f"起始时间不匹配: Cache 为 '{c_st}'，当前请求为 '{exp_st}'")

    if "end_time" in expected_spec and expected_spec["end_time"]:
        exp_et = str(expected_spec["end_time"]).strip()
        c_et = str(attrs.get("TIME_END", "")).strip()
        if exp_et and c_et and exp_et != c_et:
            reasons.append(f"终止时间不匹配: Cache 为 '{c_et}'，当前请求为 '{exp_et}'")

    if "inclusive" in expected_spec and expected_spec["inclusive"]:
        exp_inc = str(expected_spec["inclusive"]).strip().lower()
        c_inc = str(attrs.get("TIME_INCLUSIVE", "left")).strip().lower()
        if exp_inc and c_inc and exp_inc != c_inc:
            reasons.append(f"时间闭合模式不匹配: Cache 为 '{c_inc}'，当前请求为 '{exp_inc}'")

    # 6. 源文件身份检查 (Size & MTime)
    if "file_size_bytes" in expected_spec and expected_spec["file_size_bytes"] is not None:
        exp_size = int(expected_spec["file_size_bytes"])
        c_size = int(attrs.get("SOURCE_FILE_SIZE_BYTES", 0))
        if c_size > 0 and c_size != exp_size:
            reasons.append(f"源 DEM 文件大小已改变: Cache 记录 {c_size} 字节，当前磁盘文件为 {exp_size} 字节")

    if "mtime_ns" in expected_spec and expected_spec["mtime_ns"] is not None:
        exp_mtime = int(expected_spec["mtime_ns"])
        c_mtime = int(attrs.get("SOURCE_MTIME_NS", 0))
        if c_mtime > 0 and c_mtime != exp_mtime:
            reasons.append(f"源 DEM 文件修改时间已更新: Cache 记录 {c_mtime}，当前磁盘文件为 {exp_mtime}")

    # 7. 算法与拓扑参数校验 (Topology & Algorithm)
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

    if "initial_control_spacing_m" in expected_spec:
        exp_init_sp = float(expected_spec["initial_control_spacing_m"])
        c_init_sp = float(attrs.get("INITIAL_CONTROL_SPACING_M", 0.0))
        if c_init_sp > 0 and abs(c_init_sp - exp_init_sp) > 1.0:
            reasons.append(f"初始控制网格间距不匹配: Cache 为 {c_init_sp}m，当前请求为 {exp_init_sp}m")

    if "min_control_spacing_m" in expected_spec:
        exp_min_sp = float(expected_spec["min_control_spacing_m"])
        c_min_sp = float(attrs.get("MIN_CONTROL_SPACING_M", 0.0))
        if c_min_sp > 0 and abs(c_min_sp - exp_min_sp) > 1.0:
            reasons.append(f"最小控制网格间距不匹配: Cache 为 {c_min_sp}m，当前请求为 {exp_min_sp}m")

    if "inundation_error_tolerance_pct" in expected_spec:
        exp_tol = float(expected_spec["inundation_error_tolerance_pct"])
        c_tol = float(attrs.get("ERROR_TOLERANCE_PCT", 0.0))
        if c_tol > 0 and abs(c_tol - exp_tol) > 1e-3:
            reasons.append(f"淹没频率容错阈值不匹配: Cache 为 {c_tol}%，当前请求为 {exp_tol}%")

    if "topology_max_resolution_m" in expected_spec:
        exp_top = float(expected_spec["topology_max_resolution_m"])
        if "TOPOLOGY_RESOLUTION_M" not in attrs:
            reasons.append("Tide Cache 缺少 TOPOLOGY_RESOLUTION_M 属性，属于旧版缓存，必须重新生成")
        else:
            c_top = float(attrs["TOPOLOGY_RESOLUTION_M"])
            if abs(c_top - exp_top) > 1e-3:
                reasons.append(f"拓扑物理分辨率不匹配: Cache 为 {c_top}m，当前请求为 {exp_top}m")

    if "topology_valid_fraction_threshold" in expected_spec:
        exp_frac = float(expected_spec["topology_valid_fraction_threshold"])
        if "TOPOLOGY_VALID_FRACTION_THRESHOLD" not in attrs:
            reasons.append("Tide Cache 缺少 TOPOLOGY_VALID_FRACTION_THRESHOLD 属性，属于旧版缓存，必须重新生成")
        else:
            c_frac = float(attrs["TOPOLOGY_VALID_FRACTION_THRESHOLD"])
            if abs(c_frac - exp_frac) > 1e-4:
                reasons.append(f"拓扑连通有效比例阈值不匹配: Cache 为 {c_frac}，当前请求为 {exp_frac}")

    # 8. 签名自校验与篡改防御 (Tamper-evidence Verification)
    stored_sig = str(attrs.get("CACHE_SIGNATURE", "")).strip()
    cache_schema = str(attrs.get("CACHE_SCHEMA_VERSION", attrs.get("schema_version", "1.1"))).strip()
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
                source_file_size_bytes=int(attrs.get("SOURCE_FILE_SIZE_BYTES", 0)) if "SOURCE_FILE_SIZE_BYTES" in attrs else None,
                source_mtime_ns=int(attrs.get("SOURCE_MTIME_NS", 0)) if "SOURCE_MTIME_NS" in attrs else None,
                start_time=attrs.get("TIME_START", ""),
                end_time=attrs.get("TIME_END", ""),
                freq=attrs.get("TIME_STEP", ""),
                source_tz=attrs.get("TIMEZONE", "UTC"),
                inclusive=attrs.get("TIME_INCLUSIVE", "left"),
                time_samples=int(attrs.get("TIME_SAMPLES", info["time_samples"])),
                fes_model=attrs.get("TIDE_MODEL", attrs.get("FES_MODEL", "FES2022b")),
                fes_source_type=attrs.get("FES_SOURCE_TYPE", "native_lgp2"),
                constituents=attrs.get("CONSTITUENTS", "all"),
                dem_datum=attrs.get("DEM_DATUM", "egm2008"),
                target_mode=attrs.get("TARGET_MODE", "intertidal"),
                initial_control_spacing_m=float(attrs.get("INITIAL_CONTROL_SPACING_M", 4000.0)),
                min_control_spacing_m=float(attrs.get("MIN_CONTROL_SPACING_M", 500.0)),
                inundation_error_tolerance_pct=float(attrs.get("ERROR_TOLERANCE_PCT", 1.0)),
                topology_max_resolution_m=float(attrs.get("TOPOLOGY_RESOLUTION_M", 100.0)),
                topology_valid_fraction_threshold=float(attrs.get("TOPOLOGY_VALID_FRACTION_THRESHOLD", 0.5)),
                schema_version=cache_schema
            )
            if stored_sig != expected_c_sig:
                reasons.append(f"Tide Cache 元数据已被篡改或损坏 (签名不一致: {stored_sig[:12]}... != {expected_c_sig[:12]}...)")
        except Exception as ex:
            reasons.append(f"Tide Cache 签名校验异常: {str(ex)}")

    if "signature" in expected_spec:
        exp_sig = str(expected_spec["signature"]).strip()
        c_sig = info["signature"].strip()
        if exp_sig and c_sig and exp_sig != c_sig:
            # 向下兼容验证: 若 Cache 为 Schema 1.1，基于 1.1 重构规范签名进行对比
            is_valid_backward = False
            if cache_schema == "1.1":
                try:
                    exp_sig_11, _ = generate_tide_cache_signature(
                        source_width=int(expected_spec.get("width", 0)),
                        source_height=int(expected_spec.get("height", 0)),
                        source_crs=str(expected_spec.get("crs", "")),
                        source_transform=expected_spec.get("transform", []),
                        source_bounds=expected_spec.get("bounds", []),
                        source_nodata=expected_spec.get("nodata"),
                        source_file_size_bytes=expected_spec.get("file_size_bytes"),
                        source_mtime_ns=expected_spec.get("mtime_ns"),
                        start_time=expected_spec.get("start_time", ""),
                        end_time=expected_spec.get("end_time", ""),
                        freq=expected_spec.get("freq", ""),
                        source_tz=expected_spec.get("source_tz", "UTC"),
                        inclusive=expected_spec.get("inclusive", "left"),
                        time_samples=int(expected_spec.get("time_samples", 0)),
                        fes_model=expected_spec.get("fes_model", "FES2022b"),
                        fes_source_type=expected_spec.get("fes_source_type", "native_lgp2"),
                        constituents=expected_spec.get("constituents", "all"),
                        dem_datum=expected_spec.get("dem_datum", "egm2008"),
                        target_mode=expected_spec.get("target_mode", "intertidal"),
                        initial_control_spacing_m=float(expected_spec.get("initial_control_spacing_m", 4000.0)),
                        min_control_spacing_m=float(expected_spec.get("min_control_spacing_m", 500.0)),
                        inundation_error_tolerance_pct=float(expected_spec.get("inundation_error_tolerance_pct", 1.0)),
                        topology_max_resolution_m=float(expected_spec.get("topology_max_resolution_m", 100.0)),
                        topology_valid_fraction_threshold=float(expected_spec.get("topology_valid_fraction_threshold", 0.5)),
                        schema_version="1.1"
                    )
                    if c_sig == exp_sig_11:
                        is_valid_backward = True
                except Exception:
                    pass
            if not is_valid_backward:
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
    cancel_event = None,
    tide_msl_terminal: Optional[np.ndarray] = None,
    schema_version: str = CACHE_SCHEMA_VERSION
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

    top_res = float(metadata.get("topology_max_resolution_m", 100.0))
    top_frac = float(metadata.get("topology_valid_fraction_threshold", 0.5))

    fsize_val = getattr(info, "file_size_bytes", None)
    mtime_val = getattr(info, "mtime_ns", None)
    if (fsize_val is None or mtime_val is None) and getattr(info, "path", None) and os.path.exists(info.path):
        try:
            st = os.stat(info.path)
            fsize_val = st.st_size
            mtime_val = st.st_mtime_ns
        except Exception:
            pass

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
        topology_max_resolution_m=top_res,
        topology_valid_fraction_threshold=top_frac,
        source_file_size_bytes=fsize_val,
        source_mtime_ns=mtime_val,
        schema_version=schema_version
    )

    time_epochs = np.asarray([float(t.timestamp()) for t in pd.to_datetime(time_index, utc=True)], dtype=np.float64)
    est_mem = estimate_tide_cache_size(num_nodes, num_times, resident_array_count=2)

    try:
        with netCDF4.Dataset(tmp_cache_path, mode="w", format="NETCDF4") as ds:
            ds.createDimension("node", num_nodes)
            ds.createDimension("time", num_times)
            ds.createDimension("cell", num_cells)
            ds.createDimension("bounds_dim", 4)
            ds.createDimension("corners_dim", 4)

            ds.setncattr("COASTTIDEX_VERSION", COASTTIDEX_VERSION)
            ds.setncattr("CACHE_SCHEMA_VERSION", str(schema_version))
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
            ds.setncattr("SOURCE_FILE_SIZE_BYTES", int(fsize_val) if fsize_val is not None else 0)
            ds.setncattr("SOURCE_MTIME_NS", int(mtime_val) if mtime_val is not None else 0)
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
            ds.setncattr("TOPOLOGY_RESOLUTION_M", float(top_res))
            ds.setncattr("TOPOLOGY_VALID_FRACTION_THRESHOLD", float(top_frac))
            ds.setncattr("TOPOLOGY_SOURCE", "target_mask_derived")
            ds.setncattr("RESIDENT_TIMESERIES_ARRAYS_PER_NODE", 2)
            ds.setncattr("ESTIMATED_CONTROL_ARRAY_MEMORY_MB", float(est_mem["raw_mb"]))
            ds.setncattr("CONTROL_NODE_COUNT", int(num_nodes))
            ds.setncattr("ACTIVE_CONTROL_NODE_COUNT", int(sum(1 for n in all_nodes if n.valid)))
            ds.setncattr("LEAF_CELL_COUNT", int(num_cells))
            ds.setncattr("CREATED_AT", datetime.now(timezone.utc).isoformat())

            var_time = ds.createVariable("time", "f8", ("time",))
            var_time.units = "seconds since 1970-01-01 00:00:00 UTC"
            var_time.calendar = "proleptic_gregorian"
            var_time[:] = time_epochs

            var_node_x = ds.createVariable("node_x", "f8", ("node",), zlib=True)
            var_node_y = ds.createVariable("node_y", "f8", ("node",), zlib=True)
            var_node_lon = ds.createVariable("node_lon", "f8", ("node",), zlib=True)
            var_node_lat = ds.createVariable("node_lat", "f8", ("node",), zlib=True)
            var_node_val = ds.createVariable("node_valid", "u1", ("node",), zlib=True)
            var_offset = ds.createVariable("static_offset_m", "f4", ("node",), zlib=True)
            var_comp = ds.createVariable("component_id", "i4", ("node",), zlib=True)
            var_qc = ds.createVariable("node_qc", "u4", ("node",), zlib=True)

            node_x_arr = np.array([n.x for n in all_nodes], dtype=np.float64)
            node_y_arr = np.array([n.y for n in all_nodes], dtype=np.float64)
            node_lon_arr = np.array([n.lon for n in all_nodes], dtype=np.float64)
            node_lat_arr = np.array([n.lat for n in all_nodes], dtype=np.float64)
            node_val_arr = np.array([1 if n.valid else 0 for n in all_nodes], dtype=np.uint8)
            node_off_arr = np.array([n.static_offset_m for n in all_nodes], dtype=np.float32)
            node_comp_arr = np.array([n.component_id for n in all_nodes], dtype=np.int32)
            node_qc_arr = np.array([n.qc_bitmask for n in all_nodes], dtype=np.uint32)

            var_node_x[:] = node_x_arr
            var_node_y[:] = node_y_arr
            var_node_lon[:] = node_lon_arr
            var_node_lat[:] = node_lat_arr
            var_node_val[:] = node_val_arr
            var_offset[:] = node_off_arr
            var_comp[:] = node_comp_arr
            var_qc[:] = node_qc_arr

            var_tide = ds.createVariable(
                "tide_msl_m", "f4", ("node", "time"),
                zlib=True, complevel=4, shuffle=True,
                chunksizes=(min(128, num_nodes), min(512, num_times))
            )
            var_tide.units = "meters"
            var_tide.long_name = "raw astronomical tide relative to MSL"

            # 流式逐节点写入时序矩阵
            for i, n in enumerate(all_nodes):
                if cancel_event is not None and cancel_event.is_set():
                    raise RasterCalculationCancelled("用户取消了 Tide Cache 写入。")
                if n.valid and n.tide_msl_raw is not None and len(n.tide_msl_raw) == num_times:
                    var_tide[i, :] = n.tide_msl_raw
                else:
                    var_tide[i, :] = np.full(num_times, np.nan, dtype=np.float32)

            if tide_msl_terminal is not None:
                var_term = ds.createVariable("tide_msl_terminal_m", "f4", ("node",), zlib=True)
                var_term.units = "meters"
                var_term.long_name = "raw astronomical tide relative to MSL at end time"
                var_term[:] = np.asarray(tide_msl_terminal, dtype=np.float32)
                ds.setncattr("HAS_TERMINAL_TIDE", "true")
            else:
                ds.setncattr("HAS_TERMINAL_TIDE", "false")

            var_cell_bounds = ds.createVariable("cell_bounds", "f8", ("cell", "bounds_dim"), zlib=True)
            var_cell_nodes = ds.createVariable("cell_node_indices", "i4", ("cell", "corners_dim"), zlib=True)
            var_cell_lvl = ds.createVariable("cell_level", "i2", ("cell",), zlib=True)
            var_cell_qc = ds.createVariable("cell_qc", "u4", ("cell",), zlib=True)
            var_cell_err = ds.createVariable("cell_max_error", "f4", ("cell",), zlib=True)

            cell_bounds_arr = np.empty((num_cells, 4), dtype=np.float64)
            cell_nodes_arr = np.empty((num_cells, 4), dtype=np.int32)
            cell_lvl_arr = np.empty(num_cells, dtype=np.int16)
            cell_qc_arr = np.empty(num_cells, dtype=np.uint32)
            cell_err_arr = np.empty(num_cells, dtype=np.float32)

            for c_idx, cell in enumerate(leaf_cells):
                cell_bounds_arr[c_idx, :] = [cell.x_min, cell.y_min, cell.x_max, cell.y_max]
                cell_nodes_arr[c_idx, :] = [
                    node_id_map[cell.node_a.node_id],
                    node_id_map[cell.node_b.node_id],
                    node_id_map[cell.node_c.node_id],
                    node_id_map[cell.node_d.node_id]
                ]
                cell_lvl_arr[c_idx] = cell.level
                qc_mask = 0
                if cell.qc_min_spacing_reached:
                    qc_mask |= QC_BIT_MIN_SPACING_REACHED
                if cell.qc_max_refinement_reached:
                    qc_mask |= QC_BIT_MAX_REFINEMENT_REACHED
                if cell.qc_validity_boundary:
                    qc_mask |= QC_BIT_FES_VALIDITY_BOUNDARY
                cell_qc_arr[c_idx] = qc_mask
                cell_err_arr[c_idx] = cell.max_error_pct

            var_cell_bounds[:] = cell_bounds_arr
            var_cell_nodes[:] = cell_nodes_arr
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


def read_tide_cache(cache_path: str, node_chunk_size: int = 256, chunk_node_size: Optional[int] = None, load_raw_tide: bool = False) -> Dict[str, Any]:
    """
    读取 Tide Cache NetCDF 文件并重建自适应控制节点与叶单元拓扑。
    采用按节点分块流式读取 (Node-chunk reading) 策略，严禁一次性加载完整 tide_msl_m 矩阵。
    """
    if chunk_node_size is not None:
        node_chunk_size = chunk_node_size
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

        var_tide = ds.variables["tide_msl_m"]

        # 采用 node-chunking 分块逐批读取并排序，避免海量节点时内存翻倍
        chunk_size = max(1, int(node_chunk_size))
        for c_start in range(0, num_nodes, chunk_size):
            c_end = min(num_nodes, c_start + chunk_size)
            chunk_tide_raw = var_tide[c_start:c_end, :]  # 仅读取当前 chunk

            for local_idx, i in enumerate(range(c_start, c_end)):
                is_valid = bool(node_val[i])
                off_m = float(static_offset[i])

                if is_valid:
                    raw_t = np.array(chunk_tide_raw[local_idx], dtype=np.float32)
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
                    qc_code=int(node_qc[i]),
                    tide_msl_raw=raw_t if (is_valid and load_raw_tide) else None
                )
                if is_valid and load_raw_tide:
                    node.water_levels_msl = raw_t
                nodes.append(node)

            del chunk_tide_raw

        cell_bounds = ds.variables["cell_bounds"][:]
        cell_nodes = ds.variables["cell_node_indices"][:]
        cell_lvl = ds.variables["cell_level"][:]
        cell_qc = ds.variables["cell_qc"][:]
        cell_err = ds.variables["cell_max_error"][:]

        num_cells = len(cell_lvl)
        leaf_cells: List[QuadCell] = []

        for c in range(num_cells):
            idx_a = int(cell_nodes[c, 0])
            idx_b = int(cell_nodes[c, 1])
            idx_c = int(cell_nodes[c, 2])
            idx_d = int(cell_nodes[c, 3])
            for idx_k in (idx_a, idx_b, idx_c, idx_d):
                if idx_k < 0 or idx_k >= num_nodes:
                    raise TideCacheIntegrityError(
                        f"Tide Cache 拓扑损坏: QuadCell {c} 引用了越界控制节点索引 {idx_k} (合法节点范围: 0 ~ {num_nodes - 1})"
                    )

            na = nodes[idx_a]
            nb = nodes[idx_b]
            nc = nodes[idx_c]
            nd = nodes[idx_d]
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

        terminal_tide = None
        if "tide_msl_terminal_m" in ds.variables:
            terminal_tide = np.array(ds.variables["tide_msl_terminal_m"][:], dtype=np.float32)

        return {
            "metadata": attrs,
            "nodes": nodes,
            "leaf_cells": leaf_cells,
            "time_index": time_index,
            "num_nodes": num_nodes,
            "num_cells": num_cells,
            "time_samples": len(time_epochs),
            "terminal_tide": terminal_tide
        }


def read_tide_cache_structure(cache_path: str) -> TideCacheStructure:
    """
    轻量级只读解析 Tide Cache 控制网格拓扑结构与元数据 (零时序数组内存加载)。
    严禁读取 tide_msl_m 巨幅矩阵，峰值内存仅取决于节点与网格单元数量 (< 5MB)。
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

        for i in range(num_nodes):
            is_valid = bool(node_val[i])
            off_m = float(static_offset[i])
            node = ControlNode(
                node_id=i,
                x=float(node_x[i]),
                y=float(node_y[i]),
                lon=float(node_lon[i]),
                lat=float(node_lat[i]),
                water_levels_sorted=None,
                valid=is_valid,
                static_offset_m=off_m,
                component_id=int(comp_id[i]),
                qc_bitmask=int(node_qc[i]),
                qc_code=int(node_qc[i]),
                tide_msl_raw=None
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
            idx_a = int(cell_nodes[c, 0])
            idx_b = int(cell_nodes[c, 1])
            idx_c = int(cell_nodes[c, 2])
            idx_d = int(cell_nodes[c, 3])
            for idx_k in (idx_a, idx_b, idx_c, idx_d):
                if idx_k < 0 or idx_k >= num_nodes:
                    raise TideCacheIntegrityError(
                        f"Tide Cache 拓扑损坏: QuadCell {c} 引用了越界控制节点索引 {idx_k} (合法节点范围: 0 ~ {num_nodes - 1})"
                    )

            na = nodes[idx_a]
            nb = nodes[idx_b]
            nc = nodes[idx_c]
            nd = nodes[idx_d]
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

        terminal_tide = None
        if "tide_msl_terminal_m" in ds.variables:
            terminal_tide = np.array(ds.variables["tide_msl_terminal_m"][:], dtype=np.float32)

        return TideCacheStructure(
            metadata=attrs,
            nodes=nodes,
            leaf_cells=leaf_cells,
            time_index=time_index,
            num_nodes=num_nodes,
            num_cells=num_cells,
            time_samples=len(time_epochs),
            terminal_tide=terminal_tide
        )


class TideCacheTimeSeriesReader:
    """
    Tide Cache NetCDF 时序流式分块读取器。
    按需提取指定控制节点集合的局部时间窗口切片，支持 context manager。
    严格限制内存驻留，禁止将全量节点时序一次性载入内存。
    """
    def __init__(self, cache_path: str):
        self.cache_path = str(cache_path)
        self._ds: Optional[netCDF4.Dataset] = None
        self.num_nodes: int = 0
        self.num_times: int = 0

    def open(self):
        if self._ds is None:
            if not os.path.exists(self.cache_path):
                raise FileNotFoundError(f"未找到指定的 Tide Cache 文件: {self.cache_path}")
            self._ds = netCDF4.Dataset(self.cache_path, mode="r")
            self.num_nodes = len(self._ds.dimensions["node"])
            self.num_times = len(self._ds.dimensions["time"])
        return self

    def close(self):
        if self._ds is not None:
            try:
                self._ds.close()
            except Exception:
                pass
            self._ds = None

    def __enter__(self):
        return self.open()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def read_chunk(self, node_indices: List[int], start_time_idx: int, end_time_idx: int) -> np.ndarray:
        """
        读取指定节点集合在 [start_time_idx, end_time_idx) 时间区间的 Raw MSL 潮位切片。
        返回形状为 (len(node_indices), end_time_idx - start_time_idx) 的 float32 数组。
        """
        if self._ds is None:
            self.open()
        if len(node_indices) == 0:
            return np.zeros((0, max(0, end_time_idx - start_time_idx)), dtype=np.float32)
        for nid in node_indices:
            if nid < 0 or nid >= self.num_nodes:
                raise TideCacheIntegrityError(f"请求的控制节点索引越界: {nid} (合法范围: 0 ~ {self.num_nodes - 1})")

        var_tide = self._ds.variables["tide_msl_m"]
        chunk = var_tide[node_indices, start_time_idx:end_time_idx]
        return np.asarray(chunk, dtype=np.float32)


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
    基于预先生成的 Tide Cache (*_tide.nc) 与输入 DEM 解算淹没频率 GeoTIFF (Stage 2)。
    严格从 Tide Cache 读取 TOPOLOGY_RESOLUTION_M 与 TOPOLOGY_VALID_FRACTION_THRESHOLD，
    确保与 Stage 1 控制网格构建逻辑完全一致。
    """
    t_start = time.time()
    if cancel_event is not None and cancel_event.is_set():
        raise RasterCalculationCancelled("用户取消了 Stage 2 淹没频率解算。")

    if not os.path.exists(cache_path):
        raise FileNotFoundError(f"未找到指定的 Tide Cache 文件: {cache_path}")
    if not os.path.exists(dem_path):
        raise FileNotFoundError(f"未找到指定的输入 DEM 文件: {dem_path}")

    from .raster_engine import RasterTideEngine
    engine = RasterTideEngine()
    info = engine.inspect_raster(dem_path, compute_valid_count=True)

    fsize = getattr(info, "file_size_bytes", None)
    mtime = getattr(info, "mtime_ns", None)
    if (fsize is None or mtime is None) and os.path.exists(dem_path):
        try:
            st = os.stat(dem_path)
            fsize = st.st_size
            mtime = st.st_mtime_ns
        except Exception:
            pass

    expected_spec = {
        "width": info.width,
        "height": info.height,
        "crs": info.crs,
        "transform": info.transform,
        "bounds": info.bounds,
        "nodata": info.nodata,
        "file_size_bytes": fsize,
        "mtime_ns": mtime
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

    if "TOPOLOGY_RESOLUTION_M" not in meta or "TOPOLOGY_VALID_FRACTION_THRESHOLD" not in meta:
        raise TideCacheCompatibilityError(
            "Tide Cache 缺少必要拓扑参数元数据 (TOPOLOGY_RESOLUTION_M / TOPOLOGY_VALID_FRACTION_THRESHOLD)，属于旧版缓存或不兼容，必须重新生成 (Rebuild)。"
        )

    top_res_m = float(meta["TOPOLOGY_RESOLUTION_M"])
    top_frac = float(meta["TOPOLOGY_VALID_FRACTION_THRESHOLD"])

    labeled_coarse, num_features, downsample_factor, h_coarse, w_coarse, _, input_valid_count = build_support_topology(
        info=info,
        topology_max_resolution_m=top_res_m,
        topology_valid_fraction_threshold=top_frac,
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
        "TOPOLOGY_SOURCE": "target_mask_derived",
        "TOPOLOGY_RESOLUTION_M": str(top_res_m),
        "TOPOLOGY_VALID_FRACTION_THRESHOLD": str(top_frac),
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


def calculate_exposure_from_tide_cache(
    dem_path: str,
    cache_path: str,
    output_dir: Optional[str] = None,
    output_paths = None,
    base_name: Optional[str] = None,
    block_size: int = 512,
    time_chunk_size: int = 1000,
    allow_overwrite: bool = True,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    cancel_event = None
) -> Dict[str, Any]:
    """
    基于预先生成的 Tide Cache (*_tide.nc, Schema 1.2) 与输入 DEM 解算潜在天文潮露出时间域栅格产品 (零 FES 重复调用)。
    采用流式轻量级读取 (TideCacheStructure + TideCacheTimeSeriesReader)，绝不一次性加载全量时序矩阵。
    生成 7 大独立 GeoTIFF 科学产品。
    """
    t_start = time.time()
    if cancel_event is not None and cancel_event.is_set():
        raise RasterCalculationCancelled("用户取消了潜在露出栅格解算。")

    if not os.path.exists(cache_path):
        raise FileNotFoundError(f"未找到指定的 Tide Cache 文件: {cache_path}")
    if not os.path.exists(dem_path):
        raise FileNotFoundError(f"未找到指定的输入 DEM 文件: {dem_path}")

    from .raster_engine import RasterTideEngine
    engine = RasterTideEngine()
    info = engine.inspect_raster(dem_path, compute_valid_count=True)

    fsize = getattr(info, "file_size_bytes", None)
    mtime = getattr(info, "mtime_ns", None)
    if (fsize is None or mtime is None) and os.path.exists(dem_path):
        try:
            st = os.stat(dem_path)
            fsize = st.st_size
            mtime = st.st_mtime_ns
        except Exception:
            pass

    expected_spec = {
        "width": info.width,
        "height": info.height,
        "crs": info.crs,
        "transform": info.transform,
        "bounds": info.bounds,
        "nodata": info.nodata,
        "file_size_bytes": fsize,
        "mtime_ns": mtime
    }

    compatible, reasons = validate_tide_cache_compatibility(cache_path, expected_spec)
    if not compatible:
        raise TideCacheCompatibilityError(
            f"Tide Cache 与目标 DEM 不兼容，无法执行露出分析解算: {'; '.join(reasons)}"
        )

    if progress_callback:
        progress_callback(5, "Tide Cache 兼容性通过，正在加载控制网格拓扑...")

    cache_data = read_tide_cache_structure(cache_path)
    leaf_cells: List[QuadCell] = cache_data.leaf_cells
    nodes: List[ControlNode] = cache_data.nodes
    meta: Dict[str, Any] = cache_data.metadata
    time_idx: pd.DatetimeIndex = cache_data.time_index
    terminal_tide: Optional[np.ndarray] = cache_data.terminal_tide

    # 准备产物路径
    from .exposure_engine import ExposureProductPaths, stream_exposure_metrics_interpolation
    if output_paths is None:
        if output_dir is None:
            output_dir = os.path.dirname(os.path.abspath(cache_path))
        os.makedirs(output_dir, exist_ok=True)
        if base_name is None:
            raw_base = os.path.splitext(os.path.basename(dem_path))[0]
            if raw_base.endswith("_tide"):
                raw_base = raw_base[:-5]
            base_name = raw_base

        output_paths = ExposureProductPaths(
            exposure_fraction_path=os.path.join(output_dir, f"{base_name}_exposure_fraction.tif"),
            exposure_duration_h_path=os.path.join(output_dir, f"{base_name}_exposure_duration_h.tif"),
            exposure_max_continuous_h_path=os.path.join(output_dir, f"{base_name}_exposure_max_continuous_h.tif"),
            exposure_mean_event_h_path=os.path.join(output_dir, f"{base_name}_exposure_mean_event_h.tif"),
            exposure_event_count_path=os.path.join(output_dir, f"{base_name}_exposure_event_count.tif"),
            exposure_valid_time_fraction_path=os.path.join(output_dir, f"{base_name}_exposure_valid_time_fraction.tif"),
            exposure_qc_path=os.path.join(output_dir, f"{base_name}_exposure_qc.tif"),
        )

    # 检查输出文件冲突
    if not allow_overwrite:
        for p in [
            output_paths.exposure_fraction_path,
            output_paths.exposure_duration_h_path,
            output_paths.exposure_max_continuous_h_path,
            output_paths.exposure_mean_event_h_path,
            output_paths.exposure_event_count_path,
            output_paths.exposure_valid_time_fraction_path,
            output_paths.exposure_qc_path
        ]:
            if os.path.exists(p):
                raise ExistingOutputError(f"目标输出产物已存在且不允许覆盖: {p}")

    # 解析请求时间窗口及终端时刻时间戳 (秒)
    source_tz = str(meta.get("TIMEZONE", "UTC"))
    req_start_sec = None
    req_end_sec = None
    if "TIME_START" in meta:
        try:
            req_start_sec = parse_cache_time_to_utc(str(meta["TIME_START"]), source_tz)
        except Exception:
            pass
    if "TIME_END" in meta:
        try:
            req_end_sec = parse_cache_time_to_utc(str(meta["TIME_END"]), source_tz)
        except Exception:
            pass

    term_ts_sec = None
    if terminal_tide is not None and req_end_sec is not None:
        term_ts_sec = req_end_sec
    elif terminal_tide is not None and "TIME_END" in meta:
        try:
            term_ts_sec = parse_cache_time_to_utc(str(meta["TIME_END"]), source_tz)
        except Exception:
            pass

    from .raster_engine import build_support_topology
    top_res_m = float(meta.get("TOPOLOGY_RESOLUTION_M", 100.0))
    top_frac = float(meta.get("TOPOLOGY_VALID_FRACTION_THRESHOLD", 0.5))

    labeled_coarse, num_features, downsample_factor, h_coarse, w_coarse, _, input_valid_count = build_support_topology(
        info=info,
        topology_max_resolution_m=top_res_m,
        topology_valid_fraction_threshold=top_frac,
        block_size=block_size,
        cancel_event=cancel_event
    )

    def _exp_prog(pct_val, msg_val):
        if progress_callback:
            progress_callback(10 + int(pct_val * 0.9), msg_val)

    meta_tags = {
        "CACHE_SIGNATURE": str(meta.get("CACHE_SIGNATURE", "")),
        "CACHE_SCHEMA_VERSION": str(meta.get("CACHE_SCHEMA_VERSION", "1.2")),
        "TOPOLOGY_RESOLUTION_M": str(top_res_m),
        "TOPOLOGY_VALID_FRACTION_THRESHOLD": str(top_frac),
        "TOPOLOGY_GUARD": "valid_mask_topology_aware",
        "STAGE": "Stage 2b (Zero FES calls)"
    }

    res = stream_exposure_metrics_interpolation(
        dem_path=dem_path,
        output_paths=output_paths,
        cells=leaf_cells,
        nodes=nodes,
        time_series_utc=np.asarray(time_idx),
        target_datum=str(meta.get("DEM_DATUM", "egm2008")),
        time_chunk_size=time_chunk_size,
        block_size=block_size,
        terminal_node_tides=terminal_tide,
        terminal_timestamp_sec=term_ts_sec,
        requested_time_start_sec=req_start_sec,
        requested_time_end_sec=req_end_sec,
        cache_path=cache_path,
        labeled_coarse=labeled_coarse,
        downsample_factor=downsample_factor,
        h_coarse=h_coarse,
        w_coarse=w_coarse,
        metadata_tags=meta_tags,
        allow_overwrite=allow_overwrite,
        progress_callback=_exp_prog,
        cancel_event=cancel_event
    )
    return res
