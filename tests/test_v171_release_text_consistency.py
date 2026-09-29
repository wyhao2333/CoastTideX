"""
CoastTideX v1.7.1 - Release Documentation, GUI Text & Code Comment Consistency Test Suite
========================================================================================

本测试套件依据 CoastTideX v1.7.1 Final Release Closure 规范执行全要素文字一致性核查：
1. 验证用户手册、README、代码注释完全清除 v1.6 Beta 残留与夸大不实宣传；
2. 验证数学公式 GitHub 渲染规范性 (禁用 \\text{..._...}，成对 ```math 代码块)；
3. 验证 Topology Guard 启发式空间防线科学界定；
4. 验证半开区间 [start, end) 时间采样规范；
5. 验证 GUI ComboBox 表面中性化文本与底盘 itemData 绝对不变性；
6. 验证核心物理与 QC 质量控制常数绝对完整未受损。
"""

import os
import sys
import re
import unittest
from unittest.mock import patch
from pathlib import Path

# 设置无头环境，适配 CI Linux
os.environ["QT_QPA_PLATFORM"] = "offscreen"

PROJECT_ROOT = Path(__file__).resolve().parent.parent

try:
    from PyQt6.QtWidgets import QApplication
    HAS_PYQT6 = True
except ImportError:
    HAS_PYQT6 = False


