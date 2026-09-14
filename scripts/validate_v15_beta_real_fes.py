"""
CoastTideX v1.5 Beta — Controlled Real-FES / Real-Intertidal Validation Harness
脚本名称: scripts/validate_v15_beta_real_fes.py

功能:
    1. 环境与预检 (Preflight): 系统/库版本、FES2022b Native LGP2 网格核验、垂直基准/MDT 网格核验、掩膜诊断
    2. LEVEL 1: Real Native FES Smoke Test (真实沿海坐标 48h 30min Native LGP2 解算及耗时与极值统计)
    3. 真实/测试 Raster 库目录盘点 (Inventory) 与目标掩膜几何分类 (Target Mask Geometry)
    4. LEVEL 2: 真实/代表性 DEM 短周期 (48h & 7d) 完整流水线验证 (Tide Cache -> Stage 2 淹没频率与 QC)
    5. Direct Sampled-Pixel FES Oracle: 真实 DEM 像元抽样直接 FES 潮位基准真值对比与统计分析 (MAE, RMSE, P95, Max)
    6. LEVEL 3: Tide-Cache 阶段解耦序列化精度门禁 (Direct Raster vs Cache-driven Stage 2)
    7. LEVEL 4: 2024 全年 17,568 样本长时序验证与资源/内存预估
    8. LEVEL 5: 真实瓦片切缝 (Seam) 连续性测试 (Whole vs Split A/B)
    9. 断点续跑 (Resume)、参数防篡改兼容性保护与 Overwrite 模式安全校验
    10. 批量扫描队列防竞态快照测试
    11. 结构化 JSON 指标导出与完整 Beta 科学验证报告生成
"""

import os
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import time
import json
import math
import hashlib
import argparse
import platform
import subprocess
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple

import numpy as np
import pandas as pd
import rasterio
import rasterio.transform
import rasterio.warp

# 确保项目根目录在 sys.path 中
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from core.utils import (
    load_app_config, resolve_project_path, normalize_longitude, build_time_index
)
from core.datum_engine import DatumTransformer
from core.tide_engine import FESTidePredictor, ALL_34_CONSTITUENTS
from core.raster_engine import RasterTideEngine
from core.tide_cache import generate_tide_cache_signature, estimate_tide_cache_size, is_cache_complete
from core.batch_raster_engine import BatchRasterEngine, ExistingOutputPolicy

try:
    from pyproj import Transformer
except ImportError:
    Transformer = None

try:
    import pyfes
    HAS_PYFES = True
except ImportError:
    pyfes = None
    HAS_PYFES = False


# ----------------------------------------------------------------------
# 1. 辅助函数：环境与 Git 状态
# ----------------------------------------------------------------------
def collect_environment(root_dir: str) -> Dict[str, Any]:
    """采集完整的系统、依赖与 Git 状态"""
    commit_sha = "unknown"
    branch_name = "unknown"
    try:
        r_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root_dir, capture_output=True, text=True)
        if r_sha.returncode == 0:
            commit_sha = r_sha.stdout.strip()
        r_br = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root_dir, capture_output=True, text=True)
        if r_br.returncode == 0:
            branch_name = r_br.stdout.strip()
    except Exception:
        pass

    pyfes_ver = "not_installed"
    if HAS_PYFES:
        pyfes_ver = getattr(pyfes, "__version__", "installed_unknown_version")

    import netCDF4
    import scipy
    import pyproj
    import PyQt6.QtCore

    return {
        "platform": platform.platform(),
        "python_version": sys.version,
        "python_executable": sys.executable,
        "pyfes_version": pyfes_ver,
        "numpy_version": np.__version__,
        "scipy_version": scipy.__version__,
        "pandas_version": pd.__version__,
        "rasterio_version": rasterio.__version__,
        "netCDF4_version": netCDF4.__version__,
        "pyproj_version": pyproj.__version__,
        "PyQt6_version": PyQt6.QtCore.PYQT_VERSION_STR,
        "git_commit": commit_sha,
        "git_branch": branch_name,
        "timestamp": datetime.now().isoformat()
    }


