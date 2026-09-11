"""
CoastTideX - 大地水准面差值栅格生成脚本 (Generate Delta-N Geoid Difference Raster)

【大地测量学背景与物理定义】
CNES-CLS22 MDT (Mean Dynamic Topography) 的定义参考面是卫星重力场模型 GOCO06s 大地水准面。
而测绘与工程高程普遍采用高阶大地水准面模型 EGM2008 (d/o 2190)。
二者由于截断阶数和重力测量数据源的不同，在全球范围内存在分米级至米级的系统差：
    ΔN(λ, φ) = N_GOCO06s(λ, φ) - N_EGM2008(λ, φ)

因此，当我们将基于平均海平面的瞬时潮位 (Tide) 叠加 MDT 转换至空间高程时：
    H_GOCO06s = Tide_MSL + MDT
若要严格转换至 EGM2008 正高体系，必须施加水准面差异改正：
    H_EGM2008 = H_GOCO06s + ΔN
              = Tide_MSL + MDT + (N_GOCO06s - N_EGM2008)

【数据源】
- ICGEM (International Centre for Global Earth Models), GFZ Potsdam
- 参考椭球: GRS80 / WGS84
- 潮汐系统: Tide-free (与 CNES-CLS22 及 EGM2008 官方标准严格一致)
"""

import os
import sys
import argparse
import numpy as np
import rasterio
from rasterio.warp import reproject, Resampling


def generate_delta_n_raster(goco06s_tif_path: str, egm2008_tif_path: str, output_tif_path: str):
    """
    严密计算 GOCO06s 与 EGM2008 的大地水准面高差栅格并输出为压缩 GeoTIFF。
    自动检测 CRS、仿射变换 Transform、空间范围 Bounds 与分辨率 Resolution，
    并在网格属性存在差异时执行严密双线性重投影重采样对齐。
    """
    print(f"[*] 读取 GOCO06s 栅格: {goco06s_tif_path}")
    if not os.path.exists(goco06s_tif_path):
        raise FileNotFoundError(f"未找到 GOCO06s 栅格: {goco06s_tif_path}")

    print(f"[*] 读取 EGM2008 栅格: {egm2008_tif_path}")
    if not os.path.exists(egm2008_tif_path):
        raise FileNotFoundError(f"未找到 EGM2008 栅格: {egm2008_tif_path}")

    with rasterio.open(goco06s_tif_path) as src_goco, rasterio.open(egm2008_tif_path) as src_egm:
        goco_data = src_goco.read(1)
        profile = src_goco.profile.copy()
        goco_nodata = src_goco.nodata
        egm_data = src_egm.read(1)
        egm_nodata = src_egm.nodata

        # 检查空间参考系统与网格配准
        print(f"[*] GOCO06s 空间属性: 形状={src_goco.shape}, CRS={src_goco.crs}, 分辨率={src_goco.res}, 范围={src_goco.bounds}")
        print(f"[*] EGM2008 空间属性: 形状={src_egm.shape}, CRS={src_egm.crs}, 分辨率={src_egm.res}, 范围={src_egm.bounds}")

        needs_reproject = (
            src_goco.shape != src_egm.shape or
            src_goco.transform != src_egm.transform or
            src_goco.bounds != src_egm.bounds or
            src_goco.crs != src_egm.crs
        )

        if needs_reproject:
            print("[*] 检测到 GOCO06s 与 EGM2008 空间网格配准存在差异，执行严密双线性重采样 (Bilinear Resampling) 对齐...")
            egm_aligned = np.empty(goco_data.shape, dtype=np.float32)
            reproject(
                source=egm_data.astype(np.float32),
                destination=egm_aligned,
                src_transform=src_egm.transform,
                src_crs=src_egm.crs,
                dst_transform=src_goco.transform,
                dst_crs=src_goco.crs,
                resampling=Resampling.bilinear,
                src_nodata=egm_nodata,
                dst_nodata=np.nan
            )
            egm_nodata_aligned = np.nan
        else:
            print("[+] 空间网格完全一致，直接执行代数差值。")
            egm_aligned = egm_data.astype(np.float32)
            egm_nodata_aligned = egm_nodata

    # 掩膜无效值
    mask_invalid = np.zeros(goco_data.shape, dtype=bool)
    if goco_nodata is not None:
        mask_invalid |= (goco_data == goco_nodata) | np.isnan(goco_data)
    if egm_nodata_aligned is not None:
        mask_invalid |= (egm_aligned == egm_nodata_aligned) | np.isnan(egm_aligned)

    print("[*] 计算差值栅格: Delta_N = N_GOCO06s - N_EGM2008 ...")
    delta_n = goco_data.astype(np.float32) - egm_aligned
    delta_n[mask_invalid] = np.nan

    valid_vals = delta_n[~mask_invalid]
    print(f"[+] 统计信息: 极小值 = {np.min(valid_vals):.4f} m, "
          f"极大值 = {np.max(valid_vals):.4f} m, "
          f"均值 = {np.mean(valid_vals):.4f} m, "
          f"标准差 = {np.std(valid_vals):.4f} m")

    out_dir = os.path.dirname(os.path.abspath(output_tif_path))
    os.makedirs(out_dir, exist_ok=True)

    profile.update({
        'dtype': rasterio.float32,
        'count': 1,
        'compress': 'deflate',
        'predictor': 2,
        'nodata': np.nan
    })

    with rasterio.open(output_tif_path, 'w', **profile) as dst:
        dst.write(delta_n.astype(np.float32), 1)
        dst.update_tags(
            DESCRIPTION="Delta N = GOCO06s geoid minus EGM2008 geoid for CoastTideX datum transformation",
            PROVENANCE="ICGEM GFZ Potsdam, Tide-free system, GRS80/WGS84",
            FORMULA="H_EGM2008 = Tide_MSL + MDT_CLS22 + Delta_N",
            AUTHOR="CoastTideX Geodetic Module"
        )

    print(f"[OK] 成功生成 Delta-N 栅格: {output_tif_path} (文件大小: {os.path.getsize(output_tif_path) / (1024*1024):.2f} MB)")


def main():
    parser = argparse.ArgumentParser(description="生成 GOCO06s 与 EGM2008 大地水准面高差栅格 (Delta-N)")
    parser.add_argument("--goco", type=str, default=r"F:\SWOT_interpolation\1-Data\Height_anomaly\height_anomaly_ell_GOCO06s_normal_ellipsoid_GRS80_tide_free_d_o_300.tiff", help="GOCO06s 栅格路径")
    parser.add_argument("--egm", type=str, default=r"F:\SWOT_interpolation\1-Data\Height_anomaly\height_anomaly_ell_EGM2008_normal_ellipsoid_GRS80_tide_free_d_o_2190.tiff", help="EGM2008 栅格路径")
    parser.add_argument("--out", type=str, default=r"data/geoid/delta_n_goco06s_minus_egm2008.tif", help="输出 GeoTIFF 路径")

    args = parser.parse_args()
    generate_delta_n_raster(args.goco, args.egm, args.out)


if __name__ == '__main__':
    main()
