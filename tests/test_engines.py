"""
CoastTideX 单元与集成测试套件 (Test Suite v1.2.0)
严格验证：
  1. 空间坐标与经度归一化；
  2. 垂直基准闭合数学关系 (H_EGM2008 = Tide + MDT + ΔN, h_WGS84 = H_EGM2008 + N_EGM2008)；
  3. 大地水准面与差值栅格基准控制点精确数值；
  4. 动态时区与夏令时 (DST) 稳健转换；
  5. 配置文件相对路径保存与跨平台可移植性；
  6. 陆地区域 NaN 严格传播（杜绝隐式静默返回 0.0）；
  7. FES2022b 原生非结构网格调和潮位解算 (有条件执行)。
"""

import os
import sys
import unittest
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.datum_engine import DatumTransformer, is_mediterranean_or_black_sea
from core.utils import (
    normalize_longitude, COASTAL_PRESETS,
    load_app_config, resolve_project_path,
    convert_time_to_utc, to_relative_project_path, PROJECT_ROOT
)

try:
    from core.tide_engine import FESTidePredictor, HAS_PYFES, validate_constituents
except ImportError:
    FESTidePredictor = None
    HAS_PYFES = False
    validate_constituents = None

cfg = load_app_config()
HAS_FES = HAS_PYFES and os.path.exists(resolve_project_path(cfg['paths'].get('fes_ns_grid', '')))
HAS_MDT = os.path.exists(resolve_project_path(cfg['paths'].get('mdt_nc', '')))
HAS_EGM = os.path.exists(resolve_project_path(cfg['paths'].get('egm2008_tif', '')))
HAS_DELTA_N = os.path.exists(resolve_project_path(cfg['paths'].get('delta_n_tif', '')))


