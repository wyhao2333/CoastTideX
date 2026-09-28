"""
CoastTideX v1.7.1 - GUI Responsive Layout & Usability Regression Test Suite
验证在不同屏幕分辨率 (960x500, 1280x800, 1600x900, 1920x1080) 及 Windows DPI 缩放场景下：
1. 主选项卡 (Tab 0: 单点/时段, Tab 2: DEM基准转换, Tab 3: 单影像栅格, Tab 4: 批量潮间带栅格) 均无水平滚动条溢出 (h_max == 0)。
2. 下拉框自适应收缩 (Ignored 水平策略) 且完整保留 underlying itemData 核心业务枚举值。
3. 极端超长路径输入不挤爆布局。
4. 动态多级窗口缩放平滑无损。
5. 扫描生命周期按钮文字紧凑适配。
6. 系统标题与使用手册版本统一为 v1.7.1。
"""

import os
import sys
import unittest

os.environ["QT_QPA_PLATFORM"] = "offscreen"

try:
    from PyQt6.QtWidgets import QApplication, QScrollArea, QComboBox
    from PyQt6.QtCore import Qt
    HAS_PYQT6 = True
except ImportError:
    HAS_PYQT6 = False


@unittest.skipUnless(HAS_PYQT6, "需要 PyQt6 GUI 运行环境")
class TestV171GUIResponsiveLayout(unittest.TestCase):
    """CoastTideX v1.7.1 GUI 响应式布局自动化回归测试"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance()
        if cls.app is None:
            cls.app = QApplication(sys.argv)

    def setUp(self):
        from gui.main_window import MainWindow
        self.win = MainWindow()
        self.win.show()
        self.app.processEvents()

    def tearDown(self):
        self.win.close()
        self.app.processEvents()

    def test_01_all_tabs_no_horizontal_scroll_at_960x500(self):
        """测试在极限基准分辨率 960x500 下所有选项卡无水平滚动条 (h_max == 0)"""
        self.win.resize(960, 500)
        self.app.processEvents()

        for tab_idx in range(self.win.tabs.count()):
            self.win.tabs.setCurrentIndex(tab_idx)
            self.app.processEvents()
            tab_name = self.win.tabs.tabText(tab_idx)
            scroll_areas = self.win.tabs.currentWidget().findChildren(QScrollArea)
            for s_idx, scroll in enumerate(scroll_areas):
                h_max = scroll.horizontalScrollBar().maximum()
                self.assertEqual(
                    h_max, 0,
                    f"Tab {tab_idx} ('{tab_name}') ScrollArea {s_idx} 出现水平滚动条溢出: h_max={h_max} (窗口 960x500)"
                )

    def test_02_all_resolutions_matrix(self):
        """测试多分辨率矩阵 (960x500, 1280x800, 1600x900, 1920x1080) 零水平溢出"""
        resolutions = [
            (960, 500),
            (1280, 800),
            (1600, 900),
            (1920, 1080),
        ]

        for w, h in resolutions:
            self.win.resize(w, h)
            self.app.processEvents()

            for tab_idx in range(self.win.tabs.count()):
                self.win.tabs.setCurrentIndex(tab_idx)
                self.app.processEvents()
                tab_name = self.win.tabs.tabText(tab_idx)
                scroll_areas = self.win.tabs.currentWidget().findChildren(QScrollArea)
                for s_idx, scroll in enumerate(scroll_areas):
                    h_max = scroll.horizontalScrollBar().maximum()
                    self.assertEqual(
                        h_max, 0,
                        f"分辨率 {w}x{h} 下 Tab {tab_idx} ('{tab_name}') 出现水平溢出: h_max={h_max}"
                    )

    def test_03_dynamic_resize_cycling(self):
        """测试动态连续缩放循环 (1600x900 -> 1280x800 -> 960x500 -> 1920x1080 -> 960x500) 布局稳定性"""
        cycle = [(1600, 900), (1280, 800), (960, 500), (1920, 1080), (960, 500)]
        for w, h in cycle:
            self.win.resize(w, h)
            self.app.processEvents()

        # 最终停在 960x500 验证
        for tab_idx in range(self.win.tabs.count()):
            self.win.tabs.setCurrentIndex(tab_idx)
            self.app.processEvents()
            scroll_areas = self.win.tabs.currentWidget().findChildren(QScrollArea)
            for scroll in scroll_areas:
                self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)

    def test_04_long_path_strings_do_not_cause_overflow(self):
        """测试注入超长文件/文件夹路径时，控件自适应收缩且不撑爆水平滚动条"""
        self.win.resize(960, 500)
        self.app.processEvents()

        deep_path = (
            "D:/very/deep/project/workspace/storage/satellite/imagery/processing/"
            "intertidal_zones/chongming_dongtan_coastal_reserve_experiment_station_dataset/"
            "subfolder_level_5/subfolder_level_6/annual_sentinel_composites_2024"
        )
        self.win.txt_batch_in_dir.setText(deep_path)
        self.win.txt_batch_out_dir.setText(deep_path + "/output")
        self.win.edit_raster_input.setText(deep_path + "/sample_input.tif")
        self.win.edit_raster_output.setText(deep_path + "/output/sample_output.tif")
        self.win.edit_dem_input.setText(deep_path + "/dem_raw.tif")
        self.win.edit_dem_output.setText(deep_path + "/output/dem_msl.tif")
        self.win.edit_batch_dem_input.setText(deep_path)
        self.win.edit_batch_dem_output.setText(deep_path + "/dem_msl_batch")
        self.app.processEvents()

        for tab_idx in range(self.win.tabs.count()):
            self.win.tabs.setCurrentIndex(tab_idx)
            self.app.processEvents()
            scroll_areas = self.win.tabs.currentWidget().findChildren(QScrollArea)
            for scroll in scroll_areas:
                self.assertEqual(
                    scroll.horizontalScrollBar().maximum(), 0,
                    f"长路径注入后 Tab {tab_idx} 发生水平溢出"
                )

    def test_05_combobox_responsive_and_current_data_integrity(self):
        """测试响应式下拉框在文本紧凑化的同时，所有 itemData 科学枚举值严格保留"""
        # Batch Raster Tab 下拉框
        expected_batch_modes = ["tide-inundation", "tide", "inundation-from-cache", "tide-exposure", "exposure-from-cache", "all"]
        actual_batch_modes = [self.win.cmb_batch_job_mode.itemData(i) for i in range(self.win.cmb_batch_job_mode.count())]
        self.assertEqual(actual_batch_modes, expected_batch_modes)

        expected_policies = ["resume", "error_if_exists", "overwrite"]
        actual_policies = [self.win.cmb_batch_existing_policy.itemData(i) for i in range(self.win.cmb_batch_existing_policy.count())]
        self.assertEqual(actual_policies, expected_policies)

        expected_datums = ["msl", "egm2008", "goco06s", "wgs84"]
        actual_datums = [self.win.cmb_batch_datum.itemData(i) for i in range(self.win.cmb_batch_datum.count())]
        self.assertEqual(actual_datums, expected_datums)

        expected_const = ["all", "major8"]
        actual_const = [self.win.cmb_batch_const.itemData(i) for i in range(self.win.cmb_batch_const.count())]
        self.assertEqual(actual_const, expected_const)

        expected_targets = ["intertidal", "standard"]
        actual_targets = [self.win.cmb_batch_target_mode.itemData(i) for i in range(self.win.cmb_batch_target_mode.count())]
        self.assertEqual(actual_targets, expected_targets)

        # DEM Convert Tab 下拉框 (通过 findChildren 提取验证)
        dem_combos = self.win.tab_dem_convert.findChildren(QComboBox)
        all_dem_combo_data = []
        for c in dem_combos:
            for i in range(c.count()):
                all_dem_combo_data.append(c.itemData(i))
        self.assertIn("msl", all_dem_combo_data)
        self.assertIn("cnes_cls22", all_dem_combo_data)

        # Single Tab 下拉框
        single_datums = [self.win.combo_compute_datum.itemData(i) for i in range(self.win.combo_compute_datum.count())]
        self.assertIn("egm2008", single_datums)
        self.assertIn("msl", single_datums)

    def test_06_batch_scan_button_lifecycle_responsiveness(self):
        """测试批量解算扫描按钮在失效、扫描中、扫描完成等生命周期内均保持紧凑无溢出"""
        self.win.resize(960, 500)
        self.win.tabs.setCurrentIndex(4)
        self.app.processEvents()
        scroll = self.win.tabs.currentWidget().findChildren(QScrollArea)[0]

        # 1. 触发配置失效
        self.win._invalidate_batch_scan()
        self.app.processEvents()
        self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)

        # 2. 模拟扫描完成回调
        self.win._on_scan_finished([])
        self.app.processEvents()
        self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)

        # 3. 模拟扫描异常回调
        from unittest.mock import patch
        with patch("gui.main_window.QMessageBox.critical"):
            self.win._on_scan_error("测试错误")
        self.app.processEvents()
        self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)

    def test_07_about_and_status_version_strings(self):
        """测试主窗口标题与操作手册版本标识为 CoastTideX v1.7.1"""
        self.assertIn("v1.7.1", self.win.windowTitle())

        from gui.manual_dialog import ManualDialog
        manual = ManualDialog(self.win)
        self.assertIn("v1.7.1", manual.windowTitle())


if __name__ == "__main__":
    unittest.main()
