"""
CoastTideX 单元与集成测试脚本
测试 FES2022b 潮汐解算、MDT 查询、EGM2008 大地水准面起伏与完整转换链路。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
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

    def test_coastal_presets(self):
        self.assertIn("长江口 (Changjiang Estuary)", COASTAL_PRESETS)
        preset = COASTAL_PRESETS["长江口 (Changjiang Estuary)"]
        self.assertEqual(preset["lon"], 122.0)
        self.assertEqual(preset["lat"], 31.0)

    def test_datum_transformer(self):
        # 测试长江口 MDT
        mdt_val = self.transformer.get_mdt(122.0, 31.0)
        self.assertGreater(mdt_val, 0.5)
        self.assertLess(mdt_val, 1.0)

        # 测试长江口 EGM2008 N
        n_val = self.transformer.get_geoid_undulation(122.0, 31.0)
        self.assertGreater(n_val, 10.0)
        self.assertLess(n_val, 18.0)

    def test_tide_prediction_series(self):
        df = self.predictor.predict_series(
            lon=122.0,
            lat=31.0,
            start_time='2026-09-10 00:00:00',
            end_time='2026-09-10 03:00:00',
            freq='1h',
            constituents='major8'
        )
        self.assertEqual(len(df), 4)
        self.assertIn('tide_total_m', df.columns)
        self.assertTrue((df['quality_flag'] > 0).all())

        # 联立基准转换测试
        egm_tide, mdt = self.transformer.convert_msl_to_egm2008(df['tide_total_m'].values, 122.0, 31.0)
        self.assertEqual(len(egm_tide), 4)
        self.assertAlmostEqual(egm_tide[0], df['tide_total_m'].iloc[0] + mdt, places=4)


if __name__ == '__main__':
    unittest.main()
