"""
CoastTideX — Clean Performance Benchmark
(PART B: Current vs ParentBBox 严格独立性能基准测试)

支持命令:
1. python -u scripts/benchmark_clean_performance.py --mode current
2. python -u scripts/benchmark_clean_performance.py --mode parent_bbox
3. python -u scripts/benchmark_clean_performance.py --mode compare
"""

import os
import sys
import time
import json
import argparse
import threading
import numpy as np
import rasterio

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

try:
    import psutil
except ImportError:
    psutil = None

import pyfes
from core.tide_engine import FESTidePredictor
from core.datum_engine import DatumTransformer
from core.raster_engine import RasterTideEngine
from core.tide_cache import read_tide_cache

VERIFY_DIR = os.path.join(PROJECT_ROOT, "validation", "artifacts", "chongming_verify")
DEM_PATH = "F:/1-Research/China_tidal-flat_terrain(ICESat2)/3-Result/ChongMing_NoClipped(2021-2024)/ChongMing/2024/ChongMing_2024_Elevation.tif"


class PyfesEvaluateWrapper:
    """真实 pyfes.evaluate_tide 调用计量包装器"""
    def __init__(self, stats_dict: dict):
        self.stats = stats_dict
        self.orig_evaluate_tide = pyfes.evaluate_tide

    def __enter__(self):
        def _wrapped_evaluate(model, dates, lons, lats):
            t0 = time.perf_counter()
            n_pts = len(dates) if hasattr(dates, '__len__') else 1
            self.stats['pyfes_evaluate_calls'] += 1
            self.stats['pyfes_evaluate_points'] += n_pts
            try:
                return self.orig_evaluate_tide(model, dates, lons, lats)
            finally:
                self.stats['pyfes_evaluate_seconds'] += (time.perf_counter() - t0)

        pyfes.evaluate_tide = _wrapped_evaluate
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pyfes.evaluate_tide = self.orig_evaluate_tide


class CleanInstrumentedPredictor(FESTidePredictor):
    """
    可计量 FES 调用与模型加载指标的专用 Predictor。
    严格支持 Current (逐子包围框)、ParentBBox (实验性父包围框复用) 与 Production (生产代码自动化生命周期) 三种模式。
    """
    def __init__(self, mode: str = "current", enable_parent_bbox: bool = False, parent_bbox: tuple = None, stats_dict: dict = None):
        super().__init__()
        self.mode = mode
        self.enable_parent_bbox = enable_parent_bbox or (mode == "parent_bbox")
        self.parent_bbox = parent_bbox
        self.stats = stats_dict if stats_dict is not None else {}
        self._tile_model_cache = {}

    def _get_model(self, bbox: tuple, constituents: list):
        const_key = ",".join(sorted(constituents))
        target_bbox = (float(bbox[0]), max(-90.0, float(bbox[1])), float(bbox[2]), min(90.0, float(bbox[3])))
        self.stats['unique_requested_bboxes'].append(list(target_bbox))

        if self.mode == "production":
            # 生产模式：完全由 production 的 FESTidePredictor 与 RasterTideEngine 调度
            from core.tide_engine import bbox_contains
            is_cache_hit = False
            if getattr(self, '_active_parent_bboxes', None):
                for pb in self._active_parent_bboxes:
                    if bbox_contains(pb, target_bbox):
                        if (const_key, pb) in self._parent_model_cache:
                            is_cache_hit = True
                        break
            else:
                if (self._cached_model is not None and
                    self._cached_bbox == target_bbox and
                    self._cached_constituents == sorted(constituents)):
                    is_cache_hit = True

            if is_cache_hit:
                self.stats['model_cache_hits'] += 1
            else:
                self.stats['model_cache_misses'] += 1
                self.stats['model_load_count'] += 1

            t0 = time.perf_counter()
            model = super()._get_model(bbox, constituents)
            if not is_cache_hit:
                self.stats['model_load_seconds'] += (time.perf_counter() - t0)
            return model

        # ParentBBox 实验模式
        elif self.enable_parent_bbox and self.parent_bbox is not None:
            pb = self.parent_bbox
            # B4. ParentBBox guard: 严格检查 parent 是否包含 requested bbox
            if not (target_bbox[0] >= pb[0] - 1e-5 and target_bbox[1] >= pb[1] - 1e-5 and
                    target_bbox[2] <= pb[2] + 1e-5 and target_bbox[3] <= pb[3] + 1e-5):
                raise ValueError(
                    f"ParentBBox Guard 校验失败: 请求包围框 {target_bbox} 超出父瓦片包围框 {pb}！"
                )

            if const_key in self._tile_model_cache:
                self.stats['model_cache_hits'] += 1
                return self._tile_model_cache[const_key]

            self.stats['model_cache_misses'] += 1
            self.stats['model_load_count'] += 1
            t0 = time.perf_counter()
            import pyfes.config as cfg
            lgp = cfg.LGP(
                path=self.ns_grid_path,
                type='lgp2',
                codes='lgp2',
                constituents=constituents,
                bbox=(pb[0], pb[1], pb[2], pb[3])
            )
            model = lgp.load()
            self.stats['model_load_seconds'] += (time.perf_counter() - t0)
            self._tile_model_cache[const_key] = model
            return model
        else:
            # Current P0 模式: 每次使用子包围框
            if (self._cached_model is not None and
                    self._cached_bbox == target_bbox and
                    self._cached_constituents == sorted(constituents)):
                self.stats['model_cache_hits'] += 1
                return self._cached_model

            self.stats['model_cache_misses'] += 1
            self.stats['model_load_count'] += 1
            t0 = time.perf_counter()
            import pyfes.config as cfg
            lgp = cfg.LGP(
                path=self.ns_grid_path,
                type='lgp2',
                codes='lgp2',
                constituents=constituents,
                bbox=(target_bbox[0], target_bbox[1], target_bbox[2], target_bbox[3])
            )
            model = lgp.load()
            self.stats['model_load_seconds'] += (time.perf_counter() - t0)
            self._cached_model = model
            self._cached_bbox = target_bbox
            self._cached_constituents = sorted(constituents)
            return model

    def predict_points_period(self, lons, lats, *args, **kwargs):
        self.stats['predict_points_period_calls'] += 1
        return super().predict_points_period(lons, lats, *args, **kwargs)


