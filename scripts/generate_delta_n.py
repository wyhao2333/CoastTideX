"""
CoastTideX - 大地水准面差值栅格生成脚本 (Generate Delta-N Geoid Difference Raster)

【大地测量学背景与物理定义】
CNES-CLS22 MDT (Mean Dynamic Topography) 在开阔大洋的定义参考面是 GOCO06s 大地水准面，
而在地中海与黑海（CMEMS2020 区域）的定义参考面是 EIGEN-6C4 大地水准面。
而测绘与工程高程普遍采用高阶大地水准面模型 EGM2008 (d/o 2190)。
由于截断阶数和重力测量数据源的不同，在全球范围内存在分米级至米级的系统差：
    ΔN(λ, φ) = N_REF(λ, φ) - N_TARGET(λ, φ)

因此，当我们将基于平均海平面的瞬时潮位 (Tide) 叠加 MDT 转换至空间高程时：
    H_MDT_REF = Tide_MSL + MDT
若要严格转换至 EGM2008 正高体系，必须施加相应水准面的几何差异改正：
    H_EGM2008 = H_MDT_REF + ΔN
              = Tide_MSL + MDT + (N_REF - N_TARGET)

【数据源】
- ICGEM (International Centre for Global Earth Models), GFZ Potsdam
- 参考椭球: GRS80 / WGS84
- 潮汐系统: Tide-free (与 CNES-CLS22、CMEMS2020 及 EGM2008 官方标准严格一致)
"""

import os
import sys
import argparse
import datetime
import numpy as np
import rasterio
from rasterio.warp import reproject, Resampling


