"""
CoastTideX FES2022b 潮汐解算与预测引擎 (FES Tide Engine)
基于 CNES/AVISO 官方 pyfes 库，利用原生非结构有限元网格 (LGP2) 进行高保真海岸带潮位解算。

特性:
    1. 自适应局部 BBox 空间缓存，杜绝无意义的全网格扫描；
    2. 全球批量散点自适应空间网格分块 (Spatial Chunking)，彻底解决全球离散点退化为全地球加载的性能陷阱；
    3. 长时间序列自适应时间分块（Time-Chunking）流式解算，保障年际与高密度时序计算平稳及实时进度反馈；
    4. 整年高分辨率预测（predict_year）半开区间严格点数保真（2024 闰年精确生成 17,568 样本点）；
    5. 严格的 UTC 时间尺度锚定与多时区自动规范化转换；
    6. 跨子午线与反子午线（0°/360° 接缝）环形安全边界防御。
"""

import os
import warnings
from typing import Optional, Tuple, List, Dict, Any, Callable, Union
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

from .utils import (
    normalize_longitude, circular_longitude_span, build_circular_fes_bboxes, convert_time_to_utc,
    load_app_config, resolve_project_path, validate_coordinates, validate_time_params, build_time_index
)
from .datum_engine import DatumTransformer, DatumDataError, get_mdt_reference_geoid

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
        inclusive: str = 'both',
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
            inclusive: 区间包含语义: 'both' (默认闭区间 [start, end]), 'left' ([start, end)), 'right' ((start, end]), 'neither'
            constituents: 'all' 或 'major8' 或列表，若为 None 则使用 config 默认配置
            buffer_deg: 局部空间缓冲半径 (度)，若为 None 则使用 config 默认配置
            source_tz: 输入时间源时区 (如 'UTC' 或 'local')
            progress_callback: 进度回调 (0~100)

        返回:
            pd.DataFrame
        """
        if progress_callback:
            progress_callback(10, "解析时空参数并校准时区...")

        lon, lat = validate_coordinates(lon, lat)

        if freq is None:
            freq = self.default_freq
        if constituents is None:
            constituents = self.default_constituents
        if buffer_deg is None:
            buffer_deg = self.default_buffer

        validate_time_params(start_time, end_time, freq)
        const_list = validate_constituents(constituents)

        lon_norm = float(normalize_longitude(lon, to_360=True))
        lat_norm = float(lat)

        # 1. 严格的时区校准与时间网格生成 (利用 build_time_index 防御 DST 跳变并支持 inclusive 语义)
        local_series, utc_idx, dates_np = build_time_index(
            start_time=start_time,
            end_time=end_time,
            freq=freq,
            source_tz=source_tz,
            inclusive=inclusive
        )

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

        chunk_size = 5000
        n_dates = len(dates_np)

        if n_dates <= chunk_size:
            if progress_callback:
                progress_callback(70, "执行高精度调和潮位解算...")
            lons_np = np.full(n_dates, lon_norm)
            lats_np = np.full(n_dates, lat_norm)
            short_period, long_period, flags = pyfes.evaluate_tide(
                model, dates_np, lons_np, lats_np
            )
        else:
            short_list, long_list, flag_list = [], [], []
            n_chunks = int(np.ceil(n_dates / chunk_size))
            for i in range(n_chunks):
                idx_s = i * chunk_size
                idx_e = min(idx_s + chunk_size, n_dates)
                sub_dates = dates_np[idx_s:idx_e]
                sub_lons = np.full(len(sub_dates), lon_norm)
                sub_lats = np.full(len(sub_dates), lat_norm)
                if progress_callback:
                    pct = 30 + int(45 * (i + 1) / n_chunks)
                    progress_callback(pct, f"执行调和潮位解算 (时间分块 {i+1}/{n_chunks}, {len(sub_dates)}点)...")
                sp, lp, fl = pyfes.evaluate_tide(model, sub_dates, sub_lons, sub_lats)
                short_list.append(sp)
                long_list.append(lp)
                flag_list.append(fl)
            short_period = np.concatenate(short_list)
            long_period = np.concatenate(long_list)
            flags = np.concatenate(flag_list)

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

    def predict_point_period(
        self,
        lon: float,
        lat: float,
        start_time: str | pd.Timestamp,
        end_time: str | pd.Timestamp,
        freq: str = '30min',
        inclusive: str = 'both',
        constituents: str | list = None,
        buffer_deg: float = None,
        source_tz: str = 'UTC',
        datum_mode: str = 'both',
        strict: bool = False,
        datum_transformer: DatumTransformer = None,
        progress_callback = None
    ) -> pd.DataFrame:
        """
        单点连续时段潮位预测并联合多元垂直基准转换。
        可一键输出 Tide(MSL)、MDT、h_mdt_ref、h_goco06s、h_egm2008、h_wgs84 等完整基准序列。
        """
        df = self.predict_series(
            lon=lon,
            lat=lat,
            start_time=start_time,
            end_time=end_time,
            freq=freq,
            inclusive=inclusive,
            constituents=constituents,
            buffer_deg=buffer_deg,
            source_tz=source_tz,
            progress_callback=progress_callback
        )

        if datum_mode is not None:
            if datum_transformer is None:
                datum_transformer = DatumTransformer()
            datums = datum_transformer.convert_tide_datums(
                tide_msl_m=df['tide_total_m'].values,
                lons=lon,
                lats=lat,
                datum_target=datum_mode,
                strict=strict
            )
            df['tide_msl_m'] = datums['tide_msl_m']
            df['mdt_m'] = datums['mdt_m']
            df['delta_n_m'] = datums['delta_n_m']
            df['n_egm2008_m'] = datums['n_egm2008_m']
            df['h_mdt_ref_m'] = datums['h_mdt_ref_m']
            df['h_goco06s_m'] = datums['h_goco06s_m']
            df['h_egm2008_m'] = datums['h_egm2008_m']
            df['h_wgs84_m'] = datums['h_wgs84_m']
            df['datum_ref_geoid'] = datums['datum_ref_geoid']
            df['qc_warning'] = datums['qc_warning']

        return df

    def predict_year(
        self,
        lon: float,
        lat: float,
        year: int = 2024,
        freq: str = '30min',
        inclusive: str = 'left',
        constituents: str | list = None,
        buffer_deg: float = None,
        source_tz: str = 'UTC',
        datum_mode: str = 'both',
        strict: bool = False,
        datum_transformer: DatumTransformer = None,
        progress_callback = None
    ) -> pd.DataFrame:
        """
        单点整年潮位高分辨率预测。
        默认采用严密半开区间 [year-01-01 00:00:00, (year+1)-01-01 00:00:00)。
        对于 2024 闰年 (366 天)，30min 采样率严格输出 366 * 48 = 17,568 行。
        """
        start_time = f"{int(year):04d}-01-01 00:00:00"
        end_time = f"{int(year) + 1:04d}-01-01 00:00:00"
        return self.predict_point_period(
            lon=lon,
            lat=lat,
            start_time=start_time,
            end_time=end_time,
            freq=freq,
            inclusive=inclusive,
            constituents=constituents,
            buffer_deg=buffer_deg,
            source_tz=source_tz,
            datum_mode=datum_mode,
            strict=strict,
            datum_transformer=datum_transformer,
            progress_callback=progress_callback
        )

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

        # 检查并校验坐标
        for x, y in zip(lons, lats):
            validate_coordinates(x, y)

        lons_norm = np.array([normalize_longitude(x, to_360=True) for x in lons])

        # 严格进行时区校准转换为 UTC
        _, times_utc = convert_time_to_utc(df_records[time_col], source_tz=source_tz)

        # 检查空间跨度是否集中在小区域 (环形圆周感知)
        lon_span, _, _ = circular_longitude_span(lons_norm) if len(lons_norm) > 1 else (0.0, 0.0, 0.0)
        lat_span = np.ptp(lats) if len(lats) > 1 else 0.0

        df_out = df_records.copy()
        df_out['datetime_utc'] = pd.to_datetime(times_utc)
        df_out['tide_short_period_cm'] = np.nan
        df_out['tide_long_period_cm'] = np.nan
        df_out['tide_total_cm'] = np.nan
        df_out['tide_total_m'] = np.nan
        df_out['quality_flag'] = 0

        # 获取质量控制提示与基准来源 Provenance
        _, prov_res = get_mdt_reference_geoid(lons, lats, return_qc=True)
        df_out['qc_warning'] = prov_res

        # 如果所有点集中在 8°x8° 范围之内，直接使用环形感知局部 BBox 计算
        if lon_span <= 8.0 and lat_span <= 8.0:
            if progress_callback:
                progress_callback(20, f"单局部区域批量解算 ({total_rows} 个点)...")

            bboxes = build_circular_fes_bboxes(lons_norm, lats, buffer_deg=0.5)
            if len(bboxes) == 1:
                model = self._get_model(bboxes[0], const_list)
                sp, lp, flags = pyfes.evaluate_tide(model, times_utc, lons_norm, lats)
            else:
                sp = np.full(total_rows, np.nan, dtype=np.float32)
                lp = np.full(total_rows, np.nan, dtype=np.float32)
                flags = np.zeros(total_rows, dtype=np.int8)
                for cur_box in bboxes:
                    cur_mask = (lons_norm >= cur_box[0] - 1e-6) & (lons_norm <= cur_box[2] + 1e-6)
                    if np.any(cur_mask):
                        cur_model = self._get_model(cur_box, const_list)
                        s_sub, l_sub, f_sub = pyfes.evaluate_tide(cur_model, times_utc[cur_mask], lons_norm[cur_mask], lats[cur_mask])
                        sp[cur_mask] = s_sub
                        lp[cur_mask] = l_sub
                        flags[cur_mask] = f_sub

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

    def predict_points_period(
        self,
        lons: float | np.ndarray,
        lats: float | np.ndarray,
        start_time: str | pd.Timestamp,
        end_time: str | pd.Timestamp,
        freq: str = "30min",
        inclusive: str = "both",
        constituents: str | list = None,
        source_tz: str = "UTC",
        max_fes_evaluate_points: int = 500000,
        progress_callback = None
    ) -> tuple[np.ndarray, pd.DatetimeIndex, np.ndarray]:
        """
        多空间控制点 (M) × 多时间步 (T) 联合高效潮位预测。
        针对空间网格与大范围控制点解算，在内存中复用局部网格模型，
        并采用 (M_chunk × T_chunk) 批量展平分块解算，防止一次性内存溢出。

        参数:
            lons: 经度数组 (长度 M)
            lats: 纬度数组 (长度 M)
            start_time: 起始时间
            end_time: 结束时间
            freq: 采样间隔 (如 '30min')
            inclusive: 'both', 'left', 'right', 'neither'
            constituents: 分潮集合 ('all', 'major8' 或列表)
            source_tz: 时区
            max_fes_evaluate_points: 单次调用 pyfes.evaluate_tide 的最大点数 (默认 500,000)
            progress_callback: 进度回调 (0~100)

        返回:
            (tide_matrix_m, utc_time_index, quality_flags_matrix)
            tide_matrix_m: shape 为 (M, T) 的浮点数矩阵，单位为米
            utc_time_index: 长度为 T 的 UTC 时间索引
            quality_flags_matrix: shape 为 (M, T) 的质量标志矩阵
        """
        lons_arr = np.atleast_1d(np.asarray(lons, dtype=float))
        lats_arr = np.atleast_1d(np.asarray(lats, dtype=float))

        if len(lons_arr) != len(lats_arr):
            raise ValueError(f"经纬度数组长度不一致: len(lon)={len(lons_arr)}, len(lat)={len(lats_arr)}")

        for x, y in zip(lons_arr, lats_arr):
            validate_coordinates(x, y)

        if constituents is None:
            constituents = self.default_constituents
        const_list = validate_constituents(constituents)

        validate_time_params(start_time, end_time, freq)
        _, utc_idx, dates_np = build_time_index(
            start_time=start_time,
            end_time=end_time,
            freq=freq,
            source_tz=source_tz,
            inclusive=inclusive
        )

        n_pts = len(lons_arr)
        n_times = len(dates_np)

        lons_norm = np.array([normalize_longitude(x, to_360=True) for x in lons_arr], dtype=float)

        tide_matrix = np.full((n_pts, n_times), np.nan, dtype=np.float32)
        flag_matrix = np.zeros((n_pts, n_times), dtype=np.int8)

        # 检查空间跨度是否集中在局部 (环形圆周感知)
        lon_span, _, _ = circular_longitude_span(lons_norm) if n_pts > 1 else (0.0, 0.0, 0.0)
        lat_span = np.ptp(lats_arr) if n_pts > 1 else 0.0

        if lon_span <= 8.0 and lat_span <= 8.0:
            chunks = [np.arange(n_pts)]
        else:
            chunk_size = 5.0
            grid_keys = np.floor(lons_norm / chunk_size).astype(int) * 1000 + np.floor((lats_arr + 90.0) / chunk_size).astype(int)
            unique_chunks = np.unique(grid_keys)
            chunks = [np.where(grid_keys == cid)[0] for cid in unique_chunks]

        total_chunks = len(chunks)
        for c_idx, point_indices in enumerate(chunks):
            sub_lons = lons_norm[point_indices]
            sub_lats = lats_arr[point_indices]

            # 环形感知局部 BBox 划分 (消除 0°/360° 跨界造成的近全球大 BBox)
            bboxes = build_circular_fes_bboxes(sub_lons, sub_lats, buffer_deg=0.5)

            for cur_bbox in bboxes:
                in_box = (sub_lons >= cur_bbox[0] - 1e-6) & (sub_lons <= cur_bbox[2] + 1e-6)
                if not np.any(in_box):
                    continue

                sub_box_lons = sub_lons[in_box]
                sub_box_lats = sub_lats[in_box]
                m_sub_box = len(sub_box_lons)
                global_sub_indices = point_indices[in_box]

                model = self._get_model(cur_bbox, const_list)

                # 时间分块：使得 m_sub_box * t_chunk_len <= max_fes_evaluate_points
                max_eval = max(10000, int(max_fes_evaluate_points))
                t_chunk_len = max(1, max_eval // m_sub_box)
                n_t_chunks = int(np.ceil(n_times / t_chunk_len))

                for tc in range(n_t_chunks):
                    t_s = tc * t_chunk_len
                    t_e = min(t_s + t_chunk_len, n_times)
                    sub_dates = dates_np[t_s:t_e]
                    cur_t_len = len(sub_dates)

                    rep_lons = np.repeat(sub_box_lons, cur_t_len)
                    rep_lats = np.repeat(sub_box_lats, cur_t_len)
                    rep_dates = np.tile(sub_dates, m_sub_box)

                    sp, lp, flags = pyfes.evaluate_tide(model, rep_dates, rep_lons, rep_lats)
                    tot_m = (sp + lp) / 100.0

                    tide_matrix[global_sub_indices, t_s:t_e] = tot_m.reshape(m_sub_box, cur_t_len)
                    flag_matrix[global_sub_indices, t_s:t_e] = flags.reshape(m_sub_box, cur_t_len)

            if progress_callback:
                pct = int(10 + 85 * (c_idx + 1) / total_chunks)
                progress_callback(pct, f"完成空间控制块 {c_idx+1}/{total_chunks} 时空潮汐解算...")

        return tide_matrix, utc_idx, flag_matrix

    def predict_spatial_snapshot(
        self,
        lons: float | np.ndarray | list,
        lats: float | np.ndarray | list,
        timestamp: str | pd.Timestamp,
        constituents: str | list = None,
        source_tz: str = 'UTC',
        buffer_deg: float = None
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        对指定单时刻在多个空间经纬度位置执行高精度瞬时潮位解算。

        参数:
            lons: 经度数组 (长度 N)
            lats: 纬度数组 (长度 N)
            timestamp: 快照时刻 (支持字符串或 Timestamp)
            constituents: 分潮方案 ('all', 'major8' 或列表)
            source_tz: 输入时间源时区 (如 'UTC' 或 'local')
            buffer_deg: 局部空间缓冲半径 (度)

        返回:
            (tide_total_m, quality_flags)
            tide_total_m: 长度为 N 的潮位高度 (米)
            quality_flags: 长度为 N 的质量标志数组
        """
        if not HAS_PYFES:
            raise RuntimeError("当前环境未安装或无法加载 pyfes 运行库，无法执行空间快照解算。")

        lons_arr = np.atleast_1d(np.asarray(lons, dtype=float))
        lats_arr = np.atleast_1d(np.asarray(lats, dtype=float))
        n_pts = len(lons_arr)
        if len(lats_arr) != n_pts:
            raise ValueError(f"经纬度数组长度不一致: len(lon)={len(lons_arr)}, len(lat)={len(lats_arr)}")

        if n_pts == 0:
            return np.array([], dtype=np.float32), np.array([], dtype=np.int8)

        for x, y in zip(lons_arr, lats_arr):
            validate_coordinates(x, y)

        if constituents is None:
            constituents = self.default_constituents
        const_list = validate_constituents(constituents)

        buf = buffer_deg if buffer_deg is not None else self.default_buffer

        # 解析与对齐 UTC 时间
        _, dates_np = convert_time_to_utc(timestamp, source_tz=source_tz)
        ts_utc_us = dates_np[0]

        lons_norm = np.array([normalize_longitude(x, to_360=True) for x in lons_arr], dtype=float)

        # 环形圆周感知 BBox 划分 (消除 0°/360° 跨界造成的近全球大 BBox)
        bboxes = build_circular_fes_bboxes(lons_norm, lats_arr, buffer_deg=buf)
        times_arr = np.full(n_pts, ts_utc_us)

        if len(bboxes) == 1:
            model = self._get_model(bboxes[0], const_list)
            sp, lp, flags = pyfes.evaluate_tide(model, times_arr, lons_norm, lats_arr)
        else:
            sp = np.full(n_pts, np.nan, dtype=np.float32)
            lp = np.full(n_pts, np.nan, dtype=np.float32)
            flags = np.zeros(n_pts, dtype=np.int8)
            for cur_bbox in bboxes:
                in_box = (lons_norm >= cur_bbox[0] - 1e-6) & (lons_norm <= cur_bbox[2] + 1e-6)
                if not np.any(in_box):
                    continue
                model = self._get_model(cur_bbox, const_list)
                sub_sp, sub_lp, sub_flags = pyfes.evaluate_tide(
                    model, times_arr[in_box], lons_norm[in_box], lats_arr[in_box]
                )
                sp[in_box] = sub_sp
                lp[in_box] = sub_lp
                flags[in_box] = sub_flags

        tot_m = (sp + lp) / 100.0
        return tot_m.astype(np.float32), flags.astype(np.int8)

    def predict_points_at_time(
        self,
        lons: float | np.ndarray | list,
        lats: float | np.ndarray | list,
        timestamp: str | pd.Timestamp,
        constituents: str | list = None,
        source_tz: str = 'UTC',
        buffer_deg: float = None
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        单时刻多控制点潮位预测标准公共 API (用于终端时刻 H(t_end) 采样与空间单时刻反演)。
        Single-timestamp multi-point tidal prediction public API.
        """
        return self.predict_spatial_snapshot(
            lons=lons,
            lats=lats,
            timestamp=timestamp,
            constituents=constituents,
            source_tz=source_tz,
            buffer_deg=buffer_deg
        )


class SyntheticTidePredictor:
    """
    合成潮汐预测器 (Synthetic Tide Predictor)，用于无须真实 FES2022b 大型数据文件下的
    严密算法验证、单元测试、空间梯度自适应细分检验及基准对比 (Oracle Testing)。
    """
    def __init__(
        self,
        base_amplitude_m: float = 2.0,
        period_hours: float = 12.42,
        alpha_x: float = 0.05,
        beta_y: float = 0.03,
        gamma_nonlinear: float = 0.01,
        ref_lon: float = 122.0,
        ref_lat: float = 31.0,
        default_freq: str = '30min',
        base_amp: float = None,
        base_mean: float = 0.0,
        gradient_x: float = None,
        gradient_y: float = None,
        valid_lon_range: Optional[Tuple[float, float]] = None,
        valid_lat_range: Optional[Tuple[float, float]] = None,
        validity_func: Optional[Any] = None,
        **kwargs
    ):
        if base_amp is not None:
            base_amplitude_m = base_amp
        if gradient_x is not None:
            alpha_x = gradient_x
        if gradient_y is not None:
            beta_y = gradient_y
        self.base_amplitude = float(base_amplitude_m)
        self.base_mean = float(base_mean)
        self.period_hours = float(period_hours)
        self.alpha_x = float(alpha_x)
        self.beta_y = float(beta_y)
        self.gamma_nonlinear = float(gamma_nonlinear)
        self.ref_lon = float(ref_lon)
        self.ref_lat = float(ref_lat)
        self.default_freq = str(default_freq)
        self.default_constituents = ['M2', 'S2']
        self.valid_lon_range = valid_lon_range
        self.valid_lat_range = valid_lat_range
        self.validity_func = validity_func

    def _is_valid_point(self, xs, ys):
        xs_arr = np.asarray(xs, dtype=float)
        ys_arr = np.asarray(ys, dtype=float)
        valid = np.ones(xs_arr.shape, dtype=bool)
        if self.valid_lon_range is not None:
            valid &= (xs_arr >= self.valid_lon_range[0]) & (xs_arr <= self.valid_lon_range[1])
        if self.valid_lat_range is not None:
            valid &= (ys_arr >= self.valid_lat_range[0]) & (ys_arr <= self.valid_lat_range[1])
        if self.validity_func is not None:
            valid &= np.asarray(self.validity_func(xs_arr, ys_arr), dtype=bool)
        return valid

    def _eval_h(self, lons, lats, t_hours):
        dx = np.asarray(lons, dtype=float) - self.ref_lon
        dy = np.asarray(lats, dtype=float) - self.ref_lat
        spatial_offset = self.alpha_x * dx + self.beta_y * dy + self.gamma_nonlinear * (dx**2 + dy**2)
        omega = 2.0 * np.pi / self.period_hours
        val = self.base_mean + self.base_amplitude * np.cos(omega * t_hours) + spatial_offset

        valid_mask = self._is_valid_point(lons, lats)
        if not np.all(valid_mask):
            val = np.where(valid_mask, val, np.nan)
        return val

    def predict_spatial_snapshot(
        self,
        lons,
        lats,
        timestamp,
        constituents=None,
        source_tz='UTC',
        buffer_deg=None
    ) -> tuple[np.ndarray, np.ndarray]:
        _, dates_np = convert_time_to_utc(timestamp, source_tz=source_tz)
        t_sec = dates_np[0].astype('datetime64[s]').astype(float)
        t_hours = t_sec / 3600.0

        lons_arr = np.atleast_1d(np.asarray(lons, dtype=float))
        lats_arr = np.atleast_1d(np.asarray(lats, dtype=float))
        tide_m = self._eval_h(lons_arr, lats_arr, t_hours).astype(np.float32)
        flags = np.where(np.isfinite(tide_m), 1, 0).astype(np.int8)
        return tide_m, flags

    def predict_points_at_time(
        self,
        lons,
        lats,
        timestamp,
        constituents=None,
        source_tz='UTC',
        buffer_deg=None
    ) -> tuple[np.ndarray, np.ndarray]:
        return self.predict_spatial_snapshot(
            lons=lons,
            lats=lats,
            timestamp=timestamp,
            constituents=constituents,
            source_tz=source_tz,
            buffer_deg=buffer_deg
        )

    def predict_points_period(
        self,
        lons,
        lats,
        start_time,
        end_time,
        freq='30min',
        inclusive='both',
        constituents=None,
        source_tz='UTC',
        max_fes_evaluate_points=500000,
        progress_callback=None
    ) -> tuple[np.ndarray, pd.DatetimeIndex, np.ndarray]:
        _, utc_idx, dates_np = build_time_index(start_time, end_time, freq, source_tz=source_tz, inclusive=inclusive)
        t_sec = dates_np.astype('datetime64[s]').astype(float)
        t_hours = t_sec / 3600.0

        lons_arr = np.atleast_1d(np.asarray(lons, dtype=float))
        lats_arr = np.atleast_1d(np.asarray(lats, dtype=float))
        m = len(lons_arr)
        t = len(dates_np)

        dx = lons_arr - self.ref_lon
        dy = lats_arr - self.ref_lat
        spatial_offsets = self.alpha_x * dx + self.beta_y * dy + self.gamma_nonlinear * (dx**2 + dy**2)
        omega = 2.0 * np.pi / self.period_hours

        cos_t = np.cos(omega * t_hours) * self.base_amplitude
        tide_mat = (self.base_mean + spatial_offsets[:, np.newaxis]) + cos_t[np.newaxis, :]

        valid_mask = self._is_valid_point(lons_arr, lats_arr)
        if not np.all(valid_mask):
            tide_mat[~valid_mask, :] = np.nan

        flag_mat = np.where(np.isfinite(tide_mat), 1, 0).astype(np.int8)
        return tide_mat.astype(np.float32), utc_idx, flag_mat

    def predict_series(
        self,
        lon,
        lat,
        start_time,
        end_time,
        freq='30min',
        inclusive='both',
        constituents=None,
        buffer_deg=None,
        source_tz='UTC',
        progress_callback=None
    ) -> pd.DataFrame:
        input_idx, utc_idx, dates_np = build_time_index(start_time, end_time, freq, source_tz=source_tz, inclusive=inclusive)
        t_sec = dates_np.astype('datetime64[s]').astype(float)
        t_hours = t_sec / 3600.0
        tot_m = self._eval_h(lon, lat, t_hours)
        df = pd.DataFrame({
            'datetime_utc': utc_idx,
            'datetime_input': input_idx,
            'longitude': lon,
            'latitude': lat,
            'tide_short_period_cm': tot_m * 100.0,
            'tide_long_period_cm': np.zeros_like(tot_m),
            'tide_total_cm': tot_m * 100.0,
            'tide_total_m': tot_m,
            'quality_flag': np.ones(len(tot_m), dtype=int)
        })
        return df

    def predict_point_period(self, *args, **kwargs):
        lon = kwargs.get('lon', args[0] if len(args) > 0 else 0.0)
        lat = kwargs.get('lat', args[1] if len(args) > 1 else 0.0)
        start = kwargs.get('start_time', args[2] if len(args) > 2 else '2024-01-01')
        end = kwargs.get('end_time', args[3] if len(args) > 3 else '2024-01-02')
        freq = kwargs.get('freq', '30min')
        inclusive = kwargs.get('inclusive', 'both')
        source_tz = kwargs.get('source_tz', 'UTC')
        return self.predict_series(lon, lat, start, end, freq=freq, inclusive=inclusive, source_tz=source_tz)

    def predict_year(self, lon, lat, year=2024, freq='30min', inclusive='left', constituents=None, buffer_deg=None, source_tz='UTC', datum_mode='both', strict=False, datum_transformer=None, progress_callback=None):
        start = f"{int(year):04d}-01-01 00:00:00"
        end = f"{int(year)+1:04d}-01-01 00:00:00"
        return self.predict_series(lon, lat, start, end, freq=freq, inclusive=inclusive, source_tz=source_tz)


class TwoBasinSyntheticPredictor(SyntheticTidePredictor):
    """
    双水体盆地合成预测器 (用于验证阻隔水体拓扑连通防护与跨盆地无污染插值)。
    左盆地: x < barrier_x_min, mean sea level = left_msl (默认 +1.5m)
    右盆地: x > barrier_x_max, mean sea level = right_msl (默认 -1.5m)
    中间阻隔带: barrier_x_min <= x <= barrier_x_max, 严格为 NaN (陆地屏障)
    """
    def __init__(
        self,
        barrier_x_min: float = 500300.0,
        barrier_x_max: float = 500700.0,
        left_msl: float = 1.5,
        right_msl: float = -1.5,
        base_amplitude_m: float = 0.5,
        period_hours: float = 12.0,
        **kwargs
    ):
        if 'barrier_lon_left' in kwargs:
            barrier_x_min = kwargs.pop('barrier_lon_left')
        if 'barrier_lon_right' in kwargs:
            barrier_x_max = kwargs.pop('barrier_lon_right')
        if 'left_mean' in kwargs:
            left_msl = kwargs.pop('left_mean')
        if 'right_mean' in kwargs:
            right_msl = kwargs.pop('right_mean')
        super().__init__(
            base_amplitude_m=base_amplitude_m,
            period_hours=period_hours,
            alpha_x=0.0,
            beta_y=0.0,
            gamma_nonlinear=0.0,
            **kwargs
        )
        self.barrier_x_min = float(barrier_x_min)
        self.barrier_x_max = float(barrier_x_max)
        self.left_msl = float(left_msl)
        self.right_msl = float(right_msl)

    def _is_valid_point(self, xs, ys):
        xs_arr = np.asarray(xs, dtype=float)
        return (xs_arr < self.barrier_x_min) | (xs_arr > self.barrier_x_max)

    def _eval_h(self, lons, lats, t_hours):
        xs = np.asarray(lons, dtype=float)
        ys = np.asarray(lats, dtype=float)
        omega = 2.0 * np.pi / self.period_hours
        tide_osc = self.base_amplitude * np.cos(omega * t_hours)

        out = np.full(xs.shape, np.nan, dtype=float)
        mask_left = xs < self.barrier_x_min
        mask_right = xs > self.barrier_x_max

        out[mask_left] = self.left_msl + tide_osc
        out[mask_right] = self.right_msl + tide_osc
        return out

    def predict_points_period(
        self,
        lons,
        lats,
        start_time,
        end_time,
        freq='30min',
        inclusive='both',
        constituents=None,
        source_tz='UTC',
        max_fes_evaluate_points=500000,
        progress_callback=None
    ) -> tuple[np.ndarray, pd.DatetimeIndex, np.ndarray]:
        _, utc_idx, dates_np = build_time_index(start_time, end_time, freq, source_tz=source_tz, inclusive=inclusive)
        t_sec = dates_np.astype('datetime64[s]').astype(float)
        t_hours = t_sec / 3600.0

        lons_arr = np.atleast_1d(np.asarray(lons, dtype=float))
        lats_arr = np.atleast_1d(np.asarray(lats, dtype=float))
        m = len(lons_arr)
        t = len(dates_np)

        omega = 2.0 * np.pi / self.period_hours
        cos_t = np.cos(omega * t_hours) * self.base_amplitude

        offsets = np.full(m, np.nan, dtype=float)
        mask_left = (lons_arr < self.barrier_x_min)
        mask_right = (lons_arr > self.barrier_x_max)
        offsets[mask_left] = self.left_msl
        offsets[mask_right] = self.right_msl

        tide_mat = offsets[:, np.newaxis] + cos_t[np.newaxis, :]
        flag_mat = np.where(np.isfinite(tide_mat), 1, 0).astype(np.int8)
        return tide_mat.astype(np.float32), utc_idx, flag_mat

    def predict_spatial_snapshot(
        self,
        lons,
        lats,
        timestamp,
        constituents=None,
        source_tz='UTC',
        buffer_deg=None
    ) -> tuple[np.ndarray, np.ndarray]:
        _, dates_np = convert_time_to_utc(timestamp, source_tz=source_tz)
        t_sec = dates_np[0].astype('datetime64[s]').astype(float)
        t_hours = t_sec / 3600.0
        lons_arr = np.atleast_1d(np.asarray(lons, dtype=float))
        lats_arr = np.atleast_1d(np.asarray(lats, dtype=float))
        tide_m = self._eval_h(lons_arr, lats_arr, t_hours).astype(np.float32)
        flags = np.where(np.isfinite(tide_m), 1, 0).astype(np.int8)
        return tide_m, flags

    def predict_points_at_time(
        self,
        lons,
        lats,
        timestamp,
        constituents=None,
        source_tz='UTC',
        buffer_deg=None
    ) -> tuple[np.ndarray, np.ndarray]:
        return self.predict_spatial_snapshot(
            lons=lons,
            lats=lats,
            timestamp=timestamp,
            constituents=constituents,
            source_tz=source_tz,
            buffer_deg=buffer_deg
        )


DisconnectedBarrierPredictor = TwoBasinSyntheticPredictor