class TestV171ReleaseTextConsistency(unittest.TestCase):
    """v1.7.1 文档、GUI 文本与代码注释一致性测试套件"""

    @classmethod
    def setUpClass(cls):
        if HAS_PYQT6:
            cls.app = QApplication.instance()
            if cls.app is None:
                cls.app = QApplication(sys.argv)
        else:
            cls.app = None

    # 1. 验证当前 GUI 手册不含 v1.6 Beta
    def test_manual_contains_no_v16_beta(self):
        manual_path = PROJECT_ROOT / "gui" / "manual_dialog.py"
        self.assertTrue(manual_path.exists(), f"未找到文件: {manual_path}")
        text = manual_path.read_text(encoding="utf-8")

        self.assertNotIn("CoastTideX v1.6 Beta", text, "GUI 手册中依然包含 'CoastTideX v1.6 Beta'")
        self.assertNotIn("v1.6 Beta", text, "GUI 手册中依然包含 'v1.6 Beta'")
        self.assertNotIn("v1.6", text, "GUI 手册中依然包含 'v1.6'")
        self.assertIn("CoastTideX v1.7.1", text, "GUI 手册应包含 'CoastTideX v1.7.1'")

    # 2. 验证手册与 README 不含误导性 50% 磁盘占用表述
    def test_manual_and_readme_no_misleading_50_percent_disk_claim(self):
        targets = [
            PROJECT_ROOT / "README.md",
            PROJECT_ROOT / "README_EN.md",
            PROJECT_ROOT / "gui" / "manual_dialog.py",
            PROJECT_ROOT / "docs" / "V1_7_MSL_REFERENCE_WORKFLOW.md",
        ]
        for p in targets:
            if not p.exists():
                continue
            text = p.read_text(encoding="utf-8")
            self.assertNotIn("50% 磁盘", text, f"{p.name} 中包含 '50% 磁盘'")
            self.assertNotIn("50% disk", text.lower(), f"{p.name} 中包含 '50% disk'")
            self.assertNotIn("减少 50% 磁盘占用", text, f"{p.name} 中包含 '减少 50% 磁盘占用'")

    # 3. 验证 README 中 Schema 1.1 兼容边界准确客观
    def test_readme_schema11_compatibility_boundary_accurate(self):
        for fname in ["README.md", "README_EN.md"]:
            p = PROJECT_ROOT / fname
            text = p.read_text(encoding="utf-8")
            self.assertNotIn("完全向下兼容 Schema 1.1", text, f"{fname} 包含不严谨的完全向下兼容声明")
            self.assertNotIn("terminal hold-last", text.lower(), f"{fname} 包含 hold-last 误称")
            self.assertNotIn("平推", text, f"{fname} 包含平推等非事实表述")

    # 4. 验证 README 中 QC=0 科学语义客观
    def test_readme_qc_zero_semantics_accurate(self):
        p = PROJECT_ROOT / "README.md"
        text = p.read_text(encoding="utf-8")
        self.assertNotIn("QC=0 表示高保真", text, "README.md 不应将 QC=0 夸大为高保真")
        self.assertNotIn("高保真解算", text, "README.md 应中性化高保真修饰")

    # 5. 验证 README 数学公式中无导致 GitHub 渲染报错的 \\text{..._...}
    def test_readme_math_no_unescaped_underscore_in_text(self):
        pattern = re.compile(r'\\text\{[^{}]*_[^{}]*\}')
        for fname in ["README.md", "README_EN.md"]:
            p = PROJECT_ROOT / fname
            text = p.read_text(encoding="utf-8")
            matches = pattern.findall(text)
            self.assertEqual(
                len(matches), 0,
                f"{fname} 包含会导致 GitHub KaTeX 崩溃的 \\text{{..._...}} 语法: {matches}"
            )

    # 6. 验证 README 中 ```math 代码块成对闭合
    def test_readme_math_fences_balanced(self):
        for fname in ["README.md", "README_EN.md"]:
            p = PROJECT_ROOT / fname
            text = p.read_text(encoding="utf-8")
            lines = text.splitlines()

            math_open = False
            open_count = 0
            close_count = 0

            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped == "```math":
                    self.assertFalse(math_open, f"{fname} 第 {i+1} 行连续打开 ```math 而未闭合前一个块")
                    math_open = True
                    open_count += 1
                elif stripped == "```" and math_open:
                    math_open = False
                    close_count += 1

            self.assertFalse(math_open, f"{fname} 存在未闭合的 ```math 块")
            self.assertEqual(open_count, close_count, f"{fname} 的 ```math 块开闭数量不相等")
            self.assertGreater(open_count, 0, f"{fname} 应当包含至少一个 ```math 公式块")

    # 7. 验证 Topology Guard 不夸大为物理水动力连通性
    def test_topology_guard_not_claimed_as_physical_hydrodynamics(self):
        for p in [PROJECT_ROOT / "gui" / "manual_dialog.py", PROJECT_ROOT / "README.md"]:
            text = p.read_text(encoding="utf-8")
            if "Topology Guard" in text:
                self.assertIn("Target-Mask-Derived Topology Guard", text, f"{p.name} 缺少全称规范")
                self.assertTrue(
                    ("不等价于真实水动力" in text) or ("不等价于真实水动力/水力连通性" in text),
                    f"{p.name} 未明确界定 Topology Guard 不等价于真实水动力/水力连通性"
                )

    # 8. 验证栅格与 Tide Cache 严格遵循 [start, end) 半开区间
    def test_raster_cache_time_interval_semantics_accurate(self):
        p = PROJECT_ROOT / "gui" / "manual_dialog.py"
        text = p.read_text(encoding="utf-8")
        self.assertIn("[start, end)", text, "GUI 手册应明确包含半开区间 [start, end) 规范")

    # 9. 验证 GUI About 对话框文字中性且为 v1.7.1
    @unittest.skipUnless(HAS_PYQT6, "需要 PyQt6 GUI 运行环境")
    def test_gui_about_text_neutral_and_v171(self):
        from gui.main_window import MainWindow

        win = MainWindow()
        try:
            captured = {}

            def mock_about(parent, title, text):
                captured["title"] = title
                captured["text"] = text

            with patch("PyQt6.QtWidgets.QMessageBox.about", side_effect=mock_about):
                win._show_about()

            self.assertIn("text", captured, "未成功捕获 _show_about 的弹窗内容")
            about_html = captured["text"]

            self.assertIn("CoastTideX v1.7.1", about_html)
            self.assertNotIn("高保真度的空间潮汐预测", about_html)
            self.assertNotIn("防篡改断点恢复", about_html)
            self.assertNotIn("严密海拔正高", about_html)
            self.assertIn("相对 EGM2008 大地水准面的高程 / 正高近似", about_html)
        finally:
            win.close()
            if self.app:
                self.app.processEvents()

    # 10. 验证 GUI ComboBox 表面文本与底层 itemData key 不变性
    @unittest.skipUnless(HAS_PYQT6, "需要 PyQt6 GUI 运行环境")
    def test_gui_combobox_visible_text_and_data_keys(self):
        from gui.main_window import MainWindow

        win = MainWindow()
        try:
            # 1. 单点分潮 ComboBox
            self.assertEqual(win.combo_const.currentData(), "all")
            self.assertNotEqual(win.combo_const.findData("major8"), -1)
            self.assertIn("默认科学配置", win.combo_const.itemText(win.combo_const.findData("all")))

            # 2. 空间快照分潮 ComboBox
            self.assertEqual(win.combo_snap_const.currentData(), "all")
            self.assertNotEqual(win.combo_snap_const.findData("major8"), -1)

            # 3. 空间快照目标基准 ComboBox
            self.assertEqual(win.combo_snap_datum.currentData(), "egm2008")
            snap_egm_text = win.combo_snap_datum.itemText(win.combo_snap_datum.findData("egm2008"))
            self.assertEqual(snap_egm_text, "EGM2008 (相对 EGM2008 大地水准面的高程 / 正高近似)")

            # 4. 淹没分析 DEM 基准 ComboBox
            self.assertEqual(win.combo_inund_datum.currentData(), "msl")

            # 5. 淹没采样步长 15min ComboBox
            idx_15min = win.combo_inund_freq.findData("15min")
            self.assertNotEqual(idx_15min, -1)
            self.assertEqual(win.combo_inund_freq.itemText(idx_15min), "15分钟 (15min - 较高时间分辨率)")
        finally:
            win.close()
            if self.app:
                self.app.processEvents()

    # 11. 验证 CLI exposure 与 batch help 显示 v1.7.1
    def test_cli_exposure_and_batch_help_v171(self):
        from cli import build_parser

        parser = build_parser()
        # 栅格顶层 help
        raster_parser = None
        for action in parser._actions:
            if action.dest == "mode" and hasattr(action, "choices"):
                raster_parser = action.choices.get("raster")
                break

        self.assertIsNotNone(raster_parser, "未找到 raster 子解析器")
        raster_help = raster_parser.format_help()
        self.assertNotIn("v1.6 Beta", raster_help)
        self.assertIn("v1.7.1", raster_help)

        # 检查 batch 命令帮助
        batch_parser = None
        for action in raster_parser._actions:
            if action.dest == "raster_submode" and hasattr(action, "choices"):
                batch_parser = action.choices.get("batch")
                break
        self.assertIsNotNone(batch_parser, "未找到 raster batch 子解析器")
        batch_help = batch_parser.format_help()
        self.assertNotIn("v1.6 Beta", batch_help)

    # 12. 严格验证核心物理与 QC 常数未被破坏
    def test_exposure_engine_constants_unchanged(self):
        from core import exposure_engine as ee
        from core import raster_engine as re_mod

        # Exposure QC 位掩膜数值与别名
        self.assertEqual(ee.QC_EXP_VALID, 0)
        self.assertEqual(ee.QC_EXP_DEGRADED_CELL, 1)
        self.assertEqual(ee.QC_EXP_INSUFFICIENT_NODES, 2)
        self.assertEqual(ee.QC_EXP_DATUM_APPROX, 4)
        self.assertEqual(ee.QC_EXP_TERMINAL_UNAVAILABLE, 8)
        self.assertEqual(ee.QC_EXP_TERMINAL_APPROX, 8)
        self.assertEqual(ee.QC_EXP_PARTIAL_VALID_TIME, 16)
        self.assertEqual(ee.QC_EXP_PERMANENTLY_SUBMERGED, 32)
        self.assertEqual(ee.QC_EXP_PERMANENTLY_EXPOSED, 64)
        self.assertEqual(ee.QC_EXP_NODATA, 65535)

        # Raster Engine QC 掩膜数值
        self.assertEqual(re_mod.QC_BIT_VALID, 0)
        self.assertEqual(re_mod.QC_VALID, 0)
        self.assertEqual(re_mod.QC_NODATA, 65535)

    # 13. 验证 Git 跟踪状态下绝无任何 AI/Agent 指令文件与本地规则目录
    def test_git_tracked_no_agent_files(self):
        """验证 Git 跟踪状态下绝不存在任何 AGENT / AGENTS / GEMINI / CLAUDE / CODEX 指令文件或 .agents/ 目录"""
        import subprocess

        res = subprocess.run(
            ["git", "ls-files"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0:
            tracked_files = [line.strip() for line in res.stdout.splitlines() if line.strip()]
            tracked_agent_files = []
            for f in tracked_files:
                normalized = f.replace("\\", "/")
                if re.search(
                    r"(^|/)(AGENT|AGENTS|GEMINI|CLAUDE|CODEX)\.md$",
                    normalized,
                    re.IGNORECASE,
                ):
                    tracked_agent_files.append(f)
                    continue

                if normalized.startswith(".agents/"):
                    tracked_agent_files.append(f)

            self.assertEqual(
                tracked_agent_files,
                [],
                f"发现被 Git 跟踪的本地 AI/Agent 指令文件或目录: {tracked_agent_files}",
            )

    # 14. 验证生产文件无陈旧版本号且 Exposure SOFTWARE 标签为 CoastTideX v1.7.1
    def test_production_files_no_stale_versions_and_exposure_software(self):
        """验证 config.yaml、core/exposure_engine.py、core/raster_engine.py、gui/main_window.py 无陈旧版本文字，且 Exposure 标签为 v1.7.1"""
        # 1. 验证 config.yaml
        cfg_text = (PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8")
        self.assertNotIn("v1.6", cfg_text)
        self.assertIn("全球海岸带潮位模拟与高程基准转换系统 v1.7.1", cfg_text)
        self.assertIn("Exposure Engine v1.7.1", cfg_text)

        # 2. 验证 core/exposure_engine.py
        ee_text = (PROJECT_ROOT / "core" / "exposure_engine.py").read_text(encoding="utf-8")
        self.assertNotIn("CoastTideX v1.6 Beta", ee_text)
        self.assertIn('"SOFTWARE": "CoastTideX v1.7.1"', ee_text)

        # 3. 验证 core/raster_engine.py
        re_text = (PROJECT_ROOT / "core" / "raster_engine.py").read_text(encoding="utf-8")
        self.assertNotIn("Spatial Raster Tide Engine v1.6", re_text)
        self.assertNotIn("v1.6: 全系统统一", re_text)
        self.assertIn("Spatial Raster Tide Engine v1.7.1", re_text)

        # 4. 验证 gui/main_window.py
        mw_text = (PROJECT_ROOT / "gui" / "main_window.py").read_text(encoding="utf-8")
        self.assertNotIn("BatchRasterWorker (v1.5)", mw_text)
        self.assertNotRegex(mw_text, r"BatchRasterWorker\s*\(v1\.5\)")

    # 15. 验证中英文 README 中 GUI 快速上手部分准确描述全部 5 个选项卡
    def test_readme_gui_quickstart_covers_all_5_tabs(self):
        """验证 README.md 与 README_EN.md 的 GUI 快速上手部分完整包含 5 个选项卡，且与源码一致"""
        readme_zh = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        readme_en = (PROJECT_ROOT / "README_EN.md").read_text(encoding="utf-8")
        mw_code = (PROJECT_ROOT / "gui" / "main_window.py").read_text(encoding="utf-8")

        # 验证源码中 addTab 调用次数为 5
        tab_calls = re.findall(r"self\.tabs\.addTab\(", mw_code)
        self.assertEqual(len(tab_calls), 5, f"gui/main_window.py 中 tabs.addTab 次数不为 5: {len(tab_calls)}")

        # 验证中文 README
        for tab_str in ["选项卡 1", "选项卡 2", "选项卡 3", "选项卡 4", "选项卡 5", "DEM 基准转换"]:
            self.assertIn(tab_str, readme_zh, f"README.md 缺少 {tab_str}")

        # 验证英文 README
        for tab_str in ["Tab 1", "Tab 2", "Tab 3", "Tab 4", "Tab 5", "DEM Datum Conversion"]:
            self.assertIn(tab_str, readme_en, f"README_EN.md 缺少 {tab_str}")

    # 16. 验证作者研究方向、未正式发布状态徽章与生态边界措辞
    def test_readme_author_release_state_and_ecology_boundary(self):
        """验证作者研究方向中英一致、Release 徽章已下线以及生态边界中性化"""
        readme_zh = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        readme_en = (PROJECT_ROOT / "README_EN.md").read_text(encoding="utf-8")

        # 1. 作者研究方向检查
        self.assertIn("海岸带遥感", readme_zh)
        self.assertIn("Coastal Remote Sensing", readme_en)
        self.assertNotIn("沿海海洋动力学与大地测量学", readme_zh)
        self.assertNotIn("Coastal Ocean Dynamics & Geodesy", readme_en)

        # 2. Release 状态检查 (不得声称正式 Release)
        self.assertNotIn("Release-v1.7.1", readme_zh)
        self.assertNotIn("Release-v1.7.1", readme_en)
        self.assertNotIn("Current release: CoastTideX v1.7.1", readme_en)
        self.assertIn("Version-v1.7.1", readme_zh)
        self.assertIn("Version-v1.7.1", readme_en)
        self.assertIn("Current code version: **CoastTideX v1.7.1**", readme_en)

        # 3. 生态边界检查 (不得写潜在耐干时长)
        self.assertNotIn("潜在耐干时长", readme_zh)

    # 17. 验证全库关键发布文件科学措辞与中性化用语闭环
    def test_pre_release_scientific_claims_and_terminology_closure(self):
        """验证用户可见生产文档与界面文案彻底清除夸大与误导用词，且规范用语完整覆盖"""
        targets = {
            "README.md": (PROJECT_ROOT / "README.md").read_text(encoding="utf-8"),
            "README_EN.md": (PROJECT_ROOT / "README_EN.md").read_text(encoding="utf-8"),
            "CHANGELOG.md": (PROJECT_ROOT / "CHANGELOG.md").read_text(encoding="utf-8"),
            "gui/manual_dialog.py": (PROJECT_ROOT / "gui" / "manual_dialog.py").read_text(encoding="utf-8"),
            "gui/main_window.py": (PROJECT_ROOT / "gui" / "main_window.py").read_text(encoding="utf-8"),
            "cli.py": (PROJECT_ROOT / "cli.py").read_text(encoding="utf-8"),
            "core/dem_datum_converter.py": (PROJECT_ROOT / "core" / "dem_datum_converter.py").read_text(encoding="utf-8"),
        }

        banned_phrases = [
            "消除近岸潮位倾斜系统偏差",
            "专为高分辨率潮间带调校",
            "科学范式变革",
            "水体独立连通分量",
            "同一连通水体域",
            "沙滩动力学完整时间域分析",
            "底层潮汐流场",
            "34 个半日潮、日潮与长周期分潮",
            "all 34 major constituents",
            "34 个主分潮",
            "保障物理边界几何一致",
            "严密转换为局部平均海平面基准",
        ]

        for fname, text in targets.items():
            for phrase in banned_phrases:
                self.assertNotIn(
                    phrase,
                    text,
                    f"在生产文件 {fname} 中发现禁止的陈旧/夸大措辞: '{phrase}'"
                )

        # 验证核心中性化与规范词汇出现
        readme_zh = targets["README.md"]
        readme_en = targets["README_EN.md"]
        manual_zh = targets["gui/manual_dialog.py"]
        main_win = targets["gui/main_window.py"]

        self.assertIn("海岸带遥感", readme_zh)
        self.assertIn("Coastal Remote Sensing", readme_en)
        self.assertIn("全部 34 个分潮", readme_zh)
        self.assertIn("MSL Reference Workflow", readme_zh)
        self.assertIn("MSL 统一参考工作流", readme_zh)
        self.assertIn("Target-Mask-Derived Topology Guard", readme_zh)
        self.assertIn("基于目标计算掩膜派生的拓扑插值安全启发式", readme_zh)
        self.assertIn("固定代表性地形条件下的潜在天文潮露出时长", readme_zh)

        self.assertIn("v1.7 MSL 统一参考工作流", manual_zh)
        self.assertIn("native MDT interpolation path", manual_zh)
        self.assertIn("spherical 3D k-NN IDW", manual_zh)
        self.assertIn("NoData / 非计算区域", manual_zh)

        self.assertIn("面向海岸带遥感与潮间带地形分析", main_win)
        self.assertIn("EGM2008 大地水准面起伏格网", main_win)


if __name__ == "__main__":
    unittest.main()

