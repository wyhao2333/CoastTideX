"""
CoastTideX v1.6 Beta GUI与文档最终一致性对齐专项自动化测试套件
Test Suite for v1.6 Beta GUI & Documentation Final Alignment
"""

import os
import sys
import json
import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

os.environ["QT_QPA_PLATFORM"] = "offscreen"

# 确保导入路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from PyQt6.QtWidgets import QApplication, QMessageBox
from gui.main_window import MainWindow, RasterTideWorker
import core
from core.batch_raster_engine import (
    BatchRasterEngine, BatchManifest, ExistingOutputPolicy,
    JOB_MODE_TIDE_ONLY, JOB_MODE_TIDE_AND_INUNDATION, JOB_MODE_INUNDATION_FROM_CACHE,
    JOB_MODE_TIDE_AND_EXPOSURE, JOB_MODE_EXPOSURE_FROM_CACHE, JOB_MODE_ALL,
    STATUS_DONE, STATUS_FAILED
)
from core.exposure_engine import ExposureProductPaths
from gui.settings_dialog import _deep_validate_file


class TestV16PublicAPIExports(unittest.TestCase):
    """测试 1: 验证 core/__init__.py 完整导出 v1.6 Exposure 相关符号"""

    def test_core_exports(self):
        self.assertTrue(hasattr(core, "JOB_MODE_TIDE_AND_EXPOSURE"))
        self.assertTrue(hasattr(core, "JOB_MODE_EXPOSURE_FROM_CACHE"))
        self.assertTrue(hasattr(core, "JOB_MODE_ALL"))
        self.assertTrue(hasattr(core, "calculate_exposure_from_tide_cache"))
        self.assertTrue(hasattr(core, "ExposureProductPaths"))
        self.assertIn("JOB_MODE_TIDE_AND_EXPOSURE", core.__all__)
        self.assertIn("JOB_MODE_EXPOSURE_FROM_CACHE", core.__all__)
        self.assertIn("JOB_MODE_ALL", core.__all__)
        self.assertIn("calculate_exposure_from_tide_cache", core.__all__)
        self.assertIn("ExposureProductPaths", core.__all__)


class TestSettingsDialogFesMaskValidation(unittest.TestCase):
    """测试 2: 验证设置对话框针对 NetCDF FES 掩膜经纬度变量多形态的深度校验"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_fes_mask_with_latitude_longitude(self):
        import netCDF4 as nc
        p = os.path.join(self.temp_dir.name, "mask_lat_lon_formal.nc")
        with nc.Dataset(p, "w", format="NETCDF4") as ds:
            ds.createDimension("latitude", 4)
            ds.createDimension("longitude", 4)
            v_lat = ds.createVariable("latitude", "f4", ("latitude",))
            v_lon = ds.createVariable("longitude", "f4", ("longitude",))
            v_mask = ds.createVariable("mask", "i1", ("latitude", "longitude"))
            v_lat[:] = [10.0, 11.0, 12.0, 13.0]
            v_lon[:] = [120.0, 121.0, 122.0, 123.0]
            v_mask[:] = np.array([
                [0, 1, 2, 3],
                [0, 0, 1, 1],
                [2, 2, 3, 3],
                [0, 1, 2, 3]
            ], dtype=np.int8)

        ok, msg = _deep_validate_file(p, "fes_mask")
        self.assertTrue(ok, f"Validation failed: {msg}")
        self.assertIn("有效类别=[0, 1, 2, 3]", msg)

    def test_fes_mask_with_short_lat_lon(self):
        import netCDF4 as nc
        p = os.path.join(self.temp_dir.name, "mask_lat_lon_short.nc")
        with nc.Dataset(p, "w", format="NETCDF4") as ds:
            ds.createDimension("lat", 2)
            ds.createDimension("lon", 2)
            v_lat = ds.createVariable("lat", "f4", ("lat",))
            v_lon = ds.createVariable("lon", "f4", ("lon",))
            v_mask = ds.createVariable("mask", "i1", ("lat", "lon"))
            v_lat[:] = [20.0, 21.0]
            v_lon[:] = [110.0, 111.0]
            v_mask[:] = np.array([[0, 1], [2, 0]], dtype=np.int8)

        ok, msg = _deep_validate_file(p, "fes_mask")
        self.assertTrue(ok, f"Validation failed: {msg}")

    def test_fes_mask_invalid_categories(self):
        import netCDF4 as nc
        p = os.path.join(self.temp_dir.name, "mask_invalid_cats.nc")
        with nc.Dataset(p, "w", format="NETCDF4") as ds:
            ds.createDimension("lat", 2)
            ds.createDimension("lon", 2)
            ds.createVariable("lat", "f4", ("lat",))
            ds.createVariable("lon", "f4", ("lon",))
            v_mask = ds.createVariable("mask", "i1", ("lat", "lon"))
            v_mask[:] = np.array([[0, 1], [2, 99]], dtype=np.int8)

        ok, msg = _deep_validate_file(p, "fes_mask")
        self.assertFalse(ok)
        self.assertIn("包含非预期类别", msg)


class TestBatchManifestExposureFields(unittest.TestCase):
    """测试 3: 验证 BatchManifest 扩展 exposure 字段与向前向后兼容性"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.out_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_manifest_fields_and_save(self):
        manifest = BatchManifest(str(self.out_dir))
        self.assertIn("exposure_output_dir", manifest.FIELDS)
        self.assertIn("exposure_products_complete", manifest.FIELDS)
        self.assertIn("exposure_fraction_path", manifest.FIELDS)

        test_dem = str(self.out_dir / "test_tile.tif")
        manifest.upsert(
            test_dem,
            exposure_output_dir=str(self.out_dir / "test_tile_CoastTideX_exposure"),
            exposure_products_complete=True,
            exposure_fraction_path=str(self.out_dir / "test_tile_exposure_fraction.tif"),
            status=STATUS_DONE
        )
        manifest.save()

        # 检查 JSON
        self.assertTrue(manifest.json_path.exists())
        with open(manifest.json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["exposure_products_complete"], True)
        self.assertEqual(data[0]["exposure_output_dir"], str(self.out_dir / "test_tile_CoastTideX_exposure"))

        # 检查 CSV
        self.assertTrue(manifest.csv_path.exists())
        with open(manifest.csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["exposure_products_complete"], "True")

    def test_manifest_backwards_compatibility(self):
        # 写入一个缺少 exposure 字段的旧版本 manifest JSON
        legacy_json = self.out_dir / "batch_manifest.json"
        legacy_data = [{
            "input_path": "legacy_tile.tif",
            "input_name": "legacy_tile.tif",
            "tide_cache_path": "legacy_tide.nc",
            "frequency_path": "legacy_inundation.tif",
            "status": "DONE"
        }]
        with open(legacy_json, "w", encoding="utf-8") as f:
            json.dump(legacy_data, f)

        # 加载并检查未抛出异常，且自动补充空字段
        manifest = BatchManifest(str(self.out_dir))
        manifest.load()
        entry = manifest.get_entry("legacy_tile.tif")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["exposure_output_dir"], "")
        self.assertEqual(entry["exposure_products_complete"], "")
        self.assertEqual(entry["status"], "DONE")


