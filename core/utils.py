"""
CoastTideX 通用工具与常量定义
提供坐标规范化、时区精准转换、预设海岸带站点、配置文件加载与数据导出等功能。
"""

import os
import copy
import yaml
import numpy as np
import pandas as pd
import dateutil.tz
from datetime import datetime, timezone


def get_project_root() -> str:
    """获取项目根目录绝对路径"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


PROJECT_ROOT = get_project_root()


def resolve_project_path(path_str: str) -> str:
    """若为相对路径，自动解析为相对于项目根目录的绝对路径"""
    if not path_str:
        return ""
    if os.path.isabs(path_str):
        return os.path.normpath(path_str)
    return os.path.normpath(os.path.join(get_project_root(), path_str))


def to_relative_project_path(p: str) -> str:
    """若路径位于项目根目录下，将其转换为相对路径，以确保多机跨系统可移植性"""
    if not p or not isinstance(p, str):
        return p
    root = get_project_root()
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


def convert_time_to_utc(
    time_series: pd.DatetimeIndex | pd.Series | list,
    source_tz: str = 'UTC'
) -> tuple[pd.DatetimeIndex, np.ndarray]:
    """
    将输入的时间序列严格统一转换为无时区的 UTC 标准时间序列。
    全面支持动态夏令时 (DST) 跨季节时区校准。

    参数:
        time_series: 输入的时间序列
        source_tz: 源时区标识，如 'UTC', 'local', 'Asia/Shanghai' 等

    返回:
        (utc_dt_index, utc_numpy_datetime64_us)
    """
    # 兼容单标量或序列输入
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
    elif str(source_tz).lower() == 'local':
        # 采用 dateutil.tz.tzlocal() 动态依据每个日期的 OS 时区规则处理夏令时 (DST)
        tz_loc = dateutil.tz.tzlocal()
        if dt_idx.tz is None:
            utc_idx = dt_idx.tz_localize(tz_loc, ambiguous='NaT', nonexistent='shift_forward').tz_convert('UTC').tz_localize(None)
        else:
            utc_idx = dt_idx.tz_convert('UTC').tz_localize(None)
    else:
        # 指定特定时区字符串 (如 'Asia/Shanghai', 'America/New_York')
        if dt_idx.tz is None:
            utc_idx = dt_idx.tz_localize(source_tz, ambiguous='NaT', nonexistent='shift_forward').tz_convert('UTC').tz_localize(None)
        else:
            utc_idx = dt_idx.tz_convert('UTC').tz_localize(None)

    utc_numpy = utc_idx.to_numpy(dtype='datetime64[us]')
    return utc_idx, utc_numpy


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
    加载应用 YAML 配置文件，并自动将相对路径解析为项目根目录绝对路径。
    """
    if config_path is None:
        config_path = os.path.join(get_project_root(), 'config.yaml')

    if not os.path.exists(config_path):
        raise FileNotFoundError(f"未找到配置文件: {config_path}")

    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    # 路径解析
    if 'paths' in config:
        for k, v in config['paths'].items():
            if isinstance(v, str):
                config['paths'][k] = resolve_project_path(v)

    return config


def save_app_config(config_dict: dict, config_path: str = None) -> None:
    """保存应用 YAML 配置文件，并自动将项目内部绝对路径恢复为相对路径，保障跨机便携性"""
    if config_path is None:
        config_path = os.path.join(get_project_root(), 'config.yaml')

    save_dict = copy.deepcopy(config_dict)
    if 'paths' in save_dict:
        for k, v in save_dict['paths'].items():
            if isinstance(v, str):
                save_dict['paths'][k] = to_relative_project_path(v)

    with open(config_path, 'w', encoding='utf-8') as f:
        yaml.safe_dump(save_dict, f, allow_unicode=True, default_flow_style=False)


def export_dataframe(df: pd.DataFrame, output_path: str) -> None:
    """将预测结果 DataFrame 导出为 CSV 或 Excel 格式"""
    ext = os.path.splitext(output_path)[1].lower()
    if ext in ['.xlsx', '.xls']:
        df.to_excel(output_path, index=False)
    else:
        df.to_csv(output_path, index=False, encoding='utf-8-sig')
