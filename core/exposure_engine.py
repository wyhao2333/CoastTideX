"""
CoastTideX 潜在天文潮露出时间域分析引擎 / Potential Astronomical Tidal Exposure Time-Domain Engine
模块名称 / Module: core/exposure_engine.py
版本 / Version: CoastTideX v1.6 Beta

科学定义 / Scientific Definition:
---------------------------------
潜在天文潮露出时长 (Potential Astronomical Tidal Exposure Duration under a Fixed Representative Terrain):
基于固定代表性地形 DEM 与纯天文潮位时序，解算潮滩/沙滩像元水面未覆盖的时间跨度及连续事件特征。
严禁命名为“沙滩干燥时间”、“实际退水时间”或“实际水动力淹没/露出”，本产品不包含风暴潮增水、波浪爬高、
地下潜水渗透、降雨汇流与三维沉积物排水动力学。

边界条件 / Boundary Classification:
-----------------------------------
淹没状态 (Inundated): H(t) > z
露出状态 (Exposed):   H(t) <= z
严格边界不变量: H(t) == z 归属于露出状态 (Exposed)，绝不归属于淹没状态 (Inundated)。

核心架构与内存安全设计 / Core Architecture & Memory Invariants:
--------------------------------------------------------------
1. 避免全像元时序三维数组分配 (No Full 3D Pixel-Time Cube):
   不分配 (rows, cols, time_chunk) 规模的全局像元潮位三维立方体。
   采用空间分块 (默认 512x512) 与时间切片流式重构，在当前空间窗口内仅保留 2D 像元高程与必要累积状态矩阵，
   单步或按时步切片重构 H_pixel(t)，其内存开销主要取决于 block_size、局部控制节点数及分块流式缓冲区，实现可控的有界内存驻留。

2. 纯二维 NumPy 向量化跨界插值与状态更新 (Vectorized 2D State Transitions):
   彻底消除 Pixel x Time 的 Python 级双循环。
   相邻采样点 [H0, H1] 跨越地形高程 z 时，通过线性插值严格求解跨界时刻比例:
   r = clip((z - H0) / (H1 - H0), 0.0, 1.0)
   并在整幅二维空间窗口上以矢量化掩膜更新露出时长、最大单次连续露出、平均事件时长与事件计数值。

3. 拓扑连通防护与角点降级重归一化 (Topology Guard & Corner Normalization):
   严格继承 Target-Mask-Derived Topology Guard 粗粒度连通域划分，
   像元仅使用归属于同一拓扑连通域的有效控制节点插值；
   当控制单元出现无效节点 (NaN/陆地) 时，自动重归一化可用角点权重，禁止无效节点以 0m 掺入污染。

4. 产物原子写入安全保证 (Atomic GeoTIFF Output Safety):
   所有 7 大空间栅格产物采用 *.tmp.tif 临时文件写入；解算成功并通过基础校验后通过 os.replace() 原子替换，
   杜绝中途取消或异常产生损坏的半成品文件。
"""

from __future__ import annotations

import os
import time
import json
from dataclasses import dataclass
from typing import Optional, Dict, Any, List, Tuple, Callable

import numpy as np
import pandas as pd
import rasterio
import rasterio.windows
from rasterio.windows import Window

from .raster_engine import (
    RasterInfo, ControlNode, QuadCell, RasterCalculationCancelled, ExistingOutputError,
    compute_cell_membership, resolve_topology_compatible_corners, LeafCellSpatialIndex,
    COASTTIDEX_VERSION
)
from .tide_cache import TideCacheIntegrityError, TideCacheTimeSeriesReader

# 露出分析质量控制位掩膜定义 / Exposure QC Bitmask Definitions
QC_EXP_VALID = 0                         # 0: 正常高保真解算 / Normal high-fidelity computation
QC_EXP_DEGRADED_CELL = 1                 # bit 0: 四叉树单元部分角点降级插值 / Degraded interpolation in quad cell
QC_EXP_INSUFFICIENT_NODES = 2            # bit 1: 缺少足够有效控制节点 / Insufficient valid control nodes
QC_EXP_DATUM_APPROX = 4                  # bit 2: 基准面偏移采用多边形近似 / Datum offset from polygon approximation
QC_EXP_TERMINAL_UNAVAILABLE = 8          # bit 3: 终端时刻采样缺失或不可用 / Terminal endpoint unavailable
QC_EXP_TERMINAL_APPROX = QC_EXP_TERMINAL_UNAVAILABLE  # 向后兼容别名 / Backward compatibility alias
QC_EXP_PARTIAL_VALID_TIME = 16           # bit 4: 时间序列存在无效数据间隙 / Temporal invalid gaps present
QC_EXP_PERMANENTLY_SUBMERGED = 32        # bit 5: 全有效时段常时淹没 / Permanently submerged throughout valid time
QC_EXP_PERMANENTLY_EXPOSED = 64          # bit 6: 全有效时段常时露出 / Permanently exposed throughout valid time
QC_EXP_NODATA = 65535                    # 0xFFFF: 无效/陆地屏蔽像元 (UInt16 NoData) / Invalid/Masked pixel

