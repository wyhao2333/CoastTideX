"""
CoastTideX FES2022b 潮汐解算与预测引擎 (FES Tide Engine v1.1)
基于 CNES/AVISO 官方 pyfes 库，利用原生非结构有限元网格 (LGP2) 进行高保真海岸带潮位解算。

特性:
    1. 自适应局部 BBox 空间缓存，杜绝无意义的全网格扫描；
    2. 全球批量散点自适应空间网格分块 (Spatial Chunking)，彻底解决全球离散点退化为全地球加载的性能陷阱；
    3. 严格的 UTC 时间尺度锚定与多时区自动规范化转换；
    4. 跨子午线与反子午线（0°/360° 接缝）环形安全边界防御。
"""

import os
import warnings
import numpy as np
import pandas as pd

try:
    import pyfes
    import pyfes.config as cfg
    HAS_PYFES = True
except ImportError:
    pyfes = None
    cfg = None
    HAS_PYFES = False

from .utils import normalize_longitude, convert_time_to_utc, load_app_config, resolve_project_path

# 过滤 pyfes 分潮大小写 UserWarning 提示
if HAS_PYFES:
    warnings.filterwarnings("ignore", category=UserWarning, module="pyfes")

# 34 个全分潮集合
ALL_34_CONSTITUENTS = [
    '2N2', 'Eps2', 'J1', 'K1', 'K2', 'L2', 'Lambda2', 'M2', 'M3', 'M4', 'M6', 'M8',
    'MKS2', 'MN4', 'MS4', 'MSf', 'Mf', 'Mm', 'Msqm', 'Mtm', 'Mu2', 'N2', 'N4', 'Nu2',
    'O1', 'P1', 'Q1', 'R2', 'S1', 'S2', 'S4', 'Sa', 'Ssa', 'T2'
]

# 8 大核心主分潮集合（用于快速预览）
MAJOR_8_CONSTITUENTS = ['M2', 'S2', 'K1', 'O1', 'N2', 'K2', 'P1', 'Q1']


def validate_constituents(constituents: str | list | tuple | None) -> list[str]:
    """
    统一严谨校验分潮参数，支持 'all', 'major8' 以及用户自定义分潮列表。
    若存在未知分潮名称或非法类型，抛出明确的 ValueError。
    """
    if constituents is None or constituents == 'all':
        return ALL_34_CONSTITUENTS.copy()
    if constituents == 'major8':
        return MAJOR_8_CONSTITUENTS.copy()
    if isinstance(constituents, (list, tuple, set)):
        const_list = [str(c).strip() for c in constituents]
        unknown = set(const_list) - set(ALL_34_CONSTITUENTS)
        if unknown:
            raise ValueError(f"检测到未知的 FES2022 分潮名称: {sorted(list(unknown))}。合法分潮列表: {ALL_34_CONSTITUENTS}")
        return const_list
    raise ValueError(f"无效的分潮参数: {constituents}。允许选项: 'all', 'major8', 或分潮名称列表。")


