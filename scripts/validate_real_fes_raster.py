"""
CoastTideX 真实 FES/DEM 局部小区域验证验收脚本 (Real-World Tile Validation Harness)
当用户本地具备权威 FES2022b 模型与垂直基准网格时使用，对 5~20 km DEM 真实瓦片
执行高保真对比测试：
    自适应控制网格 (Adaptive Raster Engine) vs 密集规则网格参考真值 (Dense Direct Reference)

输出关键验收指标:
    - MAE (平均绝对误差)
    - RMSE (均方根误差)
    - P95 (95% 分位数误差)
    - Max Error (最大绝对误差)
    - Solved Fraction (有效解算像元比例)
    - Node Reduction Ratio (控制节点削减压缩比)
    - Runtime (执行耗时对比)
"""

import os
import sys
import time
import argparse
import tempfile
import numpy as np
import rasterio

# 将项目根目录加入模块检索路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from core.tide_engine import FESTidePredictor
from core.datum_engine import DatumTransformer
from core.raster_engine import RasterTideEngine


def main():
    parser = argparse.ArgumentParser(description="CoastTideX 真实小区域 FES/DEM 淹没频率解算验证验收工具")
    parser.add_argument("--dem", "-i", type=str, required=True, help="输入 DEM 栅格路径 (推荐 5~20km 瓦片)")
    parser.add_argument("--year", type=int, default=2024, help="预测年份 (默认: 2024)")
    parser.add_argument("--step", type=str, default="30min", help="时间步长 (默认: 30min)")
    parser.add_argument("--dem-datum", type=str, default="egm2008", help="DEM 垂直基准 (默认: egm2008)")
    parser.add_argument("--ref-spacing", type=float, default=500.0, help="密集参考基准网格间距 (米，默认: 500m)")
    parser.add_argument("--adaptive-init-spacing", type=float, default=4000.0, help="自适应初始网格间距 (米，默认: 4000m)")
    parser.add_argument("--adaptive-min-spacing", type=float, default=500.0, help="自适应最小网格间距 (米，默认: 500m)")
    parser.add_argument("--tolerance", type=float, default=1.0, help="自适应容错阈值 (%%，默认: 1.0%%)")
    parser.add_argument("--non-strict", action="store_true", help="允许基准缺失或近似多边形回退")

    args = parser.parse_args()

    if not os.path.exists(args.dem):
        print(f"[ERROR] 未找到指定的 DEM 栅格文件: {args.dem}")
        sys.exit(1)

    print("===============================================================================")
    print("   CoastTideX Real-World Tile Verification: Adaptive vs Dense Reference")
    print("===============================================================================")
    print(f"[*] 输入 DEM: {args.dem}")
    print(f"[*] 模拟时段: {args.year} 全年 (步长: {args.step})")
    print(f"[*] 目标基准: {args.dem_datum.upper()} (strict={not args.non_strict})")

    engine = RasterTideEngine()
    info = engine.inspect_raster(args.dem, compute_valid_count=False)
    print(f"[*] DEM 规格: {info.width} × {info.height}, 分辨率: {info.formatted_resolution}")
    print(f"[*] 坐标系统: {info.crs}")

    with tempfile.TemporaryDirectory() as tmpdir:
        # 1. 密集规则参考基准解算 (Dense Direct Control Grid)
        print("\n-------------------------------------------------------------------------------")
        print(f" [Step 1/2] 正在解算密集规则参考基准网格 (Dense Control: {args.ref_spacing}m)...")
        print("-------------------------------------------------------------------------------")
        ref_out = os.path.join(tmpdir, "ref_inundation.tif")
        ref_qc = os.path.join(tmpdir, "ref_qc.tif")

        t0_dense = time.time()
        ref_summary = engine.calculate_inundation_raster(
            dem_path=args.dem,
            output_path=ref_out,
            qc_output_path=ref_qc,
            year=args.year,
            freq=args.step,
            dem_datum=args.dem_datum,
            initial_control_spacing_m=args.ref_spacing,
            min_control_spacing_m=args.ref_spacing,
            inundation_error_tolerance_pct=100.0,  # 不触发进一步细分，保持规则密集网格
            strict=not args.non_strict,
            progress_callback=lambda p, m: print(f"    Dense -> [{p:3d}%] {m}")
        )
        t_dense = time.time() - t0_dense
        dense_nodes = ref_summary.control_nodes_count
        print(f"[OK] 密集参考基准解算完成: 耗时 {t_dense:.2f}s, 控制节点数: {dense_nodes}")

        # 2. 自适应四叉树控制网格解算 (Adaptive Quadtree Control Grid)
        print("\n-------------------------------------------------------------------------------")
        print(f" [Step 2/2] 正在解算自适应控制网格 (Init={args.adaptive_init_spacing}m, Min={args.adaptive_min_spacing}m, Tol={args.tolerance}%)...")
        print("-------------------------------------------------------------------------------")
        adapt_out = os.path.join(tmpdir, "adapt_inundation.tif")
        adapt_qc = os.path.join(tmpdir, "adapt_qc.tif")

        t0_adapt = time.time()
        adapt_summary = engine.calculate_inundation_raster(
            dem_path=args.dem,
            output_path=adapt_out,
            qc_output_path=adapt_qc,
            year=args.year,
            freq=args.step,
            dem_datum=args.dem_datum,
            initial_control_spacing_m=args.adaptive_init_spacing,
            min_control_spacing_m=args.adaptive_min_spacing,
            inundation_error_tolerance_pct=args.tolerance,
            strict=not args.non_strict,
            progress_callback=lambda p, m: print(f"    Adapt -> [{p:3d}%] {m}")
        )
        t_adapt = time.time() - t0_adapt
        adapt_nodes = adapt_summary.control_nodes_count
        print(f"[OK] 自适应网格解算完成: 耗时 {t_adapt:.2f}s, 控制节点数: {adapt_nodes}")

        # 3. 统计比对与验收指标计算
        print("\n===============================================================================")
        print("                         局部瓦片验收与误差对比指标")
        print("===============================================================================")
        with rasterio.open(ref_out) as ds_ref, rasterio.open(adapt_out) as ds_adapt:
            ref_data = ds_ref.read(1)
            adapt_data = ds_adapt.read(1)

            common_valid = np.isfinite(ref_data) & np.isfinite(adapt_data)
            n_common = int(np.count_nonzero(common_valid))

            if n_common == 0:
                print("[WARN] 警告: 未找到双方共同有效解算的像元，请检查 DEM 区域是否落入海洋或 FES 有效范围。")
                return

            diff = np.abs(adapt_data[common_valid] - ref_data[common_valid])
            mae = float(np.mean(diff))
            rmse = float(np.sqrt(np.mean(diff**2)))
            p95 = float(np.percentile(diff, 95))
            max_err = float(np.max(diff))

            total_valid_dem = adapt_summary.input_valid_pixels
            solved_fraction = (adapt_summary.solved_pixels / max(1, total_valid_dem)) * 100.0
            reduction_ratio = (1.0 - (adapt_nodes / max(1, dense_nodes))) * 100.0
            speedup = t_dense / max(1e-3, t_adapt)

            print(f"  * 共同有效像元数 (Common Valid Pixels): {n_common:,} / {total_valid_dem:,}")
            print(f"  * 有效解算覆盖率 (Solved Fraction)   : {solved_fraction:.2f}%")
            print(f"  * 平均绝对误差 (MAE)                 : {mae:.3f}%")
            print(f"  * 均方根误差 (RMSE)                 : {rmse:.3f}%")
            print(f"  * 95% 分位数误差 (P95 Error)         : {p95:.3f}%")
            print(f"  * 最大绝对误差 (Max Absolute Error) : {max_err:.3f}%")
            print(f"  * 控制节点压缩率 (Node Reduction)   : {reduction_ratio:.1f}% ({dense_nodes} -> {adapt_nodes})")
            print(f"  * 解算加速比 (Runtime Speedup)       : {speedup:.2f}x ({t_dense:.1f}s -> {t_adapt:.1f}s)")
            print("===============================================================================\n")


if __name__ == '__main__':
    main()
