"""
CoastTideX 单元与集成测试套件 (Test Suite v1.1)
严格验证：
  1. 空间坐标与经度归一化；
  2. 垂直基准闭合数学关系 (H_EGM2008 = Tide + MDT + ΔN, h_WGS84 = H_EGM2008 + N_EGM2008)；
  3. 陆地区域 NaN 严格传播（杜绝隐式静默返回 0.0）；
  4. FES2022b 原生非结构网格调和潮位解算与时区校准。
"""

import os
import sys
import unittest
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.tide_engine import FESTidePredictor
from core.datum_engine import DatumTransformer
from core.utils import normalize_longitude, COASTAL_PRESETS


class TestCoastTideX(unittest.TestCase):

    def setUp(self):
        self.predictor = FESTidePredictor()
        self.transformer = DatumTransformer()

    def test_normalize_longitude(self):
        self.assertAlmostEqual(normalize_longitude(-120.0, to_360=True), 240.0)
        self.assertAlmostEqual(normalize_longitude(240.0, to_360=False), -120.0)
        self.assertAlmostEqual(normalize_longitude(122.0, to_360=True), 122.0)
        self.assertAlmostEqual(normalize_longitude(0.0, to_360=True), 0.0)
        self.assertAlmostEqual(normalize_longitude(360.0, to_360=True), 0.0)

    def test_coastal_presets(self):
        self.assertIn("长江口 (Changjiang Estuary)", COASTAL_PRESETS)
        preset = COASTAL_PRESETS["长江口 (Changjiang Estuary)"]
        self.assertEqual(preset["lon"], 122.0)
        self.assertEqual(preset["lat"], 31.0)

    def test_datum_transformer_changjiang(self):
        """测试长江口基准数据取值范围与物理合理性"""
        lon, lat = 122.0, 31.0

        # MDT 测试
        mdt_val = self.transformer.get_mdt(lon, lat)
        self.assertFalse(np.isnan(mdt_val))
        self.assertGreater(mdt_val, 0.5)
        self.assertLess(mdt_val, 1.0)

        # Delta N (GOCO06s - EGM2008)
        dn_val = self.transformer.get_delta_n(lon, lat)
        self.assertFalse(np.isnan(dn_val))
        self.assertGreater(dn_val, -2.0)
        self.assertLess(dn_val, 2.0)

        # EGM2008 N
        n_val = self.transformer.get_egm2008_undulation(lon, lat)
        self.assertFalse(np.isnan(n_val))
        self.assertGreater(n_val, 10.0)
        self.assertLess(n_val, 18.0)

    def test_datum_closure_and_vectorization(self):
        """测试多元基准闭合数学关系及向量化计算"""
        lons = np.array([122.0, 122.5, 123.0])
        lats = np.array([31.0, 31.2, 31.5])
        tides = np.array([1.25, -0.80, 2.10])

        res = self.transformer.convert_tide_datums(tides, lons, lats)

        # 检查返回键
        for k in ['tide_msl_m', 'mdt_m', 'delta_n_m', 'n_egm2008_m', 'h_goco06s_m', 'h_egm2008_m', 'h_wgs84_m']:
            self.assertIn(k, res)
            self.assertEqual(len(res[k]), 3)

        # 验证严密数学闭合公式
        np.testing.assert_allclose(
            res['h_goco06s_m'],
            res['tide_msl_m'] + res['mdt_m'],
            rtol=1e-5
        )
        np.testing.assert_allclose(
            res['h_egm2008_m'],
            res['h_goco06s_m'] + res['delta_n_m'],
            rtol=1e-5
        )
        np.testing.assert_allclose(
            res['h_wgs84_m'],
            res['h_egm2008_m'] + res['n_egm2008_m'],
            rtol=1e-5
        )

    def test_nan_propagation_deep_land(self):
        """测试深陆地点 (100°E, 35°N 青藏高原内陆) 严密返回 NaN，杜绝静默返回 0.0"""
        land_lon, land_lat = 100.0, 35.0

        mdt_land = self.transformer.get_mdt(land_lon, land_lat)
        self.assertTrue(np.isnan(mdt_land), f"陆地 MDT 应为 NaN，实际为 {mdt_land}")

        res = self.transformer.convert_tide_datums(0.0, land_lon, land_lat)
        self.assertTrue(np.isnan(res['mdt_m']))
        self.assertTrue(np.isnan(res['h_goco06s_m']))
        self.assertTrue(np.isnan(res['h_egm2008_m']))
        self.assertTrue(np.isnan(res['h_wgs84_m']))

    def test_tide_prediction_series_with_datums(self):
        """测试单点时序预测及基准级联计算"""
        df = self.predictor.predict_series(
            lon=122.0,
            lat=31.0,
            start_time='2026-09-10 00:00:00',
            end_time='2026-09-10 03:00:00',
            freq='1h',
            constituents='major8',
            source_tz='UTC'
        )
        self.assertEqual(len(df), 4)
        self.assertIn('tide_total_m', df.columns)
        self.assertIn('datetime_utc', df.columns)
        self.assertTrue((df['quality_flag'] > 0).all())

        # 联合多元基准转换
        datums = self.transformer.convert_tide_datums(
            df['tide_total_m'].values, 122.0, 31.0
        )
        self.assertEqual(len(datums['h_egm2008_m']), 4)
        self.assertFalse(np.isnan(datums['h_egm2008_m']).any())


if __name__ == '__main__':
    unittest.main()
