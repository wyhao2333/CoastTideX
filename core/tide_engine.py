"""
CoastTideX FES2022b 潮汐解算与预测引擎 (FES Tide Engine)
基于 CNES/AVISO 官方 pyfes 库，利用原生非结构有限元网格 (LGP2) 进行极高精度的海岸带潮位解算。
"""

import os
import warnings
import numpy as np
import pandas as pd
import pyfes
import pyfes.config as cfg

from .utils import normalize_longitude, load_app_config

# 过滤 pyfes 分潮大小写警告
warnings.filterwarnings("ignore", category=UserWarning, module="pyfes")

# 34 个全分潮集合
ALL_34_CONSTITUENTS = [
    '2N2', 'Eps2', 'J1', 'K1', 'K2', 'L2', 'Lambda2', 'M2', 'M3', 'M4', 'M6', 'M8',
    'MKS2', 'MN4', 'MS4', 'MSf', 'Mf', 'Mm', 'Msqm', 'Mtm', 'Mu2', 'N2', 'N4', 'Nu2',
    'O1', 'P1', 'Q1', 'R2', 'S1', 'S2', 'S4', 'Sa', 'Ssa', 'T2'
]

# 8 大核心主分潮集合（用于快速预览）
MAJOR_8_CONSTITUENTS = ['M2', 'S2', 'K1', 'O1', 'N2', 'K2', 'P1', 'Q1']


