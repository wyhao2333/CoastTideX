"""
CoastTideX 通用工具与常量定义
提供坐标规范化、预设海岸带站点、配置文件加载与数据导出等功能。
"""

import os
import yaml
import pandas as pd


def normalize_longitude(lon: float, to_360: bool = True) -> float:
    """
    规范化经度坐标体系。

    参数:
        lon: 输入经度
        to_360: 若为 True，转换为 0~360° (用于 FES 模型);
                若为 False，转换为 -180~180° (用于常规地图与 MDT)

    返回:
        规范化后的经度浮点数
    """
    if to_360:
        val = lon % 360.0
        return val if val >= 0 else val + 360.0
    else:
        return ((lon + 180.0) % 360.0) - 180.0


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
    加载应用 YAML 配置文件。若未指定路径，自动搜寻项目根目录下的 config.yaml。
    """
    if config_path is None:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        config_path = os.path.join(base_dir, 'config.yaml')

    if not os.path.exists(config_path):
        raise FileNotFoundError(f"未找到配置文件: {config_path}")

    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    return config


def save_app_config(config_dict: dict, config_path: str = None) -> None:
    """
    保存应用 YAML 配置文件。
    """
    if config_path is None:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        config_path = os.path.join(base_dir, 'config.yaml')

    with open(config_path, 'w', encoding='utf-8') as f:
        yaml.safe_dump(config_dict, f, allow_unicode=True, default_flow_style=False)


def export_dataframe(df: pd.DataFrame, output_path: str) -> None:
    """
    将预测结果 DataFrame 导出为 CSV 或 Excel 格式。
    """
    ext = os.path.splitext(output_path)[1].lower()
    if ext in ['.xlsx', '.xls']:
        df.to_excel(output_path, index=False)
    else:
        df.to_csv(output_path, index=False, encoding='utf-8-sig')
