"""
CoastTideX v1.6 Beta Round 6 生产一致性与行为规范自动化测试套件
Test Suite for v1.6 Beta Round 6 Consistency, Cache Semantics, Spatial Index, and GUI Alignment
"""

import os
import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import rasterio
from rasterio.transform import from_origin
import netCDF4

os.environ["QT_QPA_PLATFORM"] = "offscreen"

# 确保模块检索路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from PyQt6.QtWidgets import QApplication, QMessageBox
from gui.main_window import MainWindow, RasterTideWorker
import core
from core.raster_engine import QuadCell, ControlNode, LeafCellSpatialIndex, RasterTideEngine
from core.batch_raster_engine import (
    BatchRasterEngine, BatchManifest, ExistingOutputPolicy,
    JOB_MODE_TIDE_ONLY, JOB_MODE_TIDE_AND_INUNDATION, JOB_MODE_INUNDATION_FROM_CACHE,
    JOB_MODE_TIDE_AND_EXPOSURE, JOB_MODE_EXPOSURE_FROM_CACHE, JOB_MODE_ALL,
    STATUS_DONE, STATUS_FAILED
)
from core.tide_cache import (
    write_tide_cache, inspect_tide_cache_metadata, is_cache_complete,
    generate_tide_cache_signature
)
from core.exposure_engine import ExposureProductPaths
from cli import build_parser


def _get_qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _create_synthetic_dem(path: str, width: int = 16, height: int = 16, nodata: float = -9999.0):
    transform = from_origin(121.5, 31.5, 0.001, 0.001)
    data = np.ones((height, width), dtype=np.float32) * 2.0
    data[0, 0] = nodata  # Include at least one nodata pixel
    profile = {
        'driver': 'GTiff',
        'height': height,
        'width': width,
        'count': 1,
        'dtype': 'float32',
        'crs': 'EPSG:4326',
        'transform': transform,
        'nodata': nodata
    }
    with rasterio.open(path, 'w', **profile) as dst:
        dst.write(data, 1)
    return profile


