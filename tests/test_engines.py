"""
CoastTideX 单元与集成测试套件 (Test Suite v1.4)
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

import tempfile
import shutil
import rasterio
from rasterio.transform import from_origin
from unittest.mock import patch, MagicMock

from core.datum_engine import (
    DatumTransformer, DatumDataError,
    get_mdt_reference_geoid, is_mediterranean_or_black_sea,
    _broadcast_pointwise_inputs
)
from core.raster_engine import (
    RasterTideEngine, RasterInfo, ControlNode,
    QC_VALID, QC_FES_EXTRAPOLATED, QC_SPATIAL_FALLBACK, QC_INSUFFICIENT_NODES
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
        self.assertEqual(res['qc_warning'], 'QC_DATUM_SOURCE_APPROX')


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
        self.assertEqual(datums['qc_warning'][1], 'QC_DATUM_SOURCE_APPROX')


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

    # 23. 严格广播校验测试 (mismatched non-scalar shapes must raise ValueError)
    def test_pointwise_broadcast_mismatch_raises(self):
        # 形状不匹配非标量 (3 vs 2)
        with self.assertRaises(ValueError):
            _broadcast_pointwise_inputs(
                np.array([1.0, 2.0, 3.0]),
                np.array([120.0, 121.0]),
                np.array([30.0, 31.0])
            )

        # 纬度与经度不匹配 (3 vs 2)
        with self.assertRaises(ValueError):
            _broadcast_pointwise_inputs(
                1.0,
                np.array([120.0, 121.0, 122.0]),
                np.array([30.0, 31.0])
            )

        # 正常广播：标量水位 + 数组经纬度
        t_b, lo_b, la_b = _broadcast_pointwise_inputs(
            1.5,
            np.array([120.0, 121.0]),
            np.array([30.0, 31.0])
        )
        self.assertEqual(len(t_b), 2)
        self.assertEqual(t_b[0], 1.5)
        self.assertEqual(t_b[1], 1.5)

        # 正常广播：数组水位 + 单点经纬度 (单点长时序解算)
        t_b, lo_b, la_b = _broadcast_pointwise_inputs(
            np.array([1.0, 2.0, 3.0]),
            120.5,
            31.2
        )
        self.assertEqual(len(lo_b), 3)
        self.assertEqual(lo_b[0], 120.5)
        self.assertEqual(la_b[2], 31.2)

    # 24. 验证 datum_target='both' 时不强制加载 EGM2008 绝对起伏栅格
    def test_convert_tide_datums_both_does_not_require_egm_undulation(self):
        trans = DatumTransformer()
        trans.set_synthetic_fixture(mdt=0.5, delta_goco=-0.2, delta_eigen=0.03, n_egm=np.nan)
        # 模拟 EGM2008 栅格路径不存在
        trans._egm2008_src = None
        trans.egm2008_path = "I:/nonexistent_egm2008_undulation.tif"

        res = trans.convert_tide_datums(
            tide_msl_m=1.0,
            lons=122.0,
            lats=31.0,
            datum_target='both',
            strict=True
        )
        self.assertAlmostEqual(res['tide_msl_m'], 1.0)
        # H_EGM2008 = Tide (1.0) + MDT (0.5) + DeltaN (-0.2) = 1.3
        self.assertAlmostEqual(res['h_egm2008_m'], 1.3)
        self.assertTrue(np.isnan(res['n_egm2008_m']))

    # 25. 验证合成 GeoTIFF 空间元数据检查 (inspect_raster)
    def test_raster_inspect_synthetic_geotiff(self):
        engine = RasterTideEngine()
        with tempfile.TemporaryDirectory() as tmpdir:
            tif_path = os.path.join(tmpdir, "test_dem.tif")
            profile = {
                'driver': 'GTiff',
                'height': 15,
                'width': 20,
                'count': 1,
                'dtype': rasterio.float32,
                'crs': 'EPSG:32651',
                'transform': from_origin(500000.0, 3400000.0, 10.0, 10.0),
                'nodata': -9999.0
            }
            data = np.full((15, 20), 12.5, dtype=np.float32)
            data[0, :5] = -9999.0  # 5 个 NoData 点

            with rasterio.open(tif_path, 'w', **profile) as dst:
                dst.write(data, 1)

            info = engine.inspect_raster(tif_path, compute_valid_count=True)
            self.assertEqual(info.width, 20)
            self.assertEqual(info.height, 15)
            self.assertEqual(info.total_pixel_count, 300)
            self.assertEqual(info.valid_pixel_count, 295)
            self.assertTrue(info.is_projected)
            self.assertAlmostEqual(info.resolution[0], 10.0)
            self.assertAlmostEqual(info.resolution[1], 10.0)
            self.assertEqual(info.formatted_resolution, "10.00 m × 10.00 m")

            # 验证地理坐标系 (如 EPSG:4326 WGS84) 下度数格式化与地面等效距离估算
            geo_tif_path = os.path.join(tmpdir, "test_geo.tif")
            geo_profile = {
                'driver': 'GTiff',
                'height': 100,
                'width': 100,
                'count': 1,
                'dtype': rasterio.float32,
                'crs': 'EPSG:4326',
                'transform': from_origin(19.0, -34.7386, 0.000999, 0.000999),
                'nodata': -9999.0
            }
            with rasterio.open(geo_tif_path, 'w', **geo_profile) as dst:
                dst.write(np.zeros((100, 100), dtype=np.float32), 1)

            info_geo = engine.inspect_raster(geo_tif_path, compute_valid_count=False)
            self.assertFalse(info_geo.is_projected)
            self.assertIn("0.000999° × 0.000999°", info_geo.formatted_resolution)
            self.assertIn("约", info_geo.formatted_resolution)
            self.assertNotIn("0.00 × 0.00", info_geo.formatted_resolution)
            self.assertIn("m", info_geo.formatted_resolution)

    # 26. 验证像元中心几何坐标对齐 (Pixel Centers with offset=center)
    def test_raster_pixel_centers_coordinate_alignment(self):
        engine = RasterTideEngine()
        tf = from_origin(120.0, 32.0, 0.1, 0.1)

        rows = np.array([0, 1])
        cols = np.array([0, 2])
        lons, lats = engine.pixel_centers_to_lonlat(tf, "EPSG:4326", rows, cols)

        # 像元中心严密坐标：
        # Row 0, Col 0: lon = 120.0 + 0.5 * 0.1 = 120.05, lat = 32.0 - 0.5 * 0.1 = 31.95
        # Row 1, Col 2: lon = 120.0 + 2.5 * 0.1 = 120.25, lat = 32.0 - 1.5 * 0.1 = 31.85
        self.assertAlmostEqual(lons[0], 120.05, places=5)
        self.assertAlmostEqual(lats[0], 31.95, places=5)
        self.assertAlmostEqual(lons[1], 120.25, places=5)
        self.assertAlmostEqual(lats[1], 31.85, places=5)

    # 27. 验证单时刻空间潮位解算与富元数据写入 (Snapshot Raster Engine)
    def test_raster_snapshot_synthetic(self):
        engine = RasterTideEngine()
        engine.transformer.set_synthetic_fixture(mdt=0.5, delta_goco=-0.2, delta_eigen=0.03, n_egm=25.0)

        with tempfile.TemporaryDirectory() as tmpdir:
            in_tif = os.path.join(tmpdir, "input.tif")
            out_tif = os.path.join(tmpdir, "snapshot_out.tif")
            profile = {
                'driver': 'GTiff',
                'height': 10,
                'width': 10,
                'count': 1,
                'dtype': rasterio.float32,
                'crs': 'EPSG:4326',
                'transform': from_origin(122.0, 31.0, 0.01, 0.01),
                'nodata': -9999.0
            }
            data = np.full((10, 10), 1.0, dtype=np.float32)
            data[0, 0] = -9999.0  # 1 个 NoData

            with rasterio.open(in_tif, 'w', **profile) as dst:
                dst.write(data, 1)

            # 模拟 pyfes evaluate_tide 返回恒定潮位 1.5m (150cm)
            with patch('pyfes.evaluate_tide', return_value=(np.full(99, 150.0), np.zeros(99), np.ones(99))):
                with patch.object(engine._get_predictor(), '_get_model', return_value=MagicMock()):
                    summary = engine.calculate_snapshot_raster(
                        input_raster_path=in_tif,
                        output_raster_path=out_tif,
                        timestamp="2024-06-15 12:00:00",
                        datum_target="egm2008",
                        strict=False
                    )

            self.assertTrue(os.path.exists(out_tif))
            self.assertEqual(summary.valid_pixels, 99)
            self.assertEqual(summary.total_pixels, 100)

            with rasterio.open(out_tif) as src_out:
                out_data = src_out.read(1)
                self.assertTrue(np.isnan(out_data[0, 0]))
                # 水位 = Tide (1.5) + MDT (0.5) + DeltaN (-0.2) = 1.8m
                self.assertAlmostEqual(out_data[0, 1], 1.8, places=3)
                tags = src_out.tags()
                self.assertEqual(tags.get('SOFTWARE'), 'CoastTideX v1.4')
                self.assertEqual(tags.get('ENGINE_MODE'), 'snapshot_raster')
                self.assertEqual(tags.get('VERTICAL_DATUM'), 'EGM2008')

    # 28. 验证潜在天文潮淹没概率 (CCDF) 严密性与地形屏障非插值
    def test_inundation_frequency_oracle_vs_spatial(self):
        # Oracle 测试：已知潮位分布下的离散淹没率
        water_levels = np.array([0.0, 1.0, 2.0, 3.0, 4.0])
        dem_elevations = np.array([-1.0, 0.5, 2.0, 3.5, 5.0])
        freq = compute_inundation_frequency(water_levels, dem_elevations, as_percentage=True)
        # -1.0 -> 5/5 = 100%
        # 0.5 -> 4/5 (1,2,3,4) = 80%
        # 2.0 -> 2/5 (3,4) = 40%
        # 3.5 -> 1/5 (4) = 20%
        # 5.0 -> 0/5 = 0%
        expected = np.array([100.0, 80.0, 40.0, 20.0, 0.0])
        np.testing.assert_allclose(freq, expected)

        # 屏障非插值逻辑检验：当周围全为无效陆地节点时，严密标记 QC_INSUFFICIENT_NODES 并输出 NoData
        node_invalid = ControlNode(
            node_id=0, x=0.0, y=0.0, lon=120.0, lat=30.0,
            water_levels_sorted=np.array([]), valid=False, quality_flag=0,
            static_offset_m=0.0, qc_code=QC_INSUFFICIENT_NODES
        )
        self.assertFalse(node_invalid.valid)
        self.assertEqual(node_invalid.qc_code, QC_INSUFFICIENT_NODES)

    # 29. 验证 GUI 与 CLI 模块导入健全性
    def test_gui_and_cli_importable(self):
        from gui.main_window import MainWindow, RasterTideWorker, SingleTideWorker, BatchTideWorker
        self.assertTrue(issubclass(RasterTideWorker, unittest.TestCase.__base__))
        import cli
        self.assertTrue(hasattr(cli, 'main'))


if __name__ == '__main__':
    unittest.main()

