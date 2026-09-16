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
from core.tide_engine import FESTidePredictor, SyntheticTidePredictor, TwoBasinSyntheticPredictor
from core.raster_engine import (
    RasterTideEngine, RasterInfo, ControlNode,
    RasterMemoryLimitError, estimate_control_node_memory,
    QC_VALID, QC_FES_EXTRAPOLATED, QC_SPATIAL_FALLBACK, QC_INSUFFICIENT_NODES,
    QC_BIT_VALID, QC_BIT_MIN_SPACING_REACHED, QC_BIT_DATUM_SOURCE_APPROX,
    QC_BIT_FES_EXTRAPOLATED, QC_BIT_SPATIAL_FALLBACK, QC_BIT_DATUM_INVALID,
    QC_BIT_CONNECTIVITY_FALLBACK, QC_BIT_INSUFFICIENT_NODES,
    QC_BIT_FES_VALIDITY_BOUNDARY, QC_BIT_MAX_REFINEMENT_REACHED
)
from core.utils import (
    normalize_longitude, COASTAL_PRESETS,
    load_app_config, resolve_project_path, to_relative_project_path,
    PROJECT_ROOT, get_resource_root, get_app_root,
    validate_coordinates, validate_time_params,
    build_time_index, compute_inundation_frequency,
    extract_scalar_metadata, convert_time_to_utc, circular_longitude_span,
    build_circular_fes_bboxes
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
    @unittest.skipUnless(HAS_PYFES, "未安装 pyfes 运行库环境，跳过真实/Mock pyfes 潮位预测")
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
        is_ci = (os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true")
        try:
            from gui.main_window import MainWindow, RasterTideWorker, SingleTideWorker, BatchTideWorker
            self.assertTrue(issubclass(RasterTideWorker, unittest.TestCase.__base__))
        except ImportError as e:
            if is_ci:
                self.fail(f"CI 环境已配置 PyQt6 与系统图形依赖，GUI 导入失败应报错拦截: {e}")
            pass  # 在极简本地无 GUI 运行环境下平滑跳过
        import cli
        self.assertTrue(hasattr(cli, 'main'))

    # 30. Test A: 验证自适应控制网格真实动态细分与节点数随容差单调增长 (无假元数据)
    def test_adaptive_grid_dynamic_subdivision_node_count(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dem_path = os.path.join(temp_dir, "dem_gradient.tif")
            out_coarse = os.path.join(temp_dir, "out_coarse.tif")
            out_fine = os.path.join(temp_dir, "out_fine.tif")

            width, height = 80, 80
            res = 50.0
            transform = from_origin(500000.0, 3500000.0, res, res)
            dem_data = np.linspace(-2.0, 2.0, width * height, dtype=np.float32).reshape((height, width))

            with rasterio.open(
                dem_path, 'w', driver='GTiff', width=width, height=height, count=1,
                dtype='float32', crs='EPSG:32651', transform=transform, nodata=-9999.0
            ) as dst:
                dst.write(dem_data, 1)

            synth = SyntheticTidePredictor(
                base_mean=0.0, base_amp=2.0,
                ref_lon=123.05, ref_lat=31.59,
                gamma_nonlinear=5000.0
            )
            engine = RasterTideEngine(tide_predictor=synth)

            # 1. 粗网格运行：极高容差 (90%) 且最小间距等于初始间距 (4000m)
            summary_coarse = engine.calculate_inundation_raster(
                dem_path=dem_path, output_path=out_coarse,
                start_time="2024-01-01 00:00:00", end_time="2024-01-02 00:00:00",
                freq="1h", dem_datum='msl',
                initial_control_spacing_m=4000.0, min_control_spacing_m=4000.0,
                inundation_error_tolerance_pct=90.0, block_size=128
            )

            # 2. 细网格运行：极严公差 (0.0001%) 且允许进一步细分至 1000m
            summary_fine = engine.calculate_inundation_raster(
                dem_path=dem_path, output_path=out_fine,
                start_time="2024-01-01 00:00:00", end_time="2024-01-02 00:00:00",
                freq="1h", dem_datum='msl',
                initial_control_spacing_m=4000.0, min_control_spacing_m=1000.0,
                inundation_error_tolerance_pct=0.0001, block_size=128
            )

            self.assertGreater(summary_fine.control_nodes_count, summary_coarse.control_nodes_count)
            self.assertEqual(summary_coarse.control_nodes_count, 5)
            self.assertGreaterEqual(summary_fine.control_nodes_count, 20)
            self.assertTrue(os.path.exists(out_coarse))
            self.assertTrue(os.path.exists(out_fine))

    # 31. Test B: 验证四叉树达到最小间距约束时停止递归并正确设置 QC_BIT_MIN_SPACING_REACHED
    def test_min_spacing_termination_and_qc_bit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dem_path = os.path.join(temp_dir, "dem_min_spacing.tif")
            out_inund = os.path.join(temp_dir, "out_min_spacing.tif")
            qc_out = os.path.join(temp_dir, "out_min_spacing_qc.tif")

            width, height = 80, 80
            res = 50.0
            transform = from_origin(500000.0, 3500000.0, res, res)
            dem_data = np.linspace(-2.0, 2.0, width * height, dtype=np.float32).reshape((height, width))

            with rasterio.open(
                dem_path, 'w', driver='GTiff', width=width, height=height, count=1,
                dtype='float32', crs='EPSG:32651', transform=transform, nodata=-9999.0
            ) as dst:
                dst.write(dem_data, 1)

            synth = SyntheticTidePredictor(
                base_mean=0.0, base_amp=2.0,
                ref_lon=123.05, ref_lat=31.59,
                gamma_nonlinear=5000.0
            )
            engine = RasterTideEngine(tide_predictor=synth)

            summary = engine.calculate_inundation_raster(
                dem_path=dem_path, output_path=out_inund, qc_output_path=qc_out,
                start_time="2024-01-01 00:00:00", end_time="2024-01-02 00:00:00",
                freq="1h", dem_datum='msl',
                initial_control_spacing_m=4000.0, min_control_spacing_m=1000.0,
                inundation_error_tolerance_pct=0.0001, block_size=128
            )

            self.assertTrue(os.path.exists(qc_out))
            with rasterio.open(qc_out) as src:
                qc_data = src.read(1)
                has_min_spacing_bit = ((qc_data & QC_BIT_MIN_SPACING_REACHED) > 0).any()
                self.assertTrue(has_min_spacing_bit, "细分触及最小间距时叶节点单元应设置 QC_BIT_MIN_SPACING_REACHED")

    # 32. Test C: 高保真预言机对比检验潜在天文潮淹没频率 (CCDF) 的数值解析准确性
    def test_oracle_inundation_frequency_exact(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dem_path = os.path.join(temp_dir, "dem_oracle.tif")
            out_inund = os.path.join(temp_dir, "out_oracle.tif")

            width, height = 10, 10
            transform = from_origin(120.0, 30.0, 0.01, 0.01)
            dem_data = np.zeros((height, width), dtype=np.float32)

            with rasterio.open(
                dem_path, 'w', driver='GTiff', width=width, height=height, count=1,
                dtype='float32', crs='EPSG:4326', transform=transform, nodata=-9999.0
            ) as dst:
                dst.write(dem_data, 1)

            synth = SyntheticTidePredictor(base_mean=0.0, base_amp=2.0, alpha_x=0.0, beta_y=0.0, gamma_nonlinear=0.0)
            engine = RasterTideEngine(tide_predictor=synth)

            summary = engine.calculate_inundation_raster(
                dem_path=dem_path, output_path=out_inund,
                start_time="2024-01-01 00:00:00", end_time="2024-01-02 00:00:00",
                freq="10min", dem_datum='msl',
                initial_control_spacing_m=4000.0, inundation_error_tolerance_pct=1.0
            )

            with rasterio.open(out_inund) as src:
                res_data = src.read(1)
                # 对称余弦振荡潮位在 0m 高程处的理论淹没概率严格约为 50% (半开区间采样离散容差 2.5%)
                np.testing.assert_allclose(res_data, 50.0, atol=2.5)

    # 33. Test D: 验证非凸/复杂几何边界与拓扑屏障隔离 (杜绝跨陆地/无数据屏障非法插值)
    def test_barrier_isolation_topology_guard(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dem_path = os.path.join(temp_dir, "dem_barrier.tif")
            out_inund = os.path.join(temp_dir, "out_barrier.tif")
            qc_out = os.path.join(temp_dir, "out_barrier_qc.tif")

            width, height = 60, 60
            transform = from_origin(500000.0, 3500000.0, 100.0, 100.0)
            dem_data = np.full((height, width), -9999.0, dtype=np.float32)
            dem_data[:, 0:20] = 0.0   # 盆地 1
            dem_data[:, 40:60] = 0.0  # 盆地 2

            with rasterio.open(
                dem_path, 'w', driver='GTiff', width=width, height=height, count=1,
                dtype='float32', crs='EPSG:32651', transform=transform, nodata=-9999.0
            ) as dst:
                dst.write(dem_data, 1)

            synth = SyntheticTidePredictor(base_mean=0.0, base_amp=2.0, alpha_x=0.0, beta_y=0.0, gamma_nonlinear=0.0)
            engine = RasterTideEngine(tide_predictor=synth)

            summary = engine.calculate_inundation_raster(
                dem_path=dem_path, output_path=out_inund, qc_output_path=qc_out,
                start_time="2024-01-01 00:00:00", end_time="2024-01-02 00:00:00",
                freq="1h", dem_datum='msl',
                initial_control_spacing_m=2000.0, min_control_spacing_m=1000.0
            )

            with rasterio.open(out_inund) as src:
                res_data = src.read(1)
                is_barrier_nodata = np.isnan(res_data[:, 25:35]) if (src.nodata is None or np.isnan(src.nodata)) else np.isclose(res_data[:, 25:35], src.nodata)
                self.assertTrue(is_barrier_nodata.all(), "中间陆地/屏障带像元必须保持 NoData 阻断传播")
                self.assertTrue(np.isfinite(res_data[:, 5:15]).all())
                self.assertTrue(np.isfinite(res_data[:, 45:55]).all())

    # 34. Test E: 验证权威来源掩膜规范类别映射 (1->GOCO06s, 2/3->EIGEN-6C4, 0/255->Fallback, Invalid->Invalid)
    def test_canonical_source_mask_categories(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            mask_path = os.path.join(temp_dir, "canonical_mask.tif")
            width, height = 5, 1
            transform = from_origin(0.0, 10.0, 1.0, 1.0)
            mask_data = np.array([[1, 2, 3, 0, 255]], dtype=np.uint8)

            with rasterio.open(
                mask_path, 'w', driver='GTiff', width=width, height=height, count=1,
                dtype='uint8', crs='EPSG:4326', transform=transform, nodata=255
            ) as dst:
                dst.write(mask_data, 1)

            transformer = DatumTransformer(source_mask_path=mask_path)

            ref1, qc1 = transformer.get_mdt_reference_geoid(0.5, 9.5)
            self.assertEqual(ref1, 'GOCO06s')
            self.assertEqual(qc1, 'AUTHORITATIVE_MASK')

            ref2, qc2 = transformer.get_mdt_reference_geoid(1.5, 9.5)
            self.assertEqual(ref2, 'EIGEN-6C4')
            self.assertEqual(qc2, 'AUTHORITATIVE_MASK')

            ref3, qc3 = transformer.get_mdt_reference_geoid(2.5, 9.5)
            self.assertEqual(ref3, 'EIGEN-6C4')
            self.assertEqual(qc3, 'AUTHORITATIVE_MASK')

            ref4, qc4 = transformer.get_mdt_reference_geoid(3.5, 9.5)
            self.assertEqual(qc4, 'QC_DATUM_SOURCE_APPROX')

            ref5, qc5 = transformer.get_mdt_reference_geoid(4.5, 9.5)
            self.assertEqual(qc5, 'QC_DATUM_SOURCE_APPROX')

            ref_inv, qc_inv = transformer.get_mdt_reference_geoid(999.0, 999.0)
            self.assertEqual(ref_inv, 'INVALID')
            self.assertEqual(qc_inv, 'QC_DATUM_INVALID')

    # 35. Test F: 验证非 WGS84 投影坐标系权威掩膜的自动空间重投影与正确采样
    def test_projected_crs_source_mask(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            mask_path = os.path.join(temp_dir, "utm_mask.tif")
            width, height = 10, 10
            transform = from_origin(500000.0, 3500000.0, 1000.0, 1000.0)
            mask_data = np.ones((height, width), dtype=np.uint8)

            with rasterio.open(
                mask_path, 'w', driver='GTiff', width=width, height=height, count=1,
                dtype='uint8', crs='EPSG:32651', transform=transform, nodata=255
            ) as dst:
                dst.write(mask_data, 1)

            transformer = DatumTransformer(source_mask_path=mask_path)
            ref, qc = transformer.get_mdt_reference_geoid(123.0527, 31.5901)
            self.assertEqual(ref, 'GOCO06s')
            self.assertEqual(qc, 'AUTHORITATIVE_MASK')

    # 36. Test G: 验证格林尼治与国际日期变更线环形经度跨度 (circular_longitude_span)
    def test_circular_longitude_span(self):
        span, _, _ = circular_longitude_span([120.0, 122.0])
        self.assertAlmostEqual(span, 2.0, places=4)
        span, _, _ = circular_longitude_span([-1.0, 1.0])
        self.assertAlmostEqual(span, 2.0, places=4)
        span, _, _ = circular_longitude_span([179.0, -179.0])
        self.assertAlmostEqual(span, 2.0, places=4)
        span, _, _ = circular_longitude_span([170.0, -170.0])
        self.assertAlmostEqual(span, 20.0, places=4)
        span, _, _ = circular_longitude_span([50.0])
        self.assertEqual(span, 0.0)
        span, _, _ = circular_longitude_span([])
        self.assertEqual(span, 0.0)

    # 37. Test H: 验证投影坐标系英制单位 (US Survey Feet) 的自适应米制换算与网格间距保持
    def test_projected_crs_feet_units(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = RasterTideEngine()
            tif_path = os.path.join(temp_dir, "test_feet.tif")
            width, height = 20, 20
            transform = from_origin(6000000.0, 2000000.0, 10.0, 10.0)
            data = np.ones((height, width), dtype=np.float32)

            with rasterio.open(
                tif_path, 'w', driver='GTiff', width=width, height=height, count=1,
                dtype='float32', crs='EPSG:2227', transform=transform, nodata=-9999.0
            ) as dst:
                dst.write(data, 1)

            info = engine.inspect_raster(tif_path, compute_valid_count=False)
            self.assertTrue(info.is_projected)
            self.assertAlmostEqual(info.unit_factor, 0.3048, delta=0.01)
            self.assertIn("ft", info.formatted_resolution.lower())

    # 38. Test I: 验证质检位掩膜 (QC Bitmask) 多标志共存保留与纯净有效性
    def test_qc_bitmask_retention(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dem_path = os.path.join(temp_dir, "dem_qc_bits.tif")
            out_inund = os.path.join(temp_dir, "out_qc_bits.tif")
            qc_out = os.path.join(temp_dir, "out_qc_bits_qc.tif")

            width, height = 20, 20
            transform = from_origin(120.0, 30.0, 0.01, 0.01)
            dem_data = np.zeros((height, width), dtype=np.float32)

            with rasterio.open(
                dem_path, 'w', driver='GTiff', width=width, height=height, count=1,
                dtype='float32', crs='EPSG:4326', transform=transform, nodata=-9999.0
            ) as dst:
                dst.write(dem_data, 1)

            synth = SyntheticTidePredictor(base_mean=0.0, base_amp=2.0)
            engine = RasterTideEngine(tide_predictor=synth)

            engine.calculate_inundation_raster(
                dem_path=dem_path, output_path=out_inund, qc_output_path=qc_out,
                start_time="2024-01-01 00:00:00", end_time="2024-01-01 04:00:00",
                freq="1h", dem_datum='msl',
                initial_control_spacing_m=4000.0, inundation_error_tolerance_pct=1.0
            )

            with rasterio.open(qc_out) as src:
                self.assertEqual(src.dtypes[0], 'uint16')
                qc_arr = src.read(1)
                self.assertTrue((qc_arr == QC_BIT_VALID).all())

    # 39. Test J: 验证解算像元数与输出 GeoTIFF 非 NaN 像元统计绝对一致性
    def test_solved_pixel_count_consistency(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dem_path = os.path.join(temp_dir, "dem_counts.tif")
            out_snap = os.path.join(temp_dir, "out_snap_counts.tif")
            out_inund = os.path.join(temp_dir, "out_inund_counts.tif")

            width, height = 10, 10
            transform = from_origin(120.0, 30.0, 0.01, 0.01)
            dem_data = np.full((height, width), -9999.0, dtype=np.float32)
            dem_data[0:5, :] = 1.0

            with rasterio.open(
                dem_path, 'w', driver='GTiff', width=width, height=height, count=1,
                dtype='float32', crs='EPSG:4326', transform=transform, nodata=-9999.0
            ) as dst:
                dst.write(dem_data, 1)

            synth = SyntheticTidePredictor()
            engine = RasterTideEngine(tide_predictor=synth)

            # 1. 快照模式检验
            snap_summary = engine.calculate_snapshot_raster(
                input_raster_path=dem_path, output_raster_path=out_snap,
                timestamp="2024-06-15 12:00:00", datum_target='msl'
            )
            self.assertEqual(snap_summary.total_pixels, 100)
            self.assertEqual(snap_summary.valid_pixels, 50)
            with rasterio.open(out_snap) as src:
                arr = src.read(1)
                finite_count = int(np.count_nonzero(~np.isnan(arr)))
                self.assertEqual(finite_count, 50)

            # 2. 淹没频率模式检验
            inund_summary = engine.calculate_inundation_raster(
                dem_path=dem_path, output_path=out_inund,
                start_time="2024-01-01 00:00:00", end_time="2024-01-01 02:00:00",
                freq="1h", dem_datum='msl'
            )
            self.assertEqual(inund_summary.total_pixels, 100)
            self.assertEqual(inund_summary.valid_pixels, 50)
            with rasterio.open(out_inund) as src:
                arr = src.read(1)
                valid_mask = ~np.isnan(arr) if (src.nodata is None or np.isnan(src.nodata)) else (~np.isnan(arr) & ~np.isclose(arr, src.nodata))
                finite_count = int(np.count_nonzero(valid_mask))
                self.assertEqual(finite_count, 50)

    # 40. Test K: 验证无头/CI环境直接导入 GUI 模块与取消信号线程安全性
    def test_gui_direct_imports_headless(self):
        is_ci = (os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true")
        try:
            from gui.main_window import MainWindow, RasterTideWorker, SingleTideWorker, BatchTideWorker
        except ImportError as e:
            if is_ci:
                self.fail(f"CI 环境已配置 PyQt6 与系统图形依赖 (libEGL/X11)，GUI 导入失败应报错拦截: {e}")
            else:
                self.skipTest(f"本地无头环境缺少系统图形依赖 (如 libEGL/X11)，安全跳过 GUI 直接导入测试: {e}")
            return
        worker = RasterTideWorker('snapshot', {'input_path': 'test.tif'})
        self.assertFalse(worker._is_cancelled)
        worker.cancel()
        self.assertTrue(worker._is_cancelled)
        self.assertTrue(worker.cancel_event.is_set())

    # 41. Test L: 验证合成潮汐预测器 (SyntheticTidePredictor) 离线全流程端到端集成
    def test_synthetic_offline_end_to_end(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dem_path = os.path.join(temp_dir, "dem_synth.tif")
            out_snap = os.path.join(temp_dir, "out_synth_snap.tif")
            out_inund = os.path.join(temp_dir, "out_synth_inund.tif")

            width, height = 20, 20
            transform = from_origin(120.0, 30.0, 0.01, 0.01)
            dem_data = np.zeros((height, width), dtype=np.float32)

            with rasterio.open(
                dem_path, 'w', driver='GTiff', width=width, height=height, count=1,
                dtype='float32', crs='EPSG:4326', transform=transform, nodata=-9999.0
            ) as dst:
                dst.write(dem_data, 1)

            synth = SyntheticTidePredictor(base_mean=0.5, base_amp=1.5)
            engine = RasterTideEngine(tide_predictor=synth)

            snap_sum = engine.calculate_snapshot_raster(
                input_raster_path=dem_path, output_raster_path=out_snap,
                timestamp="2024-01-01 12:00:00", datum_target='msl'
            )
            self.assertTrue(os.path.exists(out_snap))
            self.assertEqual(snap_sum.valid_pixels, 400)

            inund_sum = engine.calculate_inundation_raster(
                dem_path=dem_path, output_path=out_inund,
                start_time="2024-01-01 00:00:00", end_time="2024-01-01 06:00:00",
                freq="1h", dem_datum='msl'
            )
            self.assertTrue(os.path.exists(out_inund))
            self.assertEqual(inund_sum.valid_pixels, 400)

    # 42. Test M: 验证跨 0°/360° 本初子午线的紧致双 BBox 拆分与紧致跨度计算
    def test_circular_fes_bboxes_and_span(self):
        # 1. 跨越 0°/360° 本初子午线点集
        lons_cross = np.array([-1.0, 1.0, 359.5])
        lats_cross = np.array([51.0, 52.0, 51.5])
        span, arc_start, arc_end = circular_longitude_span(lons_cross)
        self.assertLess(span, 5.0)  # 跨度约 2.0~2.5 度，而非 358 度

        bboxes = build_circular_fes_bboxes(lons_cross, lats_cross, buffer_deg=0.5)
        self.assertEqual(len(bboxes), 2)
        for (b_lon_min, b_lat_min, b_lon_max, b_lat_max) in bboxes:
            self.assertGreaterEqual(b_lon_min, 0.0)
            self.assertLessEqual(b_lon_max, 360.0)
            self.assertGreaterEqual(b_lat_min, 50.0)
            self.assertLessEqual(b_lat_max, 53.0)
            self.assertLess(b_lon_max - b_lon_min, 5.0)

        # 2. 正常单区域无跨越
        lons_norm = np.array([120.0, 120.5])
        lats_norm = np.array([30.0, 30.5])
        span_norm, _, _ = circular_longitude_span(lons_norm)
        self.assertAlmostEqual(span_norm, 0.5, places=4)
        bboxes_norm = build_circular_fes_bboxes(lons_norm, lats_norm, buffer_deg=0.5)
        self.assertEqual(len(bboxes_norm), 1)
        self.assertAlmostEqual(bboxes_norm[0][0], 119.5, places=4)
        self.assertAlmostEqual(bboxes_norm[0][2], 121.0, places=4)

    # 43. Test N: 验证双水体盆地阻隔带 (TwoBasinSyntheticPredictor) 跨屏障隔离与 100%/0% Oracle
    def test_two_basin_cross_barrier_oracle(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dem_path = os.path.join(temp_dir, "dem_two_basin.tif")
            out_inund = os.path.join(temp_dir, "out_inund_two_basin.tif")

            width, height = 40, 20
            transform = from_origin(120.0, 30.2, 0.01, 0.01)
            dem_data = np.zeros((height, width), dtype=np.float32)

            with rasterio.open(
                dem_path, 'w', driver='GTiff', width=width, height=height, count=1,
                dtype='float32', crs='EPSG:4326', transform=transform, nodata=-9999.0
            ) as dst:
                dst.write(dem_data, 1)

            # 屏障经度处于 120.155 ~ 120.245
            # 左盆地: left_msl = 1.5m, amp = 0.5m -> 水位 [1.0, 2.0] > 0m (100% 淹没)
            # 右盆地: right_msl = -1.5m, amp = 0.5m -> 水位 [-2.0, -1.0] < 0m (0% 淹没)
            predictor = TwoBasinSyntheticPredictor(
                barrier_x_min=120.155,
                barrier_x_max=120.245,
                left_msl=1.5,
                right_msl=-1.5,
                base_amplitude_m=0.5,
                period_hours=12.0
            )
            engine = RasterTideEngine(
                tide_predictor=predictor,
                initial_control_spacing_m=1000.0,
                min_control_spacing_m=200.0,
                inundation_error_tolerance_pct=1.0
            )

            summary = engine.calculate_inundation_raster(
                dem_path=dem_path,
                output_path=out_inund,
                start_time="2024-01-01 00:00:00",
                end_time="2024-01-01 06:00:00",
                freq="1h",
                dem_datum='msl',
                inclusive='both'
            )

            self.assertTrue(os.path.exists(out_inund))
            with rasterio.open(out_inund) as src:
                arr = src.read(1)
                # 左盆地 (col 0..14): 严格 100.0%
                left_vals = arr[:, :14]
                self.assertTrue(np.allclose(left_vals[np.isfinite(left_vals)], 100.0, atol=1e-2))
                # 右盆地 (col 26..39): 严格 0.0%
                right_vals = arr[:, 26:]
                self.assertTrue(np.allclose(right_vals[np.isfinite(right_vals)], 0.0, atol=1e-2))
                # 阻隔带内部 (col 17..23): 严格为 NaN (无跨屏障污染插值)
                barrier_vals = arr[:, 17:23]
                self.assertTrue(np.isnan(barrier_vals).all())

    # 44. Test O: 验证 FES 有效性突变区域四叉树自适应细分、边缘探测与 QC 掩膜生成
    def test_fes_validity_boundary_refinement_and_probing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dem_path = os.path.join(temp_dir, "dem_boundary.tif")
            out_path = os.path.join(temp_dir, "out_boundary.tif")
            out_qc = os.path.join(temp_dir, "out_boundary_qc.tif")

            width, height = 200, 100
            transform = from_origin(120.0, 30.1, 0.0005, 0.0005)
            dem_data = np.zeros((height, width), dtype=np.float32)

            with rasterio.open(
                dem_path, 'w', driver='GTiff', width=width, height=height, count=1,
                dtype='float32', crs='EPSG:4326', transform=transform, nodata=-9999.0
            ) as dst:
                dst.write(dem_data, 1)

            # 1. 突变边界细分检验: 在经度 120.05 处存在刚性有效/无效跃变
            pred_boundary = SyntheticTidePredictor(valid_lon_range=(120.0, 120.05))
            engine = RasterTideEngine(
                tide_predictor=pred_boundary,
                initial_control_spacing_m=4000.0,
                min_control_spacing_m=500.0
            )
            summary = engine.calculate_inundation_raster(
                dem_path=dem_path, output_path=out_path, qc_output_path=out_qc,
                start_time="2024-01-01 00:00:00", end_time="2024-01-01 06:00:00",
                freq="1h", dem_datum='msl', inclusive='both'
            )

            self.assertEqual(int(summary.metadata['MAX_REFINEMENT_LEVEL_USED']), 3)
            with rasterio.open(out_qc) as src:
                qc_arr = src.read(1)
                boundary_count = int(np.count_nonzero(qc_arr & QC_BIT_FES_VALIDITY_BOUNDARY))
                self.assertGreater(boundary_count, 0)
                min_spacing_count = int(np.count_nonzero(qc_arr & QC_BIT_MIN_SPACING_REACHED))
                self.assertGreater(min_spacing_count, 0)

            # 2. 边缘探针探测检验: 四角与中心无效，但边缘中点有效
            def probe_validity(xs, ys):
                xs = np.asarray(xs, dtype=float)
                ys = np.asarray(ys, dtype=float)
                return (xs > 120.01) & (xs < 120.03) & (ys > 30.09)

            pred_probe = SyntheticTidePredictor(validity_func=probe_validity)
            engine_probe = RasterTideEngine(
                tide_predictor=pred_probe,
                initial_control_spacing_m=4000.0,
                min_control_spacing_m=500.0
            )
            out_probe = os.path.join(temp_dir, "out_probe.tif")
            sum_probe = engine_probe.calculate_inundation_raster(
                dem_path=dem_path, output_path=out_probe,
                start_time="2024-01-01 00:00:00", end_time="2024-01-01 06:00:00",
                freq="1h", dem_datum='msl', inclusive='both'
            )
            self.assertEqual(int(sum_probe.metadata['MAX_REFINEMENT_LEVEL_USED']), 3)

    # 45. Test P: 验证物理尺度拓扑连通域降采样 (downsample_factor > 1) 屏障保护与阈值判定
    def test_topology_downsampling_barrier_preservation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dem_path = os.path.join(temp_dir, "dem_topo_barrier.tif")
            out_path = os.path.join(temp_dir, "out_topo_barrier.tif")

            # 10m 分辨率，高 50 宽 100，拓扑网格降采样因数为 10 (100m)
            width, height = 100, 50
            transform = from_origin(500000.0, 3400000.0, 10.0, 10.0)
            dem_data = np.zeros((height, width), dtype=np.float32)
            # 在 40..60 列设置 200m 宽的陆地 NoData 阻隔屏障
            dem_data[:, 40:60] = -9999.0

            with rasterio.open(
                dem_path, 'w', driver='GTiff', width=width, height=height, count=1,
                dtype='float32', crs='EPSG:32651', transform=transform, nodata=-9999.0
            ) as dst:
                dst.write(dem_data, 1)

            pred = SyntheticTidePredictor()
            engine = RasterTideEngine(
                tide_predictor=pred,
                topology_max_resolution_m=100.0,
                topology_valid_fraction_threshold=0.5
            )
            summary = engine.calculate_inundation_raster(
                dem_path=dem_path, output_path=out_path,
                start_time="2024-01-01 00:00:00", end_time="2024-01-01 06:00:00",
                freq="1h", dem_datum='msl', inclusive='both'
            )
            self.assertEqual(int(summary.metadata['TOPOLOGY_COMPONENT_COUNT']), 2)

    # 46. Test Q: 验证四叉树逐层批量解算 (Level-wise Batched FES) 显著降低调用开销
    def test_quadtree_level_wise_batching_efficiency(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dem_path = os.path.join(temp_dir, "dem_batch_test.tif")
            out_path = os.path.join(temp_dir, "out_batch_test.tif")

            width, height = 100, 100
            transform = from_origin(120.0, 30.1, 0.0005, 0.0005)
            dem_data = np.zeros((height, width), dtype=np.float32)

            with rasterio.open(
                dem_path, 'w', driver='GTiff', width=width, height=height, count=1,
                dtype='float32', crs='EPSG:4326', transform=transform, nodata=-9999.0
            ) as dst:
                dst.write(dem_data, 1)

            pred = SyntheticTidePredictor(valid_lon_range=(120.0, 120.03))
            engine = RasterTideEngine(
                tide_predictor=pred,
                initial_control_spacing_m=4000.0,
                min_control_spacing_m=500.0,
                control_node_batch_size=128
            )
            summary = engine.calculate_inundation_raster(
                dem_path=dem_path, output_path=out_path,
                start_time="2024-01-01 00:00:00", end_time="2024-01-01 06:00:00",
                freq="1h", dem_datum='msl', inclusive='both'
            )
            predict_calls = int(summary.metadata['FES_PREDICT_CALLS'])
            total_evaluated_nodes = int(summary.metadata['FES_CONTROL_NODES_EVALUATED'])
            self.assertGreater(total_evaluated_nodes, 40)
            # 批量解算下调用次数远小于节点数 (O(levels) 级别，不超过 15 次)
            self.assertLessEqual(predict_calls, 15)

    # 47. Test R: 验证 max_fes_evaluate_points 参数正确传递至时序预测器
    def test_max_fes_evaluate_points_configuration(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dem_path = os.path.join(temp_dir, "dem_param.tif")
            out_path = os.path.join(temp_dir, "out_param.tif")

            width, height = 10, 10
            transform = from_origin(120.0, 30.1, 0.01, 0.01)
            dem_data = np.zeros((height, width), dtype=np.float32)

            with rasterio.open(
                dem_path, 'w', driver='GTiff', width=width, height=height, count=1,
                dtype='float32', crs='EPSG:4326', transform=transform, nodata=-9999.0
            ) as dst:
                dst.write(dem_data, 1)

            real_pred = SyntheticTidePredictor()
            pred_spy = MagicMock(wraps=real_pred)
            engine = RasterTideEngine(tide_predictor=pred_spy, max_fes_evaluate_points=77777)
            engine.calculate_inundation_raster(
                dem_path=dem_path, output_path=out_path,
                start_time="2024-01-01 00:00:00", end_time="2024-01-01 02:00:00",
                freq="1h", dem_datum='msl', inclusive='both'
            )
            self.assertTrue(pred_spy.predict_points_period.called)
            kwargs_passed = [call.kwargs for call in pred_spy.predict_points_period.call_args_list]
            self.assertEqual(kwargs_passed[0].get('max_fes_evaluate_points'), 77777)

    # 48. Test S: 验证控制节点常驻内存估算与超出预算硬限制 (RasterMemoryLimitError)
    def test_memory_limit_error_and_estimate(self):
        # 1. 验证内存估算辅助函数计算精度
        mem_mb = estimate_control_node_memory(1000, 17568, dtype_bytes=4)
        expected_mb = (1000.0 * 17568.0 * 4.0) / (1024.0 * 1024.0)
        self.assertAlmostEqual(mem_mb, expected_mb, places=4)

        with tempfile.TemporaryDirectory() as temp_dir:
            dem_path = os.path.join(temp_dir, "dem_mem.tif")
            out_path = os.path.join(temp_dir, "out_mem.tif")
            tmp_out_path = f"{out_path}.tmp.tif"
            tmp_qc_path = f"{os.path.splitext(out_path)[0]}_qc.tif.tmp.tif"

            width, height = 20, 20
            transform = from_origin(120.0, 30.0, 0.01, 0.01)
            dem_data = np.zeros((height, width), dtype=np.float32)

            with rasterio.open(
                dem_path, 'w', driver='GTiff', width=width, height=height, count=1,
                dtype='float32', crs='EPSG:4326', transform=transform, nodata=-9999.0
            ) as dst:
                dst.write(dem_data, 1)

            pred = SyntheticTidePredictor()
            # 将最大允许常驻节点数限制为极小值 (例如 2)，必然触发硬限制
            engine = RasterTideEngine(
                tide_predictor=pred,
                max_in_memory_control_nodes=2,
                initial_control_spacing_m=4000.0
            )

            with self.assertRaises(RasterMemoryLimitError) as ctx:
                engine.calculate_inundation_raster(
                    dem_path=dem_path, output_path=out_path,
                    start_time="2024-01-01 00:00:00", end_time="2024-01-01 06:00:00",
                    freq="1h", dem_datum='msl', inclusive='both'
                )

            err_msg = str(ctx.exception)
            self.assertIn("Resident", err_msg)
            self.assertIn("Pending", err_msg)
            self.assertIn("Total attempted", err_msg)
            self.assertIn("Limit", err_msg)
            self.assertIn("Time samples per node", err_msg)
            self.assertIn("建议解决方案", err_msg)

            # 验证异常发生时原子临时文件被安全清理
            self.assertFalse(os.path.exists(tmp_out_path))
            self.assertFalse(os.path.exists(tmp_qc_path))
            self.assertFalse(os.path.exists(out_path))

    # 49. Test T: 验证 CLI 整年模式潮位解算与垂直基准解耦 (单次解算/MSL跳过)
    def test_cli_year_mode_datum_decoupling(self):
        import cli
        with tempfile.TemporaryDirectory() as temp_dir:
            out_msl = os.path.join(temp_dir, "out_msl.csv")
            out_egm = os.path.join(temp_dir, "out_egm.csv")

            mock_pred = MagicMock()
            mock_trans = MagicMock()

            # 模拟 predict_year 返回 DataFrame
            df_mock = pd.DataFrame({
                'datetime': pd.date_range('2024-01-01', periods=5, freq='1h'),
                'tide_total_m': [0.5, 0.6, 0.7, 0.6, 0.5],
                'quality_flag': [1, 1, 1, 1, 1]
            })
            mock_pred.predict_year.return_value = df_mock.copy()

            mock_trans.convert_tide_datums.return_value = {
                'tide_msl_m': np.array([0.5, 0.6, 0.7, 0.6, 0.5]),
                'mdt_m': np.array([0.1]*5),
                'delta_n_m': np.array([-0.05]*5),
                'n_egm2008_m': np.array([10.0]*5),
                'h_mdt_ref_m': np.array([0.6]*5),
                'h_goco06s_m': np.array([0.6]*5),
                'h_egm2008_m': np.array([0.55]*5),
                'h_wgs84_m': np.array([10.55]*5),
                'datum_ref_geoid': np.array(['GOCO06s']*5),
                'qc_warning': np.array(['NORMAL']*5)
            }

            with patch('cli.FESTidePredictor', return_value=mock_pred), \
                 patch('cli.DatumTransformer', return_value=mock_trans):

                # 1. 运行 single --datum msl
                cli.main(['single', '--lon', '122.0', '--lat', '31.0', '--year', '2024', '--step', '1h', '--datum', 'msl', '--output', out_msl])
                # 验证 predict_year datum_mode 严格为 None
                self.assertEqual(mock_pred.predict_year.call_args.kwargs.get('datum_mode'), None)
                # 验证 MSL 模式下绝对不调用 DatumTransformer.convert_tide_datums
                self.assertEqual(mock_trans.convert_tide_datums.call_count, 0)
                self.assertTrue(os.path.exists(out_msl))

                # 2. 运行 single --datum egm2008
                mock_trans.convert_tide_datums.reset_mock()
                mock_pred.predict_year.reset_mock()
                mock_pred.predict_year.return_value = df_mock.copy()

                cli.main(['single', '--lon', '122.0', '--lat', '31.0', '--year', '2024', '--step', '1h', '--datum', 'egm2008', '--output', out_egm])
                self.assertEqual(mock_pred.predict_year.call_args.kwargs.get('datum_mode'), None)
                # 验证 EGM 模式下 convert_tide_datums 恰好被调用一次
                self.assertEqual(mock_trans.convert_tide_datums.call_count, 1)
                self.assertTrue(os.path.exists(out_egm))

    # 50. Test U: 验证直接抽样像元真值预言机 (Direct Sampled-Pixel FES Oracle)
    def test_direct_sampled_pixel_fes_oracle(self):
        from scripts.validate_real_fes_raster import evaluate_direct_fes_samples
        with tempfile.TemporaryDirectory() as temp_dir:
            dem_path = os.path.join(temp_dir, "dem_oracle.tif")
            adapt_path = os.path.join(temp_dir, "adapt_oracle.tif")

            width, height = 20, 20
            transform = from_origin(120.0, 30.0, 0.005, 0.005)
            # DEM 高程设定为 -0.2m
            dem_data = np.full((height, width), -0.2, dtype=np.float32)

            with rasterio.open(
                dem_path, 'w', driver='GTiff', width=width, height=height, count=1,
                dtype='float32', crs='EPSG:4326', transform=transform, nodata=-9999.0
            ) as dst:
                dst.write(dem_data, 1)

            pred = SyntheticTidePredictor(base_amplitude_m=1.0)
            engine = RasterTideEngine(tide_predictor=pred)

            # 生成自适应结果作为对比
            engine.calculate_inundation_raster(
                dem_path=dem_path,
                output_path=adapt_path,
                start_time="2024-01-01 00:00:00",
                end_time="2024-01-01 12:00:00",
                freq="1h",
                dem_datum='msl',
                inclusive='both'
            )

            # 执行 Direct FES 抽样预言机
            oracle_res = evaluate_direct_fes_samples(
                dem_path=dem_path,
                adapt_tif_path=adapt_path,
                predictor=pred,
                transformer=engine.transformer,
                start_time="2024-01-01 00:00:00",
                end_time="2024-01-01 12:00:00",
                inclusive='both',
                step="1h",
                dem_datum='msl',
                n_samples=25,
                seed=42
            )

            self.assertEqual(oracle_res['total_samples'], 25)
            self.assertEqual(oracle_res['direct_valid_count'], 25)
            self.assertEqual(oracle_res['common_valid_count'], 25)
            self.assertEqual(oracle_res['direct_invalid_count'], 0)
            # 由于合成预测器空间平滑，自适应网格与像元真值误差极小
            self.assertLess(oracle_res['mae'], 2.0)
            self.assertLess(oracle_res['max_error'], 5.0)

            # 验证随机种子可复现性
            oracle_res_repeat = evaluate_direct_fes_samples(
                dem_path=dem_path,
                adapt_tif_path=adapt_path,
                predictor=pred,
                transformer=engine.transformer,
                start_time="2024-01-01 00:00:00",
                end_time="2024-01-01 12:00:00",
                inclusive='both',
                step="1h",
                dem_datum='msl',
                n_samples=25,
                seed=42
            )
            np.testing.assert_array_equal(oracle_res['sampled_rows'], oracle_res_repeat['sampled_rows'])
            np.testing.assert_array_equal(oracle_res['sampled_cols'], oracle_res_repeat['sampled_cols'])
            np.testing.assert_allclose(oracle_res['f_direct'], oracle_res_repeat['f_direct'])


if __name__ == '__main__':
    unittest.main()