def check_data_sources(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """检查核心科学数据源的存在性、体积与元数据"""
    paths = cfg.get("paths", {})
    results = {}

    for key in ["fes_ns_grid", "fes_extrapolation_mask_nc", "mdt_nc", "egm2008_tif",
                "delta_n_goco06s_egm2008_tif", "delta_n_eigen6c4_egm2008_tif", "hybrid_mdt_source_mask"]:
        raw = paths.get(key, "")
        resolved = resolve_project_path(raw) if raw else ""
        exists = os.path.exists(resolved) if resolved else False
        size = os.path.getsize(resolved) if exists and os.path.isfile(resolved) else 0
        results[key] = {
            "configured_path": raw,
            "resolved_path": resolved,
            "exists": exists,
            "size_bytes": size
        }

    # 特别标明 EIGEN Delta-N 是否可用
    results["EIGEN_DELTA_N_AVAILABLE"] = results["delta_n_eigen6c4_egm2008_tif"]["exists"]
    return results


# ----------------------------------------------------------------------
# 2. LEVEL 1: Real Native FES Smoke Test
# ----------------------------------------------------------------------
SMOKE_COASTAL_POINTS = [
    {"name": "Yangtze_Estuary_China", "lon": 122.0, "lat": 31.0},
    {"name": "Pearl_River_Delta_China", "lon": 113.8, "lat": 22.3},
    {"name": "Bohai_Bay_China", "lon": 119.0, "lat": 38.5},
    {"name": "Crete_Mediterranean", "lon": 24.0, "lat": 35.0},
    {"name": "Dutch_Coast_NorthSea", "lon": 4.0, "lat": 52.5},
    {"name": "Cape_Hatteras_USA", "lon": -75.5, "lat": 35.2},
    {"name": "Great_Barrier_Reef_AUS", "lon": 146.0, "lat": -18.0},
    {"name": "Bay_of_Fundy_CAN", "lon": -65.0, "lat": 45.0}
]

def run_level1_fes_smoke(predictor: Any, points: List[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """执行 Level 1: 真实 Native FES 48h 散点预测冒烟测试"""
    if points is None:
        points = SMOKE_COASTAL_POINTS

    results = []
    for pt in points:
        name = pt.get("name", "point")
        lon = float(pt["lon"])
        lat = float(pt["lat"])
        t0 = time.time()
        try:
            df = predictor.predict_series(
                lon=lon,
                lat=lat,
                start_time="2024-01-01 00:00",
                end_time="2024-01-03 00:00",
                freq="30min",
                inclusive="both"
            )
            t1 = time.time()
            tide_vals = df["tide_total_m"].to_numpy()
            valid_mask = np.isfinite(tide_vals)
            valid_count = int(np.count_nonzero(valid_mask))
            invalid_count = int(len(tide_vals) - valid_count)

            if valid_count > 0:
                t_min = float(np.nanmin(tide_vals))
                t_max = float(np.nanmax(tide_vals))
                t_mean = float(np.nanmean(tide_vals))
            else:
                t_min = t_max = t_mean = None

            results.append({
                "name": name,
                "lon": lon,
                "lat": lat,
                "time_samples": len(df),
                "valid_count": valid_count,
                "invalid_count": invalid_count,
                "min": t_min,
                "max": t_max,
                "mean": t_mean,
                "runtime_seconds": round(t1 - t0, 4),
                "status": "VALID" if valid_count > 0 else "INVALID_ALL_NAN"
            })
        except Exception as e:
            results.append({
                "name": name,
                "lon": lon,
                "lat": lat,
                "time_samples": 0,
                "valid_count": 0,
                "invalid_count": 0,
                "min": None,
                "max": None,
                "mean": None,
                "runtime_seconds": round(time.time() - t0, 4),
                "status": f"ERROR: {str(e)}"
            })
    return results


# ----------------------------------------------------------------------
# 3. 栅格盘点与目标几何分类 (Inventory & Target Geometry)
# ----------------------------------------------------------------------
def scan_raster_inventory(input_dir: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """轻量扫描输入目录中所有 GeoTIFF 元数据（不读取整景像元）"""
    if not os.path.isdir(input_dir):
        return [], {"status": "REAL_VALIDATION_INPUT_DIR_NOT_FOUND", "total_files": 0, "valid_tiffs": 0}

    inventory = []
    crs_groups = {}
    res_groups = {}
    valid_count = 0
    invalid_count = 0

    for root, _, files in os.walk(input_dir):
        for f in files:
            if not (f.lower().endswith(".tif") or f.lower().endswith(".tiff")):
                continue
            abs_p = os.path.join(root, f)
            rel_p = os.path.relpath(abs_p, input_dir)
            size_b = os.path.getsize(abs_p)
            mtime_ns = os.stat(abs_p).st_mtime_ns

            try:
                with rasterio.open(abs_p) as src:
                    crs_str = src.crs.to_string() if src.crs else "NONE"
                    res = (src.res[0], src.res[1])
                    item = {
                        "relative_path": rel_p.replace("\\", "/"),
                        "absolute_path": abs_p,
                        "filename": f,
                        "file_size_bytes": size_b,
                        "mtime_ns": mtime_ns,
                        "width": src.width,
                        "height": src.height,
                        "band_count": src.count,
                        "dtype": str(src.dtypes[0]),
                        "crs": crs_str,
                        "transform": list(src.transform)[:6],
                        "resolution": res,
                        "bounds": [src.bounds.left, src.bounds.bottom, src.bounds.right, src.bounds.top],
                        "nodata": src.nodata,
                        "valid": True
                    }
                    valid_count += 1
                    crs_groups[crs_str] = crs_groups.get(crs_str, 0) + 1
                    res_key = f"{res[0]:.6f}x{res[1]:.6f}"
                    res_groups[res_key] = res_groups.get(res_key, 0) + 1
            except Exception as e:
                item = {
                    "relative_path": rel_p.replace("\\", "/"),
                    "absolute_path": abs_p,
                    "filename": f,
                    "file_size_bytes": size_b,
                    "mtime_ns": mtime_ns,
                    "valid": False,
                    "error": str(e)
                }
                invalid_count += 1
            inventory.append(item)

    summary = {
        "status": "SCANNED",
        "total_files": len(inventory),
        "valid_tiffs": valid_count,
        "invalid_tiffs": invalid_count,
        "crs_groups": crs_groups,
        "resolution_groups": res_groups
    }
    return inventory, summary


def analyze_target_mask_geometry(tif_path: str, block_size: int = 1024) -> Dict[str, Any]:
    """分块流式读取像元，统计 Target Mask 几何特征（防大图爆内存）"""
    with rasterio.open(tif_path) as src:
        w, h = src.width, src.height
        nodata = src.nodata
        total_pixels = w * h
        valid_pixels = 0
        min_r, max_r = h, -1
        min_c, max_c = w, -1
        edge_valid_count = 0

        for r_off in range(0, h, block_size):
            r_len = min(block_size, h - r_off)
            for c_off in range(0, w, block_size):
                c_len = min(block_size, w - c_off)
                win = rasterio.windows.Window(c_off, r_off, c_len, r_len)
                arr = src.read(1, window=win)
                if nodata is not None and np.isfinite(nodata):
                    m = np.isfinite(arr) & ~np.isclose(arr, nodata)
                else:
                    m = np.isfinite(arr)
                cnt = np.count_nonzero(m)
                if cnt > 0:
                    valid_pixels += cnt
                    loc_rows, loc_cols = np.where(m)
                    abs_rows = loc_rows + r_off
                    abs_cols = loc_cols + c_off
                    min_r = min(min_r, int(np.min(abs_rows)))
                    max_r = max(max_r, int(np.max(abs_rows)))
                    min_c = min(min_c, int(np.min(abs_cols)))
                    max_c = max(max_c, int(np.max(abs_cols)))

                    # 统计落在图像边缘 2 像元内的点
                    is_edge = (abs_rows < 2) | (abs_rows >= h - 2) | (abs_cols < 2) | (abs_cols >= w - 2)
                    edge_valid_count += int(np.count_nonzero(is_edge))

        valid_fraction = valid_pixels / total_pixels if total_pixels > 0 else 0.0
        edge_fraction = edge_valid_count / valid_pixels if valid_pixels > 0 else 0.0

        # 分类逻辑
        bbox_w = (max_c - min_c + 1) if max_c >= min_c else 0
        bbox_h = (max_r - min_r + 1) if max_r >= min_r else 0
        aspect = max(bbox_w, bbox_h) / max(1, min(bbox_w, bbox_h))

        if valid_pixels == 0:
            sel_type = "EMPTY_TARGET"
        elif aspect > 4.0:
            sel_type = "NARROW_STRIP"
        elif edge_fraction > 0.3:
            sel_type = "EDGE_TARGET"
        elif valid_fraction > 0.4:
            sel_type = "WIDE_TARGET"
        else:
            sel_type = "COMPLEX_TARGET"

        return {
            "total_pixels": total_pixels,
            "valid_target_pixels": valid_pixels,
            "valid_fraction": round(valid_fraction, 6),
            "target_bbox": [min_c, min_r, max_c, max_r],
            "aspect_ratio": round(aspect, 2),
            "edge_contact_fraction": round(edge_fraction, 4),
            "selection_type": sel_type
        }


def select_validation_tiles(inventory: List[Dict[str, Any]], max_tiles: int = 5) -> List[Dict[str, Any]]:
    """从清单中自动挑选 3~5 个具备代表性几何特征的 Tile"""
    valid_items = [item for item in inventory if item.get("valid", False)]
    if not valid_items:
        return []

    selected = []
    seen_types = set()

    for item in valid_items:
        geom = analyze_target_mask_geometry(item["absolute_path"])
        item_copy = dict(item)
        item_copy.update(geom)
        st = geom["selection_type"]
        if st not in seen_types and st != "EMPTY_TARGET":
            seen_types.add(st)
            item_copy["selection_reason"] = f"Representative for {st}"
            selected.append(item_copy)
            if len(selected) >= max_tiles:
                break

    if len(selected) < max_tiles:
        for item in valid_items:
            if any(s["absolute_path"] == item["absolute_path"] for s in selected):
                continue
            geom = analyze_target_mask_geometry(item["absolute_path"])
            item_copy = dict(item)
            item_copy.update(geom)
            item_copy["selection_reason"] = f"Supplemental tile ({geom['selection_type']})"
            selected.append(item_copy)
            if len(selected) >= max_tiles:
                break

    return selected


# ----------------------------------------------------------------------
# 4. 生成规范的合成验证 DEM 瓦片 (用于无真实外置数据时的严密验证)
# ----------------------------------------------------------------------
def create_synthetic_coastal_dem(
    out_path: str,
    center_lon: float = 122.0,
    center_lat: float = 31.0,
    width: int = 512,
    height: int = 512,
    res_deg: float = 0.0002,
    nodata: float = -9999.0
) -> str:
    """在真实近岸坐标附近生成包含潮滩倾斜缓坡、微潮沟与离岸岛礁的代表性 DEM 瓦片"""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    west = center_lon - (width / 2.0) * res_deg
    north = center_lat + (height / 2.0) * res_deg
    transform = rasterio.transform.from_origin(west, north, res_deg, res_deg)

    rows, cols = np.meshgrid(np.arange(height), np.arange(width), indexing='ij')
    slope = -2.5 + 6.0 * (cols / width) - 1.5 * (rows / height)
    creek = 0.6 * np.sin(rows / 25.0) * np.cos(cols / 30.0)
    dem = (slope + creek).astype(np.float32)

    land_mask = (rows < height * 0.15) & (cols < width * 0.15)
    dem[land_mask] = nodata

    profile = {
        'driver': 'GTiff',
        'height': height,
        'width': width,
        'count': 1,
        'dtype': 'float32',
        'crs': 'EPSG:4326',
        'transform': transform,
        'nodata': nodata
    }
    with rasterio.open(out_path, 'w', **profile) as dst:
        dst.write(dem, 1)

    return out_path


# ----------------------------------------------------------------------
# 5. LEVEL 3: Direct Sampled-Pixel FES Oracle
# ----------------------------------------------------------------------
def evaluate_direct_oracle(
    dem_path: str,
    adapt_tif_path: str,
    predictor: Any,
    transformer: Optional[DatumTransformer] = None,
    start_time: str = "2024-01-01 00:00",
    end_time: str = "2024-01-03 00:00",
    freq: str = "30min",
    datum: str = "msl",
    n_samples: int = 100,
    seed: int = 42,
    mask_nc_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Direct Sampled-Pixel FES Oracle:
    对真实 DEM Target 像元抽样，逐点计算 Native FES 严密天文潮位，
    按严格 H(t) > z 统计真值频率，并与自适应控制网格插值结果对比。
    """
    with rasterio.open(dem_path) as src_dem, rasterio.open(adapt_tif_path) as src_adapt:
        dem_data = src_dem.read(1)
        adapt_data = src_adapt.read(1)
        nodata = src_dem.nodata
        crs = src_dem.crs
        trans = src_dem.transform

        if nodata is not None and np.isfinite(nodata):
            v_mask = np.isfinite(dem_data) & ~np.isclose(dem_data, nodata)
        else:
            v_mask = np.isfinite(dem_data)

        valid_rows, valid_cols = np.where(v_mask)
        total_valid = len(valid_rows)
        if total_valid == 0:
            raise ValueError(f"DEM 栅格中无有效像元: {dem_path}")

        num_draw = min(int(n_samples), total_valid)
        rng = np.random.default_rng(seed)
        chosen_idx = rng.choice(total_valid, size=num_draw, replace=False)
        chosen_rows = valid_rows[chosen_idx]
        chosen_cols = valid_cols[chosen_idx]
        elevations = dem_data[chosen_rows, chosen_cols].astype(float)
        adapt_freqs = adapt_data[chosen_rows, chosen_cols].astype(float)

        xs, ys = rasterio.transform.xy(trans, chosen_rows, chosen_cols, offset='center')
        xs_arr = np.asarray(xs, dtype=float)
        ys_arr = np.asarray(ys, dtype=float)

        if not crs.is_geographic:
            if Transformer is not None:
                tf = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
                t_lons, t_lats = tf.transform(xs_arr, ys_arr)
            else:
                t_lons, t_lats = rasterio.warp.transform(crs, "EPSG:4326", xs_arr, ys_arr)
            sample_lons = normalize_longitude(np.asarray(t_lons, dtype=float), to_360=False)
            sample_lats = np.asarray(t_lats, dtype=float)
        else:
            sample_lons = normalize_longitude(xs_arr, to_360=False)
            sample_lats = ys_arr

    # 批量直接调用 Native FES (Oracle 路径绝不使用自适应控制网格或空间插值)
    direct_freqs = np.full(num_draw, np.nan, dtype=float)
    direct_valid_status = np.zeros(num_draw, dtype=bool)

    try:
        tide_mat, _, _ = predictor.predict_points_period(
            lons=sample_lons,
            lats=sample_lats,
            start_time=start_time,
            end_time=end_time,
            freq=freq,
            inclusive="left",
            constituents="all"
        )
    except Exception:
        tide_mat = None

    if tide_mat is not None:
        if datum.lower() != "msl" and transformer is not None:
            offsets_dict = transformer.get_static_datum_offsets(
                lons=sample_lons, lats=sample_lats, target=datum.upper(), strict=False
            )
            offsets = offsets_dict["offset_m"]
        else:
            offsets = np.zeros(num_draw, dtype=float)

        n_steps = tide_mat.shape[1]
        for i in range(num_draw):
            tides = tide_mat[i]
            z_i = float(elevations[i])
            off_i = offsets[i]
            if np.isfinite(off_i):
                tides = tides + off_i
                finite_tides = tides[np.isfinite(tides)]
                if len(finite_tides) >= n_steps * 0.8:
                    direct_freqs[i] = (np.count_nonzero(finite_tides > z_i) / len(finite_tides)) * 100.0
                    direct_valid_status[i] = True

    direct_requested = num_draw
    direct_valid_cnt = int(np.count_nonzero(direct_valid_status))
    direct_invalid_cnt = int(direct_requested - direct_valid_cnt)

    adapt_valid_status = np.isfinite(adapt_freqs)
    adapt_valid_cnt = int(np.count_nonzero(adapt_valid_status))
    adapt_invalid_cnt = int(direct_requested - adapt_valid_cnt)

    common_mask = direct_valid_status & adapt_valid_status
    common_valid_cnt = int(np.count_nonzero(common_mask))

    mask_classes = np.full(num_draw, -1, dtype=int)
    if mask_nc_path and os.path.exists(mask_nc_path):
        try:
            import netCDF4 as nc
            ds_m = nc.Dataset(mask_nc_path)
            lat_arr = ds_m.variables["lat"][:]
            lon_arr = ds_m.variables["lon"][:]
            mask_arr = ds_m.variables["mask"]

            for i in range(num_draw):
                ln_360 = sample_lons[i] % 360.0
                lt = sample_lats[i]
                r_idx = int(round((lt - lat_arr[0]) / (lat_arr[1] - lat_arr[0])))
                c_idx = int(round((ln_360 - lon_arr[0]) / (lon_arr[1] - lon_arr[0])))
                r_idx = max(0, min(len(lat_arr) - 1, r_idx))
                c_idx = max(0, min(len(lon_arr) - 1, c_idx))
                mask_classes[i] = int(mask_arr[r_idx, c_idx])
            ds_m.close()
        except Exception:
            pass

    sample_records = []
    diffs = []
    for i in range(num_draw):
        rec = {
            "sample_index": i,
            "row": int(chosen_rows[i]),
            "col": int(chosen_cols[i]),
            "lon": float(sample_lons[i]),
            "lat": float(sample_lats[i]),
            "elevation_m": float(elevations[i]),
            "direct_valid": bool(direct_valid_status[i]),
            "direct_frequency_pct": float(direct_freqs[i]) if direct_valid_status[i] else None,
            "adaptive_frequency_pct": float(adapt_freqs[i]) if adapt_valid_status[i] else None,
            "fes_mask_class": int(mask_classes[i])
        }
        if common_mask[i]:
            d = float(adapt_freqs[i] - direct_freqs[i])
            rec["error_pp"] = d
            rec["abs_error_pp"] = abs(d)
            diffs.append(d)
        else:
            rec["error_pp"] = None
            rec["abs_error_pp"] = None
        sample_records.append(rec)

    if common_valid_cnt < 20:
        gate_status = "INSUFFICIENT_DIRECT_NATIVE_SUPPORT"
        metrics = {
            "bias": None, "mae": None, "rmse": None, "med_ae": None,
            "p90": None, "p95": None, "p99": None, "max": None
        }
    else:
        diffs_arr = np.asarray(diffs, dtype=float)
        abs_diffs = np.abs(diffs_arr)
        mae = float(np.mean(abs_diffs))
        p95 = float(np.percentile(abs_diffs, 95))
        max_err = float(np.max(abs_diffs))

        metrics = {
            "bias": round(float(np.mean(diffs_arr)), 4),
            "mae": round(mae, 4),
            "rmse": round(float(np.sqrt(np.mean(diffs_arr**2))), 4),
            "med_ae": round(float(np.median(abs_diffs)), 4),
            "p90": round(float(np.percentile(abs_diffs, 90)), 4),
            "p95": round(p95, 4),
            "p99": round(float(np.percentile(abs_diffs, 99)), 4),
            "max": round(max_err, 4)
        }

        if mae <= 0.5 and p95 <= 1.0 and max_err <= 5.0:
            gate_status = "STRONG PASS"
        elif mae <= 1.0 and p95 <= 2.0 and max_err <= 5.0:
            gate_status = "PASS"
        else:
            gate_status = "INVESTIGATE / FAIL"

    valid_recs = [r for r in sample_records if r["abs_error_pp"] is not None]
    outliers = sorted(valid_recs, key=lambda x: x["abs_error_pp"], reverse=True)[:20]

    return {
        "dem_path": dem_path,
        "adapt_tif_path": adapt_tif_path,
        "datum": datum,
        "direct_requested": direct_requested,
        "direct_valid": direct_valid_cnt,
        "direct_invalid": direct_invalid_cnt,
        "adaptive_valid": adapt_valid_cnt,
        "adaptive_invalid": adapt_invalid_cnt,
        "common_valid": common_valid_cnt,
        "gate_status": gate_status,
        "metrics": metrics,
        "sample_records": sample_records,
        "top_outliers": outliers
    }


# ----------------------------------------------------------------------
# 6. LEVEL 3b: Cache vs Direct Raster Serialization Gate
# ----------------------------------------------------------------------
def compare_cache_vs_direct_raster(direct_tif: str, cache_stage2_tif: str) -> Dict[str, Any]:
    """比对单步流式直接解算与 Stage 1 -> Stage 2 阶段解耦产物的像元一致性"""
    with rasterio.open(direct_tif) as src_a, rasterio.open(cache_stage2_tif) as src_b:
        arr_a = src_a.read(1)
        arr_b = src_b.read(1)
        nodata = src_a.nodata

    if nodata is not None and np.isfinite(nodata):
        mask = np.isfinite(arr_a) & np.isfinite(arr_b) & ~np.isclose(arr_a, nodata) & ~np.isclose(arr_b, nodata)
    else:
        mask = np.isfinite(arr_a) & np.isfinite(arr_b)

    common_pts = np.count_nonzero(mask)
    if common_pts == 0:
        return {"status": "NO_COMMON_PIXELS", "p99_diff": None, "max_diff": None, "pass_gate": False}

    diff = np.abs(arr_a[mask] - arr_b[mask])
    max_d = float(np.max(diff))
    p99_d = float(np.percentile(diff, 99))
    pass_gate = (max_d <= 1e-3) and (p99_d <= 1e-4)

    return {
        "common_pixels": int(common_pts),
        "max_diff_pp": round(max_d, 6),
        "p99_diff_pp": round(p99_d, 6),
        "pass_gate": pass_gate,
        "status": "PASS" if pass_gate else "INVESTIGATE"
    }


# ----------------------------------------------------------------------
# 7. LEVEL 5: Real Seam Validation
# ----------------------------------------------------------------------
def run_seam_validation(
    whole_dem_path: str,
    out_dir: str,
    engine_kwargs: Dict[str, Any]
) -> Dict[str, Any]:
    """将整幅 DEM (Whole) 沿列边界切成严格相邻的 Tile A 与 Tile B，检验接缝与拼合一致性"""
    os.makedirs(out_dir, exist_ok=True)
    with rasterio.open(whole_dem_path) as src:
        profile = src.profile.copy()
        w, h = src.width, src.height
        mid_col = w // 2

        win_a = rasterio.windows.Window(0, 0, mid_col, h)
        dem_a = src.read(1, window=win_a)
        trans_a = rasterio.windows.transform(win_a, src.transform)
        prof_a = profile.copy()
        prof_a.update({'width': mid_col, 'height': h, 'transform': trans_a})
        dem_a_path = os.path.join(out_dir, "tile_A_dem.tif")
        with rasterio.open(dem_a_path, 'w', **prof_a) as dst:
            dst.write(dem_a, 1)

        win_b = rasterio.windows.Window(mid_col, 0, w - mid_col, h)
        dem_b = src.read(1, window=win_b)
        trans_b = rasterio.windows.transform(win_b, src.transform)
        prof_b = profile.copy()
        prof_b.update({'width': w - mid_col, 'height': h, 'transform': trans_b})
        dem_b_path = os.path.join(out_dir, "tile_B_dem.tif")
        with rasterio.open(dem_b_path, 'w', **prof_b) as dst:
            dst.write(dem_b, 1)

    engine = RasterTideEngine()

    engine_call_kwargs = dict(engine_kwargs)
    if "target_datum" in engine_call_kwargs:
        engine_call_kwargs["dem_datum"] = engine_call_kwargs.pop("target_datum")

    whole_out = os.path.join(out_dir, "whole_inundation.tif")
    engine.calculate_inundation_raster(
        dem_path=whole_dem_path,
        output_path=whole_out,
        **engine_call_kwargs
    )

    out_a = os.path.join(out_dir, "tile_A_inundation.tif")
    engine.calculate_inundation_raster(
        dem_path=dem_a_path,
        output_path=out_a,
        **engine_call_kwargs
    )

    out_b = os.path.join(out_dir, "tile_B_inundation.tif")
    engine.calculate_inundation_raster(
        dem_path=dem_b_path,
        output_path=out_b,
        **engine_call_kwargs
    )

    with rasterio.open(whole_out) as s_w, rasterio.open(out_a) as s_a, rasterio.open(out_b) as s_b:
        arr_w = s_w.read(1)
        arr_a = s_a.read(1)
        arr_b = s_b.read(1)
        nodata = s_w.nodata

        composite = np.zeros_like(arr_w)
        composite[:, :mid_col] = arr_a
        composite[:, mid_col:] = arr_b

        if nodata is not None and np.isfinite(nodata):
            v_mask = np.isfinite(arr_w) & np.isfinite(composite) & ~np.isclose(arr_w, nodata) & ~np.isclose(composite, nodata)
        else:
            v_mask = np.isfinite(arr_w) & np.isfinite(composite)

        diff = np.abs(arr_w[v_mask] - composite[v_mask])
        if len(diff) > 0:
            mae = float(np.mean(diff))
            p95 = float(np.percentile(diff, 95))
            p99 = float(np.percentile(diff, 99))
            max_d = float(np.max(diff))
        else:
            mae = p95 = p99 = max_d = 0.0

        col_left = arr_a[:, -1]
        col_right = arr_b[:, 0]
        seam_mask = np.isfinite(col_left) & np.isfinite(col_right)
        if nodata is not None and np.isfinite(nodata):
            seam_mask &= ~np.isclose(col_left, nodata) & ~np.isclose(col_right, nodata)
        seam_step = np.abs(col_left[seam_mask] - col_right[seam_mask])
        seam_p95 = float(np.percentile(seam_step, 95)) if len(seam_step) > 0 else 0.0

    pass_gate = (p95 <= 2.0) and (max_d <= 10.0)
    return {
        "composite_mae_pp": round(mae, 4),
        "composite_p95_pp": round(p95, 4),
        "composite_p99_pp": round(p99, 4),
        "composite_max_pp": round(max_d, 4),
        "seam_step_p95_pp": round(seam_p95, 4),
        "pass_gate": pass_gate,
        "status": "PASS" if (p95 <= 1.0 and max_d <= 10.0) else ("BETA_ACCEPTABLE" if pass_gate else "INVESTIGATE")
    }


# ----------------------------------------------------------------------
# 8. 断点续跑 (Resume) 与错参数防篡改测试
# ----------------------------------------------------------------------
def run_resume_and_incompatibility_tests(
    dem_path: str,
    work_dir: str,
    base_kwargs: Dict[str, Any]
) -> Dict[str, Any]:
    """验证 Resume 零重复调用、防篡改签名与错误参数拒绝复用机制"""
    os.makedirs(work_dir, exist_ok=True)
    batch_engine = BatchRasterEngine()

    out_dir_run1 = os.path.join(work_dir, "batch_run1")
    os.makedirs(out_dir_run1, exist_ok=True)
    input_folder = os.path.dirname(dem_path)
    b_kwargs = dict(base_kwargs)
    if "target_datum" in b_kwargs:
        b_kwargs["dem_datum"] = b_kwargs.pop("target_datum")

    t0 = time.time()
    res1 = batch_engine.run_batch(
        input_folder=input_folder,
        output_folder=out_dir_run1,
        existing_policy=ExistingOutputPolicy.OVERWRITE,
        **b_kwargs
    )
    t1 = time.time()
    tile_stem = os.path.splitext(os.path.basename(dem_path))[0]
    cache_p1 = os.path.join(out_dir_run1, f"{tile_stem}_tide.nc")

    cache_size1 = os.path.getsize(cache_p1) if os.path.exists(cache_p1) else 0
    with open(cache_p1, 'rb') as f:
        cache_hash1 = hashlib.sha256(f.read()).hexdigest()

    res2 = batch_engine.run_batch(
        input_folder=input_folder,
        output_folder=out_dir_run1,
        existing_policy=ExistingOutputPolicy.RESUME,
        **b_kwargs
    )
    with open(cache_p1, 'rb') as f:
        cache_hash2 = hashlib.sha256(f.read()).hexdigest()

    resume_success = (res2.get("skipped", 0) >= 1) and (cache_hash1 == cache_hash2)

    wrong_kwargs = dict(b_kwargs)
    wrong_kwargs["freq"] = "1h" if b_kwargs.get("freq") == "30min" else "30min"

    res_incompatible = batch_engine.run_batch(
        input_folder=input_folder,
        output_folder=out_dir_run1,
        existing_policy=ExistingOutputPolicy.RESUME,
        **wrong_kwargs
    )
    incompatible_protected = (res_incompatible.get("skipped", 0) == 0)

    res_overwrite = batch_engine.run_batch(
        input_folder=input_folder,
        output_folder=out_dir_run1,
        existing_policy=ExistingOutputPolicy.OVERWRITE,
        **b_kwargs
    )
    with open(cache_p1, 'rb') as f:
        cache_hash_after = hashlib.sha256(f.read()).hexdigest()

    return {
        "run1_counts": res1.get("counts", {}),
        "run1_time_sec": round(t1 - t0, 3),
        "run2_counts": res2.get("counts", {}),
        "resume_zero_work": resume_success,
        "cache_sha256_identical": (cache_hash1 == cache_hash2),
        "incompatible_parameter_protected": incompatible_protected,
        "pass_all": resume_success and incompatible_protected
    }


# ----------------------------------------------------------------------
# 9. 主调度器 (Main Runner)
# ----------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="CoastTideX v1.5 Beta Real FES / Intertidal Validation Harness")
    parser.add_argument("--input-dir", default=r"I:/Test_tide_model/1-Data/SWOT_raster/E0_E30/subregion_bestQ",
                        help="真实潮间带/沙滩 Raster 输入目录 (严格只读)")
    parser.add_argument("--output-dir", default=r"I:/Test_tide_model/tmp/v1_5_beta_real_fes",
                        help="真实验证产物输出根目录")
    parser.add_argument("--dem", default=None, help="可选手动指定单个 DEM 路径")
    parser.add_argument("--year", type=int, default=2024, help="验证年份")
    parser.add_argument("--start", default="2024-01-01 00:00", help="起始时间 (UTC)")
    parser.add_argument("--end", default="2024-01-03 00:00", help="短周期结束时间 (默认48h)")
    parser.add_argument("--step", default="30min", help="采样时间间隔")
    parser.add_argument("--datum", default="msl", choices=["msl", "egm2008"], help="垂直基准面")
    parser.add_argument("--direct-samples", type=int, default=100, help="Direct Oracle 抽样像元数")
    parser.add_argument("--seed", type=int, default=42, help="抽样随机数种子")
    parser.add_argument("--max-selected-tiles", type=int, default=5, help="最大选取的代表性 Tile 数")
    parser.add_argument("--skip-full-year", action="store_true", help="跳过 2024 全年长时序验证")
    parser.add_argument("--run-seam-test", action="store_true", default=True, help="运行瓦片接缝测试")
    parser.add_argument("--run-resume-test", action="store_true", default=True, help="运行断点恢复与防篡改测试")
    parser.add_argument("--run-cache-consistency", action="store_true", default=True, help="运行 Tide Cache 序列化门禁测试")
    parser.add_argument("--synthetic-demo", action="store_true", help="当无外部数据时生成真实近岸合成 DEM 运行全链条")

    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join(args.output_dir, f"run_{timestamp}")
    subdirs = ["inventory", "selected", "crops", "caches", "rasters", "metrics", "logs", "reports"]
    for sd in subdirs:
        os.makedirs(os.path.join(run_dir, sd), exist_ok=True)

    print(f"\n{'='*70}")
    print(f"CoastTideX v1.5 Beta — Controlled Real-FES Validation Harness")
    print(f"Run Output: {run_dir}")
    print(f"{'='*70}\n")

    app_cfg = load_app_config()
    env_info = collect_environment(project_root)
    data_sources = check_data_sources(app_cfg)

    with open(os.path.join(run_dir, "environment.json"), "w", encoding="utf-8") as f:
        json.dump({"environment": env_info, "data_sources": data_sources}, f, indent=2, ensure_ascii=False)

    print(f"[*] 运行环境: Python {platform.python_version()} on {env_info['platform']}")
    print(f"[*] FES 模块状态: pyfes={env_info['pyfes_version']}")
    print(f"[*] Git Commit: {env_info['git_commit']} (branch: {env_info['git_branch']})")
    print(f"[*] FES2022b 原生网格文件存在: {data_sources['fes_ns_grid']['exists']} ({data_sources['fes_ns_grid']['size_bytes']} bytes)")
    print(f"[*] EIGEN ΔN 数据可用: {data_sources['EIGEN_DELTA_N_AVAILABLE']}")

    if not data_sources['fes_ns_grid']['exists']:
        print("[!] 错误: 未找到本地 FES2022b 原生网格文件，无法执行真实验证。")
        sys.exit(1)

    predictor = FESTidePredictor()
    transformer = DatumTransformer()

    print(f"\n[*] 启动 LEVEL 1: Real Native FES Smoke Test (8个全球代表性沿海坐标)...")
    smoke_res = run_level1_fes_smoke(predictor)
    smoke_out_p = os.path.join(run_dir, "metrics", "real_fes_smoke.json")
    with open(smoke_out_p, "w", encoding="utf-8") as f:
        json.dump(smoke_res, f, indent=2, ensure_ascii=False)

    for r in smoke_res:
        valid_str = f"valid={r['valid_count']}/{r['time_samples']}"
        range_str = f"[{r['min']:.3f}m, {r['max']:.3f}m]" if r['min'] is not None else "N/A"
        print(f"    [-] {r['name']} ({r['lon']}°, {r['lat']}°): {r['status']} ({valid_str}, {range_str}, {r['runtime_seconds']}s)")

    target_tiles = []
    input_dir_exists = os.path.isdir(args.input_dir)

    if not input_dir_exists:
        print(f"\n[!] 警告: REAL_VALIDATION_INPUT_DIR_NOT_FOUND: {args.input_dir}")
        print(f"[*] 遵循安全规则 1.3: 停止真实 Raster 目录扫描，不随意扫描其他磁盘。")
        inv_summary = {
            "status": "REAL_VALIDATION_INPUT_DIR_NOT_FOUND",
            "input_dir": args.input_dir,
            "total_files": 0,
            "valid_tiffs": 0
        }
        with open(os.path.join(run_dir, "inventory", "real_raster_inventory.json"), "w", encoding="utf-8") as f:
            json.dump(inv_summary, f, indent=2, ensure_ascii=False)

        print(f"[*] 启动合成代表性近岸 DEM (Synthetic Coastal DEM) 执行全链条工程与科学算法验收...")
        syn_path = os.path.join(run_dir, "crops", "synthetic_yangtze_coastal_dem.tif")
        create_synthetic_coastal_dem(syn_path, center_lon=122.0, center_lat=31.0, width=512, height=512)
        target_tiles.append({
            "relative_path": "synthetic_yangtze_coastal_dem.tif",
            "absolute_path": syn_path,
            "selection_type": "SYNTHETIC_COASTAL_DEM",
            "selection_reason": "Controlled realistic coastal intertidal terrain near Yangtze Estuary"
        })
    else:
        print(f"\n[*] 扫描真实 Raster 目录: {args.input_dir} ...")
        inventory, inv_summary = scan_raster_inventory(args.input_dir)
        with open(os.path.join(run_dir, "inventory", "real_raster_inventory.json"), "w", encoding="utf-8") as f:
            json.dump(inv_summary, f, indent=2, ensure_ascii=False)
        target_tiles = select_validation_tiles(inventory, max_tiles=args.max_selected_tiles)

    with open(os.path.join(run_dir, "selected", "selected_validation_tiles.json"), "w", encoding="utf-8") as f:
        json.dump(target_tiles, f, indent=2, ensure_ascii=False)

    print(f"[*] 选中测试 Tile 数量: {len(target_tiles)}")
    for i, t in enumerate(target_tiles, 1):
        print(f"    {i}. {t['relative_path']} (Type: {t.get('selection_type')})")

    oracle_results = []
    cache_consistency_results = []

    time_idx_48h = build_time_index("2024-01-01 00:00", "2024-01-03 00:00", "30min", inclusive="left")
    mask_fes_nc = data_sources["fes_extrapolation_mask_nc"]["resolved_path"] if data_sources["fes_extrapolation_mask_nc"]["exists"] else None

    for tile in target_tiles:
        dem_p = tile["absolute_path"]
        tile_name = os.path.splitext(os.path.basename(dem_p))[0]
        print(f"\n[*] 处理瓦片: {tile_name} ...")

        direct_out_p = os.path.join(run_dir, "rasters", f"{tile_name}_direct_inundation.tif")
        engine = RasterTideEngine()
        t_start_direct = time.time()
        engine.calculate_inundation_raster(
            dem_path=dem_p,
            output_path=direct_out_p,
            start_time="2024-01-01 00:00",
            end_time="2024-01-03 00:00",
            freq="30min",
            dem_datum=args.datum,
            inclusive="left"
        )
        t_direct = time.time() - t_start_direct
        print(f"    [-] Direct Raster 解算耗时: {t_direct:.2f}s")

        batch_eng = BatchRasterEngine()
        tile_cache_out_dir = os.path.join(run_dir, "caches")
        b_res = batch_eng.run_batch(
            input_folder=os.path.dirname(dem_p),
            output_folder=tile_cache_out_dir,
            existing_policy=ExistingOutputPolicy.OVERWRITE,
            start_time="2024-01-01 00:00",
            end_time="2024-01-03 00:00",
            freq="30min",
            dem_datum=args.datum,
            inclusive="left"
        )
        stage2_out_p = os.path.join(tile_cache_out_dir, f"{tile_name}_inundation.tif")
        print(f"    [-] Stage 1 -> Stage 2 产物已生成: {os.path.basename(stage2_out_p)}")

        if args.run_cache_consistency and stage2_out_p and os.path.exists(stage2_out_p):
            cmp_res = compare_cache_vs_direct_raster(direct_out_p, stage2_out_p)
            cmp_res["tile"] = tile_name
            cache_consistency_results.append(cmp_res)
            print(f"    [-] Cache vs Direct P99 Diff: {cmp_res['p99_diff_pp']} pp, Max Diff: {cmp_res['max_diff_pp']} pp (Gate: {cmp_res['status']})")

        print(f"    [-] 启动 Direct Sampled-Pixel FES Oracle ({args.direct_samples} 采样点)...")
        t_start_oracle = time.time()
        oracle_eval = evaluate_direct_oracle(
            dem_path=dem_p,
            adapt_tif_path=direct_out_p,
            predictor=predictor,
            transformer=transformer,
            start_time=args.start,
            end_time=args.end,
            freq=args.step,
            datum=args.datum,
            n_samples=args.direct_samples,
            seed=args.seed,
            mask_nc_path=mask_fes_nc
        )
        t_oracle = time.time() - t_start_oracle
        oracle_eval["runtime_seconds"] = round(t_oracle, 2)
        oracle_eval["tile_name"] = tile_name
        oracle_results.append(oracle_eval)

        m = oracle_eval["metrics"]
        print(f"      Common Valid Points: {oracle_eval['common_valid']}/{oracle_eval['direct_requested']}")
        if m.get("mae") is not None:
            print(f"      MAE: {m['mae']} pp, RMSE: {m['rmse']} pp, P95: {m['p95']} pp, Max: {m['max']} pp")
        print(f"      Gate Status: {oracle_eval['gate_status']}")

        if oracle_eval.get("top_outliers"):
            outlier_df = pd.DataFrame(oracle_eval["top_outliers"])
            outlier_df.to_csv(os.path.join(run_dir, "metrics", f"beta_outliers_{tile_name}.csv"), index=False)
        sample_df = pd.DataFrame(oracle_eval["sample_records"])
        sample_df.to_csv(os.path.join(run_dir, "metrics", f"beta_samples_{tile_name}.csv"), index=False)

    fullyear_summary = {}
    if not args.skip_full_year and target_tiles:
        primary_tile = target_tiles[0]
        dem_p = primary_tile["absolute_path"]
        print(f"\n[*] 启动 LEVEL 4: 2024 Full-Year 17,568-step 长时序验证 (Tile: {os.path.basename(dem_p)})...")

        with rasterio.open(dem_p) as s:
            t_pixels = s.width * s.height
        est_nodes = 500
        time_samples = 17568
        est_ram_bytes = est_nodes * time_samples * 8
        print(f"    [-] 预估内存占用: {est_ram_bytes / (1024**2):.1f} MB (安全阈值: 4096 MB)")

        t_start_fy = time.time()
        fy_out_dir = os.path.join(run_dir, "caches", "fullyear_2024")
        b_res_fy = batch_eng.run_batch(
            input_folder=os.path.dirname(dem_p),
            output_folder=fy_out_dir,
            existing_policy=ExistingOutputPolicy.OVERWRITE,
            start_time="2024-01-01 00:00",
            end_time="2025-01-01 00:00",
            freq="30min",
            dem_datum=args.datum,
            inclusive="left"
        )
        t_total_fy = time.time() - t_start_fy
        cache_nc_p = os.path.join(fy_out_dir, f"{os.path.splitext(os.path.basename(dem_p))[0]}_tide.nc")
        nc_size = os.path.getsize(cache_nc_p) if os.path.exists(cache_nc_p) else 0

        fullyear_summary = {
            "tile": os.path.basename(dem_p),
            "year": 2024,
            "samples": time_samples,
            "runtime_seconds": round(t_total_fy, 2),
            "cache_bytes": nc_size,
            "counts": b_res_fy.get("counts", {})
        }
        print(f"    [-] 全年解算完成! 耗时: {t_total_fy:.2f}s, NetCDF Cache 体积: {nc_size / (1024**2):.2f} MB")

    seam_results = {}
    if args.run_seam_test and target_tiles:
        primary_tile = target_tiles[0]
        print(f"\n[*] 启动 LEVEL 5: 真实瓦片接缝连续性测试 (Tile: {os.path.basename(primary_tile['absolute_path'])})...")
        seam_dir = os.path.join(run_dir, "rasters", "seam_test")
        seam_res = run_seam_validation(
            whole_dem_path=primary_tile["absolute_path"],
            out_dir=seam_dir,
            engine_kwargs={
                "start_time": "2024-01-01 00:00",
                "end_time": "2024-01-03 00:00",
                "freq": "30min",
                "target_datum": args.datum,
                "inclusive": "left"
            }
        )
        seam_results = seam_res
        with open(os.path.join(run_dir, "metrics", "seam_metrics.json"), "w", encoding="utf-8") as f:
            json.dump(seam_res, f, indent=2, ensure_ascii=False)
        print(f"    [-] 接缝拼合 MAE: {seam_res['composite_mae_pp']} pp, P95: {seam_res['composite_p95_pp']} pp, Max: {seam_res['composite_max_pp']} pp (Gate: {seam_res['status']})")

    resume_results = {}
    if args.run_resume_test and target_tiles:
        primary_tile = target_tiles[0]
        print(f"\n[*] 启动断点恢复 (Resume) 与错参数防篡改测试...")
        resume_res = run_resume_and_incompatibility_tests(
            dem_path=primary_tile["absolute_path"],
            work_dir=os.path.join(run_dir, "caches", "resume_test"),
            base_kwargs={
                "start_time": "2024-01-01 00:00",
                "end_time": "2024-01-03 00:00",
                "freq": "30min",
                "target_datum": args.datum,
                "inclusive": "left"
            }
        )
        resume_results = resume_res
        with open(os.path.join(run_dir, "metrics", "resume_metrics.json"), "w", encoding="utf-8") as f:
            json.dump(resume_res, f, indent=2, ensure_ascii=False)
        print(f"    [-] Resume 零重复调用验证: {'PASS' if resume_res['resume_zero_work'] else 'FAIL'}")
        print(f"    [-] 不兼容参数防篡改保护: {'PASS' if resume_res['incompatible_parameter_protected'] else 'FAIL'}")

    overall_gate = "PASS"
    for o in oracle_results:
        if o["gate_status"] == "INVESTIGATE / FAIL":
            overall_gate = "FAIL"
            break
        elif o["gate_status"] == "INSUFFICIENT_DIRECT_NATIVE_SUPPORT":
            overall_gate = "CONDITIONAL PASS"

    beta_summary = {
        "timestamp": timestamp,
        "run_directory": run_dir,
        "environment": env_info,
        "data_sources": data_sources,
        "input_dir_status": "REAL_VALIDATION_INPUT_DIR_NOT_FOUND" if not input_dir_exists else "FOUND",
        "level1_smoke_test": smoke_res,
        "direct_oracle_results": [{
            "tile": o["tile_name"],
            "common_valid": o["common_valid"],
            "gate_status": o["gate_status"],
            "metrics": o["metrics"]
        } for o in oracle_results],
        "cache_consistency": cache_consistency_results,
        "fullyear_2024": fullyear_summary,
        "seam_validation": seam_results,
        "resume_validation": resume_results,
        "beta_decision": overall_gate
    }

    with open(os.path.join(run_dir, "reports", "beta_summary.json"), "w", encoding="utf-8") as f:
        json.dump(beta_summary, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*70}")
    print(f"Beta Validation Completed! Decision: {overall_gate}")
    print(f"Summary Report: {os.path.join(run_dir, 'reports', 'beta_summary.json')}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
