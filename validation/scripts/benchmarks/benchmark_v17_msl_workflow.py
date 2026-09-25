"""
CoastTideX v1.7 — MSL Reference Workflow Benchmark & Verification
(Chongming 2024 DEM 端到端基准转换与淹没频率解算双向验证)

科学依据: Seeger & Minderhoud (Nature, 2026) 近岸基准统一理论。

阶段 1: DEM 转换 (EGM2008 -> MSL)
阶段 2: 10,000 点决策等价性严密检验 (Tide_MSL + MDT + DeltaN > DEM_EGM2008 <=> Tide_MSL > DEM_MSL)
阶段 3: 24h 潮间带潜在淹没频率解算 (Zero-MDT-Lookup MSL Workflow)
阶段 4: 与 v1.6 旧架构 (smoke_freq_production_clean.tif) 结果比对与性能提速分析
"""

import os
import sys
import time
import json
import pandas as pd
import numpy as np
import rasterio

# 定位项目根目录
def find_project_root(start_path: str = __file__) -> str:
    cur = os.path.abspath(start_path)
    while True:
        parent = os.path.dirname(cur)
        if os.path.isdir(os.path.join(cur, "core")) and os.path.isfile(os.path.join(cur, "config.yaml")):
            return cur
        if parent == cur:
            raise RuntimeError("Could not find CoastTideX project root containing core/ and config.yaml")
        cur = parent

PROJECT_ROOT = find_project_root()
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.dem_datum_converter import convert_dem_to_msl, DEMDatumConverter
from core.datum_engine import DatumTransformer
from core.tide_engine import FESTidePredictor
from core.raster_engine import RasterTideEngine

DEM_PATH = "F:/1-Research/China_tidal-flat_terrain(ICESat2)/3-Result/ChongMing_NoClipped(2021-2024)/ChongMing/2024/ChongMing_2024_Elevation.tif"
VERIFY_DIR = os.path.join(PROJECT_ROOT, "validation", "artifacts", "chongming_verify")
OUT_MSL_DEM = os.path.join(VERIFY_DIR, "ChongMing_2024_MSL.tif")
OUT_MSL_QC = os.path.join(VERIFY_DIR, "ChongMing_2024_MSL_conversion_qc.tif")

OLD_FREQ_TIF = os.path.join(VERIFY_DIR, "smoke_freq_production_clean.tif")
OLD_QC_TIF = os.path.join(VERIFY_DIR, "smoke_qc_production_clean.tif")
OLD_PERF_JSON = os.path.join(VERIFY_DIR, "performance_production_clean.json")

NEW_FREQ_TIF = os.path.join(VERIFY_DIR, "smoke_freq_v17_msl.tif")
NEW_QC_TIF = os.path.join(VERIFY_DIR, "smoke_qc_v17_msl.tif")
NEW_CACHE_NC = os.path.join(VERIFY_DIR, "smoke_cache_v17_msl.nc")
SUMMARY_JSON = os.path.join(VERIFY_DIR, "benchmark_v17_msl_summary.json")