class MemoryMonitorThread(threading.Thread):
    """常驻后台内存采样线程，精确捕获最高峰值 RSS"""
    def __init__(self, interval_sec: float = 0.2):
        super().__init__(daemon=True)
        self.interval_sec = interval_sec
        self.stop_event = threading.Event()
        self.peak_rss_bytes = 0
        if psutil:
            self.proc = psutil.Process()
            self.peak_rss_bytes = self.proc.memory_info().rss
        else:
            self.proc = None

    def run(self):
        while not self.stop_event.is_set():
            if self.proc:
                try:
                    rss = self.proc.memory_info().rss
                    if rss > self.peak_rss_bytes:
                        self.peak_rss_bytes = rss
                except Exception:
                    pass
            time.sleep(self.interval_sec)

    def stop(self) -> int:
        self.stop_event.set()
        if self.proc:
            try:
                rss = self.proc.memory_info().rss
                if rss > self.peak_rss_bytes:
                    self.peak_rss_bytes = rss
            except Exception:
                pass
        return self.peak_rss_bytes


def run_single_benchmark(mode: str):
    """执行单个独立模式 (current 或 parent_bbox)"""
    print(f"=== 启动独立基准测试 [MODE = {mode.upper()}] ===")
    t_start = time.time()

    proc = psutil.Process() if psutil else None
    rss_start = proc.memory_info().rss if proc else 0

    mem_monitor = MemoryMonitorThread(interval_sec=0.2)
    mem_monitor.start()

    stats = {
        'whole_wall_seconds': 0.0,
        'model_load_count': 0,
        'model_load_seconds': 0.0,
        'model_cache_hits': 0,
        'model_cache_misses': 0,
        'predict_points_period_calls': 0,
        'pyfes_evaluate_calls': 0,
        'pyfes_evaluate_points': 0,
        'pyfes_evaluate_seconds': 0.0,
        'stage1_seconds': 0.0,
        'stage2_seconds': 0.0,
        'rss_start_bytes': rss_start,
        'peak_rss_bytes': 0,
        'rss_end_bytes': 0,
        'unique_requested_bboxes': []
    }

    # 参数设置：严格固定保持完全一致
    # 崇明 DEM 空间范围: [120.9999, 30.9999, 122.2215, 32.0001]
    # ParentBBox 采用整景 DEM 范围加标准 1.0 度缓冲
    parent_bbox = (119.5, 29.5, 123.5, 33.5) if mode == "parent_bbox" else None

    predictor = CleanInstrumentedPredictor(
        mode=mode,
        enable_parent_bbox=(mode == "parent_bbox"),
        parent_bbox=parent_bbox,
        stats_dict=stats
    )

    transformer = DatumTransformer()
    engine = RasterTideEngine(
        predictor=predictor,
        transformer=transformer,
        initial_control_spacing_m=4000.0,
        min_control_spacing_m=500.0,
        inundation_error_tolerance_pct=1.0
    )

    out_tif = os.path.join(VERIFY_DIR, f"smoke_freq_{mode}_clean.tif")
    out_qc = os.path.join(VERIFY_DIR, f"smoke_qc_{mode}_clean.tif")
    out_cache = os.path.join(VERIFY_DIR, f"smoke_cache_{mode}_clean.nc")

    # 定时心跳监控
    stop_heartbeat = threading.Event()
    def _heartbeat():
        while not stop_heartbeat.is_set():
            time.sleep(15.0)
            if stop_heartbeat.is_set():
                break
            el = time.time() - t_start
            cur_rss_mb = (proc.memory_info().rss / 1024 / 1024) if proc else 0
            print(f"  [Heartbeat {el:5.1f}s] Mode={mode}, Loads={stats['model_load_count']} ({stats['model_load_seconds']:.1f}s), EvalCalls={stats['pyfes_evaluate_calls']}, RSS={cur_rss_mb:.1f}MB")

    hb_thread = threading.Thread(target=_heartbeat, daemon=True)
    hb_thread.start()

    # 运行 24h 完整淹没频率解算
    try:
        with PyfesEvaluateWrapper(stats):
            t_s1 = time.time()
            summary = engine.calculate_inundation_raster(
                dem_path=DEM_PATH,
                output_path=out_tif,
                qc_output_path=out_qc,
                start_time="2024-01-01 00:00:00",
                end_time="2024-01-02 00:00:00",
                freq="1h",
                dem_datum="egm2008",
                constituents="all",
                target_mode="intertidal",
                export_tide_cache_path=out_cache,
                allow_overwrite=True
            )
            t_end_calc = time.time()
    finally:
        stop_heartbeat.set()

    t_whole_wall = time.time() - t_start
    peak_rss = mem_monitor.stop()
    rss_end = proc.memory_info().rss if proc else 0

    stats['whole_wall_seconds'] = float(t_whole_wall)
    stats['stage1_seconds'] = float(summary.elapsed_seconds if hasattr(summary, 'elapsed_seconds') else t_whole_wall)
    stats['peak_rss_bytes'] = int(peak_rss)
    stats['rss_end_bytes'] = int(rss_end)

    # 打印结果与 B3 Sanity Checks
    print("\n--- B3. Instrumentation 健全性校验 (Sanity Checks) ---")
    print(f"1. whole_wall_seconds: {stats['whole_wall_seconds']:.2f} s")
    print(f"2. model_load_count  : {stats['model_load_count']}")
    print(f"3. model_load_seconds: {stats['model_load_seconds']:.2f} s (<= whole_wall: {stats['model_load_seconds'] <= stats['whole_wall_seconds']})")
    print(f"4. predict_calls     : {stats['predict_points_period_calls']} (> 0: {stats['predict_points_period_calls'] > 0})")
    print(f"5. pyfes_calls       : {stats['pyfes_evaluate_calls']} (> 0: {stats['pyfes_evaluate_calls'] > 0})")
    print(f"6. pyfes_points      : {stats['pyfes_evaluate_points']} (> 0: {stats['pyfes_evaluate_points'] > 0})")
    print(f"7. pyfes_seconds     : {stats['pyfes_evaluate_seconds']:.2f} s (> 0: {stats['pyfes_evaluate_seconds'] > 0})")
    print(f"8. peak_rss_mb       : {stats['peak_rss_bytes'] / 1024 / 1024:.1f} MB")

    assert stats['model_load_seconds'] <= stats['whole_wall_seconds'], "FAIL: model_load_seconds > whole_wall_seconds"
    assert stats['predict_points_period_calls'] > 0, "FAIL: predict_points_period_calls == 0"
    assert stats['pyfes_evaluate_calls'] > 0, "FAIL: pyfes_evaluate_calls == 0"
    assert stats['pyfes_evaluate_points'] > 0, "FAIL: pyfes_evaluate_points == 0"
    assert stats['pyfes_evaluate_seconds'] > 0, "FAIL: pyfes_evaluate_seconds == 0"
    print("[OK] 全部 B3 健全性检查通过 (INSTRUMENTATION VALID)！")

    out_json = os.path.join(VERIFY_DIR, f"performance_{mode}_clean.json")
    with open(out_json, "w", encoding="utf-8") as fp:
        json.dump(stats, fp, indent=2)
    print(f"[OK] 结果已保存: {out_json}")


