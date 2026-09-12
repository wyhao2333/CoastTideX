"""
CoastTideX 真实 FES/DEM 局部小区域验证验收脚本 (Real-World Tile Validation Harness)
当用户本地具备权威 FES2022b 模型与垂直基准网格时使用，对 5~20 km DEM 真实瓦片
执行高保真对比测试：
    1. 自适应四叉树控制网格 (Adaptive Quadtree Control Grid)
    2. 密集规则控制网格参考 (Dense Regular Control-Grid Reference)
    3. 真实采样像元直接 FES 解算预言机真值 (Direct Sampled-Pixel FES Oracle)

输出关键验收指标:
    - Sampled Direct Oracle: MAE, RMSE, Median, P90, P95, P99, Max Error (percentage points)
    - Valid / Invalid Accounting: direct_valid, direct_invalid, adapt_valid, common_valid
    - Dense Grid Comparison: MAE, RMSE, P95, Max Error, Node Reduction Ratio, Speedup
"""

import os
import sys
import time
import argparse
import tempfile
from typing import Optional, Dict, Any

import numpy as np
import rasterio
import rasterio.warp
import rasterio.transform

try:
    from pyproj import Transformer
except ImportError:
    Transformer = None

# 将项目根目录加入模块检索路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from core.tide_engine import FESTidePredictor
from core.datum_engine import DatumTransformer
from core.raster_engine import RasterTideEngine
from core.utils import normalize_longitude


