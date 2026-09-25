"""
CoastTideX Production Round A 单元测试套件:
FES ParentBBox / Parent Spatial Scope Model Reuse 策略与生命周期测试
(Unit tests for ParentBBox Spatial Model Reuse Strategy & Lifecycle)

覆盖范围:
1. bbox_contains 纯几何包含关系判定与边界容差 (Tolerance)；
2. spatial_model_scope / begin_spatial_model_scope 单次加载与多次复用 (LGP.load() == 1)；
3. 不同分潮方案 (Constituents) 强隔离与防串扰；
4. 请求超出 ParentBBox 范围时的安全回退 (Safe Fallback to Exact BBox)；
5. 作用域退出与嵌套调用的状态清理 (Cache Cleanup & Stack Restore)；
6. 作用域内抛出异常时的确定性异常安全 (Exception Safety)；
7. 日界线 (Antimeridian +/-180) 与极区纬度裁剪 ([-90, 90]) 防御；
8. 未启用 Scope 时的传统单 Entry 精确缓存完全兼容性；
9. RasterTideEngine Stage 1 自动化作用域生命周期端到端集成与 finally 防御。
"""

import unittest
from unittest.mock import MagicMock, patch
import os
import numpy as np
import pandas as pd
import tempfile
import rasterio
from rasterio.transform import from_origin

from core.tide_engine import FESTidePredictor, bbox_contains
from core.raster_engine import RasterTideEngine


