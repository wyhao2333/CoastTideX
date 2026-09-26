"""
CoastTideX v1.7.1 单元测试套件:
批量 DEM 垂直基准转换与流式调度增强验证
(Unit tests for Batch DEM Datum Conversion & Streaming Engine)

覆盖范围:
1. 目录扫描与过滤 (scan_dem_directory, 递归、后缀、自动排除 _MSL, _qc, .tmp)；
2. 单瓦片流式基准转换 (Single tile conversion, 内存隔离)；
3. 多瓦片与子目录结构保持 (Multiple tiles & subfolder preservation)；
4. 断点续传 (Resume capability, 跳过已有产物)；
5. MSL 标签防呆拦截 (Skip MSL input with DATUM=MSL)；
6. 清单管理与持久化 (conversion_manifest.csv / json, 12 个关键字段)；
7. 故障隔离机制 (Failed tile isolation, 单瓦片异常不中断批处理)；
8. CLI convert-dem-batch 命令行解析与参数绑定；
9. GUI 异步工作线程与取消支持 (BatchDEMDatumConversionWorker).
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import numpy as np

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

try:
    import rasterio
    from rasterio.transform import from_bounds
    HAS_RASTERIO = True
except ImportError:
    HAS_RASTERIO = False

try:
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import Qt
    HAS_PYQT6 = True
except ImportError:
    HAS_PYQT6 = False

from core.batch_datum_converter import (
    BatchDEMDatumConverter,
    BatchConversionManifest,
    BatchConversionSummary,
    scan_dem_directory,
    is_dem_already_msl,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_SUCCESS,
    STATUS_FAILED,
    STATUS_SKIPPED_MSL,
    STATUS_SKIPPED_RESUME,
)
from core.dem_datum_converter import DEMConversionSummary
from cli import build_parser


@unittest.skipUnless(HAS_RASTERIO, "需要 rasterio 环境")
class TestBatchDEMConversion(unittest.TestCase):
    """批量 DEM 基准转换核心业务逻辑测试"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)
        self.in_dir = self.tmp_path / "input_dems"
        self.out_dir = self.tmp_path / "output_dems"
        self.in_dir.mkdir(parents=True, exist_ok=True)
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def _create_synthetic_dem(self, rel_path: str, is_msl: bool = False, nodata: float = -9999.0) -> Path:
        """生成合成 DEM GeoTIFF 测试影像"""
        p = self.in_dir / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)

        transform = from_bounds(121.5, 31.0, 121.8, 31.3, 20, 20)
        data = np.ones((1, 20, 20), dtype=np.float32) * 5.0
        data[0, 0:2, 0:2] = nodata

        tags = {}
        if is_msl:
            tags["DATUM"] = "MSL"
            tags["TARGET_VERTICAL_DATUM"] = "MSL"
            tags["ANALYSIS_REFERENCE"] = "MSL"
        else:
            tags["DATUM"] = "EGM2008"

        with rasterio.open(
            str(p), "w",
            driver="GTiff",
            height=20,
            width=20,
            count=1,
            dtype=rasterio.float32,
            crs="EPSG:4326",
            transform=transform,
            nodata=nodata
        ) as dst:
            dst.write(data)
            dst.update_tags(**tags)
        return p

    def _mock_dem_summary(self, in_p: str, out_p: str) -> DEMConversionSummary:
        return DEMConversionSummary(
            input_path=in_p,
            output_path=out_p,
            qc_output_path=None,
            width=20,
            height=20,
            total_pixels=400,
            valid_dem_pixels=396,
            native_mdt_pixels=300,
            extrapolated_mdt_pixels=96,
            nodata_pixels=4,
            max_extrapolation_distance_km=100.0,
            elapsed_seconds=0.15
        )

    def test_01_directory_scanning(self):
        """测试 1: 目录扫描、后缀识别与中间产物自动排除"""
        self._create_synthetic_dem("tile_1.tif")
        self._create_synthetic_dem("tile_2.tiff")
        self._create_synthetic_dem("sub1/tile_3.tif")
        self._create_synthetic_dem("sub1/sub2/tile_4.tif")

        # 干扰产物，必须自动排除
        self._create_synthetic_dem("tile_1_MSL.tif")
        self._create_synthetic_dem("sub1/tile_3_qc.tif")
        (self.in_dir / "temp.tmp.tif").write_text("dummy")
        (self.in_dir / "notes.txt").write_text("readme")

        # 递归扫描
        tiles_rec = scan_dem_directory(str(self.in_dir), recursive=True)
        self.assertEqual(len(tiles_rec), 4)
        names = [Path(t).name for t in tiles_rec]
        self.assertIn("tile_1.tif", names)
        self.assertIn("tile_2.tiff", names)
        self.assertIn("tile_3.tif", names)
        self.assertIn("tile_4.tif", names)
        self.assertNotIn("tile_1_MSL.tif", names)
        self.assertNotIn("tile_3_qc.tif", names)

        # 非递归扫描
        tiles_flat = scan_dem_directory(str(self.in_dir), recursive=False)
        self.assertEqual(len(tiles_flat), 2)
        names_flat = [Path(t).name for t in tiles_flat]
        self.assertIn("tile_1.tif", names_flat)
        self.assertIn("tile_2.tiff", names_flat)

        # 目录不存在
        with self.assertRaises(FileNotFoundError):
            scan_dem_directory(str(self.tmp_path / "non_existing_dir"))

    def test_02_single_tile_conversion(self):
        """测试 2: 单个瓦片流式基准转换与产物结构"""
        tile_path = self._create_synthetic_dem("single_tile.tif")
        converter = BatchDEMDatumConverter(max_extrapolation_distance_km=100.0, block_size=128)

        with patch.object(converter.converter, "convert_raster", side_effect=lambda **kwargs: self._mock_dem_summary(kwargs["input_dem_path"], kwargs["output_msl_path"])):
            summary = converter.run_batch(
                input_dir=str(self.in_dir),
                output_dir=str(self.out_dir),
                resume=False,
                overwrite=True
            )

            self.assertEqual(summary.total_tiles, 1)
            self.assertEqual(summary.success_count, 1)
            self.assertEqual(summary.failed_count, 0)
            self.assertEqual(summary.skipped_msl_count, 0)
            self.assertEqual(summary.skipped_resume_count, 0)

            # 验证清单记录
            manifest = BatchConversionManifest(str(self.out_dir))
            entry = manifest.get_entry(str(tile_path.resolve()))
            self.assertIsNotNone(entry)
            self.assertEqual(entry["status"], STATUS_SUCCESS)
            self.assertEqual(int(entry["total_pixels"]), 400)
            self.assertEqual(int(entry["valid_pixels"]), 396)

    def test_03_multiple_tiles_and_subdirectories(self):
        """测试 3: 多瓦片与子目录层级保持"""
        self._create_synthetic_dem("zone_a/tile_a.tif")
        self._create_synthetic_dem("zone_b/tile_b.tif")

        converter = BatchDEMDatumConverter()
        with patch.object(converter.converter, "convert_raster", side_effect=lambda **kwargs: self._mock_dem_summary(kwargs["input_dem_path"], kwargs["output_msl_path"])):
            summary = converter.run_batch(
                input_dir=str(self.in_dir),
                output_dir=str(self.out_dir),
                resume=True,
                overwrite=False
            )

            self.assertEqual(summary.total_tiles, 2)
            self.assertEqual(summary.success_count, 2)

            # 检查输出目录子结构保持
            manifest = BatchConversionManifest(str(self.out_dir))
            records = manifest.records
            self.assertEqual(len(records), 2)
            for inp_file, rec in records.items():
                self.assertEqual(rec["status"], STATUS_SUCCESS)
                self.assertIn("_MSL.tif", rec["output_file"])

    def test_04_resume_skips_completed_tiles(self):
        """测试 4: 断点续传功能 (Resume Capability)"""
        t1 = self._create_synthetic_dem("tile_1.tif")
        t2 = self._create_synthetic_dem("tile_2.tif")

        converter = BatchDEMDatumConverter()

        call_count = 0
        def fake_convert(**kwargs):
            nonlocal call_count
            call_count += 1
            out_p = Path(kwargs["output_msl_path"])
            out_p.parent.mkdir(parents=True, exist_ok=True)
            out_p.write_text("fake geotiff content")
            return self._mock_dem_summary(kwargs["input_dem_path"], str(out_p))

        with patch.object(converter.converter, "convert_raster", side_effect=fake_convert):
            # 第一轮：完全处理 2 个文件
            s1 = converter.run_batch(str(self.in_dir), str(self.out_dir), resume=True)
            self.assertEqual(s1.success_count, 2)
            self.assertEqual(call_count, 2)

            # 第二轮：resume=True，两个均应被跳过
            s2 = converter.run_batch(str(self.in_dir), str(self.out_dir), resume=True)
            self.assertEqual(s2.skipped_resume_count, 2)
            self.assertEqual(s2.success_count, 0)
            self.assertEqual(call_count, 2, "断点恢复模式下不应再次调用转换函数")

    def test_05_skip_msl_input(self):
        """测试 5: 输入 DEM 已经是 MSL 基准时的防呆跳过 (DATUM=MSL)"""
        t_egm = self._create_synthetic_dem("standard_egm.tif", is_msl=False)
        t_msl = self._create_synthetic_dem("msl_tagged_tile.tif", is_msl=True)

        is_msl_detected, reason = is_dem_already_msl(str(t_msl))
        self.assertTrue(is_msl_detected)
        self.assertIn("MSL", reason)

        converter = BatchDEMDatumConverter()
        with patch.object(converter.converter, "convert_raster", side_effect=lambda **kwargs: self._mock_dem_summary(kwargs["input_dem_path"], kwargs["output_msl_path"])):
            summary = converter.run_batch(str(self.in_dir), str(self.out_dir), resume=False)
            self.assertEqual(summary.total_tiles, 2)
            self.assertEqual(summary.success_count, 1)
            self.assertEqual(summary.skipped_msl_count, 1)

            manifest = BatchConversionManifest(str(self.out_dir))
            msl_entry = manifest.get_entry(str(t_msl.resolve()))
            self.assertEqual(msl_entry["status"], STATUS_SKIPPED_MSL)

    def test_06_manifest_generation_and_integrity(self):
        """测试 6: Manifest 清单 CSV 与 JSON 双格式持久化及字段完整性"""
        t1 = self._create_synthetic_dem("manifest_test.tif")
        converter = BatchDEMDatumConverter()

        with patch.object(converter.converter, "convert_raster", side_effect=lambda **kwargs: self._mock_dem_summary(kwargs["input_dem_path"], kwargs["output_msl_path"])):
            summary = converter.run_batch(str(self.in_dir), str(self.out_dir))

            csv_file = Path(summary.manifest_csv)
            json_file = Path(summary.manifest_json)
            self.assertTrue(csv_file.exists())
            self.assertTrue(json_file.exists())

            # 校验 CSV 表头包含全部 12 个字段
            with open(csv_file, "r", encoding="utf-8") as f:
                header = f.readline().strip().split(",")
            for field in BatchConversionManifest.FIELDS:
                self.assertIn(field, header)

    def test_07_failed_tile_isolation(self):
        """测试 7: 瓦片异常故障隔离 (单文件失败不中断批处理流)"""
        t_good = self._create_synthetic_dem("good_tile.tif")
        t_bad = self._create_synthetic_dem("bad_tile.tif")

        converter = BatchDEMDatumConverter()

        def mock_error_or_ok(**kwargs):
            if "bad_tile" in kwargs["input_dem_path"]:
                raise RuntimeError("测试模拟栅格损坏异常")
            return self._mock_dem_summary(kwargs["input_dem_path"], kwargs["output_msl_path"])

        with patch.object(converter.converter, "convert_raster", side_effect=mock_error_or_ok):
            summary = converter.run_batch(str(self.in_dir), str(self.out_dir))

            self.assertEqual(summary.total_tiles, 2)
            self.assertEqual(summary.success_count, 1)
            self.assertEqual(summary.failed_count, 1)

            manifest = BatchConversionManifest(str(self.out_dir))
            bad_entry = manifest.get_entry(str(t_bad.resolve()))
            self.assertEqual(bad_entry["status"], STATUS_FAILED)
            self.assertIn("测试模拟栅格损坏异常", bad_entry["error_message"])

            good_entry = manifest.get_entry(str(t_good.resolve()))
            self.assertEqual(good_entry["status"], STATUS_SUCCESS)

    def test_08_cli_convert_dem_batch(self):
        """测试 8: CLI convert-dem-batch 命令参数解析与调用契约"""
        parser = build_parser()
        args = parser.parse_args([
            "convert-dem-batch",
            "--input-dir", str(self.in_dir),
            "--output-dir", str(self.out_dir),
            "--max-dist-km", "80.0",
            "--workers", "2",
            "--resume",
            "--overwrite"
        ])

        self.assertEqual(args.mode, "convert-dem-batch")
        self.assertEqual(args.input_dir, str(self.in_dir))
        self.assertEqual(args.output_dir, str(self.out_dir))
        self.assertEqual(args.max_dist_km, 80.0)
        self.assertEqual(args.workers, 2)
        self.assertTrue(args.resume)
        self.assertTrue(args.overwrite)

    @unittest.skipUnless(HAS_PYQT6, "需要 PyQt6 环境")
    def test_09_gui_batch_worker_and_cancellation(self):
        """测试 9: GUI 异步工作线程 BatchDEMDatumConversionWorker 及取消机制"""
        from gui.main_window import BatchDEMDatumConversionWorker

        self._create_synthetic_dem("gui_tile_1.tif")
        params = {
            "input_dir": str(self.in_dir),
            "output_dir": str(self.out_dir),
            "max_dist_km": 100.0,
            "block_size": 256,
            "workers": 1,
            "resume": True,
            "overwrite": False
        }

        worker = BatchDEMDatumConversionWorker(params)
        self.assertFalse(worker._is_cancelled)
        worker.cancel()
        self.assertTrue(worker._is_cancelled)
        self.assertTrue(worker.cancel_event.is_set())


if __name__ == "__main__":
    unittest.main()