class FESTidePredictor:
    """
    FES2022b 潮位预测引擎。
    采用自适应空间包围框 (Bounding Box) 局部快速索引，兼备极高精度与秒级解算性能。
    """

    def __init__(self, ns_grid_path: str = None):
        config = load_app_config()
        if ns_grid_path is None:
            ns_grid_path = config['paths']['fes_ns_grid']

        if not os.path.exists(ns_grid_path):
            raise FileNotFoundError(f"未找到 FES2022b 原生非结构网格文件: {ns_grid_path}")

        self.ns_grid_path = ns_grid_path
        self._cached_model = None
        self._cached_bbox = None
        self._cached_constituents = None

    def _get_model(self, lon_norm: float, lat: float, constituents: list, buffer_deg: float):
        """
        获取或复用符合空间范围和分潮要求的 TidalModel 实例。
        """
        target_bbox = (
            max(0.0, lon_norm - buffer_deg),
            max(-90.0, lat - buffer_deg),
            min(360.0, lon_norm + buffer_deg),
            min(90.0, lat + buffer_deg)
        )

        # 检查是否可命中缓存（相同包围框与分潮列表）
        if (self._cached_model is not None and
            self._cached_bbox == target_bbox and
            self._cached_constituents == sorted(constituents)):
            return self._cached_model

        lgp_config = cfg.LGP(
            path=self.ns_grid_path,
            type='lgp2',
            codes='lgp2',
            constituents=constituents,
            bbox=target_bbox
        )
        model = lgp_config.load()

        # 更新缓存
        self._cached_model = model
        self._cached_bbox = target_bbox
        self._cached_constituents = sorted(constituents)
        return model

    def predict_series(
        self,
        lon: float,
        lat: float,
        start_time: str | pd.Timestamp,
        end_time: str | pd.Timestamp,
        freq: str = '1h',
        constituents: str | list = 'all',
        buffer_deg: float = 1.0,
        progress_callback = None
    ) -> pd.DataFrame:
        """
        预测单点在连续时间段内的潮位时间序列。

        参数:
            lon: 目标经度 (支持 -180~180 或 0~360)
            lat: 目标纬度 (-90~90)
            start_time: 起始时间 (如 '2026-09-10 00:00:00')
            end_time: 结束时间 (如 '2026-09-11 00:00:00')
            freq: 时间间隔步长 (如 '10min', '30min', '1h')
            constituents: 'all' 或 'major8' 或具体分潮名称列表
            buffer_deg: 局部包围框缓冲范围 (度)
            progress_callback: 进度回调函数，用于 GUI 进度条更新

        返回:
            pd.DataFrame: 包含时间、经纬度、短周期分潮、长周期潮、总潮位(cm/m)及 Flag
        """
        if progress_callback:
            progress_callback(10, "解析预测时空参数...")

        lon_norm = normalize_longitude(lon, to_360=True)
        lat_norm = float(lat)

        if constituents == 'all':
            const_list = ALL_34_CONSTITUENTS
        elif constituents == 'major8':
            const_list = MAJOR_8_CONSTITUENTS
        elif isinstance(constituents, list):
            const_list = constituents
        else:
            const_list = ALL_34_CONSTITUENTS

        if progress_callback:
            progress_callback(25, "加载 FES2022b 局部网格...")

        model = self._get_model(lon_norm, lat_norm, const_list, buffer_deg)

        if progress_callback:
            progress_callback(60, "生成预测时间网格...")

        time_series = pd.date_range(start=start_time, end=end_time, freq=freq)
        dates_np = time_series.to_numpy(dtype='datetime64[us]')
        lons_np = np.full(len(dates_np), lon_norm)
        lats_np = np.full(len(dates_np), lat_norm)

        if progress_callback:
            progress_callback(80, "执行高精度调和潮位解算...")

        short_period, long_period, flags = pyfes.evaluate_tide(
            model, dates_np, lons_np, lats_np
        )

        total_cm = short_period + long_period
        total_m = total_cm / 100.0

        df = pd.DataFrame({
            'datetime': time_series,
            'longitude': lon,
            'latitude': lat,
            'tide_short_period_cm': short_period,
            'tide_long_period_cm': long_period,
            'tide_total_cm': total_cm,
            'tide_total_m': total_m,
            'quality_flag': flags
        })

        if progress_callback:
            progress_callback(100, "潮位预测完成")

        return df

    def predict_batch(
        self,
        df_records: pd.DataFrame,
        lon_col: str = 'longitude',
        lat_col: str = 'latitude',
        time_col: str = 'datetime',
        constituents: str = 'all',
        progress_callback = None
    ) -> pd.DataFrame:
        """
        批量预测多个离散点/离散时刻的潮位。

        参数:
            df_records: 包含经度、纬度、时间的 DataFrame
            lon_col: 经度列名
            lat_col: 纬度列名
            time_col: 时间列名
            progress_callback: 进度回调 (0~100)

        返回:
            追加了潮位预测结果的 DataFrame
        """
        const_list = ALL_34_CONSTITUENTS if constituents == 'all' else MAJOR_8_CONSTITUENTS
        total_rows = len(df_records)

        # 转换为统一的 numpy 数组
        lons = df_records[lon_col].astype(float).values
        lats = df_records[lat_col].astype(float).values
        times = pd.to_datetime(df_records[time_col]).to_numpy(dtype='datetime64[us]')

        # 规范化经度
        lons_norm = np.array([normalize_longitude(x, to_360=True) for x in lons])

        # 获取全局包围框
        min_lon = max(0.0, float(np.min(lons_norm)) - 0.5)
        max_lon = min(360.0, float(np.max(lons_norm)) + 0.5)
        min_lat = max(-90.0, float(np.min(lats)) - 0.5)
        max_lat = min(90.0, float(np.max(lats)) + 0.5)

        bbox = (min_lon, min_lat, max_lon, max_lat)

        if progress_callback:
            progress_callback(20, f"加载批量空间区域网格 ({total_rows} 个点)...")

        lgp_config = cfg.LGP(
            path=self.ns_grid_path,
            type='lgp2',
            codes='lgp2',
            constituents=const_list,
            bbox=bbox
        )
        model = lgp_config.load()

        if progress_callback:
            progress_callback(60, "批量解算潮位...")

        short_period, long_period, flags = pyfes.evaluate_tide(
            model, times, lons_norm, lats
        )

        df_out = df_records.copy()
        df_out['tide_short_period_cm'] = short_period
        df_out['tide_long_period_cm'] = long_period
        df_out['tide_total_cm'] = short_period + long_period
        df_out['tide_total_m'] = (short_period + long_period) / 100.0
        df_out['quality_flag'] = flags

        if progress_callback:
            progress_callback(100, "批量计算完成")

        return df_out
