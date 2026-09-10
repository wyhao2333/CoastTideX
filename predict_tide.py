"""
FES2022b 潮位预测工具 (Tide Prediction Tool)
基于 CNES/AVISO 官方 pyfes 库与 FES2022b 非结构有限元高分辨率网格

支持：
1. 全球任意海岸带经纬度单点 / 批量点预测
2. 任意时刻 / 连续时间序列预测
3. 自动根据目标经纬度设定局部空间边界框 (Bounding Box)，秒级高效加载
4. 输出短周期分潮、长周期平衡潮、总潮高及质量控制标志 (Flag)
"""

import os
import argparse
import numpy as np
import pandas as pd
import pyfes
import pyfes.config as cfg

# 默认 FES2022b 原生非结构有限元网格路径
DEFAULT_NS_GRID = os.path.join(os.path.dirname(__file__), 'fes2022b', 'ocean_tide_non_structured', 'FES2022b_OceanTide_NSgrid.nc')

# 34个分潮全集
ALL_CONSTITUENTS = [
    '2N2', 'Eps2', 'J1', 'K1', 'K2', 'L2', 'Lambda2', 'M2', 'M3', 'M4', 'M6', 'M8',
    'MKS2', 'MN4', 'MS4', 'MSf', 'Mf', 'Mm', 'Msqm', 'Mtm', 'Mu2', 'N2', 'N4', 'Nu2',
    'O1', 'P1', 'Q1', 'R2', 'S1', 'S2', 'S4', 'Sa', 'Ssa', 'T2'
]

# 8个主要核心分潮（如需最极致加载速度）
MAJOR_8_CONSTITUENTS = ['M2', 'S2', 'K1', 'O1', 'N2', 'K2', 'P1', 'Q1']


def predict_coastal_tide(
    lon: float,
    lat: float,
    start_time: str,
    end_time: str,
    freq: str = '1h',
    constituents: list = None,
    grid_path: str = DEFAULT_NS_GRID,
    buffer_deg: float = 1.0
) -> pd.DataFrame:
    """
    预测指定经纬度在指定时间段内的潮位时间序列。

    参数:
        lon: 目标经度 (支持 -180~180 或 0~360)
        lat: 目标纬度 (-90~90)
        start_time: 起始时间 (格式: 'YYYY-MM-DD HH:MM:SS')
        end_time: 结束时间 (格式: 'YYYY-MM-DD HH:MM:SS')
        freq: 时间步长 (例如 '1h', '10min', '15min')
        constituents: 使用的分潮列表，默认使用全部34个分潮
        grid_path: FES2022b 非结构网格 nc 文件路径
        buffer_deg: 局部区域加载缓冲范围(度)，默认 1.0 度

    返回:
        pandas.DataFrame: 包含时间、短周期潮高(cm)、长周期潮高(cm)、总潮高(cm/m)、质量Flag
    """
    if constituents is None:
        constituents = ALL_CONSTITUENTS

    # 规范化经度为 0~360 (FES 内部坐标体系)
    lon_norm = lon if lon >= 0 else (lon + 360.0)

    # 设定局部空间边界框 (Bounding Box)，避免全网格扫描，显著降低内存并加快速度
    bbox = (
        max(0.0, lon_norm - buffer_deg),
        max(-90.0, lat - buffer_deg),
        min(360.0, lon_norm + buffer_deg),
        min(90.0, lat + buffer_deg)
    )

    print(f"[1/3] 正在加载 FES2022b 局部网格 (Lon: {bbox[0]:.2f}~{bbox[2]:.2f}, Lat: {bbox[1]:.2f}~{bbox[3]:.2f})...")
    lgp_config = cfg.LGP(
        path=grid_path,
        type='lgp2',
        codes='lgp2',
        constituents=constituents,
        bbox=bbox
    )
    tide_model = lgp_config.load()

    print(f"[2/3] 生成预测时间序列 ({start_time} 至 {end_time}, 步长: {freq})...")
    time_series = pd.date_range(start=start_time, end=end_time, freq=freq)
    dates_np = time_series.to_numpy(dtype='datetime64[us]')
    lons_np = np.full(len(dates_np), lon_norm)
    lats_np = np.full(len(dates_np), lat)

    print(f"[3/3] 执行调和分析潮位计算...")
    short_period, long_period, flags = pyfes.evaluate_tide(
        tide_model, dates_np, lons_np, lats_np
    )

    df_result = pd.DataFrame({
        'datetime': time_series,
        'longitude': lon,
        'latitude': lat,
        'tide_short_period_cm': short_period,
        'tide_long_period_cm': long_period,
        'tide_total_cm': short_period + long_period,
        'tide_total_m': (short_period + long_period) / 100.0,
        'quality_flag': flags
    })

    return df_result


if __name__ == '__main__':
    # 示例演示：预测中国长江口 (经度: 122.0°E, 纬度: 31.0°N) 未来 24 小时的潮汐变化
    test_lon, test_lat = 122.0, 31.0
    start = '2026-09-10 00:00:00'
    end = '2026-09-11 00:00:00'

    print(f"=== FES2022b 潮位预测演示 ===")
    print(f"站点坐标: 经度 {test_lon}°, 纬度 {test_lat}°")
    print(f"预测时段: {start} -> {end}")

    res = predict_coastal_tide(
        lon=test_lon,
        lat=test_lat,
        start_time=start,
        end_time=end,
        freq='1h'
    )

    print("\n预测结果预览 (前 10 个时间点):")
    print(res[['datetime', 'tide_total_cm', 'tide_total_m', 'quality_flag']].head(10).to_string(index=False))

    output_csv = os.path.join(os.path.dirname(__file__), 'predicted_tide_example.csv')
    res.to_csv(output_csv, index=False)
    print(f"\n完整预测结果已成功导出至: {output_csv}")
