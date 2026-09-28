"""
CoastTideX 通用工具与常量定义
提供路径智能解析、坐标规范化、时区精准转换、预设海岸带站点、时间序列构建、潜在潮汐淹没频率及数据导出等功能。
"""

import os
import sys
import copy
import yaml
import warnings
import numpy as np
import pandas as pd
import dateutil.tz
from datetime import datetime, timezone


def get_project_root() -> str:
    """获取项目源码根目录绝对路径"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_resource_root() -> str:
    """获取程序内置只读资源根目录 (PyInstaller frozen 模式下为 sys._MEIPASS，源码模式下为项目根目录)"""
    if getattr(sys, 'frozen', False):
        return getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    return get_project_root()


def get_app_root() -> str:
    """获取应用程序安装/可执行文件所在根目录 (PyInstaller frozen 模式下为 CoastTideX.exe 所在目录，源码模式下为项目根目录)"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return get_project_root()


PROJECT_ROOT = get_project_root()


def resolve_project_path(path_str: str, prefer_resource: bool = False) -> str:
    """
    智能解析相对路径为绝对路径。
    明确区分：
      - resource_root: PyInstaller 内置小型资源 (如 data/geoid/ 栅格、config.yaml 默认模板)
      - app_root: 外部大型模型目录 (如用户放置在 CoastTideX.exe 同级的 fes2022b/、mdt_cls22/)
    """
    if not path_str:
        return ""
    if os.path.isabs(path_str):
        return os.path.normpath(path_str)

    res_root = get_resource_root()
    app_root = get_app_root()

    path_in_res = os.path.normpath(os.path.join(res_root, path_str))
    path_in_app = os.path.normpath(os.path.join(app_root, path_str))

    if prefer_resource:
        if os.path.exists(path_in_res):
            return path_in_res
        if os.path.exists(path_in_app):
            return path_in_app
        return path_in_res
    else:
        if os.path.exists(path_in_app):
            return path_in_app
        if os.path.exists(path_in_res):
            return path_in_res
        return path_in_app


def to_relative_project_path(p: str) -> str:
    """若路径位于项目/应用根目录下，将其转换为相对路径，以确保多机跨系统可移植性"""
    if not p or not isinstance(p, str):
        return p
    root = get_app_root()
    try:
        abs_p = os.path.abspath(p)
        abs_root = os.path.abspath(root)
        if os.path.splitdrive(abs_p)[0].lower() == os.path.splitdrive(abs_root)[0].lower():
            rel = os.path.relpath(abs_p, abs_root)
            if not rel.startswith('..'):
                return rel.replace('\\', '/')
    except Exception:
        pass
    return p.replace('\\', '/')


def validate_coordinates(lon: float, lat: float) -> tuple[float, float]:
    """
    在核心层严格校验经纬度坐标。
    - 经纬度必须为有限数字 (finite)
    - 纬度必须满足 -90.0 <= lat <= 90.0
    """
    try:
        lon_f = float(lon)
        lat_f = float(lat)
    except (ValueError, TypeError) as e:
        raise ValueError(f"经纬度必须为有效数字，当前输入: lon={lon}, lat={lat}") from e

    if not (np.isfinite(lon_f) and np.isfinite(lat_f)):
        raise ValueError(f"经纬度不能为 NaN 或 Inf，当前输入: lon={lon_f}, lat={lat_f}")

    if not (-90.0 <= lat_f <= 90.0):
        raise ValueError(f"纬度必须位于 [-90, +90] 范围内，当前输入: lat={lat_f}")

    return lon_f, lat_f


def validate_time_params(
    start_time: str | datetime | pd.Timestamp,
    end_time: str | datetime | pd.Timestamp,
    freq: str = '30min'
) -> tuple[pd.Timestamp, pd.Timestamp, str]:
    """严格校验时间范围与频率步长参数"""
    try:
        ts_start = pd.Timestamp(start_time)
        ts_end = pd.Timestamp(end_time)
    except Exception as e:
        raise ValueError(f"无法解析的时间格式: start_time={start_time}, end_time={end_time}") from e

    if pd.isna(ts_start) or pd.isna(ts_end):
        raise ValueError(f"时间参数包含无效 NaT: start_time={start_time}, end_time={end_time}")

    if ts_start >= ts_end:
        raise ValueError(f"起始时间 ({ts_start}) 必须严格早于结束时间 ({ts_end})")

    if not freq or not isinstance(freq, str):
        raise ValueError(f"时间步长 freq 必须为非空字符串，当前输入: {freq}")

    try:
        delta = pd.to_timedelta(pd.tseries.frequencies.to_offset(freq).nanos, unit='ns')
        if delta <= pd.Timedelta(0):
            raise ValueError()
    except Exception as e:
        raise ValueError(f"无法识别或非正数的时间采样间隔 freq='{freq}'") from e

    return ts_start, ts_end, freq


