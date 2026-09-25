"""
CoastTideX DEM 垂直基准转换模块 (DEM Datum Converter v1.7)
基于 Seeger & Minderhoud (Nature, 2026) "Sea level much higher than assumed in most coastal hazard assessments"
提出的 MDT 驱动的近岸陆面垂直基准统一理论框架改编实现 (Adapted from Seeger & Minderhoud, 2026)。

核心思想:
    传统评估常尝试将复杂时空变化的动力海表面高程 (Ocean Surface / Tide) 转换到固定陆地重力水准面 (如 EGM2008)。
    v1.7 架构将陆地高程基准前置转换为局部平均海平面 (Local Mean Sea Level, MSL) 基准：
        Z_MSL = Z_EGM2008 - MDT - DeltaN
    之后 FES2022b 潮位 Tide_MSL(t) 与 DEM_MSL 直接在同一 MSL 空间几何物理基准下进行比较：
        Tide_MSL(t) > DEM_MSL

算法机制:
    1. MDT 空间连续性重构:
       - Step 1 (Native Ocean MDT): 开阔大洋有效区域使用双线性插值 (Bilinear Interpolation)；
       - Step 2 (Coastal / Land Extrapolation): 近岸与陆地 MDT 缺失区使用球面 3D 空间直角坐标反距离加权 (IDW) 空间外推；
       - 空间外推截断门禁: MAX_MDT_EXTRAPOLATION_DISTANCE_KM = 100.0 km。
         若像元至最近有效大洋 MDT 网格点的球面测地距离 <= 100 km，允许外推；
         若 > 100 km，返回 NoData 并标记 QC = 2，禁止深陆无限外推。
         (注: 100 km 为 CoastTideX 针对高分辨率潮滩工程设定的保守阈值，Seeger & Minderhoud 原研究针对全球宏观尺度采用 500 km)
    2. DeltaN 改正:
       - 采用全球高精度超高阶差值栅格 Delta N = N_ref - N_EGM2008 (GOCO06s 全球大洋，EIGEN-6C4 地中海/黑海)。
    3. 严密 2D 矩形分块流式 I/O (默认 1024x1024)，支持超大型高分辨率 DEM 内存友好吞吐与原子写入保护 (*.tmp.tif)。
    4. 简洁轻量转换质量控制 (Conversion QC):
       - 0: VALID (Native Ocean MDT 双线性插值)
       - 1: EXTRAPOLATED (IDW 外推，距离 <= 100 km)
       - 2: NODATA (距离 > 100 km 或输入 DEM 为 NoData)
"""

import os
import time
import warnings
from dataclasses import dataclass, field
from typing import Optional, Tuple, Dict, Any, Callable, Union

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

# CoastTideX 设定的应用专属保守外推上限 100 km (注: Seeger & Minderhoud 2026 原研究针对全球宏观尺度采用 500 km)
MAX_MDT_EXTRAPOLATION_DISTANCE_KM = 100.0
MAX_MDT_EXTRAPOLATION_DISTANCE_M = MAX_MDT_EXTRAPOLATION_DISTANCE_KM * 1000.0

# 简洁 QC 常量定义 (Simple QC Specification)
QC_MDT_NATIVE = 0        # 原始大洋 MDT 双线性插值覆盖区
QC_MDT_EXTRAPOLATED = 1    # IDW 沿岸/陆地外推有效区 (距离 <= 100 km)
QC_MDT_NODATA = 2          # 超出 100 km 阈值或输入 DEM 本身属于 NoData

