"""
CoastTideX — MDT Coastal Edge Extrapolation Test
(PART A: 真实 MDT 边缘抑制外推距离梯度验证)

严格遵循：
1. 真实 MDT 数据: mdt_cls22/mdt_hybrid_cnes_cls22_cmems2020_global.nc (0.125° x 0.125°, 约 12-14 km 粗网格)
2. 真正 EDGE HOLDOUT: 从 MDT 有效边界向内构造 0.5, 1.0, 2.0, 4.0, 8.0 km 距离带
3. 防信息泄漏: 严格保证 support ∩ holdout == empty，一次性剔除距离 <= D 的全部点
4. 验证对象: MDT 与 Total static offset C = MDT + DeltaN
5. 验证方法: Nearest 与 IDW (k=4, p=2)
6. 空间块补充分析: 西侧河口、北支、南支
"""

import os
import sys
import json
import math
import time
import numpy as np
import pandas as pd
import xarray as xr
from scipy.spatial import cKDTree

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

VERIFY_DIR = os.path.join(PROJECT_ROOT, "validation", "artifacts", "chongming_verify")
MDT_PATH = os.path.join(PROJECT_ROOT, "mdt_cls22", "mdt_hybrid_cnes_cls22_cmems2020_global.nc")
NODES_PATH = os.path.join(VERIFY_DIR, "control_nodes_verified.csv")


def dist_pt_to_seg(px: float, py: float, x1: float, y1: float, x2: float, y2: float) -> float:
    """计算二维平面点到线段的最短欧几里得距离"""
    dx = x2 - x1
    dy = y2 - y1
    l2 = dx * dx + dy * dy
    if l2 == 0:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / l2))
    proj_x = x1 + t * dx
    proj_y = y1 + t * dy
    return math.hypot(px - proj_x, py - proj_y)


def compute_metrics(errors_m: np.ndarray) -> dict:
    """计算详尽误差指标 (米与厘米)"""
    if len(errors_m) == 0:
        return {}
    abs_err_m = np.abs(errors_m)
    abs_err_cm = abs_err_m * 100.0
    err_cm = errors_m * 100.0

    return {
        "bias_m": float(np.mean(errors_m)),
        "bias_cm": float(np.mean(err_cm)),
        "mae_m": float(np.mean(abs_err_m)),
        "mae_cm": float(np.mean(abs_err_cm)),
        "rmse_m": float(np.sqrt(np.mean(errors_m ** 2))),
        "rmse_cm": float(np.sqrt(np.mean(err_cm ** 2))),
        "p50_m": float(np.median(abs_err_m)),
        "p50_cm": float(np.median(abs_err_cm)),
        "p90_m": float(np.percentile(abs_err_m, 90)),
        "p90_cm": float(np.percentile(abs_err_cm, 90)),
        "p95_m": float(np.percentile(abs_err_m, 95)),
        "p95_cm": float(np.percentile(abs_err_cm, 95)),
        "p99_m": float(np.percentile(abs_err_m, 99)),
        "p99_cm": float(np.percentile(abs_err_cm, 99)),
        "max_m": float(np.max(abs_err_m)),
        "max_cm": float(np.max(abs_err_cm))
    }


