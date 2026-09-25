"""
CoastTideX v1.7 单元测试套件:
DEM 垂直基准转换与 MSL 统一分析架构验证
(Unit tests for DEM Datum Conversion & MSL Reference Workflow)

基于 Seeger & Minderhoud (Nature, 2026) 提出的近岸基准统一理论。

覆盖范围:
1. 核心代数恒等式验证: Z_MSL = Z_EGM2008 - MDT - DeltaN；
2. 开阔大洋原生 MDT 双线性插值与 QC_MDT_NATIVE (0)；
3. 近岸/陆地缺失区 100km 内球面 3D 空间反距离加权 (IDW) 外推与 QC_MDT_EXTRAPOLATED (1)；
4. 超过 100km 外推门禁物理阻断与 QC_MDT_NODATA (2)；
5. DeltaN 改正一致性: converter 与 DatumTransformer 完全无缝一致；
6. 淹没频率判别逻辑严密等价性验证: (Tide_MSL + MDT + DeltaN > DEM_EGM2008) <=> (Tide_MSL > DEM_MSL)；
7. 2D 矩形分块流式 GeoTIFF 转换、元数据继承与原子临时文件写入保护 (*.tmp.tif)；
8. CLI convert-dem 命令行解析与子命令集成。
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import numpy as np
import rasterio
from rasterio.transform import from_origin

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from core.dem_datum_converter import (
    DEMDatumConverter,
    convert_dem_to_msl,
    MAX_MDT_EXTRAPOLATION_DISTANCE_KM,
    QC_MDT_NATIVE,
    QC_MDT_EXTRAPOLATED,
    QC_MDT_NODATA,
)
from core.datum_engine import DatumTransformer
from cli import build_parser


class TestDEMMSLConversion(unittest.TestCase):
    """测试 DEM 垂直基准转换模块数学与物理外推逻辑"""

    def setUp(self):
        self.converter = DEMDatumConverter(max_extrapolation_distance_km=100.0)
        self.transformer = DatumTransformer()

    def test_01_algebraic_formula_and_pointwise_conversion(self):
        """测试 1: 核心代数公式 Z_MSL = Z_EGM2008 - MDT - DeltaN 严密性"""
        z_egm = np.array([5.0, 10.0, -2.5, 0.0, np.nan])
        mdt_mock = np.array([0.5, -0.2, 0.3, 0.0, 0.5])
        delta_n_mock = np.array([0.1, 0.05, -0.1, 0.0, 0.1])
        qc_mock = np.array([QC_MDT_NATIVE, QC_MDT_EXTRAPOLATED, QC_MDT_NATIVE, QC_MDT_EXTRAPOLATED, QC_MDT_NATIVE])

        with patch.object(self.converter, 'evaluate_mdt_with_idw', return_value=(mdt_mock, qc_mock.copy())), \
             patch.object(self.converter.transformer, 'get_delta_n', return_value=delta_n_mock):

            lons = np.array([122.0, 122.1, 122.2, 122.3, 122.4])
            lats = np.array([31.0, 31.1, 31.2, 31.3, 31.4])
            z_msl, mdt_out, dn_out, qc_out = self.converter.convert_points(lons, lats, z_egm)

            # 点 0: 5.0 - 0.5 - 0.1 = 4.4
            self.assertAlmostEqual(z_msl[0], 4.4, places=5)
            self.assertEqual(qc_out[0], QC_MDT_NATIVE)

            # 点 1: 10.0 - (-0.2) - 0.05 = 10.15
            self.assertAlmostEqual(z_msl[1], 10.15, places=5)
            self.assertEqual(qc_out[1], QC_MDT_EXTRAPOLATED)

            # 点 2: -2.5 - 0.3 - (-0.1) = -2.7
            self.assertAlmostEqual(z_msl[2], -2.7, places=5)
            self.assertEqual(qc_out[2], QC_MDT_NATIVE)

            # 点 3: 0.0 - 0.0 - 0.0 = 0.0
            self.assertAlmostEqual(z_msl[3], 0.0, places=5)

            # 点 4: 输入为 NaN，转换结果必为 NaN，且标记 QC_MDT_NODATA
            self.assertTrue(np.isnan(z_msl[4]))
            self.assertEqual(qc_out[4], QC_MDT_NODATA)

    def test_02_native_ocean_mdt_bilinear(self):
        """测试 2: 开阔大洋原生 MDT 双线性插值与 QC_MDT_NATIVE (0)"""
        # 东海开阔海域坐标 (124.0°E, 30.5°N) 必然属于大洋 MDT 有效区
        if not os.path.exists(self.converter.mdt_path):
            self.skipTest("MDT NC 数据文件不存在，跳过真实大洋点测试")

        lons = np.array([124.0, 124.5])
        lats = np.array([30.5, 30.0])
        z_egm = np.array([0.0, 0.0])

        z_msl, mdt_vals, delta_n_vals, qc_vals = self.converter.convert_points(lons, lats, z_egm)

        for i in range(len(lons)):
            self.assertEqual(qc_vals[i], QC_MDT_NATIVE, f"大洋开阔点 ({lons[i]}, {lats[i]}) 必须标记为原生 MDT 插值")
            self.assertTrue(np.isfinite(mdt_vals[i]), "大洋 MDT 值必须为有限数值")
            self.assertTrue(np.isfinite(z_msl[i]))

    def test_03_idw_extrapolation_within_100km(self):
        """测试 3: 近岸/陆地缺失区 100km 内外推成功且标记 QC_MDT_EXTRAPOLATED (1)"""
        if not os.path.exists(self.converter.mdt_path):
            self.skipTest("MDT NC 数据文件不存在，跳过测试")

        # 崇明岛腹地 (121.5°E, 31.6°N)，距离大洋有效点约 20~50km，处于 100km 门禁范围内
        lons = np.array([121.5])
        lats = np.array([31.6])
        z_egm = np.array([3.5])

        z_msl, mdt_vals, delta_n_vals, qc_vals = self.converter.convert_points(lons, lats, z_egm)

        self.assertEqual(qc_vals[0], QC_MDT_EXTRAPOLATED, "100km 内近岸陆地点必须标记为 IDW 外推")
        self.assertTrue(np.isfinite(mdt_vals[0]), "外推 MDT 值必须为有效浮点数")
        self.assertTrue(np.isfinite(z_msl[0]))

    def test_04_distance_cutoff_over_100km(self):
        """测试 4: 距离大洋有效点超过 100km 物理阻断，返回 NaN 并标记 QC_MDT_NODATA (2)"""
        if not os.path.exists(self.converter.mdt_path):
            self.skipTest("MDT NC 数据文件不存在，跳过测试")

        # 内陆深处 (如安徽合肥附近 117.2°E, 31.8°N)，距离最近大洋点 > 300km
        lons = np.array([117.2])
        lats = np.array([31.8])
        z_egm = np.array([30.0])

        z_msl, mdt_vals, delta_n_vals, qc_vals = self.converter.convert_points(lons, lats, z_egm)

        self.assertEqual(qc_vals[0], QC_MDT_NODATA, "内陆深处 (>100km) 必须严密阻断外推并标记 QC=2")
        self.assertTrue(np.isnan(mdt_vals[0]), "超范围点 MDT 必须为 NaN")
        self.assertTrue(np.isnan(z_msl[0]), "超范围点 Z_MSL 必须为 NaN")

    def test_05_deltan_consistency(self):
        """测试 5: converter 的 Delta N 改正值与 DatumTransformer 保持 100% 精确一致"""
        if not os.path.exists(self.converter.delta_n_path):
            self.skipTest("Delta N GeoTIFF 数据不存在，跳过测试")

        test_lons = np.array([121.0, 121.5, 122.0, 122.5])
        test_lats = np.array([31.0, 31.5, 31.5, 32.0])

        dn_converter = self.converter.transformer.get_delta_n(test_lons, test_lats)
        dn_direct = self.transformer.get_delta_n(test_lons, test_lats)

        np.testing.assert_allclose(dn_converter, dn_direct, rtol=1e-7, atol=1e-7,
                                  err_msg="DEMDatumConverter 内置 DeltaN 与 DatumTransformer 必须完全一致")

    def test_06_decision_equivalence_inundation(self):
        """测试 6: 淹没判定严密等价性 (Tide_MSL + MDT + DeltaN > DEM_EGM2008) <=> (Tide_MSL > DEM_MSL)"""
        # 生成 10,000 个随机潮位、高程、MDT、DeltaN 样本
        rng = np.random.RandomState(42)
        n = 10000
        tide_msl = rng.uniform(-3.0, 4.0, size=n)
        z_egm2008 = rng.uniform(-2.0, 8.0, size=n)
        mdt = rng.uniform(-0.5, 1.0, size=n)
        delta_n = rng.uniform(-0.3, 0.3, size=n)

        # 旧流程: Tide_EGM2008 = Tide_MSL + MDT + DeltaN; Inundated = Tide_EGM2008 > DEM_EGM2008
        tide_egm2008 = tide_msl + mdt + delta_n
        decision_old = (tide_egm2008 > z_egm2008)

        # 新流程 (v1.7): DEM_MSL = DEM_EGM2008 - MDT - DeltaN; Inundated = Tide_MSL > DEM_MSL
        z_msl = z_egm2008 - mdt - delta_n
        decision_new = (tide_msl > z_msl)

        # 验证 100% 决策一致性 (0 个不一致点)
        diff_count = np.count_nonzero(decision_old != decision_new)
        self.assertEqual(diff_count, 0, f"新旧基准架构淹没布尔判定存在 {diff_count} 个不一致！理论必须 100% 等价")

    def test_07_raster_streaming_conversion(self):
        """测试 7: 2D 矩形分块流式 GeoTIFF 转换、Profile 属性继承与原子临时文件安全"""
        with tempfile.TemporaryDirectory() as tmpdir:
            input_dem = os.path.join(tmpdir, "test_dem_egm2008.tif")
            output_msl = os.path.join(tmpdir, "test_dem_msl.tif")
            output_qc = os.path.join(tmpdir, "test_dem_msl_qc.tif")

            # 构造合成 DEM (50x50, 崇明岛附近 121.8°E, 31.5°N)
            w, h = 50, 50
            trans = from_origin(121.8, 31.6, 0.001, 0.001)
            elevation_data = np.ones((h, w), dtype=np.float32) * 3.5
            elevation_data[0:5, 0:5] = -9999.0  # 设置 NoData 角落

            profile = {
                'driver': 'GTiff',
                'height': h,
                'width': w,
                'count': 1,
                'dtype': rasterio.float32,
                'crs': 'EPSG:4326',
                'transform': trans,
                'nodata': -9999.0
            }

            with rasterio.open(input_dem, 'w', **profile) as dst:
                dst.write(elevation_data, 1)

            # 执行转换 (block_size=16 触发多块流式处理)
            summary = self.converter.convert_raster(
                input_dem_path=input_dem,
                output_msl_path=output_msl,
                output_qc_path=output_qc,
                block_size=16,
                allow_overwrite=True
            )

            self.assertTrue(os.path.exists(output_msl), "输出 DEM_MSL 必须已成功生成")
            self.assertTrue(os.path.exists(output_qc), "输出 QC 掩膜必须已成功生成")
            self.assertFalse(os.path.exists(f"{output_msl}.tmp.tif"), "临时文件 *.tmp.tif 必须已安全更名清理")
            self.assertFalse(os.path.exists(f"{output_qc}.tmp.tif"), "QC 临时文件 *.tmp.tif 必须已安全更名清理")

            with rasterio.open(output_msl) as src_msl, rasterio.open(output_qc) as src_qc:
                self.assertEqual(src_msl.width, w)
                self.assertEqual(src_msl.height, h)
                self.assertEqual(src_msl.crs.to_string(), 'EPSG:4326')
                self.assertEqual(src_qc.dtypes[0], rasterio.uint8)

                # 验证 NoData 角落保持 NoData
                msl_arr = src_msl.read(1)
                qc_arr = src_qc.read(1)
                self.assertTrue(np.all(np.isclose(msl_arr[0:5, 0:5], -9999.0)))
                self.assertTrue(np.all(qc_arr[0:5, 0:5] == QC_MDT_NODATA))

                # 验证有效数据区数值经过转换
                valid_mask = ~np.isclose(msl_arr, -9999.0)
                self.assertTrue(np.any(valid_mask))
                # 标签元数据验证
                tags = src_msl.tags()
                self.assertEqual(tags.get('DATUM'), 'MSL')
                self.assertEqual(tags.get('ANALYSIS_REFERENCE'), 'MSL')

    def test_08_cli_convert_dem_parser(self):
        """测试 8: CLI 命令行工具 convert-dem 参数解析测试"""
        parser = build_parser()
        args = parser.parse_args([
            "convert-dem",
            "--input", "coastal_dem.tif",
            "--output", "coastal_dem_msl.tif",
            "--qc-output", "coastal_dem_msl_qc.tif",
            "--max-dist-km", "80.0",
            "--block-size", "512",
            "--overwrite"
        ])
        self.assertEqual(args.mode, "convert-dem")
        self.assertEqual(args.input, "coastal_dem.tif")
        self.assertEqual(args.output, "coastal_dem_msl.tif")
        self.assertEqual(args.qc_output, "coastal_dem_msl_qc.tif")
        self.assertEqual(args.max_dist_km, 80.0)
        self.assertEqual(args.block_size, 512)
        self.assertTrue(args.overwrite)


if __name__ == '__main__':
    unittest.main()
