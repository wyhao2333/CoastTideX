"""
CoastTideX 崇明岛垂直基准支持诊断与全球潮滩扩展性剖析器
(Chongming Estuary Datum-Support Diagnosis & Global Tidal-Flat Scalability Profiler)

本脚本为只读诊断工具，不侵入修改正式科学算法与原始数据。
功能包括:
1. 控制节点有效性归因分解 (FES, MDT, Delta-N, EGM2008, Topology)；
2. MDT 原始网格与双线性插值 NaN 膨胀分析 (true NoData vs bilinear-induced NaN)；
3. 像元级 QC=68 溯源归因 (All FES Invalid, All MDT Invalid, Mixed, Topology-only)；
4. 沿海基准支持扩展原型 (Coastal Datum Support Extension) 与留一法交叉验证 (LOOCV)；
5. 分阶段性能剖析器 (T0 ~ T10) 与 FES 调用/缓存命中统计。
"""

import os
import sys
import time
import json
import argparse
import tracemalloc
from dataclasses import dataclass, field, asdict
from typing import Optional, Tuple, List, Dict, Any, Sequence, Callable

import numpy as np
import pandas as pd
import rasterio
from scipy.interpolate import RegularGridInterpolator
from scipy.spatial import cKDTree
import xarray as xr

# 确保能从根目录导入 core
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.utils import (
    load_app_config, resolve_project_path, normalize_longitude,
    circular_longitude_span, build_circular_fes_bboxes, build_time_index,
    compute_inundation_frequency
)
from core.datum_engine import DatumTransformer, DatumDataError
from core.tide_engine import FESTidePredictor, ALL_34_CONSTITUENTS
from core.raster_engine import (
    RasterTideEngine, RasterInfo, ControlNode, QuadCell,
    build_support_topology, resolve_topology_compatible_corners,
    QC_BIT_VALID, QC_BIT_FES_EXTRAPOLATED, QC_BIT_SPATIAL_FALLBACK,
    QC_BIT_INSUFFICIENT_NODES, QC_BIT_DATUM_INVALID, QC_BIT_DATUM_SOURCE_APPROX,
    QC_BIT_MIN_SPACING_REACHED, QC_BIT_CONNECTIVITY_FALLBACK,
    QC_BIT_FES_VALIDITY_BOUNDARY, QC_BIT_MAX_REFINEMENT_REACHED
)


# ==============================================================================
# 1. 失败类别定义 (Failure Class Definitions)
# ==============================================================================
CLASS_FES_INVALID = "A_FES_INVALID"
CLASS_FES_VALID_MDT_INVALID = "B_FES_VALID_MDT_INVALID"
CLASS_FES_VALID_MDT_VALID_DELTAN_INVALID = "C_FES_VALID_MDT_VALID_DELTAN_INVALID"
CLASS_FES_VALID_DATUM_VALID = "D_FES_VALID_DATUM_VALID"
CLASS_OTHER_DATUM_FAILURE = "E_OTHER_DATUM_FAILURE"


@dataclass
class NodeDiagnosticRecord:
    node_id: int
    lon: float
    lat: float
    x: float
    y: float
    component_id: int
    level: int
    fes_any_finite: bool
    fes_valid_fraction: float
    fes_flag_min: int
    mdt_finite: bool
    mdt_value: float
    delta_n_finite: bool
    delta_n_value: float
    egm_undulation_finite: bool
    egm_value: float
    datum_offset_finite: bool
    datum_offset_m: float
    final_solution_valid: bool
    failure_class: str
    dist_to_nearest_valid_mdt_km: float = float('nan')