def main():
    print("=== PART A: MDT Coastal Edge Extrapolation Test 启动 ===")
    t0 = time.time()

    # 1. 读取真实 MDT 网格元数据与数据
    print(f"[*] 读取真实 MDT 数据: {MDT_PATH}")
    assert os.path.exists(MDT_PATH), f"未找到 MDT 文件: {MDT_PATH}"
    ds = xr.open_dataset(MDT_PATH)
    lons = ds['longitude'].values
    lats = ds['latitude'].values
    dlon = float(lons[1] - lons[0])
    dlat = float(lats[1] - lats[0])
    print(f"    MDT 网格规格: 经度分辨率={dlon:.4f}°, 纬度分辨率={dlat:.4f}° (~12-14 km 粗尺度海洋背景场)")

    # 提取长江口区域子网格 (120.0E ~ 123.5E, 30.0N ~ 33.0N)
    lon_mask = (lons >= 120.0) & (lons <= 123.5)
    lat_mask = (lats >= 30.0) & (lats <= 33.0)
    sub_lons = lons[lon_mask]
    sub_lats = lats[lat_mask]
    mdt_grid = ds['mdt'].values[0][np.ix_(lat_mask, lon_mask)]
    ds.close()

    # 2. 提取 Bilinear Valid 网格区域的外边界线段
    # 在双线性插值语义下，一个 0.125° x 0.125° 单元有效当且仅当其 4 个角点全部有限
    n_lat, n_lon = len(sub_lats), len(sub_lons)
    cell_valid = np.zeros((n_lat - 1, n_lon - 1), dtype=bool)
    for i in range(n_lat - 1):
        for j in range(n_lon - 1):
            c00 = np.isfinite(mdt_grid[i, j])
            c01 = np.isfinite(mdt_grid[i, j + 1])
            c10 = np.isfinite(mdt_grid[i + 1, j])
            c11 = np.isfinite(mdt_grid[i + 1, j + 1])
            cell_valid[i, j] = bool(c00 and c01 and c10 and c11)

    mid_lat = 31.5
    deg_lat_km = 111.0
    deg_lon_km = 111.0 * math.cos(math.radians(mid_lat))

    boundary_segs = []
    # 垂直网格边 (x = sub_lons[j])
    for i in range(n_lat - 1):
        for j in range(n_lon):
            left_valid = cell_valid[i, j - 1] if j > 0 else False
            right_valid = cell_valid[i, j] if j < n_lon - 1 else False
            if left_valid != right_valid:
                x_km = sub_lons[j] * deg_lon_km
                y1_km = sub_lats[i] * deg_lat_km
                y2_km = sub_lats[i + 1] * deg_lat_km
                boundary_segs.append(((x_km, y1_km), (x_km, y2_km)))

    # 水平网格边 (y = sub_lats[i])
    for i in range(n_lat):
        for j in range(n_lon - 1):
            bot_valid = cell_valid[i - 1, j] if i > 0 else False
            top_valid = cell_valid[i, j] if i < n_lat - 1 else False
            if bot_valid != top_valid:
                y_km = sub_lats[i] * deg_lat_km
                x1_km = sub_lons[j] * deg_lon_km
                x2_km = sub_lons[j + 1] * deg_lon_km
                boundary_segs.append(((x1_km, y_km), (x2_km, y_km)))

    print(f"    提取 Bilinear-Valid 外边界线段数: {len(boundary_segs)} 条")

    # 3. 读取崇明岛 4,232 个有效 Class D 控制节点
    print(f"[*] 读取崇明岛控制节点: {NODES_PATH}")
    nodes_df = pd.read_csv(NODES_PATH)
    valid_anchors = nodes_df[nodes_df['failure_class'] == 'D_FES_VALID_DATUM_VALID'].copy()
    n_anchors = len(valid_anchors)
    print(f"    有效 Class D 锚点总数: {n_anchors}")

    # 计算各锚点到真实 MDT Bilinear-Valid 边界的最短距离 (km)
    pts_x = valid_anchors['lon'].values * deg_lon_km
    pts_y = valid_anchors['lat'].values * deg_lat_km

    dists_km = []
    for px, py in zip(pts_x, pts_y):
        d = min(dist_pt_to_seg(px, py, s[0][0], s[0][1], s[1][0], s[1][1]) for s in boundary_segs)
        dists_km.append(d)

    valid_anchors['dist_to_boundary_km'] = dists_km
    valid_anchors['x_km'] = pts_x
    valid_anchors['y_km'] = pts_y

    distances_test = [0.5, 1.0, 2.0, 4.0, 8.0]
    results_json = {
        "metadata": {
            "test_name": "MDT Coastal Edge Extrapolation Test",
            "mdt_file": os.path.basename(MDT_PATH),
            "mdt_resolution_deg": [dlat, dlon],
            "mdt_resolution_approx_km": 0.125 * 111.0,
            "anchor_count": n_anchors,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        },
        "edge_holdout": {},
        "spatial_blocks": {}
    }
    csv_rows = []

    # 4. 逐距离执行边缘条带留出评估 (Edge Holdout Strip Evaluation)
    print("[*] 正在执行 0.5km, 1.0km, 2.0km, 4.0km, 8.0km 严格边缘留出测试...")

    for d in distances_test:
        holdout_mask = (valid_anchors['dist_to_boundary_km'] <= d)
        holdout_set = valid_anchors[holdout_mask]
        support_set = valid_anchors[~holdout_mask]

        n_truth = len(holdout_set)
        n_support = len(support_set)

        # 严格防信息泄漏检验
        common_ids = set(holdout_set['node_id']).intersection(set(support_set['node_id']))
        assert len(common_ids) == 0, f"严重错误: support 与 holdout 存在交集: {common_ids}"

        print(f"  [D = {d:3.1f} km] Holdout={n_truth:4d}, Support={n_support:4d} (保留率={n_support/n_anchors*100:.1f}%)")

        if n_truth == 0 or n_support < 4:
            results_json["edge_holdout"][f"{d}km"] = {
                "status": "INSUFFICIENT EVIDENCE",
                "n_truth": n_truth,
                "n_support": n_support
            }
            continue

        sup_coords = np.column_stack([support_set['x_km'].values, support_set['y_km'].values])
        sup_mdt = support_set['mdt_value'].values
        sup_offset = support_set['datum_offset_m'].values

        tgt_coords = np.column_stack([holdout_set['x_km'].values, holdout_set['y_km'].values])
        tgt_mdt_truth = holdout_set['mdt_value'].values
        tgt_offset_truth = holdout_set['datum_offset_m'].values

        sup_tree = cKDTree(sup_coords)

        # M0: Nearest Neighbor
        dists_nn, idxs_nn = sup_tree.query(tgt_coords, k=1)
        pred_mdt_nn = sup_mdt[idxs_nn]
        pred_offset_nn = sup_offset[idxs_nn]

        err_mdt_nn = pred_mdt_nn - tgt_mdt_truth
        err_offset_nn = pred_offset_nn - tgt_offset_truth

        # M1: IDW (k=4, p=2)
        dists_idw, idxs_idw = sup_tree.query(tgt_coords, k=4)
        pred_mdt_idw = []
        pred_offset_idw = []
        for i in range(n_truth):
            d_vec = dists_idw[i]
            idx_vec = idxs_idw[i]
            if np.any(d_vec < 1e-6):
                hit = np.argmin(d_vec)
                pred_mdt_idw.append(sup_mdt[idx_vec[hit]])
                pred_offset_idw.append(sup_offset[idx_vec[hit]])
            else:
                w = 1.0 / (d_vec ** 2)
                w_norm = w / np.sum(w)
                pred_mdt_idw.append(float(np.sum(w_norm * sup_mdt[idx_vec])))
                pred_offset_idw.append(float(np.sum(w_norm * sup_offset[idx_vec])))

        pred_mdt_idw = np.array(pred_mdt_idw)
        pred_offset_idw = np.array(pred_offset_idw)

        err_mdt_idw = pred_mdt_idw - tgt_mdt_truth
        err_offset_idw = pred_offset_idw - tgt_offset_truth

        m0_mdt_metrics = compute_metrics(err_mdt_nn)
        m0_off_metrics = compute_metrics(err_offset_nn)
        m1_mdt_metrics = compute_metrics(err_mdt_idw)
        m1_off_metrics = compute_metrics(err_offset_idw)

        dist_entry = {
            "status": "VALID_EVALUATION",
            "n_truth": n_truth,
            "n_predicted": n_truth,
            "n_support": n_support,
            "coverage": float(n_truth / n_truth * 100.0),
            "target_mdt": {
                "M0_Nearest": m0_mdt_metrics,
                "M1_IDW_k4_p2": m1_mdt_metrics
            },
            "target_total_offset": {
                "M0_Nearest": m0_off_metrics,
                "M1_IDW_k4_p2": m1_off_metrics
            }
        }
        results_json["edge_holdout"][f"{d}km"] = dist_entry

        # 加入 CSV 记录 (重点记录最关键的 M1 IDW 和 M0 Nearest)
        for method_name, mdt_m, off_m in [("M0_Nearest", m0_mdt_metrics, m0_off_metrics),
                                          ("M1_IDW_k4_p2", m1_mdt_metrics, m1_off_metrics)]:
            csv_rows.append({
                "distance_km": d,
                "method": method_name,
                "n_truth": n_truth,
                "n_support": n_support,
                "coverage_pct": 100.0,
                "mdt_bias_cm": mdt_m["bias_cm"],
                "mdt_mae_cm": mdt_m["mae_cm"],
                "mdt_rmse_cm": mdt_m["rmse_cm"],
                "mdt_p50_cm": mdt_m["p50_cm"],
                "mdt_p90_cm": mdt_m["p90_cm"],
                "mdt_p95_cm": mdt_m["p95_cm"],
                "mdt_p99_cm": mdt_m["p99_cm"],
                "mdt_max_cm": mdt_m["max_cm"],
                "offset_bias_cm": off_m["bias_cm"],
                "offset_mae_cm": off_m["mae_cm"],
                "offset_rmse_cm": off_m["rmse_cm"],
                "offset_p50_cm": off_m["p50_cm"],
                "offset_p90_cm": off_m["p90_cm"],
                "offset_p95_cm": off_m["p95_cm"],
                "offset_p99_cm": off_m["p99_cm"],
                "offset_max_cm": off_m["max_cm"]
            })

    # 5. 补充空间块交叉验证 (Spatial Blocks Cross-Validation)
    print("[*] 正在执行西侧河口、北支、南支三大空间块补充验证...")
    blocks = {
        "western_estuary": (valid_anchors['lon'] < 121.65) & (valid_anchors['lat'] >= 31.35) & (valid_anchors['lat'] <= 31.60),
        "north_branch": (valid_anchors['lat'] >= 31.65) & (valid_anchors['lon'] >= 121.50) & (valid_anchors['lon'] <= 122.00),
        "south_branch": (valid_anchors['lat'] <= 31.45) & (valid_anchors['lon'] >= 121.50) & (valid_anchors['lon'] <= 122.00)
    }

    for blk_name, blk_mask in blocks.items():
        blk_targets = valid_anchors[blk_mask]
        blk_support = valid_anchors[~blk_mask]
        n_t = len(blk_targets)
        n_s = len(blk_support)

        if n_t < 2 or n_s < 4:
            results_json["spatial_blocks"][blk_name] = {"status": "INSUFFICIENT_SAMPLES", "n_truth": n_t, "n_support": n_s}
            continue

        s_coords = np.column_stack([blk_support['x_km'].values, blk_support['y_km'].values])
        s_mdt = blk_support['mdt_value'].values
        s_off = blk_support['datum_offset_m'].values
        s_tree = cKDTree(s_coords)

        t_coords = np.column_stack([blk_targets['x_km'].values, blk_targets['y_km'].values])
        t_mdt = blk_targets['mdt_value'].values
        t_off = blk_targets['datum_offset_m'].values

        d_vecs, idx_vecs = s_tree.query(t_coords, k=4)
        pred_mdt_idw = []
        pred_off_idw = []
        for i in range(n_t):
            d = d_vecs[i]
            idx = idx_vecs[i]
            if np.any(d < 1e-6):
                hit = np.argmin(d)
                pred_mdt_idw.append(s_mdt[idx[hit]])
                pred_off_idw.append(s_off[idx[hit]])
            else:
                w = 1.0 / (d ** 2)
                w_norm = w / np.sum(w)
                pred_mdt_idw.append(float(np.sum(w_norm * s_mdt[idx])))
                pred_off_idw.append(float(np.sum(w_norm * s_off[idx])))

        err_m = np.array(pred_mdt_idw) - t_mdt
        err_o = np.array(pred_off_idw) - t_off

        blk_res = {
            "status": "VALID_EVALUATION",
            "n_truth": n_t,
            "n_support": n_s,
            "mdt_metrics": compute_metrics(err_m),
            "offset_metrics": compute_metrics(err_o)
        }
        results_json["spatial_blocks"][blk_name] = blk_res
        print(f"    Block '{blk_name}': n_truth={n_t}, n_support={n_s}, MDT MAE={blk_res['mdt_metrics']['mae_cm']:.2f} cm, Offset MAE={blk_res['offset_metrics']['mae_cm']:.2f} cm")

    # 6. 保存产物
    json_path = os.path.join(VERIFY_DIR, "mdt_edge_holdout_final.json")
    with open(json_path, "w", encoding="utf-8") as fp:
        json.dump(results_json, fp, indent=2, ensure_ascii=False)

    csv_path = os.path.join(VERIFY_DIR, "mdt_edge_holdout_final.csv")
    pd.DataFrame(csv_rows).to_csv(csv_path, index=False, encoding="utf-8-sig")

    print(f"[OK] PART A 评估完成，总耗时 {time.time()-t0:.2f}s！")
    print(f"     -> JSON: {json_path}")
    print(f"     -> CSV : {csv_path}")


if __name__ == "__main__":
    main()
