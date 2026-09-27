"""
CoastTideX DEM 垂直基准转换模块 (DEM Datum Converter v1.7.1)
=============================================================
基于 Seeger & Minderhoud (Nature, 2026) "Sea level much higher than assumed in most coastal hazard assessments"
提出的近岸陆面垂直基准统一理论框架改编实现 (Adapted from Seeger & Minderhoud, 2026)。

【科学关系与方法关系界定 / Methodological Relation】:
    1. 理论框架一致 (Framework Consistent):
       - 采用局域平均海平面 (Local Mean Sea Level, MSL) 作为近岸陆面与潮位分析的统一几何与物理基准面；
       - 传统方案将动态潮位转为 EGM2008，而本框架前置将陆地 DEM 转换为 MSL 基准：
             Z_MSL = Z_EGM2008 - MDT - DeltaN
       - 转换后 DEM_MSL 与 FES2022b 潮位序列 Tide_MSL(t) 直接在 MSL 空间物理基准下进行严格比较：
             Tide_MSL(t) > DEM_MSL
    2. 工程实现改编说明 (Adapted Engineering Implementation):
       - Seeger & Minderhoud (2026) 在 ArcGIS 中采用 Smooth Neighborhood IDW (平滑因子 0.5) 并在沿岸 500 km 宏观范围内实施分析；
       - CoastTideX 采用高性能 Python / SciPy 球面三维笛卡尔直角坐标 k-最近邻反距离加权 (Spherical 3D k-NN IDW, k=8, p=2)；
       - CoastTideX 默认推荐外推门禁为 100.0 km (专为高分辨率潮滩与河口潮间带设定的保守稳定工程参数)，
         同时允许用户灵活配置 0.0 ~ 500.0 km 以满足大型三角洲或全球大尺度敏感性对比实验。
       - 本模块并非 ArcGIS 原生工作流的无差别复现 (not an exact reproduction)，而是面向全球自动化流式生产的改编工程实现。

【核心特性】:
    1. 动态自适应 MDT 空间索引窗口: 依据配置的外推距离动态扩展局部 NetCDF 缓存窗口，杜绝无效源点截断；
    2. 全球 ±180° 国际日界线 / 对向子午线 (Antimeridian) 环形拓扑自动展开切片与无缝连续插值；
    3. 严密 2D 矩形分块流式 I/O，支持超大型高分辨率 DEM 内存友好吞吐与原子写入保护 (*.tmp.tif)；
    4. 可选轻量化质量控制 (write_qc): 默认不落盘冗余 QC 栅格，显著节约存储与 I/O；
    5. 嵌入完整科学溯源与算法参数元数据 (Software, Method Relation, Extrapolation Distance, IDW Params)。
"""

import os
import time
import json
import hashlib
import warnings
from dataclasses import dataclass, field
from typing import Optional, Tuple, Dict, Any, Callable, Union, List

import numpy as np
import rasterio
import rasterio.warp
from rasterio.windows import Window
import xarray as xr
from scipy.interpolate import RegularGridInterpolator
from scipy.spatial import cKDTree

try:
    from pyproj import Transformer, CRS
except ImportError:
    Transformer = None
    CRS = None

from .utils import (
    load_app_config, resolve_project_path, normalize_longitude
)
from .datum_engine import DatumTransformer, DatumDataError

# ==============================================================================
# 外推距离门禁常量定义 (Extrapolation Distance Constants)
# ==============================================================================
DEFAULT_MDT_EXTRAPOLATION_DISTANCE_KM = 100.0
MIN_MDT_EXTRAPOLATION_DISTANCE_KM = 0.0
MAX_ALLOWED_MDT_EXTRAPOLATION_DISTANCE_KM = 500.0

# 保持历史兼容别名
MAX_MDT_EXTRAPOLATION_DISTANCE_KM = DEFAULT_MDT_EXTRAPOLATION_DISTANCE_KM
MAX_MDT_EXTRAPOLATION_DISTANCE_M = DEFAULT_MDT_EXTRAPOLATION_DISTANCE_KM * 1000.0

# 简洁 QC 编码常量 (Simple QC Specification)
QC_MDT_NATIVE = 0        # 原始大洋 MDT 双线性插值覆盖区
QC_MDT_EXTRAPOLATED = 1  # IDW 沿岸/陆地外推有效区 (在配置距离之内)
QC_MDT_NODATA = 2        # 超出配置距离门禁或输入 DEM 本身属于 NoData

# 地球平均曲率半径 (用于 3D 空间直角坐标测地弦长测算)
EARTH_RADIUS_M = 6371000.0