def run_v17_benchmark():
    os.makedirs(VERIFY_DIR, exist_ok=True)
    report = {}

    print("================================================================================")
    print("CoastTideX v1.7 — MSL Reference Workflow Benchmark")
    print(f"DEM Path: {DEM_PATH}")
    print("================================================================================")

    # -------------------------------------------------------------------------
    # 阶段 1: 将 Chongming 2024 DEM 转换为 MSL 基准
    # -------------------------------------------------------------------------
    print("\n[Phase 1] 启动 Chongming 2024 DEM 垂直基准转换 (EGM2008 -> MSL)...")
    t0_conv = time.perf_counter()
    conv_summary = convert_dem_to_msl(
        input_dem_path=DEM_PATH,
        output_msl_path=OUT_MSL_DEM,
        output_qc_path=OUT_MSL_QC,
        max_extrapolation_distance_km=100.0,
        block_size=1024,
        allow_overwrite=True,
        progress_callback=lambda p, m: print(f"    [{p:3d}%] {m}")
    )
    t_conv_elapsed = time.perf_counter() - t0_conv
    print(f"[OK] DEM 基准转换完成！耗时: {t_conv_elapsed:.2f} 秒")
    print(f"     总像元数:       {conv_summary.total_pixels:,}")
    print(f"     有效 DEM 像元:   {conv_summary.valid_dem_pixels:,}")
    print(f"     - 大洋原生插值: {conv_summary.native_mdt_pixels:,} ({conv_summary.native_mdt_pixels / max(1, conv_summary.valid_dem_pixels)*100:.2f}%)")
    print(f"     - 近岸空间外推: {conv_summary.extrapolated_mdt_pixels:,} ({conv_summary.extrapolated_mdt_pixels / max(1, conv_summary.valid_dem_pixels)*100:.2f}%)")
    print(f"     - 超限/NoData:   {conv_summary.nodata_pixels:,}")

    report['conversion'] = {
        'total_pixels': conv_summary.total_pixels,
        'valid_dem_pixels': conv_summary.valid_dem_pixels,
        'native_mdt_pixels': conv_summary.native_mdt_pixels,
        'extrapolated_mdt_pixels': conv_summary.extrapolated_mdt_pixels,
        'nodata_pixels': conv_summary.nodata_pixels,
        'elapsed_seconds': t_conv_elapsed,
        'output_msl_path': OUT_MSL_DEM,
        'output_qc_path': OUT_MSL_QC
    }

    # -------------------------------------------------------------------------
    # 阶段 2: 10,000 点随机采样决策等价性检验
    # -------------------------------------------------------------------------
    print("\n[Phase 2] 抽取 10,000 个随机有效像素进行新旧决策等价性验证...")
    with rasterio.open(DEM_PATH) as src_egm, rasterio.open(OUT_MSL_DEM) as src_msl, rasterio.open(OUT_MSL_QC) as src_qc:
        w = src_egm.width
        h = src_egm.height
        trans = src_egm.transform
        nodata = src_egm.nodata

        # 快速分块均匀抽取 10,000 个有效点
        rng = np.random.RandomState(42)
        valid_coords = []
        for r_off in range(0, h, 1024):
            if len(valid_coords) >= 10000:
                break
            bh = min(1024, h - r_off)
            for c_off in range(0, w, 1024):
                if len(valid_coords) >= 10000:
                    break
                bw = min(1024, w - c_off)
                win = rasterio.windows.Window(c_off, r_off, bw, bh)
                chunk_egm = src_egm.read(1, window=win)
                chunk_msl = src_msl.read(1, window=win)
                chunk_qc = src_qc.read(1, window=win)

                vmask = np.isfinite(chunk_egm) & (chunk_egm != nodata) & np.isfinite(chunk_msl) & (chunk_qc != 2)
                if np.any(vmask):
                    r_idxs, c_idxs = np.where(vmask)
                    sub_n = min(500, len(r_idxs))
                    sub_sel = rng.choice(len(r_idxs), size=sub_n, replace=False)
                    for idx in sub_sel:
                        r_loc = r_idxs[idx]
                        c_loc = c_idxs[idx]
                        r_glob = r_off + r_loc
                        c_glob = c_off + c_loc
                        px_lon, px_lat = trans * (c_glob + 0.5, r_glob + 0.5)
                        valid_coords.append((px_lon, px_lat, float(chunk_egm[r_loc, c_loc]), float(chunk_msl[r_loc, c_loc]), int(chunk_qc[r_loc, c_loc])))
                        if len(valid_coords) >= 10000:
                            break

    assert len(valid_coords) >= 10000, f"有效点数量不足 10,000: {len(valid_coords)}"
    sample_data = np.array(valid_coords)
    s_lons = sample_data[:, 0]
    s_lats = sample_data[:, 1]
    s_z_egm = sample_data[:, 2]
    s_z_msl = sample_data[:, 3]

    print(f"     成功抽取 {len(s_lons):,} 个有效像素点，范围: lon=[{s_lons.min():.4f}, {s_lons.max():.4f}], lat=[{s_lats.min():.4f}, {s_lats.max():.4f}]")

    # 对采样点做 24h 潮位时间序列淹没判定检验
    predictor = FESTidePredictor()
    converter = DEMDatumConverter()
    transformer = DatumTransformer()

    # 计算采样点的 MDT 与 DeltaN
    mdt_pts, qc_pts = converter.evaluate_mdt_with_idw(s_lons, s_lats)
    dn_pts = transformer.get_delta_n(s_lons, s_lats, strict=False)

    # 随机生成一系列潮位时刻检验 (取 24 个离散时刻)
    dt_index = pd.date_range("2024-01-01 00:00:00", "2024-01-01 23:00:00", freq="1h")

    # 取中心点预测一个代表性潮波序列做等价性测试
    center_lon, center_lat = float(s_lons.mean()), float(s_lats.mean())
    df_center = predictor.predict_series(
        lon=center_lon, lat=center_lat,
        start_time="2024-01-01 00:00:00", end_time="2024-01-01 23:00:00",
        freq="1h", constituents="all"
    )
    tide_series = df_center['tide_total_m'].values  # MSL 基准下的纯潮位序列 (24 个时刻)

    total_decision_evaluations = len(s_lons) * len(tide_series)
    mismatches = 0
    max_abs_diff_z = float(np.max(np.abs(s_z_msl - (s_z_egm - mdt_pts - dn_pts))))

    print(f"     高程代数转换残差最大值: {max_abs_diff_z:.6e} 米")
    assert max_abs_diff_z < 1e-4, f"高程转换代数残差超出容差: {max_abs_diff_z}"

    for t_val in tide_series:
        dec_old = (t_val + mdt_pts + dn_pts) > s_z_egm
        dec_new = t_val > s_z_msl
        mismatches += int(np.count_nonzero(dec_old != dec_new))

    consistency_rate = (1.0 - mismatches / total_decision_evaluations) * 100.0
    print(f"     决策等价性检验: 总评估次数 {total_decision_evaluations:,}, 不一致次数 {mismatches}, 一致率: {consistency_rate:.6f}%")
    assert mismatches == 0, f"决策等价性未达 100%: 出现 {mismatches} 次不一致"

    report['decision_equivalence'] = {
        'sample_points': len(s_lons),
        'time_steps': len(tide_series),
        'total_evaluations': total_decision_evaluations,
        'mismatches': mismatches,
        'consistency_rate_pct': consistency_rate,
        'max_elevation_residual_m': max_abs_diff_z
    }

    # -------------------------------------------------------------------------
    # 阶段 3: 24h 潜在淹没频率解算 (Zero-MDT-Lookup MSL Workflow)
    # -------------------------------------------------------------------------
    print("\n[Phase 3] 运行 24h 潜在淹没频率解算 (Zero-MDT-Lookup MSL Workflow)...")
    engine = RasterTideEngine(
        initial_control_spacing_m=4000.0,
        min_control_spacing_m=500.0,
        inundation_error_tolerance_pct=1.0
    )

    t0_inund = time.perf_counter()
    inund_summary = engine.calculate_inundation_raster(
        dem_path=OUT_MSL_DEM,
        output_path=NEW_FREQ_TIF,
        qc_output_path=NEW_QC_TIF,
        start_time="2024-01-01 00:00:00",
        end_time="2024-01-02 00:00:00",
        freq="1h",
        dem_datum="msl",
        constituents="all",
        target_mode="intertidal",
        export_tide_cache_path=NEW_CACHE_NC,
        allow_overwrite=True,
        progress_callback=lambda p, m: print(f"    [{p:3d}%] {m}")
    )
    t_inund_elapsed = time.perf_counter() - t0_inund
    print(f"[OK] v1.7 MSL 淹没频率解算完成！耗时: {t_inund_elapsed:.2f} 秒")
    print(f"     控制节点总数: {inund_summary.control_nodes_count:,}")
    print(f"     有效 DEM 像元: {inund_summary.valid_pixels:,}")

    report['msl_inundation'] = {
        'elapsed_seconds': t_inund_elapsed,
        'control_nodes_count': inund_summary.control_nodes_count,
        'valid_pixels': inund_summary.valid_pixels,
        'total_pixels': inund_summary.total_pixels,
        'output_path': NEW_FREQ_TIF,
        'qc_output_path': NEW_QC_TIF,
        'cache_path': NEW_CACHE_NC
    }

    # -------------------------------------------------------------------------
    # 阶段 4: 与旧版 EGM2008 Workflow 产物进行空间栅格比对
    # -------------------------------------------------------------------------
    print("\n[Phase 4] 与 v1.6 旧版 EGM2008 Workflow 产物进行空间栅格全量比对...")
    if os.path.exists(OLD_FREQ_TIF):
        with rasterio.open(OLD_FREQ_TIF) as src_old, rasterio.open(NEW_FREQ_TIF) as src_new:
            arr_old = src_old.read(1)
            arr_new = src_new.read(1)
            nodata_old = src_old.nodata
            nodata_new = src_new.nodata

            valid_mask = (arr_old != nodata_old) & (arr_new != nodata_new) & np.isfinite(arr_old) & np.isfinite(arr_new)
            n_valid = int(np.count_nonzero(valid_mask))

            diff = np.abs(arr_new[valid_mask] - arr_old[valid_mask])
            max_diff = float(np.max(diff)) if n_valid > 0 else 0.0
            mean_diff = float(np.mean(diff)) if n_valid > 0 else 0.0
            p99_diff = float(np.percentile(diff, 99)) if n_valid > 0 else 0.0

            print(f"     空间像元比对数: {n_valid:,}")
            print(f"     淹没频率绝对差异均值: {mean_diff:.4f}%")
            print(f"     淹没频率 99 分位数:  {p99_diff:.4f}%")
            print(f"     淹没频率最大差异:    {max_diff:.4f}%")

            # 统计时间提速对比
            old_time = None
            if os.path.exists(OLD_PERF_JSON):
                with open(OLD_PERF_JSON, 'r') as f:
                    old_perf = json.load(f)
                    old_time = old_perf.get('whole_wall_seconds')
                    print(f"     旧架构耗时 (Clean Run): {old_time:.2f} s")
                    print(f"     新架构耗时 (MSL Run):   {t_inund_elapsed:.2f} s")
                    if old_time and old_time > 0:
                        speedup = old_time / t_inund_elapsed
                        print(f"     相对提速比: {speedup:.2f}x")

            report['comparison'] = {
                'valid_compared_pixels': n_valid,
                'mean_abs_diff_pct': mean_diff,
                'p99_abs_diff_pct': p99_diff,
                'max_abs_diff_pct': max_diff,
                'old_wall_seconds': old_time,
                'new_wall_seconds': t_inund_elapsed,
                'speedup_ratio': (old_time / t_inund_elapsed) if old_time else None
            }
    else:
        print("[WARN] 未找到旧版基准栅格 smoke_freq_production_clean.tif，跳过栅格差值比对")

    # 保存完整摘要报告
    with open(SUMMARY_JSON, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\n[OK] 完整基准测试报告已输出至: {SUMMARY_JSON}")
    print("================================================================================")
    print("CoastTideX v1.7 MSL Reference Workflow Benchmark SUCCESSFUL")
    print("================================================================================")


if __name__ == '__main__':
    run_v17_benchmark()