def compare_clean_runs(target_mode: str = "parent_bbox"):
    """对比两组独立运行的科学等价性与加速比 (Compare mode: parent_bbox or production vs current)"""
    print(f"=== PART B: 对比 Current 与 {target_mode.upper()} 纯净基准结果 ===")
    p_curr_json = os.path.join(VERIFY_DIR, "performance_current_clean.json")
    p_par_json = os.path.join(VERIFY_DIR, f"performance_{target_mode}_clean.json")

    assert os.path.exists(p_curr_json), f"缺失当前基线结果: {p_curr_json}"
    assert os.path.exists(p_par_json), f"缺失 {target_mode} 结果: {p_par_json}"

    with open(p_curr_json, "r", encoding="utf-8") as fp:
        c_stats = json.load(fp)
    with open(p_par_json, "r", encoding="utf-8") as fp:
        p_stats = json.load(fp)

    # 1. 科学输出等价性比对
    c_cache_file = os.path.join(VERIFY_DIR, "smoke_cache_current_clean.nc")
    p_cache_file = os.path.join(VERIFY_DIR, f"smoke_cache_{target_mode}_clean.nc")
    cache_c = read_tide_cache(c_cache_file, load_raw_tide=True)
    cache_p = read_tide_cache(p_cache_file, load_raw_tide=True)

    nodes_c = cache_c['nodes']
    nodes_p = cache_p['nodes']
    cells_c = cache_c['leaf_cells']
    cells_p = cache_p['leaf_cells']

    assert len(nodes_c) == len(nodes_p), f"控制节点数不等: {len(nodes_c)} vs {len(nodes_p)}"
    assert len(cells_c) == len(cells_p), f"叶单元数不等: {len(cells_c)} vs {len(cells_p)}"

    node_coords_match = all(nc.lon == np_node.lon and nc.lat == np_node.lat for nc, np_node in zip(nodes_c, nodes_p))
    node_valid_match = all(nc.valid == np_node.valid for nc, np_node in zip(nodes_c, nodes_p))

    tide_diffs = []
    for nc, np_node in zip(nodes_c, nodes_p):
        if nc.valid:
            if nc.tide_msl_raw is not None and np_node.tide_msl_raw is not None:
                tide_diffs.append(np.max(np.abs(nc.tide_msl_raw - np_node.tide_msl_raw)))
            elif len(nc.water_levels_sorted) > 0 and len(np_node.water_levels_sorted) > 0:
                tide_diffs.append(np.max(np.abs(nc.water_levels_sorted - np_node.water_levels_sorted)))

    max_tide_diff = float(np.max(tide_diffs)) if len(tide_diffs) > 0 else 0.0

    # 栅格比对
    c_tif = os.path.join(VERIFY_DIR, "smoke_freq_current_clean.tif")
    p_tif = os.path.join(VERIFY_DIR, f"smoke_freq_{target_mode}_clean.tif")
    c_qc = os.path.join(VERIFY_DIR, "smoke_qc_current_clean.tif")
    p_qc = os.path.join(VERIFY_DIR, f"smoke_qc_{target_mode}_clean.tif")

    with rasterio.open(c_tif) as sc, rasterio.open(p_tif) as sp:
        fc = sc.read(1)
        fp = sp.read(1)
    with rasterio.open(c_qc) as sqc_c, rasterio.open(p_qc) as sqc_p:
        qc_c = sqc_c.read(1)
        qc_p = sqc_p.read(1)

    finite_mask_c = np.isfinite(fc)
    finite_mask_p = np.isfinite(fp)
    finite_mask_equal = bool(np.array_equal(finite_mask_c, finite_mask_p))
    nodata_equal = bool(np.array_equal(np.isnan(fc), np.isnan(fp)))

    valid_diff = np.abs(fc[finite_mask_c] - fp[finite_mask_c])
    max_abs_diff = float(np.max(valid_diff)) if len(valid_diff) > 0 else 0.0
    mean_abs_diff = float(np.mean(valid_diff)) if len(valid_diff) > 0 else 0.0
    p99_abs_diff = float(np.percentile(valid_diff, 99)) if len(valid_diff) > 0 else 0.0
    diff_pixel_count = int(np.sum(valid_diff > 1e-7))

    qc_array_equal = bool(np.array_equal(qc_c, qc_p))

    equiv_status = "SCIENTIFICALLY IDENTICAL" if (qc_array_equal and nodata_equal and max_abs_diff <= 1e-7 and max_tide_diff <= 1e-7) else "NOT IDENTICAL"
    print(f"科学等价性判定: {equiv_status}")
    print(f"  Max Tide Diff: {max_tide_diff:.6e} m")
    print(f"  Max Freq Diff: {max_abs_diff:.6e}, Diff Pixels: {diff_pixel_count}")
    print(f"  QC Equal: {qc_array_equal}, NoData Equal: {nodata_equal}")

    # 2. 真实加速比与内存对比
    wall_curr = c_stats['whole_wall_seconds']
    wall_par = p_stats['whole_wall_seconds']
    speedup = float(wall_curr / wall_par)

    rss_curr_mb = float(c_stats['peak_rss_bytes'] / 1024 / 1024)
    rss_par_mb = float(p_stats['peak_rss_bytes'] / 1024 / 1024)
    rss_increase_mb = rss_par_mb - rss_curr_mb
    rss_ratio = float(rss_par_mb / rss_curr_mb)

    comparison = {
        "scientific_equivalence": {
            "status": equiv_status,
            "target_mode": target_mode,
            "node_count": len(nodes_c),
            "leaf_cell_count": len(cells_c),
            "node_coords_match": node_coords_match,
            "node_valid_match": node_valid_match,
            "max_tide_array_diff": max_tide_diff,
            "max_freq_diff": max_abs_diff,
            "mean_freq_diff": mean_abs_diff,
            "p99_freq_diff": p99_abs_diff,
            "different_pixel_count": diff_pixel_count,
            "qc_array_equal": qc_array_equal,
            "nodata_equal": nodata_equal
        },
        "performance_comparison": {
            "current_whole_wall_seconds": wall_curr,
            f"{target_mode}_whole_wall_seconds": wall_par,
            "speedup_ratio": speedup,
            "current_model_load_count": c_stats['model_load_count'],
            f"{target_mode}_model_load_count": p_stats['model_load_count'],
            "current_model_load_seconds": c_stats['model_load_seconds'],
            f"{target_mode}_model_load_seconds": p_stats['model_load_seconds'],
            "current_pyfes_evaluate_points": c_stats['pyfes_evaluate_points'],
            f"{target_mode}_pyfes_evaluate_points": p_stats['pyfes_evaluate_points'],
            "current_peak_rss_mb": rss_curr_mb,
            f"{target_mode}_peak_rss_mb": rss_par_mb,
            "ram_increase_mb": rss_increase_mb,
            "ram_ratio": rss_ratio
        }
    }

    comp_filename = f"performance_{target_mode}_comparison.json" if target_mode != "parent_bbox" else "performance_clean_comparison.json"
    comp_path = os.path.join(VERIFY_DIR, comp_filename)
    with open(comp_path, "w", encoding="utf-8") as fp:
        json.dump(comparison, fp, indent=2)

    print("\n--- 最终性能对决总结 ---")
    print(f"Current Wall Time    : {wall_curr:.1f} s (Model load: {c_stats['model_load_count']} 次, {c_stats['model_load_seconds']:.1f} s)")
    print(f"{target_mode.upper()} Wall Time : {wall_par:.1f} s (Model load: {p_stats['model_load_count']} 次, {p_stats['model_load_seconds']:.1f} s)")
    print(f"真实加速比 (Speedup)  : {speedup:.2f}x")
    print(f"Peak RAM 变化        : {rss_curr_mb:.1f} MB -> {rss_par_mb:.1f} MB (+{rss_increase_mb:.1f} MB, {rss_ratio:.2f}x)")
    print(f"[OK] 对比报告已保存: {comp_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["current", "parent_bbox", "production", "compare", "compare_production"], required=True)
    args = parser.parse_args()

    if args.mode in ["current", "parent_bbox", "production"]:
        run_single_benchmark(args.mode)
    elif args.mode == "compare":
        compare_clean_runs("parent_bbox")
    elif args.mode == "compare_production":
        compare_clean_runs("production")
