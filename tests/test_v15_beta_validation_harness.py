"""
CoastTideX v1.5 Beta — Real-FES Validation Harness Automated Unit Tests
测试 scripts/validate_v15_beta_real_fes.py 中的核心评估与度量函数：
    1. 环境采集与数据源预检逻辑 (collect_environment, check_data_sources)
    2. 栅格盘点与目标几何分类器 (scan_raster_inventory, analyze_target_mask_geometry, select_validation_tiles)
    3. 代表性近岸地形合成器 (create_synthetic_coastal_dem)
    4. Direct Sampled-Pixel Oracle 抽样比对与门禁判定 (evaluate_direct_oracle)
    5. Cache 与 Direct Raster 序列化精度比对门禁 (compare_cache_vs_direct_raster)
    6. 瓦片切缝连续性与重合检验 (run_seam_validation)
    7. 断点续跑与错参数防篡改测试逻辑 (run_resume_and_incompatibility_tests)
"""

import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import numpy as np
import rasterio
from rasterio.transform import from_origin

# 确保项目根目录可被导入
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from scripts.validate_v15_beta_real_fes import (
    collect_environment,
    check_data_sources,
    scan_raster_inventory,
    analyze_target_mask_geometry,
    select_validation_tiles,
    create_synthetic_coastal_dem,
    evaluate_direct_oracle,
    compare_cache_vs_direct_raster,
    run_seam_validation,
    run_resume_and_incompatibility_tests
)