def build_time_index(
    start_time: str | datetime | pd.Timestamp,
    end_time: str | datetime | pd.Timestamp,
    freq: str = '30min',
    source_tz: str = 'UTC',
    inclusive: str = 'both'
) -> tuple[pd.DatetimeIndex, pd.DatetimeIndex, np.ndarray]:
    """
    构建严格单调、无重复且跨夏令时稳健的时间序列网格。

    参数:
        start_time: 起始时刻
        end_time: 结束时刻
        freq: 步长 (如 '5min', '6min', '10min', '15min', '30min', '1h', '2h')
        source_tz: 'UTC'、'local' 或有效时区标识 (如 'America/New_York')
        inclusive: 'both' (包含两端 [start, end]), 'left' (半开区间 [start, end)),
                   'right' ((start, end]), 'neither' ((start, end))

    返回:
        (input_series_index, utc_series_index, utc_numpy_datetime64_us)
    """
    ts_start, ts_end, freq = validate_time_params(start_time, end_time, freq)

    if source_tz == 'UTC' or source_tz is None:
        # UTC 模式直接生成精确连续网格
        utc_idx = pd.date_range(start=ts_start, end=ts_end, freq=freq, inclusive=inclusive)
        input_idx = utc_idx
    else:
        target_tz = dateutil.tz.tzlocal() if str(source_tz).lower() == 'local' else source_tz

        # 先将起止时间在源时区内精准对齐
        if ts_start.tzinfo is None:
            try:
                start_aware = ts_start.tz_localize(target_tz, ambiguous=False, nonexistent='shift_forward')
            except Exception:
                start_aware = ts_start.tz_localize(target_tz, ambiguous=True, nonexistent='shift_forward')
        else:
            start_aware = ts_start.tz_convert(target_tz)

        if ts_end.tzinfo is None:
            try:
                end_aware = ts_end.tz_localize(target_tz, ambiguous=False, nonexistent='shift_forward')
            except Exception:
                end_aware = ts_end.tz_localize(target_tz, ambiguous=True, nonexistent='shift_forward')
        else:
            end_aware = ts_end.tz_convert(target_tz)

        # 转换为 UTC 起止时刻，以真实的连续物理时间流逝步长步进 (保证跨夏令时物理连续无断层/无重复)
        start_utc = start_aware.tz_convert('UTC')
        end_utc = end_aware.tz_convert('UTC')

        utc_aware_idx = pd.date_range(start=start_utc, end=end_utc, freq=freq, inclusive=inclusive)
        utc_idx = utc_aware_idx.tz_localize(None)

        # 映射回本地时间显示序列
        input_idx = utc_aware_idx.tz_convert(target_tz).tz_localize(None)

    if len(utc_idx) == 0:
        raise ValueError(f"生成的时间序列为空，请检查时间范围与步长参数: start={start_time}, end={end_time}, freq={freq}")

    # 验证严格单调递增性与无重复性
    if not utc_idx.is_monotonic_increasing:
        raise ValueError("生成的时间序列未能满足严格单调递增要求！")

    if not utc_idx.is_unique:
        raise ValueError("生成的时间序列中检测到重复的时间戳 (Duplicate Timestamps)！")

    utc_numpy = utc_idx.to_numpy(dtype='datetime64[us]')
    return input_idx, utc_idx, utc_numpy


def normalize_longitude(lon: float | np.ndarray, to_360: bool = True) -> float | np.ndarray:
    """
    规范化经度坐标体系。

    参数:
        lon: 经度 (单值或 numpy 数组)
        to_360: True 映射至 [0, 360) (FES2022 规范)；False 映射至 [-180, 180) (GIS 惯例)
    """
    if to_360:
        return np.mod(lon, 360.0)
    else:
        # 映射至 [-180, 180)
        val = np.mod(lon, 360.0)
        if np.isscalar(val):
            return val - 360.0 if val >= 180.0 else val
        else:
            return ((val + 180.0) % 360.0) - 180.0


