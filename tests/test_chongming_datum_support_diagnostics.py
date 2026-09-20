"""
单元测试: 崇明岛沿海垂直基准支持诊断与分析算法测试套件
(Unit tests for Chongming Estuary Datum-Support Diagnostics)

测试重点:
1. 节点失败类别归因 (5类划分逻辑)；
2. MDT 双线性 NaN 膨胀度量逻辑 (True NoData vs Bilinear-induced NaN)；
3. 留一法交叉验证 (LOOCV) 计算与距离分箱统计；
4. 沿海基准支持扩展策略评估 (多距离阈值与连通分量约束)。
本测试套件使用纯合成数据 (Synthetic Fixtures)，不依赖 FES/MDT 外部大文件，确保在无网/CI环境快速通过。
"""

import unittest
import numpy as np
from scripts.diagnose_coastal_datum_support import (
    CLASS_FES_INVALID,
    CLASS_FES_VALID_MDT_INVALID,
    CLASS_FES_VALID_MDT_VALID_DELTAN_INVALID,
    CLASS_FES_VALID_DATUM_VALID,
    CLASS_OTHER_DATUM_FAILURE,
    NodeDiagnosticRecord,
    CoastalDatumSupportDiagnosticEngine
)


class TestChongmingDatumSupportDiagnostics(unittest.TestCase):
    """测试崇明岛基准支持诊断相关算法与统计度量"""

    def setUp(self):
        # 创建纯逻辑测试用的诊断引擎实例 (不加载真实大文件)
        self.engine = CoastalDatumSupportDiagnosticEngine.__new__(CoastalDatumSupportDiagnosticEngine)

    def test_failure_classification_logic(self):
        """测试 5 类失败归因判别的互斥与完备性"""
        # Case A: FES 预测失效 (陆地或超出网格)
        rec_a = NodeDiagnosticRecord(
            node_id=1, lon=121.2, lat=31.5, x=100.0, y=200.0,
            component_id=0, level=0,
            fes_any_finite=False, fes_valid_fraction=0.0, fes_flag_min=0,
            mdt_finite=True, mdt_value=0.5,
            delta_n_finite=True, delta_n_value=-0.1,
            egm_undulation_finite=True, egm_value=12.0,
            datum_offset_finite=True, datum_offset_m=0.4,
            final_solution_valid=False, failure_class=CLASS_FES_INVALID
        )
        self.assertEqual(rec_a.failure_class, CLASS_FES_INVALID)

        # Case B: FES 有效但 MDT 无效 (崇明西侧核心痛点)
        rec_b = NodeDiagnosticRecord(
            node_id=2, lon=121.3, lat=31.6, x=110.0, y=210.0,
            component_id=0, level=0,
            fes_any_finite=True, fes_valid_fraction=1.0, fes_flag_min=1,
            mdt_finite=False, mdt_value=float('nan'),
            delta_n_finite=True, delta_n_value=-0.1,
            egm_undulation_finite=True, egm_value=12.0,
            datum_offset_finite=False, datum_offset_m=float('nan'),
            final_solution_valid=False, failure_class=CLASS_FES_VALID_MDT_INVALID
        )
        self.assertEqual(rec_b.failure_class, CLASS_FES_VALID_MDT_INVALID)

        # Case C: FES、MDT 有效但 Delta-N 无效
        rec_c = NodeDiagnosticRecord(
            node_id=3, lon=121.7, lat=31.5, x=150.0, y=200.0,
            component_id=0, level=0,
            fes_any_finite=True, fes_valid_fraction=1.0, fes_flag_min=1,
            mdt_finite=True, mdt_value=0.6,
            delta_n_finite=False, delta_n_value=float('nan'),
            egm_undulation_finite=True, egm_value=12.0,
            datum_offset_finite=False, datum_offset_m=float('nan'),
            final_solution_valid=False, failure_class=CLASS_FES_VALID_MDT_VALID_DELTAN_INVALID
        )
        self.assertEqual(rec_c.failure_class, CLASS_FES_VALID_MDT_VALID_DELTAN_INVALID)

        # Case D: 全流程有效基准锚点
        rec_d = NodeDiagnosticRecord(
            node_id=4, lon=121.8, lat=31.5, x=160.0, y=200.0,
            component_id=0, level=0,
            fes_any_finite=True, fes_valid_fraction=1.0, fes_flag_min=1,
            mdt_finite=True, mdt_value=0.65,
            delta_n_finite=True, delta_n_value=-0.12,
            egm_undulation_finite=True, egm_value=12.5,
            datum_offset_finite=True, datum_offset_m=0.53,
            final_solution_valid=True, failure_class=CLASS_FES_VALID_DATUM_VALID
        )
        self.assertEqual(rec_d.failure_class, CLASS_FES_VALID_DATUM_VALID)

    def test_count_finite_mdt_corners(self):
        """测试合成网格上双线性角点有效性统计函数"""
        # 构造 4x4 网格，左侧两列为 NaN，右侧两列为有效值
        lats = np.array([31.0, 31.125, 31.25, 31.375])
        lons = np.array([121.0, 121.125, 121.25, 121.375])
        mdt = np.array([
            [np.nan, np.nan, 0.40, 0.45],
            [np.nan, np.nan, 0.42, 0.47],
            [np.nan, np.nan, 0.43, 0.48],
            [np.nan, np.nan, 0.45, 0.50]
        ])
        grid_info = {
            'lats': lats,
            'lons': lons,
            'mdt': mdt,
            'step_lat': 0.125,
            'step_lon': 0.125
        }

        # 1. 深度位于陆地左侧单元 (lon=121.05, lat=31.05) -> 4 角全为 NaN -> 0
        c0 = self.engine._count_finite_mdt_corners(121.05, 31.05, grid_info)
        self.assertEqual(c0, 0)

        # 2. 跨越海岸线交界单元 (lon=121.15, lat=31.05) -> 左角为 NaN，右角为有效值 -> 2
        c2 = self.engine._count_finite_mdt_corners(121.15, 31.05, grid_info)
        self.assertEqual(c2, 2)

        # 3. 深度位于海洋右侧单元 (lon=121.30, lat=31.20) -> 4 角全为有效 -> 4
        c4 = self.engine._count_finite_mdt_corners(121.30, 31.20, grid_info)
        self.assertEqual(c4, 4)

        # 4. 超出网格范围 -> 0
        c_out = self.engine._count_finite_mdt_corners(120.0, 30.0, grid_info)
        self.assertEqual(c_out, 0)

    def test_run_anchor_loocv_synthetic(self):
        """测试留一法交叉验证 (LOOCV) 计算与统计指标"""
        # 生成 50 个沿网格排列的合成有效锚点
        # 设定 offset 具有平滑的空间渐变: offset = 0.5 + 0.01 * dx + 0.02 * dy
        records = []
        node_id = 1
        for i in range(5):
            for j in range(10):
                lon = 121.5 + j * 0.02
                lat = 31.2 + i * 0.02
                true_off = 0.5 + 0.01 * j + 0.02 * i
                rec = NodeDiagnosticRecord(
                    node_id=node_id, lon=lon, lat=lat, x=float(j*1000), y=float(i*1000),
                    component_id=0, level=0,
                    fes_any_finite=True, fes_valid_fraction=1.0, fes_flag_min=1,
                    mdt_finite=True, mdt_value=true_off - 0.1,
                    delta_n_finite=True, delta_n_value=-0.1,
                    egm_undulation_finite=True, egm_value=10.0,
                    datum_offset_finite=True, datum_offset_m=true_off,
                    final_solution_valid=True, failure_class=CLASS_FES_VALID_DATUM_VALID
                )
                records.append(rec)
                node_id += 1

        res = self.engine.run_anchor_loocv(records, target_field='offset', max_dist_km=10.0)
        self.assertEqual(res['target_field'], 'offset')
        self.assertEqual(res['count'], 50)
        # 检验统计指标存在且非 NaN
        self.assertTrue(np.isfinite(res['mae']))
        self.assertTrue(np.isfinite(res['rmse']))
        self.assertTrue(np.isfinite(res['p95']))
        self.assertTrue(res['mae'] < 0.05) # 平滑网格 IDW 插值误差应远小于 5 cm

        # 检验分箱统计
        bins = res['bin_breakdown']
        self.assertIn('<0.5km', bins)
        self.assertIn('1-2km', bins)

    def test_evaluate_extension_strategies_synthetic(self):
        """测试沿海基准扩展策略评估算法"""
        # 构造 10 个锚点 (全部在 component 1)
        anchors = []
        for i in range(10):
            anchors.append(NodeDiagnosticRecord(
                node_id=i + 1, lon=121.6, lat=31.2 + i * 0.01, x=1000.0, y=float(i * 1000),
                component_id=1, level=0,
                fes_any_finite=True, fes_valid_fraction=1.0, fes_flag_min=1,
                mdt_finite=True, mdt_value=0.5,
                delta_n_finite=True, delta_n_value=-0.1,
                egm_undulation_finite=True, egm_value=10.0,
                datum_offset_finite=True, datum_offset_m=0.4,
                final_solution_valid=True, failure_class=CLASS_FES_VALID_DATUM_VALID
            ))

        # 构造 2 个目标节点:
        # target 1: 距离 121.6 约 0.5km，且属于 component 1
        # target 2: 距离 121.6 约 5.0km，但属于 component 2 (连通分量不同)
        # 经度差 0.005° 约 0.47 km
        t1 = NodeDiagnosticRecord(
            node_id=101, lon=121.595, lat=31.2, x=950.0, y=0.0,
            component_id=1, level=0,
            fes_any_finite=True, fes_valid_fraction=1.0, fes_flag_min=1,
            mdt_finite=False, mdt_value=float('nan'),
            delta_n_finite=True, delta_n_value=-0.1,
            egm_undulation_finite=True, egm_value=10.0,
            datum_offset_finite=False, datum_offset_m=float('nan'),
            final_solution_valid=False, failure_class=CLASS_FES_VALID_MDT_INVALID
        )
        # 经度差 0.05° 约 4.7 km
        t2 = NodeDiagnosticRecord(
            node_id=102, lon=121.55, lat=31.2, x=500.0, y=0.0,
            component_id=2, level=0,
            fes_any_finite=True, fes_valid_fraction=1.0, fes_flag_min=1,
            mdt_finite=False, mdt_value=float('nan'),
            delta_n_finite=True, delta_n_value=-0.1,
            egm_undulation_finite=True, egm_value=10.0,
            datum_offset_finite=False, datum_offset_m=float('nan'),
            final_solution_valid=False, failure_class=CLASS_FES_VALID_MDT_INVALID
        )

        all_records = anchors + [t1, t2]
        eval_res = self.engine.evaluate_extension_strategies(all_records, max_distances_km=[1.0, 8.0])

        # 在 1.0 km 阈值下:
        # t1 (0.47km) 应该被成功扩展覆盖，而 t2 (4.7km) 超出阈值
        res_1km = eval_res['results']['1.0km']
        self.assertEqual(res_1km['strategy1_unconstrained_nearest']['recovered_nodes'], 1)
        self.assertEqual(res_1km['strategy2_component_constrained']['recovered_nodes'], 1)

        # 在 8.0 km 阈值下:
        # t1 和 t2 距离都在 8km 内
        # 策略 1 (无拓扑约束) 会覆盖 2 个
        # 策略 2 (连通分量约束) 由于 component 1 中没有锚点，只能覆盖 1 个！
        res_8km = eval_res['results']['8.0km']
        self.assertEqual(res_8km['strategy1_unconstrained_nearest']['recovered_nodes'], 2)
        self.assertEqual(res_8km['strategy2_component_constrained']['recovered_nodes'], 1)


if __name__ == '__main__':
    unittest.main()