def validate_mdt_extrapolation_distance(dist_km: Optional[Union[float, int]]) -> float:
    """
    验证近岸 MDT 外推距离门禁 (0.0 - 500.0 km)。

    :param dist_km: 待验证的外推距离 (km)。若为 None 则返回默认推荐值 100.0 km。
    :return: 验证后的浮点数距离 (km)。
    :raises ValueError: 当输入非有限数值、小于 0.0 或大于 500.0 km 时抛出，严禁静默截断 (Silent Clamping)。
    """
    if dist_km is None:
        return DEFAULT_MDT_EXTRAPOLATION_DISTANCE_KM
    try:
        val = float(dist_km)
    except (TypeError, ValueError):
        raise ValueError(f"MDT 外推距离必须为合法数值，收到: {dist_km}")

    if not np.isfinite(val):
        raise ValueError(f"MDT 外推距离不能为 NaN 或 Inf，收到: {val}")

    if val < MIN_MDT_EXTRAPOLATION_DISTANCE_KM or val > MAX_ALLOWED_MDT_EXTRAPOLATION_DISTANCE_KM:
        raise ValueError(
            f"MDT 外推距离超出允许范围 [{MIN_MDT_EXTRAPOLATION_DISTANCE_KM:.1f}, {MAX_ALLOWED_MDT_EXTRAPOLATION_DISTANCE_KM:.1f}] km，"
            f"收到: {val:.2f} km。(CoastTideX 默认推荐 100.0 km; 全球大尺度分析/敏感性对比最大允许 500.0 km)"
        )
    return val


def compute_minimal_circular_longitude_interval(
    lons: Union[np.ndarray, List[float]]
) -> Tuple[float, float, bool]:
    """
    计算经度点集在 [-180, 180) 环形圆周拓扑上的最小外包区间 (Minimal Circular Longitude Interval)。
    自动检测是否跨越 ±180° 国际日界线 / 对向子午线 (Antimeridian)。

    :param lons: 经度数组或列表 (度)
    :return: (lon_min_unwrapped, lon_max_unwrapped, crosses_antimeridian)
        若 crosses_antimeridian 为 True，表示点集跨越了 ±180° 日界线。
        此时 lon_min_unwrapped 在 (0, 180] 范围内，lon_max_unwrapped > 180° (对应负经度 + 360°)。
        跨度 span = lon_max_unwrapped - lon_min_unwrapped <= 360°。
    """
    lons_arr = np.asarray(lons, dtype=np.float64).ravel()
    lons_arr = lons_arr[np.isfinite(lons_arr)]
    if len(lons_arr) == 0:
        return -180.0, 180.0, False

    # 规范化到 [-180, 180)
    norm_lons = ((lons_arr + 180.0) % 360.0) - 180.0
    uniq_lons = np.unique(norm_lons)
    if len(uniq_lons) == 1:
        return float(uniq_lons[0]), float(uniq_lons[0]), False

    uniq_sorted = np.sort(uniq_lons)
    gaps = np.diff(uniq_sorted)
    # 环绕过 180° 的空隙
    wrap_gap = (uniq_sorted[0] + 360.0) - uniq_sorted[-1]
    all_gaps = np.append(gaps, wrap_gap)

    max_gap_idx = int(np.argmax(all_gaps))

    if max_gap_idx == len(all_gaps) - 1:
        # 最大空隙在 180° 处，点集本身聚集在常规经度区间内，未跨越日界线
        lon_min = float(uniq_sorted[0])
        lon_max = float(uniq_sorted[-1])
        return lon_min, lon_max, False
    else:
        # 最大空隙在内部，点集跨越了 ±180° 日界线
        start_lon = float(uniq_sorted[max_gap_idx + 1])
        end_lon = float(uniq_sorted[max_gap_idx])
        if end_lon < start_lon:
            end_lon += 360.0
        return start_lon, end_lon, True


@dataclass
class DEMConversionSummary:
    """DEM 垂直基准转换执行摘要数据类"""
    input_path: str
    output_path: str
    qc_output_path: str
    width: int
    height: int
    total_pixels: int
    valid_dem_pixels: int
    native_mdt_pixels: int
    extrapolated_mdt_pixels: int
    nodata_pixels: int
    elapsed_seconds: float
    max_extrapolation_distance_km: float
    metadata: Dict[str, Any] = field(default_factory=dict)


