"""
CoastTideX 通用工具与常量定义
提供坐标规范化、时区精准转换、预设海岸带站点、配置文件加载与数据导出等功能。
"""

import os
import yaml
import numpy as np
import pandas as pd
from datetime import datetime, timezone


def get_project_root() -> str:
    """获取项目根目录绝对路径"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resolve_project_path(path_str: str) -> str:
    """若为相对路径，自动解析为相对于项目根目录的绝对路径"""
    if not path_str:
        return ""
    if os.path.isabs(path_str):
        return os.path.normpath(path_str)
    return os.path.normpath(os.path.join(get_project_root(), path_str))


def normalize_longitude(lon: float | np.ndarray, to_360: bool = True) -> float | np.ndarray:
    """
    规范化经度坐标体系。

    参数:
        lon: 输入经度（标量或 NumPy 数组）
        to_360: 若为 True，转换为 0~360° (用于 FES 模型);
                若为 False，转换为 -180~180° (用于常规地图与 MDT)

    返回:
        规范化后的经度
    """
    if isinstance(lon, (list, tuple, np.ndarray)):
        arr = np.asarray(lon, dtype=float)
        if to_360:
            return np.where(arr < 0, (arr % 360.0 + 360.0) % 360.0, arr % 360.0)
        else:
            return ((arr + 180.0) % 360.0) - 180.0
    else:
        val = float(lon)
        if to_360:
            rem = val % 360.0
            return rem if rem >= 0 else rem + 360.0
        else:
            return ((val + 180.0) % 360.0) - 180.0


def convert_time_to_utc(
    time_series: pd.DatetimeIndex | pd.Series | list,
    source_tz: str = 'UTC'
) -> tuple[pd.DatetimeIndex, np.ndarray]:
    """
    将输入的时间序列严格统一转换为无时区的 UTC 标准时间序列。

    参数:
        time_series: 输入的时间序列
        source_tz: 源时区标识，如 'UTC', 'local', 'Asia/Shanghai' 等

    返回:
        (utc_dt_index, utc_numpy_datetime64_us)
    """
    dt_idx = pd.DatetimeIndex(pd.to_datetime(time_series))

    if source_tz == 'UTC' or source_tz is None:
        if dt_idx.tz is not None:
            utc_idx = dt_idx.tz_convert('UTC').tz_localize(None)
        else:
            utc_idx = dt_idx
    elif source_tz == 'local':
        # 获取本机系统时区
        local_tz = datetime.now().astimezone().tzinfo
        if dt_idx.tz is None:
            utc_idx = dt_idx.tz_localize(local_tz).tz_convert('UTC').tz_localize(None)
        else:
            utc_idx = dt_idx.tz_convert('UTC').tz_localize(None)
    else:
        # 指定特定时区字符串
        if dt_idx.tz is None:
            utc_idx = dt_idx.tz_localize(source_tz).tz_convert('UTC').tz_localize(None)
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
    """保存应用 YAML 配置文件"""
    if config_path is None:
        config_path = os.path.join(get_project_root(), 'config.yaml')

    with open(config_path, 'w', encoding='utf-8') as f:
        yaml.safe_dump(config_dict, f, allow_unicode=True, default_flow_style=False)


def export_dataframe(df: pd.DataFrame, output_path: str) -> None:
    """将预测结果 DataFrame 导出为 CSV 或 Excel 格式"""
    ext = os.path.splitext(output_path)[1].lower()
    if ext in ['.xlsx', '.xls']:
        df.to_excel(output_path, index=False)
    else:
        df.to_csv(output_path, index=False, encoding='utf-8-sig')