def _create_synthetic_tide_cache(cache_path: str, dem_info, num_nodes: int = 4, num_times: int = 48):
    nodes = {}
    node_list = []
    for i in range(num_nodes):
        x = dem_info.bounds[0] + (i % 2) * (dem_info.bounds[2] - dem_info.bounds[0])
        y = dem_info.bounds[1] + (i // 2) * (dem_info.bounds[3] - dem_info.bounds[1])
        node = ControlNode(node_id=i, x=x, y=y, lon=x, lat=y, valid=True)
        node.water_levels_sorted = np.linspace(-2.0, 2.0, num_times).astype(np.float32)
        nodes[(i, 0)] = node
        node_list.append(node)

    cell = QuadCell(
        cell_id=0,
        x_min=dem_info.bounds[0],
        y_min=dem_info.bounds[1],
        x_max=dem_info.bounds[2],
        y_max=dem_info.bounds[3],
        level=0,
        node_a=node_list[0],
        node_b=node_list[1 % num_nodes],
        node_c=node_list[2 % num_nodes],
        node_d=node_list[3 % num_nodes]
    )

    import pandas as pd
    time_index = pd.date_range("2024-01-01 00:00:00", periods=num_times, freq="30min")

    metadata = {
        "start_time": "2024-01-01 00:00:00",
        "end_time": "2024-01-02 00:00:00",
        "freq": "30min",
        "dem_datum": "egm2008",
        "target_mode": "intertidal",
        "constituents": "all",
        "source_tz": "UTC",
        "inclusive": "left",
        "initial_control_spacing_m": 4000.0,
        "min_control_spacing_m": 500.0,
        "inundation_error_tolerance_pct": 1.0,
        "topology_max_resolution_m": 100.0,
        "topology_valid_fraction_threshold": 0.5
    }

    term_tides = np.full(num_nodes, 0.5, dtype=np.float64)

    write_tide_cache(
        cache_path=cache_path,
        info=dem_info,
        leaf_cells=[cell],
        node_cache=nodes,
        time_index=time_index,
        metadata=metadata,
        allow_overwrite=True,
        tide_msl_terminal=term_tides
    )


class TestLeafCellSpatialIndex(unittest.TestCase):
    """测试 1: 验证 LeafCellSpatialIndex 空间桶索引与暴力扫描严格等价"""

    def setUp(self):
        # 构建一个包含多层级四叉树叶单元的集合
        self.cells = []
        cell_id = 0
        dummy_node = ControlNode(node_id=0, x=0.0, y=0.0, lon=0.0, lat=0.0, valid=True)
        for i in range(4):
            for j in range(4):
                min_x = i * 250.0
                max_x = (i + 1) * 250.0
                min_y = j * 250.0
                max_y = (j + 1) * 250.0
                c = QuadCell(
                    cell_id=cell_id,
                    x_min=min_x,
                    y_min=min_y,
                    x_max=max_x,
                    y_max=max_y,
                    level=1,
                    node_a=dummy_node,
                    node_b=dummy_node,
                    node_c=dummy_node,
                    node_d=dummy_node
                )
                self.cells.append(c)
                cell_id += 1
        self.index = LeafCellSpatialIndex(self.cells, bounds=(0.0, 0.0, 1000.0, 1000.0), num_buckets_per_dim=4)

    def test_exact_equivalence_various_queries(self):
        query_boxes = [
            (100.0, 100.0, 400.0, 400.0),    # 跨多个桶与单元
            (0.0, 0.0, 250.0, 250.0),        # 恰好一个单元
            (250.0, 250.0, 750.0, 750.0),    # 内部对称区间
            (999.0, 999.0, 1500.0, 1500.0),  # 部分超出外边界
            (2000.0, 2000.0, 3000.0, 3000.0),# 完全在外部
            (0.0, 0.0, 1000.0, 1000.0),      # 全局范围
        ]

        for qb in query_boxes:
            q_min_x, q_min_y, q_max_x, q_max_y = qb
            # 暴力遍历 AABB 相交 (使用统一的未分离区间准则)
            brute_cells = [
                c for c in self.cells
                if not (c.x_max < q_min_x or c.x_min > q_max_x or c.y_max < q_min_y or c.y_min > q_max_y)
            ]
            # 空间桶索引检索
            indexed_cells = self.index.query_intersecting_cells(qb)

            # 验证完全等价 (集合等价与数量等价)
            self.assertEqual(len(indexed_cells), len(brute_cells), f"Query box {qb} count mismatch")
            self.assertEqual(set(c.cell_id for c in indexed_cells), set(c.cell_id for c in brute_cells))


class TestBatchEngineUnknownMode(unittest.TestCase):
    """测试 2: 验证 BatchRasterEngine 快速拦截未知 job_mode"""

    def test_unknown_job_mode_raises_value_error(self):
        engine = BatchRasterEngine()
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError) as ctx:
                engine.run_batch(input_folder=td, output_folder=td, job_mode="invalid-mode-xyz")
            self.assertIn("未知的批量解算模式", str(ctx.exception))


