"""
CoastTideX 单元与集成测试套件 (Test Suite v1.3)
覆盖核心逻辑：
  1. test_validate_coordinates：验证非法经纬度（>90, <-90, nan, inf 等）正确拦截；
  2. test_validate_time_params：验证结束时间 <= 起始时间、未知 freq 时报错；
  3. test_build_time_index_half_open_vs_closed：验证 [start, end) 与 [start, end] 长度与末端点；
  4. test_build_time_index_dst_elapsed_hours：验证在 America/New_York 等夏令时切换日物理流逝时间的无间断递增与点数正确性；
  5. test_predict_year_leap_2024_counts：验证 2024 年 30min 采样率在半开区间下产生严格 17,568 个样本点；
  6. test_mediterranean_polygon_accuracy：选取边缘测试点验证多边形矢量判断不会误判为 EIGEN-6C4；
  7. test_mdt_reference_geoid_classification：验证大洋输出 GOCO06s，地中海内部输出 EIGEN-6C4；
  8. test_delta_n_eigen_missing_strict_raises：验证在 EIGEN-6C4 区域且未提供 eigen 差值文件时，strict=True 抛出 DatumDataError，strict=False 返回 NaN；
  9. test_datum_semantic_h_goco_nan_in_mediterranean：验证在地中海区域 h_mdt_ref_m 有有效值但 h_goco06s_m 严格为 NaN；
  10. test_target_aware_conversion_msl_only：验证 target='msl' 时，即使未配置任何 MDT/Geoid 文件也能 100% 成功返回，且相关字段为 NaN；
  11. test_target_aware_conversion_egm_does_not_require_wgs_raster：验证 target='egm2008' 时不强制要求 EGM 绝对栅格；
  12. test_synthetic_fixture_closure：利用 set_synthetic_fixture 验证多元基准代数闭合恒等式；
  13. test_inundation_frequency_ecdf：验证淹没频率在已知水准高下的阶梯输出与百分比单调性；
  14. test_inundation_frequency_with_nans：验证包含 NaN 时淹没概率的严谨掩码处理；
  15. test_extract_scalar_metadata：验证数组、标量、NaN 等各种元数据安全提取为标量字符串；
  16. test_relative_path_resolution：验证 resolve_project_path、to_relative_project_path、get_resource_root 与 get_app_root 逻辑；
  17. test_deep_land_nan_propagation：验证深陆点 NaN 传播；
  18. test_normalize_longitude：经度归一化测试；
  19. test_coastal_presets：预设站点有效性测试；
  20. test_validate_constituents：分潮参数严格校验；
  21. test_batch_spatial_chunking_or_warning：验证批量预测包含 qc_warning 且能正常处理；
  22. test_full_chain_with_pyfes：全链路有条件真实测试。
"""

