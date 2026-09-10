"""
CoastTideX 垂直基准转换引擎 (Datum Transformation Engine v1.1)
实现从平均海平面 (MSL) 到 GOCO06s (MDT参考基准)、EGM2008 大地水准面及 WGS84 空间几何椭球面的严密科学转换。

科学转换原理:
    1. H_GOCO06S = Tide_MSL + MDT_CLS22
    2. H_EGM2008 = Tide_MSL + MDT_CLS22 + Delta_N
       其中 Delta_N = N_GOCO06s - N_EGM2008 (大地水准面差值改正项)
    3. h_WGS84   = H_EGM2008 + N_EGM2008 = Tide_MSL + MDT_CLS22 + N_GOCO06S
"""

import os
import numpy as np
import xarray as xr
import rasterio
from scipy.interpolate import RegularGridInterpolator
from scipy.ndimage import map_coordinates

from .utils import normalize_longitude, load_app_config, resolve_project_path


class DatumTransformer:
    """
    严密的海洋与大地测量垂直基准转换器。
    全面支持向量化并行插值、真双线性空间插值与严格的 NaN 状态传播。
    """

    def __init__(
        self,
        mdt_path: str = None,
        egm2008_path: str = None,
        delta_n_path: str = None
    ):
        config = load_app_config()

        if mdt_path is None:
            mdt_path = config['paths'].get('mdt_nc')
        if egm2008_path is None:
            egm2008_path = config['paths'].get('egm2008_tif')
        if delta_n_path is None:
            delta_n_path = config['paths'].get('delta_n_tif')

        self.mdt_path = resolve_project_path(mdt_path)
        self.egm2008_path = resolve_project_path(egm2008_path)
        self.delta_n_path = resolve_project_path(delta_n_path)

        # 懒加载缓存对象
        self._mdt_interpolator = None
        self._egm_data = None
        self._egm_inv_transform = None
        self._delta_n_data = None
        self._delta_n_inv_transform = None

    def _init_mdt(self):
        """初始化 MDT 全局向量化插值器"""
        if self._mdt_interpolator is None:
            if not os.path.exists(self.mdt_path):
                raise FileNotFoundError(f"未找到 CNES-CLS22 MDT 文件: {self.mdt_path}")
            ds = xr.open_dataset(self.mdt_path)
            lats = ds.latitude.values
            lons = ds.longitude.values
            mdt_grid = ds.mdt.values[0]  # shape: (1440, 2880)
            ds.close()

            # 建立 RegularGridInterpolator，禁止非法外推，无效区返回 NaN
            self._mdt_interpolator = RegularGridInterpolator(
                (lats, lons), mdt_grid, bounds_error=False, fill_value=np.nan
            )

    def _init_egm2008(self):
        """加载 EGM2008 GeoTIFF 栅格与仿射变换"""
        if self._egm_data is None:
            if not os.path.exists(self.egm2008_path):
                raise FileNotFoundError(f"未找到 EGM2008 栅格文件: {self.egm2008_path}")
            with rasterio.open(self.egm2008_path) as src:
                self._egm_data = src.read(1).astype(np.float32)
                self._egm_inv_transform = ~src.transform

    def _init_delta_n(self):
        """加载 Delta N (GOCO06s - EGM2008) 差值栅格与仿射变换"""
        if self._delta_n_data is None:
            if not os.path.exists(self.delta_n_path):
                raise FileNotFoundError(f"未找到 Delta N 差值栅格文件: {self.delta_n_path}")
            with rasterio.open(self.delta_n_path) as src:
                self._delta_n_data = src.read(1).astype(np.float32)
                self._delta_n_inv_transform = ~src.transform

    def get_mdt(self, lons: float | np.ndarray, lats: float | np.ndarray) -> float | np.ndarray:
        """
        获取指定经纬度处的 CNES-CLS22 MDT 值 (单位: 米)。
        若落在陆地或无数据区，严格返回 np.nan。
        """
        self._init_mdt()
        is_scalar = np.isscalar(lons)
        lons_arr = np.atleast_1d(normalize_longitude(lons, to_360=False))
        lats_arr = np.atleast_1d(np.asarray(lats, dtype=float))

        points = np.column_stack([lats_arr, lons_arr])
        res = self._mdt_interpolator(points)
        return float(res[0]) if is_scalar else res

    def get_egm2008_undulation(self, lons: float | np.ndarray, lats: float | np.ndarray) -> float | np.ndarray:
        """
        通过双线性插值 (Bilinear Interpolation) 获取 EGM2008 大地水准面起伏 N (单位: 米)。
        """
        self._init_egm2008()
        is_scalar = np.isscalar(lons)
        lons_arr = np.atleast_1d(normalize_longitude(lons, to_360=False))
        lats_arr = np.atleast_1d(np.asarray(lats, dtype=float))

        cols, rows = self._egm_inv_transform * (lons_arr, lats_arr)
        # order=1 对应双线性插值 (Bilinear Interpolation)
        vals = map_coordinates(self._egm_data, [rows, cols], order=1, mode='constant', cval=np.nan)
        return float(vals[0]) if is_scalar else vals

    def get_delta_n(self, lons: float | np.ndarray, lats: float | np.ndarray) -> float | np.ndarray:
        """
        通过双线性插值获取 GOCO06s 与 EGM2008 大地水准面差值 Delta N = N_GOCO06s - N_EGM2008 (单位: 米)。
        """
        self._init_delta_n()
        is_scalar = np.isscalar(lons)
        lons_arr = np.atleast_1d(normalize_longitude(lons, to_360=False))
        lats_arr = np.atleast_1d(np.asarray(lats, dtype=float))

        cols, rows = self._delta_n_inv_transform * (lons_arr, lats_arr)
        vals = map_coordinates(self._delta_n_data, [rows, cols], order=1, mode='constant', cval=np.nan)
        return float(vals[0]) if is_scalar else vals

    def convert_tide_datums(
        self,
        tide_msl_m: float | np.ndarray,
        lons: float | np.ndarray,
        lats: float | np.ndarray
    ) -> dict:
        """
        严密统一计算所有基准面的高程体系（全面向量化并行执行）。

        返回字典包含:
            'tide_msl_m': 相对局部平均海平面的纯潮汐起伏 (m)
            'mdt_m': CNES-CLS22 平均动态地形偏置 (m)
            'delta_n_m': GOCO06s 与 EGM2008 大地水准面差值改正项 (m)
            'n_egm2008_m': EGM2008 大地水准面起伏 N (m)
            'h_goco06s_m': 相对 GOCO06s 基准面的海面高 (m) = Tide + MDT
            'h_egm2008_m': 相对 EGM2008 大地水准面的正高 (m) = Tide + MDT + Delta_N
            'h_wgs84_m': WGS84 几何空间椭球高 (m) = H_EGM2008 + N_EGM2008
        """
        is_scalar = np.isscalar(tide_msl_m) and np.isscalar(lons) and np.isscalar(lats)
        t_arr = np.atleast_1d(np.asarray(tide_msl_m, dtype=float))
        lons_arr = np.atleast_1d(normalize_longitude(lons, to_360=False))
        lats_arr = np.atleast_1d(np.asarray(lats, dtype=float))

        # 1. 向量化提取各大地测量参数
        mdt_vals = np.atleast_1d(self.get_mdt(lons_arr, lats_arr))
        delta_n_vals = np.atleast_1d(self.get_delta_n(lons_arr, lats_arr))
        n_egm_vals = np.atleast_1d(self.get_egm2008_undulation(lons_arr, lats_arr))

        # 2. 自动对齐广播维度 (解决单点时序预测中经纬度为 1 个点、潮位有时序 N 个点的不匹配问题)
        t_arr, mdt_vals, delta_n_vals, n_egm_vals = np.broadcast_arrays(
            t_arr, mdt_vals, delta_n_vals, n_egm_vals
        )

        # 3. 严格的科学级基准转换运算
        # H_GOCO06S = Tide + MDT
        h_goco06s = t_arr + mdt_vals

        # H_EGM2008 = Tide + MDT + Delta_N
        h_egm2008 = h_goco06s + delta_n_vals

        # h_WGS84 = H_EGM2008 + N_EGM2008
        h_wgs84 = h_egm2008 + n_egm_vals

        if is_scalar:
            return {
                'tide_msl_m': float(t_arr[0]),
                'mdt_m': float(mdt_vals[0]) if not np.isnan(mdt_vals[0]) else np.nan,
                'delta_n_m': float(delta_n_vals[0]) if not np.isnan(delta_n_vals[0]) else np.nan,
                'n_egm2008_m': float(n_egm_vals[0]) if not np.isnan(n_egm_vals[0]) else np.nan,
                'h_goco06s_m': float(h_goco06s[0]) if not np.isnan(h_goco06s[0]) else np.nan,
                'h_egm2008_m': float(h_egm2008[0]) if not np.isnan(h_egm2008[0]) else np.nan,
                'h_wgs84_m': float(h_wgs84[0]) if not np.isnan(h_wgs84[0]) else np.nan,
            }

        return {
            'tide_msl_m': t_arr,
            'mdt_m': mdt_vals,
            'delta_n_m': delta_n_vals,
            'n_egm2008_m': n_egm_vals,
            'h_goco06s_m': h_goco06s,
            'h_egm2008_m': h_egm2008,
            'h_wgs84_m': h_wgs84,
        }