class TestCliParserSpecification(unittest.TestCase):
    """测试 3: 验证 CLI 参数解析器 build_parser 的完备性与互斥约束"""

    def setUp(self):
        self.parser = build_parser()

    def test_raster_exposure_subcommand(self):
        args = self.parser.parse_args([
            "raster", "exposure",
            "--dem", "test_dem.tif",
            "--output-dir", "test_out",
            "--target-mode", "intertidal",
            "--step", "15min",
            "--overwrite"
        ])
        self.assertEqual(args.mode, "raster")
        self.assertEqual(args.raster_submode, "exposure")
        self.assertEqual(args.dem, "test_dem.tif")
        self.assertEqual(args.output_dir, "test_out")
        self.assertEqual(args.target_mode, "intertidal")
        self.assertEqual(args.step, "15min")
        self.assertTrue(args.overwrite)

    def test_raster_batch_subcommand_choices(self):
        args = self.parser.parse_args([
            "raster", "batch",
            "-i", "input_tiles",
            "--mode", "exposure-from-cache",
            "--existing-policy", "resume"
        ])
        self.assertEqual(args.mode, "exposure-from-cache")
        self.assertEqual(args.raster_submode, "batch")
        self.assertEqual(args.input_folder, "input_tiles")
        self.assertEqual(args.existing_policy, "resume")

    def test_invalid_subcommand_exits(self):
        with self.assertRaises(SystemExit):
            self.parser.parse_args(["unknown_command"])


class TestBatchFromCacheAuthoritativeSemantics(unittest.TestCase):
    """测试 4: 验证 Batch from-cache 模式权威缓存语义与 DEM 几何身份强约束"""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.in_dir = Path(self.td.name) / "input"
        self.out_dir = Path(self.td.name) / "output"
        self.in_dir.mkdir()
        self.out_dir.mkdir()

        self.dem_path = str(self.in_dir / "tile_01.tif")
        _create_synthetic_dem(self.dem_path, width=16, height=16)

        raster_eng = RasterTideEngine()
        self.dem_info = raster_eng.inspect_raster(self.dem_path, compute_valid_count=False)

        # 在输出目录预置一个合法完整的 Tide Cache
        self.cache_path = str(self.out_dir / "tile_01_tide.nc")
        _create_synthetic_tide_cache(self.cache_path, self.dem_info)

    def tearDown(self):
        self.td.cleanup()

    def test_from_cache_ignores_mismatched_gui_parameters(self):
        """权威缓存: GUI/CLI 传入的不同时间/基准参数不得使合法 Cache 失效"""
        batch_eng = BatchRasterEngine()
        res = batch_eng.run_batch(
            input_folder=str(self.in_dir),
            output_folder=str(self.out_dir),
            job_mode=JOB_MODE_INUNDATION_FROM_CACHE,
            freq="10min",            # Cache 中实际为 30min
            dem_datum="wgs84",       # Cache 中实际为 egm2008
            target_mode="standard",  # Cache 中实际为 intertidal
            year=2030,               # Cache 中实际为 2024
            existing_policy=ExistingOutputPolicy.OVERWRITE
        )
        # 应该成功运行，而非因为参数不匹配报错
        self.assertEqual(res["counts"]["failed"], 0)
        self.assertEqual(res["counts"]["completed"], 1)

        # 验证 manifest 中记录的是 Cache 的权威时间与步长
        manifest = BatchManifest(str(self.out_dir))
        manifest.load()
        entry = manifest.get_entry(self.dem_path)
        self.assertEqual(entry["time_step"], "30min")
        self.assertEqual(entry["status"], STATUS_DONE)

    def test_from_cache_rejects_incompatible_dem_geometry_even_under_overwrite(self):
        """政策无法改变科学兼容性: DEM 尺寸改变时，无论 OVERWRITE 还是 RESUME 均必须报 FAILED"""
        # 修改 DEM 为不同尺寸 (32x32)
        _create_synthetic_dem(self.dem_path, width=32, height=32)

        batch_eng = BatchRasterEngine()
        res = batch_eng.run_batch(
            input_folder=str(self.in_dir),
            output_folder=str(self.out_dir),
            job_mode=JOB_MODE_INUNDATION_FROM_CACHE,
            existing_policy=ExistingOutputPolicy.OVERWRITE
        )
        self.assertEqual(res["counts"]["failed"], 1)
        self.assertEqual(res["counts"]["completed"], 0)

        manifest = BatchManifest(str(self.out_dir))
        manifest.load()
        entry = manifest.get_entry(self.dem_path)
        self.assertEqual(entry["status"], STATUS_FAILED)
        self.assertIn("不兼容", entry["error_message"])