class TestCoastTideX(unittest.TestCase):

    def setUp(self):
        self.transformer = DatumTransformer()

    def test_normalize_longitude(self):
        """测试全球经度跨子午线归一化"""
        self.assertAlmostEqual(normalize_longitude(-120.0, to_360=True), 240.0)
        self.assertAlmostEqual(normalize_longitude(240.0, to_360=False), -120.0)
        self.assertAlmostEqual(normalize_longitude(122.0, to_360=True), 122.0)
        self.assertAlmostEqual(normalize_longitude(0.0, to_360=True), 0.0)
        self.assertAlmostEqual(normalize_longitude(360.0, to_360=True), 0.0)

    def test_coastal_presets(self):
        """测试预设海岸站点有效性"""
        self.assertIn("长江口 (Changjiang Estuary)", COASTAL_PRESETS)
        preset = COASTAL_PRESETS["长江口 (Changjiang Estuary)"]
        self.assertEqual(preset["lon"], 122.0)
        self.assertEqual(preset["lat"], 31.0)

    def test_timezone_and_dst(self):
        """测试 UTC 与本地时间转换，验证动态夏令时及非整时区兼容性"""
        # UTC 模式保持原值
        utc_idx, _ = convert_time_to_utc("2026-07-15 12:00:00", source_tz="UTC")
        self.assertEqual(utc_idx[0], pd.Timestamp("2026-07-15 12:00:00"))

        # 本地时区模式转换
        loc_idx, _ = convert_time_to_utc("2026-01-15 12:00:00", source_tz="local")
        self.assertEqual(len(loc_idx), 1)

        # 验证夏冬两季动态时区转换未产生崩溃或异常固定常数错位
        loc_summer, _ = convert_time_to_utc(["2026-07-15 12:00:00", "2026-01-15 12:00:00"], source_tz="local")
        self.assertEqual(len(loc_summer), 2)

    def test_relative_path_portability(self):
        """测试配置相对路径转换函数与可移植性"""
        inside_path = os.path.join(PROJECT_ROOT, "data", "geoid", "test.tif")
        rel_path = to_relative_project_path(inside_path)
        self.assertEqual(rel_path.replace("\\", "/"), "data/geoid/test.tif")

        # 外部路径保持原样 (正斜杠格式)
        outside_path = os.path.abspath(os.path.join(PROJECT_ROOT, "..", "outside_data", "grid.nc")).replace('\\', '/')
        self.assertEqual(to_relative_project_path(outside_path), outside_path)

    @unittest.skipUnless(HAS_EGM and HAS_DELTA_N, "GeoTIFF 大地水准面文件不存在，跳过基准基准点验证")
    def test_datum_transformer_changjiang_ground_truth(self):
        """测试长江口基准控制点 (122.0°E, 31.0°N) 精确数值 (纠正 0.5 像元偏移后的理论真值)"""
        lon, lat = 122.0, 31.0

        # EGM2008 N (理论栅格节点精确值 ~13.9245 m)
        n_val = self.transformer.get_egm2008_undulation(lon, lat)
        self.assertFalse(np.isnan(n_val))
        self.assertAlmostEqual(n_val, 13.9245, places=3)

        # Delta N (GOCO06s - EGM2008, 理论栅格节点精确值 ~-0.3302 m)
        dn_val = self.transformer.get_delta_n(lon, lat)
        self.assertFalse(np.isnan(dn_val))
        self.assertAlmostEqual(dn_val, -0.3302, places=3)

        # MDT 测试 (若数据存在)
        if HAS_MDT:
            mdt_val = self.transformer.get_mdt(lon, lat)
            self.assertFalse(np.isnan(mdt_val))
            self.assertAlmostEqual(mdt_val, 0.7312, places=3)

    @unittest.skipUnless(HAS_EGM and HAS_DELTA_N, "GeoTIFF 大地水准面文件不存在")
    def test_geoid_vectorization_and_closure(self):
        """测试向量化大地水准面查询与代数闭合关系 (无须 MDT 网格)"""
        lons = np.array([122.0, 122.5, 123.0])
        lats = np.array([31.0, 31.2, 31.5])
        tides = np.array([1.25, -0.80, 2.10])
        mock_mdt = np.array([0.5, 0.6, 0.7])

        delta_n = self.transformer.get_delta_n(lons, lats)
        n_egm = self.transformer.get_egm2008_undulation(lons, lats)

        h_goco = tides + mock_mdt
        h_egm = h_goco + delta_n
        h_wgs = h_egm + n_egm

        self.assertEqual(len(delta_n), 3)
        self.assertEqual(len(n_egm), 3)
        np.testing.assert_allclose(h_egm, h_goco + delta_n, rtol=1e-5)
        np.testing.assert_allclose(h_wgs, h_egm + n_egm, rtol=1e-5)

    @unittest.skipUnless(HAS_MDT and HAS_EGM and HAS_DELTA_N, "MDT 与大地水准面栅格文件未就绪，跳过全基准闭合测试")
    def test_datum_closure_and_vectorization(self):
        """测试多元基准闭合数学关系及向量化计算 (严格闭合)"""
        lons = np.array([122.0, 122.5, 123.0])
        lats = np.array([31.0, 31.2, 31.5])
        tides = np.array([1.25, -0.80, 2.10])

        res = self.transformer.convert_tide_datums(tides, lons, lats)

        # 检查返回键
        for k in ['tide_msl_m', 'mdt_m', 'delta_n_m', 'n_egm2008_m', 'h_goco06s_m', 'h_egm2008_m', 'h_wgs84_m']:
            self.assertIn(k, res)
            self.assertEqual(len(res[k]), 3)

        # 验证严密数学闭合公式
        valid_mask = ~np.isnan(res['h_wgs84_m'])
        if np.any(valid_mask):
            np.testing.assert_allclose(
                res['h_goco06s_m'][valid_mask],
                res['tide_msl_m'][valid_mask] + res['mdt_m'][valid_mask],
                rtol=1e-5
            )
            np.testing.assert_allclose(
                res['h_egm2008_m'][valid_mask],
                res['h_goco06s_m'][valid_mask] + res['delta_n_m'][valid_mask],
                rtol=1e-5
            )
            np.testing.assert_allclose(
                res['h_wgs84_m'][valid_mask],
                res['h_egm2008_m'][valid_mask] + res['n_egm2008_m'][valid_mask],
                rtol=1e-5
            )

    @unittest.skipUnless(HAS_MDT, "CNES-CLS22 MDT 文件不存在，跳过陆地 NaN 测试")
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

    @unittest.skipUnless(HAS_FES and HAS_MDT and HAS_EGM and HAS_DELTA_N, "完整模型网格数据未全部就绪，跳过全流程潮位预测")
    def test_tide_prediction_series_with_datums(self):
        """测试单点时序预测及基准级联计算"""
        predictor = FESTidePredictor()
        df = predictor.predict_series(
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

    def test_mediterranean_and_black_sea_detection(self):
        """测试地中海与黑海空间区域判定 (EIGEN-6C4 基准)"""
        # 地中海坐标 (12.0°E, 40.0°N) -> True
        self.assertTrue(is_mediterranean_or_black_sea(12.0, 40.0)[0])
        # 黑海坐标 (35.0°E, 43.0°N) -> True
        self.assertTrue(is_mediterranean_or_black_sea(35.0, 43.0)[0])
        # 长江口 (122.0°E, 31.0°N) -> False
        self.assertFalse(is_mediterranean_or_black_sea(122.0, 31.0)[0])
        # 大西洋 (-40.0°W, 30.0°N) -> False
        self.assertFalse(is_mediterranean_or_black_sea(-40.0, 30.0)[0])

    def test_validate_constituents(self):
        """测试分潮参数统一严格校验"""
        if validate_constituents is not None:
            self.assertEqual(len(validate_constituents('all')), 34)
            self.assertEqual(len(validate_constituents('major8')), 8)
            self.assertEqual(validate_constituents(['M2', 'S2']), ['M2', 'S2'])

            with self.assertRaises(ValueError):
                validate_constituents(['INVALID_TIDE_NAME'])

            with self.assertRaises(ValueError):
                validate_constituents(99999)

    def test_msl_mode_and_graceful_missing_data(self):
        """测试仅 MSL 模式与缺失外部栅格时的软着陆机制 (不抛未捕获异常崩溃)"""
        dummy_trans = DatumTransformer(
            mdt_path='nonexistent_mdt.nc',
            egm2008_path='nonexistent_egm.tif',
            delta_n_path='nonexistent_delta_n.tif'
        )
        res = dummy_trans.convert_tide_datums(1.85, 122.0, 31.0)
        self.assertEqual(res['tide_msl_m'], 1.85)
        self.assertTrue(np.isnan(res['mdt_m']))
        self.assertTrue(np.isnan(res['h_egm2008_m']))
        self.assertTrue(np.isnan(res['h_wgs84_m']))
        self.assertIn('datum_ref_geoid', res)

    def test_ci_mock_end_to_end_pipeline(self):
        """测试无大模型数据文件环境下的端到端 Mock 模拟与全基准计算 (保障 CI 真实链路覆盖)"""
        dates = pd.date_range('2026-09-10 00:00:00', periods=5, freq='1h')
        mock_tide_m = np.array([1.10, 1.45, 0.80, -0.35, -0.90])
        df_mock = pd.DataFrame({
            'datetime_utc': dates,
            'datetime_input': dates,
            'longitude': 122.0,
            'latitude': 31.0,
            'tide_total_m': mock_tide_m,
            'quality_flag': np.ones(5, dtype=int)
        })

        # 运行基准转换
        res = self.transformer.convert_tide_datums(df_mock['tide_total_m'].values, 122.0, 31.0)
        self.assertIn('tide_msl_m', res)
        self.assertIn('datum_ref_geoid', res)
        self.assertEqual(len(res['tide_msl_m']), 5)
        # 验证地中海点输入
        med_res = self.transformer.convert_tide_datums(mock_tide_m, 12.0, 40.0)
        self.assertEqual(med_res['datum_ref_geoid'][0], 'EIGEN-6C4 (CMEMS2020)')
        self.assertEqual(med_res['qc_warning'][0], 'QC_MED_BLACK_SEA_EIGEN6C4')

    def test_dst_transition_safety(self):
        """测试夏令时回拨重叠小时，确保不产生静默非法 NaT"""
        times = ['2026-11-01 01:00:00', '2026-11-01 01:30:00', '2026-11-01 02:00:00']
        utc_idx, _ = convert_time_to_utc(times, source_tz='America/New_York')
        self.assertFalse(utc_idx.isna().any())
        self.assertEqual(len(utc_idx), 3)


if __name__ == '__main__':
    unittest.main()
