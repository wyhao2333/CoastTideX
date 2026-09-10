"""
CoastTideX 垂直基准转换引擎 (Datum Transformation Engine)
实现从平均海平面 (MSL) 到 EGM2008 大地水准面、WGS84 椭球面的高精度无缝转换。
"""

import os
import numpy as np
import xarray as xr
import rasterio
from .utils import normalize_longitude, load_app_config


class DatumTransformer:
    """
    负责海洋与大地测量垂直基准转换的核心类。
    利用 CNES-CLS22 MDT 模型与 EGM2008 2.5分栅格模型，提供毫秒级高精度高程转换。
    """

    def __init__(self, mdt_path: str = None, egm2008_path: str = None):
        config = load_app_config()
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        if mdt_path is None:
            mdt_path = config['paths']['mdt_nc']
        if egm2008_path is None:
            egm2008_path = config['paths']['egm2008_tif']
            if not os.path.isabs(egm2008_path):
                egm2008_path = os.path.join(base_dir, egm2008_path)

        self.mdt_path = mdt_path
        self.egm2008_path = egm2008_path

        self._ds_mdt = None
        self._raster_egm = None

    def _get_mdt_dataset(self):
        """懒加载并缓存 MDT 数据集"""
        if self._ds_mdt is None:
            if not os.path.exists(self.mdt_path):
                raise FileNotFoundError(f"未找到 CNES-CLS22 MDT 文件: {self.mdt_path}")
            self._ds_mdt = xr.open_dataset(self.mdt_path)
        return self._ds_mdt

    def _get_egm2008_raster(self):
        """懒加载并缓存 EGM2008 GeoTIFF 数据集"""
        if self._raster_egm is None:
            if not os.path.exists(self.egm2008_path):
                raise FileNotFoundError(f"未找到 EGM2008 GeoTIFF 文件: {self.egm2008_path}")
            self._raster_egm = rasterio.open(self.egm2008_path)
        return self._raster_egm

    def get_mdt(self, lon: float, lat: float) -> float:
        """
        获取指定经纬度处的平均动态地形 (MDT, Mean Dynamic Topography)。

        参数:
            lon: 目标经度 (支持任意 -180~180 或 0~360)
            lat: 目标纬度 (-90~90)

        返回:
            float: MDT 高度，单位：米 (m)。若超出覆盖海域或深陆地则返回 0.0。
        """
        ds = self._get_mdt_dataset()
        # MDT 数据集的经度范围为 -180 ~ 180
        lon_norm = normalize_longitude(lon, to_360=False)
        lat_norm = float(lat)

        try:
            val = ds['mdt'].interp(longitude=lon_norm, latitude=lat_norm, method='linear').values
            val_scalar = float(np.asarray(val).squeeze())
            if np.isnan(val_scalar):
                # 若近岸边缘出现 NaN，尝试最近邻插值兜底
                val_near = ds['mdt'].interp(longitude=lon_norm, latitude=lat_norm, method='nearest').values
                val_scalar = float(np.asarray(val_near).squeeze())
            return val_scalar if not np.isnan(val_scalar) else 0.0
        except Exception as e:
            print(f"[Warning] 查询 MDT 异常 ({lon}, {lat}): {e}")
            return 0.0

    def get_geoid_undulation(self, lon: float, lat: float) -> float:
        """
        从 EGM2008 GeoTIFF 中查询指定点的大地水准面起伏 N (Geoid Undulation)。

        参数:
            lon: 目标经度
            lat: 目标纬度

        返回:
            float: 大地水准面起伏高度 N，单位：米 (m)
        """
        raster = self._get_egm2008_raster()
        lon_norm = normalize_longitude(lon, to_360=False)
        lat_norm = float(lat)

        try:
            pt = [(lon_norm, lat_norm)]
            sample_val = list(raster.sample(pt))[0][0]
            return float(sample_val) if not np.isnan(sample_val) else 0.0
        except Exception as e:
            print(f"[Warning] 查询 EGM2008 起伏异常 ({lon}, {lat}): {e}")
            return 0.0

    def convert_msl_to_egm2008(self, tide_msl_m: np.ndarray | float, lon: float, lat: float) -> tuple[np.ndarray | float, float]:
        """
        将相对于平均海平面 (MSL) 的潮位转换为 EGM2008 大地水准面基准。

        公式:
            H_EGM2008 = MDT + Tide_MSL

        参数:
            tide_msl_m: 相对于 MSL 的潮位，单位：米
            lon: 目标经度
            lat: 目标纬度

        返回:
            (tide_egm2008_m, mdt_val): 转换后的 EGM2008 潮位与使用的当地 MDT 偏置 (米)
        """
        mdt_val = self.get_mdt(lon, lat)
        return (tide_msl_m + mdt_val, mdt_val)

    def close(self):
        """释放文件句柄"""
        if self._ds_mdt is not None:
            self._ds_mdt.close()
            self._ds_mdt = None
        if self._raster_egm is not None:
            self._raster_egm.close()
            self._raster_egm = None