class FESTidePredictor:
    """
    FES2022b 潮位预测引擎。
    采用自适应空间包围框与全球散点空间分块聚类技术，兼备极高精度与极致解算速度。
    """

    def __init__(self, ns_grid_path: str = None):
        if not HAS_PYFES:
            raise ImportError("未找到 pyfes 模块。请确保在运行环境中已安装 CNES/AVISO pyfes 库。")

        config = load_app_config()
        self.config = config
        tide_cfg = config.get('tide', {})
        self.default_buffer = float(tide_cfg.get('spatial_buffer_deg', 1.0))
        self.default_freq = str(tide_cfg.get('default_freq', '1h'))
        self.default_constituents = str(tide_cfg.get('default_constituents', 'all'))

        if ns_grid_path is None:
            ns_grid_path = config['paths']['fes_ns_grid']

        self.ns_grid_path = resolve_project_path(ns_grid_path)

        if not os.path.exists(self.ns_grid_path):
            raise FileNotFoundError(f"未找到 FES2022b 原生非结构网格文件: {self.ns_grid_path}")

        self._cached_model = None
        self._cached_bbox = None
        self._cached_constituents = None

    def _get_model(self, bbox: tuple[float, float, float, float], constituents: list):
        """
        获取或复用符合空间包围框与分潮要求的 TidalModel 实例。
        """
        target_bbox = (
            float(bbox[0]),
            max(-90.0, float(bbox[1])),
            float(bbox[2]),
            min(90.0, float(bbox[3]))
        )

        # 检查是否可命中内存缓存（相同包围框与分潮列表）
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
        freq: str = None,
        constituents: str | list = None,
        buffer_deg: float = None,
        source_tz: str = 'UTC',
        progress_callback = None
    ) -> pd.DataFrame:
        """
        预测单点在连续时段内的潮位时序。

        参数:
            lon: 目标经度
            lat: 目标纬度 (-90~90)
            start_time: 起始时间
            end_time: 结束时间
            freq: 采样间隔 (如 '10min', '1h')，若为 None 则使用 config 默认配置
            constituents: 'all' 或 'major8' 或列表，若为 None 则使用 config 默认配置
            buffer_deg: 局部空间缓冲半径 (度)，若为 None 则使用 config 默认配置
            source_tz: 输入时间源时区 (如 'UTC' 或 'local')
            progress_callback: 进度回调 (0~100)

        返回:
            pd.DataFrame
        """
        if progress_callback:
            progress_callback(10, "解析时空参数并校准时区...")

        if freq is None:
            freq = self.default_freq
        if constituents is None:
            constituents = self.default_constituents
        if buffer_deg is None:
            buffer_deg = self.default_buffer

        const_list = validate_constituents(constituents)

        lon_norm = float(normalize_longitude(lon, to_360=True))
        lat_norm = float(lat)

        # 1. 严格的时区校准与时间网格生成
        local_series = pd.date_range(start=start_time, end=end_time, freq=freq)
        utc_idx, dates_np = convert_time_to_utc(local_series, source_tz=source_tz)

        # 2. 计算局部 BBox (允许跨越 0°/360° 环形边界，由 pyfes 自动加载环形网格拓扑)
        bbox = (
            lon_norm - buffer_deg,
            max(-90.0, lat_norm - buffer_deg),
            lon_norm + buffer_deg,
            min(90.0, lat_norm + buffer_deg)
        )

        if progress_callback:
            progress_callback(25, "加载 FES2022b 局部网格拓扑...")

        model = self._get_model(bbox, const_list)

        if progress_callback:
            progress_callback(70, "执行高精度调和潮位解算...")

        lons_np = np.full(len(dates_np), lon_norm)
        lats_np = np.full(len(dates_np), lat_norm)

        short_period, long_period, flags = pyfes.evaluate_tide(
            model, dates_np, lons_np, lats_np
        )

        total_cm = short_period + long_period
        total_m = total_cm / 100.0

        df = pd.DataFrame({
            'datetime_utc': utc_idx,
            'datetime_input': local_series,
            'longitude': lon,
            'latitude': lat,
            'tide_short_period_cm': short_period,
            'tide_long_period_cm': long_period,
            'tide_total_cm': total_cm,
            'tide_total_m': total_m,
            'quality_flag': flags
        })

        if progress_callback:
            progress_callback(100, "潮位时序解算完成")

        return df

    def predict_batch(
        self,
        df_records: pd.DataFrame,
        lon_col: str = 'longitude',
        lat_col: str = 'latitude',
        time_col: str = 'datetime',
        constituents: str | list = None,
        source_tz: str = 'UTC',
        progress_callback = None
    ) -> pd.DataFrame:
        """
        批量预测多个离散点/离散时刻的潮位。
        采用先进的空间网格分块 (Spatial Chunking) 机制，避免全地球大 BBox 退化。
        """
        if constituents is None:
            constituents = self.default_constituents
        const_list = validate_constituents(constituents)
        total_rows = len(df_records)

        lons = df_records[lon_col].astype(float).values
        lats = df_records[lat_col].astype(float).values
        lons_norm = np.array([normalize_longitude(x, to_360=True) for x in lons])

        # 严格进行时区校准转换为 UTC
        _, times_utc = convert_time_to_utc(df_records[time_col], source_tz=source_tz)

        # 检查空间跨度是否集中在小区域
        lon_span = np.ptp(lons_norm)
        lat_span = np.ptp(lats)

        df_out = df_records.copy()
        df_out['datetime_utc'] = pd.to_datetime(times_utc)
        df_out['tide_short_period_cm'] = np.nan
        df_out['tide_long_period_cm'] = np.nan
        df_out['tide_total_cm'] = np.nan
        df_out['tide_total_m'] = np.nan
        df_out['quality_flag'] = 0

        # 如果所有点集中在 8°x8° 范围之内，直接使用单局部 BBox 计算
        if lon_span <= 8.0 and lat_span <= 8.0:
            if progress_callback:
                progress_callback(20, f"单局部区域批量解算 ({total_rows} 个点)...")

            bbox = (
                float(np.min(lons_norm)) - 0.5,
                max(-90.0, float(np.min(lats)) - 0.5),
                float(np.max(lons_norm)) + 0.5,
                min(90.0, float(np.max(lats)) + 0.5)
            )
            model = self._get_model(bbox, const_list)
            sp, lp, flags = pyfes.evaluate_tide(model, times_utc, lons_norm, lats)

            df_out['tide_short_period_cm'] = sp
            df_out['tide_long_period_cm'] = lp
            df_out['tide_total_cm'] = sp + lp
            df_out['tide_total_m'] = (sp + lp) / 100.0
            df_out['quality_flag'] = flags

        else:
            # 空间离散分布：采用 5°x5° 空间网格聚类分块 (Spatial Chunking)
            if progress_callback:
                progress_callback(15, f"执行空间分块聚类 ({total_rows} 个全球离散点)...")

            chunk_size = 5.0  # 5 度网格分块
            grid_keys = np.floor(lons_norm / chunk_size).astype(int) * 1000 + np.floor((lats + 90.0) / chunk_size).astype(int)
            unique_chunks = np.unique(grid_keys)

            num_chunks = len(unique_chunks)
            if progress_callback:
                progress_callback(25, f"划分为 {num_chunks} 个空间子集串行分块解算...")

            for i, chunk_id in enumerate(unique_chunks):
                mask = (grid_keys == chunk_id)
                chunk_lons = lons_norm[mask]
                chunk_lats = lats[mask]
                chunk_times = times_utc[mask]

                chunk_bbox = (
                    float(np.min(chunk_lons)) - 0.5,
                    max(-90.0, float(np.min(chunk_lats)) - 0.5),
                    float(np.max(chunk_lons)) + 0.5,
                    min(90.0, float(np.max(chunk_lats)) + 0.5)
                )

                model = self._get_model(chunk_bbox, const_list)
                sp, lp, flags = pyfes.evaluate_tide(model, chunk_times, chunk_lons, chunk_lats)

                df_out.loc[mask, 'tide_short_period_cm'] = sp
                df_out.loc[mask, 'tide_long_period_cm'] = lp
                df_out.loc[mask, 'tide_total_cm'] = sp + lp
                df_out.loc[mask, 'tide_total_m'] = (sp + lp) / 100.0
                df_out.loc[mask, 'quality_flag'] = flags

                if progress_callback:
                    pct = int(25 + 70 * (i + 1) / num_chunks)
                    progress_callback(pct, f"正在解算分块 {i+1}/{num_chunks}...")

        if progress_callback:
            progress_callback(100, "批量潮位解算完成")

        return df_out
