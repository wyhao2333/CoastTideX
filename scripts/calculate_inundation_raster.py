"""
CoastTideX - 潮滩潜在天文潮淹没频率栅格计算工具 (Tidal Flat Inundation Frequency Raster Tool)

【应用背景】
针对海岸带滨海湿地、红树林宜林带、盐沼植被演替与全球潮滩遥感反演需求，
本工具基于高精度 FES2022b 调和潮汐模型与四大垂直基准转换体系，
将 10m/30m 数字高程模型 (DEM) 快速转换为整年 (如 2024 年) 潜在天文潮淹没频率空间栅格 (0~100%)。

【核心算法】
采用向量化经验互补累积分布函数 (CCDF / 1 - ECDF) 与 np.searchsorted 二分检索，
支持百万至千万像元级别的大范围 DEM 极速解算与分块流式写入。
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
from core.utils import compute_inundation_frequency


def compute_inundation_raster(
    dem_path: str,
    output_path: str,
    year: int = 2024,
    freq: str = "30min",
    datum_target: str = "egm2008",
    lon: float = None,
    lat: float = None,
    constituents: str = "all",
    block_size: int = 2048
):
    print(f"[*] 读取输入 DEM: {dem_path}")
    if not os.path.exists(dem_path):
        raise FileNotFoundError(f"未找到输入 DEM 文件: {dem_path}")

    with rasterio.open(dem_path) as src_dem:
        profile = src_dem.profile.copy()
        dem_crs = src_dem.crs
        dem_nodata = src_dem.nodata
        bounds = src_dem.bounds
        width = src_dem.width
        height = src_dem.height

        # 若未指定参考经纬度，自动推求 DEM 中心点的 WGS84 经纬度
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

        print(f"[*] 潮滩区域参考经纬度: ({ref_lon:.4f}°, {ref_lat:.4f}°)")
        print(f"[*] DEM 栅格规格: 宽度={width}, 高度={height}, 总像元={width*height:,}")

        # 1. 模拟整年连续潮位序列
        print(f"[*] 正在模拟 {year} 年度高密度潮位序列 (步长={freq}, 34分潮全精度)...")
        predictor = FESTidePredictor()
        df_year = predictor.predict_year(
            lon=ref_lon,
            lat=ref_lat,
            year=year,
            freq=freq,
            constituents=constituents,
            source_tz="UTC"
        )
        print(f"[+] 潮位时序模拟完成，共 {len(df_year):,} 个时间步长点。")

        # 2. 转换到指定垂直基准 (如 EGM2008)
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
            level_name = "EGM2008 绝对海拔正高"
        elif datum_target.lower() in ['wgs', 'wgs84']:
            water_levels = datum_res['h_wgs84_m']
            level_name = "WGS84 几何空间椭球高"
        elif datum_target.lower() in ['goco', 'goco06s']:
            water_levels = datum_res['h_goco06s_m']
            level_name = "GOCO06s 海面高"
        else:
            water_levels = datum_res['tide_msl_m']
            level_name = "MSL 相对海平面"

        valid_water = water_levels[np.isfinite(water_levels)]
        if len(valid_water) == 0:
            raise ValueError(f"参考点 ({ref_lon}, {ref_lat}) 计算出的水面高度全为 NaN，请确认该点位于有效海洋网格内！")

        print(f"[+] 水位统计 [{level_name}]: 极小值={np.min(valid_water):+.2f} m, 极大值={np.max(valid_water):+.2f} m, 均值={np.mean(valid_water):+.2f} m")

        # 3. 准备输出 GeoTIFF
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

        print(f"[*] 开始逐块计算潮滩像元潜在淹没频率 (CCDF / 1-ECDF)...")
        with rasterio.open(output_path, 'w', **out_profile) as dst:
            n_row_blocks = int(np.ceil(height / block_size))
            for r_idx in range(n_row_blocks):
                r_start = r_idx * block_size
                r_end = min(r_start + block_size, height)
                h_chunk = r_end - r_start

                window = rasterio.windows.Window(col_off=0, row_off=r_start, width=width, height=h_chunk)
                dem_chunk = src_dem.read(1, window=window).astype(np.float32)

                # 处理原始 NoData
                if dem_nodata is not None and np.isfinite(dem_nodata):
                    dem_chunk[np.isclose(dem_chunk, dem_nodata)] = np.nan

                freq_chunk = compute_inundation_frequency(
                    water_levels_m=water_levels,
                    terrain_elevations_m=dem_chunk,
                    as_percentage=True
                )

                dst.write(freq_chunk.astype(np.float32), 1, window=window)
                pct_done = (r_idx + 1) / n_row_blocks * 100.0
                print(f"    -> 进度: {pct_done:.1f}% (行 {r_start}~{r_end}/{height})")

            dst.update_tags(
                TITLE="Tidal Flat Inundation Frequency Raster",
                YEAR=str(year),
                FREQ=freq,
                DATUM=datum_target.upper(),
                TIDE_MODEL="FES2022b",
                REF_COORDS=f"lon={ref_lon:.4f}, lat={ref_lat:.4f}",
                UNIT="Percentage (0-100%)",
                ALGORITHM="Vectorized Complementary Empirical Cumulative Distribution Function (CCDF)",
                SOFTWARE="CoastTideX v1.3"
            )

    print(f"[OK] 淹没频率栅格计算完成并已保存至: {output_path} (文件大小: {os.path.getsize(output_path)/(1024*1024):.2f} MB)")


def main():
    parser = argparse.ArgumentParser(description="根据高分辨率 DEM 与长序列潮位模拟计算潮滩潜在天文潮淹没频率栅格")
    parser.add_argument("--dem", type=str, required=True, help="输入潮滩 DEM GeoTIFF 路径 (高程单位: 米)")
    parser.add_argument("--out", type=str, required=True, help="输出淹没频率 GeoTIFF 路径 (像元值为 0~100%% 淹没率)")
    parser.add_argument("--year", type=int, default=2024, help="潮汐模拟年份 (默认: 2024)")
    parser.add_argument("--freq", type=str, default="30min", help="潮位采样时间步长 (默认: 30min)")
    parser.add_argument("--datum", type=str, default="egm2008", choices=["egm2008", "msl", "wgs84", "goco06s"], help="DEM 对应的高程垂直基准 (默认: egm2008)")
    parser.add_argument("--lon", type=float, default=None, help="参考水文经度 (若未指定则自动采用 DEM 中心坐标)")
    parser.add_argument("--lat", type=float, default=None, help="参考水文纬度 (若未指定则自动采用 DEM 中心坐标)")
    parser.add_argument("--constituents", type=str, default="all", help="分潮方案 (默认: all，全精度34分潮)")
    parser.add_argument("--block-size", type=int, default=2048, help="栅格分块行数 (默认: 2048，适应海量超大 DEM)")

    args = parser.parse_args()
    compute_inundation_raster(
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


if __name__ == '__main__':
    main()