class TestExistingOutputPolicyConflictChecks(unittest.TestCase):
    """测试 5: 验证 ExistingOutputPolicy.ERROR_IF_EXISTS 真实冲突防线"""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.in_dir = Path(self.td.name) / "input"
        self.out_dir = Path(self.td.name) / "output"
        self.in_dir.mkdir()
        self.out_dir.mkdir()

        self.dem_path = str(self.in_dir / "tile_01.tif")
        _create_synthetic_dem(self.dem_path, width=16, height=16)

        raster_eng = RasterTideEngine()
        self.dem_info = raster_eng.inspect_raster(self.dem_path, compute_valid_count=False)

        self.cache_path = str(self.out_dir / "tile_01_tide.nc")
        _create_synthetic_tide_cache(self.cache_path, self.dem_info)

    def tearDown(self):
        self.td.cleanup()

    def test_from_cache_does_not_conflict_on_existing_cache(self):
        """在 from-cache 模式下，已存在的 Tide Cache 是输入，不触发 ERROR_IF_EXISTS 冲突"""
        batch_eng = BatchRasterEngine()
        res = batch_eng.run_batch(
            input_folder=str(self.in_dir),
            output_folder=str(self.out_dir),
            job_mode=JOB_MODE_INUNDATION_FROM_CACHE,
            existing_policy=ExistingOutputPolicy.ERROR_IF_EXISTS
        )
        # 此时输出产物尚未存在，仅 Cache 存在，应成功执行
        self.assertEqual(res["counts"]["failed"], 0)
        self.assertEqual(res["counts"]["completed"], 1)

    def test_from_cache_conflicts_when_output_tif_exists(self):
        """当输出产物已存在时，ERROR_IF_EXISTS 必须拦截并报错"""
        # 预先生成 output tif
        inund_tif = self.out_dir / "tile_01_inundation.tif"
        inund_tif.write_bytes(b"dummy_existing_content")

        batch_eng = BatchRasterEngine()
        res = batch_eng.run_batch(
            input_folder=str(self.in_dir),
            output_folder=str(self.out_dir),
            job_mode=JOB_MODE_INUNDATION_FROM_CACHE,
            existing_policy=ExistingOutputPolicy.ERROR_IF_EXISTS
        )
        self.assertEqual(res["counts"]["failed"], 1)
        self.assertEqual(res["counts"]["completed"], 0)


class TestExposureOnlyValidPixelCount(unittest.TestCase):
    """测试 6: 验证 exposure-only 批量任务正确填充 manifest.valid_pixel_count"""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.in_dir = Path(self.td.name) / "input"
        self.out_dir = Path(self.td.name) / "output"
        self.in_dir.mkdir()
        self.out_dir.mkdir()

        self.dem_path = str(self.in_dir / "tile_01.tif")
        _create_synthetic_dem(self.dem_path, width=16, height=16)

        raster_eng = RasterTideEngine()
        self.dem_info = raster_eng.inspect_raster(self.dem_path, compute_valid_count=False)

        self.cache_path = str(self.out_dir / "tile_01_tide.nc")
        _create_synthetic_tide_cache(self.cache_path, self.dem_info)

    def tearDown(self):
        self.td.cleanup()

    def test_exposure_from_cache_populates_valid_pixels(self):
        batch_eng = BatchRasterEngine()
        res = batch_eng.run_batch(
            input_folder=str(self.in_dir),
            output_folder=str(self.out_dir),
            job_mode=JOB_MODE_EXPOSURE_FROM_CACHE,
            existing_policy=ExistingOutputPolicy.OVERWRITE
        )
        self.assertEqual(res["counts"]["completed"], 1)

        manifest = BatchManifest(str(self.out_dir))
        manifest.load()
        entry = manifest.get_entry(self.dem_path)
        # 16x16 - 1 个 nodata = 255 个有效像元
        self.assertEqual(int(entry["valid_pixel_count"]), 255)