# 地球平均曲率半径 (用于 3D 空间直角坐标测地弦长测算)
EARTH_RADIUS_M = 6371000.0


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
        max_extrapolation_distance_km: float = MAX_MDT_EXTRAPOLATION_DISTANCE_KM,
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
        self.max_extrapolation_distance_km = float(max_extrapolation_distance_km)
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
        self._mdt_interp = None
        self._ocean_kdtree = None
        self._ocean_mdt_values = None

    def _ensure_mdt_spatial_index(self, bounds_wgs84: Tuple[float, float, float, float], buffer_deg: float = 2.5):
        """
        根据目标区域地理外包构建高效率局部 MDT 双线性插值器与 3D 空间 KDTree。
        bounds_wgs84: (min_lon, min_lat, max_lon, max_lat)
        """
        b_lon_min, b_lat_min, b_lon_max, b_lat_max = bounds_wgs84
        req_bounds = (
            b_lon_min - buffer_deg,
            max(-89.9, b_lat_min - buffer_deg),
            b_lon_max + buffer_deg,
            min(89.9, b_lat_max + buffer_deg)
        )

        if self._cached_window_bounds is not None:
            c_min_lon, c_min_lat, c_max_lon, c_max_lat = self._cached_window_bounds
            if (req_bounds[0] >= c_min_lon and req_bounds[1] >= c_min_lat and
                req_bounds[2] <= c_max_lon and req_bounds[3] <= c_max_lat):
                return

        if not os.path.exists(self.mdt_path):
            raise DatumDataError(f"未找到 CNES-CLS22 MDT 数据文件: {self.mdt_path}")

        # 扩大范围缓存，确保周围 100km+ 区域的大洋控制点均被囊括
        pad = max(buffer_deg, 3.0)
        q_lon_min = max(-180.0, b_lon_min - pad)
        q_lon_max = min(180.0, b_lon_max + pad)
        q_lat_min = max(-89.9, b_lat_min - pad)
        q_lat_max = min(89.9, b_lat_max + pad)

        with xr.open_dataset(self.mdt_path) as ds:
            sub = ds.sel(
                latitude=slice(float(q_lat_min), float(q_lat_max)),
                longitude=slice(float(q_lon_min), float(q_lon_max))
            )
            sub_lats = sub.latitude.values.astype(np.float64)
            sub_lons = sub.longitude.values.astype(np.float64)
            sub_mdt = sub.mdt.values[0].astype(np.float64)

        if len(sub_lats) < 2 or len(sub_lons) < 2:
            raise ValueError(f"指定区域所截取的 MDT 网格过于狭窄或越界: {bounds_wgs84}")

        self._mdt_interp = RegularGridInterpolator(
            (sub_lats, sub_lons), sub_mdt,
            method='linear',
            bounds_error=False,
            fill_value=np.nan
        )

        # 提取有效大洋 MDT 点位并转换为三维空间直角坐标 (消除极区与高纬度投影变形)
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

        self._cached_window_bounds = (q_lon_min, q_lat_min, q_lon_max, q_lat_max)

    def evaluate_mdt_with_idw(
        self,
        lons: np.ndarray,
        lats: np.ndarray
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        对指定点位数组执行两阶段 MDT 插值与外推 (Two-step MDT Interpolation & Extrapolation):
        Step 1: 原生大洋区执行双线性插值 (Bilinear Interpolation)；
        Step 2: 陆地/近岸缺失区且距离有效大洋 <= 100km 执行 IDW 外推；
        Step 3: 超出 100km 截断门禁赋予 NaN 并标记 NoData。

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
        pts_2d = np.column_stack([v_lats, v_lons])
        native_vals = self._mdt_interp(pts_2d)

        native_mask = np.isfinite(native_vals)
        if np.any(native_mask):
            idx_nat = v_indices[native_mask]
            mdt_out[idx_nat] = native_vals[native_mask].astype(np.float32)
            qc_out[idx_nat] = QC_MDT_NATIVE

        # Step 2: 缺失区域 (陆地/潮滩内部) 执行 100km 门禁限制的 IDW 外推
        missing_mask = ~native_mask
        if np.any(missing_mask) and self._ocean_kdtree is not None:
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

        # 1. 计算 MDT (含 100km IDW 外推与 QC)
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
        block_size: int = 1024,
        allow_overwrite: bool = True,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        cancel_event: Optional[Any] = None
    ) -> DEMConversionSummary:
        """
        高分辨率 GeoTIFF 空间栅格流式垂直基准转换:
            将输入 DEM_EGM2008.tif 转换为 DEM_MSL.tif 及 conversion_qc.tif。
        """
        t0 = time.time()
        if not os.path.exists(input_dem_path):
            raise FileNotFoundError(f"未找到输入 DEM 文件: {input_dem_path}")

        if output_msl_path is None:
            base, ext = os.path.splitext(input_dem_path)
            output_msl_path = f"{base}_MSL{ext}"

        if output_qc_path is None:
            base, ext = os.path.splitext(output_msl_path)
            output_qc_path = f"{base}_conversion_qc{ext}"

        if not allow_overwrite:
            if os.path.exists(output_msl_path):
                raise FileExistsError(f"输出 DEM_MSL 文件已存在且未开启覆盖权限: {output_msl_path}")
            if os.path.exists(output_qc_path):
                raise FileExistsError(f"输出 QC 文件已存在且未开启覆盖权限: {output_qc_path}")

        out_dir = os.path.dirname(os.path.abspath(output_msl_path))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

        tmp_msl = f"{output_msl_path}.tmp.tif"
        tmp_qc = f"{output_qc_path}.tmp.tif"

        if progress_callback:
            progress_callback(5, "检查输入 DEM 几何元数据并预构建局部 MDT 空间索引...")

        with rasterio.open(input_dem_path) as src:
            w = src.width
            h = src.height
            crs = src.crs
            transform = src.transform
            nodata_val = src.nodata if src.nodata is not None else -9999.0
            is_projected = crs.is_projected if crs else False

            # 计算 WGS84 地理外包矩形
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
                b_wgs = (float(min(t_lons)), float(min(t_lats)), float(max(t_lons)), float(max(t_lats)))

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

            qc_profile = out_profile.copy()
            qc_profile.update({
                'dtype': rasterio.uint8,
                'nodata': QC_MDT_NODATA,
                'predictor': 1
            })

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
                with rasterio.open(tmp_msl, 'w', **out_profile) as dst_msl, \
                     rasterio.open(tmp_qc, 'w', **qc_profile) as dst_qc:

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

                                # 计算像元中心坐标
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
                            dst_qc.write(qc_block, 1, window=window)

                            block_counter += 1
                            if progress_callback and (block_counter % 5 == 0 or block_counter == total_blocks):
                                pct = min(95, 10 + int(85 * block_counter / total_blocks))
                                progress_callback(pct, f"流式转换中 ({block_counter}/{total_blocks} 块)...")

                # 嵌入完整科学溯源元数据 (Scientific Provenance Metadata)
                elapsed = time.time() - t0
                total_pix = w * h
                meta_tags = {
                    'SOFTWARE': 'CoastTideX v1.7',
                    'CONVERTER': 'DEMDatumConverter',
                    'DATUM': 'MSL',
                    'ANALYSIS_REFERENCE': 'MSL',
                    'SOURCE_VERTICAL_DATUM': 'EGM2008',
                    'TARGET_VERTICAL_DATUM': 'MSL',
                    'MDT_MODEL': 'CNES-CLS22',
                    'MDT_METHOD': 'bilinear_native_plus_idw_extrapolated',
                    'MAX_EXTRAPOLATION_DISTANCE_KM': str(self.max_extrapolation_distance_km),
                    'DELTAN_SOURCE': 'DatumTransformer (GOCO06s/EIGEN-6C4)',
                    'EQUATION': 'Z_MSL = Z_EGM2008 - MDT - DeltaN',
                    'SCIENTIFIC_CITATION': 'Adapted from Seeger & Minderhoud (Nature, 2026)',
                    'QC_ENCODING': 'UInt8: 0=native_mdt, 1=idw_extrapolated_within_100km, 2=nodata_or_exceeded_100km',
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
                with rasterio.open(tmp_qc, 'r+') as dst_qc_meta:
                    dst_qc_meta.update_tags(**meta_tags)

                # 原子替换到最终产物路径
                if os.path.exists(output_msl_path) and allow_overwrite:
                    os.remove(output_msl_path)
                os.replace(tmp_msl, output_msl_path)

                if os.path.exists(output_qc_path) and allow_overwrite:
                    os.remove(output_qc_path)
                os.replace(tmp_qc, output_qc_path)

            except Exception:
                if os.path.exists(tmp_msl):
                    try:
                        os.remove(tmp_msl)
                    except Exception:
                        pass
                if os.path.exists(tmp_qc):
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
            qc_output_path=output_qc_path,
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
    max_extrapolation_distance_km: float = MAX_MDT_EXTRAPOLATION_DISTANCE_KM,
    block_size: int = 1024,
    allow_overwrite: bool = True,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    cancel_event: Optional[Any] = None
) -> DEMConversionSummary:
    """
    便捷公共函数: 将 DEM_EGM2008 转换为 DEM_MSL (Nature 2026 统一基准框架)。
    """
    converter = DEMDatumConverter(
        max_extrapolation_distance_km=max_extrapolation_distance_km
    )
    return converter.convert_raster(
        input_dem_path=input_dem_path,
        output_msl_path=output_msl_path,
        output_qc_path=output_qc_path,
        block_size=block_size,
        allow_overwrite=allow_overwrite,
        progress_callback=progress_callback,
        cancel_event=cancel_event
    )