def evaluate_direct_fes_samples(
    dem_path: str,
    adapt_tif_path: str,
    predictor: Any,
    transformer: DatumTransformer,
    year: int = 2024,
    step: str = "30min",
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    inclusive: Optional[str] = None,
    dem_datum: str = "egm2008",
    n_samples: int = 100,
    seed: int = 42,
    direct_max_points: int = 500000,
    strict: bool = True
) -> Dict[str, Any]:
    """
    真实抽样像元中心直接 FES 潮位解算预言机 (Direct Sampled-Pixel FES Oracle)。
    对选取的真实像元坐标直接调用 predict_points_period() 与静态基准转换，
    按严格 H(t) > z 统计直接淹没频率，并与自适应栅格输出 (adapt_tif_path) 进行逐像元比对。
    """
    with rasterio.open(dem_path) as src_dem:
        dem_data = src_dem.read(1)
        nodata = src_dem.nodata
        crs = src_dem.crs
        trans = src_dem.transform

        if nodata is not None and np.isfinite(nodata):
            v_mask = np.isfinite(dem_data) & ~np.isclose(dem_data, nodata)
        else:
            v_mask = np.isfinite(dem_data)

        valid_rows, valid_cols = np.where(v_mask)
        total_valid = len(valid_rows)

        if total_valid == 0:
            raise ValueError(f"DEM 栅格 '{dem_path}' 中未找到任何有效像元。")

        num_draw = min(int(n_samples), total_valid)
        rng = np.random.default_rng(seed)
        chosen_idx = rng.choice(total_valid, size=num_draw, replace=False)
        chosen_rows = valid_rows[chosen_idx]
        chosen_cols = valid_cols[chosen_idx]
        elevations = dem_data[chosen_rows, chosen_cols].astype(float)

        xs, ys = rasterio.transform.xy(trans, chosen_rows, chosen_cols, offset='center')
        xs_arr = np.asarray(xs, dtype=float)
        ys_arr = np.asarray(ys, dtype=float)

        if not crs.is_geographic:
            if Transformer is not None:
                tf = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
                t_lons, t_lats = tf.transform(xs_arr, ys_arr)
                sample_lons = normalize_longitude(np.asarray(t_lons, dtype=float), to_360=False)
                sample_lats = np.asarray(t_lats, dtype=float)
            else:
                t_lons, t_lats = rasterio.warp.transform(crs, "EPSG:4326", xs_arr, ys_arr)
                sample_lons = normalize_longitude(np.asarray(t_lons, dtype=float), to_360=False)
                sample_lats = np.asarray(t_lats, dtype=float)
        else:
            sample_lons = normalize_longitude(xs_arr, to_360=False)
            sample_lats = ys_arr

    # 时间范围
    if start_time is not None and end_time is not None:
        t_start = str(start_time)
        t_end = str(end_time)
        inc = inclusive or 'both'
    else:
        t_start = f"{int(year):04d}-01-01 00:00:00"
        t_end = f"{int(year)+1:04d}-01-01 00:00:00"
        inc = inclusive or 'left'

    # 1. 直接像元 FES 时序预测
    tide_mat, _, flag_mat = predictor.predict_points_period(
        lons=sample_lons,
        lats=sample_lats,
        start_time=t_start,
        end_time=t_end,
        freq=step,
        inclusive=inc,
        max_fes_evaluate_points=direct_max_points
    )

    # 2. 静态基准转换
    offsets_dict = transformer.get_static_datum_offsets(
        sample_lons, sample_lats, target=dem_datum, strict=strict
    )
    offsets = offsets_dict['offset_m']

    # 3. 计算 Direct FES 淹没频率 (严格 H(t) > z)
    f_direct = np.full(num_draw, np.nan, dtype=float)
    direct_valid = np.zeros(num_draw, dtype=bool)

    for i in range(num_draw):
        t_row = tide_mat[i]
        off_val = offsets[i]
        valid_t = t_row[np.isfinite(t_row)]
        flags = flag_mat[i]

        # 若全部为 NaN 或 Flag=0 (深陆/无解) 或基准偏移为 NaN，则视为 Direct 无效
        if len(valid_t) == 0 or np.isnan(off_val) or (flags == 0).all():
            direct_valid[i] = False
            f_direct[i] = np.nan
        else:
            h_t = valid_t + off_val
            z_i = elevations[i]
            # 严格使用 H(t) > z
            f_direct[i] = (np.count_nonzero(h_t > z_i) / float(len(h_t))) * 100.0
            direct_valid[i] = True

    # 4. 读取自适应栅格中的像元值
    with rasterio.open(adapt_tif_path) as ds_adapt:
        adapt_band = ds_adapt.read(1)
        adapt_vals = adapt_band[chosen_rows, chosen_cols].astype(float)
        adapt_valid = np.isfinite(adapt_vals) & (adapt_vals >= 0.0) & (adapt_vals <= 100.0)

    # 5. 统计核算与指标计算
    common_valid = direct_valid & adapt_valid
    n_common = int(np.count_nonzero(common_valid))
    n_direct_valid = int(np.count_nonzero(direct_valid))
    n_direct_invalid = int(num_draw - n_direct_valid)
    n_adapt_valid = int(np.count_nonzero(adapt_valid))
    n_adapt_invalid = int(num_draw - n_adapt_valid)

    discrepancy_direct_inv_adapt_val = int(np.count_nonzero(~direct_valid & adapt_valid))
    discrepancy_direct_val_adapt_inv = int(np.count_nonzero(direct_valid & ~adapt_valid))

    if n_common > 0:
        diff = np.abs(adapt_vals[common_valid] - f_direct[common_valid])
        mae = float(np.mean(diff))
        rmse = float(np.sqrt(np.mean(diff ** 2)))
        median = float(np.median(diff))
        p90 = float(np.percentile(diff, 90))
        p95 = float(np.percentile(diff, 95))
        p99 = float(np.percentile(diff, 99))
        max_err = float(np.max(diff))
    else:
        mae = rmse = median = p90 = p95 = p99 = max_err = float('nan')

    return {
        'total_samples': num_draw,
        'direct_valid_count': n_direct_valid,
        'direct_invalid_count': n_direct_invalid,
        'adapt_valid_count': n_adapt_valid,
        'adapt_invalid_count': n_adapt_invalid,
        'common_valid_count': n_common,
        'discrepancy_direct_inv_adapt_val': discrepancy_direct_inv_adapt_val,
        'discrepancy_direct_val_adapt_inv': discrepancy_direct_val_adapt_inv,
        'mae': mae,
        'rmse': rmse,
        'median': median,
        'p90': p90,
        'p95': p95,
        'p99': p99,
        'max_error': max_err,
        'seed': seed,
        'sampled_rows': chosen_rows,
        'sampled_cols': chosen_cols,
        'sample_lons': sample_lons,
        'sample_lats': sample_lats,
        'elevations': elevations,
        'f_direct': f_direct,
        'f_adaptive': adapt_vals,
        'direct_valid_mask': direct_valid,
        'adapt_valid_mask': adapt_valid
    }


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
    parser.add_argument("--direct-samples", type=int, default=100, help="直接像元 FES 预言机抽样数量 (默认: 100)")
    parser.add_argument("--seed", type=int, default=42, help="抽样随机数种子 (默认: 42)")
    parser.add_argument("--direct-max-points", type=int, default=500000, help="直接像元解算单次安全上限点数 (默认: 500000)")
    parser.add_argument("--skip-dense-grid", action="store_true", help="跳过耗时的密集规则控制网格解算，仅比对直接像元真值")

    args = parser.parse_args()

    if not os.path.exists(args.dem):
        print(f"[ERROR] 未找到指定的 DEM 栅格文件: {args.dem}")
        sys.exit(1)

    print("===============================================================================")
    print("   CoastTideX Real-World Tile Verification: Adaptive vs Direct FES Oracle")
    print("===============================================================================")
    print(f"[*] 输入 DEM: {args.dem}")
    print(f"[*] 模拟时段: {args.year} 全年 (步长: {args.step})")
    print(f"[*] 目标基准: {args.dem_datum.upper()} (strict={not args.non_strict})")

    engine = RasterTideEngine()
    info = engine.inspect_raster(args.dem, compute_valid_count=False)
    print(f"[*] DEM 规格: {info.width} × {info.height}, 分辨率: {info.formatted_resolution}")
    print(f"[*] 坐标系统: {info.crs}")

    with tempfile.TemporaryDirectory() as tmpdir:
        # 1. 密集规则控制网格解算 (Dense Regular Control-Grid Reference)
        dense_nodes = 0
        t_dense = 0.0
        ref_out = os.path.join(tmpdir, "ref_inundation.tif")
        ref_qc = os.path.join(tmpdir, "ref_qc.tif")

        if not args.skip_dense_grid:
            print("\n-------------------------------------------------------------------------------")
            print(f" [Step 1/3] 正在解算密集规则参考控制网格 (Dense Control: {args.ref_spacing}m)...")
            print("-------------------------------------------------------------------------------")
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
                inundation_error_tolerance_pct=100.0,
                strict=not args.non_strict,
                progress_callback=lambda p, m: print(f"    Dense -> [{p:3d}%] {m}")
            )
            t_dense = time.time() - t0_dense
            dense_nodes = ref_summary.control_nodes_count
            print(f"[OK] 密集参考控制网格解算完成: 耗时 {t_dense:.2f}s, 控制节点数: {dense_nodes}")
        else:
            print("\n[*] 跳过密集规则控制网格解算 (--skip-dense-grid)。")

        # 2. 自适应四叉树控制网格解算 (Adaptive Quadtree Control Grid)
        print("\n-------------------------------------------------------------------------------")
        print(f" [Step 2/3] 正在解算自适应控制网格 (Init={args.adaptive_init_spacing}m, Min={args.adaptive_min_spacing}m, Tol={args.tolerance}%)...")
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

        # 3. 真实采样像元直接 FES 解算预言机 (Direct Sampled-Pixel FES Oracle)
        print("\n-------------------------------------------------------------------------------")
        print(f" [Step 3/3] 正在执行直接像元 FES 预言机真值比对 (Direct Samples={args.direct_samples}, Seed={args.seed})...")
        print("-------------------------------------------------------------------------------")
        t0_direct = time.time()
        predictor = engine._get_predictor()
        transformer = engine.transformer

        oracle_res = evaluate_direct_fes_samples(
            dem_path=args.dem,
            adapt_tif_path=adapt_out,
            predictor=predictor,
            transformer=transformer,
            year=args.year,
            step=args.step,
            dem_datum=args.dem_datum,
            n_samples=args.direct_samples,
            seed=args.seed,
            direct_max_points=args.direct_max_points,
            strict=not args.non_strict
        )
        t_direct = time.time() - t0_direct
        print(f"[OK] 直接像元 FES 预言机比对完成 (耗时 {t_direct:.2f}s)")

        # 4. 输出最终验收报告
        print("\n===============================================================================")
        print("               CoastTideX v1.4 真实小区域 FES/DEM 验收报告")
        print("===============================================================================")

        total_valid_dem = adapt_summary.input_valid_pixels
        solved_fraction = (adapt_summary.solved_pixels / max(1, total_valid_dem)) * 100.0

        print("--- A. 整体解算覆盖率 (Overall Coverage) ---")
        print(f"  * 输入有效 DEM 像元数 (Input Valid Pixels) : {total_valid_dem:,}")
        print(f"  * 成功解算像元数 (Solved Pixels)          : {adapt_summary.solved_pixels:,}")
        print(f"  * 有效解算覆盖率 (Solved Fraction)        : {solved_fraction:.2f}%")
        print(f"  * 自适应控制节点数 (Control Nodes)         : {adapt_nodes}")
        print(f"  * 自适应解算总耗时 (Runtime)               : {t_adapt:.2f}s")

        print("\n--- B. 直接像元 FES 预言机真值误差 (Direct Sampled-Pixel FES Oracle) ---")
        print(f"  * 抽样总像元数 (Total Samples)           : {oracle_res['total_samples']}")
        print(f"  * 直接 FES 有效解像元 (Direct-valid)     : {oracle_res['direct_valid_count']}")
        print(f"  * 直接 FES 无效像元 (Direct-invalid)     : {oracle_res['direct_invalid_count']}")
        print(f"  * 自适应有效解像元 (Adapt-valid)         : {oracle_res['adapt_valid_count']}")
        print(f"  * 共同有效验证像元 (Common-valid)        : {oracle_res['common_valid_count']}")
        if oracle_res['discrepancy_direct_inv_adapt_val'] > 0:
            print(f"  * [差异提示] Direct无效但Adapt有解: {oracle_res['discrepancy_direct_inv_adapt_val']} 像元")
        if oracle_res['discrepancy_direct_val_adapt_inv'] > 0:
            print(f"  * [差异提示] Direct有效但Adapt无效: {oracle_res['discrepancy_direct_val_adapt_inv']} 像元")

        if oracle_res['common_valid_count'] > 0:
            print(f"  * 平均绝对误差 (MAE)                     : {oracle_res['mae']:.3f} percentage points")
            print(f"  * 均方根误差 (RMSE)                     : {oracle_res['rmse']:.3f} percentage points")
            print(f"  * 中位数误差 (Median)                    : {oracle_res['median']:.3f} percentage points")
            print(f"  * 90% 分位数误差 (P90)                   : {oracle_res['p90']:.3f} percentage points")
            print(f"  * 95% 分位数误差 (P95)                   : {oracle_res['p95']:.3f} percentage points")
            print(f"  * 99% 分位数误差 (P99)                   : {oracle_res['p99']:.3f} percentage points")
            print(f"  * 最大绝对误差 (Max Error)               : {oracle_res['max_error']:.3f} percentage points")
        else:
            print("  * [WARN] 未找到共同有效解像元，无法计算误差统计。")

        if not args.skip_dense_grid and os.path.exists(ref_out):
            print("\n--- C. 密集规则控制网格比对 (Dense Regular Control-Grid Reference) ---")
            with rasterio.open(ref_out) as ds_ref, rasterio.open(adapt_out) as ds_adapt:
                ref_data = ds_ref.read(1)
                adapt_data = ds_adapt.read(1)
                common_grid = np.isfinite(ref_data) & np.isfinite(adapt_data)
                n_cg = int(np.count_nonzero(common_grid))

                if n_cg > 0:
                    diff_g = np.abs(adapt_data[common_grid] - ref_data[common_grid])
                    mae_g = float(np.mean(diff_g))
                    rmse_g = float(np.sqrt(np.mean(diff_g ** 2)))
                    p95_g = float(np.percentile(diff_g, 95))
                    max_g = float(np.max(diff_g))
                    reduction = (1.0 - (adapt_nodes / max(1, dense_nodes))) * 100.0
                    speedup = t_dense / max(1e-3, t_adapt)

                    print(f"  * 密集网格控制节点数 (Dense Grid Nodes)  : {dense_nodes}")
                    print(f"  * 控制节点压缩比 (Node Reduction)        : {reduction:.1f}%")
                    print(f"  * 计算加速比 (Speedup)                  : {speedup:.2f}x ({t_dense:.1f}s -> {t_adapt:.1f}s)")
                    print(f"  * 共同有效像元数 (Common Valid)          : {n_cg:,} / {total_valid_dem:,}")
                    print(f"  * 网格间平均绝对误差 (Grid MAE)          : {mae_g:.3f} percentage points")
                    print(f"  * 网格间均方根误差 (Grid RMSE)          : {rmse_g:.3f} percentage points")
                    print(f"  * 网格间 95% 误差 (Grid P95)             : {p95_g:.3f} percentage points")
                    print(f"  * 网格间最大误差 (Grid Max Error)        : {max_g:.3f} percentage points")
        print("===============================================================================\n")


if __name__ == '__main__':
    main()
