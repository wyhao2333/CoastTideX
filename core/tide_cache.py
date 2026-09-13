"""
CoastTideX 控制网格潮汐缓存模块 (Tide Cache Module v1.5 Alpha)
支持自适应四叉树控制节点潮位时序的高效 NetCDF4 序列化、流式压缩存储、完整性校验与
基于 Tide Cache 的二阶段天文潮潜在淹没频率解算 (零 FES 重复调用)。
"""

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any, Callable
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
    QC_NODATA, RasterCalculationCancelled
)
from .utils import compute_inundation_frequency

COASTTIDEX_VERSION = "1.5-alpha"
CACHE_SCHEMA_VERSION = "1.0"


def estimate_tide_cache_size(node_count: int, time_samples: int) -> Dict[str, Any]:
    """
    估算 Tide Cache 控制节点潮位数组原始内存与未压缩数据量 (Raw Array Estimate)。

    参数:
        node_count: 控制节点数量
        time_samples: 时间序列采样步数

    返回:
        Dict 包含 raw_bytes, raw_mb, formatted_size 等。
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


def write_tide_cache(
    cache_path: str,
    info: RasterInfo,
    leaf_cells: List[QuadCell],
    node_cache: Dict[Tuple[int, int], ControlNode],
    time_index: pd.DatetimeIndex,
    metadata: Dict[str, Any],
    cancel_event = None
) -> str:
    """
    将自适应控制网格及其节点潮位时序原子级写入 NetCDF4 Tide Cache (*_tide.nc)。

    存储结构与优化:
        1. 节点与时间二维矩阵: tide_msl_m[node, time] (float32, chunked, zlib=4, shuffle=True)；
        2. 仅保存基准面偏移 static_offset_m 与 MSL 潮位，杜绝内存/磁盘翻倍存储 sorted 冗余数据；
        3. 记录叶单元空间包围盒与四角控制节点索引，支持二阶段零 FES 调用快速空间拓扑重建；
        4. 写入 *.tmp.nc 临时文件，完成后注入 CACHE_COMPLETE 标记并原子重命名。
    """
    if cancel_event is not None and cancel_event.is_set():
        raise RasterCalculationCancelled("用户取消了 Tide Cache 写入。")

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

    # 提取时间轴 (Unix epoch seconds)
    time_epochs = (time_index.astype("int64") // 10**9).to_numpy(dtype=np.float64)

    try:
        with netCDF4.Dataset(tmp_cache_path, mode="w", format="NETCDF4") as ds:
            # 1. 定义维度
            ds.createDimension("node", num_nodes)
            ds.createDimension("time", num_times)
            ds.createDimension("cell", num_cells)
            ds.createDimension("bounds_dim", 4)
            ds.createDimension("corners_dim", 4)

            # 2. 写入全局元数据
            ds.setncattr("COASTTIDEX_VERSION", COASTTIDEX_VERSION)
            ds.setncattr("CACHE_SCHEMA_VERSION", CACHE_SCHEMA_VERSION)
            ds.setncattr("SOURCE_RASTER_NAME", os.path.basename(info.path))
            ds.setncattr("SOURCE_RASTER_PATH", str(info.path))
            ds.setncattr("SOURCE_WIDTH", int(info.width))
            ds.setncattr("SOURCE_HEIGHT", int(info.height))
            ds.setncattr("SOURCE_CRS", str(info.crs))
            ds.setncattr("SOURCE_TRANSFORM", str(list(info.transform)[:6]))
            ds.setncattr("SOURCE_NODATA", float(info.nodata) if info.nodata is not None else np.nan)
            ds.setncattr("TIME_START", str(metadata.get("start_time", time_index[0].isoformat())))
            ds.setncattr("TIME_END", str(metadata.get("end_time", time_index[-1].isoformat())))
            ds.setncattr("TIME_STEP", str(metadata.get("freq", "30min")))
            ds.setncattr("TIMEZONE", str(metadata.get("source_tz", "UTC")))
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

            # 3. 创建并写入时间变量
            var_time = ds.createVariable("time", "f8", ("time",))
            var_time.units = "seconds since 1970-01-01 00:00:00 UTC"
            var_time.calendar = "proleptic_gregorian"
            var_time[:] = time_epochs

            # 4. 创建并写入控制节点坐标与属性变量
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

            # 5. 创建并写入潮位时序大矩阵 (分块压缩写入，防止内存超载)
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

            # 流式分块将各节点的潮位数组写入 NetCDF
            tide_block = np.full((chunk_nodes, num_times), np.nan, dtype=np.float32)
            block_fill = 0
            block_start_idx = 0

            for idx, node in enumerate(all_nodes):
                if cancel_event is not None and cancel_event.is_set():
                    raise RasterCalculationCancelled("用户取消了 Tide Cache 写入。")

                if getattr(node, "tide_msl_raw", None) is not None and len(node.tide_msl_raw) == num_times:
                    tide_block[block_fill, :] = node.tide_msl_raw
                elif node.valid and len(node.water_levels_sorted) == num_times:
                    # 若 raw tide 未留存，使用 sorted water levels 减去 static offset 兜底
                    tide_block[block_fill, :] = (node.water_levels_sorted - node.static_offset_m)
                else:
                    tide_block[block_fill, :] = np.nan

                block_fill += 1

                if block_fill == chunk_nodes or idx == num_nodes - 1:
                    var_tide[block_start_idx:block_start_idx + block_fill, :] = tide_block[:block_fill, :]
                    block_start_idx = idx + 1
                    block_fill = 0

            # 6. 创建并写入四叉树叶单元信息
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

            # 7. 写入完成标识
            ds.setncattr("CACHE_COMPLETE", "true")

        # 原子重命名为正式缓存文件
        if os.path.exists(cache_path):
            try:
                os.remove(cache_path)
            except Exception:
                pass
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
    在内存中根据 tide_msl + static_offset 原地构造各节点的升序水位序列，用于 CCDF 快速二分检索。
    """
    if not os.path.exists(cache_path):
        raise FileNotFoundError(f"未找到指定的 Tide Cache 文件: {cache_path}")

    with netCDF4.Dataset(cache_path, mode="r") as ds:
        is_complete = str(getattr(ds, "CACHE_COMPLETE", "false")).lower() == "true"
        if not is_complete:
            raise ValueError(f"Tide Cache 文件未完整写入 (缺少 CACHE_COMPLETE 标记): {cache_path}")

        # 读取全局属性
        attrs = {attr: getattr(ds, attr) for attr in ds.ncattrs()}

        # 读取节点数据
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

        # 读取大潮位矩阵并原地重构升序水位
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

        # 读取叶单元数据
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
    progress_callback: Optional[Callable[[int, str], None]] = None,
    cancel_event = None
) -> RasterResultSummary:
    """
    【Stage 2 核心解算器】基于已有 Tide Cache NetCDF 与原始 DEM 解算潜在天文潮淹没频率 GeoTIFF。

    硬性验收准则:
        1. 本函数执行全流程中严禁再次调用 FES 模型 (FESTidePredictor 调用次数恒等于 0)；
        2. 严格遵循天文潮淹没频率科学物理定义: P(H(t) > z) * 100%；
        3. 空间栅格属性完全继承原始 DEM: CRS, Transform, Dimensions, NoData, Resolution；
        4. 流式块状写入，支持原子写入回滚保护 (*.tmp.tif) 与 UInt16 位掩码 QC 输出。
    """
    t_start = time.time()
    if progress_callback:
        progress_callback(0, f"正在读取 Tide Cache: {os.path.basename(cache_path)}...")

    cache_data = read_tide_cache(cache_path)
    leaf_cells: List[QuadCell] = cache_data["leaf_cells"]
    nodes: List[ControlNode] = cache_data["nodes"]
    meta: Dict[str, Any] = cache_data["metadata"]

    if qc_output_path is None:
        base, ext = os.path.splitext(output_path)
        qc_output_path = f"{base}_qc{ext}"

    out_dir = os.path.dirname(os.path.abspath(output_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    tmp_output = f"{output_path}.tmp.tif"
    tmp_qc = f"{qc_output_path}.tmp.tif"

    if block_size is None:
        block_size = 512

    try:
        with rasterio.open(dem_path) as src_dem:
            profile = src_dem.profile.copy()
            width = src_dem.width
            height = src_dem.height
            transform = src_dem.transform
            crs = src_dem.crs
            dem_nodata = src_dem.nodata

            # 验证 DEM 与 Cache 兼容性
            cache_w = int(meta.get("SOURCE_WIDTH", width))
            cache_h = int(meta.get("SOURCE_HEIGHT", height))
            if cache_w != width or cache_h != height:
                raise ValueError(
                    f"DEM 尺寸 ({width}x{height}) 与 Tide Cache 记录的尺寸 ({cache_w}x{cache_h}) 不匹配！"
                )

            # 确定输出 NoData
            if dem_nodata is not None and np.isfinite(dem_nodata):
                out_nodata = float(dem_nodata)
            else:
                out_nodata = float(np.nan)

            out_profile = profile.copy()
            out_profile.update(
                dtype=rasterio.float32,
                count=1,
                nodata=out_nodata,
                compress="lzw"
            )

            qc_profile = profile.copy()
            qc_profile.update(
                dtype=rasterio.uint16,
                count=1,
                nodata=QC_NODATA,
                compress="lzw"
            )

            # 建立空间桶索引 (Spatial Bucket Index)
            min_x = min(cell.x_min for cell in leaf_cells)
            max_x = max(cell.x_max for cell in leaf_cells)
            min_y = min(cell.y_min for cell in leaf_cells)
            max_y = max(cell.y_max for cell in leaf_cells)

            span_x = max_x - min_x
            span_y = max_y - min_y
            bucket_size_x = max(100.0, span_x / 32.0)
            bucket_size_y = max(100.0, span_y / 32.0)
            spatial_buckets: Dict[Tuple[int, int], List[QuadCell]] = defaultdict(list)

            for cell in leaf_cells:
                bx0 = int((cell.x_min - min_x) // bucket_size_x)
                bx1 = int((cell.x_max - min_x) // bucket_size_x)
                by0 = int((cell.y_min - min_y) // bucket_size_y)
                by1 = int((cell.y_max - min_y) // bucket_size_y)
                for bx in range(bx0, bx1 + 1):
                    for by in range(by0, by1 + 1):
                        spatial_buckets[(bx, by)].append(cell)

            total_valid_pixels = 0
            total_solved_pixels = 0
            total_unsolved_pixels = 0
            total_pixels = width * height

            n_blocks_x = (width + block_size - 1) // block_size
            n_blocks_y = (height + block_size - 1) // block_size
            total_blocks = n_blocks_x * n_blocks_y
            completed_blocks = 0

            if progress_callback:
                progress_callback(10, f"空间索引就绪，开始流式插值解算 (总像元: {total_pixels:,})...")

            with rasterio.open(tmp_output, "w", **out_profile) as dst_out,                  rasterio.open(tmp_qc, "w", **qc_profile) as dst_qc:

                for r_idx in range(0, height, block_size):
                    w_h = min(block_size, height - r_idx)
                    for c_idx in range(0, width, block_size):
                        if cancel_event is not None and cancel_event.is_set():
                            raise RasterCalculationCancelled("用户取消了淹没频率计算。")

                        w_w = min(block_size, width - c_idx)
                        win = Window(c_idx, r_idx, w_w, w_h)

                        dem_block = src_dem.read(1, window=win)
                        freq_block = np.full((w_h, w_w), out_nodata, dtype=np.float32)
                        qc_block = np.full((w_h, w_w), QC_NODATA, dtype=np.uint16)

                        # 识别有效地形 Target Pixels
                        if dem_nodata is not None and np.isfinite(dem_nodata):
                            target_mask = (dem_block != dem_nodata) & np.isfinite(dem_block)
                        else:
                            target_mask = np.isfinite(dem_block)

                        n_valid_in_win = int(np.count_nonzero(target_mask))
                        total_valid_pixels += n_valid_in_win

                        if n_valid_in_win > 0:
                            rows_local, cols_local = np.where(target_mask)
                            z_vals = dem_block[rows_local, cols_local]

                            rows_global = r_idx + rows_local
                            cols_global = c_idx + cols_local

                            xs, ys = rasterio.transform.xy(transform, rows_global, cols_global, offset="center")
                            xs = np.array(xs, dtype=np.float64)
                            ys = np.array(ys, dtype=np.float64)

                            # 收集候选单元
                            w_min_x, w_max_x = np.min(xs), np.max(xs)
                            w_min_y, w_max_y = np.min(ys), np.max(ys)

                            b_col_min = int((w_min_x - min_x) // bucket_size_x)
                            b_col_max = int((w_max_x - min_x) // bucket_size_x)
                            b_row_min = int((w_min_y - min_y) // bucket_size_y)
                            b_row_max = int((w_max_y - min_y) // bucket_size_y)

                            candidate_cells_set: Set[int] = set()
                            candidate_cells: List[QuadCell] = []
                            for bx in range(b_col_min, b_col_max + 1):
                                for by in range(b_row_min, b_row_max + 1):
                                    for c in spatial_buckets.get((bx, by), []):
                                        if c.cell_id not in candidate_cells_set:
                                            candidate_cells_set.add(c.cell_id)
                                            candidate_cells.append(c)

                            if not candidate_cells:
                                candidate_cells = leaf_cells

                            c_x0 = np.array([c.x_min for c in candidate_cells])
                            c_x1 = np.array([c.x_max for c in candidate_cells])
                            c_y0 = np.array([c.y_min for c in candidate_cells])
                            c_y1 = np.array([c.y_max for c in candidate_cells])

                            win_freqs = np.full(n_valid_in_win, out_nodata, dtype=np.float32)
                            win_qcs = np.full(n_valid_in_win, QC_BIT_INSUFFICIENT_NODES, dtype=np.uint16)

                            for p_i in range(n_valid_in_win):
                                px_x = xs[p_i]
                                px_y = ys[p_i]
                                p_z = z_vals[p_i]

                                in_box = (
                                    (px_x >= c_x0 - 1e-4) & (px_x <= c_x1 + 1e-4) &
                                    (px_y >= c_y0 - 1e-4) & (px_y <= c_y1 + 1e-4)
                                )
                                hit_indices = np.where(in_box)[0]

                                target_cell = None
                                if len(hit_indices) == 1:
                                    target_cell = candidate_cells[hit_indices[0]]
                                elif len(hit_indices) > 1:
                                    best_c = None
                                    best_lvl = -1
                                    for h_idx in hit_indices:
                                        c_obj = candidate_cells[h_idx]
                                        if c_obj.level > best_lvl:
                                            best_lvl = c_obj.level
                                            best_c = c_obj
                                    target_cell = best_c
                                else:
                                    # 最近邻回退
                                    c_xm = (c_x0 + c_x1) / 2.0
                                    c_ym = (c_y0 + c_y1) / 2.0
                                    dists = (c_xm - px_x)**2 + (c_ym - px_y)**2
                                    target_cell = candidate_cells[np.argmin(dists)]

                                c_na = target_cell.node_a
                                c_nb = target_cell.node_b
                                c_nc = target_cell.node_c
                                c_nd = target_cell.node_d

                                corners = [c_na, c_nb, c_nc, c_nd]
                                val_corners = [cn for cn in corners if cn.valid and len(cn.water_levels_sorted) > 0]

                                if len(val_corners) == 0:
                                    win_freqs[p_i] = out_nodata
                                    win_qcs[p_i] = QC_BIT_INSUFFICIENT_NODES
                                    continue

                                # 提取单元级基础 QC
                                pix_qc = QC_BIT_VALID
                                if getattr(target_cell, "qc_min_spacing_reached", False):
                                    pix_qc |= QC_BIT_MIN_SPACING_REACHED
                                if getattr(target_cell, "qc_max_refinement_reached", False):
                                    pix_qc |= QC_BIT_MAX_REFINEMENT_REACHED
                                if getattr(target_cell, "qc_validity_boundary", False):
                                    pix_qc |= QC_BIT_FES_VALIDITY_BOUNDARY

                                # 双线性空间插值权重
                                cell_w = max(1e-6, target_cell.x_max - target_cell.x_min)
                                cell_h = max(1e-6, target_cell.y_max - target_cell.y_min)
                                u = np.clip((px_x - target_cell.x_min) / cell_w, 0.0, 1.0)
                                v = np.clip((px_y - target_cell.y_min) / cell_h, 0.0, 1.0)

                                w_a = (1.0 - u) * (1.0 - v)
                                w_b = u * (1.0 - v)
                                w_c = (1.0 - u) * v
                                w_d = u * v

                                w_list = [w_a, w_b, w_c, w_d]

                                if len(val_corners) == 4:
                                    f_a = compute_inundation_frequency(c_na.water_levels_sorted, p_z, as_percentage=True)
                                    f_b = compute_inundation_frequency(c_nb.water_levels_sorted, p_z, as_percentage=True)
                                    f_c = compute_inundation_frequency(c_nc.water_levels_sorted, p_z, as_percentage=True)
                                    f_d = compute_inundation_frequency(c_nd.water_levels_sorted, p_z, as_percentage=True)
                                    f_interp = w_a * f_a + w_b * f_b + w_c * f_c + w_d * f_d
                                else:
                                    pix_qc |= QC_BIT_SPATIAL_FALLBACK
                                    sum_w = 0.0
                                    f_accum = 0.0
                                    for idx_cn, cn in enumerate(corners):
                                        if cn.valid and len(cn.water_levels_sorted) > 0:
                                            w_cur = w_list[idx_cn]
                                            f_cur = compute_inundation_frequency(cn.water_levels_sorted, p_z, as_percentage=True)
                                            f_accum += w_cur * f_cur
                                            sum_w += w_cur
                                    if sum_w > 1e-9:
                                        f_interp = f_accum / sum_w
                                    else:
                                        f_interp = np.mean([
                                            compute_inundation_frequency(cn.water_levels_sorted, p_z, as_percentage=True)
                                            for cn in val_corners
                                        ])

                                for cn in val_corners:
                                    pix_qc |= (cn.qc_bitmask & (QC_BIT_FES_EXTRAPOLATED | QC_BIT_DATUM_SOURCE_APPROX | QC_BIT_DATUM_INVALID))

                                win_freqs[p_i] = np.clip(f_interp, 0.0, 100.0)
                                win_qcs[p_i] = pix_qc

                            freq_block[rows_local, cols_local] = win_freqs
                            qc_block[rows_local, cols_local] = win_qcs

                            solved_mask = np.isfinite(win_freqs) if not np.isfinite(out_nodata) else (win_freqs != out_nodata)
                            n_solved = int(np.count_nonzero(solved_mask))
                            total_solved_pixels += n_solved
                            total_unsolved_pixels += (n_valid_in_win - n_solved)

                        dst_out.write(freq_block, 1, window=win)
                        dst_qc.write(qc_block, 1, window=win)

                        completed_blocks += 1
                        if progress_callback and (completed_blocks % 5 == 0 or completed_blocks == total_blocks):
                            pct = 10 + int(85 * (completed_blocks / total_blocks))
                            progress_callback(pct, f"流式插值解算中 ({completed_blocks}/{total_blocks} 块已完成)...")

                # 嵌入 GeoTIFF Metadata
                provenance_tags = {
                    "COASTTIDEX_VERSION": COASTTIDEX_VERSION,
                    "DATA_PRODUCT": "Potential Astronomical Tidal Inundation Frequency",
                    "DEFINITION": "P(H(t) > z) under fixed representative terrain",
                    "SOURCE_DEM": os.path.basename(dem_path),
                    "SOURCE_TIDE_CACHE": os.path.basename(cache_path),
                    "TIME_SAMPLES": str(meta.get("TIME_SAMPLES", "")),
                    "DEM_DATUM": str(meta.get("DEM_DATUM", "egm2008")),
                    "TARGET_MODE": str(meta.get("TARGET_MODE", "intertidal")),
                    "STAGE": "Stage 2 (Zero FES calls)",
                    "SOLVED_PIXELS": str(total_solved_pixels),
                    "INPUT_VALID_PIXELS": str(total_valid_pixels)
                }
                dst_out.update_tags(**provenance_tags)
                dst_qc.update_tags(**provenance_tags)

        # 原子重命名
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except Exception:
                pass
        os.replace(tmp_output, output_path)

        if os.path.exists(qc_output_path):
            try:
                os.remove(qc_output_path)
            except Exception:
                pass
        os.replace(tmp_qc, qc_output_path)

        elapsed = time.time() - t_start
        if progress_callback:
            progress_callback(100, f"淹没频率解算完成，共耗时 {elapsed:.2f}s。")

        return RasterResultSummary(
            output_path=output_path,
            qc_output_path=qc_output_path,
            mode="tide_cache_inundation",
            width=width,
            height=height,
            valid_pixels=total_solved_pixels,
            total_pixels=total_pixels,
            input_valid_pixels=total_valid_pixels,
            solved_pixels=total_solved_pixels,
            unsolved_pixels=total_unsolved_pixels,
            control_nodes_count=len(nodes),
            elapsed_seconds=elapsed,
            metadata=meta
        )

    except Exception:
        if os.path.exists(tmp_output):
            try:
                os.remove(tmp_output)
            except Exception:
                pass
        if os.path.exists(tmp_qc):
            try:
                os.remove(tmp_qc)
            except Exception:
                pass
        raise