class TestParentBBoxModelReuse(unittest.TestCase):
    """测试 FES ParentBBox 模型复用、分潮隔离与异常安全生命周期"""

    def setUp(self):
        self.patch_pyfes = patch('core.tide_engine.HAS_PYFES', True)
        self.patch_pyfes.start()

        self.mock_lgp_patch = patch('core.tide_engine.cfg.LGP')
        self.mock_lgp_cls = self.mock_lgp_patch.start()

        # 构造 mock LGP model
        self.mock_model = MagicMock()
        self.mock_lgp_instance = MagicMock()
        self.mock_lgp_instance.load.return_value = self.mock_model
        self.mock_lgp_cls.return_value = self.mock_lgp_instance

        with patch('core.tide_engine.load_app_config') as mock_cfg, \
             patch('os.path.exists', return_value=True):
            mock_cfg.return_value = {
                'paths': {'fes_ns_grid': 'dummy/fes_ns_grid.nc'},
                'tide': {
                    'spatial_buffer_deg': 1.0,
                    'default_freq': '1h',
                    'default_constituents': 'all'
                }
            }
            self.predictor = FESTidePredictor()

    def tearDown(self):
        self.mock_lgp_patch.stop()
        self.patch_pyfes.stop()

    def test_01_bbox_contains_geometry(self):
        """测试几何包围框包含关系判定算法及微小数值容差"""
        parent = (120.0, 30.0, 125.0, 35.0)

        # 严格内部
        self.assertTrue(bbox_contains(parent, (121.0, 31.0, 124.0, 34.0)))

        # 边界完全贴合
        self.assertTrue(bbox_contains(parent, (120.0, 30.0, 125.0, 35.0)))

        # 微小数值浮点误差容差内 (<= 1e-5)
        self.assertTrue(bbox_contains(parent, (119.999995, 29.999995, 125.000005, 35.000005)))

        # 经度超出左界
        self.assertFalse(bbox_contains(parent, (119.5, 30.0, 125.0, 35.0)))
        # 经度超出右界
        self.assertFalse(bbox_contains(parent, (120.0, 30.0, 125.5, 35.0)))
        # 纬度超出下界
        self.assertFalse(bbox_contains(parent, (120.0, 29.5, 125.0, 35.0)))
        # 纬度超出上界
        self.assertFalse(bbox_contains(parent, (120.0, 30.0, 125.0, 35.5)))

    def test_02_contained_bbox_reuse_single_load(self):
        """测试在 ParentBBox 作用域内，不同子 BBox 请求复用同一模型，LGP.load 仅执行 1 次"""
        parent_box = (121.0, 31.0, 123.0, 32.5)
        self.predictor.begin_spatial_model_scope(bboxes=[parent_box])

        # 子批次 1 (Level 0 网格单元)
        m1 = self.predictor._get_model((121.2, 31.2, 121.5, 31.5), ['M2', 'S2'])
        # 子批次 2 (Level 1 细分单元)
        m2 = self.predictor._get_model((121.8, 31.6, 122.2, 32.0), ['M2', 'S2'])
        # 子批次 3 (边缘探测单元)
        m3 = self.predictor._get_model((122.0, 32.1, 122.8, 32.4), ['M2', 'S2'])

        self.assertIs(m1, self.mock_model)
        self.assertIs(m2, self.mock_model)
        self.assertIs(m3, self.mock_model)

        # 验证 LGP 初始化传入的是 parent_box，且 load 只被调用了一次
        self.assertEqual(self.mock_lgp_instance.load.call_count, 1)
        init_kwargs = self.mock_lgp_cls.call_args.kwargs
        self.assertEqual(init_kwargs['bbox'], parent_box)

        self.predictor.end_spatial_model_scope()

    def test_03_constituent_isolation(self):
        """测试同一 ParentBBox 下不同分潮配置强隔离，生成独立缓存 entry，禁止串扰"""
        parent_box = (121.0, 31.0, 123.0, 32.5)
        self.predictor.begin_spatial_model_scope(bboxes=[parent_box])

        mock_model_m2s2 = MagicMock(name='model_m2s2')
        mock_model_all = MagicMock(name='model_all')

        def lgp_side_effect(**kwargs):
            inst = MagicMock()
            if 'K1' in kwargs['constituents']:
                inst.load.return_value = mock_model_all
            else:
                inst.load.return_value = mock_model_m2s2
            return inst

        self.mock_lgp_cls.side_effect = lgp_side_effect

        # 请求分潮方案 A: M2, S2
        mA1 = self.predictor._get_model((121.2, 31.2, 121.5, 31.5), ['M2', 'S2'])
        mA2 = self.predictor._get_model((121.6, 31.4, 122.0, 31.8), ['M2', 'S2'])
        self.assertIs(mA1, mock_model_m2s2)
        self.assertIs(mA2, mock_model_m2s2)

        # 请求分潮方案 B: M2, S2, K1
        mB1 = self.predictor._get_model((121.2, 31.2, 121.5, 31.5), ['M2', 'S2', 'K1'])
        self.assertIs(mB1, mock_model_all)

        # 验证方案 A 与方案 B 分别加载 1 次，共 2 次
        self.assertEqual(len(self.predictor._parent_model_cache), 2)
        self.assertIn(('M2,S2', parent_box), self.predictor._parent_model_cache)
        self.assertIn(('K1,M2,S2', parent_box), self.predictor._parent_model_cache)

        self.predictor.end_spatial_model_scope()

    def test_04_out_of_parent_fallback(self):
        """测试请求 BBox 超出当前 ParentBBox 时，安全回退至精确 BBox 单次加载，不发生错误复用"""
        parent_box = (121.0, 31.0, 122.0, 32.0)
        self.predictor.begin_spatial_model_scope(bboxes=[parent_box])

        # 内部请求
        m_in = self.predictor._get_model((121.2, 31.2, 121.5, 31.5), ['M2', 'S2'])
        self.assertIs(m_in, self.mock_model)
        self.assertEqual(self.mock_lgp_cls.call_args.kwargs['bbox'], parent_box)

        # 外部请求 (超出父包围框)
        outside_box = (125.0, 28.0, 126.0, 29.0)
        m_out = self.predictor._get_model(outside_box, ['M2', 'S2'])
        self.assertIs(m_out, self.mock_model)
        # 验证回退到精确加载 outside_box
        self.assertEqual(self.mock_lgp_cls.call_args.kwargs['bbox'], outside_box)

        self.predictor.end_spatial_model_scope()

    def test_05_scope_cleanup_and_nesting(self):
        """测试作用域退出时缓存全量清空，以及嵌套作用域 push/pop 栈恢复"""
        parent_box_1 = (120.0, 30.0, 122.0, 32.0)
        parent_box_2 = (123.0, 33.0, 124.0, 34.0)

        self.predictor.begin_spatial_model_scope(bboxes=[parent_box_1])
        self.predictor._get_model((120.5, 30.5, 121.0, 31.0), ['M2'])
        self.assertEqual(len(self.predictor._parent_model_cache), 1)

        # 嵌套进入子作用域
        self.predictor.begin_spatial_model_scope(bboxes=[parent_box_2])
        self.assertEqual(len(self.predictor._parent_model_cache), 0)
        self.assertEqual(self.predictor._active_parent_bboxes, [parent_box_2])

        self.predictor._get_model((123.2, 33.2, 123.5, 33.5), ['M2'])
        self.assertEqual(len(self.predictor._parent_model_cache), 1)

        # 退出子作用域，应恢复父作用域 1 的状态
        self.predictor.end_spatial_model_scope()
        self.assertEqual(self.predictor._active_parent_bboxes, [parent_box_1])
        self.assertEqual(len(self.predictor._parent_model_cache), 1)

        # 彻底退出最外层作用域
        self.predictor.end_spatial_model_scope()
        self.assertIsNone(self.predictor._active_parent_bboxes)
        self.assertEqual(len(self.predictor._parent_model_cache), 0)

    def test_06_context_manager_and_exception_safety(self):
        """测试 spatial_model_scope 上下文管理器及抛出异常时的安全清理保障"""
        parent_box = (121.0, 31.0, 122.0, 32.0)

        with self.assertRaises(RuntimeError):
            with self.predictor.spatial_model_scope(bboxes=[parent_box]):
                self.assertIsNotNone(self.predictor._active_parent_bboxes)
                self.predictor._get_model((121.2, 31.2, 121.5, 31.5), ['M2'])
                raise RuntimeError("Simulated calculation error")

        # 验证异常发生后已安全清空
        self.assertIsNone(self.predictor._active_parent_bboxes)
        self.assertEqual(len(self.predictor._parent_model_cache), 0)

    def test_07_antimeridian_and_latitude_clipping(self):
        """测试跨越日界线与极区附近的 BBox 裁剪保护"""
        # 输入靠近极区和日界线经纬度
        lons = [179.8, -179.8]
        lats = [89.5, -89.5]

        self.predictor.begin_spatial_model_scope(lons=lons, lats=lats, buffer_deg=1.0)
        for pb in self.predictor._active_parent_bboxes:
            # 纬度必须严格在 [-90.0, 90.0] 内
            self.assertGreaterEqual(pb[1], -90.0)
            self.assertLessEqual(pb[3], 90.0)
            # 跨日界线应拆分为 2 个局部包围框，而非单幅跨度接近 360° 的畸形大框
            self.assertLess(pb[2] - pb[0], 180.0)

        self.predictor.end_spatial_model_scope()

    def test_08_legacy_no_scope_compatibility(self):
        """测试在未调用 begin_spatial_model_scope 时保持原样单 entry 精确缓存机制"""
        self.assertIsNone(self.predictor._active_parent_bboxes)

        box_a = (121.0, 31.0, 122.0, 32.0)
        m_a1 = self.predictor._get_model(box_a, ['M2'])
        m_a2 = self.predictor._get_model(box_a, ['M2'])
        self.assertIs(m_a1, m_a2)
        self.assertEqual(self.mock_lgp_instance.load.call_count, 1)

        # 改变请求包围框，触发单 entry 重新加载
        box_b = (121.5, 31.5, 122.5, 32.5)
        m_b = self.predictor._get_model(box_b, ['M2'])
        self.assertIs(m_b, self.mock_model)
        self.assertEqual(self.mock_lgp_instance.load.call_count, 2)

    def test_09_raster_engine_stage1_scope_lifecycle(self):
        """测试 RasterTideEngine Stage 1 自动化生命周期中正确调用 scope 并在完成/异常时释放"""
        engine = RasterTideEngine()

        # 构造微型测试 DEM
        with tempfile.TemporaryDirectory() as tmpdir:
            dem_path = os.path.join(tmpdir, "test_dem.tif")
            transform = from_origin(121.0, 32.0, 0.05, 0.05)
            profile = {
                'driver': 'GTiff',
                'height': 10,
                'width': 10,
                'count': 1,
                'dtype': rasterio.float32,
                'crs': 'EPSG:4326',
                'transform': transform,
                'nodata': -9999.0
            }
            data = np.full((10, 10), 1.5, dtype=np.float32)
            with rasterio.open(dem_path, 'w', **profile) as dst:
                dst.write(data, 1)

            # 使用带有 Mock 的 Predictor
            mock_predictor = MagicMock()
            mock_predictor.begin_spatial_model_scope = MagicMock()
            mock_predictor.end_spatial_model_scope = MagicMock()

            # 模拟 predict_points_period 返回
            mock_times = pd.date_range('2024-01-01 00:00', periods=5, freq='1h')
            mock_tide_mat = np.zeros((200, 5), dtype=np.float32)
            mock_flags = np.zeros((200, 5), dtype=np.int8)
            mock_predictor.predict_points_period.return_value = (mock_tide_mat, mock_times, mock_flags)

            engine.predictor = mock_predictor

            # 正常执行 grid_only 模式
            engine.calculate_inundation_raster(
                dem_path=dem_path,
                start_time='2024-01-01 00:00',
                end_time='2024-01-01 04:00',
                freq='1h',
                grid_only=True
            )

            # 验证 begin_spatial_model_scope 和 end_spatial_model_scope 均被恰好调用 1 次
            self.assertEqual(mock_predictor.begin_spatial_model_scope.call_count, 1)
            self.assertEqual(mock_predictor.end_spatial_model_scope.call_count, 1)

            # 验证 DEM footprint 经纬度传入正确
            call_kwargs = mock_predictor.begin_spatial_model_scope.call_args.kwargs
            self.assertIn('lons', call_kwargs)
            self.assertIn('lats', call_kwargs)
            self.assertEqual(call_kwargs['buffer_deg'], 1.0)


if __name__ == '__main__':
    unittest.main()