# NoData 规范常量
NODATA_FLOAT32 = -9999.0
NODATA_EVENT_COUNT = np.iinfo(np.uint32).max  # 4294967295 (UInt32 NoData, 彻底与合法 0 次事件解耦)
NODATA_QC = 65535


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
    terminal_timestamp_seconds: Optional[float] = None,
    requested_total_window_sec: Optional[float] = None
) -> Dict[str, Any]:
    """
    单点一维连续潜在天文潮露出时间域基准算法 (包含跨界线性插值与事件统计)。
    1D reference/oracle algorithm for continuous potential tidal exposure with crossing interpolation.

    参数 / Parameters:
        water_levels: 各采样时刻的水位序列 [米] / Water level series at sample timestamps [m]
        timestamps_seconds: 各采样时刻的纪元秒时间戳 / Epoch timestamps in seconds
        elevation: 地形高程 z [米] / Terrain surface elevation z [m]
        terminal_water_level: 终端时刻 H(t_end) [米]，用于闭合最后一个半开区间 / Terminal water level H(t_end) [m]
        terminal_timestamp_seconds: 终端时刻纪元秒 / Terminal epoch timestamp in seconds
        requested_total_window_sec: 显式指定的全时段请求窗口跨度 (秒)，缺失 terminal 采样时用于保留完整时间分母

    返回 / Returns:
        包含累计露出时长、露出比例、最长连续露出、平均事件时长与事件次数的字典。
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
        total_window_sec = float(terminal_timestamp_seconds - timestamps_seconds[0])
    else:
        wl_seq = np.asarray(water_levels, dtype=float)
        ts_seq = np.asarray(timestamps_seconds, dtype=float)
        if requested_total_window_sec is not None:
            total_window_sec = float(requested_total_window_sec)
        elif len(ts_seq) > 1:
            dt_nom = (ts_seq[-1] - ts_seq[0]) / (len(ts_seq) - 1)
            total_window_sec = float(ts_seq[-1] + dt_nom - ts_seq[0])
        else:
            total_window_sec = float(ts_seq[-1] - ts_seq[0]) if len(ts_seq) > 1 else 1.0

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
            denom = float(h0 - h1)
            r = (h0 - z) / denom if denom != 0.0 else 0.0
            r = max(0.0, min(1.0, r))
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
    if current_exposure_sec > 0.0:
        event_count += 1
        if current_exposure_sec > max_continuous_sec:
            max_continuous_sec = current_exposure_sec

    cum_exp_h = total_exposure_sec / 3600.0
    max_cont_h = max_continuous_sec / 3600.0
    valid_dur_h = valid_duration_sec / 3600.0
    total_win_h = total_window_sec / 3600.0

    # 常时全淹没像元事件发生次数规范为 0
    if cum_exp_h <= 1e-6:
        event_count = 0

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


class _AtomicExposureWriter:
    """
    露出分析 7 大 GeoTIFF 产物原子写入安全包装器 (Atomic GeoTIFF Writer Bundle)。
    
    工程特性说明 (Engineering Architecture Note):
    采用单文件级原子替换 (Per-file atomic replacement via os.replace)，并非跨 7 个文件的分布式数据库事务。
    通过临时文件 (*.tmp.tif) 隔离写入，在全部处理完成并通过校验后逐一替换到正式路径。
    若打开 (open)、写入 (write_window) 或分块处理流程发生任何异常/用户取消，
    严格在 try/finally 中关闭句柄并清理全部已创建的 *.tmp.tif 临时文件，绝不残留损坏的半成品。
    """
    def __init__(self, paths: ExposureProductPaths, profile: dict):
        self.paths = paths
        self.profile = profile
        self.tmp_paths = {
            "frac": f"{paths.exposure_fraction_path}.tmp.tif",
            "dur": f"{paths.exposure_duration_h_path}.tmp.tif",
            "max": f"{paths.exposure_max_continuous_h_path}.tmp.tif",
            "mean": f"{paths.exposure_mean_event_h_path}.tmp.tif",
            "count": f"{paths.exposure_event_count_path}.tmp.tif",
            "val_time": f"{paths.exposure_valid_time_fraction_path}.tmp.tif",
            "qc": f"{paths.exposure_qc_path}.tmp.tif"
        }
        self.target_paths = {
            "frac": paths.exposure_fraction_path,
            "dur": paths.exposure_duration_h_path,
            "max": paths.exposure_max_continuous_h_path,
            "mean": paths.exposure_mean_event_h_path,
            "count": paths.exposure_event_count_path,
            "val_time": paths.exposure_valid_time_fraction_path,
            "qc": paths.exposure_qc_path
        }
        self.handles = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.cleanup_tmp()
        else:
            if self.handles:
                self.close_and_commit()

    def open(self, metadata_tags: Optional[Dict[str, str]] = None):
        prof_f32 = self.profile.copy()
        prof_f32.update({'count': 1, 'dtype': 'float32', 'nodata': NODATA_FLOAT32, 'compress': 'deflate', 'predictor': 2})
        if self.profile.get('width', 0) >= 16 and self.profile.get('height', 0) >= 16:
            prof_f32['tiled'] = True
            prof_f32['blockxsize'] = 512
            prof_f32['blockysize'] = 512

        prof_u32 = prof_f32.copy()
        prof_u32.update({'dtype': 'uint32', 'nodata': NODATA_EVENT_COUNT, 'predictor': 1})

        prof_u16 = prof_f32.copy()
        prof_u16.update({'dtype': 'uint16', 'nodata': NODATA_QC, 'predictor': 1})

        try:
            # 确保目标目录存在
            for p in self.target_paths.values():
                d = os.path.dirname(os.path.abspath(p))
                if d:
                    os.makedirs(d, exist_ok=True)

            self.handles["frac"] = rasterio.open(self.tmp_paths["frac"], 'w', **prof_f32)
            self.handles["dur"] = rasterio.open(self.tmp_paths["dur"], 'w', **prof_f32)
            self.handles["max"] = rasterio.open(self.tmp_paths["max"], 'w', **prof_f32)
            self.handles["mean"] = rasterio.open(self.tmp_paths["mean"], 'w', **prof_f32)
            self.handles["val_time"] = rasterio.open(self.tmp_paths["val_time"], 'w', **prof_f32)
            self.handles["count"] = rasterio.open(self.tmp_paths["count"], 'w', **prof_u32)
            self.handles["qc"] = rasterio.open(self.tmp_paths["qc"], 'w', **prof_u16)

            if metadata_tags:
                for k, ds in self.handles.items():
                    tags = metadata_tags.copy()
                    tags["PRODUCT_ROLE"] = k
                    ds.update_tags(**tags)
        except Exception:
            self.cleanup_tmp()
            raise


    def write_window(
        self,
        window: Window,
        frac: np.ndarray,
        dur: np.ndarray,
        max_c: np.ndarray,
        mean_e: np.ndarray,
        count: np.ndarray,
        val_time: np.ndarray,
        qc: np.ndarray
    ):
        self.handles["frac"].write(frac.astype(np.float32), 1, window=window)
        self.handles["dur"].write(dur.astype(np.float32), 1, window=window)
        self.handles["max"].write(max_c.astype(np.float32), 1, window=window)
        self.handles["mean"].write(mean_e.astype(np.float32), 1, window=window)
        self.handles["count"].write(count.astype(np.uint32), 1, window=window)
        self.handles["val_time"].write(val_time.astype(np.float32), 1, window=window)
        self.handles["qc"].write(qc.astype(np.uint16), 1, window=window)

    def finalize(self):
        self.close_and_commit()

    def close_and_commit(self):
        # 关闭所有临时文件句柄
        for ds in self.handles.values():
            try:
                ds.close()
            except Exception:
                pass
        self.handles.clear()

        # 原子替换 (Atomic Rename)
        for k in self.tmp_paths:
            tmp_f = self.tmp_paths[k]
            target_f = self.target_paths[k]
            if os.path.exists(tmp_f):
                os.replace(tmp_f, target_f)

    def cleanup_tmp(self):
        for ds in self.handles.values():
            try:
                ds.close()
            except Exception:
                pass
        self.handles.clear()
        for tmp_f in self.tmp_paths.values():
            try:
                if os.path.exists(tmp_f):
                    os.remove(tmp_f)
            except Exception:
                pass


class _InMemoryTimeSeriesReader:
    """内部轻量适配器：用于内存中已附带时序的节点对象切片 (保证测试与轻量调用接口统一)"""
    def __init__(self, nodes: List[ControlNode]):
        self.nodes = nodes
        self.num_nodes = len(nodes)

    def read_chunk(self, node_indices: List[int], start_time_idx: int, end_time_idx: int) -> np.ndarray:
        chunk_len = max(0, end_time_idx - start_time_idx)
        if len(node_indices) == 0:
            return np.zeros((0, chunk_len), dtype=np.float32)
        res = np.empty((len(node_indices), chunk_len), dtype=np.float32)
        for i, nid in enumerate(node_indices):
            if nid < 0 or nid >= self.num_nodes:
                raise TideCacheIntegrityError(f"请求的控制节点索引越界: {nid}")
            node = self.nodes[nid]
            raw = getattr(node, "tide_msl_raw", None)
            if raw is None:
                raw = getattr(node, "water_levels_msl", None)
            if raw is not None and len(raw) >= end_time_idx:
                res[i, :] = raw[start_time_idx:end_time_idx]
            else:
                res[i, :] = np.nan
        return res


def compute_2d_vec(
    wl_nodes: np.ndarray,
    term_nodes: Optional[np.ndarray],
    times_sec: np.ndarray,
    terminal_ts: Optional[float],
    weights: np.ndarray,
    z: np.ndarray
) -> Dict[str, np.ndarray]:
    """
    纯二维向量化潜在天文潮露出时间域计算函数 (用于算法验证与小块高保真解算)。
    Vectorized 2D exposure calculator for validation and testing.

    参数:
        wl_nodes: (n_times, K) 控制节点时序
        term_nodes: (K,) 终端时刻水位
        times_sec: (n_times,) 时间戳 (秒)
        terminal_ts: 终端时刻时间戳 (秒)
        weights: (K, rows, cols) 或 (K, 1, 1) 权重矩阵
        z: (rows, cols) 地形高程矩阵
    """
    n_times, k_nodes = wl_nodes.shape
    rows, cols = z.shape

    total_exp_sec = np.zeros((rows, cols), dtype=np.float64)
    max_cont_sec = np.zeros((rows, cols), dtype=np.float64)
    curr_exp_sec = np.zeros((rows, cols), dtype=np.float64)
    ev_count = np.zeros((rows, cols), dtype=np.uint32)
    valid_dur_sec = np.zeros((rows, cols), dtype=np.float64)

    # 规范化 weights 形状至 (K, rows, cols)
    if weights.ndim == 3 and weights.shape[1] == 1 and weights.shape[2] == 1:
        w_broad = np.broadcast_to(weights, (k_nodes, rows, cols))
    else:
        w_broad = weights

    def _get_slice(t_idx: int) -> np.ndarray:
        node_vals = wl_nodes[t_idx, :] # (K,)
        # 逐节点累加，避免一次性分配 (K, rows, cols)
        h = np.zeros((rows, cols), dtype=np.float32)
        for k in range(k_nodes):
            h += w_broad[k] * node_vals[k]
        return h

    prev_wl = np.full((rows, cols), np.nan, dtype=np.float32)
    prev_ts = 0.0

    for step_idx in range(n_times):
        h1 = _get_slice(step_idx)
        t1 = times_sec[step_idx]

        if step_idx == 0:
            prev_wl = h1
            prev_ts = t1
            continue

        dt = float(t1 - prev_ts)
        if dt <= 0.0:
            prev_wl = h1
            prev_ts = t1
            continue

        val_step = np.isfinite(prev_wl) & np.isfinite(h1)
        inval_step = (~val_step)
        cut_ongoing = inval_step & (curr_exp_sec > 0.0)
        if np.any(cut_ongoing):
            ev_count[cut_ongoing] += 1
            max_cont_sec[cut_ongoing] = np.maximum(max_cont_sec[cut_ongoing], curr_exp_sec[cut_ongoing])
            curr_exp_sec[cut_ongoing] = 0.0

        if np.any(val_step):
            valid_dur_sec[val_step] += dt
            h0_v = prev_wl[val_step]
            h1_v = h1[val_step]
            z_v = z[val_step]
            c_v = curr_exp_sec[val_step]
            t_v = total_exp_sec[val_step]
            m_v = max_cont_sec[val_step]
            cnt_v = ev_count[val_step]

            e0 = (h0_v <= z_v)
            e1 = (h1_v <= z_v)

            # Case A: 全露出
            m_a = e0 & e1
            if np.any(m_a):
                c_v[m_a] += dt
                t_v[m_a] += dt
                m_v[m_a] = np.maximum(m_v[m_a], c_v[m_a])

            # Case B: 全淹没
            m_b = (~e0) & (~e1)
            ong_b = m_b & (c_v > 0.0)
            if np.any(ong_b):
                cnt_v[ong_b] += 1
                m_v[ong_b] = np.maximum(m_v[ong_b], c_v[ong_b])
                c_v[ong_b] = 0.0

            # Case C: 淹没 -> 露出
            m_c = (~e0) & e1
            if np.any(m_c):
                den_c = h0_v[m_c] - h1_v[m_c]
                r_c = np.where(np.abs(den_c) > 1e-9, (h0_v[m_c] - z_v[m_c]) / den_c, 0.0)
                r_c = np.clip(r_c, 0.0, 1.0)
                dt_e_c = (1.0 - r_c) * dt

                ong_c = (c_v[m_c] > 0.0)
                if np.any(ong_c):
                    cnt_v[m_c] = np.where(ong_c, cnt_v[m_c] + 1, cnt_v[m_c])
                    m_v[m_c] = np.where(ong_c, np.maximum(m_v[m_c], c_v[m_c]), m_v[m_c])

                c_v[m_c] = dt_e_c
                t_v[m_c] += dt_e_c
                m_v[m_c] = np.maximum(m_v[m_c], c_v[m_c])

            # Case D: 露出 -> 淹没
            m_d = e0 & (~e1)
            if np.any(m_d):
                den_d = h1_v[m_d] - h0_v[m_d]
                r_d = np.where(np.abs(den_d) > 1e-9, (z_v[m_d] - h0_v[m_d]) / den_d, 0.0)
                r_d = np.clip(r_d, 0.0, 1.0)
                dt_e_d = r_d * dt

                c_v[m_d] += dt_e_d
                t_v[m_d] += dt_e_d
                m_v[m_d] = np.maximum(m_v[m_d], c_v[m_d])
                cnt_v[m_d] += 1
                c_v[m_d] = 0.0

            curr_exp_sec[val_step] = c_v
            total_exp_sec[val_step] = t_v
            max_cont_sec[val_step] = m_v
            ev_count[val_step] = cnt_v

        prev_wl = h1
        prev_ts = t1

    # 终端时刻跨界处理
    if term_nodes is not None and terminal_ts is not None and terminal_ts > prev_ts:
        dt_term = float(terminal_ts - prev_ts)
        h_term = np.zeros((rows, cols), dtype=np.float32)
        for k in range(k_nodes):
            h_term += w_broad[k] * term_nodes[k]

        val_term = np.isfinite(prev_wl) & np.isfinite(h_term)
        inval_term = (~val_term)
        cut_ong_term = inval_term & (curr_exp_sec > 0.0)
        if np.any(cut_ong_term):
            ev_count[cut_ong_term] += 1
            max_cont_sec[cut_ong_term] = np.maximum(max_cont_sec[cut_ong_term], curr_exp_sec[cut_ong_term])
            curr_exp_sec[cut_ong_term] = 0.0

        if np.any(val_term):
            valid_dur_sec[val_term] += dt_term
            h0_v = prev_wl[val_term]
            h1_v = h_term[val_term]
            z_v = z[val_term]
            c_v = curr_exp_sec[val_term]
            t_v = total_exp_sec[val_term]
            m_v = max_cont_sec[val_term]
            cnt_v = ev_count[val_term]

            e0 = (h0_v <= z_v)
            e1 = (h1_v <= z_v)

            m_a = e0 & e1
            if np.any(m_a):
                c_v[m_a] += dt_term
                t_v[m_a] += dt_term
                m_v[m_a] = np.maximum(m_v[m_a], c_v[m_a])

            m_b = (~e0) & (~e1)
            ong_b = m_b & (c_v > 0.0)
            if np.any(ong_b):
                cnt_v[ong_b] += 1
                m_v[ong_b] = np.maximum(m_v[ong_b], c_v[ong_b])
                c_v[ong_b] = 0.0

            m_c = (~e0) & e1
            if np.any(m_c):
                den_c = h0_v[m_c] - h1_v[m_c]
                r_c = np.where(np.abs(den_c) > 1e-9, (h0_v[m_c] - z_v[m_c]) / den_c, 0.0)
                r_c = np.clip(r_c, 0.0, 1.0)
                dt_e_c = (1.0 - r_c) * dt_term

                ong_c = (c_v[m_c] > 0.0)
                if np.any(ong_c):
                    cnt_v[m_c] = np.where(ong_c, cnt_v[m_c] + 1, cnt_v[m_c])
                    m_v[m_c] = np.where(ong_c, np.maximum(m_v[m_c], c_v[m_c]), m_v[m_c])

                c_v[m_c] = dt_e_c
                t_v[m_c] += dt_e_c
                m_v[m_c] = np.maximum(m_v[m_c], c_v[m_c])

            m_d = e0 & (~e1)
            if np.any(m_d):
                den_d = h1_v[m_d] - h0_v[m_d]
                r_d = np.where(np.abs(den_d) > 1e-9, (z_v[m_d] - h0_v[m_d]) / den_d, 0.0)
                r_d = np.clip(r_d, 0.0, 1.0)
                dt_e_d = r_d * dt_term

                c_v[m_d] += dt_e_d
                t_v[m_d] += dt_e_d
                m_v[m_d] = np.maximum(m_v[m_d], c_v[m_d])
                cnt_v[m_d] += 1
                c_v[m_d] = 0.0

            curr_exp_sec[val_term] = c_v
            total_exp_sec[val_term] = t_v
            max_cont_sec[val_term] = m_v
            ev_count[val_term] = cnt_v

    # 序列末端收口
    tail_ong = (curr_exp_sec > 0.0)
    if np.any(tail_ong):
        ev_count[tail_ong] += 1
        max_cont_sec[tail_ong] = np.maximum(max_cont_sec[tail_ong], curr_exp_sec[tail_ong])
        curr_exp_sec[tail_ong] = 0.0

    cum_exp_h = total_exp_sec / 3600.0
    max_cont_h = max_cont_sec / 3600.0

    # 常时全淹没像元规范事件数为 0
    perm_sub = (cum_exp_h <= 1e-6)
    ev_count[perm_sub] = 0

    ev_count_safe = np.where(ev_count > 0, ev_count, 1)
    mean_ev_h = np.where(ev_count > 0, cum_exp_h / ev_count_safe, 0.0)

    val_safe = np.where(valid_dur_sec > 0.0, valid_dur_sec, 1.0)
    exp_frac_pct = np.where(valid_dur_sec > 0.0, (total_exp_sec / val_safe) * 100.0, 0.0)

    tot_win_sec = float((terminal_ts if terminal_ts else times_sec[-1]) - times_sec[0])
    win_safe = max(1.0, tot_win_sec)
    val_time_frac_pct = (valid_dur_sec / win_safe) * 100.0

    return {
        "exposure_fraction_pct": exp_frac_pct.astype(np.float32),
        "cumulative_exposure_h": cum_exp_h.astype(np.float32),
        "max_continuous_exposure_h": max_cont_h.astype(np.float32),
        "mean_event_duration_h": mean_ev_h.astype(np.float32),
        "event_count": ev_count.astype(np.uint32),
        "valid_time_fraction_pct": val_time_frac_pct.astype(np.float32)
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
    requested_time_start_sec: Optional[float] = None,
    requested_time_end_sec: Optional[float] = None,
    cache_path: Optional[str] = None,
    time_reader: Optional[Any] = None,
    labeled_coarse: Optional[np.ndarray] = None,
    downsample_factor: int = 1,
    h_coarse: int = 0,
    w_coarse: int = 0,
    metadata_tags: Optional[Dict[str, str]] = None,
    allow_overwrite: bool = True,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    cancel_event: Optional[Any] = None,
    spatial_index: Optional[Any] = None
) -> Dict[str, Any]:
    """
    基于自适应控制网格与分块流式累加，解算高分辨率 DEM 的潜在天文潮露出时间域栅格产品。
    Stream bilinear spatial interpolation and continuous exposure accumulation without creating 3D full cubes.

    科学不变量与工程安全 / Invariants & Engineering Safety:
    1. 内存中绝不构造 rows x cols x time_chunk 的 3D 像元矩阵 (彻底杜绝 1GB+ 临时时序占用)；
    2. 绝不在内存中一次性分配全量节点时序 (彻底杜绝 n_nodes x n_time 巨型矩阵)；
    3. 彻底去除像元级 Python 循环，全部采用 2D NumPy 数组矢量化状态转移；
    4. 严格遵循 target-mask-derived topology guard，禁止跨连通域屏障插值；
    5. 无效控制角点严格重归一化权重，绝不以 0m 混入插值；
    6. 全产物 *.tmp.tif 原子写入，异常/取消时零残留；
    7. event_count 无效值为 4294967295，常时淹没为 0。
    """
    t_start = time.time()

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

    with rasterio.open(dem_path) as src_dem:
        profile = src_dem.profile.copy()
        w = src_dem.width
        h = src_dem.height
        nodata_val = src_dem.nodata
        trans = src_dem.transform
        raster_bounds = (src_dem.bounds.left, src_dem.bounds.bottom, src_dem.bounds.right, src_dem.bounds.top)

    # 预计算时间序列及步长 (秒)
    n_time = len(time_series_utc)
    ts_seconds = np.asarray([pd.Timestamp(t).timestamp() for t in time_series_utc], dtype=np.float64)

    has_terminal = (terminal_node_tides is not None and terminal_timestamp_sec is not None)
    if requested_time_start_sec is not None and requested_time_end_sec is not None:
        total_time_span_sec = float(requested_time_end_sec - requested_time_start_sec)
    elif has_terminal and terminal_timestamp_sec is not None:
        total_time_span_sec = float(terminal_timestamp_sec - ts_seconds[0])
    else:
        dt_nom = (ts_seconds[-1] - ts_seconds[0]) / (n_time - 1) if n_time > 1 else 1800.0
        total_time_span_sec = float((ts_seconds[-1] + dt_nom) - ts_seconds[0])

    if total_time_span_sec <= 0.0:
        total_time_span_sec = 1.0

    if has_terminal and terminal_timestamp_sec is not None:
        terminal_dt_sec = float(terminal_timestamp_sec - ts_seconds[-1])
    else:
        terminal_dt_sec = 0.0

    # 建立轻量节点静态属性与映射 (禁止在此处分配 n_nodes x n_time 巨幅矩阵)
    n_nodes = len(nodes)
    node_offsets = np.array([float(n.static_offset_m) if np.isfinite(n.static_offset_m) else 0.0 for n in nodes], dtype=np.float32)
    node_valid_mask = np.array([bool(n.valid) for n in nodes], dtype=bool)
    node_components = np.array([int(getattr(n, "component_id", 0)) for n in nodes], dtype=np.int32)
    node_qc_arr = np.array([int(getattr(n, "qc_bitmask", 0)) for n in nodes], dtype=np.uint32)
    node_datum_approx = (node_qc_arr & 16) != 0
    node_id_to_idx = {getattr(n, "node_id", i): i for i, n in enumerate(nodes)}

    # 初始化时序切片读取器
    reader = None
    reader_needs_close = False
    if time_reader is not None:
        reader = time_reader
    elif cache_path is not None:
        reader = TideCacheTimeSeriesReader(cache_path).open()
        reader_needs_close = True
    else:
        reader = _InMemoryTimeSeriesReader(nodes)

    # 终端时刻各节点水位 (严格校验有效性，无效设为 NaN)
    terminal_node_wl = np.full(n_nodes, np.nan, dtype=np.float32)
    if has_terminal:
        for idx, node in enumerate(nodes):
            if node.valid and idx < len(terminal_node_tides):
                t_val = terminal_node_tides[idx]
                if np.isfinite(t_val):
                    terminal_node_wl[idx] = float(t_val + node_offsets[idx])

    # 准备元数据标签
    source_tz = str(metadata_tags.get("TIMEZONE", "UTC")) if metadata_tags else "UTC"

    req_start_tag = str(metadata_tags.get("REQUESTED_TIME_START", metadata_tags.get("TIME_START", ""))) if metadata_tags else ""
    if not req_start_tag:
        req_start_tag = str(time_series_utc[0])

    req_end_tag = str(metadata_tags.get("REQUESTED_TIME_END", metadata_tags.get("TIME_END", ""))) if metadata_tags else ""
    if not req_end_tag:
        if has_terminal and terminal_timestamp_sec is not None:
            req_end_tag = pd.Timestamp(terminal_timestamp_sec, unit="s", tz="UTC").isoformat()
        else:
            req_end_tag = str(time_series_utc[-1])

    if requested_time_start_sec is not None:
        start_utc_epoch = float(requested_time_start_sec)
        start_utc_iso = pd.Timestamp(start_utc_epoch, unit="s", tz="UTC").isoformat()
    elif metadata_tags and "TIME_START_UTC_EPOCH" in metadata_tags and metadata_tags["TIME_START_UTC_EPOCH"]:
        start_utc_epoch = float(metadata_tags["TIME_START_UTC_EPOCH"])
        start_utc_iso = str(metadata_tags.get("TIME_START_UTC", pd.Timestamp(start_utc_epoch, unit="s", tz="UTC").isoformat()))
    else:
        start_utc_epoch = float(ts_seconds[0])
        start_utc_iso = str(metadata_tags.get("TIME_START_UTC", pd.Timestamp(start_utc_epoch, unit="s", tz="UTC").isoformat())) if metadata_tags else pd.Timestamp(start_utc_epoch, unit="s", tz="UTC").isoformat()

    if requested_time_end_sec is not None:
        end_utc_epoch = float(requested_time_end_sec)
        end_utc_iso = pd.Timestamp(end_utc_epoch, unit="s", tz="UTC").isoformat()
    elif has_terminal and terminal_timestamp_sec is not None:
        end_utc_epoch = float(terminal_timestamp_sec)
        end_utc_iso = pd.Timestamp(end_utc_epoch, unit="s", tz="UTC").isoformat()
    elif metadata_tags and "TIME_END_UTC_EPOCH" in metadata_tags and metadata_tags["TIME_END_UTC_EPOCH"]:
        end_utc_epoch = float(metadata_tags["TIME_END_UTC_EPOCH"])
        end_utc_iso = str(metadata_tags.get("TIME_END_UTC", pd.Timestamp(end_utc_epoch, unit="s", tz="UTC").isoformat()))
    else:
        end_utc_epoch = float(ts_seconds[-1])
        end_utc_iso = str(metadata_tags.get("TIME_END_UTC", pd.Timestamp(end_utc_epoch, unit="s", tz="UTC").isoformat())) if metadata_tags else pd.Timestamp(end_utc_epoch, unit="s", tz="UTC").isoformat()

    full_meta = {
        "SOFTWARE": "CoastTideX v1.6 Beta",
        "COASTTIDEX_VERSION": COASTTIDEX_VERSION,
        "PRODUCT_TYPE": "Potential Astronomical Tidal Exposure Duration Suite",
        "EXPOSURE_DEFINITION": "Potential Astronomical Tidal Exposure Duration under a Fixed Representative Terrain (Inundated: H>z, Exposed: H<=z)",
        "TIDE_MODEL": "FES2022b",
        "VERTICAL_DATUM": str(target_datum).upper(),
        "DEM_DATUM": str(target_datum).lower(),
        "TIME_START": req_start_tag,
        "TIME_END": req_end_tag,
        "REQUESTED_TIME_START": req_start_tag,
        "REQUESTED_TIME_END": req_end_tag,
        "TIMEZONE": source_tz,
        "TIME_START_UTC": start_utc_iso,
        "TIME_END_UTC": end_utc_iso,
        "TIME_START_UTC_EPOCH": str(start_utc_epoch),
        "TIME_END_UTC_EPOCH": str(end_utc_epoch),
        "TERMINAL_SAMPLE_AVAILABLE": "true" if has_terminal else "false",
        "TIME_INTERVAL_SEMANTICS": "[start, end)",
        "TIME_SAMPLES": str(n_time),
        "SOURCE_DEM": os.path.basename(dem_path),
        "CACHE_SIGNATURE": str(metadata_tags.get("CACHE_SIGNATURE", "")) if metadata_tags else "",
        "CACHE_SCHEMA_VERSION": str(metadata_tags.get("CACHE_SCHEMA_VERSION", "1.2")) if metadata_tags else "1.2"
    }
    if metadata_tags:
        for k, v in metadata_tags.items():
            if v is not None and k not in full_meta:
                full_meta[k] = str(v)

    writer = _AtomicExposureWriter(output_paths, profile)
    writer.open(metadata_tags=full_meta)

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
            raise TideCacheIntegrityError("QuadCell 缺少节点索引属性，无法识别角点控制节点！")
        for nid in raw_ids:
            if nid not in node_id_to_idx:
                raise TideCacheIntegrityError(f"QuadCell 引用了不存在的控制节点 ID {nid}，严禁降级回退！")
        return tuple(node_id_to_idx[nid] for nid in raw_ids)

    if spatial_index is None:
        spatial_index = LeafCellSpatialIndex(cells, bounds=raster_bounds)
    else:
        spatial_index = spatial_index
    total_input_valid_pixels = 0
    total_solved_pixels = 0

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

                    total_input_valid_pixels += int(np.count_nonzero(valid_dem_mask))

                    # 初始化当前块的输出与状态数组
                    out_frac = np.full((r_len, c_len), NODATA_FLOAT32, dtype=np.float32)
                    out_dur = np.full((r_len, c_len), NODATA_FLOAT32, dtype=np.float32)
                    out_max = np.full((r_len, c_len), NODATA_FLOAT32, dtype=np.float32)
                    out_mean = np.full((r_len, c_len), NODATA_FLOAT32, dtype=np.float32)
                    out_count = np.full((r_len, c_len), NODATA_EVENT_COUNT, dtype=np.uint32)
                    out_val_time = np.full((r_len, c_len), NODATA_FLOAT32, dtype=np.float32)
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

                        # 映射粗粒度拓扑连通域
                        if labeled_coarse is not None and downsample_factor > 0 and h_coarse > 0 and w_coarse > 0:
                            r_c = np.clip(rows_grid // downsample_factor, 0, h_coarse - 1)
                            c_c = np.clip(cols_grid // downsample_factor, 0, w_coarse - 1)
                            pixel_comps = labeled_coarse[r_c, c_c]
                        else:
                            pixel_comps = np.zeros((r_len, c_len), dtype=np.int32)

                        # 在像元级维护二维流式连续事件状态变量 (随时间步迭代更新，绝不分配 3D 矩阵)
                        total_exp_sec = np.zeros((r_len, c_len), dtype=np.float64)
                        max_cont_sec = np.zeros((r_len, c_len), dtype=np.float64)
                        curr_exp_sec = np.zeros((r_len, c_len), dtype=np.float64)
                        ev_count = np.zeros((r_len, c_len), dtype=np.uint32)
                        valid_dur_sec = np.zeros((r_len, c_len), dtype=np.float64)
                        prev_wl = np.full((r_len, c_len), np.nan, dtype=np.float32)
                        prev_ts = 0.0

                        # 检索落在当前块中的四叉树叶单元 (利用空间桶索引加速)
                        b_x0, b_y1 = rasterio.transform.xy(trans, r_off, c_off, offset='ul')
                        b_x1, b_y0 = rasterio.transform.xy(trans, r_off + r_len, c_off + c_len, offset='lr')
                        min_x, max_x = min(b_x0, b_x1), max(b_x0, b_x1)
                        min_y, max_y = min(b_y0, b_y1), max(b_y0, b_y1)

                        block_cells = spatial_index.query_intersecting_cells((min_x, min_y, max_x, max_y))

                        # 预计算当前窗口内像元的几何插值与拓扑连通映射 (Precompute cell-pixel mapping)
                        cell_mappings = []

                        for cell in block_cells:
                            c_idxs = _get_cell_node_idxs(cell)
                            cx0, cx1, cy0, cy1 = _get_cell_bounds(cell)

                            in_cell = compute_cell_membership(
                                xs_arr, ys_arr,
                                (cx0, cy0, cx1, cy1),
                                raster_bounds=raster_bounds
                            ) & valid_dem_mask
                            if not np.any(in_cell):
                                continue

                            px_xs = xs_arr[in_cell]
                            px_ys = ys_arr[in_cell]
                            px_c = pixel_comps[in_cell]
                            dx = max(1e-9, cx1 - cx0)
                            dy = max(1e-9, cy1 - cy0)
                            u = np.clip((px_xs - cx0) / dx, 0.0, 1.0)
                            v = np.clip((px_ys - cy0) / dy, 0.0, 1.0)

                            w00 = ((1.0 - u) * (1.0 - v)).astype(np.float32)
                            w10 = (u * (1.0 - v)).astype(np.float32)
                            w01 = ((1.0 - u) * v).astype(np.float32)
                            w11 = (u * v).astype(np.float32)
                            weights_all = [w00, w10, w01, w11]

                            # 检查 4 个角点的有效性与拓扑连通域
                            val_sub = [node_valid_mask[idx_n] for idx_n in c_idxs]
                            comp_sub = [node_components[idx_n] for idx_n in c_idxs]
                            datum_approx_sub = any(node_datum_approx[idx_n] for idx_n in c_idxs)

                            # 按当前像元拓扑连通域分别处理
                            unq_p_comps = np.unique(px_c)
                            for p_comp in unq_p_comps:
                                mask_comp = (px_c == p_comp)
                                sub_in_cell = np.zeros_like(in_cell)
                                sub_in_cell[in_cell] = mask_comp
                                if not np.any(sub_in_cell):
                                    continue

                                usable_weights_raw = [w[mask_comp] for w in weights_all]
                                usable_corners, norm_weights, top_flags = resolve_topology_compatible_corners(
                                    pixel_component=int(p_comp),
                                    corner_components=comp_sub,
                                    corner_valids=val_sub,
                                    corner_weights=usable_weights_raw
                                )

                                if top_flags["insufficient_nodes"] or norm_weights is None:
                                    out_qc[sub_in_cell] = QC_EXP_INSUFFICIENT_NODES
                                    continue

                                usable_indices = tuple(c_idxs[k] for k in usable_corners)

                                # 记录 QC
                                cell_qc = QC_EXP_VALID
                                if top_flags["spatial_fallback"]:
                                    cell_qc |= QC_EXP_DEGRADED_CELL
                                if datum_approx_sub:
                                    cell_qc |= QC_EXP_DATUM_APPROX
                                out_qc[sub_in_cell] = cell_qc

                                cell_mappings.append((sub_in_cell, usable_indices, norm_weights))

                        # 找出当前分块所需的所有唯一控制节点全局索引 (按需提取局部节点)
                        block_node_set = set()
                        for _, idxs, _ in cell_mappings:
                            for gi in idxs:
                                block_node_set.add(gi)
                        block_node_indices = sorted(list(block_node_set))
                        local_map = {gi: li for li, gi in enumerate(block_node_indices)}
                        local_offsets = node_offsets[block_node_indices] if len(block_node_indices) > 0 else np.array([], dtype=np.float32)

                        # 时间维按 time_chunk_size 分块流式推进 (按需从 reader 切片，绝不全量常驻)
                        for chunk_start in range(0, n_time, time_chunk_size):
                            chunk_end = min(n_time, chunk_start + time_chunk_size)
                            chunk_len = chunk_end - chunk_start
                            if len(block_node_indices) > 0:
                                raw_chunk = reader.read_chunk(block_node_indices, chunk_start, chunk_end)
                                wl_chunk = raw_chunk + local_offsets[:, None]
                            else:
                                wl_chunk = np.empty((0, chunk_len), dtype=np.float32)

                            for t_local in range(chunk_len):
                                step_idx = chunk_start + t_local
                                t1 = ts_seconds[step_idx]

                                h1_slice = np.full((r_len, c_len), np.nan, dtype=np.float32)
                                for sub_mask, idxs, w_mat in cell_mappings:
                                    local_idxs = [local_map[gi] for gi in idxs]
                                    node_wls = wl_chunk[local_idxs, t_local]
                                    val_nodes = np.isfinite(node_wls)
                                    if np.all(val_nodes):
                                        h1_slice[sub_mask] = np.sum(w_mat * node_wls[:, None], axis=0)
                                    elif np.any(val_nodes):
                                        sub_w = w_mat[val_nodes, :]
                                        sum_w = np.sum(sub_w, axis=0)
                                        sum_w_safe = np.where(sum_w > 1e-9, sum_w, 1.0)
                                        norm_sub_w = sub_w / sum_w_safe[None, :]
                                        h1_slice[sub_mask] = np.sum(norm_sub_w * node_wls[val_nodes, None], axis=0)

                                if step_idx == 0:
                                    prev_wl = h1_slice
                                    prev_ts = t1
                                    continue

                                dt = float(t1 - prev_ts)
                                if dt <= 0.0:
                                    prev_wl = h1_slice
                                    prev_ts = t1
                                    continue

                                val_step = np.isfinite(prev_wl) & np.isfinite(h1_slice) & valid_dem_mask

                                inval_step = (~val_step) & valid_dem_mask
                                cut_ongoing = inval_step & (curr_exp_sec > 0.0)
                                if np.any(cut_ongoing):
                                    ev_count[cut_ongoing] += 1
                                    max_cont_sec[cut_ongoing] = np.maximum(max_cont_sec[cut_ongoing], curr_exp_sec[cut_ongoing])
                                    curr_exp_sec[cut_ongoing] = 0.0

                                if np.any(val_step):
                                    valid_dur_sec[val_step] += dt
                                    h0_v = prev_wl[val_step]
                                    h1_v = h1_slice[val_step]
                                    z_v = dem_block[val_step]
                                    c_v = curr_exp_sec[val_step]
                                    t_v = total_exp_sec[val_step]
                                    m_v = max_cont_sec[val_step]
                                    cnt_v = ev_count[val_step]

                                    e0 = (h0_v <= z_v)
                                    e1 = (h1_v <= z_v)

                                    # Case A: 全露出
                                    m_a = e0 & e1
                                    if np.any(m_a):
                                        c_v[m_a] += dt
                                        t_v[m_a] += dt
                                        m_v[m_a] = np.maximum(m_v[m_a], c_v[m_a])

                                    # Case B: 全淹没
                                    m_b = (~e0) & (~e1)
                                    ong_b = m_b & (c_v > 0.0)
                                    if np.any(ong_b):
                                        cnt_v[ong_b] += 1
                                        m_v[ong_b] = np.maximum(m_v[ong_b], c_v[ong_b])
                                        c_v[ong_b] = 0.0

                                    # Case C: 淹没 -> 露出 (~e0 & e1)
                                    m_c = (~e0) & e1
                                    if np.any(m_c):
                                        den_c = h0_v[m_c] - h1_v[m_c]
                                        r_c = np.where(np.abs(den_c) > 1e-9, (h0_v[m_c] - z_v[m_c]) / den_c, 0.0)
                                        r_c = np.clip(r_c, 0.0, 1.0)
                                        dt_e_c = (1.0 - r_c) * dt

                                        ong_c = (c_v[m_c] > 0.0)
                                        if np.any(ong_c):
                                            cnt_v[m_c] = np.where(ong_c, cnt_v[m_c] + 1, cnt_v[m_c])
                                            m_v[m_c] = np.where(ong_c, np.maximum(m_v[m_c], c_v[m_c]), m_v[m_c])

                                        c_v[m_c] = dt_e_c
                                        t_v[m_c] += dt_e_c
                                        m_v[m_c] = np.maximum(m_v[m_c], c_v[m_c])

                                    # Case D: 露出 -> 淹没 (e0 & ~e1)
                                    m_d = e0 & (~e1)
                                    if np.any(m_d):
                                        den_d = h1_v[m_d] - h0_v[m_d]
                                        r_d = np.where(np.abs(den_d) > 1e-9, (z_v[m_d] - h0_v[m_d]) / den_d, 0.0)
                                        r_d = np.clip(r_d, 0.0, 1.0)
                                        dt_e_d = r_d * dt

                                        c_v[m_d] += dt_e_d
                                        t_v[m_d] += dt_e_d
                                        m_v[m_d] = np.maximum(m_v[m_d], c_v[m_d])
                                        cnt_v[m_d] += 1
                                        c_v[m_d] = 0.0

                                    curr_exp_sec[val_step] = c_v
                                    total_exp_sec[val_step] = t_v
                                    max_cont_sec[val_step] = m_v
                                    ev_count[val_step] = cnt_v

                                prev_wl = h1_slice
                                prev_ts = t1

                        val_term_step = np.zeros((r_len, c_len), dtype=bool)
                        # 处理终端时刻采样 (Terminal Sample Crossing)
                        if has_terminal and terminal_dt_sec > 0.0:
                            h_term_slice = np.full((r_len, c_len), np.nan, dtype=np.float32)
                            for sub_mask, idxs, w_mat in cell_mappings:
                                term_wls = terminal_node_wl[list(idxs)]
                                val_term = np.isfinite(term_wls)
                                if np.all(val_term):
                                    h_term_slice[sub_mask] = np.sum(w_mat * term_wls[:, None], axis=0)
                                elif np.any(val_term):
                                    sub_w = w_mat[val_term, :]
                                    sum_w = np.sum(sub_w, axis=0)
                                    sum_w_safe = np.where(sum_w > 1e-9, sum_w, 1.0)
                                    norm_sub_w = sub_w / sum_w_safe[None, :]
                                    h_term_slice[sub_mask] = np.sum(norm_sub_w * term_wls[val_term, None], axis=0)

                            dt_term = terminal_dt_sec
                            val_term_step = np.isfinite(prev_wl) & np.isfinite(h_term_slice) & valid_dem_mask

                            inval_term = (~val_term_step) & valid_dem_mask
                            cut_ong_term = inval_term & (curr_exp_sec > 0.0)
                            if np.any(cut_ong_term):
                                ev_count[cut_ong_term] += 1
                                max_cont_sec[cut_ong_term] = np.maximum(max_cont_sec[cut_ong_term], curr_exp_sec[cut_ong_term])
                                curr_exp_sec[cut_ong_term] = 0.0

                            if np.any(val_term_step):
                                valid_dur_sec[val_term_step] += dt_term
                                h0_v = prev_wl[val_term_step]
                                h1_v = h_term_slice[val_term_step]
                                z_v = dem_block[val_term_step]
                                c_v = curr_exp_sec[val_term_step]
                                t_v = total_exp_sec[val_term_step]
                                m_v = max_cont_sec[val_term_step]
                                cnt_v = ev_count[val_term_step]

                                e0 = (h0_v <= z_v)
                                e1 = (h1_v <= z_v)

                                m_a = e0 & e1
                                if np.any(m_a):
                                    c_v[m_a] += dt_term
                                    t_v[m_a] += dt_term
                                    m_v[m_a] = np.maximum(m_v[m_a], c_v[m_a])

                                m_b = (~e0) & (~e1)
                                ong_b = m_b & (c_v > 0.0)
                                if np.any(ong_b):
                                    cnt_v[ong_b] += 1
                                    m_v[ong_b] = np.maximum(m_v[ong_b], c_v[ong_b])
                                    c_v[ong_b] = 0.0

                                m_c = (~e0) & e1
                                if np.any(m_c):
                                    den_c = h0_v[m_c] - h1_v[m_c]
                                    r_c = np.where(np.abs(den_c) > 1e-9, (h0_v[m_c] - z_v[m_c]) / den_c, 0.0)
                                    r_c = np.clip(r_c, 0.0, 1.0)
                                    dt_e_c = (1.0 - r_c) * dt_term

                                    ong_c = (c_v[m_c] > 0.0)
                                    if np.any(ong_c):
                                        cnt_v[m_c] = np.where(ong_c, cnt_v[m_c] + 1, cnt_v[m_c])
                                        m_v[m_c] = np.where(ong_c, np.maximum(m_v[m_c], c_v[m_c]), m_v[m_c])

                                    c_v[m_c] = dt_e_c
                                    t_v[m_c] += dt_e_c
                                    m_v[m_c] = np.maximum(m_v[m_c], c_v[m_c])

                                m_d = e0 & (~e1)
                                if np.any(m_d):
                                    den_d = h1_v[m_d] - h0_v[m_d]
                                    r_d = np.where(np.abs(den_d) > 1e-9, (z_v[m_d] - h0_v[m_d]) / den_d, 0.0)
                                    r_d = np.clip(r_d, 0.0, 1.0)
                                    dt_e_d = r_d * dt_term

                                    c_v[m_d] += dt_e_d
                                    t_v[m_d] += dt_e_d
                                    m_v[m_d] = np.maximum(m_v[m_d], c_v[m_d])
                                    cnt_v[m_d] += 1
                                    c_v[m_d] = 0.0

                                curr_exp_sec[val_term_step] = c_v
                                total_exp_sec[val_term_step] = t_v
                                max_cont_sec[val_term_step] = m_v
                                ev_count[val_term_step] = cnt_v

                        # 末端事件闭合收口
                        unclosed = (curr_exp_sec > 0.0) & valid_dem_mask
                        if np.any(unclosed):
                            ev_count[unclosed] += 1
                            max_cont_sec[unclosed] = np.maximum(max_cont_sec[unclosed], curr_exp_sec[unclosed])
                            curr_exp_sec[unclosed] = 0.0

                        # 计算当前块最终输出矩阵
                        val_pixels = (valid_dur_sec > 0.0) & valid_dem_mask
                        if np.any(val_pixels):
                            dur_h = total_exp_sec[val_pixels] / 3600.0
                            val_dur_h = valid_dur_sec[val_pixels] / 3600.0
                            max_h = max_cont_sec[val_pixels] / 3600.0
                            cnts = ev_count[val_pixels]

                            # 常时全淹没像元事件发生次数规范为 0
                            is_perm_sub = (dur_h <= 1e-6)
                            cnts = np.where(is_perm_sub, 0, cnts)

                            out_dur[val_pixels] = dur_h.astype(np.float32)
                            out_max[val_pixels] = max_h.astype(np.float32)
                            out_count[val_pixels] = cnts.astype(np.uint32)
                            out_frac[val_pixels] = (dur_h / val_dur_h * 100.0).astype(np.float32)

                            safe_cnts = np.where(cnts > 0, cnts, 1)
                            out_mean[val_pixels] = np.where(cnts > 0, dur_h / safe_cnts, 0.0).astype(np.float32)

                            val_time_ratio = valid_dur_sec[val_pixels] / total_time_span_sec
                            out_val_time[val_pixels] = (val_time_ratio * 100.0).astype(np.float32)

                            qc_v = out_qc[val_pixels]
                            term_unavail_mask = ~val_term_step[val_pixels]
                            qc_v = np.where(term_unavail_mask, qc_v | QC_EXP_TERMINAL_UNAVAILABLE, qc_v)

                            # 极端常时状态标记
                            is_perm_exp = (np.abs(dur_h - val_dur_h) <= 1e-4)
                            qc_v = np.where(is_perm_sub, qc_v | QC_EXP_PERMANENTLY_SUBMERGED, qc_v)
                            qc_v = np.where(is_perm_exp, qc_v | QC_EXP_PERMANENTLY_EXPOSED, qc_v)

                            # 时间间隙标记
                            has_time_gaps = (val_time_ratio < 0.9999)
                            qc_v = np.where(has_time_gaps, qc_v | QC_EXP_PARTIAL_VALID_TIME, qc_v)

                            out_qc[val_pixels] = qc_v.astype(np.uint16)

                    # 写入当前窗口到临时 GeoTIFF 文件
                    writer.write_window(
                        win,
                        frac=out_frac,
                        dur=out_dur,
                        max_c=out_max,
                        mean_e=out_mean,
                        count=out_count,
                        val_time=out_val_time,
                        qc=out_qc
                    )

                    # 记录成功解算的有效像元
                    solved_in_block = int(np.count_nonzero(valid_dem_mask & (out_frac != NODATA_FLOAT32) & np.isfinite(out_frac)))
                    total_solved_pixels += solved_in_block

                    processed_blocks += 1
                    if progress_callback:
                        pct = int(processed_blocks / total_blocks * 100)
                        progress_callback(pct, f"潜在天文潮露出栅格反演中 ({processed_blocks}/{total_blocks} 块)...")

        # 成功写完所有分块后，原子替换为最终产物文件
        writer.close_and_commit()

    except Exception:
        # 异常或取消时清理临时文件，绝不留下损坏的半成品文件
        writer.cleanup_tmp()
        raise
    finally:
        if reader_needs_close and reader is not None:
            try:
                reader.close()
            except Exception:
                pass

    elapsed = time.time() - t_start
    if progress_callback:
        progress_callback(100, f"潜在天文潮露出时间域栅格产品解算完成 (耗时 {elapsed:.2f}s)！")

    return {
        "status": "COMPLETED",
        "elapsed_seconds": round(elapsed, 2),
        "products": output_paths,
        "input_valid_pixels": int(total_input_valid_pixels),
        "solved_pixels": int(total_solved_pixels),
        "unsolved_pixels": int(max(0, total_input_valid_pixels - total_solved_pixels))
    }