def circular_longitude_span(lons: float | np.ndarray | list) -> tuple[float, float, float]:
    """
    计算球面经度集合在环形圆周 (0°~360° 或 -180°~180°) 上的最小实际角跨度 (Circular Longitude Span)。
    彻底解决格林尼治子午线 (0°附近, 如 -0.5° 与 +0.5°) 和国际日期变更线 (180°附近, 如 179.5° 与 -179.5°)
    传统 np.ptp() 计算导致角跨度膨胀为 ~359° 的经典科学 Bug。

    参数:
        lons: 经度标量、列表或数组

    返回:
        (span_deg, arc_start_360, arc_end_360):
        - span_deg: 实际最小圆弧角跨度 (度, 0.0 ~ 360.0)
        - arc_start_360: 该紧凑圆弧在 [0, 360) 体系下的起始经度
        - arc_end_360: 该紧凑圆弧在 [0, 360) 体系下的终止经度
    """
    lons_arr = np.atleast_1d(np.asarray(lons, dtype=float))
    valid_lons = lons_arr[np.isfinite(lons_arr)]
    if len(valid_lons) <= 1:
        v = float(valid_lons[0] % 360.0) if len(valid_lons) == 1 else 0.0
        return 0.0, v, v

    lons_360 = np.sort(np.unique(valid_lons % 360.0))
    n = len(lons_360)
    if n == 1:
        return 0.0, float(lons_360[0]), float(lons_360[0])

    gaps = np.empty(n, dtype=float)
    gaps[:-1] = lons_360[1:] - lons_360[:-1]
    gaps[-1] = (lons_360[0] + 360.0) - lons_360[-1]

    max_gap_idx = int(np.argmax(gaps))
    max_gap = float(gaps[max_gap_idx])

    span = max(0.0, 360.0 - max_gap)
    start_pt = float(lons_360[(max_gap_idx + 1) % n])
    end_pt = float(lons_360[max_gap_idx])

    return span, start_pt, end_pt


def build_circular_fes_bboxes(
    lons: float | np.ndarray | list,
    lats: float | np.ndarray | list,
    buffer_deg: float = 0.5
) -> list[tuple[float, float, float, float]]:
    """
    根据给定的经纬度集合，构建环形圆周感知 (Circular-aware) 的局部 FES2022b 网格查询包围框 (BBox)。
    彻底消除跨越格林尼治子午线 (0°附近, 如 -1° 与 +1°) 或国际日期变更线 (180°附近, 如 179° 与 -179°)
    时生成近 360° 全球包围框而引发的内存暴涨与模型假死。

    参数:
        lons: 经度标量、列表或数组
        lats: 纬度标量、列表或数组
        buffer_deg: 空间缓冲外扩半径 (度，默认 0.5°)

    返回:
        list of bboxes: 每个 bbox 为 (lon_min, lat_min, lon_max, lat_max)
        如果跨越 0°/360° 分界线且总跨度较小，拆分为两个紧凑的局部 BBox；
        否则返回单个紧凑的局部 BBox。保证每个 BBox 的经度跨度均严格受控于局部真实几何跨度。
    """
    lons_arr = np.atleast_1d(np.asarray(lons, dtype=float))
    lats_arr = np.atleast_1d(np.asarray(lats, dtype=float))
    valid_mask = np.isfinite(lons_arr) & np.isfinite(lats_arr)
    if not np.any(valid_mask):
        return [(0.0, -90.0, 360.0, 90.0)]

    valid_lons = lons_arr[valid_mask]
    valid_lats = lats_arr[valid_mask]

    lat_min = max(-90.0, float(np.min(valid_lats)) - buffer_deg)
    lat_max = min(90.0, float(np.max(valid_lats)) + buffer_deg)

    lons_360 = valid_lons % 360.0
    span, arc_start, arc_end = circular_longitude_span(lons_360)

    # 若点集跨度已达全球 (>= 180°)，直接返回全经度包围框
    if span >= 180.0:
        lon_min = max(0.0, float(np.min(lons_360)) - buffer_deg)
        lon_max = min(360.0, float(np.max(lons_360)) + buffer_deg)
        return [(lon_min, lat_min, lon_max, lat_max)]

    if arc_end >= arc_start:
        # 普通未跨越 0°/360° 边界的圆弧 (含 180° 日期变更线，因在 [0, 360) 体系下 180° 是平滑连续的)
        lon_min = max(0.0, arc_start - buffer_deg)
        lon_max = min(360.0, arc_end + buffer_deg)
        return [(lon_min, lat_min, lon_max, lat_max)]
    else:
        # 跨越格林尼治 0°/360° 边界的圆弧: 拆分为 [arc_start - buf, 360] 与 [0, arc_end + buf] 两个紧凑局部 BBox
        box1_lon_min = max(0.0, arc_start - buffer_deg)
        box1_lon_max = 360.0
        box2_lon_min = 0.0
        box2_lon_max = min(360.0, arc_end + buffer_deg)
        return [
            (box1_lon_min, lat_min, box1_lon_max, lat_max),
            (box2_lon_min, lat_min, box2_lon_max, lat_max)
        ]


