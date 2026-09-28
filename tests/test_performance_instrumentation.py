"""
单元测试: FES 仪器化与模型缓存剖析器测试套件
(Unit tests for InstrumentedFESTidePredictor and model cache behavior)

测试重点:
1. 仪器化指标计数器 (model_load_count, cache_hits, cache_misses, unique_bboxes 等)；
2. 单 entry 缓存下的抖动模拟 (Cache Thrashing when BBox shifts)；
3. 瓦片/区域级大包围框缓存优化验证 (Tile BBox Cache eliminates reloads)。
使用 Mock 模拟底层 pyfes，确保在纯净 Linux CI / 无 pyfes 环境下 100% 稳定运行。
"""

import unittest
from unittest.mock import MagicMock, patch
import sys

from scripts.diagnose_coastal_datum_support import InstrumentedFESTidePredictor


class TestPerformanceInstrumentation(unittest.TestCase):
    """测试 FES 预测器性能统计与缓存感知机制"""

    def setUp(self):
        self.patch_pyfes = patch('core.tide_engine.HAS_PYFES', True)
        self.patch_pyfes.start()
        # 构造一个不需要连接外部配置的 Predictor 测试实例
        with patch('core.tide_engine.load_app_config') as mock_cfg, \
             patch('os.path.exists', return_value=True):
            mock_cfg.return_value = {
                'paths': {
                    'fes_ns_grid': 'dummy/fes_ns_grid.nc'
                },
                'tide': {
                    'spatial_buffer_deg': 1.0,
                    'default_freq': '1h',
                    'default_constituents': 'all'
                }
            }
            self.predictor_default = InstrumentedFESTidePredictor(
                enable_tile_bbox_cache=False
            )
            self.predictor_tile_cached = InstrumentedFESTidePredictor(
                enable_tile_bbox_cache=True,
                parent_bbox=(121.0, 31.0, 122.5, 32.0)
            )

    def tearDown(self):
        self.patch_pyfes.stop()

    def test_stats_initialization(self):
        """测试初始统计字典结构与初值"""
        stats = self.predictor_default.stats
        self.assertEqual(stats['model_load_count'], 0)
        self.assertEqual(stats['model_load_time_sec'], 0.0)
        self.assertEqual(stats['cache_hits'], 0)
        self.assertEqual(stats['cache_misses'], 0)
        self.assertEqual(len(stats['unique_bboxes']), 0)

    def test_single_entry_cache_thrashing_demonstration(self):
        """测试单 entry 缓存：当 BBox 微小滑动时导致 cache thrashing"""
        mock_pyfes = MagicMock()
        mock_lgp_instance = MagicMock()
        mock_lgp_instance.load.return_value = "mock_model_obj"
        mock_pyfes.config.LGP.return_value = mock_lgp_instance

        with patch.dict(sys.modules, {'pyfes': mock_pyfes, 'pyfes.config': mock_pyfes.config}):
            constituents = ['M2', 'S2']
            bbox_1 = (121.0, 31.0, 121.2, 31.2)
            bbox_2 = (121.1, 31.1, 121.3, 31.3) # 微小平移

            # 1. 第一次调用 bbox_1 -> Miss
            m1 = self.predictor_default._get_model(bbox_1, constituents)
            self.assertEqual(self.predictor_default.stats['model_load_count'], 1)
            self.assertEqual(self.predictor_default.stats['cache_misses'], 1)
            self.assertEqual(self.predictor_default.stats['cache_hits'], 0)

            # 2. 第二次调用相同 bbox_1 -> Hit
            m2 = self.predictor_default._get_model(bbox_1, constituents)
            self.assertEqual(self.predictor_default.stats['model_load_count'], 1)
            self.assertEqual(self.predictor_default.stats['cache_hits'], 1)

            # 3. 第三次调用微调后的 bbox_2 -> Miss 且重新 load (Thrashing)
            m3 = self.predictor_default._get_model(bbox_2, constituents)
            self.assertEqual(self.predictor_default.stats['model_load_count'], 2)
            self.assertEqual(self.predictor_default.stats['cache_misses'], 2)
            self.assertEqual(len(self.predictor_default.stats['unique_bboxes']), 2)

    def test_tile_bbox_cache_eliminates_reloads(self):
        """测试瓦片/区域包围框缓存：多个不同子 bbox 均命中父包围框模型，避免重复加载"""
        mock_pyfes = MagicMock()
        mock_lgp_instance = MagicMock()
        mock_lgp_instance.load.return_value = "mock_parent_model_obj"
        mock_pyfes.config.LGP.return_value = mock_lgp_instance

        with patch.dict(sys.modules, {'pyfes': mock_pyfes, 'pyfes.config': mock_pyfes.config}):
            constituents = ['M2', 'S2']
            sub_bbox_1 = (121.1, 31.1, 121.3, 31.3)
            sub_bbox_2 = (121.4, 31.4, 121.6, 31.6)
            sub_bbox_3 = (121.7, 31.2, 122.0, 31.5)

            # 1. 第一次调用子 bbox 1 -> 父模型 Load (Miss 1 次)
            m1 = self.predictor_tile_cached._get_model(sub_bbox_1, constituents)
            self.assertEqual(self.predictor_tile_cached.stats['model_load_count'], 1)
            self.assertEqual(self.predictor_tile_cached.stats['cache_misses'], 1)
            self.assertEqual(self.predictor_tile_cached.stats['cache_hits'], 0)

            # 2. 第二次调用不同子 bbox 2 -> 命中父模型 (Hit 1 次，load 仍为 1)
            m2 = self.predictor_tile_cached._get_model(sub_bbox_2, constituents)
            self.assertEqual(self.predictor_tile_cached.stats['model_load_count'], 1)
            self.assertEqual(self.predictor_tile_cached.stats['cache_hits'], 1)

            # 3. 第三次调用不同子 bbox 3 -> 再次命中 (Hit 2 次，load 仍为 1)
            m3 = self.predictor_tile_cached._get_model(sub_bbox_3, constituents)
            self.assertEqual(self.predictor_tile_cached.stats['model_load_count'], 1)
            self.assertEqual(self.predictor_tile_cached.stats['cache_hits'], 2)


if __name__ == '__main__':
    unittest.main()
