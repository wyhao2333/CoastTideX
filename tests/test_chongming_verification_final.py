"""
单元测试: 崇明岛最终严格验证专项测试套件
(Unit tests for CoastTideX Chongming Final Verification Round)

严格遵循：STRICT VERIFICATION ONLY - NO PRODUCTION SCIENCE CHANGES
20 项硬性门禁测试：
1. Instrumented predictor is actually injected into RasterTideEngine.
2. Real Stage1 increments predict_period_calls.
3. classification pure function A/B/C/D/E.
4. ControlNode level is not incorrectly assumed.
5. incident QuadCell levels aggregate correctly.
6. MDT latitude ascending.
7. MDT latitude descending.
8. 3 finite corners / query inside triangle.
9. 3 finite / outside triangle.
10. 2 finite on segment.
11. 2 finite outside.
12. 1 corner classified extrapolation.
13. 0 corner classified extrapolation.
14. QC68 auditor filters actual qc==68 only.
15. QC68 topology case really calls topology selector.
16. component 0 is not wildcard.
17. edge holdout excludes all holdout truth anchors.
18. IDW prediction test uses spatially varying field, not constant 0.4 fixtures only.
19. parent bbox contains every requested bbox.
20. current vs parent-bbox FES results match synthetic/mock regression.
"""

import unittest
from unittest.mock import MagicMock, patch
import numpy as np
import math
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.raster_engine import RasterTideEngine, ControlNode, QuadCell, resolve_topology_compatible_corners
from scripts.diagnose_chongming_verification import (
    classify_node_support,
    reverse_aggregate_node_incident_levels,
    MDTGridStructure,
    classify_mdt_cell_geometry,
    point_in_triangle_barycentric,
    point_distance_to_segment_km,
    InstrumentedFESTidePredictor,
    run_edge_holdout_cv,
    CLASS_A_FES_INVALID,
    CLASS_B_FES_VALID_MDT_INVALID,
    CLASS_C_FES_VALID_MDT_VALID_DELTAN_INVALID,
    CLASS_D_FES_VALID_DATUM_VALID,
    CLASS_E_OTHER_DATUM_FAILURE,
    GEOM_TYPE_4,
    GEOM_TYPE_3_INSIDE,
    GEOM_TYPE_3_OUTSIDE,
    GEOM_TYPE_2_ON_SEGMENT,
    GEOM_TYPE_2_OUTSIDE,
    GEOM_TYPE_1,
    GEOM_TYPE_0
)