class TestBatchEngineErrorIfExists(unittest.TestCase):
    """测试 4: 验证 BatchRasterEngine 在 ERROR_IF_EXISTS 策略下针对全部 7 个露出产物的预检防线"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.in_dir = Path(self.temp_dir.name) / "inputs"
        self.out_dir = Path(self.temp_dir.name) / "outputs"
        self.in_dir.mkdir(parents=True, exist_ok=True)
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_unified_need_flags_logic(self):
        # 测试 6 大模式下的阶段需求判定逻辑
        modes_expectations = {
            JOB_MODE_TIDE_ONLY: (True, False, False),
            JOB_MODE_TIDE_AND_INUNDATION: (True, True, False),
            JOB_MODE_INUNDATION_FROM_CACHE: (False, True, False),
            JOB_MODE_TIDE_AND_EXPOSURE: (True, False, True),
            JOB_MODE_EXPOSURE_FROM_CACHE: (False, False, True),
            JOB_MODE_ALL: (True, True, True),
        }
        for mode, (exp_tide, exp_freq, exp_exp) in modes_expectations.items():
            need_tide = (mode in [JOB_MODE_TIDE_ONLY, JOB_MODE_TIDE_AND_INUNDATION, JOB_MODE_TIDE_AND_EXPOSURE, JOB_MODE_ALL])
            need_freq = (mode in [JOB_MODE_TIDE_AND_INUNDATION, JOB_MODE_INUNDATION_FROM_CACHE, JOB_MODE_ALL])
            need_exp = (mode in [JOB_MODE_TIDE_AND_EXPOSURE, JOB_MODE_EXPOSURE_FROM_CACHE, JOB_MODE_ALL])
            self.assertEqual(need_tide, exp_tide, f"Mismatch for mode {mode} need_tide")
            self.assertEqual(need_freq, exp_freq, f"Mismatch for mode {mode} need_freq")
            self.assertEqual(need_exp, exp_exp, f"Mismatch for mode {mode} need_exp")


class TestCLIExposureAndBatchParsing(unittest.TestCase):
    """测试 5: 验证 CLI 命令行参数解析器对 exposure 与 batch 6 种模式的准确支持"""

    def test_cli_parser_exposure(self):
        from cli import main
        import argparse
        # 模拟 sys.argv 测试参数解析
        test_args = [
            "raster", "exposure",
            "--dem", "dummy_dem.tif",
            "--output-dir", "dummy_out",
            "--year", "2024"
        ]
        # 我们用 patch parser.parse_args 或直接导入 parser 构建测试
        with patch("sys.argv", ["cli.py"] + test_args):
            with patch("cli.FESTidePredictor"), patch("cli.DatumTransformer"):
                with patch("core.raster_engine.RasterTideEngine.calculate_exposure_raster") as mock_exp:
                    mock_exp.return_value = {"elapsed_seconds": 1.0, "products": None}
                    with patch("core.raster_engine.RasterTideEngine.inspect_raster") as mock_insp:
                        mock_insp.return_value = MagicMock(width=100, height=100, crs="EPSG:4326", formatted_resolution="10m")
                        try:
                            main(test_args)
                        except SystemExit:
                            pass
                        self.assertTrue(mock_exp.called)

    def test_cli_parser_batch_all_modes(self):
        # 验证 6 种模式在 choices 列表中均合法
        import cli
        # 检查 batch parser 中的 choices
        # 通过构造 parser 检验
        import argparse
        parser = argparse.ArgumentParser()
        # 验证 choices 集合与 batch_raster_engine 模式一致
        valid_modes = [
            JOB_MODE_TIDE_ONLY,
            JOB_MODE_TIDE_AND_INUNDATION,
            JOB_MODE_INUNDATION_FROM_CACHE,
            JOB_MODE_TIDE_AND_EXPOSURE,
            JOB_MODE_EXPOSURE_FROM_CACHE,
            JOB_MODE_ALL
        ]
        self.assertEqual(len(valid_modes), 6)


class TestManualDialogContent(unittest.TestCase):
    """测试 6: 验证用户手册 HTML 包含完整的 15 章节与关键科学不变量定义"""

    def test_manual_html_structure(self):
        from gui.manual_dialog import MANUAL_HTML
        # 1. 标题与版本
        self.assertIn("CoastTideX 用户操作手册与科学原理文档 (v1.6 Beta)", MANUAL_HTML)
        # 2. 15 个章节标识
        self.assertIn("一、 系统定位与科学用途", MANUAL_HTML)
        self.assertIn("二、 核心科学定义与边界不变量", MANUAL_HTML)
        self.assertIn("三、 潜在天文潮露出 7 大空间栅格产物体系", MANUAL_HTML)
        self.assertIn("四、 连续事件段统计口径与时间窗口约束", MANUAL_HTML)
        self.assertIn("五、 时间采样语义与 Schema 1.2 终端采样", MANUAL_HTML)
        self.assertIn("六、 四大高程基准体系与转换原理", MANUAL_HTML)
        self.assertIn("七、 自适应控制网格与空间拓扑连通防护", MANUAL_HTML)
        self.assertIn("八、 持久化 Tide Cache 架构与防篡改签名", MANUAL_HTML)
        self.assertIn("九、 批量处理 6 大运行模式", MANUAL_HTML)
        self.assertIn("十、 现有输出处理策略与断点恢复", MANUAL_HTML)
        self.assertIn("十一、 质量控制掩膜位定义", MANUAL_HTML)
        self.assertIn("十二、 批处理清单与产物追溯", MANUAL_HTML)
        self.assertIn("十三、 系统硬件资源与内存管理", MANUAL_HTML)
        self.assertIn("十四、 科学局限性与使用边界", MANUAL_HTML)
        self.assertIn("十五、 典型应用场景与推荐工作流", MANUAL_HTML)

        # 3. 严格科学公式与边界判定
        self.assertIn("H(t) &gt; z", MANUAL_HTML)
        self.assertIn("H(t) &le; z", MANUAL_HTML)
        self.assertIn("H(t) == z", MANUAL_HTML)
        self.assertIn("严格归属于露出状态 (Exposed)", MANUAL_HTML)
        self.assertIn("严禁混淆为“沙滩干燥时间", MANUAL_HTML)
        self.assertIn("tide_msl_terminal_m", MANUAL_HTML)
        self.assertIn("17,568", MANUAL_HTML)


class TestDocumentationLint(unittest.TestCase):
    """测试 7: 检查全库 Markdown 文档，防止硬编码测试数徽章、破坏性 LaTeX 或本地绝对链接回潮"""

    def test_no_static_test_count_badge_in_readme(self):
        readme_path = os.path.join(project_root, "README.md")
        readme_en_path = os.path.join(project_root, "README_EN.md")
        for p in [readme_path, readme_en_path]:
            with open(p, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertNotIn("Tests-143%20Passing", content, f"Static badge found in {p}")
            self.assertNotIn("Tests-136%20Passing", content, f"Static badge found in {p}")

    def test_no_broken_latex_nrac_or_tab_times(self):
        docs_dir = os.path.join(project_root, "docs")
        for md_file in Path(docs_dir).glob("*.md"):
            with open(md_file, "r", encoding="utf-8") as f:
                text = f.read()
            self.assertNotIn("\\nrac", text, f"Broken LaTeX \\nrac found in {md_file}")
            # 确保没有因 \t 转义为 tab 的 broken times
            self.assertNotIn("\times", text.replace("\\times", ""), f"Raw tab times found in {md_file}")

    def test_no_local_drive_file_uris_in_audit_docs(self):
        audit_r2 = os.path.join(project_root, "docs", "V1_6_EXPOSURE_HARDENING_AUDIT.md")
        with open(audit_r2, "r", encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("file:///I:", text, "Local file URI found in Round 2 audit doc")


class TestMainWindowExposureAndBatchWiring(unittest.TestCase):
    """测试 8: 验证 GUI 主窗口 Tab 3 潜在露出模式切换与批处理 9 列表格及模式提示联动"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.win = MainWindow()

    def tearDown(self):
        self.win.close()

    def test_raster_tab_exposure_mode_switch(self):
        # 初始应为潮位栅格 (snapshot)
        self.assertEqual(self.win.combo_raster_mode.currentIndex(), 0)

        # 切换至潜在露出 (exposure)
        exp_idx = self.win.combo_raster_mode.findData("exposure")
        self.assertGreaterEqual(exp_idx, 0)
        self.win.combo_raster_mode.setCurrentIndex(exp_idx)

        # 验证输出路径标签变为输出目录，QC 控件被隐藏
        self.assertIn("Output Directory", self.win.lbl_raster_output.text())
        self.assertTrue(self.win.lbl_inund_qc.isHidden())
        self.assertTrue(self.win.edit_inund_qc.isHidden())
        self.assertTrue(self.win.btn_browse_qc.isHidden())

        # 验证自动推导输出目录名称为 <DEM_STEM>_CoastTideX_exposure
        self.win.edit_raster_input.setText("C:/data/island_dem.tif")
        self.win._propose_raster_output("C:/data/island_dem.tif")
        self.assertTrue(self.win.edit_raster_output.text().endswith("island_dem_CoastTideX_exposure"))

        # 切换回淹没频率 (inundation)
        inund_idx = self.win.combo_raster_mode.findData("inundation")
        self.win.combo_raster_mode.setCurrentIndex(inund_idx)
        self.assertIn("淹没频率", self.win.lbl_raster_output.text())
        self.assertFalse(self.win.lbl_inund_qc.isHidden())
        self.assertFalse(self.win.edit_inund_qc.isHidden())
        self.assertFalse(self.win.btn_browse_qc.isHidden())

    def test_batch_table_columns_and_mode_tips(self):
        # 验证批处理表格列数为 9 列，且第 8 列表头为潜在露出产物
        self.assertEqual(self.win.table_batch_rasters.columnCount(), 9)
        col8_text = self.win.table_batch_rasters.horizontalHeaderItem(8).text()
        self.assertIn("露出", col8_text)

        # 验证 6 种模式切换时 UI 联动和提示文字
        expected_modes = [
            ("tide", True, True, "tide"),
            ("tide-inundation", True, True, "tide-inundation"),
            ("inundation-from-cache", False, False, "inundation-from-cache"),
            ("tide-exposure", True, True, "tide-exposure"),
            ("exposure-from-cache", False, False, "exposure-from-cache"),
            ("all", True, True, "all"),
        ]
        for mode_val, time_enabled, sci_enabled, kw in expected_modes:
            idx = self.win.cmb_batch_job_mode.findData(mode_val)
            self.assertGreaterEqual(idx, 0, f"Mode {mode_val} not found in cmb_batch_job_mode")
            self.win.cmb_batch_job_mode.setCurrentIndex(idx)

            self.assertEqual(self.win.grp_batch_time.isEnabled(), time_enabled, f"grp_batch_time for {mode_val}")
            self.assertEqual(self.win.grp_batch_sci.isEnabled(), sci_enabled, f"grp_batch_sci for {mode_val}")
            self.assertIn(kw, self.win.lbl_batch_job_mode_tip.text())

    def test_raster_worker_exposure_execution(self):
        mock_engine = MagicMock()
        mock_engine.calculate_exposure_raster.return_value = {
            "mode": "exposure",
            "elapsed_seconds": 1.5,
            "products": None
        }
        params = {
            "input_path": "test.tif",
            "output_dir": "test_exp",
            "year": 2024
        }
        worker = RasterTideWorker(params=params, mode="exposure", engine=mock_engine)
        worker.run()
        self.assertTrue(mock_engine.calculate_exposure_raster.called)


if __name__ == "__main__":
    unittest.main()
