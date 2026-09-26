"""
CoastTideX v1.7 - GUI MSL Reference Workflow Unit Tests
验证 GUI DEM 基准转换选项卡 (EGM2008 -> MSL)、100 km 外推门禁锁定、双重转换防呆拦截与下游直通 handoff。
"""

import os
import sys
import unittest
import tempfile
import numpy as np
from unittest.mock import patch, MagicMock

# 启用无头模式 (Headless / Offscreen)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

try:
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import Qt
    HAS_PYQT6 = True
except ImportError:
    HAS_PYQT6 = False

try:
    import rasterio
    from rasterio.transform import from_bounds
    HAS_RASTERIO = True
except ImportError:
    HAS_RASTERIO = False


@unittest.skipUnless(HAS_PYQT6 and HAS_RASTERIO, "需要 PyQt6 与 rasterio 环境")
class TestV17GUIMSLWorkflow(unittest.TestCase):
    """v1.7 GUI DEM 基准转换与直通工作流测试"""

    @classmethod
    def setUpClass(cls):
        # 确保全局唯一的 QApplication 实例
        cls.app = QApplication.instance()
        if cls.app is None:
            cls.app = QApplication(sys.argv)

    def setUp(self):
        from gui.main_window import MainWindow
        self.win = MainWindow()
        self.win.show()
        self.win.tabs.setCurrentWidget(self.win.tab_dem_convert)
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.win.close()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def _create_synthetic_dem(self, filename: str, is_msl: bool = False) -> str:
        """生成合成 DEM 测试栅格"""
        path = os.path.join(self.temp_dir.name, filename)
        transform = from_bounds(121.5, 31.0, 121.8, 31.3, 30, 30)
        data = np.ones((1, 30, 30), dtype=np.float32) * 5.0

        tags = {}
        if is_msl:
            tags['DATUM'] = 'MSL'
            tags['TARGET_VERTICAL_DATUM'] = 'MSL'
            tags['ANALYSIS_REFERENCE'] = 'MSL'
        else:
            tags['DATUM'] = 'EGM2008'

        with rasterio.open(
            path, 'w',
            driver='GTiff',
            height=30,
            width=30,
            count=1,
            dtype=rasterio.float32,
            crs="EPSG:4326",
            transform=transform,
            nodata=-9999.0
        ) as dst:
            dst.write(data)
            dst.update_tags(**tags)
        return path

    def test_dem_convert_tab_initialization(self):
        """测试 DEM 基准转换选项卡是否正确挂载在 Index 2 并包含全套核心组件"""
        self.assertEqual(self.win.tabs.count(), 5)
        self.assertEqual(self.win.tabs.widget(2), self.win.tab_dem_convert)
        self.assertIn("DEM 基准转换", self.win.tabs.tabText(2))
        self.assertIn("EGM2008→MSL", self.win.tabs.tabText(2))

        # 检查核心控件是否存在
        self.assertTrue(hasattr(self.win, "edit_dem_input"))
        self.assertTrue(hasattr(self.win, "spin_dem_max_dist"))
        self.assertTrue(hasattr(self.win, "spin_dem_block_size"))
        self.assertTrue(hasattr(self.win, "chk_dem_apply_deltan"))
        self.assertTrue(hasattr(self.win, "chk_dem_save_qc"))
        self.assertTrue(hasattr(self.win, "edit_dem_output"))
        self.assertTrue(hasattr(self.win, "btn_run_dem_convert"))
        self.assertTrue(hasattr(self.win, "btn_cancel_dem_convert"))
        self.assertTrue(hasattr(self.win, "grp_dem_results"))
        self.assertTrue(hasattr(self.win, "btn_handoff_inund"))
        self.assertTrue(hasattr(self.win, "btn_handoff_exp"))
        self.assertTrue(hasattr(self.win, "btn_dem_open_folder"))

    def test_spin_dem_max_dist_capped_at_100(self):
        """测试沿岸外推距离上限控件严格锁定在 100.0 km (Seeger & Minderhoud 2026 保守阈值)"""
        spin = self.win.spin_dem_max_dist
        self.assertEqual(spin.maximum(), 100.0)
        self.assertEqual(spin.minimum(), 0.0)
        self.assertEqual(spin.value(), 100.0)

        # 尝试设置超出 100 km 的数值，应被截断在 100.0
        spin.setValue(500.0)
        self.assertEqual(spin.value(), 100.0)

    def test_inundation_exposure_defaults_to_msl(self):
        """测试单影像栅格分析中 DEM 基准面默认且优先推荐 MSL"""
        combo = self.win.combo_inund_datum
        self.assertEqual(combo.currentData(), "msl")
        self.assertIn("MSL 推荐模式", self.win.lbl_inund_datum_hint.text())

        # 切换到 EGM2008 时，应当出现兼容模式警告提示
        idx_egm = combo.findData("egm2008")
        self.assertGreaterEqual(idx_egm, 0)
        combo.setCurrentIndex(idx_egm)
        self.assertIn("EGM2008 为兼容模式", self.win.lbl_inund_datum_hint.text())

        # 切回 MSL
        idx_msl = combo.findData("msl")
        combo.setCurrentIndex(idx_msl)
        self.assertIn("MSL 推荐模式", self.win.lbl_inund_datum_hint.text())

    def test_batch_raster_defaults_to_msl(self):
        """测试批量潮间带栅格分析中 DEM 高程基准下拉列表默认包含 MSL"""
        first_item = self.win.cmb_batch_datum.itemText(0)
        self.assertTrue(first_item.startswith("MSL"))

    def test_double_conversion_guard(self):
        """测试双重转换防呆拦截机制：输入已是 MSL 的 DEM 时显示告警卡片并直通"""
        msl_dem = self._create_synthetic_dem("test_already_msl.tif", is_msl=True)
        self.win.edit_dem_input.setText(msl_dem)

        # 触发元数据检查
        self.win._inspect_dem_input_ui(msl_dem)

        # 验证告警卡片展示与检测文本
        self.assertFalse(self.win.frame_dem_warning.isHidden())
        self.assertIn("已处于 MSL 局部平均海平面基准", self.win.lbl_dem_warning.text())
        self.assertIn("MSL", self.win.lbl_dem_detected_datum.text())
        self.assertIn("强制重新转换", self.win.btn_run_dem_convert.text())

        # 结果直通卡片应自动展现，方便用户直接使用已有产物
        self.assertFalse(self.win.grp_dem_results.isHidden())
        self.assertEqual(self.win.lbl_res_dem_path.text(), msl_dem)

    def test_normal_egm2008_dem_inspection(self):
        """测试正常 EGM2008 DEM 检查逻辑"""
        egm_dem = self._create_synthetic_dem("test_egm2008.tif", is_msl=False)
        self.win.edit_dem_input.setText(egm_dem)
        self.win._inspect_dem_input_ui(egm_dem)

        self.assertTrue(self.win.frame_dem_warning.isHidden())
        self.assertIn("EGM2008", self.win.lbl_dem_detected_datum.text())
        self.assertIn("开始 DEM 基准转换", self.win.btn_run_dem_convert.text())
        self.assertTrue(self.win.edit_dem_output.text().endswith("_MSL.tif"))

    def test_handoff_to_inundation_and_exposure(self):
        """测试从 DEM 基准转换结果卡片一键直通到单影像淹没频率与露出时间分析"""
        msl_dem = self._create_synthetic_dem("chongming_MSL.tif", is_msl=True)
        self.win.lbl_res_dem_path.setText(msl_dem)
        self.win.grp_dem_results.setVisible(True)

        # 1. 测试直通淹没频率分析
        self.win._handoff_to_inundation()
        self.assertEqual(self.win.tabs.currentWidget(), self.win.tab_raster)
        self.assertEqual(self.win.edit_raster_input.text(), msl_dem)
        self.assertEqual(self.win.combo_raster_mode.currentData(), "inundation")
        self.assertEqual(self.win.combo_inund_datum.currentData(), "msl")

        # 2. 回到 DEM 转换标签页并测试直通露出时间分析
        self.win.tabs.setCurrentWidget(self.win.tab_dem_convert)
        self.win._handoff_to_exposure()
        self.assertEqual(self.win.tabs.currentWidget(), self.win.tab_raster)
        self.assertEqual(self.win.edit_raster_input.text(), msl_dem)
        self.assertEqual(self.win.combo_raster_mode.currentData(), "exposure")
        self.assertEqual(self.win.combo_inund_datum.currentData(), "msl")

    def test_conversion_worker_execution(self):
        """测试 DEMDatumConversionWorker 执行逻辑与 UI 回调更新"""
        from gui.main_window import DEMDatumConversionWorker
        from core.dem_datum_converter import DEMConversionSummary
        egm_dem = self._create_synthetic_dem("convert_worker_test.tif", is_msl=False)
        out_msl = os.path.join(self.temp_dir.name, "convert_worker_test_MSL.tif")

        params = {
            'input_path': egm_dem,
            'output_path': out_msl,
            'qc_output_path': None,
            'max_dist_km': 100.0,
            'block_size': 512,
            'allow_overwrite': True,
            'save_qc': False
        }
        worker = DEMDatumConversionWorker(params)

        mock_summary = DEMConversionSummary(
            input_path=egm_dem,
            output_path=out_msl,
            qc_output_path=None,
            width=30,
            height=30,
            total_pixels=900,
            valid_dem_pixels=900,
            native_mdt_pixels=900,
            extrapolated_mdt_pixels=0,
            nodata_pixels=0,
            elapsed_seconds=0.1,
            max_extrapolation_distance_km=100.0,
            metadata={'DATUM': 'MSL'}
        )

        def mock_convert_func(**kwargs):
            with open(out_msl, 'w') as f:
                f.write('dummy')
            cb = kwargs.get('progress_callback')
            if cb:
                cb(50, "处理中...")
                cb(100, "完成")
            return mock_summary

        with patch('core.dem_datum_converter.convert_dem_to_msl', side_effect=mock_convert_func):
            results = []
            progresses = []
            worker.progress.connect(lambda p, m: progresses.append((p, m)))
            worker.finished.connect(lambda s: results.append(s))
            worker.run()

            self.assertEqual(len(results), 1)
            summary = results[0]
            self.assertEqual(summary.output_path, out_msl)
            self.assertTrue(os.path.exists(out_msl))
            self.assertGreaterEqual(len(progresses), 1)

            # 测试 UI 槽函数响应
            self.win._on_dem_conversion_finished(summary)
            self.assertFalse(self.win.grp_dem_results.isHidden())
            self.assertEqual(self.win.lbl_res_dem_path.text(), out_msl)
            self.assertIn("30 × 30", self.win.lbl_res_dem_dims.text())

        # 测试 worker 取消机制
        worker.cancel()
        self.assertTrue(worker._is_cancelled)
        self.assertTrue(worker.cancel_event.is_set())

    def test_batch_dem_mode_toggle_and_gui_workflow(self):
        """测试 v1.7.1 批量 DEM 转换模式切换、目录扫描与指标卡更新"""
        # 初始应为单幅模式
        self.assertTrue(self.win.radio_dem_mode_single.isChecked())
        self.assertFalse(self.win.widget_dem_single.isHidden())
        self.assertTrue(self.win.widget_dem_batch.isHidden())

        # 切换到批量模式
        self.win.radio_dem_mode_batch.setChecked(True)
        self.assertTrue(self.win.widget_dem_single.isHidden())
        self.assertFalse(self.win.widget_dem_batch.isHidden())

        # 创建测试 DEM 文件
        self._create_synthetic_dem("tile_1.tif")
        self._create_synthetic_dem("tile_2.tif")

        self.win.edit_batch_dem_input.setText(self.temp_dir.name)
        self.win._scan_batch_dem_folder(self.temp_dir.name)

        self.assertIn("2", self.win.lbl_batch_dem_scan_status.text())
        self.assertEqual(self.win.lbl_batch_dem_stat_total.text(), "2")

        # 测试进度回调槽函数
        stats = {
            "total": 2,
            "success": 1,
            "failed": 0,
            "skipped_msl": 0,
            "skipped_resume": 1,
            "current_file": "tile_1.tif"
        }
        self.win._on_batch_dem_progress(1, 2, "tile_1.tif", "正在转换...", stats)
        self.assertEqual(self.win.prog_batch_dem_overall.value(), 50)
        self.assertEqual(self.win.lbl_batch_dem_stat_success.text(), "1")
        self.assertEqual(self.win.lbl_batch_dem_stat_skipped_resume.text(), "1")

        # 测试完成槽函数
        from core.batch_datum_converter import BatchConversionSummary
        summary = BatchConversionSummary(
            input_dir=self.temp_dir.name,
            output_dir=os.path.join(self.temp_dir.name, "out"),
            total_tiles=2,
            success_count=1,
            failed_count=0,
            skipped_msl_count=0,
            skipped_resume_count=1,
            elapsed_seconds=1.23,
            manifest_csv=os.path.join(self.temp_dir.name, "out", "conversion_manifest.csv"),
            manifest_json=os.path.join(self.temp_dir.name, "out", "conversion_manifest.json")
        )
        self.win._on_batch_dem_finished(summary)
        self.assertEqual(self.win.prog_batch_dem_overall.value(), 100)
        self.assertFalse(self.win.grp_batch_dem_results.isHidden())
        self.assertEqual(self.win.lbl_batch_dem_manifest_csv.text(), summary.manifest_csv)

    def test_batch_dem_handoff_to_batch_raster(self):
        """测试批量 DEM 产物目录一键直通载入 Tab 4 批量潮间带栅格解算"""
        out_dir = os.path.join(self.temp_dir.name, "out_msl")
        os.makedirs(out_dir, exist_ok=True)
        self.win.edit_batch_dem_output.setText(out_dir)

        with patch.object(self.win, "_on_scan_batch_rasters") as mock_scan:
            self.win._handoff_batch_dem_to_batch_raster()
            self.assertEqual(self.win.tabs.currentIndex(), 4)
            self.assertEqual(self.win.txt_batch_in_dir.text(), out_dir)
            mock_scan.assert_called_once()


if __name__ == "__main__":
    unittest.main()