import os
import sys
import unittest
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.datum_engine import (
    DatumTransformer, DatumDataError,
    get_mdt_reference_geoid, is_mediterranean_or_black_sea
)
from core.utils import (
    normalize_longitude, COASTAL_PRESETS,
    load_app_config, resolve_project_path, to_relative_project_path,
    PROJECT_ROOT, get_resource_root, get_app_root,
    validate_coordinates, validate_time_params,
    build_time_index, compute_inundation_frequency,
    extract_scalar_metadata, convert_time_to_utc
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
HAS_DELTA_N = os.path.exists(resolve_project_path(cfg['paths'].get('delta_n_goco06s_egm2008_tif', cfg['paths'].get('delta_n_tif', ''))))


class TestCoastTideX(unittest.TestCase):

    def setUp(self):
        self.transformer = DatumTransformer()

    # 1. 验证非法经纬度正确拦截
    def test_validate_coordinates(self):
        # 合法经纬度
        lon, lat = validate_coordinates(120.0, 30.0)
        self.assertEqual((lon, lat), (120.0, 30.0))
        lon, lat = validate_coordinates(-180.0, -90.0)
        self.assertEqual((lon, lat), (-180.0, -90.0))
        lon, lat = validate_coordinates(360.0, 90.0)
        self.assertEqual((lon, lat), (360.0, 90.0))

        # 非法纬度
        with self.assertRaises(ValueError):
            validate_coordinates(120.0, 95.0)
        with self.assertRaises(ValueError):
            validate_coordinates(120.0, -90.1)

        # NaN 与 Inf
        with self.assertRaises(ValueError):
            validate_coordinates(np.nan, 30.0)
        with self.assertRaises(ValueError):
            validate_coordinates(120.0, np.nan)
        with self.assertRaises(ValueError):
            validate_coordinates(np.inf, 30.0)
        with self.assertRaises(ValueError):
            validate_coordinates(120.0, -np.inf)

    # 2. 验证结束时间 <= 起始时间、未知 freq 时报错
    def test_validate_time_params(self):
        self.assertTrue(validate_time_params("2024-01-01", "2024-01-02", "30min"))

        # end < start
        with self.assertRaises(ValueError):
            validate_time_params("2024-01-02", "2024-01-01", "1h")

        # end == start
        with self.assertRaises(ValueError):
            validate_time_params("2024-01-01 00:00:00", "2024-01-01 00:00:00", "1h")

        # 非法采样步长
        with self.assertRaises(ValueError):
            validate_time_params("2024-01-01", "2024-01-02", "invalid_freq_xyz")

    # 3. 验证 [start, end) 与 [start, end] 长度与末端点
    def test_build_time_index_half_open_vs_closed(self):
        # 闭区间 [00:00, 03:00] 步长 1h: 00:00, 01:00, 02:00, 03:00 (共 4 点)
        inp_closed, utc_closed, dates_closed = build_time_index(
            "2024-01-01 00:00:00", "2024-01-01 03:00:00", freq="1h", inclusive="both"
        )
        self.assertEqual(len(inp_closed), 4)
        self.assertEqual(inp_closed[-1], pd.Timestamp("2024-01-01 03:00:00"))

        # 左闭右开半开区间 [00:00, 03:00) 步长 1h: 00:00, 01:00, 02:00 (共 3 点)
        inp_open, utc_open, dates_open = build_time_index(
            "2024-01-01 00:00:00", "2024-01-01 03:00:00", freq="1h", inclusive="left"
        )
        self.assertEqual(len(inp_open), 3)
        self.assertEqual(inp_open[-1], pd.Timestamp("2024-01-01 02:00:00"))

    # 4. 验证在 America/New_York 等夏令时切换日物理流逝时间的无间断递增与点数正确性
    def test_build_time_index_dst_elapsed_hours(self):
        # 2024-03-10 春季切换日 (Spring Forward 跳过 1 小时，全天物理流逝 23 小时)
        inp_sp, utc_sp, dates_sp = build_time_index(
            "2024-03-10 00:00:00", "2024-03-11 00:00:00",
            freq="1h", source_tz="America/New_York", inclusive="left"
        )
        self.assertEqual(len(inp_sp), 23)
        # 严格物理时间单调递增，无重复，无间断
        self.assertTrue(np.all(np.diff(dates_sp) > np.timedelta64(0, 's')))

        # 2024-11-03 秋季切换日 (Fall Back 重复 1 小时，全天物理流逝 25 小时)
        inp_fb, utc_fb, dates_fb = build_time_index(
            "2024-11-03 00:00:00", "2024-11-04 00:00:00",
            freq="1h", source_tz="America/New_York", inclusive="left"
        )
        self.assertEqual(len(inp_fb), 25)
        self.assertTrue(np.all(np.diff(dates_fb) > np.timedelta64(0, 's')))

    # 5. 验证 2024 年 30min 采样率在半开区间下产生严格 17,568 个样本点
    def test_predict_year_leap_2024_counts(self):
        # 2024 闰年 366 天，每天 48 个半小时点: 366 * 48 = 17,568
        inp_2024, utc_2024, dates_2024 = build_time_index(
            "2024-01-01 00:00:00", "2025-01-01 00:00:00",
            freq="30min", source_tz="UTC", inclusive="left"
        )
        self.assertEqual(len(inp_2024), 17568)
        self.assertEqual(inp_2024[0], pd.Timestamp("2024-01-01 00:00:00"))
        self.assertEqual(inp_2024[-1], pd.Timestamp("2024-12-31 23:30:00"))

        # 2023 平年 365 天: 365 * 48 = 17,520
        inp_2023, _, _ = build_time_index(
            "2023-01-01 00:00:00", "2024-01-01 00:00:00",
            freq="30min", source_tz="UTC", inclusive="left"
        )
        self.assertEqual(len(inp_2023), 17520)

    # 6. 选取边缘测试点，验证多边形矢量判断不会误判为 EIGEN-6C4
    def test_mediterranean_polygon_accuracy(self):
        # 加的斯湾 (大西洋): (-6.3°E, 36.5°N) -> 绝非地中海
        self.assertEqual(get_mdt_reference_geoid(-6.3, 36.5), 'GOCO06s')
        self.assertFalse(bool(np.atleast_1d(is_mediterranean_or_black_sea(-6.3, 36.5))[0]))

        # 直布罗陀海峡西口外大西洋侧: (-7.0°E, 36.0°N) -> 绝非地中海
        self.assertEqual(get_mdt_reference_geoid(-7.0, 36.0), 'GOCO06s')
        self.assertFalse(bool(np.atleast_1d(is_mediterranean_or_black_sea(-7.0, 36.0))[0]))

        # 比斯开湾 (大西洋): (-3.0°E, 44.5°N) -> 绝非地中海
        self.assertEqual(get_mdt_reference_geoid(-3.0, 44.5), 'GOCO06s')
        self.assertFalse(bool(np.atleast_1d(is_mediterranean_or_black_sea(-3.0, 44.5))[0]))

        # 葡萄牙西海岸 (里斯本外海): (-9.5°E, 38.7°N) -> 绝非地中海
        self.assertEqual(get_mdt_reference_geoid(-9.5, 38.7), 'GOCO06s')
        self.assertFalse(bool(np.atleast_1d(is_mediterranean_or_black_sea(-9.5, 38.7))[0]))

        # 西班牙内陆 (马德里): (-3.7°E, 40.4°N) -> 绝非地中海
        self.assertEqual(get_mdt_reference_geoid(-3.7, 40.4), 'GOCO06s')
        self.assertFalse(bool(np.atleast_1d(is_mediterranean_or_black_sea(-3.7, 40.4))[0]))

        # 红海: (38.0°E, 20.0°N) -> 绝非地中海
        self.assertEqual(get_mdt_reference_geoid(38.0, 20.0), 'GOCO06s')
        self.assertFalse(bool(np.atleast_1d(is_mediterranean_or_black_sea(38.0, 20.0))[0]))

    # 7. 验证大洋输出 GOCO06s，地中海/黑海内部输出 EIGEN-6C4
    def test_mdt_reference_geoid_classification(self):
        # 大洋 (长江口) -> GOCO06s
        self.assertEqual(get_mdt_reference_geoid(122.0, 31.0), 'GOCO06s')
        # 地中海 (利古里亚海 / 罗马西海域) -> EIGEN-6C4
        self.assertEqual(get_mdt_reference_geoid(12.0, 41.5), 'EIGEN-6C4')
        # 黑海 (中心海域) -> EIGEN-6C4
        self.assertEqual(get_mdt_reference_geoid(35.0, 43.0), 'EIGEN-6C4')
        # 非法纬度
        self.assertEqual(get_mdt_reference_geoid(120.0, 95.0), 'INVALID')

    # 8. 验证在 EIGEN-6C4 区域且未提供 eigen 差值文件时，strict=True 抛出 DatumDataError，strict=False 返回 NaN
    def test_delta_n_eigen_missing_strict_raises(self):
        trans = DatumTransformer(delta_n_eigen_path="nonexistent_delta_eigen.tif")
        med_lon, med_lat = 15.0, 38.0  # 第勒尼安海 (地中海内部)

        # strict=True 时显式抛出科学异常，严禁暗中改用 GOCO 差值替代
        with self.assertRaises(DatumDataError):
            trans.get_delta_n(med_lon, med_lat, strict=True)

        # strict=False 时优雅返回 NaN 并保留警告
        val = trans.get_delta_n(med_lon, med_lat, strict=False)
        self.assertTrue(np.isnan(val))

    # 9. 验证在地中海区域 h_mdt_ref_m 有有效值但 h_goco06s_m 严格为 NaN
    def test_datum_semantic_h_goco_nan_in_mediterranean(self):
        trans = DatumTransformer()
        trans.set_synthetic_fixture(mdt=0.35, delta_goco=-0.15, delta_eigen=0.03, n_egm=48.2)
        med_lon, med_lat = 15.0, 38.0

        res = trans.convert_tide_datums(tide_msl_m=1.20, lons=med_lon, lats=med_lat, datum_target='all')
        # h_mdt_ref_m 应为有效值: 1.20 + 0.35 = 1.55
        self.assertAlmostEqual(res['h_mdt_ref_m'], 1.55)
        # 语义严格性：h_goco06s_m 在地中海严格为 NaN，不冒充！
        self.assertTrue(np.isnan(res['h_goco06s_m']))
        # h_egm2008_m = 1.55 + 0.03 = 1.58
        self.assertAlmostEqual(res['h_egm2008_m'], 1.58)
        # h_wgs84_m = 1.58 + 48.2 = 49.78
        self.assertAlmostEqual(res['h_wgs84_m'], 49.78)
        self.assertEqual(res['datum_ref_geoid'], 'EIGEN-6C4')
        self.assertEqual(res['qc_warning'], 'QC_MED_BLACK_SEA_EIGEN6C4')

    # 10. 验证 target='msl' 时，即使未配置任何 MDT/Geoid 文件也能 100% 成功返回，且相关字段为 NaN
    def test_target_aware_conversion_msl_only(self):
        dummy_trans = DatumTransformer(
            mdt_path="missing_mdt.nc",
            egm2008_path="missing_egm.tif",
            delta_n_path="missing_dn.tif"
        )
        res = dummy_trans.convert_tide_datums(
            tide_msl_m=2.35, lons=122.0, lats=31.0, datum_target='msl', strict=True
        )
        self.assertAlmostEqual(res['tide_msl_m'], 2.35)
        self.assertTrue(np.isnan(res['mdt_m']))
        self.assertTrue(np.isnan(res['delta_n_m']))
        self.assertTrue(np.isnan(res['h_mdt_ref_m']))
        self.assertTrue(np.isnan(res['h_egm2008_m']))
        self.assertTrue(np.isnan(res['h_wgs84_m']))
        self.assertEqual(res['datum_ref_geoid'], 'MSL')

    # 11. 验证 target='egm2008' 时不强制要求 EGM 绝对栅格
    def test_target_aware_conversion_egm_does_not_require_wgs_raster(self):
        trans = DatumTransformer(egm2008_path="missing_egm_wgs.tif")
        trans.set_synthetic_fixture(mdt=0.50, delta_goco=-0.25, delta_eigen=None, n_egm=None)
        res = trans.convert_tide_datums(
            tide_msl_m=1.00, lons=122.0, lats=31.0, datum_target='egm2008', strict=True
        )
        # h_egm2008_m = 1.00 + 0.50 - 0.25 = 1.25
        self.assertAlmostEqual(res['h_egm2008_m'], 1.25)
        self.assertTrue(np.isnan(res['h_wgs84_m']))

    # 12. 利用 set_synthetic_fixture 验证多元基准代数闭合恒等式
    def test_synthetic_fixture_closure(self):
        trans = DatumTransformer()
        trans.set_synthetic_fixture(mdt=0.80, delta_goco=-0.30, delta_eigen=0.05, n_egm=15.0)

        tides = np.array([1.2, -0.5, 2.1])
        lons = np.array([122.0, 122.5, 123.0])
        lats = np.array([31.0, 31.2, 31.5])

        res = trans.convert_tide_datums(tides, lons, lats, datum_target='all', strict=True)
        np.testing.assert_allclose(res['h_mdt_ref_m'], tides + 0.80)
        np.testing.assert_allclose(res['h_egm2008_m'], res['h_mdt_ref_m'] - 0.30)
        np.testing.assert_allclose(res['h_wgs84_m'], res['h_egm2008_m'] + 15.0)

    # 13. 验证淹没频率在已知水准高下的阶梯输出与百分比单调性
    def test_inundation_frequency_ecdf(self):
        water_levels = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        terrain = np.array([0.0, 2.0, 3.5, 5.0, 6.0])

        freq_pct = compute_inundation_frequency(water_levels, terrain, as_percentage=True)
        # terrain=0.0 -> 全部 5 点 > 0.0 -> 100%
        self.assertAlmostEqual(freq_pct[0], 100.0)
        # terrain=2.0 -> 3 点 (3,4,5) > 2.0 -> 3/5 = 60%
        self.assertAlmostEqual(freq_pct[1], 60.0)
        # terrain=3.5 -> 2 点 (4,5) > 3.5 -> 2/5 = 40%
        self.assertAlmostEqual(freq_pct[2], 40.0)
        # terrain=5.0 -> 0 点 > 5.0 -> 0%
        self.assertAlmostEqual(freq_pct[3], 0.0)
        # terrain=6.0 -> 0 点 > 6.0 -> 0%
        self.assertAlmostEqual(freq_pct[4], 0.0)

        # 频率随高程单调递减或平坦
        self.assertTrue(np.all(np.diff(freq_pct) <= 0))

    # 14. 验证包含 NaN 时淹没概率的严谨掩码处理
    def test_inundation_frequency_with_nans(self):
        water_levels = np.array([1.0, np.nan, 3.0, 4.0])
        terrain = np.array([2.0, np.nan])

        freq = compute_inundation_frequency(water_levels, terrain, as_percentage=False)
        # 有效水位 3 个 (1.0, 3.0, 4.0)，其中 2 个 > 2.0 -> 2/3 ≈ 0.6667
        self.assertAlmostEqual(freq[0], 2.0 / 3.0, places=4)
        self.assertTrue(np.isnan(freq[1]))

    # 15. 验证数组、标量、NaN 等各种元数据安全提取为标量字符串
    def test_extract_scalar_metadata(self):
        self.assertEqual(extract_scalar_metadata("1.2346"), "1.2346")
        self.assertEqual(extract_scalar_metadata(np.nan, default="-"), "-")
        self.assertEqual(extract_scalar_metadata(np.array(["2.5"])), "2.5")
        self.assertEqual(extract_scalar_metadata("GOCO06s"), "GOCO06s")
        self.assertEqual(extract_scalar_metadata(np.array(["EIGEN-6C4"])), "EIGEN-6C4")

    # 16. 验证 resolve_project_path、to_relative_project_path、get_resource_root 与 get_app_root 逻辑
    def test_relative_path_resolution(self):
        self.assertTrue(os.path.exists(get_resource_root()))
        self.assertTrue(os.path.exists(get_app_root()))

        res_path = resolve_project_path("data/geoid/us_nga_egm08_25.tif")
        self.assertTrue(os.path.isabs(res_path))

        rel = to_relative_project_path(os.path.join(PROJECT_ROOT, "data", "geoid", "sample.tif"))
        self.assertEqual(rel.replace("\\", "/"), "data/geoid/sample.tif")

    # 17. 验证深陆点 NaN 传播
    @unittest.skipUnless(HAS_MDT, "CNES-CLS22 MDT 文件未就绪，跳过陆地 NaN 测试")
    def test_deep_land_nan_propagation(self):
        land_lon, land_lat = 100.0, 35.0  # 青藏高原深内陆
        mdt_land = self.transformer.get_mdt(land_lon, land_lat)
        self.assertTrue(np.isnan(mdt_land))

        res = self.transformer.convert_tide_datums(0.0, land_lon, land_lat)
        self.assertTrue(np.isnan(res['mdt_m']))
        self.assertTrue(np.isnan(res['h_mdt_ref_m']))
        self.assertTrue(np.isnan(res['h_egm2008_m']))

    # 18. 经度归一化测试
    def test_normalize_longitude(self):
        self.assertAlmostEqual(normalize_longitude(-120.0, to_360=True), 240.0)
        self.assertAlmostEqual(normalize_longitude(240.0, to_360=False), -120.0)
        self.assertAlmostEqual(normalize_longitude(0.0, to_360=True), 0.0)
        self.assertAlmostEqual(normalize_longitude(360.0, to_360=True), 0.0)

    # 19. 预设站点有效性测试
    def test_coastal_presets(self):
        self.assertIn("长江口 (Changjiang Estuary)", COASTAL_PRESETS)
        preset = COASTAL_PRESETS["长江口 (Changjiang Estuary)"]
        self.assertEqual(preset["lon"], 122.0)
        self.assertEqual(preset["lat"], 31.0)

    # 20. 分潮参数严格校验
    def test_validate_constituents(self):
        if validate_constituents is not None:
            self.assertEqual(len(validate_constituents('all')), 34)
            self.assertEqual(len(validate_constituents('major8')), 8)
            self.assertEqual(validate_constituents(['M2', 'S2']), ['M2', 'S2'])
            with self.assertRaises(ValueError):
                validate_constituents(['INVALID_NAME'])

    # 21. 验证批量预测包含 qc_warning 且能正常处理
    def test_batch_spatial_chunking_or_warning(self):
        df_batch = pd.DataFrame({
            'longitude': [122.0, 15.0],
            'latitude': [31.0, 38.0],
            'datetime': ['2026-09-10 00:00:00', '2026-09-10 00:00:00'],
            'tide_total_m': [1.2, 0.5]
        })
        trans = DatumTransformer()
        trans.set_synthetic_fixture(mdt=0.5, delta_goco=-0.2, delta_eigen=0.03, n_egm=20.0)
        datums = trans.convert_tide_datums(
            df_batch['tide_total_m'].values,
            df_batch['longitude'].values,
            df_batch['latitude'].values
        )
        self.assertIn('qc_warning', datums)
        self.assertEqual(datums['qc_warning'][0], 'NORMAL')
        self.assertEqual(datums['qc_warning'][1], 'QC_MED_BLACK_SEA_EIGEN6C4')

    # 22. 全链路有条件真实测试
    @unittest.skipUnless(HAS_FES and HAS_MDT and HAS_EGM and HAS_DELTA_N, "完整模型网格数据未就绪，跳过真实 pyfes 潮位预测")
    def test_full_chain_with_pyfes(self):
        predictor = FESTidePredictor()
        df = predictor.predict_point_period(
            lon=122.0,
            lat=31.0,
            start_time='2026-09-10 00:00:00',
            end_time='2026-09-10 02:00:00',
            freq='1h',
            constituents='major8',
            datum_mode='both'
        )
        self.assertEqual(len(df), 3)
        self.assertIn('tide_total_m', df.columns)
        self.assertIn('h_mdt_ref_m', df.columns)
        self.assertIn('h_egm2008_m', df.columns)
        self.assertIn('qc_warning', df.columns)
        self.assertFalse(df['h_egm2008_m'].isna().any())


if __name__ == '__main__':
    unittest.main()