class DEMDatumConverter:
    """
    DEM 垂直基准转换器 (DEM Vertical Datum Converter)
    实现 DEM_EGM2008 到 DEM_MSL 的高精度流式转换。
    """

    def __init__(
        self,
        mdt_path: Optional[str] = None,
        delta_n_path: Optional[str] = None,
        max_extrapolation_distance_km: float = DEFAULT_MDT_EXTRAPOLATION_DISTANCE_KM,
        idw_power: float = 2.0,
        idw_k: int = 8,
        transformer: Optional[DatumTransformer] = None
    ):
        config = load_app_config()
        paths_cfg = config.get('paths', {})

        if mdt_path is None:
            mdt_path = paths_cfg.get('mdt_nc', 'mdt_cls22/mdt_hybrid_cnes_cls22_cmems2020_global.nc')
        if delta_n_path is None:
            delta_n_path = paths_cfg.get('delta_n_goco06s_egm2008_tif', 'data/geoid/delta_n_goco06s_minus_egm2008.tif')

        self.mdt_path = resolve_project_path(mdt_path)
        self.delta_n_path = resolve_project_path(delta_n_path, prefer_resource=True)

        self.max_extrapolation_distance_km = validate_mdt_extrapolation_distance(max_extrapolation_distance_km)
        self.max_extrapolation_distance_m = self.max_extrapolation_distance_km * 1000.0

        self.idw_power = float(idw_power)
        self.idw_k = max(1, int(idw_k))

        if transformer is not None:
            self.transformer = transformer
        else:
            self.transformer = DatumTransformer(
                mdt_path=self.mdt_path,
                delta_n_path=self.delta_n_path
            )

        # 缓存局部 MDT 插值器与 KDTree
        self._cached_window_bounds = None
        self._cached_max_extrap_dist_m = None
        self._crosses_antimeridian = False
        self._mdt_interp = None
        self._ocean_kdtree = None
        self._ocean_mdt_values = None

    @staticmethod
    def compute_dynamic_support_window(
        bounds_wgs84: Tuple[float, float, float, float],
        max_extrapolation_distance_km: float
    ) -> Tuple[float, float, float, float]:
        """
        计算考虑外推距离和高纬度自适应的动态支持窗口外包 (w_lon, s_lat, e_lon, n_lat)。
        """
        b_lon_min, b_lat_min, b_lon_max, b_lat_max = bounds_wgs84
        max_extrap_m = float(max_extrapolation_distance_km) * 1000.0
        angular_radius_deg = float(np.degrees(max_extrap_m / EARTH_RADIUS_M))
        grid_margin = 0.5
        lat_pad = angular_radius_deg + grid_margin

        max_abs_lat = max(abs(b_lat_min), abs(b_lat_max))
        cos_lat = max(float(abs(np.cos(np.radians(max_abs_lat)))), 0.05)
        lon_pad = min(180.0, (angular_radius_deg / cos_lat) + grid_margin)

        return (
            b_lon_min - lon_pad,
            max(-89.9, b_lat_min - lat_pad),
            b_lon_max + lon_pad,
            min(89.9, b_lat_max + lat_pad)
        )

    def _compute_dynamic_support_window(
        self,
        bounds_wgs84: Tuple[float, float, float, float],
        max_extrapolation_distance_km: Optional[float] = None
    ) -> Tuple[float, float, float, float]:
        dist_km = max_extrapolation_distance_km if max_extrapolation_distance_km is not None else self.max_extrapolation_distance_km
        return self.compute_dynamic_support_window(bounds_wgs84, dist_km)

    def _ensure_mdt_spatial_index(self, bounds_wgs84: Tuple[float, float, float, float]):
        """
        根据目标区域地理外包与配置的最大外推距离，构建高效率局部 MDT 双线性插值器与 3D 空间 KDTree。
        支持根据 max_extrapolation_distance_km 动态扩展支持窗口，并完美支持跨 ±180° 日界线切片。

        bounds_wgs84: (min_lon, min_lat, max_lon, max_lat)
        """
        b_lon_min, b_lat_min, b_lon_max, b_lat_max = bounds_wgs84

        # 1. 动态支持窗口大小计算 (Dynamic Support Window)
        # 球面角距离半径 (degrees)
        angular_radius_deg = float(np.degrees(self.max_extrapolation_distance_m / EARTH_RADIUS_M))
        grid_margin = 0.5  # 至少 4 个 0.125° 网格单元的安全插值余量
        lat_pad = angular_radius_deg + grid_margin

        # 高纬度经度收缩自适应 buffer
        max_abs_lat = max(abs(b_lat_min), abs(b_lat_max))
        cos_lat = max(float(abs(np.cos(np.radians(max_abs_lat)))), 0.05)
        lon_pad = min(180.0, (angular_radius_deg / cos_lat) + grid_margin)

        # 计算经度最小圆周区间
        eff_lon_min, eff_lon_max, crosses_antimeridian = compute_minimal_circular_longitude_interval(
            [b_lon_min, b_lon_max]
        )

        q_lat_min = max(-89.9, b_lat_min - lat_pad)
        q_lat_max = min(89.9, b_lat_max + lat_pad)

        q_lon_min = eff_lon_min - lon_pad
        q_lon_max = eff_lon_max + lon_pad

        # 检查是否因 buffer 扩展而跨越 ±180° 日界线
        needs_antimeridian = crosses_antimeridian or (q_lon_max > 180.0) or (q_lon_min < -180.0)

        if needs_antimeridian:
            # 统一规范化为 [lon_start_unwrapped, lon_end_unwrapped]
            if q_lon_min < -180.0:
                q_lon_min_unwrapped = q_lon_min + 360.0
                q_lon_max_unwrapped = q_lon_max + 360.0
            else:
                q_lon_min_unwrapped = q_lon_min
                q_lon_max_unwrapped = q_lon_max
        else:
            q_lon_min_unwrapped = max(-180.0, q_lon_min)
            q_lon_max_unwrapped = min(180.0, q_lon_max)

        req_bounds = (q_lon_min_unwrapped, q_lat_min, q_lon_max_unwrapped, q_lat_max, needs_antimeridian)

        # 检查缓存是否有效且完全包含当前请求窗口
        if (self._cached_window_bounds is not None and
            self._cached_max_extrap_dist_m is not None and
            self._cached_max_extrap_dist_m >= self.max_extrapolation_distance_m):

            c_lon_min, c_lat_min, c_lon_max, c_lat_max, c_antimeridian = self._cached_window_bounds
            if c_antimeridian == needs_antimeridian:
                if (req_bounds[0] >= c_lon_min and req_bounds[1] >= c_lat_min and
                    req_bounds[2] <= c_lon_max and req_bounds[3] <= c_lat_max):
                    return

        if not os.path.exists(self.mdt_path):
            raise DatumDataError(f"未找到 CNES-CLS22 MDT 数据文件: {self.mdt_path}")

        # 增加额外预留 padding，减少小幅度滑动时的重复重构开销
        cache_pad_deg = 1.0
        final_lat_min = max(-89.9, q_lat_min - cache_pad_deg)
        final_lat_max = min(89.9, q_lat_max + cache_pad_deg)

        final_lon_min = q_lon_min_unwrapped - cache_pad_deg
        final_lon_max = q_lon_max_unwrapped + cache_pad_deg

        with xr.open_dataset(self.mdt_path) as ds:
            mdt_lon_min = float(ds.longitude.min())
            is_360_convention = (mdt_lon_min >= 0.0)

            if not needs_antimeridian:
                # 标准无日界线切片
                sub_ds = ds.sel(
                    latitude=slice(float(final_lat_min), float(final_lat_max)),
                    longitude=slice(float(max(-180.0, final_lon_min)), float(min(180.0, final_lon_max)))
                )
                sub_lats = sub_ds.latitude.values.astype(np.float64)
                sub_lons = sub_ds.longitude.values.astype(np.float64)
                sub_mdt = sub_ds.mdt.values[0].astype(np.float64)
                self._crosses_antimeridian = False
            else:
                # 跨越 ±180° 日界线切片 (两个局部 slice + unwrapped concatenate)
                # 切片 1: 正经度侧 [final_lon_min_norm, 180.0]
                slice1_min = max(-180.0, ((final_lon_min + 180.0) % 360.0) - 180.0) if final_lon_min < 0 else min(179.99, final_lon_min)
                slice2_max = min(180.0, ((final_lon_max + 180.0) % 360.0) - 180.0)

                sub1 = ds.sel(
                    latitude=slice(float(final_lat_min), float(final_lat_max)),
                    longitude=slice(float(slice1_min), 180.0)
                )
                sub2 = ds.sel(
                    latitude=slice(float(final_lat_min), float(final_lat_max)),
                    longitude=slice(-180.0, float(slice2_max))
                )

                sub1_lons = sub1.longitude.values.astype(np.float64)
                sub2_lons = sub2.longitude.values.astype(np.float64) + 360.0
                sub_lons = np.concatenate([sub1_lons, sub2_lons])
                sub_lats = sub1.latitude.values.astype(np.float64)

                sub1_mdt = sub1.mdt.values[0].astype(np.float64)
                sub2_mdt = sub2.mdt.values[0].astype(np.float64)
                sub_mdt = np.concatenate([sub1_mdt, sub2_mdt], axis=1)
                self._crosses_antimeridian = True

        if len(sub_lats) < 2 or len(sub_lons) < 2:
            raise ValueError(f"指定区域所截取的 MDT 网格过于狭窄或越界: {bounds_wgs84}")

        self._mdt_interp = RegularGridInterpolator(
            (sub_lats, sub_lons), sub_mdt,
            method='linear',
            bounds_error=False,
            fill_value=np.nan
        )

        # 提取有效大洋 MDT 点位并转换为三维空间笛卡尔直角坐标 (XYZ)
        mesh_lats, mesh_lons = np.meshgrid(sub_lats, sub_lons, indexing='ij')
        valid_mask = np.isfinite(sub_mdt)
        valid_lats = mesh_lats[valid_mask]
        valid_lons = mesh_lons[valid_mask]
        valid_vals = sub_mdt[valid_mask]

        if len(valid_vals) > 0:
            rad_lats = np.radians(valid_lats)
            rad_lons = np.radians(valid_lons)
            xs = EARTH_RADIUS_M * np.cos(rad_lats) * np.cos(rad_lons)
            ys = EARTH_RADIUS_M * np.cos(rad_lats) * np.sin(rad_lons)
            zs = EARTH_RADIUS_M * np.sin(rad_lats)
            pts_3d = np.column_stack([xs, ys, zs])
            self._ocean_kdtree = cKDTree(pts_3d)
            self._ocean_mdt_values = valid_vals
        else:
            self._ocean_kdtree = None
            self._ocean_mdt_values = np.array([], dtype=float)

        self._cached_window_bounds = (final_lon_min, final_lat_min, final_lon_max, final_lat_max, self._crosses_antimeridian)
        self._cached_max_extrap_dist_m = self.max_extrapolation_distance_m

    def evaluate_mdt_with_idw(
        self,
        lons: np.ndarray,
        lats: np.ndarray
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        对指定点位数组执行两阶段 MDT 插值与外推 (Two-step MDT Interpolation & Extrapolation):
        Step 1: 原生大洋区执行双线性插值 (Bilinear Interpolation)；
        Step 2: 陆地/近岸缺失区且距离有效大洋 <= max_extrapolation_distance_km 执行 IDW 外推；
        Step 3: 超出外推距离门禁 (或 max_extrapolation_distance_km == 0) 赋予 NaN 并标记 NoData。

        返回:
            (mdt_values, qc_flags)
            qc_flags: 0=NATIVE, 1=EXTRAPOLATED, 2=NODATA
        """
        lons_arr = np.atleast_1d(np.asarray(lons, dtype=np.float64))
        lats_arr = np.atleast_1d(np.asarray(lats, dtype=np.float64))
        n_pts = len(lons_arr)

        mdt_out = np.full(n_pts, np.nan, dtype=np.float32)
        qc_out = np.full(n_pts, QC_MDT_NODATA, dtype=np.uint8)

        valid_coords = np.isfinite(lons_arr) & np.isfinite(lats_arr) & (lats_arr >= -90.0) & (lats_arr <= 90.0)
        if not np.any(valid_coords):
            return mdt_out, qc_out

        v_lons = lons_arr[valid_coords]
        v_lats = lats_arr[valid_coords]
        v_indices = np.where(valid_coords)[0]

        # 确保局部空间索引就绪
        b_wgs = (float(np.min(v_lons)), float(np.min(v_lats)), float(np.max(v_lons)), float(np.max(v_lats)))
        self._ensure_mdt_spatial_index(b_wgs)

        # Step 1: 原生大洋双线性插值
        if self._crosses_antimeridian:
            # 跨日界线模式下，负经度点展开到 > 180°
            interp_lons = np.where(v_lons < 0.0, v_lons + 360.0, v_lons)
        else:
            interp_lons = v_lons

        pts_2d = np.column_stack([v_lats, interp_lons])
        native_vals = self._mdt_interp(pts_2d)

        native_mask = np.isfinite(native_vals)
        if np.any(native_mask):
            idx_nat = v_indices[native_mask]
            mdt_out[idx_nat] = native_vals[native_mask].astype(np.float32)
            qc_out[idx_nat] = QC_MDT_NATIVE

        # Step 2: 缺失区域 (陆地/潮滩内部) 执行受门禁限制的 IDW 外推
        # 当配置距离 <= 0 时严格跳过外推，纯保留原生有效值
        missing_mask = ~native_mask
        if (self.max_extrapolation_distance_m > 0.0 and
            np.any(missing_mask) and
            self._ocean_kdtree is not None and
            len(self._ocean_mdt_values) > 0):

            m_lons = v_lons[missing_mask]
            m_lats = v_lats[missing_mask]
            idx_miss = v_indices[missing_mask]

            rad_lat = np.radians(m_lats)
            rad_lon = np.radians(m_lons)
            xt = EARTH_RADIUS_M * np.cos(rad_lat) * np.cos(rad_lon)
            yt = EARTH_RADIUS_M * np.cos(rad_lat) * np.sin(rad_lon)
            zt = EARTH_RADIUS_M * np.sin(rad_lat)
            targets_3d = np.column_stack([xt, yt, zt])

            k_query = min(self.idw_k, len(self._ocean_mdt_values))
            dists, indices = self._ocean_kdtree.query(targets_3d, k=k_query, workers=-1)

            if k_query == 1:
                dists = dists[:, np.newaxis]
                indices = indices[:, np.newaxis]

            min_dists = dists[:, 0]
            within_dist_mask = (min_dists <= self.max_extrapolation_distance_m)

            if np.any(within_dist_mask):
                sub_dists = dists[within_dist_mask]
                sub_indices = indices[within_dist_mask]
                sub_vals = self._ocean_mdt_values[sub_indices]

                # 避免距离极小除以 0
                zero_hit = (sub_dists[:, 0] < 1e-4)
                extrap_res = np.zeros(len(sub_dists), dtype=np.float32)

                if np.any(zero_hit):
                    extrap_res[zero_hit] = sub_vals[zero_hit, 0]

                non_zero = ~zero_hit
                if np.any(non_zero):
                    nz_dists = sub_dists[non_zero]
                    nz_vals = sub_vals[non_zero]
                    weights = 1.0 / (nz_dists ** self.idw_power)
                    sum_w = np.sum(weights, axis=1)
                    extrap_res[non_zero] = (np.sum(weights * nz_vals, axis=1) / sum_w).astype(np.float32)

                idx_extrap = idx_miss[within_dist_mask]
                mdt_out[idx_extrap] = extrap_res
                qc_out[idx_extrap] = QC_MDT_EXTRAPOLATED

        return mdt_out, qc_out

    def convert_points(
        self,
        lons: np.ndarray,
        lats: np.ndarray,
        z_egm2008: np.ndarray
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """
        点位级向量化转换:
            Z_MSL = Z_EGM2008 - MDT - DeltaN

        返回:
            (z_msl, mdt, delta_n, qc)
        """
        lons_arr = np.atleast_1d(normalize_longitude(lons, to_360=False))
        lats_arr = np.atleast_1d(np.asarray(lats, dtype=float))
        z_arr = np.atleast_1d(np.asarray(z_egm2008, dtype=float))

        n_pts = len(z_arr)
        z_msl = np.full(n_pts, np.nan, dtype=np.float32)

        # 1. 计算 MDT (含 IDW 外推与 QC)
        mdt_vals, qc_vals = self.evaluate_mdt_with_idw(lons_arr, lats_arr)

        # 2. 计算 DeltaN
        delta_n_vals = np.atleast_1d(self.transformer.get_delta_n(lons_arr, lats_arr, strict=False)).astype(np.float32)

        # 3. 严格执行基准转换公式
        valid_calc = np.isfinite(z_arr) & np.isfinite(mdt_vals) & np.isfinite(delta_n_vals) & (qc_vals != QC_MDT_NODATA)
        z_msl[valid_calc] = (z_arr[valid_calc] - mdt_vals[valid_calc] - delta_n_vals[valid_calc]).astype(np.float32)

        # 无效点统一置 NoData 标记
        qc_vals[~valid_calc] = QC_MDT_NODATA

        return z_msl, mdt_vals, delta_n_vals, qc_vals

    def convert_raster(
        self,
        input_dem_path: str,
        output_msl_path: Optional[str] = None,
        output_qc_path: Optional[str] = None,
        max_extrapolation_distance_km: Optional[float] = None,
        block_size: int = 1024,
        allow_overwrite: bool = True,
        write_qc: Optional[bool] = None,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        cancel_event: Optional[Any] = None
    ) -> DEMConversionSummary:
        """
        高分辨率 GeoTIFF 空间栅格流式垂直基准转换:
            将输入 DEM_EGM2008.tif 转换为 DEM_MSL.tif。

        :param input_dem_path: 输入 DEM 文件路径 (EGM2008 基准)
        :param output_msl_path: 输出 DEM_MSL 路径 (默认: <input>_MSL.tif)
        :param output_qc_path: 输出 QC 路径 (若提供路径或 write_qc=True 时生效)
        :param max_extrapolation_distance_km: MDT 外推距离门禁 (0 - 500 km, 默认: 100 km)
        :param block_size: 2D 空间流式分块大小 (默认 1024)
        :param allow_overwrite: 是否允许覆盖既有输出
        :param write_qc: 是否落盘保存质量控制掩膜 GeoTIFF (默认: False，若显式提供 output_qc_path 则默认开启)
        :param progress_callback: 进度回调 (percent, msg)
        :param cancel_event: 取消事件句柄
        """
        t0 = time.time()
        if write_qc is None:
            write_qc = (output_qc_path is not None)

        if max_extrapolation_distance_km is not None:
            self.max_extrapolation_distance_km = validate_mdt_extrapolation_distance(max_extrapolation_distance_km)
            self.max_extrapolation_distance_m = self.max_extrapolation_distance_km * 1000.0

        if not os.path.exists(input_dem_path):
            raise FileNotFoundError(f"未找到输入 DEM 文件: {input_dem_path}")

        if output_msl_path is None:
            base, ext = os.path.splitext(input_dem_path)
            output_msl_path = f"{base}_MSL{ext}"

        if write_qc:
            if output_qc_path is None:
                base, ext = os.path.splitext(output_msl_path)
                output_qc_path = f"{base}_conversion_qc{ext}"
        else:
            output_qc_path = ""

        if not allow_overwrite:
            if os.path.exists(output_msl_path):
                raise FileExistsError(f"输出 DEM_MSL 文件已存在且未开启覆盖权限: {output_msl_path}")
            if write_qc and output_qc_path and os.path.exists(output_qc_path):
                raise FileExistsError(f"输出 QC 文件已存在且未开启覆盖权限: {output_qc_path}")

        out_dir = os.path.dirname(os.path.abspath(output_msl_path))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

        tmp_msl = f"{output_msl_path}.tmp.tif"
        tmp_qc = f"{output_qc_path}.tmp.tif" if write_qc and output_qc_path else None

        if progress_callback:
            progress_callback(5, "检查输入 DEM 几何元数据并预构建局部 MDT 空间索引...")

        with rasterio.open(input_dem_path) as src:
            w = src.width
            h = src.height
            crs = src.crs
            transform = src.transform
            nodata_val = src.nodata if src.nodata is not None else -9999.0
            is_projected = crs.is_projected if crs else False

            # 计算 WGS84 地理外包矩形 (支持投影坐标系与高纬度跨越)
            bounds = src.bounds
            if not is_projected:
                b_wgs = (float(bounds.left), float(bounds.bottom), float(bounds.right), float(bounds.top))
                transformer_to_wgs = None
            else:
                xs_corners = [bounds.left, bounds.right, bounds.right, bounds.left,
                              (bounds.left + bounds.right) / 2.0, (bounds.left + bounds.right) / 2.0,
                              bounds.left, bounds.right]
                ys_corners = [bounds.bottom, bounds.bottom, bounds.top, bounds.top,
                              bounds.bottom, bounds.top,
                              (bounds.bottom + bounds.top) / 2.0, (bounds.bottom + bounds.top) / 2.0]
                if Transformer is not None:
                    transformer_to_wgs = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
                    t_lons, t_lats = transformer_to_wgs.transform(xs_corners, ys_corners)
                else:
                    t_lons, t_lats = rasterio.warp.transform(crs, "EPSG:4326", xs_corners, ys_corners)

                eff_l_min, eff_l_max, _ = compute_minimal_circular_longitude_interval(t_lons)
                b_wgs = (float(eff_l_min), float(min(t_lats)), float(eff_l_max), float(max(t_lats)))

            self._ensure_mdt_spatial_index(b_wgs)

            # 配置输出栅格 profile
            out_profile = {
                'driver': 'GTiff',
                'height': h,
                'width': w,
                'count': 1,
                'dtype': rasterio.float32,
                'crs': crs,
                'transform': transform,
                'nodata': np.float32(nodata_val),
                'compress': 'deflate',
                'predictor': 2,
                'tiled': (w >= 16 and h >= 16 and block_size >= 16)
            }
            if out_profile['tiled']:
                out_profile['blockxsize'] = min(512, max(16, (int(block_size) // 16) * 16))
                out_profile['blockysize'] = min(512, max(16, (int(block_size) // 16) * 16))

            if write_qc and tmp_qc:
                qc_profile = out_profile.copy()
                qc_profile.update({
                    'dtype': rasterio.uint8,
                    'nodata': QC_MDT_NODATA,
                    'predictor': 1
                })
            else:
                qc_profile = None

            total_blocks_x = int(np.ceil(w / block_size))
            total_blocks_y = int(np.ceil(h / block_size))
            total_blocks = total_blocks_x * total_blocks_y
            block_counter = 0

            valid_dem_count = 0
            native_count = 0
            extrap_count = 0
            nodata_count = 0

            if progress_callback:
                progress_callback(10, f"开始 2D 矩形分块流式转换 ({w}×{h}, 共 {total_blocks} 块)...")

            try:
                # 依据 write_qc 动态打开输出文件句柄
                dst_qc = None
                with rasterio.open(tmp_msl, 'w', **out_profile) as dst_msl:
                    if write_qc and tmp_qc:
                        dst_qc = rasterio.open(tmp_qc, 'w', **qc_profile)

                    try:
                        for r_off in range(0, h, block_size):
                            for c_off in range(0, w, block_size):
                                if cancel_event is not None and cancel_event.is_set():
                                    raise RuntimeError("用户主动取消了 DEM 垂直基准转换任务。")

                                win_w = min(block_size, w - c_off)
                                win_h = min(block_size, h - r_off)
                                window = Window(c_off, r_off, win_w, win_h)

                                dem_block = src.read(1, window=window)
                                out_block = np.full(dem_block.shape, nodata_val, dtype=np.float32)
                                qc_block = np.full(dem_block.shape, QC_MDT_NODATA, dtype=np.uint8)

                                if src.nodata is not None and np.isfinite(src.nodata):
                                    v_mask = ~np.isclose(dem_block, src.nodata) & np.isfinite(dem_block)
                                else:
                                    v_mask = np.isfinite(dem_block)

                                n_valid = int(np.count_nonzero(v_mask))
                                valid_dem_count += n_valid

                                if n_valid > 0:
                                    rows_local, cols_local = np.where(v_mask)
                                    rows_glob = rows_local + r_off
                                    cols_glob = cols_local + c_off

                                    # 计算像元中心地理坐标
                                    xs_crs, ys_crs = rasterio.transform.xy(transform, rows_glob, cols_glob, offset='center')
                                    xs_arr = np.asarray(xs_crs, dtype=float)
                                    ys_arr = np.asarray(ys_crs, dtype=float)

                                    if not is_projected:
                                        pix_lons = normalize_longitude(xs_arr, to_360=False)
                                        pix_lats = ys_arr
                                    else:
                                        if transformer_to_wgs is not None:
                                            t_lons, t_lats = transformer_to_wgs.transform(xs_arr, ys_arr)
                                        else:
                                            t_lons, t_lats = rasterio.warp.transform(crs, "EPSG:4326", xs_arr, ys_arr)
                                        pix_lons = normalize_longitude(np.asarray(t_lons, dtype=float), to_360=False)
                                        pix_lats = np.asarray(t_lats, dtype=float)

                                    z_vals = dem_block[v_mask]
                                    z_msl_sub, mdt_sub, dn_sub, qc_sub = self.convert_points(pix_lons, pix_lats, z_vals)

                                    # 填入 block
                                    sub_valid = (qc_sub != QC_MDT_NODATA) & np.isfinite(z_msl_sub)
                                    out_block[rows_local[sub_valid], cols_local[sub_valid]] = z_msl_sub[sub_valid]
                                    qc_block[rows_local, cols_local] = qc_sub

                                    native_count += int(np.count_nonzero(qc_sub == QC_MDT_NATIVE))
                                    extrap_count += int(np.count_nonzero(qc_sub == QC_MDT_EXTRAPOLATED))
                                    nodata_count += int(np.count_nonzero(qc_sub == QC_MDT_NODATA))
                                else:
                                    nodata_count += (win_w * win_h)

                                dst_msl.write(out_block, 1, window=window)
                                if dst_qc is not None:
                                    dst_qc.write(qc_block, 1, window=window)

                                block_counter += 1
                                if progress_callback and (block_counter % 5 == 0 or block_counter == total_blocks):
                                    pct = min(95, 10 + int(85 * block_counter / total_blocks))
                                    progress_callback(pct, f"流式转换中 ({block_counter}/{total_blocks} 块)...")
                    finally:
                        if dst_qc is not None:
                            dst_qc.close()

                # 嵌入完整科学溯源与算法元数据 (Scientific Provenance Metadata)
                elapsed = time.time() - t0
                total_pix = w * h
                meta_tags = {
                    'SOFTWARE': 'CoastTideX v1.7.1',
                    'CONVERTER': 'DEMDatumConverter',
                    'DATUM': 'MSL',
                    'ANALYSIS_REFERENCE': 'MSL',
                    'SOURCE_VERTICAL_DATUM': 'EGM2008',
                    'TARGET_VERTICAL_DATUM': 'MSL',
                    'MDT_MODEL': 'CNES-CLS22',
                    'MDT_METHOD': 'bilinear_native_plus_spherical_knn_idw',
                    'IDW_POWER': str(self.idw_power),
                    'IDW_K': str(self.idw_k),
                    'DISTANCE_METRIC': '3D_spherical_chord_distance',
                    'MAX_EXTRAPOLATION_DISTANCE_KM': str(self.max_extrapolation_distance_km),
                    'DELTAN_SOURCE': 'DatumTransformer (GOCO06s/EIGEN-6C4)',
                    'EQUATION': 'Z_MSL = Z_EGM2008 - MDT - DeltaN',
                    'SCIENTIFIC_CITATION': 'Adapted from Seeger & Minderhoud (Nature, 2026)',
                    'METHOD_RELATION': 'Adapted from Seeger & Minderhoud (2026); not an exact reproduction of ArcGIS Smooth Neighborhood IDW',
                    'QC_ENCODING': 'UInt8: 0=native_mdt, 1=idw_extrapolated, 2=nodata',
                    'INPUT_DEM': os.path.basename(input_dem_path),
                    'TOTAL_PIXELS': str(total_pix),
                    'VALID_DEM_PIXELS': str(valid_dem_count),
                    'NATIVE_MDT_PIXELS': str(native_count),
                    'EXTRAPOLATED_MDT_PIXELS': str(extrap_count),
                    'NODATA_PIXELS': str(total_pix - native_count - extrap_count),
                    'ELAPSED_SECONDS': f"{elapsed:.2f}"
                }

                with rasterio.open(tmp_msl, 'r+') as dst_msl_meta:
                    dst_msl_meta.update_tags(**meta_tags)

                if write_qc and tmp_qc and os.path.exists(tmp_qc):
                    with rasterio.open(tmp_qc, 'r+') as dst_qc_meta:
                        dst_qc_meta.update_tags(**meta_tags)

                # 原子替换到最终目标路径
                if os.path.exists(output_msl_path) and allow_overwrite:
                    os.remove(output_msl_path)
                os.replace(tmp_msl, output_msl_path)

                if write_qc and tmp_qc and output_qc_path:
                    if os.path.exists(output_qc_path) and allow_overwrite:
                        os.remove(output_qc_path)
                    os.replace(tmp_qc, output_qc_path)

            except Exception:
                if os.path.exists(tmp_msl):
                    try:
                        os.remove(tmp_msl)
                    except Exception:
                        pass
                if tmp_qc and os.path.exists(tmp_qc):
                    try:
                        os.remove(tmp_qc)
                    except Exception:
                        pass
                raise

        if progress_callback:
            progress_callback(100, f"DEM 垂直基准转换成功完成！耗时: {elapsed:.2f}s")

        return DEMConversionSummary(
            input_path=input_dem_path,
            output_path=output_msl_path,
            qc_output_path=output_qc_path if write_qc else "",
            width=w,
            height=h,
            total_pixels=total_pix,
            valid_dem_pixels=valid_dem_count,
            native_mdt_pixels=native_count,
            extrapolated_mdt_pixels=extrap_count,
            nodata_pixels=total_pix - native_count - extrap_count,
            elapsed_seconds=elapsed,
            max_extrapolation_distance_km=self.max_extrapolation_distance_km,
            metadata=meta_tags
        )


def convert_dem_to_msl(
    input_dem_path: str,
    output_msl_path: Optional[str] = None,
    output_qc_path: Optional[str] = None,
    max_extrapolation_distance_km: float = DEFAULT_MDT_EXTRAPOLATION_DISTANCE_KM,
    block_size: int = 1024,
    allow_overwrite: bool = True,
    write_qc: Optional[bool] = None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    cancel_event: Optional[Any] = None
) -> DEMConversionSummary:
    """
    便捷公共函数: 将 DEM_EGM2008 转换为 DEM_MSL (Adapted from Seeger & Minderhoud, Nature, 2026)。

    :param input_dem_path: 输入 DEM 文件路径 (EGM2008)
    :param output_msl_path: 输出 DEM_MSL 路径
    :param output_qc_path: 输出 QC 掩膜路径 (若提供路径或 write_qc=True 生效)
    :param max_extrapolation_distance_km: MDT 外推距离门禁 (0.0 - 500.0 km, 默认: 100.0 km)
    :param block_size: 空间分块流式大小
    :param allow_overwrite: 允许覆盖
    :param write_qc: 是否输出质量控制掩膜 GeoTIFF (默认: False，若显式提供 output_qc_path 则默认开启)
    :param progress_callback: 进度回调
    :param cancel_event: 取消事件
    """
    converter = DEMDatumConverter(
        max_extrapolation_distance_km=max_extrapolation_distance_km
    )
    return converter.convert_raster(
        input_dem_path=input_dem_path,
        output_msl_path=output_msl_path,
        output_qc_path=output_qc_path,
        max_extrapolation_distance_km=max_extrapolation_distance_km,
        block_size=block_size,
        allow_overwrite=allow_overwrite,
        write_qc=write_qc,
        progress_callback=progress_callback,
        cancel_event=cancel_event
    )
