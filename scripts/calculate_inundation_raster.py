"""
CoastTideX - 潮滩潜在天文潮淹没频率栅格计算工具 (Tidal Flat Inundation Frequency Raster Tool v1.4)

【应用背景】
针对海岸带滨海湿地、红树林宜林带、盐沼植被演替与全球潮滩遥感反演需求，
本工具基于高精度 FES2022b 调和潮汐模型与四大垂直基准转换体系，
将 10m/30m 数字高程模型 (DEM) 转换为整年 (如 2024 年) 潜在天文潮淹没频率空间栅格 (0~100%)。

【核心算法】
默认采用基于自适应控制网格 (Adaptive Tide Control Grid) 与经验互补累积分布函数 (CCDF / 1 - ECDF)
的严密空间插值解算架构，严禁无脑使用中心单点代替全图。
若指定 --single-point-approx，则作为快速预览模式（会输出学术严谨性警告）。
"""

import os
import sys
import argparse
import numpy as np
import rasterio
from pyproj import Transformer

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# 将项目根目录加入模块检索路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from core.tide_engine import FESTidePredictor
from core.datum_engine import DatumTransformer
from core.raster_engine import RasterTideEngine
from core.utils import compute_inundation_frequency


def _run_single_point_approx(
    dem_path: str,
    output_path: str,
    year: int = 2024,
    freq: str = "30min",
    datum_target: str = "egm2008",
    lon: float = None,
    lat: float = None,
    constituents: str = "all",
    block_size: int = 512
):
    print("\n" + "=" * 78)
    print("[WARN] 警告 (SCIENTIFIC WARNING):")
    print("  您显式启用了 [--single-point-approx] 快速预览模式！")
    print("  该模式仅使用单个中心点潮位序列近似代表整幅 DEM，忽略了空间潮波迟后与潮差梯度。")
    print("  对于大范围或长距离潮滩，这会导致显著的系统性淹没频率偏差。")
    print("  科学研究、论文发表与工程定级请务必使用默认的自适应空间控制网格解算模式！")
    print("=" * 78 + "\n")

    if not os.path.exists(dem_path):
        raise FileNotFoundError(f"未找到输入 DEM 文件: {dem_path}")

    with rasterio.open(dem_path) as src_dem:
        profile = src_dem.profile.copy()
        dem_crs = src_dem.crs
        dem_nodata = src_dem.nodata
        bounds = src_dem.bounds
        width = src_dem.width
        height = src_dem.height

        if lon is None or lat is None:
            center_x = (bounds.left + bounds.right) / 2.0
            center_y = (bounds.bottom + bounds.top) / 2.0

            if dem_crs is not None and not dem_crs.is_geographic:
                transformer_to_wgs = Transformer.from_crs(dem_crs, "EPSG:4326", always_xy=True)
                center_lon, center_lat = transformer_to_wgs.transform(center_x, center_y)
            else:
                center_lon, center_lat = center_x, center_y
            ref_lon, ref_lat = float(center_lon), float(center_lat)
        else:
            ref_lon, ref_lat = float(lon), float(lat)

        print(f"[*] 中心参考经纬度: ({ref_lon:.4f}°, {ref_lat:.4f}°)")
        print(f"[*] DEM 栅格规格: 宽度={width}, 高度={height}, 总像元={width * height:,}")

        print(f"[*] 正在解算 {year} 年度潮位序列 (步长={freq})...")
        predictor = FESTidePredictor()
        df_year = predictor.predict_year(
            lon=ref_lon,
            lat=ref_lat,
            year=year,
            freq=freq,
            constituents=constituents,
            source_tz="UTC"
        )

        print(f"[*] 正在统一垂直基准至 [{datum_target.upper()}]...")
        transformer = DatumTransformer()
        datum_res = transformer.convert_tide_datums(
            df_year['tide_total_m'].values,
            ref_lon,
            ref_lat,
            datum_target=datum_target,
            strict=False
        )

        if datum_target.lower() in ['egm', 'egm2008', 'both']:
            water_levels = datum_res['h_egm2008_m']
        elif datum_target.lower() in ['wgs', 'wgs84']:
            water_levels = datum_res['h_wgs84_m']
        elif datum_target.lower() in ['goco', 'goco06s']:
            water_levels = datum_res['h_goco06s_m']
        else:
            water_levels = datum_res['tide_msl_m']

        valid_water = water_levels[np.isfinite(water_levels)]
        if len(valid_water) == 0:
            raise ValueError(f"参考点 ({ref_lon}, {ref_lat}) 水位全为 NaN，请确认该点位于海洋网格内！")

        out_profile = profile.copy()
        out_profile.update({
            'dtype': rasterio.float32,
            'count': 1,
            'compress': 'deflate',
            'predictor': 2,
            'nodata': np.nan
        })

        out_dir = os.path.dirname(os.path.abspath(output_path))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

        engine = RasterTideEngine()
        total_windows = int(np.ceil(width / block_size) * np.ceil(height / block_size))
        win_idx = 0

        print("[*] 正在分块计算 CCDF 淹没频率并写入...")
        with rasterio.open(output_path, 'w', **out_profile) as dst:
            for window in engine.iter_raster_windows(width, height, block_size=block_size):
                dem_chunk = src_dem.read(1, window=window).astype(np.float32)
                if dem_nodata is not None and np.isfinite(dem_nodata):
                    dem_chunk[np.isclose(dem_chunk, dem_nodata)] = np.nan

                freq_chunk = compute_inundation_frequency(
                    water_levels_m=water_levels,
                    terrain_elevations_m=dem_chunk,
                    as_percentage=True
                )
                dst.write(freq_chunk.astype(np.float32), 1, window=window)
                win_idx += 1

            dst.update_tags(
                SOFTWARE="CoastTideX v1.4",
                ENGINE_MODE="single_point_approx",
                TIDE_MODEL="FES2022b",
                INUNDATION_TYPE="potential_astronomical_tidal",
                YEAR=str(year),
                FREQ=freq,
                DATUM=datum_target.upper(),
                REF_COORDS=f"lon={ref_lon:.4f}, lat={ref_lat:.4f}",
                SCIENTIFIC_WARNING="Single center point approximation. Not recommended for publication."
            )

    print(f"[OK] 快速近似计算完成并保存至: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="CoastTideX: 高分辨率 DEM 潜在天文潮淹没频率空间栅格解算工具 v1.4"
    )
    parser.add_argument("--dem", type=str, required=True, help="输入潮滩 DEM GeoTIFF 路径 (高程单位: 米)")
    parser.add_argument("--out", type=str, required=True, help="输出淹没频率 GeoTIFF 路径 (像元值为 0~100%% 淹没率)")
    parser.add_argument("--qc-out", type=str, default=None, help="输出质量控制掩膜 GeoTIFF 路径 (默认: <out>_qc.tif)")
    parser.add_argument("--year", type=int, default=2024, help="潮汐模拟年份 (默认: 2024)")
    parser.add_argument("--freq", type=str, default="30min", help="潮位采样时间步长 (默认: 30min)")
    parser.add_argument("--datum", type=str, default="egm2008", choices=["egm2008", "msl", "wgs84", "goco06s"], help="DEM 对应的高程垂直基准 (默认: egm2008)")
    parser.add_argument("--constituents", type=str, default="all", help="分潮方案 (默认: all，全精度34分潮)")
    parser.add_argument("--initial-spacing", type=float, default=4000.0, help="自适应控制网格初始间距 (米，默认: 4000)")
    parser.add_argument("--min-spacing", type=float, default=500.0, help="自适应控制网格最小间距 (米，默认: 500)")
    parser.add_argument("--tolerance", type=float, default=1.0, help="淹没频率容错阈值 (%%，默认: 1.0)")
    parser.add_argument("--block-size", type=int, default=512, help="2D 分块大小 (默认: 512)")
    parser.add_argument("--non-strict", action="store_true", help="允许基准缺失或近似回退")
    parser.add_argument("--single-point-approx", action="store_true", help="启用中心单点近似模式 (仅建议快速预览，有警告)")
    parser.add_argument("--lon", type=float, default=None, help="[仅单点近似模式] 指定参考经度")
    parser.add_argument("--lat", type=float, default=None, help="[仅单点近似模式] 指定参考纬度")

    args = parser.parse_args()

    if args.single_point_approx:
        _run_single_point_approx(
            dem_path=args.dem,
            output_path=args.out,
            year=args.year,
            freq=args.freq,
            datum_target=args.datum,
            lon=args.lon,
            lat=args.lat,
            constituents=args.constituents,
            block_size=args.block_size
        )
    else:
        print(f"[*] 启动 CoastTideX v1.4 自适应控制网格空间淹没频率解算...")
        print(f"[*] 输入 DEM: {args.dem}")
        print(f"[*] 输出路径: {args.out}")
        print(f"[*] 控制网格: 初始间距={args.initial_spacing}m, 最小间距={args.min_spacing}m, 容差={args.tolerance}%")

        engine = RasterTideEngine()
        summary = engine.calculate_inundation_raster(
            dem_path=args.dem,
            output_path=args.out,
            qc_output_path=args.qc_out,
            year=args.year,
            freq=args.freq,
            dem_datum=args.datum,
            constituents=args.constituents,
            source_tz="UTC",
            initial_control_spacing_m=args.initial_spacing,
            min_control_spacing_m=args.min_spacing,
            inundation_error_tolerance_pct=args.tolerance,
            block_size=args.block_size,
            strict=not args.non_strict,
            progress_callback=lambda p, m: print(f"    -> [{p:3d}%] {m}")
        )
        print(f"[OK] 解算圆满成功！")
        print(f"     有效 DEM 像元数: {summary.valid_pixels:,} / {summary.total_pixels:,}")
        print(f"     控制节点总数: {summary.control_nodes_count:,}")
        print(f"     总计算耗时: {summary.elapsed_seconds:.2f} 秒")
        print(f"     淹没频率栅格: {summary.output_path}")
        print(f"     质量控制掩膜: {summary.qc_output_path}")


if __name__ == '__main__':
    main()