class TestV15BetaValidationHarness(unittest.TestCase):
    """v1.5 Beta 真实验证工具链自动化单元测试套件"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="coasttidex_beta_test_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_test_geotiff(
        self,
        rel_path: str,
        data: np.ndarray,
        nodata: float = -9999.0,
        crs: str = "EPSG:4326",
        res: float = 0.001
    ) -> str:
        """辅助函数：创建测试 GeoTIFF 文件"""
        abs_path = os.path.join(self.test_dir, rel_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        h, w = data.shape
        transform = from_origin(120.0, 30.0, res, res)
        profile = {
            'driver': 'GTiff',
            'height': h,
            'width': w,
            'count': 1,
            'dtype': str(data.dtype),
            'crs': crs,
            'transform': transform,
            'nodata': nodata
        }
        with rasterio.open(abs_path, 'w', **profile) as dst:
            dst.write(data, 1)
        return abs_path

    # ------------------------------------------------------------------
    # 1. 环境采集与数据源预检
    # ------------------------------------------------------------------
    def test_collect_environment(self):
        """测试环境采集返回包含平台、Python版本、Git信息的字典"""
        env = collect_environment(PROJECT_ROOT)
        self.assertIsInstance(env, dict)
        self.assertIn("platform", env)
        self.assertIn("python_version", env)
        self.assertIn("git_commit", env)
        self.assertIn("git_branch", env)
        self.assertIn("numpy_version", env)

    def test_check_data_sources_mock(self):
        """测试数据源状态检测逻辑"""
        fake_cfg = {
            "paths": {
                "fes_ns_grid": os.path.join(self.test_dir, "fake_fes.nc"),
                "delta_n_eigen6c4_egm2008_tif": os.path.join(self.test_dir, "fake_eigen.tif")
            }
        }
        # 文件不存在时
        ds = check_data_sources(fake_cfg)
        self.assertFalse(ds["fes_ns_grid"]["exists"])
        self.assertFalse(ds["EIGEN_DELTA_N_AVAILABLE"])

        # 创建假文件
        with open(fake_cfg["paths"]["delta_n_eigen6c4_egm2008_tif"], "w") as f:
            f.write("fake")
        ds2 = check_data_sources(fake_cfg)
        self.assertTrue(ds2["delta_n_eigen6c4_egm2008_tif"]["exists"])
        self.assertTrue(ds2["EIGEN_DELTA_N_AVAILABLE"])

    # ------------------------------------------------------------------
    # 2. 栅格盘点与目标几何分类器
    # ------------------------------------------------------------------
    def test_scan_raster_inventory_not_found(self):
        """测试不存在的目录能安全捕获 REAL_VALIDATION_INPUT_DIR_NOT_FOUND"""
        inv, summary = scan_raster_inventory(os.path.join(self.test_dir, "non_existent"))
        self.assertEqual(inv, [])
        self.assertEqual(summary["status"], "REAL_VALIDATION_INPUT_DIR_NOT_FOUND")

    def test_target_mask_geometry_classification(self):
        """测试不同几何形态的目标掩膜分类（空、细长条带、边界、大面积等）"""
        # 1. EMPTY_TARGET: 全部为 nodata
        arr_empty = np.full((50, 50), -9999.0, dtype=np.float32)
        p_empty = self._create_test_geotiff("empty.tif", arr_empty)
        geom_empty = analyze_target_mask_geometry(p_empty)
        self.assertEqual(geom_empty["selection_type"], "EMPTY_TARGET")
        self.assertEqual(geom_empty["valid_target_pixels"], 0)

        # 2. NARROW_STRIP: 纵横比 > 4.0
        arr_narrow = np.full((20, 100), -9999.0, dtype=np.float32)
        arr_narrow[5:8, 10:90] = 1.0
        p_narrow = self._create_test_geotiff("narrow.tif", arr_narrow)
        geom_narrow = analyze_target_mask_geometry(p_narrow)
        self.assertEqual(geom_narrow["selection_type"], "NARROW_STRIP")
        self.assertGreater(geom_narrow["aspect_ratio"], 4.0)

        # 3. EDGE_TARGET: 紧贴栅格边缘像元占比高
        arr_edge = np.full((50, 50), -9999.0, dtype=np.float32)
        arr_edge[0:10, 0:10] = 2.0
        p_edge = self._create_test_geotiff("edge.tif", arr_edge)
        geom_edge = analyze_target_mask_geometry(p_edge)
        self.assertEqual(geom_edge["selection_type"], "EDGE_TARGET")

        # 4. WIDE_TARGET: 覆盖比例 > 40%
        arr_wide = np.full((50, 50), 3.0, dtype=np.float32)
        p_wide = self._create_test_geotiff("wide.tif", arr_wide)
        geom_wide = analyze_target_mask_geometry(p_wide)
        self.assertEqual(geom_wide["selection_type"], "WIDE_TARGET")
        self.assertGreater(geom_wide["valid_fraction"], 0.4)

    def test_select_validation_tiles(self):
        """测试自动挑选代表性瓦片逻辑"""
        # 创建几类测试图
        arr_empty = np.full((30, 30), -9999.0, dtype=np.float32)
        p1 = self._create_test_geotiff("t1.tif", arr_empty)

        arr_wide = np.full((30, 30), 2.0, dtype=np.float32)
        p2 = self._create_test_geotiff("t2.tif", arr_wide)

        inv, _ = scan_raster_inventory(self.test_dir)
        selected = select_validation_tiles(inv, max_tiles=3)
        # EMPTY_TARGET 应该被跳过或排在最后，有效 tile 会被选中
        self.assertTrue(any(s["selection_type"] == "WIDE_TARGET" for s in selected))

    # ------------------------------------------------------------------
    # 3. 代表性近岸地形合成器
    # ------------------------------------------------------------------
    def test_create_synthetic_coastal_dem(self):
        """测试合成代表性近岸 DEM 产物规范与数值有效性"""
        syn_p = os.path.join(self.test_dir, "synthetic_coast.tif")
        out_p = create_synthetic_coastal_dem(syn_p, center_lon=122.0, center_lat=31.0, width=64, height=64)
        self.assertTrue(os.path.exists(out_p))

        with rasterio.open(out_p) as src:
            self.assertEqual(src.width, 64)
            self.assertEqual(src.height, 64)
            self.assertEqual(src.crs.to_string(), "EPSG:4326")
            data = src.read(1)
            nodata = src.nodata
            valid_mask = np.isfinite(data) & (data != nodata)
            self.assertGreater(np.count_nonzero(valid_mask), 100)
            # 检验坡度与微地形起伏合理性
            self.assertLess(np.min(data[valid_mask]), 5.0)
            self.assertGreater(np.max(data[valid_mask]), -5.0)

    # ------------------------------------------------------------------
    # 4. Direct Sampled-Pixel Oracle 抽样比对与门禁判定
    # ------------------------------------------------------------------
    def test_evaluate_direct_oracle_metrics_and_gates(self):
        """测试 Direct Oracle 抽样对比、误差指标与门禁状态判定"""
        # 创建 DEM 和对应的淹没频率模拟结果图
        h, w = 30, 30
        dem_arr = np.full((h, w), 0.5, dtype=np.float32)
        dem_p = self._create_test_geotiff("oracle_dem.tif", dem_arr)

        adapt_arr = np.full((h, w), 45.0, dtype=np.float32)
        adapt_p = self._create_test_geotiff("oracle_adapt.tif", adapt_arr)

        # 模拟 Predictor，返回固定的潮位序列
        mock_predictor = MagicMock()
        # 100 个点，每个点 96 个时间步，全部为 1.0m (均大于 0.5m，频率应为 100%)
        mock_tides = np.full((50, 96), 1.0, dtype=np.float32)
        mock_predictor.predict_points_period.return_value = (mock_tides, None, None)

        res = evaluate_direct_oracle(
            dem_path=dem_p,
            adapt_tif_path=adapt_p,
            predictor=mock_predictor,
            n_samples=50,
            seed=42
        )

        self.assertEqual(res["direct_requested"], 50)
        self.assertEqual(res["direct_valid"], 50)
        self.assertEqual(res["common_valid"], 50)
        # direct_freq 是 100%，adapt_freq 是 45%，误差约为 -55 pp
        self.assertAlmostEqual(res["metrics"]["mae"], 55.0, delta=1.0)
        self.assertEqual(res["gate_status"], "INVESTIGATE / FAIL")
        self.assertGreater(len(res["top_outliers"]), 0)

        # 模拟高精度一致情况 (Direct 频率接近 Adapt 频率)
        adapt_arr_accurate = np.full((h, w), 100.0, dtype=np.float32)
        adapt_p_acc = self._create_test_geotiff("oracle_adapt_acc.tif", adapt_arr_accurate)
        res_acc = evaluate_direct_oracle(
            dem_path=dem_p,
            adapt_tif_path=adapt_p_acc,
            predictor=mock_predictor,
            n_samples=50,
            seed=42
        )
        self.assertAlmostEqual(res_acc["metrics"]["mae"], 0.0, delta=1e-3)
        self.assertEqual(res_acc["gate_status"], "STRONG PASS")

    # ------------------------------------------------------------------
    # 5. Cache 与 Direct Raster 序列化比对门禁
    # ------------------------------------------------------------------
    def test_compare_cache_vs_direct_raster(self):
        """测试单步 Direct 与 Stage 1->Stage 2 产物像元一致性门禁"""
        h, w = 20, 20
        arr1 = np.full((h, w), 50.0, dtype=np.float32)
        p1 = self._create_test_geotiff("raster_direct.tif", arr1)

        # 像元完全一致
        arr2 = np.full((h, w), 50.0, dtype=np.float32)
        p2 = self._create_test_geotiff("raster_stage2.tif", arr2)

        res_pass = compare_cache_vs_direct_raster(p1, p2)
        self.assertTrue(res_pass["pass_gate"])
        self.assertEqual(res_pass["status"], "PASS")
        self.assertEqual(res_pass["max_diff_pp"], 0.0)

        # 出现显著偏差 (> 1e-3 pp)
        arr3 = np.full((h, w), 50.05, dtype=np.float32)
        p3 = self._create_test_geotiff("raster_stage2_diverge.tif", arr3)
        res_fail = compare_cache_vs_direct_raster(p1, p3)
        self.assertFalse(res_fail["pass_gate"])
        self.assertEqual(res_fail["status"], "INVESTIGATE")

    # ------------------------------------------------------------------
    # 6. 瓦片切缝连续性检验
    # ------------------------------------------------------------------
    def test_run_seam_validation(self):
        """测试切缝拼合检验算法与接缝阶跃计算"""
        # 创建一个小尺寸 DEM
        dem_data = np.full((32, 32), 1.0, dtype=np.float32)
        dem_path = self._create_test_geotiff("seam_whole.tif", dem_data)
        out_dir = os.path.join(self.test_dir, "seam_out")

        # Mock RasterTideEngine.calculate_inundation_raster 生成恒定结果
        with patch("scripts.validate_v15_beta_real_fes.RasterTideEngine") as MockEngine:
            instance = MockEngine.return_value

            def fake_calc(dem_path, output_path, **kwargs):
                with rasterio.open(dem_path) as s:
                    h, w = s.height, s.width
                    prof = s.profile.copy()
                prof.update({'dtype': 'float32'})
                with rasterio.open(output_path, 'w', **prof) as dst:
                    dst.write(np.full((h, w), 30.0, dtype=np.float32), 1)

            instance.calculate_inundation_raster.side_effect = fake_calc

            seam_res = run_seam_validation(
                whole_dem_path=dem_path,
                out_dir=out_dir,
                engine_kwargs={"start_time": "2024-01-01 00:00", "end_time": "2024-01-02 00:00"}
            )

            self.assertTrue(seam_res["pass_gate"])
            self.assertEqual(seam_res["composite_max_pp"], 0.0)
            self.assertEqual(seam_res["seam_step_p95_pp"], 0.0)
            self.assertEqual(seam_res["status"], "PASS")

    # ------------------------------------------------------------------
    # 7. 断点恢复与防篡改测试逻辑
    # ------------------------------------------------------------------
    def test_run_resume_and_incompatibility_mock(self):
        """测试 Resume 与参数保护测试框架的数据流与校验"""
        dem_data = np.full((16, 16), 1.0, dtype=np.float32)
        dem_path = self._create_test_geotiff("resume_dem.tif", dem_data)
        work_dir = os.path.join(self.test_dir, "resume_work")

        with patch("scripts.validate_v15_beta_real_fes.BatchRasterEngine") as MockBatchEngine:
            batch_inst = MockBatchEngine.return_value

            # 模拟批处理运行
            def fake_run(input_folder, output_folder, existing_policy, **kwargs):
                # 写入假 cache 文件
                cache_file = os.path.join(output_folder, "resume_dem_tide.nc")
                with open(cache_file, "w") as f:
                    f.write("mock_netcdf_cache_content")

                if existing_policy.value == "resume":
                    # 如果参数与第一次相同则跳过，否则不跳过
                    if kwargs.get("freq") == "1h":
                        return {"skipped": 0, "processed": 1}
                    return {"skipped": 1, "processed": 0}
                return {"skipped": 0, "processed": 1}

            batch_inst.run_batch.side_effect = fake_run

            res = run_resume_and_incompatibility_tests(
                dem_path=dem_path,
                work_dir=work_dir,
                base_kwargs={"start_time": "2024-01-01 00:00", "end_time": "2024-01-02 00:00", "freq": "30min"}
            )

            self.assertTrue(res["resume_zero_work"])
            self.assertTrue(res["cache_sha256_identical"])
            self.assertTrue(res["incompatible_parameter_protected"])
            self.assertTrue(res["pass_all"])


if __name__ == "__main__":
    unittest.main()
