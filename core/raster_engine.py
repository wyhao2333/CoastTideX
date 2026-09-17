"""
CoastTideX 空间栅格潮位与自适应控制网格淹没频率解算引擎 (Spatial Raster Tide Engine v1.6)
支持大范围 GeoTIFF 逐像元空间潮位解算与基于自适应四叉树控制网格 (Adaptive Quadtree Control Grid)
的年度潜在天文潮淹没频率计算。

核心特性:
    1. Snapshot Raster: 逐有效像元真实空间 FES2022b 潮位解算、静态垂直基准转换与 UInt16 QC 掩膜生成；
    2. Annual Inundation Raster: 结合自适应四叉树控制网格 (Adaptive Refinement)、
       经验互补累积分布函数 (CCDF) 与拓扑连通性保护 (Valid-mask Topology-aware Interpolation Guard)，
       高效解算 10m/30m 高分辨率 DEM 全年潜在天文潮淹没频率 (0~100%)；
    3. 拓扑屏障保护: 结合物理尺度 (topology_max_resolution_m) 与保守连通域分析阻止跨越陆地/NoData 屏障盲目插值；
    4. 2D 矩形分块流式 I/O (默认 512x512)，支持超大影像流式吞吐与动态内存控制 (M_batch x T)；
    5. 严格保持原始栅格 CRS、单位感知 (米/英尺)、网格仿射变换、尺寸与像元中心对齐；
    6. 原子级写入保护 (*.tmp.tif)、UInt16 位掩码 QC 与富元数据 (Provenance Metadata) 嵌入。
"""

import os
import time
import warnings
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterator, Optional, Tuple, List, Dict, Any, Set, Callable

import numpy as np
import pandas as pd
import rasterio
import rasterio.warp
import rasterio.windows
from rasterio.windows import Window
import scipy.ndimage

try:
    from pyproj import Transformer, CRS
except ImportError:
    Transformer = None
    CRS = None

from .utils import (
    load_app_config, resolve_project_path, normalize_longitude,
    circular_longitude_span, convert_time_to_utc, compute_inundation_frequency
)
from .datum_engine import DatumTransformer, DatumDataError
from .tide_engine import FESTidePredictor


class RasterCalculationCancelled(RuntimeError):
    """用户主动取消栅格解算任务异常"""
    pass


class ExistingOutputError(FileExistsError):
    """目标正式产物已存在且当前策略不允许覆盖时抛出"""
    pass


class RasterMemoryLimitError(RuntimeError):
    """自适应控制网格节点数超出常驻内存预算限制异常"""
    pass


def estimate_control_node_memory(node_count: int, time_samples: int, dtype_bytes: int = 4) -> float:
    """
    估算控制节点常驻内存大小 (MB)。
    根据常驻排序时序数组 (float32, 4字节/像元) 计算所需物理内存。
    """
    return (float(node_count) * float(time_samples) * float(dtype_bytes)) / (1024.0 * 1024.0)


# 质量控制位掩码定义 (UInt16 Bitmask)
QC_BIT_VALID = 0                      # 0: 无异常 / 完全有效高保真解算
QC_BIT_FES_EXTRAPOLATED = 1 << 0      # bit 0 (1): 近岸动力学外推 (quality_flag < 0)
QC_BIT_SPATIAL_FALLBACK = 1 << 1      # bit 1 (2): 空间插值降级 (可用控制节点少于4个)
QC_BIT_INSUFFICIENT_NODES = 1 << 2    # bit 2 (4): 周围缺乏有效海洋控制节点 (无法插值，输出 NoData)
QC_BIT_DATUM_INVALID = 1 << 3         # bit 3 (8): 垂直基准转换数据无效 (NaN)
QC_BIT_DATUM_SOURCE_APPROX = 1 << 4   # bit 4 (16): 使用了几何多边形近似基准源
QC_BIT_MIN_SPACING_REACHED = 1 << 5   # bit 5 (32): 网格细分达最小允许间距但仍未满足公差阈值
QC_BIT_CONNECTIVITY_FALLBACK = 1 << 6 # bit 6 (64): 跨越拓扑阻隔，非同连通域或边界隔离
QC_BIT_FES_VALIDITY_BOUNDARY = 1 << 7 # bit 7 (128): FES/海洋有效性突变边界 (海陆交界或外推不连续)
QC_BIT_MAX_REFINEMENT_REACHED = 1 << 8# bit 8 (256): 达到最大细分深度限制但未达公差
QC_NODATA = 65535                     # UInt16 NoData 填充值

# 向后兼容别名
QC_VALID = QC_BIT_VALID
QC_FES_EXTRAPOLATED = QC_BIT_FES_EXTRAPOLATED
QC_SPATIAL_FALLBACK = QC_BIT_SPATIAL_FALLBACK
QC_INSUFFICIENT_NODES = QC_BIT_INSUFFICIENT_NODES
QC_DATUM_INVALID = QC_BIT_DATUM_INVALID
QC_DATUM_APPROX_SOURCE = QC_BIT_DATUM_SOURCE_APPROX
QC_MIN_SPACING_REACHED = QC_BIT_MIN_SPACING_REACHED
QC_CONNECTIVITY_FALLBACK = QC_BIT_CONNECTIVITY_FALLBACK
QC_FES_VALIDITY_BOUNDARY = QC_BIT_FES_VALIDITY_BOUNDARY
QC_MAX_REFINEMENT_REACHED = QC_BIT_MAX_REFINEMENT_REACHED


@dataclass
class RasterInfo:
    """栅格基础属性元数据对象"""
    path: str
    crs: str
    is_projected: bool
    width: int
    height: int
    transform: rasterio.Affine
    bounds: Tuple[float, float, float, float]
    resolution: Tuple[float, float]
    nodata: Optional[float]
    valid_pixel_count: int
    total_pixel_count: int
    dtype: str
    unit_name: str = "metre"
    unit_factor: float = 1.0
    file_size_bytes: int = 0
    mtime_ns: int = 0

    @property
    def formatted_resolution(self) -> str:
        """
        智能格式化空间分辨率字符串。
        - 对地理坐标系 (如 WGS84 EPSG:4326)，单位为度 (°)，自动保留合适有效数字，
          并结合影像中心纬度估算地表等效米制尺寸 (约 XX m)；
        - 对投影坐标系，自适应识别米 (m)、千米 (km) 或英尺 (ft)。
        """
        rx, ry = abs(float(self.resolution[0])), abs(float(self.resolution[1]))
        if not self.is_projected:
            if rx < 0.001 or ry < 0.001:
                deg_str = f"{rx:.6f}° × {ry:.6f}°"
            elif rx < 0.01 or ry < 0.01:
                deg_str = f"{rx:.5f}° × {ry:.5f}°"
            else:
                deg_str = f"{rx:.4f}° × {ry:.4f}°"

            mid_lat = (self.bounds[1] + self.bounds[3]) / 2.0
            cos_lat = max(0.001, np.cos(np.radians(mid_lat)))
            m_x = rx * 111320.0 * cos_lat
            m_y = ry * 111320.0

            def _fmt_dist(d_m):
                if d_m >= 1000.0:
                    return f"{d_m / 1000.0:.2f} km"
                elif d_m >= 10.0:
                    return f"{d_m:.1f} m"
                else:
                    return f"{d_m:.2f} m"

            return f"{deg_str} (约 {_fmt_dist(m_x)} × {_fmt_dist(m_y)})"
        else:
            u_name = str(self.unit_name).lower()
            if "foot" in u_name or "ft" in u_name:
                m_x = rx * self.unit_factor
                m_y = ry * self.unit_factor
                return f"{rx:.2f} ft × {ry:.2f} ft (约 {m_x:.2f} m × {m_y:.2f} m)"
            elif rx >= 1000.0 or ry >= 1000.0:
                return f"{rx / 1000.0:.2f} km × {ry / 1000.0:.2f} km"
            elif rx < 1.0 or ry < 1.0:
                return f"{rx:.3f} m × {ry:.3f} m"
            else:
                return f"{rx:.2f} m × {ry:.2f} m"

    @property
    def formatted_crs(self) -> str:
        """
        友好格式化坐标参考系统 (CRS) 字符串。
        例如 'EPSG:4326 (WGS 84)' 或 'EPSG:32651 (WGS 84 / UTM zone 51N)'。
        """
        if not self.crs:
            return "未知坐标系 (Unknown CRS)"

        try:
            if CRS is not None:
                proj_crs = CRS.from_user_input(self.crs)
                epsg = proj_crs.to_epsg()
                name = proj_crs.name
                if epsg and name:
                    return f"EPSG:{epsg} ({name})"
                elif epsg:
                    return f"EPSG:{epsg}"
                elif name:
                    return f"{self.crs} ({name})"
        except Exception:
            pass

        crs_str = str(self.crs)
        if len(crs_str) > 35:
            if '"' in crs_str:
                parts = crs_str.split('"')
                if len(parts) >= 2 and parts[1].strip():
                    return parts[1].strip()
            return crs_str[:32] + "..."
        return crs_str


@dataclass
class ControlNode:
    """自适应控制网格节点"""
    node_id: int
    x: float
    y: float
    lon: float
    lat: float
    water_levels_sorted: np.ndarray = field(default_factory=lambda: np.array([], dtype=np.float32))
    valid: bool = False
    quality_flag: int = 0
    static_offset_m: float = 0.0
    qc_bitmask: int = QC_BIT_VALID
    component_id: int = 0
    qc_code: Optional[int] = None
    tide_msl_raw: Optional[np.ndarray] = None

    def __post_init__(self):
        if self.qc_code is not None:
            self.qc_bitmask = self.qc_code
        else:
            self.qc_code = self.qc_bitmask


@dataclass
class QuadCell:
    """四叉树自适应细分网格单元"""
    cell_id: int
    x_min: float
    y_min: float
    x_max: float
    y_max: float
    level: int
    node_a: ControlNode
    node_b: ControlNode
    node_c: ControlNode
    node_d: ControlNode
    qc_min_spacing_reached: bool = False
    qc_max_refinement_reached: bool = False
    qc_validity_boundary: bool = False
    max_error_pct: float = 0.0

    @property
    def x0(self) -> float:
        return self.x_min

    @property
    def x1(self) -> float:
        return self.x_max

    @property
    def y0(self) -> float:
        return self.y_min

    @property
    def y1(self) -> float:
        return self.y_max

    @property
    def node_indices(self) -> Tuple[int, int, int, int]:
        return (self.node_a.node_id, self.node_b.node_id, self.node_c.node_id, self.node_d.node_id)


