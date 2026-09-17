"""
CoastTideX 命令行工具 (Command-Line Interface v1.6)
用于脚本批处理、无人值守自动化、年度连续模拟、空间栅格潮位解算与淹没频率分析。

使用示例:
    # 自定义时段单点预测
    python cli.py single --lon 122.0 --lat 31.0 --start "2026-09-10 00:00:00" --end "2026-09-11 00:00:00" --step 1h --output output.csv

    # 2024 整年高密度预测 (30min步长，半开区间严格 17,568 样本点)
    python cli.py single --lon 122.0 --lat 31.0 --year 2024 --step 30min --output tide_2024.csv

    # 批量计算
    python cli.py batch --input input_points.csv --lon-col lon --lat-col lat --time-col time --output batch_out.csv

    # 空间栅格单时刻解算 (Snapshot Raster)
    python cli.py raster snapshot --input water_boundary.tif --output tide_snapshot.tif --time "2024-06-15 12:00:00" --datum egm2008

    # 潮滩 DEM 潜在天文潮淹没频率解算 (Annual Inundation Frequency)
    python cli.py raster inundation --dem coastal_dem.tif --output inundation_freq.tif --year 2024 --step 30min --dem-datum egm2008
"""

import os
import sys
import argparse
import numpy as np
import pandas as pd
from typing import Optional, List

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
from core.raster_engine import RasterTideEngine
from core.utils import export_dataframe


