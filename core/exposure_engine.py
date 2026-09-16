"""
CoastTideX 潜在天文潮露出时间域分析引擎 / Potential Astronomical Tidal Exposure Time-Domain Engine
模块名称: core/exposure_engine.py

科学定义 / Scientific Definition:
---------------------------------
淹没状态 (Inundated): H(t) > z
露出状态 (Exposed):   H(t) <= z
严格边界条件 / Strict Boundary: H(t) == z 归属于露出状态 (Exposed)，不得归属于淹没状态 (Inundated)。

核心计算原则 / Core Principles:
---------------------------------
1. 时间保序计算 (Time-Ordered Execution):
   不同于以排序互补累积分布 (Sorted CCDF) 为基础的淹没频率计算，连续露出时长与事件分析必须严格保持
   真实时间序列先后顺序。
   先插值时间域潮位，再执行阈值判定与事件检测；严禁在控制节点求取时长后再插值到像元。
   (Correct pipeline: H_node(t) -> spatial bilinear interpolation -> H_pixel(t) -> threshold / event analysis)

2. 线性跨界交点插值 (Linear Crossing Interpolation):
   在连续时间步 [t0, t1] 之间，若水面高程跨越地形高程 z，通过线性插值求解精确交点时刻 t*，
   杜绝将 30min 采样整点作为事件唯一边界的量化截断误差。

3. 流式分块累加 (Chunked Streaming Accumulation):
   采用栅格空间分块 (Spatial Blocks) 与时间分块 (Time Chunks) 双重流式累加机制，
   严禁在内存中创建 pixel x time 全时空三维矩阵 (No full 3D cube)。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Optional, Dict, Any, List, Tuple, Callable

import numpy as np
import pandas as pd
import rasterio
import rasterio.windows
from rasterio.windows import Window
import netCDF4

from .utils import normalize_longitude, load_app_config
from .raster_engine import (
    RasterInfo, ControlNode, QuadCell, RasterResultSummary,
    RasterCalculationCancelled
)

# 露出分析质量控制位掩膜定义 / Exposure QC Bitmask Definitions
QC_EXP_VALID = 0                         # 正常高保真解算 / Normal high-fidelity computation
QC_EXP_DEGRADED_CELL = 1                 # 四叉树单元部分角点降级插值 / Degraded interpolation in quad cell
QC_EXP_INSUFFICIENT_NODES = 2            # 缺少足够有效控制节点 / Insufficient valid control nodes
QC_EXP_DATUM_APPROX = 4                  # 基准面偏移采用多边形近似 / Datum offset from polygon approximation
QC_EXP_TERMINAL_APPROX = 8               # 终端时刻潮位采样采用近似平推 / Terminal water level approximated
QC_EXP_PARTIAL_VALID_TIME = 16           # 时间序列存在无效数据间隙 / Temporal invalid gaps present
QC_EXP_PERMANENTLY_SUBMERGED = 32        # 全时段常时淹没 / Permanently submerged throughout window
QC_EXP_PERMANENTLY_EXPOSED = 64          # 全时段常时露出 / Permanently exposed throughout window
QC_EXP_NODATA = 65535                    # 无效/陆地屏蔽像元 (UInt16 NoData) / Invalid/Masked pixel


@dataclass
class ExposureProductPaths:
    """
    露出分析生成的空间栅格产物路径集合。
    File path bundle for generated tidal exposure raster products.
    """
    exposure_fraction_path: str           # *_exposure_fraction.tif (Float32, %)
    exposure_duration_h_path: str         # *_exposure_duration_h.tif (Float32, hours)
    exposure_max_continuous_h_path: str   # *_exposure_max_continuous_h.tif (Float32, hours)
    exposure_mean_event_h_path: str       # *_exposure_mean_event_h.tif (Float32, hours)
    exposure_event_count_path: str        # *_exposure_event_count.tif (UInt32, count)
    exposure_valid_time_fraction_path: str # *_exposure_valid_time_fraction.tif (Float32, %)
    exposure_qc_path: str                 # *_exposure_qc.tif (UInt16, bitmask)


def compute_1d_continuous_exposure(
    water_levels: np.ndarray,
    timestamps_seconds: np.ndarray,
    elevation: float,
    terminal_water_level: Optional[float] = None,
    terminal_timestamp_seconds: Optional[float] = None
) -> Dict[str, Any]:
    """
    单点一维连续潜在天文潮露出时间域基准算法 (包含跨界线性插值与事件统计)。
    1D reference algorithm for continuous potential tidal exposure with crossing interpolation.

    参数 / Parameters:
        water_levels: 各采样时刻的水位序列 [米] / Water level series at sample timestamps [m]
        timestamps_seconds: 各采样时刻的纪元秒时间戳 / Epoch timestamps in seconds
        elevation: 地形高程 z [米] / Terrain surface elevation z [m]
        terminal_water_level: 终端时刻 H(t_end) [米]，用于闭合最后一个半开区间 / Terminal water level H(t_end) [m]
        terminal_timestamp_seconds: 终端时刻纪元秒 / Terminal epoch timestamp in seconds

    返回 / Returns:
        包含累计露出时长、露出比例、最长连续露出、平均事件时长与事件次数的字典。
        Dictionary containing cumulative exposure duration, fraction, max continuous, mean event and count.
    """
    z = float(elevation)
    n_samples = len(water_levels)
    if n_samples == 0:
        return {
            "exposure_fraction_pct": 0.0,
            "cumulative_exposure_h": 0.0,
            "max_continuous_exposure_h": 0.0,
            "mean_event_duration_h": 0.0,
            "event_count": 0,
            "valid_time_fraction_pct": 0.0,
            "valid_duration_h": 0.0,
            "total_window_h": 0.0
        }

    # 组装完整采样点 (若提供 terminal sample，则拼接为 n_samples + 1 个点)
    if terminal_water_level is not None and terminal_timestamp_seconds is not None:
        wl_seq = np.append(water_levels, terminal_water_level)
        ts_seq = np.append(timestamps_seconds, terminal_timestamp_seconds)
    else:
        wl_seq = np.asarray(water_levels, dtype=float)
        ts_seq = np.asarray(timestamps_seconds, dtype=float)

    total_window_sec = float(ts_seq[-1] - ts_seq[0]) if len(ts_seq) > 1 else 0.0
    if total_window_sec <= 0.0:
        total_window_sec = 1.0

    valid_duration_sec = 0.0
    total_exposure_sec = 0.0
    max_continuous_sec = 0.0
    current_exposure_sec = 0.0
    event_count = 0

    n_intervals = len(wl_seq) - 1
    for i in range(n_intervals):
        h0 = wl_seq[i]
        h1 = wl_seq[i + 1]
        t0 = ts_seq[i]
        t1 = ts_seq[i + 1]
        dt = float(t1 - t0)
        if dt <= 0.0:
            continue

        # 检查有效性；若出现 NaN 间隙，重置当前连续事件并跳过
        # If NaN gap is encountered, reset current continuous event without merging across gap
        if not (np.isfinite(h0) and np.isfinite(h1)):
            if current_exposure_sec > 0.0:
                event_count += 1
                if current_exposure_sec > max_continuous_sec:
                    max_continuous_sec = current_exposure_sec
                current_exposure_sec = 0.0
            continue

        valid_duration_sec += dt
        exp0 = (h0 <= z)
        exp1 = (h1 <= z)

        if exp0 and exp1:
            # 整个时间步区间完全处于露出状态 / Entire interval is exposed
            current_exposure_sec += dt
            total_exposure_sec += dt
            if current_exposure_sec > max_continuous_sec:
                max_continuous_sec = current_exposure_sec

        elif (not exp0) and (not exp1):
            # 整个时间步区间完全处于淹没状态 / Entire interval is inundated
            if current_exposure_sec > 0.0:
                event_count += 1
                if current_exposure_sec > max_continuous_sec:
                    max_continuous_sec = current_exposure_sec
                current_exposure_sec = 0.0

        elif (not exp0) and exp1:
            # 状态由淹没转为露出 / Transition from submerged (h0 > z) to exposed (h1 <= z)
            # 线性插值求取跨界交点时刻比例 r in [0, 1] / Linear crossing ratio
            denom = float(h0 - h1)
            r = (h0 - z) / denom if denom != 0.0 else 0.0
            r = max(0.0, min(1.0, r))
            dt_sub = r * dt
            dt_exp = (1.0 - r) * dt

            # 前半段淹没：若此前有事件在延续则终止结算
            if current_exposure_sec > 0.0:
                event_count += 1
                if current_exposure_sec > max_continuous_sec:
                    max_continuous_sec = current_exposure_sec

            # 后半段开始新的露出事件
            current_exposure_sec = dt_exp
            total_exposure_sec += dt_exp
            if current_exposure_sec > max_continuous_sec:
                max_continuous_sec = current_exposure_sec

        else:
            # 状态由露出转为淹没 / Transition from exposed (h0 <= z) to submerged (h1 > z)
            denom = float(h1 - h0)
            r = (z - h0) / denom if denom != 0.0 else 0.0
            r = max(0.0, min(1.0, r))
            dt_exp = r * dt

            # 前半段露出并终止当前事件
            current_exposure_sec += dt_exp
            total_exposure_sec += dt_exp
            if current_exposure_sec > max_continuous_sec:
                max_continuous_sec = current_exposure_sec

            event_count += 1
            current_exposure_sec = 0.0

    # 序列末端收口：若整个时序结束时仍有露出事件未闭合，计入事件数
    # Finalization at end of sequence: close trailing ongoing event
    if current_exposure_sec > 0.0:
        event_count += 1
        if current_exposure_sec > max_continuous_sec:
            max_continuous_sec = current_exposure_sec

    cum_exp_h = total_exposure_sec / 3600.0
    max_cont_h = max_continuous_sec / 3600.0
    valid_dur_h = valid_duration_sec / 3600.0
    total_win_h = total_window_sec / 3600.0

    mean_event_h = (cum_exp_h / event_count) if event_count > 0 else 0.0
    exp_frac_pct = (total_exposure_sec / valid_duration_sec * 100.0) if valid_duration_sec > 0.0 else 0.0
    val_time_pct = (valid_duration_sec / total_window_sec * 100.0) if total_window_sec > 0.0 else 0.0

    return {
        "exposure_fraction_pct": round(float(exp_frac_pct), 6),
        "cumulative_exposure_h": round(float(cum_exp_h), 6),
        "cumulative_exposure_sec": float(total_exposure_sec),
        "max_continuous_exposure_h": round(float(max_cont_h), 6),
        "mean_event_duration_h": round(float(mean_event_h), 6),
        "event_count": int(event_count),
        "valid_time_fraction_pct": round(float(val_time_pct), 6),
        "valid_duration_h": round(float(valid_dur_h), 6),
        "total_window_h": round(float(total_win_h), 6)
    }


def stream_exposure_metrics_interpolation(
    dem_path: str,
    output_paths: ExposureProductPaths,
    cells: List[QuadCell],
    nodes: List[ControlNode],
    time_series_utc: np.ndarray,
    target_datum: str = "egm2008",
    time_chunk_size: int = 1000,
    block_size: int = 512,
    terminal_node_tides: Optional[np.ndarray] = None,
    terminal_timestamp_sec: Optional[float] = None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    cancel_event: Optional[Any] = None
) -> Dict[str, Any]:
    """
    基于自适应控制网格与分块流式累加，解算高分辨率 DEM 的潜在天文潮露出时间域栅格产品。
    Stream bilinear spatial interpolation and continuous exposure accumulation without creating 3D full cubes.

    科学不变量与内存安全 / Invariants & Memory Safety:
    - 仅在叶单元内执行双线性空间插值获得像元 H_pixel(t)；
    - 时间维采用 time_chunk_size (默认 1000~2000 步) 迭代，内存随像元数与时步数严格解耦；
    - 输出 7 大独立 GeoTIFF 科学产物。
    """
    t_start = time.time()

    with rasterio.open(dem_path) as src_dem:
        profile = src_dem.profile.copy()
        w = src_dem.width
        h = src_dem.height
        nodata_val = src_dem.nodata
        trans = src_dem.transform

    # 预计算时间序列及步长 (秒)
    n_time = len(time_series_utc)
    ts_seconds = np.asarray([pd.Timestamp(t).timestamp() for t in time_series_utc], dtype=np.float64)

    has_terminal = (terminal_node_tides is not None and terminal_timestamp_sec is not None)
    if has_terminal:
        terminal_dt_sec = float(terminal_timestamp_sec - ts_seconds[-1])
        total_time_span_sec = float(terminal_timestamp_sec - ts_seconds[0])
    else:
        terminal_dt_sec = 0.0
        total_time_span_sec = float(ts_seconds[-1] - ts_seconds[0]) if n_time > 1 else 1.0

    if total_time_span_sec <= 0.0:
        total_time_span_sec = 1.0

    # 提取控制节点的水位时序 (已转换至目标基准)
    # Extract node water levels in target datum
    n_nodes = len(nodes)
    node_water_levels = np.zeros((n_nodes, n_time), dtype=np.float32)
    node_valid_mask = np.zeros(n_nodes, dtype=bool)
    node_id_to_idx = {getattr(n, "node_id", i): i for i, n in enumerate(nodes)}

    for idx, node in enumerate(nodes):
        raw_tide = getattr(node, "tide_msl_raw", None)
        if raw_tide is None:
            raw_tide = getattr(node, "water_levels_msl", None)
        if node.valid and raw_tide is not None and len(raw_tide) == n_time:
            # 加上静态垂直基准偏移量 / Add static datum offset
            off = node.static_offset_m if np.isfinite(node.static_offset_m) else 0.0
            node_water_levels[idx, :] = (raw_tide + off).astype(np.float32)
            node_valid_mask[idx] = True

    # 终端时刻各节点水位
    terminal_node_wl = np.zeros(n_nodes, dtype=np.float32)
    if has_terminal:
        for idx, node in enumerate(nodes):
            if node.valid and idx < len(terminal_node_tides):
                off = node.static_offset_m if np.isfinite(node.static_offset_m) else 0.0
                terminal_node_wl[idx] = float(terminal_node_tides[idx] + off)

    # 准备 7 大输出 GeoTIFF Profile
    prof_f32 = profile.copy()
    prof_f32.update({'count': 1, 'dtype': 'float32', 'nodata': -9999.0})

    prof_u32 = profile.copy()
    prof_u32.update({'count': 1, 'dtype': 'uint32', 'nodata': 0})

    prof_u16 = profile.copy()
    prof_u16.update({'count': 1, 'dtype': 'uint16', 'nodata': QC_EXP_NODATA})

    # 创建目标输出 GeoTIFF 文件
    dst_frac = rasterio.open(output_paths.exposure_fraction_path, 'w', **prof_f32)
    dst_dur = rasterio.open(output_paths.exposure_duration_h_path, 'w', **prof_f32)
    dst_max = rasterio.open(output_paths.exposure_max_continuous_h_path, 'w', **prof_f32)
    dst_mean = rasterio.open(output_paths.exposure_mean_event_h_path, 'w', **prof_f32)
    dst_count = rasterio.open(output_paths.exposure_event_count_path, 'w', **prof_u32)
    dst_val_time = rasterio.open(output_paths.exposure_valid_time_fraction_path, 'w', **prof_f32)
    dst_qc = rasterio.open(output_paths.exposure_qc_path, 'w', **prof_u16)

    # 遍历空间栅格窗口 (512x512)
    total_blocks_r = int(np.ceil(h / block_size))
    total_blocks_c = int(np.ceil(w / block_size))
    total_blocks = total_blocks_r * total_blocks_c
    processed_blocks = 0

    def _get_cell_bounds(c):
        x0 = getattr(c, "x0", getattr(c, "x_min", 0.0))
        x1 = getattr(c, "x1", getattr(c, "x_max", 0.0))
        y0 = getattr(c, "y0", getattr(c, "y_min", 0.0))
        y1 = getattr(c, "y1", getattr(c, "y_max", 0.0))
        return x0, x1, y0, y1

    def _get_cell_node_idxs(c):
        if hasattr(c, "node_indices"):
            raw_ids = c.node_indices
        elif hasattr(c, "node_a") and hasattr(c, "node_b") and hasattr(c, "node_c") and hasattr(c, "node_d"):
            raw_ids = (c.node_a.node_id, c.node_b.node_id, c.node_c.node_id, c.node_d.node_id)
        else:
            raw_ids = (0, 0, 0, 0)
        return tuple(node_id_to_idx.get(nid, 0) for nid in raw_ids)

    inv_trans = ~trans

    try:
        with rasterio.open(dem_path) as src_dem:
            for r_off in range(0, h, block_size):
                r_len = min(block_size, h - r_off)
                for c_off in range(0, w, block_size):
                    if cancel_event and cancel_event.is_set():
                        raise RasterCalculationCancelled("用户取消了潜在露出栅格解算。")

                    c_len = min(block_size, w - c_off)
                    win = Window(c_off, r_off, c_len, r_len)
                    dem_block = src_dem.read(1, window=win).astype(np.float32)

                    # 判定有效高程掩膜
                    if nodata_val is not None and np.isfinite(nodata_val):
                        valid_dem_mask = np.isfinite(dem_block) & ~np.isclose(dem_block, nodata_val)
                    else:
                        valid_dem_mask = np.isfinite(dem_block)

                    # 初始化当前块的 7 大累加状态数组
                    out_frac = np.full((r_len, c_len), -9999.0, dtype=np.float32)
                    out_dur = np.full((r_len, c_len), -9999.0, dtype=np.float32)
                    out_max = np.full((r_len, c_len), -9999.0, dtype=np.float32)
                    out_mean = np.full((r_len, c_len), -9999.0, dtype=np.float32)
                    out_count = np.zeros((r_len, c_len), dtype=np.uint32)
                    out_val_time = np.full((r_len, c_len), -9999.0, dtype=np.float32)
                    out_qc = np.full((r_len, c_len), QC_EXP_NODATA, dtype=np.uint16)

                    if np.any(valid_dem_mask):
                        # 获取当前窗口像元的绝对空间坐标
                        rows_grid, cols_grid = np.meshgrid(
                            np.arange(r_off, r_off + r_len),
                            np.arange(c_off, c_off + c_len),
                            indexing='ij'
                        )
                        xs_grid, ys_grid = rasterio.transform.xy(trans, rows_grid, cols_grid, offset='center')
                        xs_arr = np.asarray(xs_grid, dtype=np.float64).reshape((r_len, c_len))
                        ys_arr = np.asarray(ys_grid, dtype=np.float64).reshape((r_len, c_len))

                        # 在像元级维护流式连续事件状态变量
                        # State variables across time chunks for valid pixels
                        total_exp_sec = np.zeros((r_len, c_len), dtype=np.float64)
                        max_cont_sec = np.zeros((r_len, c_len), dtype=np.float64)
                        curr_exp_sec = np.zeros((r_len, c_len), dtype=np.float64)
                        ev_count = np.zeros((r_len, c_len), dtype=np.int32)
                        valid_dur_sec = np.zeros((r_len, c_len), dtype=np.float64)

                        # 记录跨时间块衔接的上一个时间步水位与有效性
                        prev_wl = np.full((r_len, c_len), np.nan, dtype=np.float32)
                        prev_ts = 0.0

                        # 检索落在当前块中的四叉树叶单元
                        # Find overlapping cells for this spatial block
                        b_x0, b_y1 = rasterio.transform.xy(trans, r_off, c_off, offset='ul')
                        b_x1, b_y0 = rasterio.transform.xy(trans, r_off + r_len, c_off + c_len, offset='lr')
                        min_x, max_x = min(b_x0, b_x1), max(b_x0, b_x1)
                        min_y, max_y = min(b_y0, b_y1), max(b_y0, b_y1)

                        block_cells = []
                        for c in cells:
                            cx0, cx1, cy0, cy1 = _get_cell_bounds(c)
                            if not (cx1 < min_x or cx0 > max_x or cy1 < min_y or cy0 > max_y):
                                block_cells.append(c)

                        # 时间维分块循环 (Time-Chunking Loop)
                        for t_start_idx in range(0, n_time, time_chunk_size):
                            t_end_idx = min(n_time, t_start_idx + time_chunk_size)
                            sub_n_time = t_end_idx - t_start_idx
                            chunk_ts_sec = ts_seconds[t_start_idx:t_end_idx]

                            # 像元时序水位矩阵 (像元数 x 当前时间块步数)
                            chunk_pixel_tides = np.full((r_len, c_len, sub_n_time), np.nan, dtype=np.float32)

                            # 逐叶单元进行双线性插值填充像元水位
                            for cell in block_cells:
                                c_idxs = _get_cell_node_idxs(cell)
                                cx0, cx1, cy0, cy1 = _get_cell_bounds(cell)

                                # 若角点全部有效，则进行严格四节点双线性插值
                                all_nodes_valid = (
                                    node_valid_mask[c_idxs[0]] and
                                    node_valid_mask[c_idxs[1]] and
                                    node_valid_mask[c_idxs[2]] and
                                    node_valid_mask[c_idxs[3]]
                                )

                                # 找出落在当前单元内的块像元局部索引
                                in_cell = (
                                    (xs_arr >= cx0) & (xs_arr <= cx1) &
                                    (ys_arr >= cy0) & (ys_arr <= cy1) &
                                    valid_dem_mask
                                )
                                if not np.any(in_cell):
                                    continue

                                px_xs = xs_arr[in_cell]
                                px_ys = ys_arr[in_cell]
                                dx = max(1e-9, cx1 - cx0)
                                dy = max(1e-9, cy1 - cy0)
                                u = np.clip((px_xs - cx0) / dx, 0.0, 1.0)
                                v = np.clip((px_ys - cy0) / dy, 0.0, 1.0)

                                w00 = (1.0 - u) * (1.0 - v)
                                w10 = u * (1.0 - v)
                                w01 = (1.0 - u) * v
                                w11 = u * v

                                wl00 = node_water_levels[c_idxs[0], t_start_idx:t_end_idx]
                                wl10 = node_water_levels[c_idxs[1], t_start_idx:t_end_idx]
                                wl01 = node_water_levels[c_idxs[2], t_start_idx:t_end_idx]
                                wl11 = node_water_levels[c_idxs[3], t_start_idx:t_end_idx]

                                if all_nodes_valid:
                                    # 向量化双线性插值 / Vectorized bilinear interpolation
                                    # shape: (P, sub_n_time)
                                    interp_tides = (
                                        w00[:, None] * wl00[None, :] +
                                        w10[:, None] * wl10[None, :] +
                                        w01[:, None] * wl01[None, :] +
                                        w11[:, None] * wl11[None, :]
                                    )
                                    chunk_pixel_tides[in_cell, :] = interp_tides
                                    out_qc[in_cell] = QC_EXP_VALID
                                else:
                                    # 降级可用角点加权回退 / Fallback to available nodes
                                    val_sub = [node_valid_mask[idx_n] for idx_n in c_idxs]
                                    if any(val_sub):
                                        weights = np.array([w00, w10, w01, w11])  # (4, P)
                                        w_sum = np.zeros(len(px_xs), dtype=np.float32)
                                        accum_t = np.zeros((len(px_xs), sub_n_time), dtype=np.float32)
                                        node_wls = [wl00, wl10, wl01, wl11]
                                        for k_n in range(4):
                                            if val_sub[k_n]:
                                                w_k = weights[k_n].astype(np.float32)
                                                w_sum += w_k
                                                accum_t += w_k[:, None] * node_wls[k_n][None, :]
                                        w_safe = np.where(w_sum > 0.0, w_sum, 1.0)
                                        chunk_pixel_tides[in_cell, :] = accum_t / w_safe[:, None]
                                        out_qc[in_cell] = QC_EXP_DEGRADED_CELL
                                    else:
                                        out_qc[in_cell] = QC_EXP_INSUFFICIENT_NODES

                            # 逐像元在当前时间块内执行连续露出事件积分与跨界插值
                            # Integrate exposure duration and crossings across the time chunk
                            val_pixels_idx = np.where(valid_dem_mask)
                            for r_idx, c_idx in zip(val_pixels_idx[0], val_pixels_idx[1]):
                                z_val = dem_block[r_idx, c_idx]
                                t_slice = chunk_pixel_tides[r_idx, c_idx, :]

                                # 若与上一时间块衔接，先计算跨块边界区间的露出
                                if t_start_idx > 0 and np.isfinite(prev_wl[r_idx, c_idx]) and np.isfinite(t_slice[0]):
                                    dt_cross = float(chunk_ts_sec[0] - prev_ts)
                                    if dt_cross > 0.0:
                                        valid_dur_sec[r_idx, c_idx] += dt_cross
                                        h0 = prev_wl[r_idx, c_idx]
                                        h1 = t_slice[0]
                                        e0 = (h0 <= z_val)
                                        e1 = (h1 <= z_val)
                                        if e0 and e1:
                                            curr_exp_sec[r_idx, c_idx] += dt_cross
                                            total_exp_sec[r_idx, c_idx] += dt_cross
                                            if curr_exp_sec[r_idx, c_idx] > max_cont_sec[r_idx, c_idx]:
                                                max_cont_sec[r_idx, c_idx] = curr_exp_sec[r_idx, c_idx]
                                        elif (not e0) and (not e1):
                                            if curr_exp_sec[r_idx, c_idx] > 0.0:
                                                ev_count[r_idx, c_idx] += 1
                                                if curr_exp_sec[r_idx, c_idx] > max_cont_sec[r_idx, c_idx]:
                                                    max_cont_sec[r_idx, c_idx] = curr_exp_sec[r_idx, c_idx]
                                                curr_exp_sec[r_idx, c_idx] = 0.0
                                        elif (not e0) and e1:
                                            r = (h0 - z_val) / float(h0 - h1)
                                            r = max(0.0, min(1.0, r))
                                            dt_e = (1.0 - r) * dt_cross
                                            if curr_exp_sec[r_idx, c_idx] > 0.0:
                                                ev_count[r_idx, c_idx] += 1
                                                if curr_exp_sec[r_idx, c_idx] > max_cont_sec[r_idx, c_idx]:
                                                    max_cont_sec[r_idx, c_idx] = curr_exp_sec[r_idx, c_idx]
                                            curr_exp_sec[r_idx, c_idx] = dt_e
                                            total_exp_sec[r_idx, c_idx] += dt_e
                                            if curr_exp_sec[r_idx, c_idx] > max_cont_sec[r_idx, c_idx]:
                                                max_cont_sec[r_idx, c_idx] = curr_exp_sec[r_idx, c_idx]
                                        else:
                                            r = (z_val - h0) / float(h1 - h0)
                                            r = max(0.0, min(1.0, r))
                                            dt_e = r * dt_cross
                                            curr_exp_sec[r_idx, c_idx] += dt_e
                                            total_exp_sec[r_idx, c_idx] += dt_e
                                            if curr_exp_sec[r_idx, c_idx] > max_cont_sec[r_idx, c_idx]:
                                                max_cont_sec[r_idx, c_idx] = curr_exp_sec[r_idx, c_idx]
                                            ev_count[r_idx, c_idx] += 1
                                            curr_exp_sec[r_idx, c_idx] = 0.0

                                # 块内逐步积分
                                for step_k in range(sub_n_time - 1):
                                    h0 = t_slice[step_k]
                                    h1 = t_slice[step_k + 1]
                                    dt_step = float(chunk_ts_sec[step_k + 1] - chunk_ts_sec[step_k])
                                    if dt_step <= 0.0:
                                        continue

                                    if not (np.isfinite(h0) and np.isfinite(h1)):
                                        if curr_exp_sec[r_idx, c_idx] > 0.0:
                                            ev_count[r_idx, c_idx] += 1
                                            if curr_exp_sec[r_idx, c_idx] > max_cont_sec[r_idx, c_idx]:
                                                max_cont_sec[r_idx, c_idx] = curr_exp_sec[r_idx, c_idx]
                                            curr_exp_sec[r_idx, c_idx] = 0.0
                                        continue

                                    valid_dur_sec[r_idx, c_idx] += dt_step
                                    e0 = (h0 <= z_val)
                                    e1 = (h1 <= z_val)

                                    if e0 and e1:
                                        curr_exp_sec[r_idx, c_idx] += dt_step
                                        total_exp_sec[r_idx, c_idx] += dt_step
                                        if curr_exp_sec[r_idx, c_idx] > max_cont_sec[r_idx, c_idx]:
                                            max_cont_sec[r_idx, c_idx] = curr_exp_sec[r_idx, c_idx]
                                    elif (not e0) and (not e1):
                                        if curr_exp_sec[r_idx, c_idx] > 0.0:
                                            ev_count[r_idx, c_idx] += 1
                                            if curr_exp_sec[r_idx, c_idx] > max_cont_sec[r_idx, c_idx]:
                                                max_cont_sec[r_idx, c_idx] = curr_exp_sec[r_idx, c_idx]
                                            curr_exp_sec[r_idx, c_idx] = 0.0
                                    elif (not e0) and e1:
                                        r = (h0 - z_val) / float(h0 - h1)
                                        r = max(0.0, min(1.0, r))
                                        dt_e = (1.0 - r) * dt_step
                                        if curr_exp_sec[r_idx, c_idx] > 0.0:
                                            ev_count[r_idx, c_idx] += 1
                                            if curr_exp_sec[r_idx, c_idx] > max_cont_sec[r_idx, c_idx]:
                                                max_cont_sec[r_idx, c_idx] = curr_exp_sec[r_idx, c_idx]
                                        curr_exp_sec[r_idx, c_idx] = dt_e
                                        total_exp_sec[r_idx, c_idx] += dt_e
                                        if curr_exp_sec[r_idx, c_idx] > max_cont_sec[r_idx, c_idx]:
                                            max_cont_sec[r_idx, c_idx] = curr_exp_sec[r_idx, c_idx]
                                    else:
                                        r = (z_val - h0) / float(h1 - h0)
                                        r = max(0.0, min(1.0, r))
                                        dt_e = r * dt_step
                                        curr_exp_sec[r_idx, c_idx] += dt_e
                                        total_exp_sec[r_idx, c_idx] += dt_e
                                        if curr_exp_sec[r_idx, c_idx] > max_cont_sec[r_idx, c_idx]:
                                            max_cont_sec[r_idx, c_idx] = curr_exp_sec[r_idx, c_idx]
                                        ev_count[r_idx, c_idx] += 1
                                        curr_exp_sec[r_idx, c_idx] = 0.0

                                # 更新跨块前序状态
                                prev_wl[r_idx, c_idx] = t_slice[-1]

                            prev_ts = chunk_ts_sec[-1]

                        # 处理终端时刻采样 (Terminal Sample Crossing)
                        # Process optional terminal sample H(t_end) for the final interval [t_N-1, t_end]
                        if has_terminal and terminal_dt_sec > 0.0:
                            # 插值终端时刻各像元水位
                            terminal_pixel_tides = np.full((r_len, c_len), np.nan, dtype=np.float32)
                            for cell in block_cells:
                                c_idxs = _get_cell_node_idxs(cell)
                                cx0, cx1, cy0, cy1 = _get_cell_bounds(cell)
                                in_cell = (
                                    (xs_arr >= cx0) & (xs_arr <= cx1) &
                                    (ys_arr >= cy0) & (ys_arr <= cy1) &
                                    valid_dem_mask
                                )
                                if not np.any(in_cell):
                                    continue
                                px_xs = xs_arr[in_cell]
                                px_ys = ys_arr[in_cell]
                                dx = max(1e-9, cx1 - cx0)
                                dy = max(1e-9, cy1 - cy0)
                                u = np.clip((px_xs - cx0) / dx, 0.0, 1.0)
                                v = np.clip((px_ys - cy0) / dy, 0.0, 1.0)
                                w00 = (1.0 - u) * (1.0 - v)
                                w10 = u * (1.0 - v)
                                w01 = (1.0 - u) * v
                                w11 = u * v

                                tw00 = terminal_node_wl[c_idxs[0]]
                                tw10 = terminal_node_wl[c_idxs[1]]
                                tw01 = terminal_node_wl[c_idxs[2]]
                                tw11 = terminal_node_wl[c_idxs[3]]
                                terminal_pixel_tides[in_cell] = (
                                    w00 * tw00 + w10 * tw10 + w01 * tw01 + w11 * tw11
                                )

                            for r_idx, c_idx in zip(val_pixels_idx[0], val_pixels_idx[1]):
                                z_val = dem_block[r_idx, c_idx]
                                h0 = prev_wl[r_idx, c_idx]
                                h1 = terminal_pixel_tides[r_idx, c_idx]
                                if np.isfinite(h0) and np.isfinite(h1):
                                    valid_dur_sec[r_idx, c_idx] += terminal_dt_sec
                                    e0 = (h0 <= z_val)
                                    e1 = (h1 <= z_val)
                                    if e0 and e1:
                                        curr_exp_sec[r_idx, c_idx] += terminal_dt_sec
                                        total_exp_sec[r_idx, c_idx] += terminal_dt_sec
                                        if curr_exp_sec[r_idx, c_idx] > max_cont_sec[r_idx, c_idx]:
                                            max_cont_sec[r_idx, c_idx] = curr_exp_sec[r_idx, c_idx]
                                    elif (not e0) and (not e1):
                                        if curr_exp_sec[r_idx, c_idx] > 0.0:
                                            ev_count[r_idx, c_idx] += 1
                                            if curr_exp_sec[r_idx, c_idx] > max_cont_sec[r_idx, c_idx]:
                                                max_cont_sec[r_idx, c_idx] = curr_exp_sec[r_idx, c_idx]
                                            curr_exp_sec[r_idx, c_idx] = 0.0
                                    elif (not e0) and e1:
                                        r = (h0 - z_val) / float(h0 - h1)
                                        r = max(0.0, min(1.0, r))
                                        dt_e = (1.0 - r) * terminal_dt_sec
                                        if curr_exp_sec[r_idx, c_idx] > 0.0:
                                            ev_count[r_idx, c_idx] += 1
                                            if curr_exp_sec[r_idx, c_idx] > max_cont_sec[r_idx, c_idx]:
                                                max_cont_sec[r_idx, c_idx] = curr_exp_sec[r_idx, c_idx]
                                        curr_exp_sec[r_idx, c_idx] = dt_e
                                        total_exp_sec[r_idx, c_idx] += dt_e
                                        if curr_exp_sec[r_idx, c_idx] > max_cont_sec[r_idx, c_idx]:
                                            max_cont_sec[r_idx, c_idx] = curr_exp_sec[r_idx, c_idx]
                                    else:
                                        r = (z_val - h0) / float(h1 - h0)
                                        r = max(0.0, min(1.0, r))
                                        dt_e = r * terminal_dt_sec
                                        curr_exp_sec[r_idx, c_idx] += dt_e
                                        total_exp_sec[r_idx, c_idx] += dt_e
                                        if curr_exp_sec[r_idx, c_idx] > max_cont_sec[r_idx, c_idx]:
                                            max_cont_sec[r_idx, c_idx] = curr_exp_sec[r_idx, c_idx]
                                        ev_count[r_idx, c_idx] += 1
                                        curr_exp_sec[r_idx, c_idx] = 0.0

                        # 末端事件闭合收口
                        unclosed = (curr_exp_sec > 0.0) & valid_dem_mask
                        if np.any(unclosed):
                            ev_count[unclosed] += 1
                            max_cont_sec[unclosed] = np.maximum(max_cont_sec[unclosed], curr_exp_sec[unclosed])

                        # 计算当前块最终输出矩阵
                        val_pixels = (valid_dur_sec > 0.0) & valid_dem_mask
                        if np.any(val_pixels):
                            dur_h = total_exp_sec[val_pixels] / 3600.0
                            val_dur_h = valid_dur_sec[val_pixels] / 3600.0
                            max_h = max_cont_sec[val_pixels] / 3600.0
                            cnts = ev_count[val_pixels].astype(np.uint32)

                            out_dur[val_pixels] = dur_h.astype(np.float32)
                            out_max[val_pixels] = max_h.astype(np.float32)
                            out_count[val_pixels] = cnts

                            # 露出比例 (%)
                            out_frac[val_pixels] = (dur_h / val_dur_h * 100.0).astype(np.float32)
                            # 平均事件时长 (hours)
                            safe_cnts = np.where(cnts > 0, cnts, 1)
                            mean_h = np.where(cnts > 0, dur_h / safe_cnts, 0.0)
                            out_mean[val_pixels] = mean_h.astype(np.float32)

                            # 有效时间覆盖率 (%)
                            out_val_time[val_pixels] = (valid_dur_sec[val_pixels] / total_time_span_sec * 100.0).astype(np.float32)

                            if not has_terminal:
                                out_qc[val_pixels] |= QC_EXP_TERMINAL_APPROX

                            # 极端常时状态标记
                            is_perm_sub = (dur_h <= 1e-4)
                            is_perm_exp = np.isclose(dur_h, val_dur_h, atol=1e-3)
                            qc_val = out_qc[val_pixels]
                            qc_val = np.where(is_perm_sub, qc_val | QC_EXP_PERMANENTLY_SUBMERGED, qc_val)
                            qc_val = np.where(is_perm_exp, qc_val | QC_EXP_PERMANENTLY_EXPOSED, qc_val)
                            out_qc[val_pixels] = qc_val.astype(np.uint16)

                    # 写入当前窗口到 7 大产物文件
                    dst_frac.write(out_frac, 1, window=win)
                    dst_dur.write(out_dur, 1, window=win)
                    dst_max.write(out_max, 1, window=win)
                    dst_mean.write(out_mean, 1, window=win)
                    dst_count.write(out_count, 1, window=win)
                    dst_val_time.write(out_val_time, 1, window=win)
                    dst_qc.write(out_qc, 1, window=win)

                    processed_blocks += 1
                    if progress_callback:
                        pct = int(processed_blocks / total_blocks * 100)
                        progress_callback(pct, f"潜在天文潮露出栅格反演中 ({processed_blocks}/{total_blocks} 块)...")

    finally:
        dst_frac.close()
        dst_dur.close()
        dst_max.close()
        dst_mean.close()
        dst_count.close()
        dst_val_time.close()
        dst_qc.close()

    elapsed = time.time() - t_start
    if progress_callback:
        progress_callback(100, f"潜在天文潮露出时间域栅格产品解算完成 (耗时 {elapsed:.2f}s)！")

    return {
        "status": "COMPLETED",
        "elapsed_seconds": round(elapsed, 2),
        "products": output_paths
    }