# ==============================================================================
# 2. 仪器化与缓存感知的 FESTidePredictor (用于可控剖析，不改正式代码)
# ==============================================================================
class InstrumentedFESTidePredictor(FESTidePredictor):
    """
    可计量 FES 调用与模型加载指标的 Predictor 派生类。
    可选启用 bounded LRU / tile bbox 缓存以支持消融实验。
    """
    def __init__(self, *args, enable_tile_bbox_cache: bool = False, parent_bbox: Optional[Tuple[float, float, float, float]] = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.stats = {
            'model_load_count': 0,
            'model_load_time_sec': 0.0,
            'predict_period_calls': 0,
            'evaluate_tide_calls': 0,
            'evaluated_node_time_pairs': 0,
            'evaluate_tide_time_sec': 0.0,
            'cache_hits': 0,
            'cache_misses': 0,
            'unique_bboxes': set()
        }
        self.enable_tile_bbox_cache = enable_tile_bbox_cache
        self.parent_bbox = parent_bbox
        self._lru_cache: Dict[Tuple[float, float, float, float, str], Any] = {}
        self._lru_capacity = 4

    def _get_model(self, bbox: tuple[float, float, float, float], constituents: list):
        import pyfes.config as cfg
        const_key = ",".join(sorted(constituents))
        target_bbox = (float(bbox[0]), max(-90.0, float(bbox[1])), float(bbox[2]), min(90.0, float(bbox[3])))
        self.stats['unique_bboxes'].add(target_bbox)

        # 优化测试模式: 复用整个 DEM 瓦片/区域的大包围框模型
        if self.enable_tile_bbox_cache and self.parent_bbox is not None:
            pb = self.parent_bbox
            parent_key = (pb[0], pb[1], pb[2], pb[3], const_key)
            if parent_key in self._lru_cache:
                self.stats['cache_hits'] += 1
                return self._lru_cache[parent_key]
            else:
                self.stats['cache_misses'] += 1
                self.stats['model_load_count'] += 1
                t0 = time.time()
                lgp_cfg = cfg.LGP(path=self.ns_grid_path, type='lgp2', codes='lgp2', constituents=constituents, bbox=pb)
                m = lgp_cfg.load()
                t_el = time.time() - t0
                self.stats['model_load_time_sec'] += t_el
                self._lru_cache[parent_key] = m
                return m

        # 默认生产逻辑: 单 entry 精确匹配
        if (self._cached_model is not None and
            self._cached_bbox == target_bbox and
            self._cached_constituents == sorted(constituents)):
            self.stats['cache_hits'] += 1
            return self._cached_model

        self.stats['cache_misses'] += 1
        self.stats['model_load_count'] += 1
        t0 = time.time()
        lgp_config = cfg.LGP(path=self.ns_grid_path, type='lgp2', codes='lgp2', constituents=constituents, bbox=target_bbox)
        model = lgp_config.load()
        self.stats['model_load_time_sec'] += time.time() - t0

        self._cached_model = model
        self._cached_bbox = target_bbox
        self._cached_constituents = sorted(constituents)
        return model


# ==============================================================================
# 3. 诊断核心类 (CoastalDatumSupportDiagnosticEngine)
# ==============================================================================
class CoastalDatumSupportDiagnosticEngine:
    """
    负责执行崇明岛及沿海场景的控制节点解耦审计、MDT空间外推测试与性能剖析。
    """
    def __init__(self, config_path: Optional[str] = None):
        self.cfg = load_app_config(config_path)
        self.transformer = DatumTransformer(config_path)

    def run_node_validity_diagnosis(
        self,
        dem_path: str,
        year: int = 2024,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        freq: str = '1h',
        constituents: str | list = 'all',
        dem_datum: str = 'egm2008',
        target_mode: str = 'intertidal',
        initial_spacing_m: Optional[float] = None,
        min_spacing_m: Optional[float] = None,
        tolerance_pct: Optional[float] = None,
        enable_tile_bbox_cache: bool = True,
        tmp_dir: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        对指定 DEM 进行完整的控制网格细分，并对每一个生成的节点拆分有效性来源。
        """
        t_diag_start = time.time()
        engine = RasterTideEngine(config_path=None)
        info = engine.inspect_raster(dem_path, compute_valid_count=True)

        # 确定时序
        t_start_str = start_time or f"{year:04d}-01-01 00:00:00"
        t_end_str = end_time or f"{year+1:04d}-01-01 00:00:00"
        _, time_idx, _ = build_time_index(t_start_str, t_end_str, freq=freq, inclusive='left')

        # 计算父包围框
        parent_bbox = None
        min_x, min_y, max_x, max_y = info.bounds
        if not info.is_projected:
            parent_bbox = (min_x - 0.5, min_y - 0.5, max_x + 0.5, max_y + 0.5)
        else:
            from pyproj import Transformer
            t_wgs = Transformer.from_crs(info.crs, "EPSG:4326", always_xy=True)
            lons, lats = t_wgs.transform([min_x, max_x], [min_y, max_y])
            parent_bbox = (min(lons) - 0.5, min(lats) - 0.5, max(lons) + 0.5, max(lats) + 0.5)

        inst_predictor = InstrumentedFESTidePredictor(
            enable_tile_bbox_cache=enable_tile_bbox_cache,
            parent_bbox=parent_bbox
        )
        engine._cached_predictor = inst_predictor

        import tempfile
        from core.tide_cache import read_tide_cache

        work_dir = tmp_dir or tempfile.gettempdir()
        tmp_cache_path = os.path.join(work_dir, f"diag_cache_{int(time.time()*1000)}.nc")

        # 运行网格构建并导出至临时 Cache 以保证获取完整的 leaf_cells 与节点拓扑
        t_grid_start = time.time()
        cache_meta = engine.build_tide_control_grid(
            dem_path=dem_path,
            export_tide_cache_path=tmp_cache_path,
            start_time=t_start_str,
            end_time=t_end_str,
            freq=freq,
            dem_datum=dem_datum,
            constituents=constituents,
            initial_control_spacing_m=initial_spacing_m,
            min_control_spacing_m=min_spacing_m,
            inundation_error_tolerance_pct=tolerance_pct,
            target_mode=target_mode,
            strict=True,
            allow_overwrite=True
        )
        t_grid_sec = time.time() - t_grid_start

        # 从 Cache 读取节点与拓扑
        cache_data = read_tide_cache(tmp_cache_path, load_raw_tide=True)
        all_nodes = cache_data['nodes']
        leaf_cells = cache_data['leaf_cells']

        # 清理临时 Cache
        try:
            if os.path.exists(tmp_cache_path):
                os.remove(tmp_cache_path)
        except Exception:
            pass

        # 提取 MDT 原始网格用于距离与双线性膨胀计算
        raw_mdt_tree, raw_mdt_coords, mdt_grid_info = self._get_raw_mdt_grid_info()

        # 针对每个节点进行深入归因审计
        records: List[NodeDiagnosticRecord] = []
        counts = {
            CLASS_FES_INVALID: 0,
            CLASS_FES_VALID_MDT_INVALID: 0,
            CLASS_FES_VALID_MDT_VALID_DELTAN_INVALID: 0,
            CLASS_FES_VALID_DATUM_VALID: 0,
            CLASS_OTHER_DATUM_FAILURE: 0
        }

        # 批量准备坐标
        n_lons = np.array([n.lon for n in all_nodes], dtype=float)
        n_lats = np.array([n.lat for n in all_nodes], dtype=float)

        # 计算 MDT 与 Delta-N 单独值
        mdt_vals = np.atleast_1d(self.transformer.get_mdt(n_lons, n_lats, strict=False)) if len(n_lons) > 0 else np.array([])
        deltan_vals = np.atleast_1d(self.transformer.get_delta_n(n_lons, n_lats, strict=False)) if len(n_lons) > 0 else np.array([])
        egm_vals = np.atleast_1d(self.transformer.get_egm2008_undulation(n_lons, n_lats, strict=False)) if len(n_lons) > 0 else np.array([])
        offsets_dict = self.transformer.get_static_datum_offsets(n_lons, n_lats, target=dem_datum, strict=False) if len(n_lons) > 0 else {'offset_m': np.array([])}
        offsets_vals = offsets_dict['offset_m']

        # 距离计算 (最近有效 MDT 原生网格中心距离)
        if len(n_lons) > 0 and raw_mdt_tree is not None:
            mid_lat = np.mean(n_lats)
            deg_lat_km = 111.0
            deg_lon_km = 111.0 * np.cos(np.radians(mid_lat))
            pts_scaled = np.column_stack([n_lons * deg_lon_km, n_lats * deg_lat_km])
            dists_km, _ = raw_mdt_tree.query(pts_scaled)
        else:
            dists_km = np.full(len(n_lons), np.nan)

        level_stats: Dict[int, Dict[str, int]] = {}
        bilinear_inflation_stats = {
            'true_mdt_nodata_count': 0, # 4角全为 NaN
            'bilinear_induced_nan_count': 0, # 1~3角为有效，但在网格双线性插值时被毒化为 NaN
            'finite_corners_distribution': {0: 0, 1: 0, 2: 0, 3: 0, 4: 0}
        }

        # 独立批量检测各节点 FES 原始有效性 (无论其基准偏移是否为 NaN)
        fes_snapshot_tides, fes_snapshot_flags = inst_predictor.predict_spatial_snapshot(
            n_lons, n_lats, t_start_str, constituents=constituents
        )
        fes_finite_mask = np.isfinite(fes_snapshot_tides)

        node_rec_map: Dict[int, NodeDiagnosticRecord] = {}

        for i, n in enumerate(all_nodes):
            lvl = getattr(n, 'level', 0)
            if lvl not in level_stats:
                level_stats[lvl] = {c: 0 for c in counts}

            # FES 真实第一性原理有效性判定
            fes_valid = bool(fes_finite_mask[i])
            fl_min = int(fes_snapshot_flags[i]) if len(fes_snapshot_flags) > i else 0
            
            mdt_f = bool(np.isfinite(mdt_vals[i]))
            dn_f = bool(np.isfinite(deltan_vals[i]))
            off_f = bool(np.isfinite(offsets_vals[i]))
            sol_valid = bool(fes_valid and off_f)

            # 双线性 NaN 膨胀分析
            if mdt_grid_info is not None:
                n_finite_c = self._count_finite_mdt_corners(n.lon, n.lat, mdt_grid_info)
                bilinear_inflation_stats['finite_corners_distribution'][n_finite_c] += 1
                if not mdt_f:
                    if n_finite_c == 0:
                        bilinear_inflation_stats['true_mdt_nodata_count'] += 1
                    elif 1 <= n_finite_c <= 3:
                        bilinear_inflation_stats['bilinear_induced_nan_count'] += 1

            # 分类逻辑
            if not fes_valid:
                f_class = CLASS_FES_INVALID
            elif not mdt_f:
                f_class = CLASS_FES_VALID_MDT_INVALID
            elif not dn_f:
                f_class = CLASS_FES_VALID_MDT_VALID_DELTAN_INVALID
            elif off_f:
                f_class = CLASS_FES_VALID_DATUM_VALID
            else:
                f_class = CLASS_OTHER_DATUM_FAILURE

            counts[f_class] += 1
            level_stats[lvl][f_class] += 1

            rec = NodeDiagnosticRecord(
                node_id=n.node_id,
                lon=float(n.lon),
                lat=float(n.lat),
                x=float(n.x),
                y=float(n.y),
                component_id=int(n.component_id),
                level=int(lvl),
                fes_any_finite=fes_valid,
                fes_valid_fraction=1.0 if fes_valid else 0.0,
                fes_flag_min=int(fl_min),
                mdt_finite=mdt_f,
                mdt_value=float(mdt_vals[i]) if mdt_f else float('nan'),
                delta_n_finite=dn_f,
                delta_n_value=float(deltan_vals[i]) if dn_f else float('nan'),
                egm_undulation_finite=bool(np.isfinite(egm_vals[i])),
                egm_value=float(egm_vals[i]) if np.isfinite(egm_vals[i]) else float('nan'),
                datum_offset_finite=off_f,
                datum_offset_m=float(offsets_vals[i]) if off_f else float('nan'),
                final_solution_valid=sol_valid,
                failure_class=f_class,
                dist_to_nearest_valid_mdt_km=float(dists_km[i])
            )
            records.append(rec)
            node_rec_map[n.node_id] = rec

        total_nodes = len(records)
        pcts = {k: (v / total_nodes * 100.0 if total_nodes > 0 else 0.0) for k, v in counts.items()}

        # 5. 像元级 QC=68 归因审计 (Pixel QC Failure Provenance Audit)
        qc68_audit = self._audit_qc68_provenance(info, leaf_cells, node_rec_map)

        return {
            'total_nodes': total_nodes,
            'counts': counts,
            'percentages': pcts,
            'level_breakdown': level_stats,
            'records': records,
            'leaf_cells_count': len(leaf_cells),
            'bilinear_inflation_stats': bilinear_inflation_stats,
            'qc68_audit': qc68_audit,
            'grid_build_time_sec': t_grid_sec,
            'predictor_stats': inst_predictor.stats,
            'diagnostic_time_sec': time.time() - t_diag_start
        }

    def _get_raw_mdt_grid_info(self) -> Tuple[Optional[cKDTree], Optional[np.ndarray], Optional[Dict[str, Any]]]:
        """提取原生 MDT NetCDF 中数据，构建 KDTree 并返回网格索引参数"""
        if not os.path.exists(self.transformer.mdt_path):
            return None, None, None
        try:
            ds = xr.open_dataset(self.transformer.mdt_path)
            # 全球/区域提取
            sub = ds.sel(latitude=slice(29.0, 34.0), longitude=slice(119.0, 124.0))
            mdt_sub = sub.mdt.values[0]
            lats_sub = sub.latitude.values
            lons_sub = sub.longitude.values
            ds.close()

            valid_r, valid_c = np.where(np.isfinite(mdt_sub))
            if len(valid_r) == 0:
                return None, None, None

            valid_lats = lats_sub[valid_r]
            valid_lons = lons_sub[valid_c]

            mid_lat = np.mean(valid_lats)
            deg_lat_km = 111.0
            deg_lon_km = 111.0 * np.cos(np.radians(mid_lat))
            pts_scaled = np.column_stack([valid_lons * deg_lon_km, valid_lats * deg_lat_km])
            tree = cKDTree(pts_scaled)
            coords = np.column_stack([valid_lons, valid_lats])

            grid_info = {
                'lats': lats_sub,
                'lons': lons_sub,
                'mdt': mdt_sub,
                'step_lat': float(lats_sub[1] - lats_sub[0]),
                'step_lon': float(lons_sub[1] - lons_sub[0])
            }
            return tree, coords, grid_info
        except Exception:
            return None, None, None

    def _count_finite_mdt_corners(self, lon: float, lat: float, grid_info: Dict[str, Any]) -> int:
        """计算包含该经纬度的 0.125° MDT 网格单元 4 个角点中有限数值的个数"""
        lats = grid_info['lats']
        lons = grid_info['lons']
        mdt = grid_info['mdt']

        if lat < lats[0] or lat > lats[-1] or lon < lons[0] or lon > lons[-1]:
            return 0

        r0 = int(np.floor((lat - lats[0]) / grid_info['step_lat']))
        c0 = int(np.floor((lon - lons[0]) / grid_info['step_lon']))

        r0 = np.clip(r0, 0, len(lats) - 2)
        c0 = np.clip(c0, 0, len(lons) - 2)

        corners = [
            mdt[r0, c0],
            mdt[r0, c0 + 1],
            mdt[r0 + 1, c0],
            mdt[r0 + 1, c0 + 1]
        ]
        return int(sum(bool(np.isfinite(v)) for v in corners))

    def _audit_qc68_provenance(
        self,
        info: RasterInfo,
        leaf_cells: List[QuadCell],
        node_rec_map: Dict[int, NodeDiagnosticRecord]
    ) -> Dict[str, Any]:
        """
        审计所有未解算 (QC=68) 像元归属的叶单元，统计其 4 角点的失败类别。
        """
        cell_categories = {
            'all_fes_invalid': 0,
            'all_mdt_invalid': 0,
            'mixed_fes_and_mdt_invalid': 0,
            'topology_mismatch_only': 0,
            'other': 0
        }

        # 分类每个叶单元
        for cell in leaf_cells:
            c_nodes = [cell.node_a, cell.node_b, cell.node_c, cell.node_d]
            recs = [node_rec_map.get(n.node_id) for n in c_nodes]
            if any(r is None for r in recs):
                cell_categories['other'] += 1
                continue

            f_classes = [r.failure_class for r in recs]
            fes_invs = sum(1 for c in f_classes if c == CLASS_FES_INVALID)
            mdt_invs = sum(1 for c in f_classes if c == CLASS_FES_VALID_MDT_INVALID)
            valid_anchors = sum(1 for c in f_classes if c == CLASS_FES_VALID_DATUM_VALID)

            if fes_invs == 4:
                cell_categories['all_fes_invalid'] += 1
            elif mdt_invs == 4:
                cell_categories['all_mdt_invalid'] += 1
            elif (fes_invs > 0 or mdt_invs > 0) and valid_anchors == 0:
                cell_categories['mixed_fes_and_mdt_invalid'] += 1
            elif valid_anchors > 0:
                cell_categories['topology_mismatch_only'] += 1
            else:
                cell_categories['other'] += 1

        total_cells = len(leaf_cells)
        return {
            'total_leaf_cells': total_cells,
            'cell_failure_distribution': cell_categories,
            'cell_failure_percentages': {
                k: (v / total_cells * 100.0 if total_cells > 0 else 0.0)
                for k, v in cell_categories.items()
            }
        }

    # ==========================================================================
    # 4. 留一法交叉验证 (Leave-One-Out Cross-Validation, LOOCV)
    # ==========================================================================
    def run_anchor_loocv(
        self,
        records: List[NodeDiagnosticRecord],
        target_field: str = 'offset', # 'mdt' 或 'offset'
        max_dist_km: float = 8.0,
        sample_limit: int = 500
    ) -> Dict[str, Any]:
        """
        在所有有效基准锚点 (FES valid & MDT valid & DeltaN valid) 上执行留一法交叉验证。
        验证基于近邻锚点插值估计缺失基准偏移的可行性与科学误差。
        """
        anchors = [r for r in records if r.failure_class == CLASS_FES_VALID_DATUM_VALID]
        if len(anchors) < 4:
            return {'status': 'insufficient_anchors', 'sample_count': len(anchors)}

        # 若锚点很多，空间均匀抽样 sample_limit 个进行 LOOCV
        rng = np.random.default_rng(42)
        if len(anchors) > sample_limit:
            idx_sampled = rng.choice(len(anchors), size=sample_limit, replace=False)
            eval_anchors = [anchors[i] for i in idx_sampled]
        else:
            eval_anchors = anchors

        mid_lat = np.mean([r.lat for r in anchors])
        deg_lat_km = 111.0
        deg_lon_km = 111.0 * np.cos(np.radians(mid_lat))

        all_coords_km = np.array([[r.lon * deg_lon_km, r.lat * deg_lat_km] for r in anchors])
        if target_field == 'mdt':
            all_vals = np.array([r.mdt_value for r in anchors])
        else:
            all_vals = np.array([r.datum_offset_m for r in anchors])

        tree = cKDTree(all_coords_km)

        errors = []
        distances = []
        dist_bins = {
            '<0.5km': [],
            '0.5-1km': [],
            '1-2km': [],
            '2-4km': [],
            '>4km': []
        }

        for r in eval_anchors:
            target_pt = np.array([r.lon * deg_lon_km, r.lat * deg_lat_km])
            true_val = r.mdt_value if target_field == 'mdt' else r.datum_offset_m

            # 查找最近的 k=4 个邻居 (第 0 个必定是自身，距离为 0)
            dists, idxs = tree.query(target_pt, k=5)
            # 剔除自身
            neighbor_dists = dists[1:]
            neighbor_idxs = idxs[1:]

            # 筛选在 max_dist_km 内的邻居
            valid_mask = neighbor_dists <= max_dist_km
            if not np.any(valid_mask):
                continue

            v_dists = neighbor_dists[valid_mask]
            v_vals = all_vals[neighbor_idxs[valid_mask]]

            # IDW 逆距离加权 (p=2)
            weights = 1.0 / np.maximum(v_dists, 1e-4)**2
            weights /= np.sum(weights)
            pred_val = np.sum(weights * v_vals)

            err = pred_val - true_val
            min_d = v_dists[0]
            errors.append(err)
            distances.append(min_d)

            if min_d < 0.5:
                dist_bins['<0.5km'].append(err)
            elif min_d < 1.0:
                dist_bins['0.5-1km'].append(err)
            elif min_d < 2.0:
                dist_bins['1-2km'].append(err)
            elif min_d < 4.0:
                dist_bins['2-4km'].append(err)
            else:
                dist_bins['>4km'].append(err)

        if not errors:
            return {'status': 'no_neighbors_within_distance'}

        arr_err = np.array(errors)
        abs_err = np.abs(arr_err)

        def _calc_stats(e_list):
            if not e_list:
                return {'count': 0, 'bias': np.nan, 'mae': np.nan, 'rmse': np.nan, 'p90': np.nan, 'p95': np.nan, 'max': np.nan}
            arr = np.array(e_list)
            abs_a = np.abs(arr)
            return {
                'count': len(arr),
                'bias': float(np.mean(arr)),
                'mae': float(np.mean(abs_a)),
                'rmse': float(np.sqrt(np.mean(arr**2))),
                'p90': float(np.percentile(abs_a, 90)),
                'p95': float(np.percentile(abs_a, 95)),
                'max': float(np.max(abs_a))
            }

        bin_stats = {k: _calc_stats(v) for k, v in dist_bins.items()}
        overall = _calc_stats(errors)
        overall['target_field'] = target_field
        overall['bin_breakdown'] = bin_stats
        overall['min_dist_p50'] = float(np.percentile(distances, 50))
        overall['min_dist_p95'] = float(np.percentile(distances, 95))
        return overall

    # ==========================================================================
    # 5. 沿海基准支持扩展策略评估 (Coastal Datum Support Extension Benchmark)
    # ==========================================================================
    def evaluate_extension_strategies(
        self,
        records: List[NodeDiagnosticRecord],
        max_distances_km: List[float] = [0.5, 1.0, 2.0, 4.0, 8.0]
    ) -> Dict[str, Any]:
        """
        评估多种基准扩展策略下，能修复多少 FES_VALID_MDT_INVALID 节点。
        """
        anchors = [r for r in records if r.failure_class == CLASS_FES_VALID_DATUM_VALID]
        targets = [r for r in records if r.failure_class == CLASS_FES_VALID_MDT_INVALID]

        total_nodes = len(records)
        target_count = len(targets)
        anchor_count = len(anchors)

        if anchor_count == 0 or target_count == 0:
            return {
                'total_nodes': total_nodes,
                'anchor_count': anchor_count,
                'target_count': target_count,
                'results': {}
            }

        mid_lat = np.mean([r.lat for r in records])
        deg_lat_km = 111.0
        deg_lon_km = 111.0 * np.cos(np.radians(mid_lat))

        anchor_coords = np.array([[r.lon * deg_lon_km, r.lat * deg_lat_km] for r in anchors])
        target_coords = np.array([[r.lon * deg_lon_km, r.lat * deg_lat_km] for r in targets])
        anchor_comps = np.array([r.component_id for r in anchors])
        target_comps = np.array([r.component_id for r in targets])

        tree_global = cKDTree(anchor_coords)

        # 策略比较
        results = {}
        for max_d in max_distances_km:
            # 策略 1: 无拓扑约束最近邻 (Unconstrained Nearest Anchor)
            dists_1, idxs_1 = tree_global.query(target_coords, k=1)
            solved_1 = dists_1 <= max_d
            count_1 = int(np.count_nonzero(solved_1))

            # 策略 2: 同连通域约束最近邻 (Component-Constrained Nearest Anchor)
            # 仅当 component_id > 0 且与 anchor 一致，或 component==0
            comp_match_mask = (anchor_comps[idxs_1] == target_comps) | (target_comps == 0) | (anchor_comps[idxs_1] == 0)
            solved_2 = solved_1 & comp_match_mask
            count_2 = int(np.count_nonzero(solved_2))

            # 策略 3: IDW k=4
            dists_k, _ = tree_global.query(target_coords, k=min(4, len(anchors)))
            # 至少有 1 个邻居在 max_d 内
            solved_3 = dists_k[:, 0] <= max_d
            count_3 = int(np.count_nonzero(solved_3))

            results[f"{max_d}km"] = {
                'strategy1_unconstrained_nearest': {
                    'recovered_nodes': count_1,
                    'recovered_pct': float(count_1 / target_count * 100.0),
                    'effective_nodes_pct': float((anchor_count + count_1) / total_nodes * 100.0)
                },
                'strategy2_component_constrained': {
                    'recovered_nodes': count_2,
                    'recovered_pct': float(count_2 / target_count * 100.0),
                    'effective_nodes_pct': float((anchor_count + count_2) / total_nodes * 100.0)
                },
                'strategy3_idw_k4': {
                    'recovered_nodes': count_3,
                    'recovered_pct': float(count_3 / target_count * 100.0),
                    'effective_nodes_pct': float((anchor_count + count_3) / total_nodes * 100.0)
                }
            }

        return {
            'total_nodes': total_nodes,
            'anchor_count': anchor_count,
            'target_count': target_count,
            'results': results
        }


# ==============================================================================
# 6. 独立 CLI 入口
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(description="CoastTideX 崇明岛基准支持诊断与扩展性剖析工具")
    parser.add_argument("--dem", required=True, help="待诊断 DEM 路径")
    parser.add_argument("--out-dir", default="outputs/chongming_diagnosis", help="诊断产物输出目录")
    parser.add_argument("--year", type=int, default=2024, help="模拟年份")
    parser.add_argument("--freq", default="1h", help="时间步长")
    parser.add_argument("--constituents", default="all", help="分潮集合 ('all' 或 'major8')")
    parser.add_argument("--target-mode", default="intertidal", choices=["intertidal", "standard"], help="目标模式")
    parser.add_argument("--disable-cache-opt", action="store_true", help="禁用局部 BBox 缓存优化 (执行生产原始单entry对比)")

    args = parser.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    diag = CoastalDatumSupportDiagnosticEngine()
    print(f"[*] 启动崇明岛基准支持诊断: DEM = {args.dem}")
    print(f"[*] 参数: year={args.year}, freq={args.freq}, constituents={args.constituents}, mode={args.target_mode}")

    res = diag.run_node_validity_diagnosis(
        dem_path=args.dem,
        year=args.year,
        freq=args.freq,
        constituents=args.constituents,
        target_mode=args.target_mode,
        enable_tile_bbox_cache=(not args.disable_cache_opt)
    )

    print(f"[OK] 诊断完成! 总控制节点数: {res['total_nodes']}")
    for k, v in res['counts'].items():
        print(f"     - {k}: {v} ({res['percentages'][k]:.2f}%)")

    # 保存 CSV 与 GeoJSON
    csv_path = os.path.join(args.out_dir, "control_nodes_diagnosis.csv")
    df = pd.DataFrame([asdict(r) for r in res['records']])
    df.to_csv(csv_path, index=False)
    print(f"[+] 节点明细已保存至: {csv_path}")

    geojson_path = os.path.join(args.out_dir, "control_nodes_diagnosis.geojson")
    features = []
    for r in res['records']:
        feat = {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [r.lon, r.lat]
            },
            "properties": {
                "node_id": r.node_id,
                "failure_class": r.failure_class,
                "level": r.level,
                "component_id": r.component_id,
                "fes_finite": r.fes_any_finite,
                "mdt_finite": r.mdt_finite,
                "mdt_val": r.mdt_value,
                "offset_m": r.datum_offset_m,
                "dist_mdt_km": r.dist_to_nearest_valid_mdt_km
            }
        }
        features.append(feat)
    geojson_obj = {
        "type": "FeatureCollection",
        "features": features
    }
    with open(geojson_path, 'w', encoding='utf-8') as f:
        json.dump(geojson_obj, f, indent=2)
    print(f"[+] 空间矢量已保存至: {geojson_path}")

    # 执行 LOOCV
    loocv_offset = diag.run_anchor_loocv(res['records'], target_field='offset')
    loocv_mdt = diag.run_anchor_loocv(res['records'], target_field='mdt')

    # 执行扩展策略评估
    ext_eval = diag.evaluate_extension_strategies(res['records'])

    summary_out = {
        'dem_path': args.dem,
        'summary': {
            'total_nodes': res['total_nodes'],
            'counts': res['counts'],
            'percentages': res['percentages'],
            'level_breakdown': res['level_breakdown'],
            'diagnostic_time_sec': res['diagnostic_time_sec'],
            'predictor_stats': {
                'model_load_count': res['predictor_stats']['model_load_count'],
                'model_load_time_sec': res['predictor_stats']['model_load_time_sec'],
                'cache_hits': res['predictor_stats']['cache_hits'],
                'cache_misses': res['predictor_stats']['cache_misses']
            }
        },
        'bilinear_inflation_stats': res['bilinear_inflation_stats'],
        'qc68_audit': res['qc68_audit'],
        'loocv_offset': loocv_offset,
        'loocv_mdt': loocv_mdt,
        'extension_strategies': ext_eval
    }

    json_path = os.path.join(args.out_dir, "diagnosis_summary.json")
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(summary_out, f, indent=2, ensure_ascii=False)
    print(f"[+] 诊断总结 JSON 已保存至: {json_path}")


if __name__ == "__main__":
    main()