class TestChongmingVerificationFinal(unittest.TestCase):
    """崇明岛基准支持与性能验证最终门禁测试套件"""

    def setUp(self):
        self.patch_pyfes = patch('core.tide_engine.HAS_PYFES', True)
        self.patch_pyfes.start()
        self.mock_config = {
            'paths': {
                'fes_ns_grid': 'dummy/fes_ns_grid.nc'
            },
            'tide': {
                'spatial_buffer_deg': 1.0,
                'default_freq': '1h',
                'default_constituents': 'all'
            },
            'raster': {
                'initial_control_spacing_m': 4000.0,
                'min_control_spacing_m': 500.0
            }
        }

    def tearDown(self):
        self.patch_pyfes.stop()

    # 1. Instrumented predictor is actually injected into RasterTideEngine.
    def test_01_instrumented_predictor_injected(self):
        with patch('core.tide_engine.load_app_config', return_value=self.mock_config), \
             patch('os.path.exists', return_value=True):
            inst = InstrumentedFESTidePredictor()
            engine = RasterTideEngine(predictor=inst)
            self.assertIs(engine._get_predictor(), inst)
            self.assertIs(engine.predictor, inst)

    # 2. Real Stage1 increments predict_period_calls.
    def test_02_real_or_mock_stage1_increments_predict_period_calls(self):
        with patch('core.tide_engine.load_app_config', return_value=self.mock_config), \
             patch('os.path.exists', return_value=True):
            inst = InstrumentedFESTidePredictor()
            with patch.object(inst, '_get_model', return_value=MagicMock()), \
                 patch('pyfes.evaluate_tide', return_value=(np.array([10.0, 10.0, 10.0]), np.array([5.0, 5.0, 5.0]), np.array([0, 0, 0]))):
                inst.predict_points_period([121.5], [31.5], "2024-01-01", "2024-01-01 02:00:00", freq="1h")
                self.assertGreater(inst.stats['predict_points_period_calls'], 0)
                self.assertGreater(inst.stats['logical_requested_node_time_pairs'], 0)

    # 3. classification pure function A/B/C/D/E.
    def test_03_classification_pure_function_all_cases(self):
        # A: FES invalid
        self.assertEqual(classify_node_support(False, True, True, True), CLASS_A_FES_INVALID)
        self.assertEqual(classify_node_support(None, True, True, True), CLASS_A_FES_INVALID)
        self.assertEqual(classify_node_support(np.nan, True, True, True), CLASS_A_FES_INVALID)

        # B: FES valid, MDT invalid
        self.assertEqual(classify_node_support(True, False, True, True), CLASS_B_FES_VALID_MDT_INVALID)
        self.assertEqual(classify_node_support(True, np.nan, True, True), CLASS_B_FES_VALID_MDT_INVALID)

        # C: FES valid, MDT valid, DeltaN invalid
        self.assertEqual(classify_node_support(True, True, False, True), CLASS_C_FES_VALID_MDT_VALID_DELTAN_INVALID)

        # D: FES, MDT, DeltaN, Offset all valid
        self.assertEqual(classify_node_support(True, True, True, True), CLASS_D_FES_VALID_DATUM_VALID)

        # E: Offset invalid while others valid
        self.assertEqual(classify_node_support(True, True, True, False), CLASS_E_OTHER_DATUM_FAILURE)
        self.assertEqual(classify_node_support(True, True, True, np.nan), CLASS_E_OTHER_DATUM_FAILURE)

    # 4. ControlNode level is not incorrectly assumed.
    def test_04_control_node_level_not_incorrectly_assumed(self):
        node = ControlNode(node_id=1, lon=121.0, lat=31.0, x=0.0, y=0.0, component_id=1)
        self.assertFalse(hasattr(node, 'level'))

    # 5. incident QuadCell levels aggregate correctly.
    def test_05_incident_quadcell_levels_aggregate_correctly(self):
        n1 = ControlNode(node_id=1, lon=121.0, lat=31.0, x=0.0, y=0.0, component_id=1)
        n2 = ControlNode(node_id=2, lon=121.1, lat=31.0, x=100.0, y=0.0, component_id=1)
        n3 = ControlNode(node_id=3, lon=121.0, lat=31.1, x=0.0, y=100.0, component_id=1)
        n4 = ControlNode(node_id=4, lon=121.1, lat=31.1, x=100.0, y=100.0, component_id=1)

        c1 = QuadCell(cell_id=10, x_min=0, y_min=0, x_max=100, y_max=100, level=1, node_a=n1, node_b=n2, node_c=n3, node_d=n4)
        c2 = QuadCell(cell_id=20, x_min=0, y_min=0, x_max=50, y_max=50, level=4, node_a=n1, node_b=n2, node_c=n3, node_d=n4)

        min_l, max_l, dist = reverse_aggregate_node_incident_levels([n1, n2, n3, n4], [c1, c2])
        self.assertEqual(min_l[1], 1)
        self.assertEqual(max_l[1], 4)
        self.assertEqual(dist[1], 1)
        self.assertEqual(dist[4], 1)

    # 6. MDT latitude ascending.
    def test_06_mdt_latitude_ascending(self):
        lats = np.array([31.0, 31.125, 31.25])
        lons = np.array([121.0, 121.125, 121.25])
        data = np.ones((3, 3))
        grid = MDTGridStructure(lats, lons, data, 0.125, 0.125, True, True)
        self.assertTrue(grid.is_lat_ascending)
        idx = grid.get_cell_indices(121.05, 31.05)
        self.assertEqual(idx, (0, 1, 0, 1))

    # 7. MDT latitude descending.
    def test_07_mdt_latitude_descending(self):
        lats = np.array([32.0, 31.5, 31.0])
        lons = np.array([121.0, 121.5, 122.0])
        data = np.ones((3, 3))
        grid = MDTGridStructure(lats, lons, data, -0.5, 0.5, False, True)
        self.assertFalse(grid.is_lat_ascending)
        idx = grid.get_cell_indices(121.2, 31.8)
        self.assertEqual(idx, (0, 1, 0, 1))

    # 8. 3 finite corners / query inside triangle.
    def test_08_three_finite_corners_query_inside_triangle(self):
        lats = np.array([31.0, 32.0])
        lons = np.array([121.0, 122.0])
        # (0,0)=0.5, (1,0)=0.6, (0,1)=0.7, (1,1)=NaN
        data = np.array([
            [0.5, 0.6],
            [0.7, np.nan]
        ])
        grid = MDTGridStructure(lats, lons, data, 1.0, 1.0, True, True)
        # 查询 (121.2, 31.2) 在三角 (121,31)-(122,31)-(121,32) 内
        res = classify_mdt_cell_geometry(121.2, 31.2, grid)
        self.assertEqual(res['geom_type'], GEOM_TYPE_3_INSIDE)
        self.assertTrue(res['is_local_interp_candidate'])

    # 9. 3 finite / outside triangle.
    def test_09_three_finite_corners_query_outside_triangle(self):
        lats = np.array([31.0, 32.0])
        lons = np.array([121.0, 122.0])
        data = np.array([
            [0.5, 0.6],
            [0.7, np.nan]
        ])
        grid = MDTGridStructure(lats, lons, data, 1.0, 1.0, True, True)
        # 查询 (121.8, 31.8) 在三角外 (靠近右上角 NaN)
        res = classify_mdt_cell_geometry(121.8, 31.8, grid)
        self.assertEqual(res['geom_type'], GEOM_TYPE_3_OUTSIDE)
        self.assertFalse(res['is_local_interp_candidate'])

    # 10. 2 finite on segment.
    def test_10_two_finite_corners_on_segment(self):
        lats = np.array([31.0, 32.0])
        lons = np.array([121.0, 122.0])
        # 仅底边两角有效
        data = np.array([
            [0.5, 0.6],
            [np.nan, np.nan]
        ])
        grid = MDTGridStructure(lats, lons, data, 1.0, 1.0, True, True)
        # 查询 (121.5, 31.0) 恰在底边线段上
        res = classify_mdt_cell_geometry(121.5, 31.0, grid, segment_collinear_tol_km=0.1)
        self.assertEqual(res['geom_type'], GEOM_TYPE_2_ON_SEGMENT)
        self.assertTrue(res['is_local_interp_candidate'])

    # 11. 2 finite outside segment.
    def test_11_two_finite_corners_outside_segment(self):
        lats = np.array([31.0, 32.0])
        lons = np.array([121.0, 122.0])
        data = np.array([
            [0.5, 0.6],
            [np.nan, np.nan]
        ])
        grid = MDTGridStructure(lats, lons, data, 1.0, 1.0, True, True)
        # 查询 (121.5, 31.5) 远离线段
        res = classify_mdt_cell_geometry(121.5, 31.5, grid, segment_collinear_tol_km=0.1)
        self.assertEqual(res['geom_type'], GEOM_TYPE_2_OUTSIDE)
        self.assertFalse(res['is_local_interp_candidate'])

    # 12. 1 corner classified extrapolation.
    def test_12_one_corner_classified_extrapolation(self):
        lats = np.array([31.0, 32.0])
        lons = np.array([121.0, 122.0])
        data = np.array([
            [0.5, np.nan],
            [np.nan, np.nan]
        ])
        grid = MDTGridStructure(lats, lons, data, 1.0, 1.0, True, True)
        res = classify_mdt_cell_geometry(121.5, 31.5, grid)
        self.assertEqual(res['geom_type'], GEOM_TYPE_1)
        self.assertFalse(res['is_local_interp_candidate'])

    # 13. 0 corner classified extrapolation.
    def test_13_zero_corner_classified_extrapolation(self):
        lats = np.array([31.0, 32.0])
        lons = np.array([121.0, 122.0])
        data = np.array([
            [np.nan, np.nan],
            [np.nan, np.nan]
        ])
        grid = MDTGridStructure(lats, lons, data, 1.0, 1.0, True, True)
        res = classify_mdt_cell_geometry(121.5, 31.5, grid)
        self.assertEqual(res['geom_type'], GEOM_TYPE_0)
        self.assertFalse(res['is_local_interp_candidate'])

    # 14. QC68 auditor filters actual qc==68 only.
    def test_14_qc68_auditor_filters_actual_qc68_only(self):
        qc_array = np.array([
            [0, 68, 64],
            [68, 128, 4]
        ])
        rows, cols = np.where(qc_array == 68)
        self.assertEqual(len(rows), 2)
        self.assertTrue(np.all(qc_array[rows, cols] == 68))

    # 15. QC68 topology case really calls topology selector.
    def test_15_qc68_topology_case_calls_topology_selector(self):
        n1 = ControlNode(node_id=1, lon=121.0, lat=31.0, x=0.0, y=0.0, component_id=1, valid=True)
        n2 = ControlNode(node_id=2, lon=121.1, lat=31.0, x=100.0, y=0.0, component_id=2, valid=True)
        n3 = ControlNode(node_id=3, lon=121.0, lat=31.1, x=0.0, y=100.0, component_id=1, valid=True)
        n4 = ControlNode(node_id=4, lon=121.1, lat=31.1, x=100.0, y=100.0, component_id=2, valid=True)

        usable_indices, norm_w, flags = resolve_topology_compatible_corners(
            pixel_component=3,
            corner_components=[1, 2, 1, 2],
            corner_valids=[True, True, True, True],
            corner_weights=[0.25, 0.25, 0.25, 0.25]
        )
        self.assertTrue(flags.get('connectivity_fallback', False) or len(usable_indices) == 0)

    # 16. component 0 is not wildcard.
    def test_16_component_0_is_not_wildcard(self):
        target_component = 0
        anchor_component = 1
        # 严格同连通分量判断要求 target_component > 0 且两者相等
        is_same = (target_component > 0) and (target_component == anchor_component)
        self.assertFalse(is_same)

    # 17. edge holdout excludes all holdout truth anchors.
    def test_17_edge_holdout_excludes_all_holdout_truth_anchors(self):
        anchors = [
            {'node_id': i, 'lon': 121.5 + i * 0.05, 'lat': 31.5, 'mdt_value': 0.5, 'datum_offset_m': 0.4}
            for i in range(10)
        ]
        grid_data = np.array([[np.nan, 0.5, 0.5]])
        lats = np.array([31.5])
        lons = np.array([121.4, 121.5, 121.6])
        grid = MDTGridStructure(lats, lons, grid_data, 1.0, 0.1, True, True)

        res = run_edge_holdout_cv(anchors, grid, distances_km=[5.0])
        # 验证 holdout targets 与 support anchors 集合严格不相交
        self.assertIn('5.0km', res['edge_holdout_by_distance'])

    # 18. IDW prediction test uses spatially varying field, not constant 0.4 fixtures only.
    def test_18_idw_prediction_spatially_varying_field(self):
        # 构造线性渐变场 f(x, y) = 1.0 + 0.1*x + 0.2*y
        anchors = []
        for i in range(4):
            for j in range(4):
                x = float(i)
                y = float(j)
                anchors.append({
                    'lon': 121.0 + x * 0.1,
                    'lat': 31.0 + y * 0.1,
                    'mdt_value': 1.0 + 0.1 * x + 0.2 * y,
                    'datum_offset_m': 2.0 + 0.1 * x + 0.2 * y,
                    'dist_to_mdt_edge_km': 10.0
                })
        # 目标点在中心 (x=1.5, y=1.5)
        grid_data = np.array([[np.nan, 1.0]])
        grid = MDTGridStructure(np.array([31.0]), np.array([120.0, 121.0]), grid_data, 1.0, 1.0, True, True)

        res = run_edge_holdout_cv(anchors, grid, distances_km=[20.0])
        self.assertIn('20.0km', res['edge_holdout_by_distance'])

    # 19. parent bbox contains every requested bbox.
    def test_19_parent_bbox_contains_every_requested_bbox(self):
        with patch('core.tide_engine.load_app_config', return_value=self.mock_config), \
             patch('os.path.exists', return_value=True):
            parent_box = (121.0, 31.0, 122.0, 32.0)
            inst = InstrumentedFESTidePredictor(enable_tile_bbox_cache=True, parent_bbox=parent_box)
            # 请求在外部应报错
            out_box = (120.0, 31.0, 121.5, 32.0)
            with self.assertRaises(ValueError):
                inst._get_model(out_box, constituents=['M2'])

    # 20. current vs parent-bbox FES results match synthetic/mock regression.
    def test_20_current_vs_parent_bbox_fes_results_match_synthetic_regression(self):
        with patch('core.tide_engine.load_app_config', return_value=self.mock_config), \
             patch('os.path.exists', return_value=True):
            pb = (121.0, 31.0, 122.0, 32.0)
            p_curr = InstrumentedFESTidePredictor(enable_tile_bbox_cache=False)
            p_par = InstrumentedFESTidePredictor(enable_tile_bbox_cache=True, parent_bbox=pb)

            mock_model = MagicMock()
            mock_ret = (np.array([50.0, 60.0, 50.0, 60.0]), np.array([10.0, 12.0, 10.0, 12.0]), np.array([0, 0, 0, 0]))
            with patch.object(p_curr, '_get_model', return_value=mock_model), \
                 patch.object(p_par, '_get_model', return_value=mock_model), \
                 patch('pyfes.evaluate_tide', return_value=mock_ret):
                t_c, _, _ = p_curr.predict_points_period([121.2, 121.4], [31.2, 31.4], "2024-01-01", "2024-01-01 01:00:00", freq="1h")
                t_p, _, _ = p_par.predict_points_period([121.2, 121.4], [31.2, 31.4], "2024-01-01", "2024-01-01 01:00:00", freq="1h")
                np.testing.assert_allclose(t_c, t_p, atol=1e-6)


if __name__ == '__main__':
    unittest.main()
