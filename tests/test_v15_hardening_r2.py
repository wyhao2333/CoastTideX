"""
CoastTideX v1.5 Alpha Hardening Round 2 自动化测试套件 (Test Suite for Hardening Round 2)
全面覆盖第 22 节 ~ 第 26 节加固需求:
  22.1 GUI Scan: 文本变更绝不自动扫描 (textChanged 不触发磁盘扫描)
  22.2 GUI Scan: 显式扫描快照无缝传递，开始计算时禁止二次磁盘扫描
  22.3 GUI Invalidation: 输入路径/输出路径/递归切换严格失效任务队列
  22.4 GUI Invalidation: 切换目录后启动防御与 ScanSnapshotStaleError
  22.5 Async Scan: BatchScanWorker 异步工作线程与无锁 UI 响应
  22.6 Duplicate Basenames: 递归多级子目录同名文件相对路径显示与精确映射
  22.7 Invalid Rasters: 损坏或缺失 CRS 的 GeoTIFF 标红为无效且不计入就绪
  23.1 Resume Compatibility: 年份不一致拦截与指导性报错
  23.2 Resume Compatibility: 采样步长不一致拦截
  23.3 Resume Compatibility: 垂直基准不一致拦截
  23.4 Resume Compatibility: 分潮方案不一致拦截
  23.5 Resume Compatibility: 空间间距/误差容限不一致拦截
  23.6 Resume Compatibility: 源 DEM 大小或修改时间篡改拦截
  23.7 Deep Artifact Verification: 虚假 DONE 拦截 (缺失或零字节产物重算)
  23.8 Topology Consistency: Stage 1 与 Stage 2 拓扑参数绝对一致与防猜机制
  24.1 Memory Safety: 8 字节/样本/节点内存预算 (双常驻数组)
  24.2 Memory Safety: Tide Cache 节点分块流式读取 (禁止全量加载)
  25.1 Time Range: 全量半开区间 [start, end) 严格点数保真
  25.2 Custom Interval: 自定义正整数时间步长解析与解算
  26.1 NoData Inheritance: 继承输入 DEM 有限 Float32 NoData (-9999.0)
  26.2 NoData Inheritance: 输入 DEM 无 NoData 时严谨回退为 Float32 NaN
"""

import os
import sys
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import from_origin

# 设置无头 Qt 环境
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

from core.raster_engine import (
    RasterTideEngine, RasterInfo, ControlNode, QuadCell, ExistingOutputError
)
from core.tide_cache import (
    write_tide_cache, read_tide_cache, is_cache_complete,
    inspect_tide_cache_metadata, validate_tide_cache_compatibility,
    generate_tide_cache_signature, calculate_inundation_from_tide_cache,
    build_expected_cache_spec, estimate_tide_cache_size,
    TideCacheCompatibilityError
)
from core.batch_raster_engine import (
    BatchRasterEngine, BatchManifest, ExistingOutputPolicy,
    ScanSnapshotStaleError,
    JOB_MODE_TIDE_ONLY, JOB_MODE_TIDE_AND_INUNDATION, JOB_MODE_INUNDATION_FROM_CACHE,
    STATUS_DONE, STATUS_FAILED, STATUS_TIDE_READY
)
from gui.main_window import MainWindow, BatchScanWorker


class MockR2Predictor:
    """轻量测试 FES 预测桩"""
    def __init__(self, base_level: float = 1.0):
        self.base_level = base_level
        self.call_count = 0

    def predict_points_period(self, lons, lats, start_time, end_time, freq="30min",
                              inclusive="left", constituents="all", source_tz="UTC",
                              max_fes_evaluate_points=None):
        self.call_count += 1
        n_pts = len(lons)
        dr = pd.date_range(start_time, end_time, freq=freq, inclusive=inclusive, tz="UTC")
        n_times = len(dr)
        tide_mat = np.full((n_pts, n_times), self.base_level, dtype=np.float32)
        flag_mat = np.ones((n_pts, n_times), dtype=np.int8)
        return tide_mat, dr, flag_mat


