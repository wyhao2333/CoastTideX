"""
CoastTideX v1.5 Alpha Round 2.1 自动化测试套件
验证数据源科学语义澄清、Help/About与作者信息更新、扫描防竞态、产物深层校验及 NoData 冲突保护。
"""

import os
import sys
import time
import yaml
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import from_origin
import netCDF4 as nc

os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtCore import Qt

from core.utils import load_app_config, resolve_project_path
from gui.settings_dialog import _deep_validate_file, SettingsDialog
from gui.main_window import MainWindow, BatchScanWorker
from core.raster_engine import (
    RasterTideEngine, RasterInfo, ControlNode, QuadCell,
    stream_inundation_frequency_interpolation
)
from core.batch_raster_engine import (
    BatchRasterEngine, _verify_raster_artifacts
)


class TestV15Round21DataSourceHelp(unittest.TestCase):
    """CoastTideX v1.5 Alpha Round 2.1 数据源与帮助清理测试套件"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="coasttidex_r21_")
        self.engine = RasterTideEngine()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_synthetic_dem(self, filename: str, nodata: float = -9999.0, width: int = 64, height: int = 64, dtype="float32"):
        p = os.path.join(self.test_dir, filename)
        transform = from_origin(120.0, 30.0, 0.001, 0.001)
        data = np.full((height, width), 2.0, dtype=dtype)
        data[0:10, 0:10] = nodata
        profile = {
            'driver': 'GTiff',
            'height': height,
            'width': width,
            'count': 1,
            'dtype': dtype,
            'crs': 'EPSG:4326',
            'transform': transform,
            'nodata': nodata
        }
        with rasterio.open(p, 'w', **profile) as dst:
            dst.write(data, 1)
        return p

    def test_01_settings_deep_validate_fes_mask_valid(self):
        """测试 1: 验证规范的 FES2022b 掩膜 NetCDF 文件 (含 mask, lat, lon 变量且值为 0,1,2,3)"""
        nc_path = os.path.join(self.test_dir, "test_fes_mask_valid.nc")
        ds = nc.Dataset(nc_path, "w", format="NETCDF4")
        ds.createDimension("lat", 4)
        ds.createDimension("lon", 4)
        lat_v = ds.createVariable("lat", "f4", ("lat",))
        lon_v = ds.createVariable("lon", "f4", ("lon",))
        mask_v = ds.createVariable("mask", "i2", ("lat", "lon"))
        lat_v[:] = [10.0, 11.0, 12.0, 13.0]
        lon_v[:] = [100.0, 101.0, 102.0, 103.0]
        mask_v[:] = np.array([[0, 1, 2, 3], [0, 0, 1, 2], [2, 2, 3, 3], [1, 1, 0, 0]], dtype=np.int16)
        ds.close()

        ok, msg = _deep_validate_file(nc_path, "fes_mask")
        self.assertTrue(ok)
        self.assertIn("正常", msg)
        self.assertIn("[0, 1, 2, 3]", msg)

    def test_02_settings_deep_validate_fes_mask_missing_var(self):
        """测试 2: FES2022b 掩膜缺少 mask 变量时深度校验报错"""
        nc_path = os.path.join(self.test_dir, "test_fes_mask_no_mask.nc")
        ds = nc.Dataset(nc_path, "w", format="NETCDF4")
        ds.createDimension("lat", 4)
        ds.createDimension("lon", 4)
        lat_v = ds.createVariable("lat", "f4", ("lat",))
        lon_v = ds.createVariable("lon", "f4", ("lon",))
        lat_v[:] = [10.0, 11.0, 12.0, 13.0]
        lon_v[:] = [100.0, 101.0, 102.0, 103.0]
        ds.close()

        ok, msg = _deep_validate_file(nc_path, "fes_mask")
        self.assertFalse(ok)
        self.assertIn("缺少关键变量: mask", msg)

    def test_03_settings_deep_validate_fes_mask_invalid_classes(self):
        """测试 3: FES2022b 掩膜包含非预期类别 (如 99) 深度校验报错"""
        nc_path = os.path.join(self.test_dir, "test_fes_mask_invalid_val.nc")
        ds = nc.Dataset(nc_path, "w", format="NETCDF4")
        ds.createDimension("lat", 2)
        ds.createDimension("lon", 2)
        ds.createVariable("lat", "f4", ("lat",))
        ds.createVariable("lon", "f4", ("lon",))
        mask_v = ds.createVariable("mask", "i2", ("lat", "lon"))
        mask_v[:] = np.array([[0, 1], [2, 99]], dtype=np.int16)
        ds.close()

        ok, msg = _deep_validate_file(nc_path, "fes_mask")
        self.assertFalse(ok)
        self.assertIn("包含非预期类别", msg)

    def test_04_settings_deep_validate_source_mask_valid(self):
        """测试 4: 规范的 Hybrid MDT 来源掩膜 GeoTIFF (类目在 0, 1, 2, 3, 255 内) 深度校验正常"""
        tif_path = os.path.join(self.test_dir, "test_source_mask_valid.tif")
        transform = from_origin(0.0, 40.0, 0.1, 0.1)
        data = np.array([[1, 2], [3, 255]], dtype=np.uint8)
        profile = {
            'driver': 'GTiff',
            'height': 2,
            'width': 2,
            'count': 1,
            'dtype': 'uint8',
            'crs': 'EPSG:4326',
            'transform': transform,
            'nodata': 255
        }
        with rasterio.open(tif_path, 'w', **profile) as dst:
            dst.write(data, 1)

        ok, msg = _deep_validate_file(tif_path, "source_mask")
        self.assertTrue(ok)
        self.assertIn("正常", msg)

    def test_05_settings_deep_validate_source_mask_invalid_classes(self):
        """测试 5: Hybrid MDT 来源掩膜包含非法类目 (如 4, 88) 深度校验报错"""
        tif_path = os.path.join(self.test_dir, "test_source_mask_invalid.tif")
        transform = from_origin(0.0, 40.0, 0.1, 0.1)
        data = np.array([[1, 2], [4, 88]], dtype=np.uint8)
        profile = {
            'driver': 'GTiff',
            'height': 2,
            'width': 2,
            'count': 1,
            'dtype': 'uint8',
            'crs': 'EPSG:4326',
            'transform': transform,
            'nodata': 255
        }
        with rasterio.open(tif_path, 'w', **profile) as dst:
            dst.write(data, 1)

        ok, msg = _deep_validate_file(tif_path, "source_mask")
        self.assertFalse(ok)
        self.assertIn("包含非法类别", msg)

    def test_06_settings_dialog_optional_fields_message(self):
        """测试 6: SettingsDialog 校验未配置的可选字段时正常显示 ℹ️ 未配置（可选...）且不崩溃"""
        dlg = SettingsDialog()
        # 验证默认值初始化为规范相对路径
        self.assertEqual(dlg.edit_delta_n_eigen.text(), "data/geoid/delta_n_eigen6c4_minus_egm2008.tif")

        # 当文件不存在时深度校验给出友好指引与预警
        dlg.edit_delta_n_eigen.setText("data/geoid/non_existent_delta_n_eigen.tif")
        with patch.object(QMessageBox, 'information') as mock_info:
            dlg._validate_paths()
            mock_info.assert_called_once()
            called_msg = mock_info.call_args[0][2]
            self.assertIn("未找到 EIGEN-6C4–EGM2008 ΔN 文件", called_msg)
            self.assertIn("普通全球大洋 GOCO06s 区域不受影响", called_msg)
            self.assertIn("scripts/generate_delta_n.py", called_msg)

        # 当用户显式清空时提示未配置
        dlg.edit_fes_mask.setText("")
        dlg.edit_delta_n_eigen.setText("")
        dlg.edit_source_mask.setText("")

        with patch.object(QMessageBox, 'information') as mock_info:
            dlg._validate_paths()
            mock_info.assert_called_once()
            called_msg = mock_info.call_args[0][2]
            self.assertIn("FES2022b 潮位外推掩膜: ℹ️ 未配置（可选", called_msg)
            self.assertIn("EIGEN-6C4-EGM2008 ΔN: ℹ️ 未配置（可选", called_msg)
            self.assertIn("Hybrid MDT 来源掩膜: ℹ️ 未配置（可选", called_msg)
        dlg.close()

    def test_06b_settings_dialog_custom_path_preservation(self):
        """测试 6b: 验证 SettingsDialog 保持用户自定义路径不被默认值覆盖"""
        custom_cfg = {
            'paths': {
                'delta_n_eigen6c4_egm2008_tif': 'D:/my_custom_dir/custom_eigen.tif'
            }
        }
        with patch('gui.settings_dialog.load_app_config', return_value=custom_cfg):
            dlg = SettingsDialog()
            self.assertEqual(dlg.edit_delta_n_eigen.text(), 'D:/my_custom_dir/custom_eigen.tif')
            dlg.close()

    def test_07_config_yaml_defaults(self):
        """测试 7: 验证 config.yaml 默认值配置规范"""
        cfg = load_app_config()
        paths = cfg.get("paths", {})
        self.assertEqual(paths.get("hybrid_mdt_source_mask"), "")
        self.assertTrue(paths.get("delta_n_eigen6c4_egm2008_tif", "").replace("\\", "/").endswith("data/geoid/delta_n_eigen6c4_minus_egm2008.tif"))
        self.assertTrue(paths.get("fes_extrapolation_mask_nc", "").replace("\\", "/").endswith("fes2022b/mask_fes2022B.nc"))

        # 检查未解析的原始 yaml 文件声明
        with open("config.yaml", "r", encoding="utf-8") as f:
            raw_cfg = yaml.safe_load(f)
        self.assertEqual(raw_cfg["paths"].get("hybrid_mdt_source_mask"), "")
        self.assertEqual(raw_cfg["paths"].get("delta_n_eigen6c4_egm2008_tif"), "data/geoid/delta_n_eigen6c4_minus_egm2008.tif")
        self.assertEqual(raw_cfg["paths"].get("fes_extrapolation_mask_nc"), "fes2022b/mask_fes2022B.nc")

    def test_08_about_dialog_text_author_and_version(self):
        """测试 8: 关于对话框中作者统一为 Wang Yuhao，版本为 v1.5 Alpha，严禁非统一形式出现"""
        win = MainWindow()
        with patch.object(QMessageBox, 'about') as mock_about:
            win._show_about()
            mock_about.assert_called_once()
            about_html = mock_about.call_args[0][2]

            self.assertIn("CoastTideX", about_html)
            self.assertIn("王宇浩", about_html)
            self.assertNotIn("王宇豪", about_html)
            self.assertNotIn("wyhao2333", about_html)
            self.assertNotIn("零误差", about_html)
            self.assertIn("可选配置 Hybrid MDT 来源分类栅格", about_html)
        win.close()

    def test_09_scan_worker_spec_timestamp(self):
        """测试 9: BatchScanWorker.run 输出的 spec 包含真实正数 scan_time"""
        in_dir = self.test_dir
        out_dir = os.path.join(self.test_dir, "out")
        worker = BatchScanWorker(in_dir, out_dir, False)

        captured_spec = {}
        def _on_finish(files, spec):
            captured_spec.update(spec)

        worker.finished.connect(_on_finish)
        t_before = time.time()
        worker.run()
        t_after = time.time()

        self.assertIn("scan_time", captured_spec)
        self.assertGreaterEqual(captured_spec["scan_time"], t_before - 0.1)
        self.assertLessEqual(captured_spec["scan_time"], t_after + 0.1)

    def test_10_scan_finished_stale_discard(self):
        """测试 10: 异步扫描返回时，若用户已更改目录或递归选项，系统判定快照过期并丢弃"""
        win = MainWindow()
        win.txt_batch_in_dir.setText(os.path.join(self.test_dir, "folder_A"))
        win.chk_batch_recursive.setChecked(False)

        stale_spec = {
            "in_dir": os.path.join(self.test_dir, "folder_B"),
            "out_dir": os.path.join(self.test_dir, "folder_B", "CoastTideX_output"),
            "recursive": False,
            "scan_time": time.time()
        }
        fake_discovered = [{"filename": "tile.tif", "valid": True}]

        win._on_scan_finished(fake_discovered, stale_spec)

        self.assertFalse(win.scan_fresh)
        self.assertEqual(len(win.discovered_batch_files), 0)
        self.assertIn("目录或扫描配置已更改，已丢弃旧的扫描结果", win.lbl_batch_status.text())
        win.close()

    def test_11_batch_raster_engine_imports_no_duplicate(self):
        """测试 11: 检查 core.batch_raster_engine 不存在重复导入，模块加载正常"""
        import core.batch_raster_engine as bre
        self.assertTrue(hasattr(bre, "validate_tide_cache_compatibility"))
        self.assertTrue(hasattr(bre, "BatchRasterEngine"))

    def test_12_verify_raster_artifacts_signature_mandatory(self):
        """测试 12: 当指定 expected_cache_sig 时，如果已有成果无签名或签名不匹配，严密返回 False"""
        dem_p = self._create_synthetic_dem("dem12.tif")
        dem_info = self.engine.inspect_raster(dem_p, compute_valid_count=False)

        freq_p = os.path.join(self.test_dir, "freq12.tif")
        qc_p = os.path.join(self.test_dir, "qc12.tif")

        profile_f = {
            'driver': 'GTiff',
            'height': dem_info.height,
            'width': dem_info.width,
            'count': 1,
            'dtype': 'float32',
            'crs': dem_info.crs,
            'transform': dem_info.transform
        }
        with rasterio.open(freq_p, 'w', **profile_f) as dst:
            dst.write(np.zeros((dem_info.height, dem_info.width), dtype=np.float32), 1)

        profile_qc = profile_f.copy()
        profile_qc['dtype'] = 'uint16'
        profile_qc['transform'] = dem_info.transform
        with rasterio.open(qc_p, 'w', **profile_qc) as dst:
            dst.write(np.zeros((dem_info.height, dem_info.width), dtype=np.uint16), 1)

        # 成果无签名，但传入 expected_cache_sig -> 必须返回 False
        self.assertFalse(_verify_raster_artifacts(dem_info, freq_p, qc_p, expected_cache_sig="expected_sig_123"))

        # 成果签名不匹配 -> 必须返回 False
        with rasterio.open(freq_p, 'r+') as dst:
            dst.update_tags(CACHE_SIGNATURE="wrong_sig")
        self.assertFalse(_verify_raster_artifacts(dem_info, freq_p, qc_p, expected_cache_sig="expected_sig_123"))

        # 成果签名完全匹配 -> 返回 True
        with rasterio.open(freq_p, 'r+') as dst:
            dst.update_tags(CACHE_SIGNATURE="expected_sig_123")
        self.assertTrue(_verify_raster_artifacts(dem_info, freq_p, qc_p, expected_cache_sig="expected_sig_123"))

    def test_13_verify_raster_artifacts_transform_mismatch(self):
        """测试 13: 栅格 Transform 仿射变换不一致时返回 False"""
        dem_p = self._create_synthetic_dem("dem13.tif")
        dem_info = self.engine.inspect_raster(dem_p, compute_valid_count=False)

        freq_p = os.path.join(self.test_dir, "freq13.tif")
        qc_p = os.path.join(self.test_dir, "qc13.tif")

        mismatched_transform = from_origin(120.005, 30.005, 0.001, 0.001)
        profile_f = {
            'driver': 'GTiff',
            'height': dem_info.height,
            'width': dem_info.width,
            'count': 1,
            'dtype': 'float32',
            'crs': dem_info.crs,
            'transform': mismatched_transform
        }
        with rasterio.open(freq_p, 'w', **profile_f) as dst:
            dst.write(np.zeros((dem_info.height, dem_info.width), dtype=np.float32), 1)

        profile_qc = profile_f.copy()
        profile_qc['dtype'] = 'uint16'
        profile_qc['transform'] = dem_info.transform
        with rasterio.open(qc_p, 'w', **profile_qc) as dst:
            dst.write(np.zeros((dem_info.height, dem_info.width), dtype=np.uint16), 1)

        self.assertFalse(_verify_raster_artifacts(dem_info, freq_p, qc_p))

    def test_14_verify_raster_artifacts_dtype(self):
        """测试 14: 栅格数据类型不合规 (如 freq 为 float64 或 qc 为 int32) 时返回 False"""
        dem_p = self._create_synthetic_dem("dem14.tif")
        dem_info = self.engine.inspect_raster(dem_p, compute_valid_count=False)

        freq_p = os.path.join(self.test_dir, "freq14.tif")
        qc_p = os.path.join(self.test_dir, "qc14.tif")

        profile_f = {
            'driver': 'GTiff',
            'height': dem_info.height,
            'width': dem_info.width,
            'count': 1,
            'dtype': 'float64',
            'crs': dem_info.crs,
            'transform': dem_info.transform
        }
        with rasterio.open(freq_p, 'w', **profile_f) as dst:
            dst.write(np.zeros((dem_info.height, dem_info.width), dtype=np.float64), 1)

        profile_qc = profile_f.copy()
        profile_qc['dtype'] = 'uint16'
        profile_qc['transform'] = dem_info.transform
        with rasterio.open(qc_p, 'w', **profile_qc) as dst:
            dst.write(np.zeros((dem_info.height, dem_info.width), dtype=np.uint16), 1)

        self.assertFalse(_verify_raster_artifacts(dem_info, freq_p, qc_p))

    def test_15_nodata_collision_protection(self):
        """测试 15: DEM NoData 冲突保护：DEM NoData 在 [0, 100] 内时输出回退为 NaN，不在区间时正常继承"""
        n0 = ControlNode(node_id=0, x=120.0, y=29.968, lon=120.0, lat=29.968, water_levels_sorted=np.array([0.0, 2.0], dtype=np.float32), valid=True)
        n1 = ControlNode(node_id=1, x=120.064, y=29.968, lon=120.064, lat=29.968, water_levels_sorted=np.array([0.0, 2.0], dtype=np.float32), valid=True)
        n2 = ControlNode(node_id=2, x=120.0, y=30.0, lon=120.0, lat=30.0, water_levels_sorted=np.array([0.0, 2.0], dtype=np.float32), valid=True)
        n3 = ControlNode(node_id=3, x=120.064, y=30.0, lon=120.064, lat=30.0, water_levels_sorted=np.array([0.0, 2.0], dtype=np.float32), valid=True)

        cell = QuadCell(
            cell_id=0,
            x_min=120.0, y_min=29.968, x_max=120.064, y_max=30.0,
            level=0,
            node_a=n0, node_b=n1, node_c=n2, node_d=n3
        )
        leaf_cells = [cell]

        labeled_coarse = np.ones((1, 1), dtype=np.int32)
        downsample_factor = 64
        h_coarse, w_coarse = 1, 1
        input_valid_count = 64 * 64

        test_cases = [
            (0.0, True),      # 0.0 在 [0, 100] 内 -> 必须回退为 NaN
            (50.0, True),     # 50.0 在 [0, 100] 内 -> 必须回退为 NaN
            (100.0, True),    # 100.0 在 [0, 100] 内 -> 必须回退为 NaN
            (-9999.0, False), # -9999.0 不在 [0, 100] 内 -> 正常继承 -9999.0
            (-1.0, False)     # -1.0 不在 [0, 100] 内 -> 正常继承 -1.0
        ]

        for test_nodata, expect_nan in test_cases:
            dem_p = self._create_synthetic_dem(f"dem_nodata_{test_nodata}.tif", nodata=test_nodata)
            info = self.engine.inspect_raster(dem_p, compute_valid_count=False)

            out_tif = os.path.join(self.test_dir, f"out_inundation_{test_nodata}.tif")
            qc_tif = os.path.join(self.test_dir, f"out_qc_{test_nodata}.tif")

            stream_inundation_frequency_interpolation(
                info=info,
                leaf_cells=leaf_cells,
                labeled_coarse=labeled_coarse,
                downsample_factor=downsample_factor,
                h_coarse=h_coarse,
                w_coarse=w_coarse,
                input_valid_count=input_valid_count,
                output_path=out_tif,
                qc_output_path=qc_tif,
                block_size=64,
                allow_overwrite=True
            )

            with rasterio.open(out_tif) as src_out:
                out_nd = src_out.nodata
                if expect_nan:
                    self.assertTrue(np.isnan(out_nd), f"Input nodata {test_nodata} must fallback to NaN, got {out_nd}")
                else:
                    self.assertAlmostEqual(out_nd, test_nodata, places=3, msg=f"Input nodata {test_nodata} should be inherited, got {out_nd}")


if __name__ == "__main__":
    unittest.main()
