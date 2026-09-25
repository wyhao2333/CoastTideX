"""
CoastTideX v1.6 Beta - Real FES2022b Exposure Oracle Empirical Evaluation Script
对比 Path A (Stage 1 Tide Cache + Stage 2 Exposure Engine) 与 Path B (逐像元直接调用 FES2022b + 1D Oracle)
"""

import os
import sys

# 动态定位项目根目录 (必须包含 core/ 与 config.yaml)
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

import json
import tempfile
import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import Affine
import pyproj

from core.raster_engine import RasterTideEngine
from core.tide_cache import calculate_exposure_from_tide_cache
from core.tide_engine import FESTidePredictor
from core.datum_engine import DatumTransformer
from core.exposure_engine import compute_1d_continuous_exposure


def run_evaluation(num_samples: int = 30):
    print("=" * 70)
    print("CoastTideX v1.6 Beta - Controlled Real-FES2022b Empirical Exposure Oracle")
    print("=" * 70)

    # 1. 确定地理位置: 长江口东侧浅海/潮间带 (122.0°E, 31.0°N)
    lon_center, lat_center = 122.0, 31.0
    transformer_to_utm = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32651", always_xy=True)
    transformer_to_wgs = pyproj.Transformer.from_crs("EPSG:32651", "EPSG:4326", always_xy=True)
    cx, cy = transformer_to_utm.transform(lon_center, lat_center)

    # 2. 构建 30x30 代表性近岸地形 DEM (像元大小 100m, 覆盖 3km x 3km)
    width, height = 30, 30
    res = 100.0
    x0 = cx - (width * res) / 2.0
    y1 = cy + (height * res) / 2.0
    trans = Affine(res, 0.0, x0, 0.0, -res, y1)

    # 高程斜坡: -1.8m 至 +1.8m (完整跨越长江口潮位区间)
    x_coords = np.linspace(0, 1, width)
    y_coords = np.linspace(0, 1, height)
    xx, yy = np.meshgrid(x_coords, y_coords)
    elev = (-1.8 + 3.6 * ((xx + yy) / 2.0)).astype(np.float32)

    temp_dir = tempfile.mkdtemp(prefix="fes_eval_")
    dem_path = os.path.join(temp_dir, "representative_yangtze_dem.tif")
    profile = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": 1,
        "dtype": "float32",
        "crs": "EPSG:32651",
        "transform": trans,
        "nodata": -9999.0
    }
    with rasterio.open(dem_path, "w", **profile) as dst:
        dst.write(elev, 1)

    print(f"[*] 生成代表性近岸 DEM: {dem_path} ({width}x{height}, 高程范围 [{elev.min():.2f}, {elev.max():.2f}] m)")

    # 3. 设定 48 小时分析时段 (2024-01-01 00:00 至 2024-01-03 00:00 UTC, 步长 30min)
    t_start = "2024-01-01 00:00:00"
    t_end = "2024-01-03 00:00:00"
    freq = "30min"

    # Path A: CoastTideX Stage 1 -> Tide Cache -> Stage 2 Exposure Engine
    cache_path = os.path.join(temp_dir, "yangtze_tide.nc")
    out_dir = os.path.join(temp_dir, "exposure_out")

    print("[*] 启动 Path A: Stage 1 自适应四叉树生成与 Tide Cache 序列化...")
    engine = RasterTideEngine(
        initial_control_spacing_m=2000.0,
        min_control_spacing_m=500.0,
        inundation_error_tolerance_pct=1.0
    )
    inund_path = os.path.join(temp_dir, "yangtze_inundation.tif")
    qc_path = os.path.join(temp_dir, "yangtze_inundation_qc.tif")
    engine.calculate_inundation_raster(
        dem_path=dem_path,
        output_path=inund_path,
        qc_output_path=qc_path,
        start_time=t_start,
        end_time=t_end,
        freq=freq,
        dem_datum="egm2008",
        constituents="all",
        export_tide_cache_path=cache_path,
        allow_overwrite=True
    )

    print("[*] 启动 Path A: Stage 2 Exposure Engine 流式解算...")
    res_a = calculate_exposure_from_tide_cache(
        dem_path=dem_path,
        cache_path=cache_path,
        output_dir=out_dir,
        time_chunk_size=50,
        allow_overwrite=True
    )

    # 读取 Path A 产物栅格
    with rasterio.open(res_a["products"].exposure_fraction_path) as s_frac, \
         rasterio.open(res_a["products"].exposure_duration_h_path) as s_dur, \
         rasterio.open(res_a["products"].exposure_max_continuous_h_path) as s_max, \
         rasterio.open(res_a["products"].exposure_event_count_path) as s_cnt:
        arr_frac_a = s_frac.read(1)
        arr_dur_a = s_dur.read(1)
        arr_max_a = s_max.read(1)
        arr_cnt_a = s_cnt.read(1)

    # 4. 抽取 30 个均匀覆盖且有效像元执行 Path B: Direct FES + 1D Oracle
    np.random.seed(12345)
    all_rows, all_cols = np.meshgrid(np.arange(height), np.arange(width), indexing="ij")
    flat_rows = all_rows.flatten()
    flat_cols = all_cols.flatten()
    chosen_indices = np.random.choice(len(flat_rows), size=num_samples, replace=False)

    sampled_rows = flat_rows[chosen_indices]
    sampled_cols = flat_cols[chosen_indices]

    xs, ys = rasterio.transform.xy(trans, sampled_rows, sampled_cols, offset="center")
    lons, lats = transformer_to_wgs.transform(xs, ys)

    print(f"[*] 启动 Path B: 对 {num_samples} 个像元点位逐点调用真实 FES2022b 与 1D Oracle...")
    predictor = FESTidePredictor()
    transformer_datum = DatumTransformer()

    # 时间序列索引 (半开区间 [start, end))
    dr = pd.date_range(t_start, t_end, freq=freq, inclusive="left", tz="UTC")
    ts_seconds = np.asarray([t.timestamp() for t in dr], dtype=np.float64)
    term_ts_sec = pd.Timestamp(t_end, tz="UTC").timestamp()

    # 逐点评估
    records = []
    for i in range(num_samples):
        r = sampled_rows[i]
        c = sampled_cols[i]
        lon_i = lons[i]
        lat_i = lats[i]
        z_i = float(elev[r, c])

        # Path A 对应像元值
        val_frac_a = float(arr_frac_a[r, c])
        val_dur_a = float(arr_dur_a[r, c])
        val_max_a = float(arr_max_a[r, c])
        val_cnt_a = int(arr_cnt_a[r, c])

        # Path B: 直接 FES 时序
        df_tide = predictor.predict_series(
            lon=lon_i, lat=lat_i,
            start_time=t_start, end_time=t_end,
            freq=freq, inclusive="both", constituents="all"
        )
        tides_msl = df_tide["tide_total_m"].to_numpy().astype(np.float32)
        
        # 垂直基准转换至 EGM2008
        res_datum = transformer_datum.convert_tide_datums(
            tide_msl_m=tides_msl,
            lons=lon_i,
            lats=lat_i,
            datum_target="egm2008"
        )
        wl_egm = res_datum['h_egm2008_m']

        wl_series = wl_egm[:-1]
        term_wl = float(wl_egm[-1])

        # Path B: 1D 连续露出解析解
        res_b = compute_1d_continuous_exposure(
            water_levels=wl_series,
            timestamps_seconds=ts_seconds,
            elevation=z_i,
            terminal_water_level=term_wl,
            terminal_timestamp_seconds=term_ts_sec
        )

        val_frac_b = float(res_b["exposure_fraction_pct"])
        val_dur_b = float(res_b["cumulative_exposure_h"])
        val_max_b = float(res_b["max_continuous_exposure_h"])
        val_cnt_b = int(res_b["event_count"])

        records.append({
            "idx": i + 1,
            "row": int(r),
            "col": int(c),
            "lon": round(float(lon_i), 4),
            "lat": round(float(lat_i), 4),
            "z": round(z_i, 3),
            "frac_A": round(val_frac_a, 4),
            "frac_B": round(val_frac_b, 4),
            "dur_A": round(val_dur_a, 4),
            "dur_B": round(val_dur_b, 4),
            "max_A": round(val_max_a, 4),
            "max_B": round(val_max_b, 4),
            "cnt_A": val_cnt_a,
            "cnt_B": val_cnt_b,
            "err_frac": round(val_frac_a - val_frac_b, 4),
            "err_dur": round(val_dur_a - val_dur_b, 4),
            "err_max": round(val_max_a - val_max_b, 4),
            "err_cnt": val_cnt_a - val_cnt_b
        })

    # 5. 统计经验误差指标
    err_frac = np.array([rec["err_frac"] for rec in records])
    err_dur = np.array([rec["err_dur"] for rec in records])
    err_max = np.array([rec["err_max"] for rec in records])
    err_cnt = np.array([rec["err_cnt"] for rec in records])

    def calc_metrics(errors):
        abs_err = np.abs(errors)
        return {
            "bias": round(float(np.mean(errors)), 4),
            "mae": round(float(np.mean(abs_err)), 4),
            "rmse": round(float(np.sqrt(np.mean(errors ** 2))), 4),
            "p95": round(float(np.percentile(abs_err, 95)), 4),
            "max": round(float(np.max(abs_err)), 4)
        }

    summary = {
        "dataset": "Yangtze Estuary (122.0E, 31.0N) Representative Intertidal Mudflat",
        "sample_count": num_samples,
        "time_span_hours": 48.0,
        "time_samples": len(ts_seconds),
        "metrics": {
            "exposure_fraction_pct": calc_metrics(err_frac),
            "exposure_duration_h": calc_metrics(err_dur),
            "exposure_max_continuous_h": calc_metrics(err_max),
            "exposure_event_count": calc_metrics(err_cnt)
        },
        "records": records
    }

    out_dir = os.path.join(PROJECT_ROOT, "validation", "artifacts")
    os.makedirs(out_dir, exist_ok=True)
    out_json = os.path.join(out_dir, "empirical_fes_exposure_oracle_results.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 70)
    print("Real-FES2022b Empirical Oracle 对比统计结果 (30 抽样点)")
    print("=" * 70)
    for m_name, m_dict in summary["metrics"].items():
        print(f"--- 指标: {m_name} ---")
        print(f"  Bias (Mean Error):  {m_dict['bias']:+.4f}")
        print(f"  MAE:                {m_dict['mae']:.4f}")
        print(f"  RMSE:               {m_dict['rmse']:.4f}")
        print(f"  P95 Absolute Error: {m_dict['p95']:.4f}")
        print(f"  Max Absolute Error: {m_dict['max']:.4f}")

    print("=" * 70)
    print(f"[*] 结果已完整保存至: {out_json}")
    return summary


if __name__ == "__main__":
    run_evaluation(30)