class TestGuiBatchConstraintsAndControls(unittest.TestCase):
    """测试 7: 验证 GUI 批处理控件状态在从缓存模式及任务结束后正确恢复"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        QApplication.processEvents()

    def test_apply_batch_mode_constraints(self):
        # 切换至 from-cache 模式
        self.window._apply_batch_mode_constraints("inundation-from-cache")
        self.assertFalse(self.window.grp_batch_time.isEnabled())
        self.assertFalse(self.window.grp_batch_sci.isEnabled())

        # 切换至普通模式
        self.window._apply_batch_mode_constraints("tide-inundation")
        self.assertTrue(self.window.grp_batch_time.isEnabled())
        self.assertTrue(self.window.grp_batch_sci.isEnabled())

    def test_set_batch_controls_running_restores_from_cache_state(self):
        # 在 from-cache 模式下运行并结束
        idx = self.window.cmb_batch_job_mode.findData("inundation-from-cache")
        self.window.cmb_batch_job_mode.setCurrentIndex(idx)

        # 运行前状态
        self.assertFalse(self.window.grp_batch_time.isEnabled())
        self.assertFalse(self.window.grp_batch_sci.isEnabled())

        # 启动锁定
        self.window._set_batch_controls_running(True)
        self.assertFalse(self.window.btn_start_batch.isEnabled())
        self.assertTrue(self.window.btn_cancel_batch.isEnabled())

        # 任务结束恢复
        self.window._set_batch_controls_running(False)
        self.assertTrue(self.window.btn_start_batch.isEnabled())
        self.assertFalse(self.window.btn_cancel_batch.isEnabled())
        # 核心回归点: 任务恢复后，from-cache 的时间与科学参数必须依旧保持禁用！
        self.assertFalse(self.window.grp_batch_time.isEnabled())
        self.assertFalse(self.window.grp_batch_sci.isEnabled())

    def test_tab3_target_mode_selector(self):
        self.assertTrue(hasattr(self.window, "combo_inund_target_mode"))
        self.assertEqual(self.window.combo_inund_target_mode.currentData(), "intertidal")


class TestGuiOpenDirectorySafety(unittest.TestCase):
    """测试 8: 验证 _open_directory 方法对空路径或不存在路径安全防护"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        QApplication.processEvents()

    def test_open_directory_handles_none_and_nonexistent(self):
        with patch.object(QMessageBox, "information") as mock_info:
            self.window._open_directory(None)
            self.window._open_directory("")
            self.window._open_directory("/path/to/nonexistent/directory/12345")
            self.assertEqual(mock_info.call_count, 3)

        with tempfile.TemporaryDirectory() as td:
            with patch("PyQt6.QtGui.QDesktopServices.openUrl") as mock_open:
                self.window._open_directory(td)
                mock_open.assert_called_once()


class TestDocumentationLint(unittest.TestCase):
    """测试 9: 自动化 Lint 检验文档与代码中无开发者绝对路径与非学术夸大词汇"""

    def test_readme_cleanliness(self):
        for fname in ["README.md", "README_EN.md"]:
            fpath = os.path.join(project_root, fname)
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertNotIn("I:\\Test_tide_model", content, f"{fname} 包含开发者绝对路径")
            self.assertNotIn("100~500", content, f"{fname} 包含 100~500 倍夸大修辞")
            self.assertNotIn("Stage 2c", content, f"{fname} 包含虚构的 Stage 2c 架构")

    def test_changelog_cleanliness(self):
        fpath = os.path.join(project_root, "CHANGELOG.md")
        with open(fpath, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("143 项全部通过", content, "CHANGELOG.md 包含写死的测试数量")
        self.assertNotIn("100 组独立随机时序对比测试中，与 1D 参考算法达到精确 0 误差等价", content)


if __name__ == "__main__":
    unittest.main()