def convert_time_to_utc(
    time_series: pd.DatetimeIndex | pd.Series | list,
    source_tz: str = 'UTC'
) -> tuple[pd.DatetimeIndex, np.ndarray]:
    """
    将离散输入的时间序列严格统一转换为无时区的 UTC 标准时间序列。
    全面支持动态夏令时 (DST) 跨季节时区校准。
    """
    if isinstance(time_series, (str, datetime, pd.Timestamp)) or not hasattr(time_series, '__len__'):
        time_inputs = [time_series]
    else:
        time_inputs = time_series

    dt_idx = pd.DatetimeIndex(pd.to_datetime(time_inputs))

    if source_tz == 'UTC' or source_tz is None:
        if dt_idx.tz is not None:
            utc_idx = dt_idx.tz_convert('UTC').tz_localize(None)
        else:
            utc_idx = dt_idx
    else:
        target_tz = dateutil.tz.tzlocal() if str(source_tz).lower() == 'local' else source_tz
        if dt_idx.tz is None:
            try:
                localized = dt_idx.tz_localize(target_tz, ambiguous='infer', nonexistent='shift_forward')
            except Exception:
                temp_loc = dt_idx.tz_localize(target_tz, ambiguous='NaT', nonexistent='shift_forward')
                if temp_loc.isna().any():
                    nat_cnt = int(temp_loc.isna().sum())
                    warnings.warn(
                        f"检测到输入时序中包含 {nat_cnt} 个处于夏令时回拨重复区间 (Fall-back ambiguous hour) 的时间点，"
                        "已自动按夏令时初次出现时段对齐解析，消除非法 NaT。"
                    )
                localized = dt_idx.tz_localize(target_tz, ambiguous=True, nonexistent='shift_forward')
            utc_idx = localized.tz_convert('UTC').tz_localize(None)
        else:
            utc_idx = dt_idx.tz_convert('UTC').tz_localize(None)

    utc_numpy = utc_idx.to_numpy(dtype='datetime64[us]')
    return utc_idx, utc_numpy


def compute_inundation_frequency(
    water_levels_m: float | np.ndarray | list,
    terrain_elevations_m: float | np.ndarray | list,
    as_percentage: bool = False
) -> float | np.ndarray:
    """
    计算潜在天文潮汐淹没频率 (Potential Astronomical Tidal Inundation Frequency)。

    科学定义与数学公式:
        F(z) = P(H_water > z)
        表示在固定地形条件下，仅由天文潮及静态 MDT 表征的水位高于特定地形高程的累积超频概率。
        采用向量化排序与经验累积分布函数/二分检索 (np.searchsorted)，杜绝构建巨大的像素×时间二维矩阵。

    重要科学边界说明:
        本模块解算的是静态地形与调和潮汐驱动下的潜在淹没频率，不包含风暴增水 (storm surge)、
        海平面异常 (SLA)、波浪爬高破碎 (wave setup)、径流汇入 (river discharge)
        及潮滩水动力阻滞/水力连通性等复杂物理过程。

    参数:
        water_levels_m: 潮位/水面高度时序数组 (单位: 米，必须与地形在同一垂直基准，如均基于 EGM2008)
        terrain_elevations_m: 地形高程标量或数组 (单位: 米)
        as_percentage: 是否以百分比 (0~100%) 返回，默认 False (0~1.0)

    返回:
        float 或与 terrain_elevations_m 形状一致的 numpy.ndarray
    """
    w = np.asarray(water_levels_m, dtype=float)
    is_scalar_z = np.isscalar(terrain_elevations_m)
    z_arr = np.atleast_1d(np.asarray(terrain_elevations_m, dtype=float))

    valid_w = w[np.isfinite(w)]
    if len(valid_w) == 0:
        res = np.full(z_arr.shape, np.nan, dtype=float)
        return float(res[0]) if is_scalar_z else res

    w_sorted = np.sort(valid_w)
    n = len(w_sorted)

    # side='right' 统计有效潮位中 <= z 的样本数
    idx = np.searchsorted(w_sorted, z_arr, side='right')
    freq = (n - idx) / float(n)
    freq[~np.isfinite(z_arr)] = np.nan

    if as_percentage:
        freq = freq * 100.0

    return float(freq[0]) if is_scalar_z else freq