def generate_delta_n_raster(
    ref_tif_path: str,
    target_tif_path: str,
    output_tif_path: str,
    ref_name: str = "GOCO06s",
    target_name: str = "EGM2008"
):
    """
    严密计算参考大地水准面 (如 GOCO06s, EIGEN-6C4) 与目标大地水准面 (如 EGM2008) 的高差栅格并输出为压缩 GeoTIFF。
    自动检测 CRS、仿射变换 Transform、空间范围 Bounds 与分辨率 Resolution，
    并在网格属性存在差异时执行严密双线性重投影重采样对齐。
    """
    print(f"[*] 读取参考水准面 [{ref_name}] 栅格: {ref_tif_path}")
    if not os.path.exists(ref_tif_path):
        raise FileNotFoundError(f"未找到参考水准面 [{ref_name}] 栅格: {ref_tif_path}")

    print(f"[*] 读取目标水准面 [{target_name}] 栅格: {target_tif_path}")
    if not os.path.exists(target_tif_path):
        raise FileNotFoundError(f"未找到目标水准面 [{target_name}] 栅格: {target_tif_path}")

    with rasterio.open(ref_tif_path) as src_ref, rasterio.open(target_tif_path) as src_target:
        ref_data = src_ref.read(1)
        profile = src_ref.profile.copy()
        ref_nodata = src_ref.nodata
        target_data = src_target.read(1)
        target_nodata = src_target.nodata

        print(f"[*] {ref_name} 空间属性: 形状={src_ref.shape}, CRS={src_ref.crs}, 分辨率={src_ref.res}, 范围={src_ref.bounds}")
        print(f"[*] {target_name} 空间属性: 形状={src_target.shape}, CRS={src_target.crs}, 分辨率={src_target.res}, 范围={src_target.bounds}")

        needs_reproject = (
            src_ref.shape != src_target.shape or
            src_ref.transform != src_target.transform or
            src_ref.bounds != src_target.bounds or
            src_ref.crs != src_target.crs
        )

        if needs_reproject:
            print(f"[*] 检测到 {ref_name} 与 {target_name} 空间网格配准存在差异，执行严密双线性重采样 (Bilinear Resampling) 对齐...")
            target_aligned = np.empty(ref_data.shape, dtype=np.float32)
            reproject(
                source=target_data.astype(np.float32),
                destination=target_aligned,
                src_transform=src_target.transform,
                src_crs=src_target.crs,
                dst_transform=src_ref.transform,
                dst_crs=src_ref.crs,
                resampling=Resampling.bilinear,
                src_nodata=target_nodata,
                dst_nodata=np.nan
            )
            target_nodata_aligned = np.nan
        else:
            print("[+] 空间网格完全一致，直接执行代数差值。")
            target_aligned = target_data.astype(np.float32)
            target_nodata_aligned = target_nodata

    # 严密的无效值与非有限数掩膜防御
    mask_invalid = ~np.isfinite(ref_data)
    if ref_nodata is not None and np.isfinite(ref_nodata):
        mask_invalid |= np.isclose(ref_data, ref_nodata)

    mask_target_invalid = ~np.isfinite(target_aligned)
    if target_nodata_aligned is not None and np.isfinite(target_nodata_aligned):
        mask_target_invalid |= np.isclose(target_aligned, target_nodata_aligned)

    mask_invalid |= mask_target_invalid

    valid_count = int(np.count_nonzero(~mask_invalid))
    total_count = ref_data.size
    print(f"[*] 有效格网像元数量: {valid_count} / {total_count} ({valid_count / total_count * 100:.2f}%)")

    if valid_count == 0:
        raise ValueError(f"有效重叠格网点数为 0，无法生成有效 Delta N 栅格！请检查输入栅格的空间范围、坐标系或 NoData 定义。")

    print(f"[*] 计算差值栅格: Delta_N = N_{ref_name} - N_{target_name} ...")
    delta_n = ref_data.astype(np.float32) - target_aligned
    delta_n[mask_invalid] = np.nan

    valid_vals = delta_n[~mask_invalid]
    print(f"[+] 统计信息: 极小值 = {np.min(valid_vals):.4f} m, "
          f"极大值 = {np.max(valid_vals):.4f} m, "
          f"均值 = {np.mean(valid_vals):.4f} m, "
          f"标准差 = {np.std(valid_vals):.4f} m")

    out_dir = os.path.dirname(os.path.abspath(output_tif_path))
    if out_dir:
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
            REF_MODEL=ref_name,
            TARGET_MODEL=target_name,
            CREATED_AT=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            FORMULA=f"H_{target_name} = Tide_MSL + MDT + (N_{ref_name} - N_{target_name})",
            DELTA_N_DEF=f"N_{ref_name} - N_{target_name}",
            DESCRIPTION=f"Delta N = {ref_name} geoid minus {target_name} geoid for CoastTideX datum transformation",
            PROVENANCE="ICGEM GFZ Potsdam, Tide-free system, GRS80/WGS84",
            AUTHOR="CoastTideX Geodetic Module"
        )

    print(f"[OK] 成功生成 Delta-N 栅格: {output_tif_path} (文件大小: {os.path.getsize(output_tif_path) / (1024*1024):.2f} MB)")


def main():
    parser = argparse.ArgumentParser(description="生成大地水准面高差栅格 (Delta-N = N_REF - N_TARGET)")
    parser.add_argument("--ref-name", type=str, default="GOCO06s", help="参考水准面模型名称 (如 GOCO06s, EIGEN-6C4)")
    parser.add_argument("--ref-tif", "--ref", "--goco", type=str,
                        default=r"F:\SWOT_interpolation\1-Data\Height_anomaly\height_anomaly_ell_GOCO06s_normal_ellipsoid_GRS80_tide_free_d_o_300.tiff",
                        help="参考水准面栅格路径 (如 GOCO06s 或 EIGEN-6C4)")
    parser.add_argument("--target-name", type=str, default="EGM2008", help="目标水准面模型名称 (如 EGM2008)")
    parser.add_argument("--target-tif", "--target", "--egm", type=str,
                        default=r"F:\SWOT_interpolation\1-Data\Height_anomaly\height_anomaly_ell_EGM2008_normal_ellipsoid_GRS80_tide_free_d_o_2190.tiff",
                        help="目标水准面栅格路径 (如 EGM2008)")
    parser.add_argument("--out", type=str, default=r"data/geoid/delta_n_goco06s_minus_egm2008.tif", help="输出 GeoTIFF 路径")

    args = parser.parse_args()
    generate_delta_n_raster(
        ref_tif_path=args.ref_tif,
        target_tif_path=args.target_tif,
        output_tif_path=args.out,
        ref_name=args.ref_name,
        target_name=args.target_name
    )


if __name__ == '__main__':
    main()
