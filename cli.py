"""
CoastTideX 命令行工具 (Command-Line Interface)
用于脚本批处理、无人值守自动化以及与 GIS 工作流整合。

使用示例:
    # 预测单点
    python cli.py single --lon 122.0 --lat 31.0 --start "2026-09-10 00:00:00" --end "2026-09-11 00:00:00" --step 1h --output output.csv

    # 批量计算
    python cli.py batch --input input_points.csv --lon-col lon --lat-col lat --time-col time --output batch_out.csv
"""

import os
import sys
import argparse
import pandas as pd

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# 将项目根目录加入模块检索路径
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from core.tide_engine import FESTidePredictor
from core.datum_engine import DatumTransformer
from core.utils import export_dataframe


def main():
    parser = argparse.ArgumentParser(description="CoastTideX: 全球海岸带高精度潮位预测与基准转换工具")

    subparsers = parser.add_subparsers(dest="mode", help="运行模式: single (单点) 或 batch (批量)")

    # 单点模式参数
    p_single = subparsers.add_parser("single", help="单点时间序列预测")
    p_single.add_argument("--lon", type=float, required=True, help="目标经度 (-180~180 或 0~360)")
    p_single.add_argument("--lat", type=float, required=True, help="目标纬度 (-90~90)")
    p_single.add_argument("--start", type=str, required=True, help="起始时间 (如 '2026-09-10 00:00:00')")
    p_single.add_argument("--end", type=str, required=True, help="结束时间 (如 '2026-09-11 00:00:00')")
    p_single.add_argument("--step", type=str, default="1h", help="时间步长 (默认: 1h)")
    p_single.add_argument("--constituents", type=str, default="all", choices=["all", "major8"], help="分潮集合")
    p_single.add_argument("--tz", type=str, default="UTC", choices=["UTC", "local"], help="输入时间时区 (UTC 或 local)")
    p_single.add_argument("--output", "-o", type=str, default="predicted_tide.csv", help="输出文件路径")

    # 批量模式参数
    p_batch = subparsers.add_parser("batch", help="批量 CSV 文件点位潮位计算")
    p_batch.add_argument("--input", "-i", type=str, required=True, help="输入 CSV 文件路径")
    p_batch.add_argument("--lon-col", type=str, default="longitude", help="经度列名")
    p_batch.add_argument("--lat-col", type=str, default="latitude", help="纬度列名")
    p_batch.add_argument("--time-col", type=str, default="datetime", help="时间列名")
    p_batch.add_argument("--constituents", type=str, default="all", choices=["all", "major8"], help="分潮集合")
    p_batch.add_argument("--tz", type=str, default="UTC", choices=["UTC", "local"], help="输入时间时区 (UTC 或 local)")
    p_batch.add_argument("--output", "-o", type=str, default="batch_output.csv", help="输出 CSV 路径")

    args = parser.parse_args()

    if not args.mode:
        parser.print_help()
        sys.exit(0)

    predictor = FESTidePredictor()
    transformer = DatumTransformer()

    if args.mode == "single":
        print(f"[*] 启动单点潮位预测: ({args.lon}°, {args.lat}°)")
        print(f"[*] 时段: {args.start} -> {args.end}, 步长: {args.step}, 时区: {args.tz}")

        df = predictor.predict_series(
            lon=args.lon,
            lat=args.lat,
            start_time=args.start,
            end_time=args.end,
            freq=args.step,
            constituents=args.constituents,
            source_tz=args.tz
        )

        print("[*] 严密计算四大垂直基准 (MSL, GOCO06s, EGM2008, WGS84)...")
        datum_res = transformer.convert_tide_datums(
            df['tide_total_m'].values, args.lon, args.lat
        )

        df['tide_msl_m'] = datum_res['tide_msl_m']
        df['mdt_m'] = datum_res['mdt_m']
        df['delta_n_m'] = datum_res['delta_n_m']
        df['n_egm2008_m'] = datum_res['n_egm2008_m']
        df['h_goco06s_m'] = datum_res['h_goco06s_m']
        df['h_egm2008_m'] = datum_res['h_egm2008_m']
        df['h_wgs84_m'] = datum_res['h_wgs84_m']

        if 'quality_flag' in df.columns:
            flags = df['quality_flag'].values
            if (flags == 0).any():
                print("[WARN] 注意: 预测序列中包含质量 Flag=0 的点（陆地或无有效潮汐解），潮位及高程为 NaN！")
            elif (flags < 0).any():
                print("[WARN] 提示: 预测序列中包含近岸动力学外推点（Flag < 0），潮位精度可能低于开阔海域！")

        export_dataframe(df, args.output)
        print(f"[OK] 预测成功，包含完整四大基准列，结果已保存至: {args.output}")

    elif args.mode == "batch":
        print(f"[*] 读取批量输入文件: {args.input}")
        df_records = pd.read_csv(args.input)
        print(f"[*] 总记录数: {len(df_records)}, 时区: {args.tz}")

        df_out = predictor.predict_batch(
            df_records,
            lon_col=args.lon_col,
            lat_col=args.lat_col,
            time_col=args.time_col,
            constituents=args.constituents,
            source_tz=args.tz
        )

        print("[*] 向量化批量计算四大垂直基准...")
        lons = df_out[args.lon_col].astype(float).values
        lats = df_out[args.lat_col].astype(float).values
        tide_msl = df_out['tide_total_m'].values

        datum_res = transformer.convert_tide_datums(tide_msl, lons, lats)
        df_out['tide_msl_m'] = datum_res['tide_msl_m']
        df_out['mdt_m'] = datum_res['mdt_m']
        df_out['delta_n_m'] = datum_res['delta_n_m']
        df_out['n_egm2008_m'] = datum_res['n_egm2008_m']
        df_out['h_goco06s_m'] = datum_res['h_goco06s_m']
        df_out['h_egm2008_m'] = datum_res['h_egm2008_m']
        df_out['h_wgs84_m'] = datum_res['h_wgs84_m']

        if 'quality_flag' in df_out.columns:
            flags = df_out['quality_flag'].values
            n_zero = int((flags == 0).sum())
            n_neg = int((flags < 0).sum())
            if n_zero > 0:
                print(f"[WARN] 注意: 批量解算中发现 {n_zero} 个无有效数据/陆地点 (Flag=0)，对应值为 NaN。")
            if n_neg > 0:
                print(f"[WARN] 提示: 批量解算中包含 {n_neg} 个近岸外推点 (Flag < 0)。")

        export_dataframe(df_out, args.output)
        print(f"[OK] 批量解算完成，已导出至: {args.output}")


if __name__ == '__main__':
    main()