def extract_scalar_metadata(val, default: str = "NORMAL") -> str:
    """安全提取单点时序结果中的标量字符串元数据，杜绝 numpy array 的 repr 泄露到 GUI"""
    if val is None:
        return default
    if isinstance(val, (str, bytes)):
        s = str(val).strip()
        return s if s else default
    if hasattr(val, '__len__'):
        if len(val) == 0:
            return default
        val = val[0]
    if pd.isna(val):
        return default
    return str(val).strip()


# 全球经典海岸带、河口湾区与主要港口预设字典
COASTAL_PRESETS = {
    "长江口 (Changjiang Estuary)": {"lon": 122.00, "lat": 31.00, "desc": "中国最大河口湾，强非正规半日潮"},
    "珠江口 / 伶仃洋 (Pearl River Estuary)": {"lon": 113.75, "lat": 22.35, "desc": "粤港澳大湾区，不规则半日潮混合区"},
    "杭州湾 (Hangzhou Bay)": {"lon": 121.20, "lat": 30.50, "desc": "钱塘江大潮发源地，喇叭形强潮河口湾"},
    "渤海湾 (Bohai Bay)": {"lon": 118.00, "lat": 38.80, "desc": "半封闭浅水海湾，浅水分潮发育明显"},
    "维多利亚港 (Victoria Harbour, HK)": {"lon": 114.17, "lat": 22.29, "desc": "香港核心水域"},
    "胶州湾 (Jiaozhou Bay, Qingdao)": {"lon": 120.25, "lat": 36.10, "desc": "青岛国家高程基准起源地，正规半日潮"},
    "东京湾 (Tokyo Bay, Japan)": {"lon": 139.80, "lat": 35.50, "desc": "日本典型狭长半封闭湾区"},
    "新加坡海峡 (Singapore Strait)": {"lon": 103.85, "lat": 1.25, "desc": "全球黄金水道，日潮与半日潮交互影响"},
    "鹿特丹港 (Rotterdam, Netherlands)": {"lon": 4.10, "lat": 51.95, "desc": "欧洲第一大港，北海半日潮"},
    "纽约港 (New York Harbor, USA)": {"lon": -74.05, "lat": 40.65, "desc": "美国大西洋沿岸典型半日潮河口港"},
    "旧金山湾 (San Francisco Bay, USA)": {"lon": -122.42, "lat": 37.82, "desc": "太平洋沿岸大型河口海湾，混合半日潮"},
    "悉尼港 (Sydney Harbour, Australia)": {"lon": 151.25, "lat": -33.85, "desc": "澳洲东海岸典型天然深水港"},
    "亚马逊河口 (Amazon River Mouth)": {"lon": -49.50, "lat": 0.50, "desc": "全球流量最大河口，强潮巨潮作用显著"}
}


def load_app_config(config_path: str = None) -> dict:
    """
    加载应用 YAML 配置文件，并自动将相对路径解析为绝对路径。
    """
    if config_path is None:
        config_path = resolve_project_path('config.yaml', prefer_resource=True)

    if not os.path.exists(config_path):
        raise FileNotFoundError(f"未找到配置文件: {config_path}")

    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    # 路径解析
    if 'paths' in config:
        for k, v in config['paths'].items():
            if isinstance(v, str):
                prefer_res = 'geoid' in v or 'delta_n' in v or 'egm08' in v
                config['paths'][k] = resolve_project_path(v, prefer_resource=prefer_res)

    return config


def save_app_config(config_dict: dict, config_path: str = None) -> None:
    """保存应用 YAML 配置文件，并自动将项目内部绝对路径恢复为相对路径，保障跨机便携性"""
    if config_path is None:
        config_path = resolve_project_path('config.yaml', prefer_resource=True)

    save_dict = copy.deepcopy(config_dict)
    if 'paths' in save_dict:
        for k, v in save_dict['paths'].items():
            if isinstance(v, str):
                save_dict['paths'][k] = to_relative_project_path(v)

    with open(config_path, 'w', encoding='utf-8') as f:
        yaml.safe_dump(save_dict, f, allow_unicode=True, default_flow_style=False)


def export_dataframe(df: pd.DataFrame, output_path: str) -> None:
    """将预测结果 DataFrame 完整导出为 CSV 或 Excel 格式"""
    ext = os.path.splitext(output_path)[1].lower()
    if ext in ['.xlsx', '.xls']:
        df.to_excel(output_path, index=False)
    else:
        df.to_csv(output_path, index=False, encoding='utf-8-sig')