class TestV15HardeningRound2(unittest.TestCase):
    """CoastTideX v1.5 Alpha Hardening Round 2 自动化测试套件"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="coasttidex_r2_")
        self.pred = MockR2Predictor(base_level=1.2)
        self.engine = RasterTideEngine(
            tide_predictor=self.pred,
            initial_control_spacing_m=2000.0,
            min_control_spacing_m=500.0,
            inundation_error_tolerance_pct=1.0,
            topology_max_resolution_m=60.0,
            topology_valid_fraction_threshold=0.01
        )
        self.engine.transformer.get_static_datum_offsets = MagicMock(
            side_effect=lambda lons, lats, target, strict=True: {
                'offset_m': np.zeros(len(lons), dtype=float),
                'qc_warning': [''] * len(lons)
            }
        )
        self.batch_engine = BatchRasterEngine(raster_engine=self.engine)

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_synthetic_dem(
        self,
        path: str,
        width: int = 40,
        height: int = 40,
        res: float = 20.0,
        origin_x: float = 500000.0,
        origin_y: float = 3400000.0,
        elevation_val: float = 0.5,
        nodata: float = -9999.0,
        crs: str = "EPSG:32651"
    ):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        transform = from_origin(origin_x, origin_y, res, res)
        dem = np.full((height, width), elevation_val, dtype=np.float32)
        with rasterio.open(
            path, 'w', driver='GTiff', width=width, height=height, count=1,
            dtype='float32', crs=crs, transform=transform, nodata=nodata
        ) as dst:
            dst.write(dem, 1)

    # =========================================================================
    # Section 22: GUI Scan / Batch Queue UX / Async Scanning
    # =========================================================================

    def test_22_1_text_changed_no_auto_scan(self):
        """22.1: 修改输入文件夹路径绝不触发全量扫描，仅置无效标记"""
        win = MainWindow()
        dem_path = os.path.join(self.test_dir, "input", "test.tif")
        self._create_synthetic_dem(dem_path)

        with patch.object(self.batch_engine, "discover_rasters") as mock_disc:
            win.txt_batch_in_dir.setText(os.path.dirname(dem_path))
            self.assertEqual(mock_disc.call_count, 0)
            self.assertFalse(win.scan_fresh)
            self.assertFalse(win.btn_start_batch.isEnabled())

    def test_22_2_scan_snapshot_used_without_rescan(self):
        """22.2: 显式扫描快照无缝传递给 batch_engine，启动时禁止二次磁盘扫描"""
        in_dir = os.path.join(self.test_dir, "input")
        self._create_synthetic_dem(os.path.join(in_dir, "tile1.tif"))
        self._create_synthetic_dem(os.path.join(in_dir, "tile2.tif"))

        discovered = self.batch_engine.discover_rasters(in_dir)
        self.assertEqual(len(discovered), 2)

        out_dir = os.path.join(self.test_dir, "output")
        with patch.object(self.batch_engine, "discover_rasters") as mock_disc:
            summary = self.batch_engine.run_batch(
                input_folder=in_dir,
                output_folder=out_dir,
                discovered_files=discovered,
                job_mode=JOB_MODE_TIDE_ONLY,
                year=2024
            )
            self.assertEqual(mock_disc.call_count, 0)
            self.assertEqual(summary["total"], 2)
            c_done = summary.get("completed", summary.get("succeeded"))
            self.assertEqual(c_done, 2)

    def test_22_3_invalidation_rules(self):
        """22.3: 输入目录、输出目录或递归选项变动时，任务队列立即失效，启动按钮禁用"""
        win = MainWindow()
        win.scan_fresh = True
        win.discovered_batch_files = [{"filename": "tile.tif"}]
        win.btn_start_batch.setEnabled(True)

        win.txt_batch_in_dir.setText("C:\\new_folder")
        self.assertFalse(win.scan_fresh)
        self.assertFalse(win.btn_start_batch.isEnabled())

        win.scan_fresh = True
        win.btn_start_batch.setEnabled(True)
        win.txt_batch_out_dir.setText("C:\\custom_out")
        self.assertFalse(win.scan_fresh)
        self.assertFalse(win.btn_start_batch.isEnabled())

        win.scan_fresh = True
        win.btn_start_batch.setEnabled(True)
        win.chk_batch_recursive.setChecked(not win.chk_batch_recursive.isChecked())
        self.assertFalse(win.scan_fresh)
        self.assertFalse(win.btn_start_batch.isEnabled())

    def test_22_4_folder_switch_prevents_stale_run(self):
        """22.4: 切换文件夹导致快照过期时，严格阻断执行并抛出 ScanSnapshotStaleError"""
        dir_a = os.path.join(self.test_dir, "dir_a")
        dir_b = os.path.join(self.test_dir, "dir_b")
        self._create_synthetic_dem(os.path.join(dir_a, "tile_a.tif"))
        self._create_synthetic_dem(os.path.join(dir_b, "tile_b.tif"))

        snap_a = self.batch_engine.discover_rasters(dir_a)

        out_dir = os.path.join(self.test_dir, "output")
        with self.assertRaises(ScanSnapshotStaleError):
            self.batch_engine.run_batch(
                input_folder=dir_b,
                output_folder=out_dir,
                discovered_files=snap_a,
                job_mode=JOB_MODE_TIDE_ONLY
            )

    def test_22_5_async_scan_worker(self):
        """22.5: BatchScanWorker 继承 QThread，仅读头信息并在完成时发射 finished 信号"""
        in_dir = os.path.join(self.test_dir, "async_input")
        out_dir = os.path.join(self.test_dir, "async_output")
        self._create_synthetic_dem(os.path.join(in_dir, "test1.tif"))
        self._create_synthetic_dem(os.path.join(in_dir, "test2.tif"))

        worker = BatchScanWorker(
            in_dir=in_dir,
            out_dir=out_dir,
            recursive=False
        )

        results = []
        specs = []
        worker.finished.connect(lambda lst, sp: (results.extend(lst), specs.append(sp)))
        worker.run()

        self.assertEqual(len(results), 2)
        self.assertTrue(results[0]["valid"])
        self.assertEqual(results[0]["crs"], "EPSG:32651")
        self.assertIn("file_size_mb", results[0])
        self.assertEqual(len(specs), 1)

    def test_22_6_recursive_duplicate_basenames(self):
        """22.6: 递归多层级同名文件显示相对路径并建立 O(1) 准确映射"""
        win = MainWindow()
        in_dir = os.path.join(self.test_dir, "nested_input")
        sub1 = os.path.join(in_dir, "site_a")
        sub2 = os.path.join(in_dir, "site_b")
        self._create_synthetic_dem(os.path.join(sub1, "dem.tif"))
        self._create_synthetic_dem(os.path.join(sub2, "dem.tif"))

        discovered = self.batch_engine.discover_rasters(in_dir, recursive=True)
        self.assertEqual(len(discovered), 2)

        rel_paths = [d["relative_path"] for d in discovered]
        self.assertIn(os.path.join("site_a", "dem.tif"), rel_paths)
        self.assertIn(os.path.join("site_b", "dem.tif"), rel_paths)

        win._on_scan_finished(discovered)
        self.assertEqual(win.table_batch_rasters.rowCount(), 2)
        self.assertEqual(len(win._batch_row_by_relative_path), 2)

        p1 = os.path.join("site_a", "dem.tif")
        p2 = os.path.join("site_b", "dem.tif")
        row1 = win._batch_row_by_relative_path[p1]
        row2 = win._batch_row_by_relative_path[p2]
        self.assertNotEqual(row1, row2)

        win._on_batch_progress(50, 100, p2, "测试处理中", {"total": 2, "completed": 1, "failed": 0, "skipped": 0})
        item2 = win.table_batch_rasters.item(row2, 5)
        item1 = win.table_batch_rasters.item(row1, 5)
        self.assertIn("测试处理中", item2.text())
        self.assertNotIn("测试处理中", item1.text())

    def test_22_7_corrupt_or_missing_crs_flagged_invalid(self):
        """22.7: 损坏或缺少 CRS 的 GeoTIFF 被标记为“无效 (Invalid)”且不计入就绪数"""
        win = MainWindow()
        in_dir = os.path.join(self.test_dir, "corrupt_input")
        os.makedirs(in_dir, exist_ok=True)

        self._create_synthetic_dem(os.path.join(in_dir, "valid.tif"), crs="EPSG:32651")
        corrupt_path = os.path.join(in_dir, "corrupt.tif")
        with open(corrupt_path, "wb") as f:
            f.write(b"not a valid tiff header")

        no_crs_path = os.path.join(in_dir, "no_crs.tif")
        transform = from_origin(500000, 3400000, 20, 20)
        with rasterio.open(
            no_crs_path, 'w', driver='GTiff', width=10, height=10, count=1,
            dtype='float32', crs=None, transform=transform
        ) as dst:
            dst.write(np.zeros((10, 10), dtype=np.float32), 1)

        discovered = self.batch_engine.discover_rasters(in_dir)
        self.assertEqual(len(discovered), 3)

        win._on_scan_finished(discovered)
        self.assertIn("有效 1, 无效 2", win.lbl_batch_status.text())

        for r_idx, d in enumerate(discovered):
            item = win.table_batch_rasters.item(r_idx, 5)
            if d["filename"] == "valid.tif":
                self.assertIn("就绪", item.text())
            else:
                self.assertIn("无效", item.text())
                self.assertTrue(len(item.toolTip()) > 0)

    # =========================================================================
    # Section 23: Strict Resume Compatibility & Artifact Verification
    # =========================================================================

    def test_23_1_resume_rejects_incompatible_year(self):
        """23.1: Resume 遇到不同年份的缓存，严格报错中止并提示使用 OVERWRITE 重建"""
        in_dir = os.path.join(self.test_dir, "resume_yr_in")
        out_dir = os.path.join(self.test_dir, "resume_yr_out")
        dem_p = os.path.join(in_dir, "tile.tif")
        self._create_synthetic_dem(dem_p)

        self.batch_engine.run_batch(
            input_folder=in_dir, output_folder=out_dir,
            job_mode=JOB_MODE_TIDE_ONLY, year=2024,
            existing_policy=ExistingOutputPolicy.OVERWRITE
        )

        sum2 = self.batch_engine.run_batch(
            input_folder=in_dir, output_folder=out_dir,
            job_mode=JOB_MODE_TIDE_ONLY, year=2023,
            existing_policy=ExistingOutputPolicy.RESUME
        )
        self.assertEqual(sum2["failed"], 1)
        manifest = BatchManifest(out_dir)
        m_entry = manifest.get_entry(dem_p)
        self.assertEqual(m_entry["status"], STATUS_FAILED)
        self.assertIn("Existing cache belongs to another parameter set", m_entry["error_message"])
        self.assertIn("Use OVERWRITE to rebuild", m_entry["error_message"])

    def test_23_2_resume_rejects_incompatible_freq(self):
        """23.2: Resume 遇到不同采样步长的缓存，严格报错中止"""
        in_dir = os.path.join(self.test_dir, "resume_fq_in")
        out_dir = os.path.join(self.test_dir, "resume_fq_out")
        dem_p = os.path.join(in_dir, "tile.tif")
        self._create_synthetic_dem(dem_p)

        self.batch_engine.run_batch(
            input_folder=in_dir, output_folder=out_dir,
            job_mode=JOB_MODE_TIDE_ONLY, freq="30min"
        )
        sum2 = self.batch_engine.run_batch(
            input_folder=in_dir, output_folder=out_dir,
            job_mode=JOB_MODE_TIDE_ONLY, freq="1h",
            existing_policy=ExistingOutputPolicy.RESUME
        )
        self.assertEqual(sum2["failed"], 1)
        m_entry = BatchManifest(out_dir).get_entry(dem_p)
        self.assertIn("Existing cache belongs to another parameter set", m_entry["error_message"])
        self.assertTrue("采样步长不匹配" in m_entry["error_message"] or "30min" in m_entry["error_message"])

    def test_23_3_resume_rejects_incompatible_datum(self):
        """23.3: Resume 遇到不同基准的缓存，严格报错中止"""
        in_dir = os.path.join(self.test_dir, "resume_dt_in")
        out_dir = os.path.join(self.test_dir, "resume_dt_out")
        dem_p = os.path.join(in_dir, "tile.tif")
        self._create_synthetic_dem(dem_p)

        self.batch_engine.run_batch(
            input_folder=in_dir, output_folder=out_dir,
            job_mode=JOB_MODE_TIDE_ONLY, dem_datum="egm2008"
        )
        sum2 = self.batch_engine.run_batch(
            input_folder=in_dir, output_folder=out_dir,
            job_mode=JOB_MODE_TIDE_ONLY, dem_datum="wgs84",
            existing_policy=ExistingOutputPolicy.RESUME
        )
        self.assertEqual(sum2["failed"], 1)
        m_entry = BatchManifest(out_dir).get_entry(dem_p)
        self.assertIn("Existing cache belongs to another parameter set", m_entry["error_message"])
        self.assertTrue("垂直基准不匹配" in m_entry["error_message"] or "egm2008" in m_entry["error_message"])

    def test_23_4_resume_rejects_incompatible_constituents(self):
        """23.4: Resume 遇到不同分潮方案的缓存，严格报错中止"""
        in_dir = os.path.join(self.test_dir, "resume_cn_in")
        out_dir = os.path.join(self.test_dir, "resume_cn_out")
        dem_p = os.path.join(in_dir, "tile.tif")
        self._create_synthetic_dem(dem_p)

        self.batch_engine.run_batch(
            input_folder=in_dir, output_folder=out_dir,
            job_mode=JOB_MODE_TIDE_ONLY, constituents="all"
        )
        sum2 = self.batch_engine.run_batch(
            input_folder=in_dir, output_folder=out_dir,
            job_mode=JOB_MODE_TIDE_ONLY, constituents="major8",
            existing_policy=ExistingOutputPolicy.RESUME
        )
        self.assertEqual(sum2["failed"], 1)
        m_entry = BatchManifest(out_dir).get_entry(dem_p)
        self.assertIn("Existing cache belongs to another parameter set", m_entry["error_message"])
        self.assertTrue("分潮方案不匹配" in m_entry["error_message"] or "major8" in m_entry["error_message"])

    def test_23_5_resume_rejects_incompatible_spacing_tolerance(self):
        """23.5: Resume 遇到不同自适应间距或误差阈值的缓存，严格报错中止"""
        in_dir = os.path.join(self.test_dir, "resume_sp_in")
        out_dir = os.path.join(self.test_dir, "resume_sp_out")
        dem_p = os.path.join(in_dir, "tile.tif")
        self._create_synthetic_dem(dem_p)

        self.batch_engine.run_batch(
            input_folder=in_dir, output_folder=out_dir,
            job_mode=JOB_MODE_TIDE_ONLY, initial_control_spacing_m=2000.0
        )
        sum2 = self.batch_engine.run_batch(
            input_folder=in_dir, output_folder=out_dir,
            job_mode=JOB_MODE_TIDE_ONLY, initial_control_spacing_m=4000.0,
            existing_policy=ExistingOutputPolicy.RESUME
        )
        self.assertEqual(sum2["failed"], 1)
        m_entry = BatchManifest(out_dir).get_entry(dem_p)
        self.assertIn("Existing cache belongs to another parameter set", m_entry["error_message"])
        self.assertTrue("控制点间距不匹配" in m_entry["error_message"] or "2000" in m_entry["error_message"])

    def test_23_6_resume_rejects_modified_source_dem(self):
        """23.6: 源 DEM 文件大小或修改时间被变动后，缓存防线严格识别并不予复用"""
        in_dir = os.path.join(self.test_dir, "resume_dem_in")
        out_dir = os.path.join(self.test_dir, "resume_dem_out")
        dem_p = os.path.join(in_dir, "tile.tif")
        self._create_synthetic_dem(dem_p)

        self.batch_engine.run_batch(
            input_folder=in_dir, output_folder=out_dir,
            job_mode=JOB_MODE_TIDE_ONLY
        )

        time.sleep(0.01)
        with open(dem_p, "ab") as f:
            f.write(b"12345678")

        sum2 = self.batch_engine.run_batch(
            input_folder=in_dir, output_folder=out_dir,
            job_mode=JOB_MODE_TIDE_ONLY,
            existing_policy=ExistingOutputPolicy.RESUME
        )
        self.assertEqual(sum2["failed"], 1)
        m_entry = BatchManifest(out_dir).get_entry(dem_p)
        self.assertIn("Existing cache belongs to another parameter set", m_entry["error_message"])
        self.assertTrue("源 DEM" in m_entry["error_message"] or "不匹配" in m_entry["error_message"])

    def test_23_7_deep_artifact_verification(self):
        """23.7: 磁盘物理产物深层校验: 即使 manifest 记录 DONE，若产物损坏/缺失绝不盲目跳过"""
        in_dir = os.path.join(self.test_dir, "deep_art_in")
        out_dir = os.path.join(self.test_dir, "deep_art_out")
        dem_p = os.path.join(in_dir, "tile.tif")
        self._create_synthetic_dem(dem_p)

        self.batch_engine.run_batch(
            input_folder=in_dir, output_folder=out_dir,
            job_mode=JOB_MODE_TIDE_AND_INUNDATION
        )

        freq_p = os.path.join(out_dir, "tile_inundation.tif")
        self.assertTrue(os.path.exists(freq_p))

        with open(freq_p, "wb") as f:
            f.write(b"")

        sum2 = self.batch_engine.run_batch(
            input_folder=in_dir, output_folder=out_dir,
            job_mode=JOB_MODE_TIDE_AND_INUNDATION,
            existing_policy=ExistingOutputPolicy.RESUME
        )
        c_done = sum2.get("completed", sum2.get("succeeded"))
        self.assertEqual(c_done, 1)
        self.assertGreater(os.path.getsize(freq_p), 100)

    def test_23_8_stage1_stage2_topology_parameters_identical(self):
        """23.8: Stage 1 写入的拓扑参数必须完整被 Stage 2 读取，缺失时抛出 TideCacheCompatibilityError"""
        in_dir = os.path.join(self.test_dir, "topo_in")
        out_dir = os.path.join(self.test_dir, "topo_out")
        dem_p = os.path.join(in_dir, "tile.tif")
        self._create_synthetic_dem(dem_p)

        self.batch_engine.run_batch(
            input_folder=in_dir, output_folder=out_dir,
            job_mode=JOB_MODE_TIDE_ONLY
        )
        cache_p = os.path.join(out_dir, "tile_tide.nc")
        meta = inspect_tide_cache_metadata(cache_p)
        self.assertEqual(meta["metadata"]["TOPOLOGY_RESOLUTION_M"], 60.0)
        self.assertEqual(meta["metadata"]["TOPOLOGY_VALID_FRACTION_THRESHOLD"], 0.01)
        self.assertEqual(meta["metadata"]["TOPOLOGY_SOURCE"], "target_mask_derived")

        sum2 = self.batch_engine.run_batch(
            input_folder=in_dir, output_folder=out_dir,
            job_mode=JOB_MODE_INUNDATION_FROM_CACHE,
            existing_policy=ExistingOutputPolicy.OVERWRITE
        )
        c_done = sum2.get("completed", sum2.get("succeeded"))
        self.assertEqual(c_done, 1)

    # =========================================================================
    # Section 24: Memory Budget Safety & Chunked Reading
    # =========================================================================

    def test_24_1_memory_budget_8bytes_per_sample_node(self):
        """24.1: 内存预算模型基于双常驻数组 (8 字节/样本/节点) 准确估算"""
        est = estimate_tide_cache_size(node_count=100, time_samples=17568, resident_array_count=2)
        expected_bytes = 100 * 17568 * 8
        self.assertEqual(est["raw_bytes"], expected_bytes)
        self.assertEqual(est["resident_array_count"], 2)
        self.assertAlmostEqual(est["raw_mb"], expected_bytes / (1024**2), places=2)

    def test_24_2_tide_cache_chunked_reading(self):
        """24.2: read_tide_cache 支持节点分块读取，禁止一次性全部切片加载入内存"""
        in_dir = os.path.join(self.test_dir, "chunk_in")
        out_dir = os.path.join(self.test_dir, "chunk_out")
        dem_p = os.path.join(in_dir, "tile.tif")
        self._create_synthetic_dem(dem_p)

        self.batch_engine.run_batch(
            input_folder=in_dir, output_folder=out_dir,
            job_mode=JOB_MODE_TIDE_ONLY
        )
        cache_p = os.path.join(out_dir, "tile_tide.nc")
        data = read_tide_cache(cache_p, chunk_node_size=1)
        self.assertIn("nodes", data)
        self.assertEqual(len(data["nodes"]), 5)
        self.assertEqual(len(data["nodes"][0].water_levels_sorted), 17568)
        self.assertTrue(np.all(np.isfinite(data["nodes"][0].water_levels_sorted)))

    # =========================================================================
    # Section 25: Uniform Half-Open Time Interval & Custom Interval
    # =========================================================================

    def test_25_1_uniform_half_open_time_interval(self):
        """25.1: 批处理引擎时间序列严格采用 [start, end) 半开区间，保证样本数精确闭合"""
        dem_p = os.path.join(self.test_dir, "time_dem.tif")
        self._create_synthetic_dem(dem_p)
        info = self.engine.inspect_raster(dem_p)

        spec = build_expected_cache_spec(
            info=info,
            start_time="2024-01-01 00:00:00",
            end_time="2025-01-01 00:00:00",
            freq="30min",
            inclusive="left"
        )
        self.assertEqual(spec["time_samples"], 17568)
        self.assertEqual(spec["inclusive"], "left")

    def test_25_2_custom_time_interval_parsing(self):
        """25.2: 支持正整数分钟/小时自定义步长 (如 45min, 3h)"""
        win = MainWindow()
        with patch("PyQt6.QtWidgets.QInputDialog.getText", return_value=("45min", True)):
            win.cmb_batch_step.setCurrentIndex(5)  # "Custom... (自定义)"
            self.assertEqual(win._get_batch_frequency(), "45min")

        win.cmb_batch_step.setCurrentIndex(0)
        with patch("PyQt6.QtWidgets.QInputDialog.getText", return_value=("3h", True)):
            win.cmb_batch_step.setCurrentIndex(5)
            self.assertEqual(win._get_batch_frequency(), "3h")

        win.cmb_batch_step.setCurrentIndex(0)
        with patch("PyQt6.QtWidgets.QInputDialog.getText", return_value=("invalid_step", True)):
            with patch("PyQt6.QtWidgets.QMessageBox.warning") as mock_warn:
                win.cmb_batch_step.setCurrentIndex(5)
                self.assertEqual(win._get_batch_frequency(), "30min")
                self.assertTrue(mock_warn.called)

    # =========================================================================
    # Section 26: NoData Inheritance Standard
    # =========================================================================

    def test_26_1_nodata_inheritance_finite_float32(self):
        """26.1: 输入 DEM 为有限 Float32 NoData (-9999.0) 时，淹没频率栅格继承 -9999.0"""
        in_p = os.path.join(self.test_dir, "dem_finite_nodata.tif")
        out_p = os.path.join(self.test_dir, "freq_finite_nodata.tif")
        qc_p = os.path.join(self.test_dir, "qc_finite_nodata.tif")

        transform = from_origin(500000, 3400000, 20, 20)
        dem = np.full((20, 20), 1.0, dtype=np.float32)
        dem[:, :10] = -9999.0

        with rasterio.open(
            in_p, 'w', driver='GTiff', width=20, height=20, count=1,
            dtype='float32', crs='EPSG:32651', transform=transform, nodata=-9999.0
        ) as dst:
            dst.write(dem, 1)

        self.engine.calculate_inundation_raster(
            dem_path=in_p,
            output_path=out_p,
            qc_output_path=qc_p,
            year=2024,
            freq="6h"
        )

        with rasterio.open(out_p) as src:
            self.assertEqual(src.nodata, -9999.0)
            data = src.read(1)
            np.testing.assert_allclose(data[:, :10], -9999.0)
            self.assertTrue(np.all(np.isfinite(data[:, 10:])))

    def test_26_2_nodata_inheritance_nan_fallback(self):
        """26.2: 输入 DEM 无 NoData 或为 NaN 时，淹没频率栅格严谨填入 Float32 NaN"""
        in_p = os.path.join(self.test_dir, "dem_nan_nodata.tif")
        out_p = os.path.join(self.test_dir, "freq_nan_nodata.tif")
        qc_p = os.path.join(self.test_dir, "qc_nan_nodata.tif")

        transform = from_origin(500000, 3400000, 20, 20)
        dem = np.full((20, 20), 1.0, dtype=np.float32)
        dem[:, :10] = np.nan

        with rasterio.open(
            in_p, 'w', driver='GTiff', width=20, height=20, count=1,
            dtype='float32', crs='EPSG:32651', transform=transform, nodata=None
        ) as dst:
            dst.write(dem, 1)

        self.engine.calculate_inundation_raster(
            dem_path=in_p,
            output_path=out_p,
            qc_output_path=qc_p,
            year=2024,
            freq="6h"
        )

        with rasterio.open(out_p) as src:
            self.assertTrue(np.isnan(src.nodata))
            data = src.read(1)
            self.assertTrue(np.all(np.isnan(data[:, :10])))
            self.assertTrue(np.all(np.isfinite(data[:, 10:])))


if __name__ == "__main__":
    unittest.main()