def main(args_list: Optional[List[str]] = None):
    parser = argparse.ArgumentParser(description="CoastTideX: 全球海岸带高精度潮位预测与基准转换工具 v1.6")

    subparsers = parser.add_subparsers(dest="mode", help="运行模式: single (单点), batch (批量), 或 raster (空间栅格)")

    # 1. 单点模式参数
    p_single = subparsers.add_parser("single", help="单点时间序列预测 (支持自定义时段或整年模式)")
    p_single.add_argument("--lon", type=float, required=True, help="目标经度 (-180~180 或 0~360)")
    p_single.add_argument("--lat", type=float, required=True, help="目标纬度 (-90~90)")
    p_single.add_argument("--start", type=str, default=None, help="起始时间 (如 '2026-09-10 00:00:00')")
    p_single.add_argument("--end", type=str, default=None, help="结束时间 (如 '2026-09-11 00:00:00')")
    p_single.add_argument("--year", type=int, default=None, help="快捷整年预测年份 (如 2024，指定时自动采用半开区间整年模式)")
    p_single.add_argument("--step", type=str, default="1h", help="时间步长 (默认: 1h)")
    p_single.add_argument("--inclusive", type=str, default="both", choices=["both", "left", "right", "neither"], help="时间区间包含模式 (默认: both，整年模式下自动为 left)")
    p_single.add_argument("--constituents", type=str, default="all", choices=["all", "major8"], help="分潮集合")
    p_single.add_argument("--tz", type=str, default="UTC", choices=["UTC", "local"], help="输入时间时区 (UTC 或 local)")
    p_single.add_argument("--datum", type=str, default="both", choices=["msl", "egm2008", "both", "all", "wgs84", "mdt_ref"], help="目标垂直基准 (默认: both -> MSL + EGM2008)")
    p_single.add_argument("--non-strict", action="store_true", help="允许基准缺失或近似多边形回退 (默认对非 MSL 基准为严格模式)")
    p_single.add_argument("--output", "-o", type=str, default="predicted_tide.csv", help="输出文件路径")

    # 2. 批量模式参数
    p_batch = subparsers.add_parser("batch", help="批量 CSV 文件点位潮位计算")
    p_batch.add_argument("--input", "-i", type=str, required=True, help="输入 CSV 文件路径")
    p_batch.add_argument("--lon-col", type=str, default="longitude", help="经度列名")
    p_batch.add_argument("--lat-col", type=str, default="latitude", help="纬度列名")
    p_batch.add_argument("--time-col", type=str, default="datetime", help="时间列名")
    p_batch.add_argument("--constituents", type=str, default="all", choices=["all", "major8"], help="分潮集合")
    p_batch.add_argument("--tz", type=str, default="UTC", choices=["UTC", "local"], help="输入时间时区 (UTC 或 local)")
    p_batch.add_argument("--datum", type=str, default="both", choices=["msl", "egm2008", "both", "all", "wgs84", "mdt_ref"], help="目标垂直基准 (默认: both -> MSL + EGM2008)")
    p_batch.add_argument("--non-strict", action="store_true", help="允许基准缺失或近似多边形回退 (默认对非 MSL 基准为严格模式)")
    p_batch.add_argument("--output", "-o", type=str, default="batch_output.csv", help="输出 CSV 路径")

    # 3. 空间栅格模式参数
    p_raster = subparsers.add_parser("raster", help="空间栅格解算模式 (snapshot 单时刻空间潮位 / inundation 潜在淹没频率)")
    raster_subparsers = p_raster.add_subparsers(dest="raster_submode", help="栅格子模式: snapshot 或 inundation")

    # 3.1 栅格单时刻快照
    p_snap = raster_subparsers.add_parser("snapshot", help="单时刻空间潮位 / 水面高程 GeoTIFF 解算")
    p_snap.add_argument("--input", "-i", "--dem", type=str, required=True, help="输入 GeoTIFF / DEM 路径")
    p_snap.add_argument("--output", "-o", type=str, required=True, help="输出 GeoTIFF 路径")
    p_snap.add_argument("--time", type=str, required=True, help="解算时刻 (如 '2024-06-15 12:00:00')")
    p_snap.add_argument("--datum", type=str, default="egm2008", choices=["egm2008", "msl", "goco06s", "wgs84"], help="目标垂直基准 (默认: egm2008)")
    p_snap.add_argument("--constituents", type=str, default="all", help="分潮集合 (all 或 major8)")
    p_snap.add_argument("--tz", type=str, default="UTC", choices=["UTC", "local"], help="时刻时区 (默认: UTC)")
    p_snap.add_argument("--block-size", type=int, default=512, help="2D 分块大小 (默认: 512)")
    p_snap.add_argument("--non-strict", action="store_true", help="允许基准缺失或近似回退")

    # 3.2 栅格潜在淹没频率
    p_inund = raster_subparsers.add_parser("inundation", help="自适应控制网格潜在天文潮淹没频率 GeoTIFF 解算")
    p_inund.add_argument("--dem", "-i", type=str, required=True, help="输入 DEM GeoTIFF 路径")
    p_inund.add_argument("--output", "-o", type=str, required=True, help="输出淹没频率 GeoTIFF 路径")
    p_inund.add_argument("--qc-output", type=str, default=None, help="输出质量掩膜 GeoTIFF 路径 (默认: <output>_qc.tif)")
    p_inund.add_argument("--year", type=int, default=2024, help="预测年份 (默认: 2024)")
    p_inund.add_argument("--start", type=str, default=None, help="自定义起始时间")
    p_inund.add_argument("--end", type=str, default=None, help="自定义结束时间")
    p_inund.add_argument("--step", type=str, default="30min", help="采样间隔 (默认: 30min)")
    p_inund.add_argument("--dem-datum", type=str, default="egm2008", choices=["egm2008", "msl", "goco06s", "wgs84"], help="DEM 高程基准 (默认: egm2008)")
    p_inund.add_argument("--constituents", type=str, default="all", help="分潮集合 (默认: all)")
    p_inund.add_argument("--tz", type=str, default="UTC", choices=["UTC", "local"], help="时间时区 (默认: UTC)")
    p_inund.add_argument("--initial-spacing", type=float, default=4000.0, help="初始控制网格间距 (米，默认: 4000)")
    p_inund.add_argument("--min-spacing", type=float, default=500.0, help="最小控制网格间距 (米，默认: 500)")
    p_inund.add_argument("--tolerance", type=float, default=1.0, help="淹没频率容错阈值 (%%，默认: 1.0)")
    p_inund.add_argument("--block-size", type=int, default=512, help="2D 分块大小 (默认: 512)")
    p_inund.add_argument("--non-strict", action="store_true", help="允许基准缺失或近似回退")
    p_inund.add_argument("--target-mode", type=str, default="intertidal", choices=["intertidal", "standard"], help="目标感知模式 (默认: intertidal)")
    p_inund.add_argument("--export-cache", type=str, default=None, help="可选导出 Tide Cache (*_tide.nc)")

    # 3.3 栅格潜在天文潮露出时间域分析 (v1.6)
    p_exposure = raster_subparsers.add_parser("exposure", help="潜在天文潮露出时间域产品计算 (露出时长/频率/事件分析)")
    p_exposure.add_argument("--dem", "-i", type=str, required=True, help="输入 DEM GeoTIFF 路径")
    p_exposure.add_argument("--output-dir", "-o", type=str, default=None, help="输出产品目录 (默认与 DEM 同级)")
    p_exposure.add_argument("--cache", type=str, default=None, help="可选已有 Tide Cache (*_tide.nc)，若提供则零 FES 计算")
    p_exposure.add_argument("--year", type=int, default=2024, help="预测年份 (默认: 2024)")
    p_exposure.add_argument("--start", type=str, default=None, help="自定义起始时间")
    p_exposure.add_argument("--end", type=str, default=None, help="自定义结束时间")
    p_exposure.add_argument("--step", type=str, default="30min", help="采样间隔 (默认: 30min)")
    p_exposure.add_argument("--dem-datum", type=str, default="egm2008", choices=["egm2008", "msl", "goco06s", "wgs84"], help="DEM 高程基准 (默认: egm2008)")
    p_exposure.add_argument("--constituents", type=str, default="all", help="分潮集合 (默认: all)")
    p_exposure.add_argument("--tz", type=str, default="UTC", choices=["UTC", "local"], help="时间时区 (默认: UTC)")
    p_exposure.add_argument("--initial-spacing", type=float, default=4000.0, help="初始控制网格间距 (米，默认: 4000)")
    p_exposure.add_argument("--min-spacing", type=float, default=500.0, help="最小控制网格间距 (米，默认: 500)")
    p_exposure.add_argument("--tolerance", type=float, default=1.0, help="容错阈值 (%%，默认: 1.0)")
    p_exposure.add_argument("--block-size", type=int, default=512, help="2D 分块大小 (默认: 512)")
    p_exposure.add_argument("--time-chunk", type=int, default=1000, help="时间流式分块步数 (默认: 1000)")
    p_exposure.add_argument("--target-mode", type=str, default="intertidal", choices=["intertidal", "standard"], help="目标区域模式 (默认: intertidal)")
    p_exposure.add_argument("--overwrite", action="store_true", help="强制覆盖已存在输出")

    # 3.4 批量潮间带栅格解算 (v1.5/v1.6)
    p_batch_raster = raster_subparsers.add_parser("batch", aliases=["batch-intertidal"], help="批量潮间带栅格解算与 Tide Cache 流程 (v1.5)")
    p_batch_raster.add_argument("--input-folder", "-i", type=str, required=True, help="输入 GeoTIFF 文件夹路径")
    p_batch_raster.add_argument("--output-folder", "-o", type=str, default=None, help="输出文件夹路径 (默认: <input_folder>/CoastTideX_output)")
    p_batch_raster.add_argument("--mode", type=str, default="tide-inundation", choices=["tide", "tide-inundation", "inundation-from-cache", "tide-exposure", "exposure-from-cache", "all"], help="解算模式 (默认: tide-inundation)")
    p_batch_raster.add_argument("--year", type=int, default=2024, help="预测年份 (默认: 2024)")
    p_batch_raster.add_argument("--start", type=str, default=None, help="自定义起始时间")
    p_batch_raster.add_argument("--end", type=str, default=None, help="自定义结束时间")
    p_batch_raster.add_argument("--step", type=str, default="30min", help="采样间隔 (默认: 30min)")
    p_batch_raster.add_argument("--dem-datum", type=str, default="egm2008", choices=["egm2008", "msl", "goco06s", "wgs84"], help="DEM 高程基准 (默认: egm2008)")
    p_batch_raster.add_argument("--constituents", type=str, default="all", help="分潮集合 (默认: all)")
    p_batch_raster.add_argument("--target-mode", type=str, default="intertidal", choices=["intertidal", "standard"], help="目标区域模式 (默认: intertidal)")
    p_batch_raster.add_argument("--initial-spacing", type=float, default=4000.0, help="初始控制网格间距 (米，默认: 4000)")
    p_batch_raster.add_argument("--min-spacing", type=float, default=500.0, help="最小控制网格间距 (米，默认: 500)")
    p_batch_raster.add_argument("--tolerance", type=float, default=1.0, help="淹没频率容错阈值 (%%，默认: 1.0)")
    p_batch_raster.add_argument("--block-size", type=int, default=512, help="2D 分块大小 (默认: 512)")
    p_batch_raster.add_argument("--recursive", action="store_true", help="是否递归扫描子目录")
    p_batch_raster.add_argument("--existing-policy", type=str, default=None, choices=["resume", "error_if_exists", "overwrite"], help="现有输出处理策略 (默认: resume)")
    p_batch_raster.add_argument("--resume", action="store_true", default=False, help="显式指定断点恢复策略")
    p_batch_raster.add_argument("--no-resume", action="store_true", help="禁用断点恢复")
    p_batch_raster.add_argument("--overwrite", action="store_true", help="强制覆盖已存在输出")
    p_batch_raster.add_argument("--non-strict", action="store_true", help="允许基准缺失或近似回退")

    args = parser.parse_args(args_list)

    if not args.mode:
        parser.print_help()
        sys.exit(0)

    predictor = FESTidePredictor()
    transformer = DatumTransformer()

    if args.mode == "single":
        print(f"[*] 启动单点潮位预测: ({args.lon}°, {args.lat}°)")

        if args.year is not None:
            print(f"[*] 运行模式: 整年预测模式 ({args.year} 年), 步长: {args.step}, 时区: {args.tz}")
            df = predictor.predict_year(
                lon=args.lon,
                lat=args.lat,
                year=args.year,
                freq=args.step,
                constituents=args.constituents,
                source_tz=args.tz,
                datum_mode=None
            )
        else:
            if not args.start or not args.end:
                print("[ERROR] 自定义时段模式必须同时提供 --start 和 --end 参数，或提供 --year 指定整年！")
                sys.exit(1)
            print(f"[*] 运行模式: 自定义时段 ({args.start} -> {args.end}), 步长: {args.step}, 包含语义: {args.inclusive}, 时区: {args.tz}")
            df = predictor.predict_series(
                lon=args.lon,
                lat=args.lat,
                start_time=args.start,
                end_time=args.end,
                freq=args.step,
                inclusive=args.inclusive,
                constituents=args.constituents,
                source_tz=args.tz
            )

        datum_target = args.datum.lower()
        if datum_target == "msl":
            # 纯 MSL 模式: 零依赖外部基准栅格，绝不调用 DatumTransformer
            df['tide_msl_m'] = df['tide_total_m']
            df['mdt_m'] = np.nan
            df['delta_n_m'] = np.nan
            df['n_egm2008_m'] = np.nan
            df['h_mdt_ref_m'] = np.nan
            df['h_goco06s_m'] = np.nan
            df['h_egm2008_m'] = np.nan
            df['h_wgs84_m'] = np.nan
            df['datum_ref_geoid'] = 'MSL'
            df['qc_warning'] = 'NORMAL'
        else:
            strict_mode = not args.non_strict
            datum_desc_map = {
                "egm2008": "MSL + EGM2008",
                "both": "MSL + EGM2008",
                "mdt_ref": "MSL + MDT_REF",
                "wgs84": "MSL + EGM2008 + WGS84",
                "all": "MSL + MDT_REF + EGM2008 + WGS84"
            }
            datum_desc = datum_desc_map.get(datum_target, datum_target.upper())
            print(f"[*] 严密计算目标垂直基准 ({datum_desc}, strict={strict_mode})...")
            datum_res = transformer.convert_tide_datums(
                df['tide_total_m'].values, args.lon, args.lat,
                datum_target=datum_target, strict=strict_mode
            )

            df['tide_msl_m'] = datum_res['tide_msl_m']
            df['mdt_m'] = datum_res['mdt_m']
            df['delta_n_m'] = datum_res['delta_n_m']
            df['n_egm2008_m'] = datum_res['n_egm2008_m']
            df['h_mdt_ref_m'] = datum_res['h_mdt_ref_m']
            df['h_goco06s_m'] = datum_res['h_goco06s_m']
            df['h_egm2008_m'] = datum_res['h_egm2008_m']
            df['h_wgs84_m'] = datum_res['h_wgs84_m']
            df['datum_ref_geoid'] = datum_res['datum_ref_geoid']
            df['qc_warning'] = datum_res['qc_warning']

        if 'quality_flag' in df.columns:
            flags = df['quality_flag'].values
            if (flags == 0).any():
                print("[WARN] 注意: 预测序列中包含质量 Flag=0 的点（陆地或无有效潮汐解），潮位及高程为 NaN！")
            elif (flags < 0).any():
                print("[WARN] 提示: 预测序列中包含近岸动力学外推点（Flag < 0），潮位精度可能低于开阔海域！")

        export_dataframe(df, args.output)
        print(f"[OK] 预测成功，共生成 {len(df):,} 行记录，结果已保存至: {args.output}")

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

        datum_target = args.datum.lower()
        if datum_target == "msl":
            df_out['tide_msl_m'] = df_out['tide_total_m']
            df_out['mdt_m'] = np.nan
            df_out['delta_n_m'] = np.nan
            df_out['n_egm2008_m'] = np.nan
            df_out['h_mdt_ref_m'] = np.nan
            df_out['h_goco06s_m'] = np.nan
            df_out['h_egm2008_m'] = np.nan
            df_out['h_wgs84_m'] = np.nan
            df_out['datum_ref_geoid'] = 'MSL'
            df_out['qc_warning'] = 'NORMAL'
        else:
            strict_mode = not args.non_strict
            datum_desc_map = {
                "egm2008": "MSL + EGM2008",
                "both": "MSL + EGM2008",
                "mdt_ref": "MSL + MDT_REF",
                "wgs84": "MSL + EGM2008 + WGS84",
                "all": "MSL + MDT_REF + EGM2008 + WGS84"
            }
            datum_desc = datum_desc_map.get(datum_target, datum_target.upper())
            print(f"[*] 向量化批量计算目标垂直基准 ({datum_desc}, strict={strict_mode})...")
            lons = df_out[args.lon_col].astype(float).values
            lats = df_out[args.lat_col].astype(float).values
            tide_msl = df_out['tide_total_m'].values

            datum_res = transformer.convert_tide_datums(
                tide_msl, lons, lats,
                datum_target=datum_target, strict=strict_mode
            )
            df_out['tide_msl_m'] = datum_res['tide_msl_m']
            df_out['mdt_m'] = datum_res['mdt_m']
            df_out['delta_n_m'] = datum_res['delta_n_m']
            df_out['n_egm2008_m'] = datum_res['n_egm2008_m']
            df_out['h_mdt_ref_m'] = datum_res['h_mdt_ref_m']
            df_out['h_goco06s_m'] = datum_res['h_goco06s_m']
            df_out['h_egm2008_m'] = datum_res['h_egm2008_m']
            df_out['h_wgs84_m'] = datum_res['h_wgs84_m']
            df_out['datum_ref_geoid'] = datum_res['datum_ref_geoid']
            df_out['qc_warning'] = datum_res['qc_warning']

        if 'quality_flag' in df_out.columns:
            flags = df_out['quality_flag'].values
            n_zero = int((flags == 0).sum())
            n_neg = int((flags < 0).sum())
            if n_zero > 0:
                print(f"[WARN] 注意: 批量解算中发现 {n_zero} 个无有效数据/陆地点 (Flag=0)，对应值为 NaN。")
            if n_neg > 0:
                print(f"[WARN] 提示: 批量解算中包含 {n_neg} 个近岸外推点 (Flag < 0)。")

        export_dataframe(df_out, args.output)
        print(f"[OK] 批量解算完成，共生成 {len(df_out):,} 行记录，已导出至: {args.output}")

    elif args.mode == "raster":
        if not getattr(args, 'raster_submode', None):
            p_raster.print_help()
            sys.exit(0)

        raster_engine = RasterTideEngine()

        if args.raster_submode == "snapshot":
            print(f"[*] 启动单时刻空间潮位解算 (Snapshot Raster Mode)...")
            print(f"[*] 输入栅格: {args.input}")
            info = raster_engine.inspect_raster(args.input, compute_valid_count=False)
            print(f"[*] 栅格规格: {info.width} × {info.height}, 坐标系: {info.crs}")
            print(f"[*] 空间分辨率: {info.formatted_resolution}")
            print(f"[*] 输出路径: {args.output}")
            print(f"[*] 解算时刻: {args.time} ({args.tz}), 目标基准: {args.datum.upper()}")

            summary = raster_engine.calculate_snapshot_raster(
                input_raster_path=args.input,
                output_raster_path=args.output,
                timestamp=args.time,
                datum_target=args.datum,
                constituents=args.constituents,
                source_tz=args.tz,
                block_size=args.block_size,
                strict=not args.non_strict,
                progress_callback=lambda p, m: print(f"    -> [{p:3d}%] {m}")
            )
            print(f"[OK] 单时刻空间潮位解算成功！")
            print(f"     有效解算像元数: {summary.valid_pixels:,} / {summary.total_pixels:,}")
            print(f"     计算耗时: {summary.elapsed_seconds:.2f} 秒")
            print(f"     输出文件: {summary.output_path}")

        elif args.raster_submode == "inundation":
            print(f"[*] 启动自适应控制网格潜在天文潮淹没频率解算...")
            print(f"[*] 输入 DEM: {args.dem}")
            info = raster_engine.inspect_raster(args.dem, compute_valid_count=False)
            print(f"[*] DEM 规格: {info.width} × {info.height}, 坐标系: {info.crs}")
            print(f"[*] 空间分辨率: {info.formatted_resolution}")
            print(f"[*] 输出路径: {args.output}")
            print(f"[*] 采样间隔: {args.step}, DEM基准: {args.dem_datum.upper()}")
            print(f"[*] 自适应网格: 初始间距={args.initial_spacing}m, 最小间距={args.min_spacing}m, 容差={args.tolerance}%")

            summary = raster_engine.calculate_inundation_raster(
                dem_path=args.dem,
                output_path=args.output,
                qc_output_path=args.qc_output,
                year=args.year,
                start_time=args.start,
                end_time=args.end,
                freq=args.step,
                dem_datum=args.dem_datum,
                constituents=args.constituents,
                source_tz=args.tz,
                initial_control_spacing_m=args.initial_spacing,
                min_control_spacing_m=args.min_spacing,
                inundation_error_tolerance_pct=args.tolerance,
                block_size=args.block_size,
                strict=not args.non_strict,
                progress_callback=lambda p, m: print(f"    -> [{p:3d}%] {m}"),
                target_mode=args.target_mode,
                export_tide_cache_path=args.export_cache
            )
            print(f"[OK] 潜在天文潮淹没频率解算成功！")
            print(f"     有效 DEM 像元数: {summary.valid_pixels:,} / {summary.total_pixels:,}")
            print(f"     控制节点总数: {summary.control_nodes_count:,}")
            print(f"     计算耗时: {summary.elapsed_seconds:.2f} 秒")
            print(f"     淹没频率栅格: {summary.output_path}")
            print(f"     质量控制掩膜: {summary.qc_output_path}")

        elif args.raster_submode == "exposure":
            print(f"[*] 启动潜在天文潮露出时间域栅格产品解算 (v1.6)...")
            print(f"[*] 输入 DEM: {args.dem}")
            info = raster_engine.inspect_raster(args.dem, compute_valid_count=False)
            print(f"[*] DEM 规格: {info.width} × {info.height}, 坐标系: {info.crs}")
            print(f"[*] 空间分辨率: {info.formatted_resolution}")
            print(f"[*] 采样间隔: {args.step}, DEM基准: {args.dem_datum.upper()}")

            if args.cache:
                print(f"[*] 使用已有 Tide Cache (零 FES 重复调用): {args.cache}")
                from core.tide_cache import calculate_exposure_from_tide_cache
                exp_res = calculate_exposure_from_tide_cache(
                    dem_path=args.dem,
                    cache_path=args.cache,
                    output_dir=args.output_dir,
                    block_size=args.block_size,
                    time_chunk_size=args.time_chunk,
                    allow_overwrite=args.overwrite,
                    progress_callback=lambda p, m: print(f"    -> [{p:3d}%] {m}")
                )
            else:
                exp_res = raster_engine.calculate_exposure_raster(
                    dem_path=args.dem,
                    output_dir=args.output_dir,
                    year=args.year,
                    start_time=args.start,
                    end_time=args.end,
                    freq=args.step,
                    dem_datum=args.dem_datum,
                    constituents=args.constituents,
                    source_tz=args.tz,
                    initial_control_spacing_m=args.initial_spacing,
                    min_control_spacing_m=args.min_spacing,
                    inundation_error_tolerance_pct=args.tolerance,
                    block_size=args.block_size,
                    time_chunk_size=args.time_chunk,
                    target_mode=args.target_mode,
                    allow_overwrite=args.overwrite,
                    progress_callback=lambda p, m: print(f"    -> [{p:3d}%] {m}")
                )
            print(f"[OK] 潜在天文潮露出时间域产物反演成功！")
            print(f"     解算耗时: {exp_res.get('elapsed_seconds', 0.0)} 秒")
            paths = exp_res.get('products')
            if paths:
                print(f"     露出比例 (%):        {paths.exposure_fraction_path}")
                print(f"     累计露出时长 (h):     {paths.exposure_duration_h_path}")
                print(f"     最长连续露出 (h):     {paths.exposure_max_continuous_h_path}")
                print(f"     平均事件时长 (h):     {paths.exposure_mean_event_h_path}")
                print(f"     事件发生次数:         {paths.exposure_event_count_path}")
                print(f"     有效时间覆盖率 (%):   {paths.exposure_valid_time_fraction_path}")
                print(f"     质量控制掩膜 (QC):    {paths.exposure_qc_path}")

        elif args.raster_submode in ["batch", "batch-intertidal"]:
            from core.batch_raster_engine import BatchRasterEngine, ExistingOutputPolicy, normalize_existing_output_policy
            
            # 防御性校验: 严禁 --resume 与 --overwrite 同时指定
            if args.resume and args.overwrite:
                print("[Error] 参数冲突: --resume 与 --overwrite 不能同时指定！请指定单一确定的策略。", file=sys.stderr)
                sys.exit(2)

            if args.existing_policy:
                eff_policy = normalize_existing_output_policy(existing_policy=args.existing_policy)
            elif args.overwrite:
                eff_policy = ExistingOutputPolicy.OVERWRITE
            elif args.no_resume:
                eff_policy = ExistingOutputPolicy.ERROR_IF_EXISTS
            else:
                eff_policy = ExistingOutputPolicy.RESUME

            batch_engine = BatchRasterEngine(raster_engine=raster_engine)
            print(f"[*] 启动批量潮间带栅格解算任务...")
            print(f"[*] 输入目录: {args.input_folder}")
            if args.mode == "inundation-from-cache":
                print(f"[*] 运行模式: inundation-from-cache (Tide Cache is read-only input)")
            else:
                print(f"[*] 运行模式: {args.mode}")
            print(f"[*] Existing output policy: {eff_policy.value}")
            print(f"[*] 采样间隔: {args.step}, 目标模式: {args.target_mode}")

            def _cli_batch_prog(ov, ti, f, m, c):
                if f:
                    print(f"    [{ov:3d}%] {f} [{ti:3d}%] {m}")
                else:
                    print(f"    [{ov:3d}%] {m}")

            res = batch_engine.run_batch(
                input_folder=args.input_folder,
                output_folder=args.output_folder,
                job_mode=args.mode,
                year=args.year,
                start_time=args.start,
                end_time=args.end,
                freq=args.step,
                dem_datum=args.dem_datum,
                constituents=args.constituents,
                target_mode=args.target_mode,
                initial_control_spacing_m=args.initial_spacing,
                min_control_spacing_m=args.min_spacing,
                inundation_error_tolerance_pct=args.tolerance,
                block_size=args.block_size,
                strict=not args.non_strict,
                recursive=args.recursive,
                existing_policy=eff_policy,
                progress_callback=_cli_batch_prog
            )
            print(f"[OK] 批量任务执行完毕！")
            print(f"     输出目录: {res['output_folder']}")
            print(f"     任务清单 (JSON): {res['manifest_json']}")
            print(f"     任务清单 (CSV):  {res['manifest_csv']}")
            print(f"     统计结果: 完成 {res['counts']['completed']}, 失败 {res['counts']['failed']}, 跳过 {res['counts']['skipped']}, 取消 {res['counts']['cancelled']}")


if __name__ == '__main__':
    main()