@dataclass
class RasterResultSummary:
    """栅格解算任务结果摘要"""
    output_path: str
    qc_output_path: Optional[str]
    mode: str
    width: int
    height: int
    valid_pixels: int              # 兼容字段，等于 solved_pixels
    total_pixels: int
    input_valid_pixels: int = 0    # 输入 DEM 非 NoData 像元数
    solved_pixels: int = 0         # 成功解算的像元数
    unsolved_pixels: int = 0       # 未能解算 (NaN) 的像元数
    control_nodes_count: Optional[int] = 0
    elapsed_seconds: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


def build_support_topology(
    info: RasterInfo,
    topology_max_resolution_m: float = 200.0,
    topology_valid_fraction_threshold: float = 0.20,
    block_size: int = 512,
    cancel_event = None
) -> Tuple[np.ndarray, int, int, int, int, np.ndarray, int]:
    """
    基于 DEM 构建粗粒度连通域支撑掩膜 (Support Topology)。

    【科学语义区分 (Scientific Distinction)】:
    - TARGET MASK: 细粒度 (10m) 像元级有效地形高程，指示哪些像元需要计算并输出淹没频率；
    - FES SUPPORT: 空间宏观上具有潮汐水动力学支撑的有效控制节点；
    - SUPPORT TOPOLOGY: 粗粒度连通域划分，用于防止跨越陆地/NoData 屏障的错误插值。
      特别说明: Target NoData 不等同于物理水力隔离屏障 (Physical Barrier)。

    返回:
        (labeled_coarse, num_features, downsample_factor, h_coarse, w_coarse, coarse_active_support, input_valid_count)
    """
    if info.is_projected:
        pixel_size_m = min(abs(info.resolution[0]), abs(info.resolution[1])) * info.unit_factor
    else:
        mid_lat = (info.bounds[1] + info.bounds[3]) / 2.0
        cos_lat = max(0.01, np.cos(np.radians(mid_lat)))
        pixel_size_m = min(abs(info.resolution[0]) * 111320.0 * cos_lat, abs(info.resolution[1]) * 111320.0)

    downsample_factor = max(1, int(round(topology_max_resolution_m / max(1e-3, pixel_size_m))))
    h_coarse = int(np.ceil(info.height / downsample_factor))
    w_coarse = int(np.ceil(info.width / downsample_factor))

    coarse_active_support = np.zeros((h_coarse, w_coarse), dtype=bool)
    coarse_valid_counts = np.zeros((h_coarse, w_coarse), dtype=np.int32)

    cell_h = np.minimum(downsample_factor, info.height - np.arange(h_coarse) * downsample_factor)
    cell_w = np.minimum(downsample_factor, info.width - np.arange(w_coarse) * downsample_factor)
    coarse_total_counts = np.outer(cell_h, cell_w).astype(np.int32)

    input_valid_count = 0
    with rasterio.open(info.path) as src_dem:
        for r_off in range(0, info.height, block_size):
            bh = min(block_size, info.height - r_off)
            for c_off in range(0, info.width, block_size):
                if cancel_event is not None and cancel_event.is_set():
                    raise RasterCalculationCancelled("用户取消了任务。")
                bw = min(block_size, info.width - c_off)
                win = Window(c_off, r_off, bw, bh)
                chunk = src_dem.read(1, window=win)
                if info.nodata is not None and np.isfinite(info.nodata):
                    v_mask = ~np.isclose(chunk, info.nodata) & np.isfinite(chunk)
                else:
                    v_mask = np.isfinite(chunk)
                input_valid_count += int(np.count_nonzero(v_mask))

                if np.any(v_mask):
                    rows_loc, cols_loc = np.where(v_mask)
                    r_glob = rows_loc + r_off
                    c_glob = cols_loc + c_off
                    r_c = np.clip(r_glob // downsample_factor, 0, h_coarse - 1)
                    c_c = np.clip(c_glob // downsample_factor, 0, w_coarse - 1)
                    coarse_active_support[r_c, c_c] = True
                    np.add.at(coarse_valid_counts, (r_c, c_c), 1)

    if input_valid_count == 0:
        raise ValueError(f"输入 DEM '{info.path}' 中未找到任何有效地形高程像元 (全部为 NoData/NaN)。")

    valid_fractions = coarse_valid_counts / np.maximum(1, coarse_total_counts)
    coarse_connectivity = (valid_fractions >= topology_valid_fraction_threshold)

    labeled_coarse, num_features = scipy.ndimage.label(coarse_connectivity, structure=np.ones((3, 3)))
    return labeled_coarse, num_features, downsample_factor, h_coarse, w_coarse, coarse_active_support, input_valid_count


def stream_inundation_frequency_interpolation(
    info: RasterInfo,
    leaf_cells: List[QuadCell],
    labeled_coarse: np.ndarray,
    downsample_factor: int,
    h_coarse: int,
    w_coarse: int,
    input_valid_count: int,
    output_path: str,
    qc_output_path: Optional[str] = None,
    block_size: int = 512,
    metadata_tags: Optional[Dict[str, str]] = None,
    allow_overwrite: bool = True,
    control_nodes_count: Optional[int] = None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    cancel_event = None
) -> RasterResultSummary:
    """
    【共享流式插值内核 (Shared Interpolation Kernel)】
    负责在 2D DEM 上根据自适应叶单元与控制节点排序时序流式评估潜在天文潮淹没频率与 QC 掩膜。
    保证 Direct 单影像路径与 Stage 2 Cache 路径 100% 数学与科学连通域拓扑一致。
    """
    t_start = time.time()
    if qc_output_path is None:
        base, ext = os.path.splitext(output_path)
        qc_output_path = f"{base}_qc{ext}"

    if not allow_overwrite:
        if os.path.exists(output_path):
            raise ExistingOutputError(f"输出文件已存在且未开启覆盖权限: {output_path}")
        if qc_output_path and os.path.exists(qc_output_path):
            raise ExistingOutputError(f"QC 输出文件已存在且未开启覆盖权限: {qc_output_path}")

    out_dir = os.path.dirname(os.path.abspath(output_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    tmp_output = f"{output_path}.tmp.tif"
    tmp_qc = f"{qc_output_path}.tmp.tif"

    min_x, min_y, max_x, max_y = info.bounds
    bucket_size_x = max(100.0, (max_x - min_x) / 32.0)
    bucket_size_y = max(100.0, (max_y - min_y) / 32.0)
    spatial_buckets: Dict[Tuple[int, int], List[QuadCell]] = defaultdict(list)

    for cell in leaf_cells:
        bx0 = int((cell.x_min - min_x) // bucket_size_x)
        bx1 = int((cell.x_max - min_x) // bucket_size_x)
        by0 = int((cell.y_min - min_y) // bucket_size_y)
        by1 = int((cell.y_max - min_y) // bucket_size_y)
        for bx in range(bx0, bx1 + 1):
            for by in range(by0, by1 + 1):
                spatial_buckets[(bx, by)].append(cell)

    # 遵守需求: 如果输入 DEM nodata 是可表示的有限 Float32 且不在有效淹没频率区间 [0, 100] 内，则输出继承该 nodata；否则回退为 NaN 防冲突
    if info.nodata is not None and np.isfinite(info.nodata) and not (0.0 <= float(info.nodata) <= 100.0):
        output_nodata = float(np.float32(info.nodata))
    else:
        output_nodata = np.nan

    out_profile = {
        'driver': 'GTiff',
        'height': info.height,
        'width': info.width,
        'count': 1,
        'dtype': rasterio.float32,
        'crs': rasterio.crs.CRS.from_user_input(info.crs),
        'transform': info.transform,
        'nodata': output_nodata,
        'compress': 'deflate',
        'predictor': 2,
    }
    if info.width >= 16 and info.height >= 16 and block_size >= 16:
        out_profile['tiled'] = True
        out_profile['blockxsize'] = min(512, max(16, (int(block_size) // 16) * 16))
        out_profile['blockysize'] = min(512, max(16, (int(block_size) // 16) * 16))
    else:
        out_profile['tiled'] = False

    qc_profile = out_profile.copy()
    qc_profile.update({
        'dtype': rasterio.uint16,
        'nodata': QC_NODATA,
        'predictor': 1
    })

    total_windows = int(np.ceil(info.width / block_size) * np.ceil(info.height / block_size))
    win_idx = 0
    solved_pixel_count = 0

    try:
        with rasterio.open(info.path) as src_dem,              rasterio.open(tmp_output, 'w', **out_profile) as dst_inund,              rasterio.open(tmp_qc, 'w', **qc_profile) as dst_qc:

            for r_off in range(0, info.height, block_size):
                bh = min(block_size, info.height - r_off)
                for c_off in range(0, info.width, block_size):
                    if cancel_event is not None and cancel_event.is_set():
                        raise RasterCalculationCancelled("用户取消了任务。")

                    bw = min(block_size, info.width - c_off)
                    window = Window(c_off, r_off, bw, bh)

                    dem_chunk = src_dem.read(1, window=window).astype(np.float32)
                    if info.nodata is not None and np.isfinite(info.nodata):
                        valid_dem_mask = ~np.isclose(dem_chunk, info.nodata) & np.isfinite(dem_chunk)
                    else:
                        valid_dem_mask = np.isfinite(dem_chunk)

                    inund_chunk = np.full(dem_chunk.shape, output_nodata, dtype=np.float32)
                    qc_chunk = np.full(dem_chunk.shape, QC_NODATA, dtype=np.uint16)

                    n_chunk_valid = int(np.count_nonzero(valid_dem_mask))
                    if n_chunk_valid > 0:
                        rows_local, cols_local = np.where(valid_dem_mask)
                        rows_global = rows_local + window.row_off
                        cols_global = cols_local + window.col_off

                        px_xs, px_ys = rasterio.transform.xy(info.transform, rows_global, cols_global, offset='center')
                        px_xs = np.asarray(px_xs, dtype=float)
                        px_ys = np.asarray(px_ys, dtype=float)
                        z_vals = dem_chunk[valid_dem_mask]

                        w_bounds = rasterio.windows.bounds(window, transform=info.transform)
                        win_x0 = min(w_bounds[0], w_bounds[2])
                        win_y0 = min(w_bounds[1], w_bounds[3])
                        win_x1 = max(w_bounds[0], w_bounds[2])
                        win_y1 = max(w_bounds[1], w_bounds[3])

                        wbx0 = int((win_x0 - min_x) // bucket_size_x)
                        wbx1 = int((win_x1 - min_x) // bucket_size_x)
                        wby0 = int((win_y0 - min_y) // bucket_size_y)
                        wby1 = int((win_y1 - min_y) // bucket_size_y)

                        candidate_cells: List[QuadCell] = []
                        seen_cids = set()
                        for bx in range(wbx0, wbx1 + 1):
                            for by in range(wby0, wby1 + 1):
                                for c in spatial_buckets.get((bx, by), []):
                                    if c.cell_id not in seen_cids:
                                        seen_cids.add(c.cell_id)
                                        candidate_cells.append(c)

                        intersecting_cells = [
                            c for c in candidate_cells
                            if not (c.x_max < win_x0 or c.x_min > win_x1 or c.y_max < win_y0 or c.y_min > win_y1)
                        ]

                        freq_out = np.full(n_chunk_valid, np.nan, dtype=np.float32)
                        qc_out = np.zeros(n_chunk_valid, dtype=np.uint16)

                        r_c_arr = np.clip(rows_global // downsample_factor, 0, h_coarse - 1)
                        c_c_arr = np.clip(cols_global // downsample_factor, 0, w_coarse - 1)
                        pixel_comps = labeled_coarse[r_c_arr, c_c_arr]

                        for cell in intersecting_cells:
                            is_east = (cell.x_max >= info.bounds[2] - 1e-6)
                            is_north = (cell.y_max >= info.bounds[3] - 1e-6)
                            x_in = (px_xs >= cell.x_min) & (px_xs <= cell.x_max if is_east else px_xs < cell.x_max)
                            y_in = (px_ys >= cell.y_min) & (px_ys <= cell.y_max if is_north else px_ys < cell.y_max)
                            in_cell = x_in & y_in
                            if not np.any(in_cell):
                                continue

                            sub_z = z_vals[in_cell]
                            sub_x = px_xs[in_cell]
                            sub_y = px_ys[in_cell]
                            sub_comps = pixel_comps[in_cell]

                            dx_cell = max(1e-6, cell.x_max - cell.x_min)
                            dy_cell = max(1e-6, cell.y_max - cell.y_min)
                            u = np.clip((sub_x - cell.x_min) / dx_cell, 0.0, 1.0)
                            v = np.clip((sub_y - cell.y_min) / dy_cell, 0.0, 1.0)

                            w_a = (1.0 - u) * (1.0 - v)
                            w_b = u * (1.0 - v)
                            w_c = (1.0 - u) * v
                            w_d = u * v

                            c_nodes = [
                                (cell.node_a, w_a),
                                (cell.node_b, w_b),
                                (cell.node_c, w_c),
                                (cell.node_d, w_d)
                            ]

                            valid_nodes = []
                            for n, w_vec in c_nodes:
                                if n.valid and len(n.water_levels_sorted) > 0:
                                    valid_nodes.append((n, w_vec))

                            sub_freq = np.full(len(sub_z), np.nan, dtype=np.float32)
                            sub_qc = np.zeros(len(sub_z), dtype=np.uint16)

                            cell_qc_flags = QC_BIT_VALID
                            if cell.qc_min_spacing_reached:
                                cell_qc_flags |= QC_BIT_MIN_SPACING_REACHED
                            if cell.qc_max_refinement_reached:
                                cell_qc_flags |= QC_BIT_MAX_REFINEMENT_REACHED
                            if cell.qc_validity_boundary:
                                cell_qc_flags |= QC_BIT_FES_VALIDITY_BOUNDARY

                            node_comps = {n.component_id for n, _ in valid_nodes}
                            non_z_node_comps = {c for c in node_comps if c > 0}

                            if len(valid_nodes) == 0:
                                sub_qc |= (cell_qc_flags | QC_BIT_INSUFFICIENT_NODES)
                            elif len(valid_nodes) == 4 and (len(non_z_node_comps) == 0 or (len(non_z_node_comps) == 1 and (sub_comps == list(non_z_node_comps)[0]).all())):
                                na, nb, nc, nd = cell.node_a, cell.node_b, cell.node_c, cell.node_d
                                fa = compute_inundation_frequency(na.water_levels_sorted, sub_z, as_percentage=True)
                                fb = compute_inundation_frequency(nb.water_levels_sorted, sub_z, as_percentage=True)
                                fc = compute_inundation_frequency(nc.water_levels_sorted, sub_z, as_percentage=True)
                                fd = compute_inundation_frequency(nd.water_levels_sorted, sub_z, as_percentage=True)

                                sub_freq = (w_a * fa + w_b * fb + w_c * fc + w_d * fd).astype(np.float32)
                                node_bits = na.qc_bitmask | nb.qc_bitmask | nc.qc_bitmask | nd.qc_bitmask
                                sub_qc |= (cell_qc_flags | node_bits)
                            else:
                                unique_p_comps = np.unique(sub_comps)
                                for p_comp in unique_p_comps:
                                    mask_pc = (sub_comps == p_comp)
                                    pc_z = sub_z[mask_pc]

                                    if p_comp > 0:
                                        if p_comp in non_z_node_comps:
                                            usable_nodes = [
                                                (n, w_vec[mask_pc]) for n, w_vec in valid_nodes
                                                if n.component_id == p_comp
                                            ]
                                        elif len(non_z_node_comps) == 0:
                                            # 所有节点未划分连通域 (全为 0)，允许作为单连通域整体插值
                                            usable_nodes = [(n, w_vec[mask_pc]) for n, w_vec in valid_nodes]
                                        else:
                                            usable_nodes = []
                                    else:
                                        # 像元处于未知连通域 (0): 仅当所有节点全归属同一连通域或全未划分时，方允许插值
                                        if len(non_z_node_comps) <= 1:
                                            usable_nodes = [(n, w_vec[mask_pc]) for n, w_vec in valid_nodes]
                                        else:
                                            usable_nodes = []

                                    if len(usable_nodes) == 0:
                                        sub_freq[mask_pc] = np.nan
                                        sub_qc[mask_pc] |= (cell_qc_flags | QC_BIT_INSUFFICIENT_NODES | QC_BIT_CONNECTIVITY_FALLBACK)
                                    else:
                                        total_w = sum(w for _, w in usable_nodes)
                                        total_w = np.where(total_w > 1e-6, total_w, 1.0)
                                        f_accum = np.zeros_like(pc_z, dtype=float)
                                        p_bits = cell_qc_flags
                                        if len(usable_nodes) < 4:
                                            p_bits |= QC_BIT_SPATIAL_FALLBACK
                                        if len(usable_nodes) < len(valid_nodes) or p_comp == 0:
                                            p_bits |= QC_BIT_CONNECTIVITY_FALLBACK

                                        for n, w_vec in usable_nodes:
                                            fn = compute_inundation_frequency(n.water_levels_sorted, pc_z, as_percentage=True)
                                            f_accum += (w_vec / total_w) * fn
                                            p_bits |= n.qc_bitmask

                                        sub_freq[mask_pc] = f_accum.astype(np.float32)
                                        sub_qc[mask_pc] |= p_bits

                            freq_out[in_cell] = sub_freq
                            qc_out[in_cell] = sub_qc

                        inund_chunk[valid_dem_mask] = freq_out
                        qc_chunk[valid_dem_mask] = qc_out
                        solved_pixel_count += int(np.count_nonzero(np.isfinite(freq_out)))

                    dst_inund.write(inund_chunk, 1, window=window)
                    dst_qc.write(qc_chunk, 1, window=window)

                    win_idx += 1
                    if progress_callback:
                        pct = int(60 + (win_idx / total_windows) * 38)
                        progress_callback(pct, f"正在流式写入淹没频率栅格 ({win_idx}/{total_windows} 块)...")

            if metadata_tags:
                dst_inund.update_tags(**metadata_tags)
                dst_qc.update_tags(**metadata_tags)

        # 原子重命名为正式文件 (安全替换，绝不提前 unlink)
        os.replace(tmp_output, output_path)
        os.replace(tmp_qc, qc_output_path)

    except Exception:
        for p in [tmp_output, tmp_qc]:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        raise

    elapsed = time.time() - t_start
    return RasterResultSummary(
        output_path=output_path,
        qc_output_path=qc_output_path,
        mode='inundation',
        width=info.width,
        height=info.height,
        valid_pixels=solved_pixel_count,
        total_pixels=info.total_pixel_count,
        input_valid_pixels=input_valid_count,
        solved_pixels=solved_pixel_count,
        unsolved_pixels=input_valid_count - solved_pixel_count,
        control_nodes_count=control_nodes_count if control_nodes_count is not None else len({cell.node_a.node_id for cell in leaf_cells} | {cell.node_b.node_id for cell in leaf_cells} | {cell.node_c.node_id for cell in leaf_cells} | {cell.node_d.node_id for cell in leaf_cells}),
        elapsed_seconds=elapsed,
        metadata=metadata_tags or {}
    )


class RasterTideEngine:
    """
    CoastTideX 空间栅格潮位解算核心引擎 (v1.4)。
    """

    def __init__(
        self,
        predictor: Optional[Any] = None,
        transformer: Optional[DatumTransformer] = None,
        tide_predictor: Optional[Any] = None,
        initial_control_spacing_m: Optional[float] = None,
        min_control_spacing_m: Optional[float] = None,
        inundation_error_tolerance_pct: Optional[float] = None,
        default_block_size: Optional[int] = None,
        topology_max_resolution_m: Optional[float] = None,
        topology_valid_fraction_threshold: Optional[float] = None,
        control_node_batch_size: Optional[int] = None,
        absolute_max_refinement_depth: Optional[int] = None,
        max_in_memory_control_nodes: Optional[int] = None,
        max_fes_evaluate_points: Optional[int] = None,
        **kwargs
    ):
        if tide_predictor is not None and predictor is None:
            predictor = tide_predictor
        self.config = load_app_config()
        self.predictor = predictor
        self.transformer = transformer or DatumTransformer()

        raster_cfg = self.config.get('raster', {})
        self.max_fes_evaluate_points = int(max_fes_evaluate_points if max_fes_evaluate_points is not None else raster_cfg.get('max_fes_evaluate_points', 500000))
        self.initial_control_spacing_m = float(initial_control_spacing_m if initial_control_spacing_m is not None else raster_cfg.get('initial_control_spacing_m', 4000.0))
        self.min_control_spacing_m = float(min_control_spacing_m if min_control_spacing_m is not None else raster_cfg.get('min_control_spacing_m', 500.0))
        self.inundation_error_tolerance_pct = float(inundation_error_tolerance_pct if inundation_error_tolerance_pct is not None else raster_cfg.get('inundation_error_tolerance_pct', 1.0))
        self.default_block_size = int(default_block_size if default_block_size is not None else raster_cfg.get('default_block_size', 512))
        self.topology_max_resolution_m = float(topology_max_resolution_m if topology_max_resolution_m is not None else raster_cfg.get('topology_max_resolution_m', 100.0))
        self.topology_valid_fraction_threshold = float(topology_valid_fraction_threshold if topology_valid_fraction_threshold is not None else raster_cfg.get('topology_valid_fraction_threshold', 0.5))
        self.control_node_batch_size = int(control_node_batch_size if control_node_batch_size is not None else raster_cfg.get('control_node_batch_size', 128))
        self.absolute_max_refinement_depth = int(absolute_max_refinement_depth if absolute_max_refinement_depth is not None else raster_cfg.get('absolute_max_refinement_depth', 12))
        self.max_in_memory_control_nodes = int(max_in_memory_control_nodes if max_in_memory_control_nodes is not None else raster_cfg.get('max_in_memory_control_nodes', 50000))

    def _get_predictor(self) -> FESTidePredictor:
        if self.predictor is None:
            self.predictor = FESTidePredictor()
        return self.predictor

    def inspect_raster(self, raster_path: str, compute_valid_count: bool = True) -> RasterInfo:
        """
        严密审查输入 GeoTIFF 栅格元数据、CRS、空间分辨率、轴单位及有效像元数量。
        """
        resolved_path = resolve_project_path(raster_path)
        if not os.path.exists(resolved_path):
            raise FileNotFoundError(f"未找到指定的栅格文件: {raster_path}")

        with rasterio.open(resolved_path) as src:
            if src.crs is None:
                raise ValueError(
                    f"输入栅格 '{raster_path}' 缺少坐标参考系 (CRS)。"
                    "CoastTideX 空间引擎要求输入 GeoTIFF 具备明确的地理或投影坐标系统。"
                )

            crs_str = src.crs.to_string()
            is_proj = not src.crs.is_geographic
            w = src.width
            h = src.height
            total_px = w * h
            nodata_val = src.nodata
            res_x, res_y = src.res
            bounds = (src.bounds.left, src.bounds.bottom, src.bounds.right, src.bounds.top)
            transform = src.transform
            dtype_str = str(src.dtypes[0])

            unit_name = "metre"
            unit_factor = 1.0
            if is_proj and CRS is not None:
                try:
                    proj_crs = CRS.from_user_input(src.crs)
                    if proj_crs.axis_info and len(proj_crs.axis_info) > 0:
                        ax = proj_crs.axis_info[0]
                        if ax.unit_name:
                            unit_name = ax.unit_name
                        if ax.unit_conversion_factor:
                            unit_factor = float(ax.unit_conversion_factor)
                except Exception:
                    pass

            valid_px = total_px
            if compute_valid_count:
                valid_px = 0
                for _, window in src.block_windows(1):
                    chunk = src.read(1, window=window)
                    if nodata_val is not None and np.isfinite(nodata_val):
                        valid_mask = ~np.isclose(chunk, nodata_val) & np.isfinite(chunk)
                    else:
                        valid_mask = np.isfinite(chunk)
                    valid_px += int(np.count_nonzero(valid_mask))

        fsize = 0
        mtime = 0
        try:
            st = os.stat(resolved_path)
            fsize = st.st_size
            mtime = st.st_mtime_ns
        except Exception:
            pass

        return RasterInfo(
            path=resolved_path,
            crs=crs_str,
            is_projected=is_proj,
            width=w,
            height=h,
            transform=transform,
            bounds=bounds,
            resolution=(res_x, res_y),
            nodata=nodata_val,
            valid_pixel_count=valid_px,
            total_pixel_count=total_px,
            dtype=dtype_str,
            unit_name=unit_name,
            unit_factor=unit_factor,
            file_size_bytes=fsize,
            mtime_ns=mtime
        )


    def calculate_exposure_raster(
        self,
        dem_path: str,
        output_paths = None,
        output_dir: Optional[str] = None,
        year: int = 2024,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        freq: str = "30min",
        dem_datum: str = "egm2008",
        constituents = "all",
        source_tz: str = "UTC",
        initial_control_spacing_m: Optional[float] = None,
        min_control_spacing_m: Optional[float] = None,
        inundation_error_tolerance_pct: Optional[float] = None,
        block_size: Optional[int] = None,
        time_chunk_size: int = 1000,
        strict: bool = True,
        progress_callback = None,
        cancel_event = None,
        inclusive: Optional[str] = None,
        target_mode: str = "intertidal",
        export_tide_cache_path: Optional[str] = None,
        allow_overwrite: bool = True
    ) -> Dict[str, Any]:
        """
        解算固定代表性地形条件下的潜在天文潮露出时间域 7 大空间栅格产品。
        若提供了 export_tide_cache_path，先构建或复用 Tide Cache，再执行露出反演；
        若未提供，生成临时 Tide Cache 供流水线流式解算。
        """
        import tempfile
        from .tide_cache import calculate_exposure_from_tide_cache, is_cache_complete

        need_cleanup = False
        if not export_tide_cache_path:
            td = tempfile.TemporaryDirectory()
            cache_p = os.path.join(td.name, "temp_grid_tide.nc")
            need_cleanup = True
        else:
            cache_p = export_tide_cache_path
            td = None

        try:
            if not os.path.exists(cache_p) or not is_cache_complete(cache_p) or allow_overwrite:
                def _prog_s1(p, msg):
                    if progress_callback:
                        progress_callback(int(p * 0.4), f"Stage 1: {msg}")

                self.build_tide_control_grid(
                    dem_path=dem_path,
                    export_tide_cache_path=cache_p,
                    year=year,
                    start_time=start_time,
                    end_time=end_time,
                    freq=freq,
                    dem_datum=dem_datum,
                    constituents=constituents,
                    source_tz=source_tz,
                    initial_control_spacing_m=initial_control_spacing_m,
                    min_control_spacing_m=min_control_spacing_m,
                    inundation_error_tolerance_pct=inundation_error_tolerance_pct,
                    strict=strict,
                    progress_callback=_prog_s1,
                    cancel_event=cancel_event,
                    inclusive=inclusive,
                    target_mode=target_mode,
                    allow_overwrite=allow_overwrite
                )

            def _prog_s2(p, msg):
                if progress_callback:
                    progress_callback(40 + int(p * 0.6), f"Stage 2: {msg}")

            res = calculate_exposure_from_tide_cache(
                dem_path=dem_path,
                cache_path=cache_p,
                output_dir=output_dir,
                output_paths=output_paths,
                block_size=block_size or 512,
                time_chunk_size=time_chunk_size,
                allow_overwrite=allow_overwrite,
                progress_callback=_prog_s2,
                cancel_event=cancel_event
            )
            return res
        finally:
            if need_cleanup and td:
                try:
                    td.cleanup()
                except Exception:
                    pass

    def iter_raster_windows(
        self,
        width: int,
        height: int,
        block_size: int = 512
    ) -> Iterator[Window]:
        """
        生成稳健的 2D 矩形块 (Block Windows) 迭代器。
        彻底避免横向通栏 (full-width strip) 带来的内存爆炸风险。
        """
        bs = max(64, int(block_size))
        for r_off in range(0, height, bs):
            h_chunk = min(bs, height - r_off)
            for c_off in range(0, width, bs):
                w_chunk = min(bs, width - c_off)
                yield Window(col_off=c_off, row_off=r_off, width=w_chunk, height=h_chunk)

    def pixel_centers_to_lonlat(
        self,
        transform: rasterio.Affine,
        crs_str: str,
        row_indices: np.ndarray,
        col_indices: np.ndarray
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        严密将栅格像元行/列索引转换为 WGS84 (EPSG:4326) 经纬度。
        严格采用像元中心 (Pixel Center, offset=0.5) 几何定义，杜绝像元左上角偏移误差。
        """
        xs, ys = rasterio.transform.xy(transform, row_indices, col_indices, offset='center')
        xs_arr = np.atleast_1d(np.asarray(xs, dtype=float))
        ys_arr = np.atleast_1d(np.asarray(ys, dtype=float))

        src_crs = rasterio.crs.CRS.from_user_input(crs_str)
        if src_crs.is_geographic:
            lons = normalize_longitude(xs_arr, to_360=False)
            lats = ys_arr
            return lons, lats

        if Transformer is not None:
            pyproj_crs = CRS.from_user_input(crs_str)
            transformer = Transformer.from_crs(pyproj_crs, "EPSG:4326", always_xy=True)
            lons, lats = transformer.transform(xs_arr, ys_arr)
            lons = normalize_longitude(np.asarray(lons, dtype=float), to_360=False)
            lats = np.asarray(lats, dtype=float)
            return lons, lats
        else:
            res_lons, res_lats = rasterio.warp.transform(src_crs, "EPSG:4326", xs_arr, ys_arr)
            lons = normalize_longitude(np.asarray(res_lons, dtype=float), to_360=False)
            lats = np.asarray(res_lats, dtype=float)
            return lons, lats

    def calculate_snapshot_raster(
        self,
        dem_path: Optional[str] = None,
        timestamp: Optional[str] = None,
        output_raster_path: Optional[str] = None,
        qc_output_path: Optional[str] = None,
        datum_target: str = "msl",
        constituents: str | list = "all",
        source_tz: str = "UTC",
        block_size: Optional[int] = None,
        strict: bool = True,
        progress_callback = None,
        cancel_event = None,
        input_raster_path: Optional[str] = None
    ) -> RasterResultSummary:
        """
        【Mode A】逐有效像元真实解算单时刻空间潮位与质量掩膜 (Snapshot Raster)。
        """
        if dem_path is None and input_raster_path is not None:
            dem_path = input_raster_path
        if dem_path is None:
            raise ValueError("calculate_snapshot_raster 需要传入 dem_path 或 input_raster_path")
        t_start = time.time()
        info = self.inspect_raster(dem_path, compute_valid_count=False)
        predictor = self._get_predictor()

        if block_size is None:
            block_size = self.default_block_size

        if qc_output_path is None:
            base, ext = os.path.splitext(output_raster_path)
            qc_output_path = f"{base}_qc{ext}"

        out_dir = os.path.dirname(os.path.abspath(output_raster_path))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        tmp_output = f"{output_raster_path}.tmp.tif"
        tmp_qc = f"{qc_output_path}.tmp.tif"

        ts_utc, _ = convert_time_to_utc(timestamp, source_tz=source_tz)
        ts_utc_str = ts_utc.strftime("%Y-%m-%d %H:%M:%S")

        out_profile = {
            'driver': 'GTiff',
            'height': info.height,
            'width': info.width,
            'count': 1,
            'dtype': rasterio.float32,
            'crs': rasterio.crs.CRS.from_user_input(info.crs),
            'transform': info.transform,
            'nodata': np.nan,
            'compress': 'deflate',
            'predictor': 2,
        }
        if info.width >= 16 and info.height >= 16 and block_size >= 16:
            out_profile['tiled'] = True
            out_profile['blockxsize'] = min(512, max(16, (int(block_size) // 16) * 16))
            out_profile['blockysize'] = min(512, max(16, (int(block_size) // 16) * 16))
        else:
            out_profile['tiled'] = False

        qc_profile = out_profile.copy()
        qc_profile.update({
            'dtype': rasterio.uint16,
            'nodata': QC_NODATA,
            'predictor': 1
        })

        total_windows = int(np.ceil(info.width / block_size) * np.ceil(info.height / block_size))
        win_idx = 0
        solved_total = 0
        input_valid_total = 0

        try:
            with rasterio.open(info.path) as src_dem,                  rasterio.open(tmp_output, 'w', **out_profile) as dst_out,                  rasterio.open(tmp_qc, 'w', **qc_profile) as dst_qc:

                for window in self.iter_raster_windows(info.width, info.height, block_size=block_size):
                    if cancel_event is not None and cancel_event.is_set():
                        raise RasterCalculationCancelled("用户取消了任务。")

                    chunk = src_dem.read(1, window=window)
                    if info.nodata is not None and np.isfinite(info.nodata):
                        valid_mask = ~np.isclose(chunk, info.nodata) & np.isfinite(chunk)
                    else:
                        valid_mask = np.isfinite(chunk)

                    out_chunk = np.full(chunk.shape, np.nan, dtype=np.float32)
                    qc_chunk = np.full(chunk.shape, QC_NODATA, dtype=np.uint16)

                    n_valid = int(np.count_nonzero(valid_mask))
                    input_valid_total += n_valid

                    if n_valid > 0:
                        rows_local, cols_local = np.where(valid_mask)
                        rows_global = rows_local + window.row_off
                        cols_global = cols_local + window.col_off

                        lons, lats = self.pixel_centers_to_lonlat(info.transform, info.crs, rows_global, cols_global)

                        # 通过 Predictor 统一公共接口调用空间快照解算 (局部 circular bbox)
                        tide_msl, fes_flags = predictor.predict_spatial_snapshot(
                            lons=lons,
                            lats=lats,
                            timestamp=ts_utc_str,
                            constituents=constituents,
                            source_tz='UTC'
                        )

                        # 静态基准转换
                        offsets_dict = self.transformer.get_static_datum_offsets(
                            lons=lons, lats=lats, target=datum_target, strict=strict
                        )
                        offsets = offsets_dict['offset_m']
                        qc_provenance = offsets_dict['qc_warning']

                        # 像元水面高与位掩码质量计算
                        water_levels = tide_msl + offsets
                        out_chunk[valid_mask] = water_levels.astype(np.float32)

                        pix_qc = np.zeros(n_valid, dtype=np.uint16)
                        fes_extra_mask = (fes_flags < 0)
                        fes_inv_mask = (fes_flags == 0) | np.isnan(tide_msl)
                        datum_inv_mask = np.isnan(offsets)
                        datum_approx_mask = (qc_provenance == 'QC_DATUM_SOURCE_APPROX')

                        pix_qc[fes_extra_mask] |= QC_BIT_FES_EXTRAPOLATED
                        pix_qc[fes_inv_mask] |= QC_BIT_INSUFFICIENT_NODES
                        pix_qc[datum_inv_mask] |= QC_BIT_DATUM_INVALID
                        pix_qc[datum_approx_mask] |= QC_BIT_DATUM_SOURCE_APPROX

                        qc_chunk[valid_mask] = pix_qc

                        solved_mask = np.isfinite(water_levels)
                        solved_total += int(np.count_nonzero(solved_mask))

                    dst_out.write(out_chunk, 1, window=window)
                    dst_qc.write(qc_chunk, 1, window=window)

                    win_idx += 1
                    if progress_callback:
                        pct = int(win_idx / total_windows * 95)
                        progress_callback(pct, f"正在流式解算单时刻空间潮位与质量掩膜 ({win_idx}/{total_windows} 块)...")

                metadata = {
                    'SOFTWARE': 'CoastTideX v1.4',
                    'ENGINE_MODE': 'snapshot_raster',
                    'TIDE_MODEL': 'FES2022b',
                    'SNAPSHOT_TIME_UTC': ts_utc_str,
                    'VERTICAL_DATUM': str(datum_target).upper(),
                    'INPUT_VALID_PIXELS': str(input_valid_total),
                    'SOLVED_PIXELS': str(solved_total),
                    'UNSOLVED_PIXELS': str(input_valid_total - solved_total),
                    'TOTAL_PIXELS': str(info.total_pixel_count),
                    'QC_ENCODING': 'UInt16 bitmask: bit0=FES_extrapolated, bit1=spatial_fallback, bit2=insufficient_nodes, bit3=datum_invalid, bit4=datum_source_approx, bit5=min_spacing_reached, bit6=connectivity_fallback, bit7=fes_validity_boundary, bit8=max_refinement_reached'
                }
                dst_out.update_tags(**metadata)
                dst_qc.update_tags(**metadata)

            os.replace(tmp_output, output_raster_path)
            os.replace(tmp_qc, qc_output_path)

        except Exception:
            for p in [tmp_output, tmp_qc]:
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass
            raise

        elapsed = time.time() - t_start
        if progress_callback:
            progress_callback(100, f"单时刻空间潮位解算完成 (耗时 {elapsed:.1f}s)！")

        return RasterResultSummary(
            output_path=output_raster_path,
            qc_output_path=qc_output_path,
            mode='snapshot',
            width=info.width,
            height=info.height,
            valid_pixels=solved_total,
            total_pixels=info.total_pixel_count,
            input_valid_pixels=input_valid_total,
            solved_pixels=solved_total,
            unsolved_pixels=input_valid_total - solved_total,
            control_nodes_count=0,
            elapsed_seconds=elapsed,
            metadata=metadata
        )

    def build_tide_control_grid(
        self,
        dem_path: str,
        export_tide_cache_path: Optional[str] = None,
        year: int = 2024,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        freq: str = '30min',
        dem_datum: str = 'egm2008',
        constituents: str | list = 'all',
        source_tz: str = 'UTC',
        initial_control_spacing_m: Optional[float] = None,
        min_control_spacing_m: Optional[float] = None,
        inundation_error_tolerance_pct: Optional[float] = None,
        strict: bool = True,
        progress_callback = None,
        cancel_event = None,
        inclusive: Optional[str] = None,
        target_mode: str = 'intertidal',
        allow_overwrite: bool = True
    ) -> Dict[str, Any]:
        """
        【Stage 1 控制网格构建器】构建自适应四叉树控制网格并批量解算各控制节点的 FES 潮位时序。
        仅生成控制节点与叶单元拓扑 (不生成 2D 像元 GeoTIFF)，专供 Tide Cache 导出与批量管线调度。
        """
        from .tide_cache import inspect_tide_cache_metadata
        if export_tide_cache_path:
            summary = self.calculate_inundation_raster(
                dem_path=dem_path,
                output_path="",
                export_tide_cache_path=export_tide_cache_path,
                grid_only=True,
                year=year,
                start_time=start_time,
                end_time=end_time,
                freq=freq,
                dem_datum=dem_datum,
                constituents=constituents,
                source_tz=source_tz,
                initial_control_spacing_m=initial_control_spacing_m,
                min_control_spacing_m=min_control_spacing_m,
                inundation_error_tolerance_pct=inundation_error_tolerance_pct,
                strict=strict,
                progress_callback=progress_callback,
                cancel_event=cancel_event,
                inclusive=inclusive,
                target_mode=target_mode,
                allow_overwrite=allow_overwrite
            )
            cache_meta = inspect_tide_cache_metadata(export_tide_cache_path)
            cache_meta['summary'] = summary
            return cache_meta
        else:
            import tempfile
            with tempfile.TemporaryDirectory() as td:
                tmp_cache = os.path.join(td, 'dummy_tide.nc')
                summary = self.calculate_inundation_raster(
                    dem_path=dem_path,
                    output_path="",
                    export_tide_cache_path=tmp_cache,
                    grid_only=True,
                    year=year,
                    start_time=start_time,
                    end_time=end_time,
                    freq=freq,
                    dem_datum=dem_datum,
                    constituents=constituents,
                    source_tz=source_tz,
                    initial_control_spacing_m=initial_control_spacing_m,
                    min_control_spacing_m=min_control_spacing_m,
                    inundation_error_tolerance_pct=inundation_error_tolerance_pct,
                    strict=strict,
                    progress_callback=progress_callback,
                    cancel_event=cancel_event,
                    inclusive=inclusive,
                    target_mode=target_mode,
                    allow_overwrite=True
                )
                cache_meta = inspect_tide_cache_metadata(tmp_cache)
                cache_meta['summary'] = summary
                return cache_meta

    def calculate_inundation_raster(
        self,
        dem_path: str,
        output_path: str = "",
        qc_output_path: Optional[str] = None,
        year: int = 2024,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        freq: str = "30min",
        dem_datum: str = "egm2008",
        constituents: str | list = "all",
        source_tz: str = "UTC",
        initial_control_spacing_m: Optional[float] = None,
        min_control_spacing_m: Optional[float] = None,
        inundation_error_tolerance_pct: Optional[float] = None,
        block_size: Optional[int] = None,
        strict: bool = True,
        progress_callback = None,
        cancel_event = None,
        inclusive: Optional[str] = None,
        target_mode: str = "standard",
        export_tide_cache_path: Optional[str] = None,
        grid_only: bool = False,
        allow_overwrite: bool = True
    ) -> RasterResultSummary:
        """
        【Mode B】自适应潮位控制网格 (Adaptive Tide Control Grid) 潜在天文潮淹没频率 GeoTIFF 解算。
        科学算法与工程机制:
          1. 物理尺度拓扑分析 (topology_max_resolution_m) 与保守连通域划分 (Topology-aware connectivity guard)；
          2. 提取 DEM 有效区域与活性支持掩膜，生成初始有效控制网格单元；
          3. 逐层宽度优先批量解算 (Level-wise BFS Batched FES)，按 control_node_batch_size 流式评估；
          4. 考虑 FES 有效性突变 (validity discontinuity) 与边缘探测 (edge probing)；
          5. 细分深度受动态层数与 absolute_max_refinement_depth 约束，达上限未收敛时标记 QC 位；
          6. 空间桶索引 (Spatial Bucket Index) 消除大型栅格叶节点匹配瓶颈；
          7. 拓扑屏障保护: component 0 为 UNKNOWN，阻止跨越陆地/NoData 屏障盲目平滑；
          8. 流式输出 Float32 淹没频率 (0~100%) 与 UInt16 质量控制位掩膜 (*_qc.tif)。
        """
        t_start = time.time()
        info = self.inspect_raster(dem_path, compute_valid_count=False)
        predictor = self._get_predictor()

        if initial_control_spacing_m is None:
            initial_control_spacing_m = self.initial_control_spacing_m
        if min_control_spacing_m is None:
            min_control_spacing_m = self.min_control_spacing_m
        if inundation_error_tolerance_pct is None:
            inundation_error_tolerance_pct = self.inundation_error_tolerance_pct
        if block_size is None:
            block_size = self.default_block_size

        if not grid_only:
            if qc_output_path is None:
                base, ext = os.path.splitext(output_path)
                qc_output_path = f"{base}_qc{ext}"

            if not allow_overwrite:
                if os.path.exists(output_path):
                    raise ExistingOutputError(f"输出文件已存在且未开启覆盖权限: {output_path}")
                if qc_output_path and os.path.exists(qc_output_path):
                    raise ExistingOutputError(f"QC 输出文件已存在且未开启覆盖权限: {qc_output_path}")

            out_dir = os.path.dirname(os.path.abspath(output_path))
            if out_dir:
                os.makedirs(out_dir, exist_ok=True)
            tmp_output = f"{output_path}.tmp.tif"
            tmp_qc = f"{qc_output_path}.tmp.tif"
        else:
            tmp_output = ""
            tmp_qc = "" 

        # 时间范围对齐 (默认整年严格半开区间 [start, end) 保持权重均等，自定义时段支持显式 inclusive 参数)
        if inclusive is not None:
            inclusive_mode = inclusive
            t_start_str = str(start_time) if start_time is not None else f"{year:04d}-01-01 00:00:00"
            t_end_str = str(end_time) if end_time is not None else f"{year+1:04d}-01-01 00:00:00"
        elif start_time is None or end_time is None:
            t_start_str = f"{year:04d}-01-01 00:00:00"
            t_end_str = f"{year+1:04d}-01-01 00:00:00"
            inclusive_mode = 'left'
        else:
            t_start_str = str(start_time)
            t_end_str = str(end_time)
            inclusive_mode = 'left'  # v1.6: 全系统统一默认严格半开区间 [start, end) 杜绝端点双重统计

        time_idx = pd.date_range(t_start_str, t_end_str, freq=freq, inclusive=inclusive_mode, tz="UTC")
        n_time_samples = len(time_idx)

        if progress_callback:
            progress_callback(3, "正在审查 DEM 物理分辨率与构建拓扑连通域掩膜...")

        # 1. 物理尺度控制的拓扑连通域分析 (Physical Scale Topology Guard)
        labeled_coarse, num_features, downsample_factor, h_coarse, w_coarse, coarse_active_support, input_valid_count = build_support_topology(
            info=info,
            topology_max_resolution_m=self.topology_max_resolution_m,
            topology_valid_fraction_threshold=self.topology_valid_fraction_threshold,
            block_size=block_size,
            cancel_event=cancel_event
        )

        def _get_point_component(px_x: float, px_y: float) -> int:
            """
            获取坐标对应的拓扑连通域编号。
            component 0 严格为 UNKNOWN，仅在邻域内存在唯一明确连通域时方可归属。
            """
            col_f, row_f = ~info.transform * (px_x, px_y)
            r_idx = int(np.clip(row_f // downsample_factor, 0, h_coarse - 1))
            c_idx = int(np.clip(col_f // downsample_factor, 0, w_coarse - 1))
            cid = int(labeled_coarse[r_idx, c_idx])
            if cid == 0:
                sub = labeled_coarse[max(0, r_idx - 2):min(h_coarse, r_idx + 3), max(0, c_idx - 2):min(w_coarse, c_idx + 3)]
                non_z = sub[sub > 0]
                unq = np.unique(non_z)
                if len(unq) == 1:
                    cid = int(unq[0])
                else:
                    cid = 0
            return cid

        # 2. 坐标单位感知与初始规则网格布设
        min_x, min_y, max_x, max_y = info.bounds
        if info.is_projected:
            step_x_crs = initial_control_spacing_m / info.unit_factor
            step_y_crs = initial_control_spacing_m / info.unit_factor
            min_spacing_crs = min_control_spacing_m / info.unit_factor
        else:
            mid_lat = (min_y + max_y) / 2.0
            step_y_crs = initial_control_spacing_m / 111320.0
            step_x_crs = initial_control_spacing_m / (111320.0 * max(0.01, np.cos(np.radians(mid_lat))))
            min_spacing_crs = min_control_spacing_m / 111320.0

        xs_init = np.arange(min_x, max_x + step_x_crs, step_x_crs)
        ys_init = np.arange(min_y, max_y + step_y_crs, step_y_crs)

        # 3. 量化坐标缓存 (Node Cache) 与 Level-wise 节点批次解算
        node_cache: Dict[Tuple[int, int], ControlNode] = {}
        node_id_counter = [0]
        predictor_call_count = [0]
        evaluated_node_count = [0]

        def _coord_key(x_val: float, y_val: float) -> Tuple[int, int]:
            if info.is_projected:
                return (int(round(x_val * 100)), int(round(y_val * 100)))
            else:
                return (int(round(x_val * 1e6)), int(round(y_val * 1e6)))

        def _get_or_create_node(x_val: float, y_val: float) -> ControlNode:
            k = _coord_key(x_val, y_val)
            if k in node_cache:
                return node_cache[k]

            src_c = rasterio.crs.CRS.from_user_input(info.crs)
            if not info.is_projected:
                lon_n = normalize_longitude(x_val, to_360=False)
                lat_n = float(y_val)
            else:
                if Transformer is not None:
                    t_wgs = Transformer.from_crs(src_c, "EPSG:4326", always_xy=True)
                    t_lon, t_lat = t_wgs.transform(x_val, y_val)
                else:
                    t_lon, t_lat = rasterio.warp.transform(src_c, "EPSG:4326", [x_val], [y_val])
                    t_lon, t_lat = t_lon[0], t_lat[0]
                lon_n = normalize_longitude(float(t_lon), to_360=False)
                lat_n = float(t_lat)

            cid = _get_point_component(x_val, y_val)
            node = ControlNode(
                node_id=node_id_counter[0],
                x=float(x_val),
                y=float(y_val),
                lon=float(lon_n),
                lat=float(lat_n),
                component_id=cid
            )
            node_id_counter[0] += 1
            node_cache[k] = node
            return node

        def _evaluate_nodes_batch(nodes_to_eval: List[ControlNode]):
            """
            批量解算控制节点 FES 时序并执行静态基准转换。
            严格遵循批次分块 (control_node_batch_size) 与内存就地排序，释放冗余矩阵。
            """
            uncalculated = [n for n in nodes_to_eval if not n.valid and len(n.water_levels_sorted) == 0 and not getattr(n, '_evaluated', False)]
            if not uncalculated:
                return

            resident_count = sum(1 for n in node_cache.values() if len(n.water_levels_sorted) > 0)
            pending_count = len(uncalculated)
            total_attempted = resident_count + pending_count
            if total_attempted > self.max_in_memory_control_nodes:
                mem_required_mb = estimate_control_node_memory(total_attempted, n_time_samples, dtype_bytes=4)
                mem_budget_mb = estimate_control_node_memory(self.max_in_memory_control_nodes, n_time_samples, dtype_bytes=4)
                raise RasterMemoryLimitError(
                    f"自适应控制网格节点超出常驻内存预算上限 (Raster engine exceeded max_in_memory_control_nodes budget)! "
                    f"当前常驻节点 (Resident): {resident_count}, 待解算新节点 (Pending batch): {pending_count}, "
                    f"总尝试节点数 (Total attempted): {total_attempted}, 内存上限 (Limit): {self.max_in_memory_control_nodes}. "
                    f"每个节点时间采样点数 (Time samples per node): {n_time_samples}. "
                    f"预估所需时序数组内存: {mem_required_mb:.2f} MB (当前预算上限: {mem_budget_mb:.2f} MB). "
                    f"建议解决方案: (1) 在 config.yaml 中调大 raster.max_in_memory_control_nodes; "
                    f"(2) 适当增大最小控制网格间距 min_control_spacing_m; "
                    f"(3) 适当提高淹没误差容限 inundation_error_tolerance_pct; "
                    f"(4) 裁剪 DEM 空间范围以降低复杂海岸线节点密度。"
                )

            for n in uncalculated:
                n._evaluated = True

            batch_size = max(1, self.control_node_batch_size)
            n_total = len(uncalculated)

            for b_i in range(0, n_total, batch_size):
                if cancel_event is not None and cancel_event.is_set():
                    raise RasterCalculationCancelled("用户取消了任务。")
                sub_nodes = uncalculated[b_i:b_i + batch_size]
                b_lons = np.array([n.lon for n in sub_nodes], dtype=float)
                b_lats = np.array([n.lat for n in sub_nodes], dtype=float)

                tide_mat, _, flag_mat = predictor.predict_points_period(
                    lons=b_lons,
                    lats=b_lats,
                    start_time=t_start_str,
                    end_time=t_end_str,
                    freq=freq,
                    inclusive=inclusive_mode,
                    constituents=constituents,
                    source_tz=source_tz,
                    max_fes_evaluate_points=self.max_fes_evaluate_points
                )
                predictor_call_count[0] += 1
                evaluated_node_count[0] += len(sub_nodes)

                offsets_dict = self.transformer.get_static_datum_offsets(
                    lons=b_lons, lats=b_lats, target=dem_datum, strict=strict
                )
                b_offsets = offsets_dict['offset_m']
                b_qc_prov = offsets_dict['qc_warning']

                for idx_n, n_obj in enumerate(sub_nodes):
                    t_ser = tide_mat[idx_n]
                    valid_mask_t = np.isfinite(t_ser)
                    valid_t = t_ser[valid_mask_t]
                    off_val = b_offsets[idx_n]
                    is_valid = (len(valid_t) > 0) and np.isfinite(off_val)
                    fl = flag_mat[idx_n]
                    q_min = int(np.min(fl)) if len(fl) > 0 else 0

                    qc_bits = QC_BIT_VALID
                    if not is_valid:
                        qc_bits |= QC_BIT_INSUFFICIENT_NODES
                    if q_min < 0:
                        qc_bits |= QC_BIT_FES_EXTRAPOLATED
                    if b_qc_prov[idx_n] == 'QC_DATUM_SOURCE_APPROX':
                        qc_bits |= QC_BIT_DATUM_SOURCE_APPROX
                    if np.isnan(off_val):
                        qc_bits |= QC_BIT_DATUM_INVALID

                    if is_valid:
                        # 保存原始 MSL 潮位序列供 Tide Cache 导出 (仅当指定导出 cache 时暂存)
                        if export_tide_cache_path:
                            n_obj.tide_msl_raw = t_ser.copy().astype(np.float32)
                        else:
                            n_obj.tide_msl_raw = None
                        # 原地添加基准偏移并排序，极大节省内存
                        valid_t += off_val
                        valid_t.sort()
                        sorted_w = valid_t.astype(np.float32)
                    else:
                        n_obj.tide_msl_raw = None
                        sorted_w = np.array([], dtype=np.float32)

                    n_obj.water_levels_sorted = sorted_w
                    n_obj.valid = is_valid
                    n_obj.quality_flag = q_min
                    n_obj.static_offset_m = float(off_val) if np.isfinite(off_val) else 0.0
                    n_obj.qc_bitmask = qc_bits

                del tide_mat
                del flag_mat

        # 4. 生成具有 DEM 支持的初始活动控制网格
        active_initial_cells: List[Tuple[float, float, float, float]] = []
        for i_x in range(len(xs_init) - 1):
            x0, x1 = xs_init[i_x], xs_init[i_x + 1]
            for i_y in range(len(ys_init) - 1):
                y0, y1 = ys_init[i_y], ys_init[i_y + 1]
                c0_f, r1_f = ~info.transform * (x0, y0)
                c1_f, r0_f = ~info.transform * (x1, y1)
                r_min = int(np.clip(min(r0_f, r1_f) // downsample_factor, 0, h_coarse - 1))
                r_max = int(np.clip(max(r0_f, r1_f) // downsample_factor + 1, 0, h_coarse))
                c_min = int(np.clip(min(c0_f, c1_f) // downsample_factor, 0, w_coarse - 1))
                c_max = int(np.clip(max(c0_f, c1_f) // downsample_factor + 1, 0, w_coarse))
                if np.any(coarse_active_support[r_min:r_max, c_min:c_max]):
                    active_initial_cells.append((x0, y0, x1, y1))

        if not active_initial_cells:
            for i_x in range(len(xs_init) - 1):
                for i_y in range(len(ys_init) - 1):
                    active_initial_cells.append((xs_init[i_x], ys_init[i_y], xs_init[i_x + 1], ys_init[i_y + 1]))

        initial_node_keys_set = set()
        for x0, y0, x1, y1 in active_initial_cells:
            for pt_x in [x0, x1]:
                for pt_y in [y0, y1]:
                    initial_node_keys_set.add(_coord_key(pt_x, pt_y))
        initial_grid_nodes_count = len(initial_node_keys_set)

        # 动态计算所需最大细分深度与绝对防护上限 (Dynamic Max Refinement Depth)
        required_max_depth = int(np.ceil(np.log2(max(1.0, initial_control_spacing_m / max(1.0, min_control_spacing_m)))))
        effective_max_depth = min(required_max_depth, self.absolute_max_refinement_depth)

        if progress_callback:
            progress_callback(10, f"初始网格就绪 (有效单元: {len(active_initial_cells)} 个, 最大允许深度: {effective_max_depth}), 开始宽度优先自适应细分...")

        # 5. 宽度优先四叉树自适应细分 (Level-wise Breadth-First Refinement)
        leaf_cells: List[QuadCell] = []
        cell_id_counter = [0]
        max_level_reached = [0]

        current_cells: List[Tuple[float, float, float, float, int]] = [
            (x0, y0, x1, y1, 0) for x0, y0, x1, y1 in active_initial_cells
        ]

        with rasterio.open(info.path) as src_for_refine:
            while current_cells:
                if cancel_event is not None and cancel_event.is_set():
                    raise RasterCalculationCancelled("用户取消了任务。")

                current_level = current_cells[0][4]
                if current_level > max_level_reached[0]:
                    max_level_reached[0] = current_level

                # A. 收集当前层所有候选单元的四角与中心节点
                nodes_to_batch: List[ControlNode] = []
                for x0, y0, x1, y1, lvl in current_cells:
                    na = _get_or_create_node(x0, y0)
                    nb = _get_or_create_node(x1, y0)
                    nc = _get_or_create_node(x0, y1)
                    nd = _get_or_create_node(x1, y1)
                    ne = _get_or_create_node((x0 + x1) / 2.0, (y0 + y1) / 2.0)
                    nodes_to_batch.extend([na, nb, nc, nd, ne])

                # 统一批量解算当前层的所有未计算节点
                _evaluate_nodes_batch(nodes_to_batch)

                # B. 检查边缘探测 (Edge Probing) 需求
                probe_nodes_to_batch: List[ControlNode] = []
                cell_probe_map: Dict[int, List[ControlNode]] = {}

                for idx_cell, (x0, y0, x1, y1, lvl) in enumerate(current_cells):
                    na = _get_or_create_node(x0, y0)
                    nb = _get_or_create_node(x1, y0)
                    nc = _get_or_create_node(x0, y1)
                    nd = _get_or_create_node(x1, y1)
                    ne = _get_or_create_node((x0 + x1) / 2.0, (y0 + y1) / 2.0)

                    # 若四角和中心全为无效 (FES Invalid / Land)，但单元内可能存在有效 DEM 地形，触发边缘中点探测
                    if not (na.valid or nb.valid or nc.valid or nd.valid or ne.valid):
                        x_mid = (x0 + x1) / 2.0
                        y_mid = (y0 + y1) / 2.0
                        p_ab = _get_or_create_node(x_mid, y0)
                        p_cd = _get_or_create_node(x_mid, y1)
                        p_ac = _get_or_create_node(x0, y_mid)
                        p_bd = _get_or_create_node(x1, y_mid)
                        probes = [p_ab, p_cd, p_ac, p_bd]
                        cell_probe_map[idx_cell] = probes
                        probe_nodes_to_batch.extend(probes)

                if probe_nodes_to_batch:
                    _evaluate_nodes_batch(probe_nodes_to_batch)

                # C. 逐单元判定误差、FES 连续性与细分决策
                next_level_cells: List[Tuple[float, float, float, float, int]] = []

                for idx_cell, (x0, y0, x1, y1, lvl) in enumerate(current_cells):
                    na = _get_or_create_node(x0, y0)
                    nb = _get_or_create_node(x1, y0)
                    nc = _get_or_create_node(x0, y1)
                    nd = _get_or_create_node(x1, y1)
                    x_mid = (x0 + x1) / 2.0
                    y_mid = (y0 + y1) / 2.0
                    ne = _get_or_create_node(x_mid, y_mid)

                    try:
                        win_f = rasterio.windows.from_bounds(
                            min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1),
                            transform=info.transform
                        )
                        c_off = max(0, int(np.floor(win_f.col_off)))
                        r_off = max(0, int(np.floor(win_f.row_off)))
                        c_max = min(info.width, int(np.ceil(win_f.col_off + win_f.width)))
                        r_max = min(info.height, int(np.ceil(win_f.row_off + win_f.height)))
                        win = Window(c_off, r_off, max(0, c_max - c_off), max(0, r_max - r_off))
                    except Exception:
                        win = Window(0, 0, 0, 0)

                    if win.width <= 0 or win.height <= 0:
                        leaf_cells.append(QuadCell(
                            cell_id=cell_id_counter[0], x_min=x0, y_min=y0, x_max=x1, y_max=y1,
                            level=lvl, node_a=na, node_b=nb, node_c=nc, node_d=nd,
                            qc_min_spacing_reached=False, qc_max_refinement_reached=False,
                            qc_validity_boundary=False, max_error_pct=0.0
                        ))
                        cell_id_counter[0] += 1
                        continue

                    sub_dem = src_for_refine.read(1, window=win)
                    if info.nodata is not None and np.isfinite(info.nodata):
                        val_z = sub_dem[~np.isclose(sub_dem, info.nodata) & np.isfinite(sub_dem)]
                    else:
                        val_z = sub_dem[np.isfinite(sub_dem)]

                    if len(val_z) == 0:
                        leaf_cells.append(QuadCell(
                            cell_id=cell_id_counter[0], x_min=x0, y_min=y0, x_max=x1, y_max=y1,
                            level=lvl, node_a=na, node_b=nb, node_c=nc, node_d=nd,
                            qc_min_spacing_reached=False, qc_max_refinement_reached=False,
                            qc_validity_boundary=False, max_error_pct=0.0
                        ))
                        cell_id_counter[0] += 1
                        continue

                    # 提取单元内部代表性地形高程
                    if len(val_z) >= 10:
                        z_test = np.quantile(val_z, [0.10, 0.50, 0.90])
                    else:
                        z_test = np.unique(val_z)

                    # 1. CCDF 插值误差计算
                    cell_error = 0.0
                    corners = [na, nb, nc, nd]
                    v_corners = [cn for cn in corners if cn.valid and len(cn.water_levels_sorted) > 0]
                    if ne.valid and len(ne.water_levels_sorted) > 0:
                        f_true = compute_inundation_frequency(ne.water_levels_sorted, z_test, as_percentage=True)
                        if len(v_corners) > 0:
                            f_interp = np.zeros_like(z_test, dtype=float)
                            w_each = 1.0 / len(v_corners)
                            for cn in v_corners:
                                f_interp += w_each * compute_inundation_frequency(cn.water_levels_sorted, z_test, as_percentage=True)
                            cell_error = float(np.max(np.abs(f_true - f_interp)))
                    elif len(v_corners) >= 2:
                        # 中心为陆地但有多个有效海角：评估有效角节点之间的频率变化梯度
                        corner_freqs = [compute_inundation_frequency(cn.water_levels_sorted, z_test, as_percentage=True) for cn in v_corners]
                        max_diff = 0.0
                        for i_cf in range(len(corner_freqs)):
                            for j_cf in range(i_cf + 1, len(corner_freqs)):
                                d_cf = float(np.max(np.abs(corner_freqs[i_cf] - corner_freqs[j_cf])))
                                if d_cf > max_diff:
                                    max_diff = d_cf
                        cell_error = max_diff

                    # 2. FES 有效性突变与连通域边界分析 (Validity Discontinuity Check)
                    corner_valids = [na.valid, nb.valid, nc.valid, nd.valid]
                    center_valid = ne.valid
                    validity_discontinuity = False

                    if len(set(corner_valids)) > 1:
                        validity_discontinuity = True
                    elif center_valid != corner_valids[0]:
                        validity_discontinuity = True
                    elif center_valid and not all(corner_valids):
                        validity_discontinuity = True
                    elif not center_valid and any(corner_valids):
                        validity_discontinuity = True

                    # 边缘探测结果判定
                    if idx_cell in cell_probe_map:
                        probe_valids = [p.valid for p in cell_probe_map[idx_cell]]
                        if any(probe_valids):
                            validity_discontinuity = True

                    # 多连通域交错判定
                    corner_comps = {cn.component_id for cn in [na, nb, nc, nd] if cn.valid and cn.component_id > 0}
                    if len(corner_comps) > 1:
                        validity_discontinuity = True

                    cur_spacing = min(x1 - x0, y1 - y0)
                    can_subdivide_spacing = (cur_spacing / 2.0 >= min_spacing_crs - 1e-6)
                    can_subdivide_depth = (lvl < effective_max_depth)
                    can_subdivide = can_subdivide_spacing and can_subdivide_depth

                    if target_mode == "intertidal":
                        # 潮间带目标感知模式:
                        # 1. 连通域冲突 (跨水体屏障): 必须细分
                        topology_conflict = (len(corner_comps) > 1)
                        # 2. 精度指标: 频率误差超标必须细分
                        accuracy_fail = (cell_error > inundation_error_tolerance_pct)
                        # 3. 寻找水体支撑: 无有效角节点，但探测或中心有水体时，细分以捕获水体控制节点
                        has_valid_corner = any(corner_valids)
                        probes_found_water = False
                        if idx_cell in cell_probe_map:
                            probes_found_water = any(p.valid for p in cell_probe_map[idx_cell])
                        need_water_anchor = (not has_valid_corner) and (center_valid or probes_found_water)

                        should_subdivide = topology_conflict or accuracy_fail or need_water_anchor
                    else:
                        should_subdivide = (cell_error > inundation_error_tolerance_pct) or validity_discontinuity

                    if should_subdivide and can_subdivide:
                        next_level_cells.append((x0, y0, x_mid, y_mid, lvl + 1))
                        next_level_cells.append((x_mid, y0, x1, y_mid, lvl + 1))
                        next_level_cells.append((x0, y_mid, x_mid, y1, lvl + 1))
                        next_level_cells.append((x_mid, y_mid, x1, y1, lvl + 1))
                    else:
                        reached_min = should_subdivide and not can_subdivide_spacing
                        reached_max_depth = should_subdivide and can_subdivide_spacing and not can_subdivide_depth
                        leaf_cells.append(QuadCell(
                            cell_id=cell_id_counter[0],
                            x_min=x0, y_min=y0, x_max=x1, y_max=y1,
                            level=lvl, node_a=na, node_b=nb, node_c=nc, node_d=nd,
                            qc_min_spacing_reached=reached_min,
                            qc_max_refinement_reached=reached_max_depth,
                            qc_validity_boundary=validity_discontinuity,
                            max_error_pct=cell_error
                        ))
                        cell_id_counter[0] += 1

                current_cells = next_level_cells
                if progress_callback:
                    pct = min(58, 10 + int(48 * (current_level + 1) / (effective_max_depth + 1)))
                    progress_callback(pct, f"自适应梯度细分进行中 (已完成 Level {current_level}, 现有 {len(leaf_cells)} 个叶节点单元)...")

        final_nodes_count = len(node_cache)
        active_nodes_count = sum(1 for n in node_cache.values() if n.valid)

        # 若指定了导出 Tide Cache 路径，在此刻将控制网格及其时序原子序列化
        if export_tide_cache_path:
            from .tide_cache import write_tide_cache

            # 计算终端时刻水位 (Terminal Tide at end_time) 以支撑高精度连续露出分析 (Schema 1.2)
            terminal_tides = None
            try:
                valid_nodes = [n for n in node_cache.values() if n.valid]
                if valid_nodes:
                    all_nodes_list = list(node_cache.values())
                    all_lons = np.array([n.lon for n in all_nodes_list], dtype=float)
                    all_lats = np.array([n.lat for n in all_nodes_list], dtype=float)
                    if hasattr(predictor, "predict_points_at_time"):
                        t_vals, _ = predictor.predict_points_at_time(
                            lons=all_lons,
                            lats=all_lats,
                            timestamp=t_end_str,
                            constituents=constituents,
                            source_tz=source_tz
                        )
                        terminal_tides = t_vals.astype(np.float32)
                    elif hasattr(predictor, "predict_spatial_snapshot"):
                        t_vals, _ = predictor.predict_spatial_snapshot(
                            lons=all_lons,
                            lats=all_lats,
                            timestamp=t_end_str,
                            constituents=constituents,
                            source_tz=source_tz
                        )
                        terminal_tides = t_vals.astype(np.float32)
                    else:
                        # 兼容旧版 mock predictor: 使用严格半开区间 [t_end, t_end + freq) 取第 0 点 (t_end 处真实水位)
                        dt_offset = pd.to_timedelta(pd.tseries.frequencies.to_offset(freq).nanos, unit='ns')
                        t_end_next = (pd.Timestamp(t_end_str) + dt_offset).isoformat()
                        t_mat, _, _ = predictor.predict_points_period(
                            lons=all_lons,
                            lats=all_lats,
                            start_time=t_end_str,
                            end_time=t_end_next,
                            freq=freq,
                            inclusive="left",
                            constituents=constituents,
                            source_tz=source_tz,
                            max_fes_evaluate_points=self.max_fes_evaluate_points
                        )
                        terminal_tides = t_mat[:, 0].astype(np.float32)
            except Exception as e:
                warnings.warn(f"无法预计算终端时刻潮位采样: {e}")

            cache_meta = {
                "start_time": t_start_str,
                "end_time": t_end_str,
                "freq": freq,
                "source_tz": source_tz,
                "inclusive": inclusive_mode,
                "constituents": constituents,
                "dem_datum": dem_datum,
                "initial_control_spacing_m": initial_control_spacing_m,
                "min_control_spacing_m": min_control_spacing_m,
                "inundation_error_tolerance_pct": inundation_error_tolerance_pct,
                "target_mode": target_mode,
                "topology_max_resolution_m": self.topology_max_resolution_m,
                "topology_valid_fraction_threshold": self.topology_valid_fraction_threshold
            }
            write_tide_cache(
                cache_path=export_tide_cache_path,
                info=info,
                leaf_cells=leaf_cells,
                node_cache=node_cache,
                time_index=time_idx,
                metadata=cache_meta,
                allow_overwrite=allow_overwrite,
                cancel_event=cancel_event,
                tide_msl_terminal=terminal_tides
            )
            # 导出 Cache 完成后立即解引用所有控制节点的 raw MSL 数组，常驻内存仅保留 water_levels_sorted
            for n in node_cache.values():
                n.tide_msl_raw = None

        if grid_only:
            elapsed = time.time() - t_start
            return RasterResultSummary(
                output_path=output_path or "",
                qc_output_path=qc_output_path or "",
                mode="adaptive_control_grid_tide_only",
                width=info.width,
                height=info.height,
                valid_pixels=0,
                total_pixels=info.total_pixel_count,
                input_valid_pixels=info.valid_pixel_count,
                solved_pixels=0,
                unsolved_pixels=0,
                control_nodes_count=final_nodes_count,
                elapsed_seconds=elapsed,
                metadata={
                    "TOTAL_CONTROL_NODES": final_nodes_count,
                    "MAX_REFINEMENT_LEVEL_USED": max((c.level for c in leaf_cells), default=0),
                    "TOTAL_LEAF_CELLS": len(leaf_cells)
                }
            )

        if progress_callback:
            progress_callback(60, f"自适应细分完成: 共 {len(leaf_cells)} 个叶单元, {final_nodes_count} 个控制节点。构建空间索引并开始流式插值写入...")

        metadata = {
            'SOFTWARE': 'CoastTideX v1.5 Alpha',
            'ENGINE_MODE': 'inundation_frequency_raster',
            'TIDE_MODEL': 'FES2022b',
            'TIDE_CONSTITUENTS': 'all' if constituents == 'all' else str(constituents),
            'INUNDATION_TYPE': 'potential_astronomical_tidal',
            'TIME_START': t_start_str,
            'TIME_END': t_end_str,
            'TIME_STEP': freq,
            'TIMEZONE': source_tz,
            'VERTICAL_DATUM': str(dem_datum).upper(),
            'SPATIAL_METHOD': 'adaptive_quadtree_control_grid',
            'TOPOLOGY_GUARD': 'valid_mask_topology_aware',
            'INITIAL_CONTROL_SPACING_M': str(initial_control_spacing_m),
            'MIN_CONTROL_SPACING_M': str(min_control_spacing_m),
            'ERROR_TOLERANCE_PCT': str(inundation_error_tolerance_pct),
            'INITIAL_GRID_NODES': str(initial_grid_nodes_count),
            'ACTIVE_CONTROL_NODES': str(active_nodes_count),
            'FINAL_CONTROL_NODES': str(final_nodes_count),
            'MAX_REFINEMENT_DEPTH_ALLOWED': str(effective_max_depth),
            'MAX_REFINEMENT_LEVEL_USED': str(max_level_reached[0]),
            'CONTROL_NODE_BATCH_SIZE': str(self.control_node_batch_size),
            'FES_PREDICT_CALLS': str(predictor_call_count[0]),
            'FES_CONTROL_NODES_EVALUATED': str(evaluated_node_count[0]),
            'TOPOLOGY_SOURCE': 'target_mask_derived',
            'TOPOLOGY_RESOLUTION_M': str(self.topology_max_resolution_m),
            'TOPOLOGY_VALID_FRACTION_THRESHOLD': str(self.topology_valid_fraction_threshold),
            'TOPOLOGY_COMPONENT_COUNT': str(num_features),
            'INPUT_VALID_PIXELS': str(input_valid_count),
            'TOTAL_PIXELS': str(info.total_pixel_count),
            'MAX_IN_MEMORY_CONTROL_NODES': str(self.max_in_memory_control_nodes),
            'RESIDENT_CONTROL_NODES': str(final_nodes_count),
            'RESIDENT_TIMESERIES_ARRAYS_PER_NODE': '2',
            'TIME_SAMPLES_PER_NODE': str(n_time_samples),
            'ESTIMATED_CONTROL_ARRAY_MEMORY_MB': f"{estimate_control_node_memory(final_nodes_count, n_time_samples, dtype_bytes=8):.2f}",
            'TARGET_MODE': str(target_mode),
            'QC_ENCODING': 'UInt16 bitmask: bit0=FES_extrapolated, bit1=spatial_fallback, bit2=insufficient_nodes, bit3=datum_invalid, bit4=datum_source_approx, bit5=min_spacing_reached, bit6=connectivity_fallback, bit7=fes_validity_boundary, bit8=max_refinement_reached'
        }

        return stream_inundation_frequency_interpolation(
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
            metadata_tags=metadata,
            allow_overwrite=allow_overwrite,
            control_nodes_count=final_nodes_count,
            progress_callback=progress_callback,
            cancel_event=cancel_event
        )
