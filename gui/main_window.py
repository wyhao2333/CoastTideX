"""
CoastTideX 桌面主窗口 (Main Window)
集成单点连续预测、批量多点解算、交互式波形分析、基准转换与报表导出。
"""

import os
import re
from typing import Optional, Any, Dict, List
import numpy as np
import pandas as pd
import dateutil.tz
from datetime import datetime, timedelta
# 关键机制：在 Windows 下在加载 PyQt6 前预加载 pyfes / rasterio C++ 库，避免 Qt6 运行时内存/DLL冲突
try:
    import pyfes
except ImportError:
    pass
try:
    import rasterio
except ImportError:
    pass

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QDateTime, QUrl
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QTabWidget, QGroupBox, QLabel, QLineEdit, QComboBox,
    QDateTimeEdit, QPushButton, QProgressBar, QTableWidget,
    QTableWidgetItem, QHeaderView, QFileDialog, QMessageBox,
    QSplitter, QStatusBar, QScrollArea, QFrame, QSpinBox,
    QCheckBox, QDoubleSpinBox, QInputDialog, QApplication
)
from PyQt6.QtGui import QIcon, QFont, QAction, QColor, QDesktopServices
import threading

from core.tide_engine import FESTidePredictor
from core.datum_engine import DatumTransformer
from core.raster_engine import RasterTideEngine, RasterInfo, RasterResultSummary, RasterCalculationCancelled
from core.dem_datum_converter import (
    DEMDatumConverter, convert_dem_to_msl, DEMConversionSummary,
    MAX_MDT_EXTRAPOLATION_DISTANCE_KM
)
from core.utils import COASTAL_PRESETS, export_dataframe, load_app_config, extract_scalar_metadata
from .chart_widget import TideChartWidget
from .settings_dialog import SettingsDialog
from .manual_dialog import ManualDialog
from .styles import DARK_THEME_QSS


def _safe_float(val):
    """安全解析为浮点数，若无效则返回 np.nan"""
    if val is None:
        return np.nan
    if hasattr(val, '__len__') and not isinstance(val, (str, bytes)):
        if len(val) == 0:
            return np.nan
        val = val[0]
    try:
        if pd.isna(val):
            return np.nan
        return float(val)
    except (ValueError, TypeError):
        return np.nan


class SingleTideWorker(QThread):
    """后台单点潮位计算线程，保障界面交互不卡顿"""
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(pd.DataFrame, dict)
    error = pyqtSignal(str)

    def __init__(self, lon, lat, start_time, end_time, freq, constituents, source_tz='UTC', datum_mode='both', inclusive='both'):
        super().__init__()
        self.lon = lon
        self.lat = lat
        self.start_time = start_time
        self.end_time = end_time
        self.freq = freq
        self.constituents = constituents
        self.source_tz = source_tz
        self.datum_mode = datum_mode
        self.inclusive = inclusive

    def run(self):
        try:
            predictor = FESTidePredictor()

            def p_cb(percent, msg):
                self.progress.emit(percent, msg)

            # 1. 运行潮位时间序列预测
            df = predictor.predict_series(
                lon=self.lon,
                lat=self.lat,
                start_time=self.start_time,
                end_time=self.end_time,
                freq=self.freq,
                inclusive=self.inclusive,
                constituents=self.constituents,
                source_tz=self.source_tz,
                progress_callback=p_cb
            )

            # 2. 判断是否为仅 MSL 模式 (解耦大地测量基准文件依赖)
            if self.datum_mode == 'msl':
                p_cb(90, "已选定仅 MSL 模式，无需加载大地水准面与 MDT 栅格...")
                df['tide_msl_m'] = df['tide_total_m']
                df['mdt_m'] = np.nan
                df['delta_n_m'] = np.nan
                df['n_egm2008_m'] = np.nan
                df['h_mdt_ref_m'] = np.nan
                df['h_goco06s_m'] = np.nan
                df['h_egm2008_m'] = np.nan
                df['h_wgs84_m'] = np.nan
                df['datum_ref_geoid'] = 'MSL'
                df['qc_warning'] = 'NORMAL'

                scalar_datum = {
                    'mdt_m': np.nan,
                    'delta_n_m': np.nan,
                    'n_egm2008_m': np.nan,
                    'datum_ref_geoid': '局部平均海平面 (MSL)',
                    'qc_warning': 'NORMAL'
                }
                p_cb(100, "单点 MSL 潮位模拟完成！")
                self.finished.emit(df, scalar_datum)
                return

            # 3. 运行严密四大垂直基准转换 (MSL -> MDT_REF -> EGM2008 -> WGS84)
            p_cb(85, "严密转换四大垂直基准 (MSL/MDT_REF/EGM2008/WGS84)...")
            transformer = DatumTransformer()
            strict_mode = (self.datum_mode != 'msl')
            datum_res = transformer.convert_tide_datums(
                df['tide_total_m'].values, self.lon, self.lat, datum_target=self.datum_mode, strict=strict_mode
            )

            df['tide_msl_m'] = datum_res['tide_msl_m']
            df['mdt_m'] = datum_res['mdt_m']
            df['delta_n_m'] = datum_res['delta_n_m']
            df['n_egm2008_m'] = datum_res['n_egm2008_m']
            df['h_mdt_ref_m'] = datum_res['h_mdt_ref_m']
            df['h_goco06s_m'] = datum_res['h_goco06s_m']
            df['h_egm2008_m'] = datum_res['h_egm2008_m']
            df['h_wgs84_m'] = datum_res['h_wgs84_m']
            df['datum_ref_geoid'] = datum_res['datum_ref_geoid']
            df['qc_warning'] = datum_res['qc_warning']

            scalar_datum = {
                'mdt_m': _safe_float(datum_res['mdt_m']),
                'delta_n_m': _safe_float(datum_res['delta_n_m']),
                'n_egm2008_m': _safe_float(datum_res['n_egm2008_m']),
                'datum_ref_geoid': str(extract_scalar_metadata(datum_res.get('datum_ref_geoid', 'GOCO06s'), default='GOCO06s')),
                'qc_warning': str(extract_scalar_metadata(datum_res.get('qc_warning', 'NORMAL'), default='NORMAL')),
            }

            p_cb(100, "全部计算完成！")
            self.finished.emit(df, scalar_datum)

        except Exception as e:
            self.error.emit(str(e))


class BatchTideWorker(QThread):
    """后台批量表格计算线程，支持自适应分块与高效向量化基准转换"""
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(pd.DataFrame)
    error = pyqtSignal(str)

    def __init__(self, df_records, lon_col, lat_col, time_col, constituents='all', source_tz='UTC', datum_mode='both'):
        super().__init__()
        self.df_records = df_records
        self.lon_col = lon_col
        self.lat_col = lat_col
        self.time_col = time_col
        self.constituents = constituents
        self.source_tz = source_tz
        self.datum_mode = datum_mode

    def run(self):
        try:
            predictor = FESTidePredictor()

            def p_cb(percent, msg):
                self.progress.emit(percent, msg)

            df_out = predictor.predict_batch(
                self.df_records,
                lon_col=self.lon_col,
                lat_col=self.lat_col,
                time_col=self.time_col,
                constituents=self.constituents,
                source_tz=self.source_tz,
                progress_callback=p_cb
            )

            # 判断是否为仅 MSL 模式 (解耦大地测量基准要求)
            if self.datum_mode == 'msl':
                p_cb(90, "已选定仅 MSL 模式，跳过批量大地水准面与 MDT 转换...")
                df_out['tide_msl_m'] = df_out['tide_total_m']
                df_out['mdt_m'] = np.nan
                df_out['delta_n_m'] = np.nan
                df_out['n_egm2008_m'] = np.nan
                df_out['h_mdt_ref_m'] = np.nan
                df_out['h_goco06s_m'] = np.nan
                df_out['h_egm2008_m'] = np.nan
                df_out['h_wgs84_m'] = np.nan
                df_out['datum_ref_geoid'] = 'MSL'
                df_out['qc_warning'] = 'NORMAL'
                p_cb(100, "批量 MSL 计算完成！")
                self.finished.emit(df_out)
                return

            # 向量化批量基准转换 (无慢速循环)
            p_cb(75, "向量化匹配 MDT, Delta_N 与 EGM2008 基准...")
            transformer = DatumTransformer()
            lons = df_out[self.lon_col].astype(float).values
            lats = df_out[self.lat_col].astype(float).values
            tide_msl = df_out['tide_total_m'].values

            strict_mode = (self.datum_mode != 'msl')
            datum_res = transformer.convert_tide_datums(tide_msl, lons, lats, datum_target=self.datum_mode, strict=strict_mode)
            df_out['tide_msl_m'] = datum_res['tide_msl_m']
            df_out['mdt_m'] = datum_res['mdt_m']
            df_out['delta_n_m'] = datum_res['delta_n_m']
            df_out['n_egm2008_m'] = datum_res['n_egm2008_m']
            df_out['h_mdt_ref_m'] = datum_res['h_mdt_ref_m']
            df_out['h_goco06s_m'] = datum_res['h_goco06s_m']
            df_out['h_egm2008_m'] = datum_res['h_egm2008_m']
            df_out['h_wgs84_m'] = datum_res['h_wgs84_m']
            df_out['datum_ref_geoid'] = datum_res['datum_ref_geoid']
            df_out['qc_warning'] = datum_res['qc_warning']

            p_cb(100, "批量计算完成！")
            self.finished.emit(df_out)
        except Exception as e:
            self.error.emit(str(e))


class RasterTideWorker(QThread):
    """空间栅格解算后台工作线程 (Snapshot / Inundation / Exposure)"""
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(object)  # RasterResultSummary or dict
    error = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, mode: str, params: dict, engine: Optional[Any] = None):
        super().__init__()
        self.mode = mode
        self.params = params
        self.engine = engine
        self.cancel_event = threading.Event()
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True
        self.cancel_event.set()

    def run(self):
        try:
            engine = self.engine or RasterTideEngine()

            def p_cb(percent, msg):
                if not self._is_cancelled:
                    self.progress.emit(percent, msg)

            if self.mode == 'snapshot':
                summary = engine.calculate_snapshot_raster(
                    input_raster_path=self.params['input_path'],
                    output_raster_path=self.params['output_path'],
                    timestamp=self.params['timestamp'],
                    datum_target=self.params['datum_target'],
                    constituents=self.params['constituents'],
                    source_tz=self.params['source_tz'],
                    block_size=self.params['block_size'],
                    strict=self.params['strict'],
                    progress_callback=p_cb,
                    cancel_event=self.cancel_event
                )
            elif self.mode == 'inundation':
                summary = engine.calculate_inundation_raster(
                    dem_path=self.params['input_path'],
                    output_path=self.params['output_path'],
                    qc_output_path=self.params.get('qc_output_path'),
                    year=self.params.get('year', 2024),
                    start_time=self.params.get('start_time'),
                    end_time=self.params.get('end_time'),
                    freq=self.params.get('freq', '30min'),
                    dem_datum=self.params.get('dem_datum', 'egm2008'),
                    constituents=self.params.get('constituents', 'all'),
                    source_tz=self.params.get('source_tz', 'UTC'),
                    target_mode=self.params.get('target_mode', 'intertidal'),
                    initial_control_spacing_m=self.params.get('initial_control_spacing_m', 4000.0),
                    min_control_spacing_m=self.params.get('min_control_spacing_m', 500.0),
                    inundation_error_tolerance_pct=self.params.get('inundation_error_tolerance_pct', 1.0),
                    block_size=self.params.get('block_size', 512),
                    strict=self.params.get('strict', True),
                    progress_callback=p_cb,
                    cancel_event=self.cancel_event
                )
            elif self.mode == 'exposure':
                summary = engine.calculate_exposure_raster(
                    dem_path=self.params['input_path'],
                    output_dir=self.params.get('output_dir'),
                    output_paths=self.params.get('output_paths'),
                    year=self.params.get('year', 2024),
                    start_time=self.params.get('start_time'),
                    end_time=self.params.get('end_time'),
                    freq=self.params.get('freq', '30min'),
                    dem_datum=self.params.get('dem_datum', 'egm2008'),
                    constituents=self.params.get('constituents', 'all'),
                    source_tz=self.params.get('source_tz', 'UTC'),
                    target_mode=self.params.get('target_mode', 'intertidal'),
                    initial_control_spacing_m=self.params.get('initial_control_spacing_m', 4000.0),
                    min_control_spacing_m=self.params.get('min_control_spacing_m', 500.0),
                    inundation_error_tolerance_pct=self.params.get('inundation_error_tolerance_pct', 1.0),
                    block_size=self.params.get('block_size', 512),
                    strict=self.params.get('strict', True),
                    allow_overwrite=self.params.get('allow_overwrite', True),
                    progress_callback=p_cb,
                    cancel_event=self.cancel_event,
                    export_tide_cache_path=self.params.get('export_tide_cache_path')
                )
            else:
                raise ValueError(f"未知栅格模式: {self.mode}")

            if not self._is_cancelled:
                self.finished.emit(summary)
        except RasterCalculationCancelled:
            self._is_cancelled = True
            self.cancelled.emit()
        except Exception as e:
            if not self._is_cancelled:
                self.error.emit(str(e))



class BatchScanWorker(QThread):
    """后台轻量扫描 GeoTIFF 头信息工作线程，严禁阻塞 UI 主线程"""
    finished = pyqtSignal(list, dict)  # (discovered_list, scan_spec)
    error = pyqtSignal(str)

    def __init__(self, in_dir: str, out_dir: str, recursive: bool):
        super().__init__()
        self.in_dir = in_dir
        self.out_dir = out_dir
        self.recursive = recursive

    def run(self):
        try:
            import time
            from core.batch_raster_engine import BatchRasterEngine
            res = BatchRasterEngine.discover_rasters(
                input_folder=self.in_dir,
                recursive=self.recursive,
                output_folder=self.out_dir
            )
            spec = {
                "in_dir": self.in_dir,
                "out_dir": self.out_dir,
                "recursive": self.recursive,
                "scan_time": time.time()
            }
            self.finished.emit(res, spec)
        except Exception as ex:
            self.error.emit(str(ex))


class BatchRasterWorker(QThread):
    """批量潮间带栅格解算后台工作线程 (v1.5)"""
    progress = pyqtSignal(int, int, str, str, dict)
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, params: dict):
        super().__init__()
        self.params = params
        self.cancel_event = threading.Event()
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True
        self.cancel_event.set()

    def run(self):
        try:
            from core.batch_raster_engine import BatchRasterEngine
            batch_engine = BatchRasterEngine()

            def p_cb(ov, ti, fn, msg, counts):
                if not self._is_cancelled:
                    self.progress.emit(ov, ti, fn, msg, counts)

            res = batch_engine.run_batch(
                input_folder=self.params['input_folder'],
                output_folder=self.params.get('output_folder'),
                job_mode=self.params.get('job_mode', 'tide-inundation'),
                year=self.params.get('year', 2024),
                start_time=self.params.get('start_time'),
                end_time=self.params.get('end_time'),
                freq=self.params.get('freq', '30min'),
                dem_datum=self.params.get('dem_datum', 'egm2008'),
                constituents=self.params.get('constituents', 'all'),
                target_mode=self.params.get('target_mode', 'intertidal'),
                initial_control_spacing_m=self.params.get('initial_control_spacing_m', 4000.0),
                min_control_spacing_m=self.params.get('min_control_spacing_m', 500.0),
                inundation_error_tolerance_pct=self.params.get('inundation_error_tolerance_pct', 1.0),
                block_size=self.params.get('block_size', 512),
                strict=self.params.get('strict', True),
                recursive=self.params.get('recursive', False),
                existing_policy=self.params.get('existing_policy', 'resume'),
                progress_callback=p_cb,
                cancel_event=self.cancel_event,
                discovered_files=self.params.get('discovered_files')
            )
            if self._is_cancelled:
                self.cancelled.emit()
            else:
                self.finished.emit(res)
        except RasterCalculationCancelled:
            self.cancelled.emit()
        except Exception as e:
            self.error.emit(f"批量任务发生异常: {str(e)}")


class DEMDatumConversionWorker(QThread):
    """后台 DEM 垂直基准转换工作线程 (EGM2008 -> MSL, v1.7)"""
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(object)  # DEMConversionSummary
    error = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, params: dict):
        super().__init__()
        self.params = params
        self.cancel_event = threading.Event()
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True
        self.cancel_event.set()

    def run(self):
        try:
            from core.dem_datum_converter import convert_dem_to_msl

            def p_cb(percent, msg):
                if not self._is_cancelled:
                    self.progress.emit(percent, msg)

            summary = convert_dem_to_msl(
                input_dem_path=self.params['input_path'],
                output_msl_path=self.params.get('output_path'),
                output_qc_path=self.params.get('qc_output_path'),
                max_extrapolation_distance_km=self.params.get('max_dist_km', 100.0),
                block_size=self.params.get('block_size', 512),
                allow_overwrite=self.params.get('allow_overwrite', True),
                progress_callback=p_cb,
                cancel_event=self.cancel_event
            )

            # 若未勾选保存 QC 掩膜，清理临时生成的 QC 产物
            save_qc = self.params.get('save_qc', False)
            if not save_qc and summary.qc_output_path and os.path.exists(summary.qc_output_path):
                try:
                    os.remove(summary.qc_output_path)
                except Exception:
                    pass

            if self._is_cancelled:
                self.cancelled.emit()
            else:
                self.finished.emit(summary)
        except Exception as e:
            if self._is_cancelled or "用户主动取消" in str(e):
                self.cancelled.emit()
            else:
                self.error.emit(str(e))


class MainWindow(QMainWindow):
    """CoastTideX 桌面客户端主窗口"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("CoastTideX v1.7 - 全球海岸带潮位模拟与高程基准转换系统")
        self.resize(1280, 800)
        self.setMinimumSize(960, 500)
        self.setStyleSheet(DARK_THEME_QSS)

        self.current_result_df = None
        self.batch_result_df = None
        self._current_tz_mode = "UTC"
        self._user_selected_freq = "30min"
        self.raster_worker = None
        self.current_raster_info = None
        self.dem_worker = None
        self.current_dem_summary = None

        self._init_menu()
        self._init_ui()
        self._set_default_values()

    def _init_menu(self):
        menubar = self.menuBar()

        # 文件菜单
        menu_file = menubar.addMenu("文件 (&F)")
        action_settings = QAction("⚙️ 数据源与系统设置", self)
        action_settings.triggered.connect(self._open_settings)
        menu_file.addAction(action_settings)

        action_exit = QAction("退出 (&X)", self)
        action_exit.triggered.connect(self.close)
        menu_file.addAction(action_exit)

        # 帮助菜单
        menu_help = menubar.addMenu("帮助 (&H)")
        action_manual = QAction("📖 功能说明与操作手册", self)
        action_manual.triggered.connect(self._show_manual)
        menu_help.addAction(action_manual)

        action_about = QAction("ℹ️ 关于 CoastTideX", self)
        action_about.triggered.connect(self._show_about)
        menu_help.addAction(action_about)

    def _init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # 核心选项卡
        self.tabs = QTabWidget()
        self.tab_single = QWidget()
        self.tab_batch = QWidget()
        self.tab_dem_convert = QWidget()
        self.tab_raster = QWidget()
        self.tab_batch_raster = QWidget()

        self.tabs.addTab(self.tab_single, " 🌊 单点/时段潮位序列 ")
        self.tabs.addTab(self.tab_batch, " 📊 批量站点多时刻解算 ")
        self.tabs.addTab(self.tab_dem_convert, " 📐 DEM 基准转换 (EGM2008→MSL) ")
        self.tabs.addTab(self.tab_raster, " 🗺️ 单影像栅格解算 / 验证 ")
        self.tabs.addTab(self.tab_batch_raster, " 🗂️ 批量潮间带栅格解算 ")

        self._setup_single_tab()
        self._setup_batch_tab()
        self._setup_dem_convert_tab()
        self._setup_raster_tab()
        self._setup_batch_raster_tab()

        main_layout.addWidget(self.tabs)

        # 底部状态栏
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("就绪 - 欢迎使用 CoastTideX v1.7 (MSL 统一基准架构)")

    def _setup_single_tab(self):
        layout = QHBoxLayout(self.tab_single)
        layout.setSpacing(10)

        # 左侧控制面板 (包装在 QScrollArea 内，支持自适应垂直滚动，彻底解除窗口垂直缩放锁定)
        scroll_left = QScrollArea()
        scroll_left.setWidgetResizable(True)
        scroll_left.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_left.setFrameShape(QFrame.Shape.NoFrame)
        scroll_left.setFixedWidth(390)

        left_panel = QWidget()
        layout_left = QVBoxLayout(left_panel)
        layout_left.setContentsMargins(2, 2, 8, 2)
        layout_left.setSpacing(12)

        # 1. 空间位置设置分组
        grp_spatial = QGroupBox("1. 空间位置与预设")
        layout_sp = QGridLayout(grp_spatial)
        layout_sp.setSpacing(8)

        layout_sp.addWidget(QLabel("预设站点:"), 0, 0)
        self.combo_presets = QComboBox()
        self.combo_presets.addItem("自定义坐标...")
        for name in COASTAL_PRESETS.keys():
            self.combo_presets.addItem(name)
        self.combo_presets.currentIndexChanged.connect(self._on_preset_changed)
        layout_sp.addWidget(self.combo_presets, 0, 1)

        layout_sp.addWidget(QLabel("目标经度 (°):"), 1, 0)
        self.edit_lon = QLineEdit("122.0000")
        layout_sp.addWidget(self.edit_lon, 1, 1)

        layout_sp.addWidget(QLabel("目标纬度 (°):"), 2, 0)
        self.edit_lat = QLineEdit("31.0000")
        layout_sp.addWidget(self.edit_lat, 2, 1)

        layout_left.addWidget(grp_spatial)

        # 2. 时间与步长设置分组
        grp_time = QGroupBox("2. 预测时段与分辨率")
        layout_time = QGridLayout(grp_time)
        layout_time.setSpacing(8)

        layout_time.addWidget(QLabel("时间模式:"), 0, 0)
        self.combo_time_mode = QComboBox()
        self.combo_time_mode.addItem("自定义时段 (Custom Period)", "period")
        self.combo_time_mode.addItem("整年快捷模式 (Year Mode)", "year")
        self.combo_time_mode.currentIndexChanged.connect(self._on_time_mode_changed)
        layout_time.addWidget(self.combo_time_mode, 0, 1)

        self.lbl_start = QLabel("起始时间:")
        layout_time.addWidget(self.lbl_start, 1, 0)
        self.time_start = QDateTimeEdit(QDateTime.currentDateTime())
        self.time_start.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.time_start.setCalendarPopup(True)
        self.time_start.dateTimeChanged.connect(self._update_sample_estimate)
        layout_time.addWidget(self.time_start, 1, 1)

        self.lbl_end = QLabel("结束时间:")
        layout_time.addWidget(self.lbl_end, 2, 0)
        self.time_end = QDateTimeEdit(QDateTime.currentDateTime().addDays(1))
        self.time_end.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.time_end.setCalendarPopup(True)
        self.time_end.dateTimeChanged.connect(self._update_sample_estimate)
        layout_time.addWidget(self.time_end, 2, 1)

        self.lbl_year = QLabel("预测年份:")
        layout_time.addWidget(self.lbl_year, 3, 0)
        self.spin_year = QSpinBox()
        self.spin_year.setRange(1950, 2099)
        self.spin_year.setValue(2024)
        self.spin_year.valueChanged.connect(self._update_sample_estimate)
        layout_time.addWidget(self.spin_year, 3, 1)

        layout_time.addWidget(QLabel("采样间隔:"), 4, 0)
        self.combo_freq = QComboBox()
        self.combo_freq.addItem("5分钟 (5min)", "5min")
        self.combo_freq.addItem("6分钟 (6min)", "6min")
        self.combo_freq.addItem("10分钟 (10min)", "10min")
        self.combo_freq.addItem("15分钟 (15min)", "15min")
        self.combo_freq.addItem("30分钟 (30min)", "30min")
        self.combo_freq.addItem("1小时 (1h)", "1h")
        self.combo_freq.addItem("2小时 (2h)", "2h")
        self.combo_freq.setCurrentIndex(4)  # 默认 30min
        self.combo_freq.currentIndexChanged.connect(self._update_sample_estimate)
        self.combo_freq.activated.connect(self._on_freq_user_changed)
        layout_time.addWidget(self.combo_freq, 4, 1)

        layout_time.addWidget(QLabel("输入时区:"), 5, 0)
        self.combo_tz = QComboBox()
        self.combo_tz.addItem("UTC (世界标准时)", "UTC")
        self.combo_tz.addItem("本地时间 (Local Time)", "local")
        self.combo_tz.currentIndexChanged.connect(self._on_timezone_changed)
        layout_time.addWidget(self.combo_tz, 5, 1)

        layout_time.addWidget(QLabel("预期样本:"), 6, 0)
        self.lbl_sample_count = QLabel("-")
        self.lbl_sample_count.setStyleSheet("color: #38bdf8; font-weight: bold;")
        layout_time.addWidget(self.lbl_sample_count, 6, 1)

        layout_left.addWidget(grp_time)

        # 3. 模型与基准设置
        grp_model = QGroupBox("3. 算法精度与高程基准")
        layout_model = QGridLayout(grp_model)
        layout_model.setSpacing(8)

        layout_model.addWidget(QLabel("分潮模式:"), 0, 0)
        self.combo_const = QComboBox()
        self.combo_const.addItem("全部 34 个主分潮 (全精度)", "all")
        self.combo_const.addItem("8 个核心主分潮 (快速预览)", "major8")
        layout_model.addWidget(self.combo_const, 0, 1)

        layout_model.addWidget(QLabel("计算基准面:"), 1, 0)
        self.combo_compute_datum = QComboBox()
        self.combo_compute_datum.addItem("对比输出 (MSL & EGM2008)", "both")
        self.combo_compute_datum.addItem("全部基准面 (MSL/GOCO/EGM/WGS)", "all")
        self.combo_compute_datum.addItem("仅 EGM2008 (大地水准面正高)", "egm2008")
        self.combo_compute_datum.addItem("仅 MSL (相对平均海平面)", "msl")
        self.combo_compute_datum.currentIndexChanged.connect(self._on_compute_datum_changed)
        layout_model.addWidget(self.combo_compute_datum, 1, 1)

        layout_model.addWidget(QLabel("显示/统计基准:"), 2, 0)
        self.combo_display_datum = QComboBox()
        self.combo_display_datum.addItem("EGM2008 (大地水准面正高)", "egm")
        self.combo_display_datum.addItem("MSL (相对平均海平面)", "msl")
        self.combo_display_datum.addItem("GOCO06s/EIGEN-6C4 (Tide+MDT)", "goco")
        self.combo_display_datum.addItem("WGS84 (空间几何椭球高)", "wgs")
        self.combo_display_datum.currentIndexChanged.connect(self._on_datum_display_changed)
        layout_model.addWidget(self.combo_display_datum, 2, 1)

        layout_left.addWidget(grp_model)

        # 4. 执行按钮与进度条
        self.btn_run_single = QPushButton("🚀 开始潮位模拟计算")
        self.btn_run_single.setFixedHeight(40)
        self.btn_run_single.clicked.connect(self._run_single_simulation)
        layout_left.addWidget(self.btn_run_single)

        self.prog_single = QProgressBar()
        self.prog_single.setValue(0)
        self.prog_single.setTextVisible(True)
        layout_left.addWidget(self.prog_single)

        # 统计卡片面板
        grp_stat = QGroupBox("4. 统计极值指标 (当前选定基准)")
        layout_stat = QGridLayout(grp_stat)
        self.lbl_stat_target = QLabel("EGM2008 基准")
        self.lbl_max = QLabel("-")
        self.lbl_min = QLabel("-")
        self.lbl_range = QLabel("-")
        self.lbl_mdt = QLabel("-")
        self.lbl_delta_n = QLabel("-")
        self.lbl_geoid_n = QLabel("-")
        self.lbl_qc_status = QLabel("-")

        layout_stat.addWidget(QLabel("当前统计基准:"), 0, 0)
        layout_stat.addWidget(self.lbl_stat_target, 0, 1)
        layout_stat.addWidget(QLabel("最高潮位 (峰值):"), 1, 0)
        layout_stat.addWidget(self.lbl_max, 1, 1)
        layout_stat.addWidget(QLabel("最低潮位 (谷值):"), 2, 0)
        layout_stat.addWidget(self.lbl_min, 2, 1)
        layout_stat.addWidget(QLabel("最大潮差 (Range):"), 3, 0)
        layout_stat.addWidget(self.lbl_range, 3, 1)
        layout_stat.addWidget(QLabel("当地 MDT 偏置:"), 4, 0)
        layout_stat.addWidget(self.lbl_mdt, 4, 1)
        layout_stat.addWidget(QLabel("ΔN 改正 / 基准面:"), 5, 0)
        layout_stat.addWidget(self.lbl_delta_n, 5, 1)
        layout_stat.addWidget(QLabel("EGM2008 水准面 N:"), 6, 0)
        layout_stat.addWidget(self.lbl_geoid_n, 6, 1)
        layout_stat.addWidget(QLabel("网格质量评价:"), 7, 0)
        layout_stat.addWidget(self.lbl_qc_status, 7, 1)
        layout_left.addWidget(grp_stat)

        layout_left.addStretch()
        scroll_left.setWidget(left_panel)
        layout.addWidget(scroll_left)

        # 右侧图表与表格展示 (分割器)
        splitter_right = QSplitter(Qt.Orientation.Vertical)

        # 上半区：图表组件
        self.chart_widget = TideChartWidget()
        splitter_right.addWidget(self.chart_widget)

        # 下半区：表格与导出
        table_container = QWidget()
        layout_tc = QVBoxLayout(table_container)
        layout_tc.setContentsMargins(0, 4, 0, 0)

        layout_tb_header = QHBoxLayout()
        layout_tb_header.addWidget(QLabel("📋 逐时刻潮位预测数据表"))
        layout_tb_header.addStretch()

        self.btn_export_csv = QPushButton("导出为 CSV")
        self.btn_export_csv.setObjectName("btn_secondary")
        self.btn_export_csv.clicked.connect(self._export_csv)
        self.btn_export_csv.setEnabled(False)

        self.btn_export_excel = QPushButton("导出为 Excel")
        self.btn_export_excel.setObjectName("btn_secondary")
        self.btn_export_excel.clicked.connect(self._export_excel)
        self.btn_export_excel.setEnabled(False)

        layout_tb_header.addWidget(self.btn_export_csv)
        layout_tb_header.addWidget(self.btn_export_excel)
        layout_tc.addLayout(layout_tb_header)

        self.table_single = QTableWidget()
        self.table_single.setColumnCount(8)
        self.table_single.setHorizontalHeaderLabels([
            "时间 (UTC)", "时间 (输入/本地)", "潮位 MSL (m)", "MDT (m)",
            "ΔN 改正 (m)", "EGM2008 正高 (m)", "WGS84 椭球高 (m)", "质量 Flag"
        ])
        self.table_single.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout_tc.addWidget(self.table_single)

        splitter_right.addWidget(table_container)
        splitter_right.setSizes([450, 250])

        layout.addWidget(splitter_right, stretch=1)

    def _setup_batch_tab(self):
        layout = QVBoxLayout(self.tab_batch)
        layout.setSpacing(12)

        # 顶部配置卡片
        grp_batch_ctrl = QGroupBox("批量解算文件与字段配置")
        layout_bc = QGridLayout(grp_batch_ctrl)

        layout_bc.addWidget(QLabel("选择输入表格 (CSV):"), 0, 0)
        self.edit_batch_file = QLineEdit()
        self.edit_batch_file.setPlaceholderText("请选择包含经度、纬度、时间的 CSV 文件...")
        btn_browse_batch = QPushButton("浏览文件...")
        btn_browse_batch.setObjectName("btn_secondary")
        btn_browse_batch.clicked.connect(self._browse_batch_csv)
        layout_bc.addWidget(self.edit_batch_file, 0, 1)
        layout_bc.addWidget(btn_browse_batch, 0, 2)

        # 字段映射
        layout_bc.addWidget(QLabel("经度列名:"), 1, 0)
        self.combo_col_lon = QComboBox()
        layout_bc.addWidget(self.combo_col_lon, 1, 1)

        layout_bc.addWidget(QLabel("纬度列名:"), 2, 0)
        self.combo_col_lat = QComboBox()
        layout_bc.addWidget(self.combo_col_lat, 2, 1)

        layout_bc.addWidget(QLabel("时间列名:"), 3, 0)
        self.combo_col_time = QComboBox()
        layout_bc.addWidget(self.combo_col_time, 3, 1)

        # 时区选择
        layout_bc.addWidget(QLabel("时间列时区:"), 4, 0)
        self.combo_batch_tz = QComboBox()
        self.combo_batch_tz.addItem("UTC (世界标准时)", "UTC")
        self.combo_batch_tz.addItem("本地时间 (Local Time)", "local")
        layout_bc.addWidget(self.combo_batch_tz, 4, 1)

        # 基准面模式
        layout_bc.addWidget(QLabel("基准面模式:"), 5, 0)
        self.combo_batch_datum = QComboBox()
        self.combo_batch_datum.addItem("全部基准面 (MSL/GOCO/EGM/WGS)", "all")
        self.combo_batch_datum.addItem("仅 MSL (相对平均海平面)", "msl")
        layout_bc.addWidget(self.combo_batch_datum, 5, 1)

        self.btn_run_batch = QPushButton("⚡ 开始批量解算")
        self.btn_run_batch.setFixedHeight(36)
        self.btn_run_batch.setEnabled(False)
        self.btn_run_batch.clicked.connect(self._run_batch_simulation)
        layout_bc.addWidget(self.btn_run_batch, 6, 1)

        self.prog_batch = QProgressBar()
        self.prog_batch.setValue(0)
        layout_bc.addWidget(self.prog_batch, 6, 2)

        layout.addWidget(grp_batch_ctrl)

        # 结果预览与导出
        layout_batch_table = QVBoxLayout()
        layout_bth = QHBoxLayout()
        layout_bth.addWidget(QLabel("批量结果数据预览"))
        layout_bth.addStretch()

        self.btn_export_batch = QPushButton("导出批量计算结果")
        self.btn_export_batch.setObjectName("btn_success")
        self.btn_export_batch.setEnabled(False)
        self.btn_export_batch.clicked.connect(self._export_batch_result)
        layout_bth.addWidget(self.btn_export_batch)
        layout_batch_table.addLayout(layout_bth)

        self.table_batch = QTableWidget()
        layout_batch_table.addWidget(self.table_batch)

        layout.addLayout(layout_batch_table)

    def _setup_dem_convert_tab(self):
        """配置 DEM 基准转换 (EGM2008 -> MSL) 选项卡 (v1.7 新增)"""
        scroll = QScrollArea(self.tab_dem_convert)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        panel = QWidget()
        layout_main = QVBoxLayout(panel)
        layout_main.setContentsMargins(10, 10, 10, 10)
        layout_main.setSpacing(12)

        # 1. 输入 DEM 栅格与元数据检查卡片
        grp_in = QGroupBox("1. 输入 DEM 栅格 (EGM2008 基准)")
        layout_in = QGridLayout(grp_in)
        layout_in.setSpacing(8)

        layout_in.addWidget(QLabel("输入 DEM GeoTIFF:"), 0, 0)
        self.edit_dem_input = QLineEdit()
        self.edit_dem_input.setPlaceholderText("请选择基于 EGM2008 大地水准面正高的高程栅格 (GeoTIFF)...")
        self.edit_dem_input.textChanged.connect(self._on_dem_input_changed)
        layout_in.addWidget(self.edit_dem_input, 0, 1)

        btn_browse_in = QPushButton("浏览 DEM 文件...")
        btn_browse_in.setObjectName("btn_secondary")
        btn_browse_in.clicked.connect(self._browse_dem_input)
        layout_in.addWidget(btn_browse_in, 0, 2)

        btn_inspect = QPushButton("🔍 检查 DEM 元数据")
        btn_inspect.setObjectName("btn_secondary")
        btn_inspect.clicked.connect(self._inspect_dem_input_ui)
        layout_in.addWidget(btn_inspect, 0, 3)

        # 元数据展示卡片
        frame_meta = QFrame()
        frame_meta.setStyleSheet("background-color: #1a1d24; border: 1px solid #334155; border-radius: 6px; padding: 6px;")
        layout_meta = QGridLayout(frame_meta)
        layout_meta.setSpacing(6)

        layout_meta.addWidget(QLabel("栅格规格:"), 0, 0)
        self.lbl_dem_dims = QLabel("-")
        self.lbl_dem_dims.setStyleSheet("font-weight: bold; color: #38bdf8;")
        layout_meta.addWidget(self.lbl_dem_dims, 0, 1)

        layout_meta.addWidget(QLabel("波段数量:"), 0, 2)
        self.lbl_dem_bands = QLabel("-")
        layout_meta.addWidget(self.lbl_dem_bands, 0, 3)

        layout_meta.addWidget(QLabel("坐标系统 (CRS):"), 1, 0)
        self.lbl_dem_crs = QLabel("-")
        self.lbl_dem_crs.setStyleSheet("font-weight: bold; color: #a78bfa;")
        layout_meta.addWidget(self.lbl_dem_crs, 1, 1)

        layout_meta.addWidget(QLabel("空间分辨率:"), 1, 2)
        self.lbl_dem_res = QLabel("-")
        layout_meta.addWidget(self.lbl_dem_res, 1, 3)

        layout_meta.addWidget(QLabel("NoData 值:"), 2, 0)
        self.lbl_dem_nodata = QLabel("-")
        layout_meta.addWidget(self.lbl_dem_nodata, 2, 1)

        layout_meta.addWidget(QLabel("识别基准面:"), 2, 2)
        self.lbl_dem_detected_datum = QLabel("-")
        layout_meta.addWidget(self.lbl_dem_detected_datum, 2, 3)

        layout_meta.addWidget(QLabel("空间范围 (Bounds):"), 3, 0)
        self.lbl_dem_bounds = QLabel("-")
        layout_meta.addWidget(self.lbl_dem_bounds, 3, 1, 1, 3)

        layout_in.addWidget(frame_meta, 1, 0, 1, 4)

        # 双重转换告警提示卡片 (Double-Conversion Guard)
        self.frame_dem_warning = QFrame()
        self.frame_dem_warning.setStyleSheet("background-color: #451a03; border: 1px solid #f59e0b; border-radius: 6px; padding: 8px;")
        layout_warn = QHBoxLayout(self.frame_dem_warning)
        layout_warn.setContentsMargins(8, 4, 8, 4)
        self.lbl_dem_warning = QLabel()
        self.lbl_dem_warning.setStyleSheet("color: #fef08a; font-size: 12px; line-height: 1.4;")
        self.lbl_dem_warning.setWordWrap(True)
        layout_warn.addWidget(self.lbl_dem_warning)
        self.frame_dem_warning.setVisible(False)
        layout_in.addWidget(self.frame_dem_warning, 2, 0, 1, 4)

        layout_main.addWidget(grp_in)

        # 2. 转换科学范式与参数配置
        grp_params = QGroupBox("2. 转换科学范式与参数配置 (Seeger & Minderhoud, Nature, 2026 理论范式改编)")
        layout_params = QGridLayout(grp_params)
        layout_params.setSpacing(8)

        layout_params.addWidget(QLabel("目标垂直基准:"), 0, 0)
        combo_target_datum = QComboBox()
        combo_target_datum.addItem("EGM2008 → 局部平均海平面 (Local MSL) (公式: Z_MSL = Z_EGM2008 - MDT - ΔN)", "msl")
        combo_target_datum.setEnabled(False)
        layout_params.addWidget(combo_target_datum, 0, 1, 1, 3)

        layout_params.addWidget(QLabel("MDT 模型与方法:"), 1, 0)
        combo_mdt_source = QComboBox()
        combo_mdt_source.addItem("CNES-CLS22 / CMEMS2020 混合大洋 MDT (原生大洋双线性插值 + 沿岸 3D-IDW 外推)", "cnes_cls22")
        combo_mdt_source.setEnabled(False)
        layout_params.addWidget(combo_mdt_source, 1, 1, 1, 3)

        layout_params.addWidget(QLabel("沿岸外推距离上限:"), 2, 0)
        self.spin_dem_max_dist = QDoubleSpinBox()
        self.spin_dem_max_dist.setRange(0.0, 100.0)
        self.spin_dem_max_dist.setValue(100.0)
        self.spin_dem_max_dist.setSingleStep(5.0)
        self.spin_dem_max_dist.setSuffix(" km")
        self.spin_dem_max_dist.setToolTip("MDT 沿岸 IDW 空间外推保守截断上限，严格限制在 0.0 ~ 100.0 km。\n(Seeger & Minderhoud 2026 原研究针对全球宏观尺度采用 500 km)")
        layout_params.addWidget(self.spin_dem_max_dist, 2, 1)

        layout_params.addWidget(QLabel("2D 分块流式大小:"), 2, 2)
        self.spin_dem_block_size = QSpinBox()
        self.spin_dem_block_size.setRange(64, 4096)
        self.spin_dem_block_size.setValue(512)
        self.spin_dem_block_size.setSingleStep(64)
        self.spin_dem_block_size.setSuffix(" px")
        layout_params.addWidget(self.spin_dem_block_size, 2, 3)

        self.chk_dem_apply_deltan = QCheckBox("包含高程异常差值改正 ΔN (GOCO06s/EIGEN-6C4 与 EGM2008 闭合改正)")
        self.chk_dem_apply_deltan.setChecked(True)
        self.chk_dem_apply_deltan.setEnabled(False)
        layout_params.addWidget(self.chk_dem_apply_deltan, 3, 0, 1, 2)

        self.chk_dem_save_qc = QCheckBox("保存转换质量控制掩膜 GeoTIFF (Conversion QC Mask)")
        self.chk_dem_save_qc.setChecked(False)
        self.chk_dem_save_qc.stateChanged.connect(self._on_dem_save_qc_toggled)
        layout_params.addWidget(self.chk_dem_save_qc, 3, 2, 1, 2)

        layout_main.addWidget(grp_params)

        # 3. 输出路径配置与任务控制
        grp_exec = QGroupBox("3. 输出路径配置与任务执行")
        layout_exec = QGridLayout(grp_exec)
        layout_exec.setSpacing(8)

        layout_exec.addWidget(QLabel("输出 DEM_MSL 文件:"), 0, 0)
        self.edit_dem_output = QLineEdit()
        self.edit_dem_output.setPlaceholderText("输出 DEM_MSL GeoTIFF 路径 (默认: <输入路径>_MSL.tif)...")
        layout_exec.addWidget(self.edit_dem_output, 0, 1)

        btn_browse_out = QPushButton("浏览...")
        btn_browse_out.setObjectName("btn_secondary")
        btn_browse_out.clicked.connect(self._browse_dem_output)
        layout_exec.addWidget(btn_browse_out, 0, 2)

        self.lbl_dem_qc_output = QLabel("输出 QC 掩膜文件:")
        layout_exec.addWidget(self.lbl_dem_qc_output, 1, 0)
        self.edit_dem_qc_output = QLineEdit()
        self.edit_dem_qc_output.setPlaceholderText("输出 QC 掩膜 GeoTIFF 路径...")
        layout_exec.addWidget(self.edit_dem_qc_output, 1, 1)

        self.btn_browse_dem_qc = QPushButton("浏览...")
        self.btn_browse_dem_qc.setObjectName("btn_secondary")
        self.btn_browse_dem_qc.clicked.connect(self._browse_dem_qc_output)
        layout_exec.addWidget(self.btn_browse_dem_qc, 1, 2)

        self.lbl_dem_qc_output.setVisible(False)
        self.edit_dem_qc_output.setVisible(False)
        self.btn_browse_dem_qc.setVisible(False)

        # 执行与取消按钮
        btn_box = QHBoxLayout()
        self.btn_run_dem_convert = QPushButton("🚀 开始 DEM 基准转换 (EGM2008 → MSL)")
        self.btn_run_dem_convert.setFixedHeight(40)
        self.btn_run_dem_convert.setStyleSheet("background-color: #059669; color: white; font-weight: bold; font-size: 13px;")
        self.btn_run_dem_convert.clicked.connect(self._run_dem_conversion)

        self.btn_cancel_dem_convert = QPushButton("🛑 取消任务")
        self.btn_cancel_dem_convert.setFixedHeight(40)
        self.btn_cancel_dem_convert.setEnabled(False)
        self.btn_cancel_dem_convert.setStyleSheet("background-color: #ef4444; color: white; font-weight: bold;")
        self.btn_cancel_dem_convert.clicked.connect(self._cancel_dem_conversion)

        btn_box.addWidget(self.btn_run_dem_convert, stretch=3)
        btn_box.addWidget(self.btn_cancel_dem_convert, stretch=1)
        layout_exec.addLayout(btn_box, 2, 0, 1, 3)

        self.prog_dem_convert = QProgressBar()
        self.prog_dem_convert.setValue(0)
        self.prog_dem_convert.setTextVisible(True)
        layout_exec.addWidget(self.prog_dem_convert, 3, 0, 1, 3)

        self.lbl_dem_status = QLabel("就绪 - 请选择输入 DEM (EGM2008) 影像并配置转换参数")
        self.lbl_dem_status.setStyleSheet("color: #94a3b8; font-size: 12px;")
        layout_exec.addWidget(self.lbl_dem_status, 4, 0, 1, 3)

        layout_main.addWidget(grp_exec)

        # 4. 转换结果摘要与下游分析直通卡片
        self.grp_dem_results = QGroupBox("4. 转换结果摘要与下游分析直通")
        layout_res = QVBoxLayout(self.grp_dem_results)
        layout_res.setSpacing(10)

        frame_summary = QFrame()
        frame_summary.setStyleSheet("background-color: #1a1d24; border: 1px solid #334155; border-radius: 6px; padding: 8px;")
        layout_sum = QGridLayout(frame_summary)
        layout_sum.setSpacing(6)

        layout_sum.addWidget(QLabel("产物文件路径:"), 0, 0)
        self.lbl_res_dem_path = QLabel("-")
        self.lbl_res_dem_path.setStyleSheet("font-weight: bold; color: #38bdf8;")
        layout_sum.addWidget(self.lbl_res_dem_path, 0, 1, 1, 3)

        layout_sum.addWidget(QLabel("栅格规格:"), 1, 0)
        self.lbl_res_dem_dims = QLabel("-")
        layout_sum.addWidget(self.lbl_res_dem_dims, 1, 1)

        layout_sum.addWidget(QLabel("有效 DEM 像元:"), 1, 2)
        self.lbl_res_dem_valid = QLabel("-")
        self.lbl_res_dem_valid.setStyleSheet("font-weight: bold; color: #4ade80;")
        layout_sum.addWidget(self.lbl_res_dem_valid, 1, 3)

        layout_sum.addWidget(QLabel("大洋双线性像元:"), 2, 0)
        self.lbl_res_dem_native = QLabel("-")
        layout_sum.addWidget(self.lbl_res_dem_native, 2, 1)

        layout_sum.addWidget(QLabel("沿岸 3D-IDW 外推:"), 2, 2)
        self.lbl_res_dem_extrap = QLabel("-")
        layout_sum.addWidget(self.lbl_res_dem_extrap, 2, 3)

        layout_sum.addWidget(QLabel("超出100km/NoData:"), 3, 0)
        self.lbl_res_dem_nodata = QLabel("-")
        layout_sum.addWidget(self.lbl_res_dem_nodata, 3, 1)

        layout_sum.addWidget(QLabel("执行耗时:"), 3, 2)
        self.lbl_res_dem_elapsed = QLabel("-")
        layout_sum.addWidget(self.lbl_res_dem_elapsed, 3, 3)

        layout_res.addWidget(frame_summary)

        # 直通操作按钮
        box_handoff = QHBoxLayout()
        self.btn_handoff_inund = QPushButton("📊 将此 DEM_MSL 载入单影像淹没频率分析")
        self.btn_handoff_inund.setFixedHeight(38)
        self.btn_handoff_inund.setStyleSheet("background-color: #2563eb; color: white; font-weight: bold; font-size: 12px; padding: 6px 12px;")
        self.btn_handoff_inund.clicked.connect(self._handoff_to_inundation)

        self.btn_handoff_exp = QPushButton("⏳ 将此 DEM_MSL 载入单影像露出时间分析")
        self.btn_handoff_exp.setFixedHeight(38)
        self.btn_handoff_exp.setStyleSheet("background-color: #0d9488; color: white; font-weight: bold; font-size: 12px; padding: 6px 12px;")
        self.btn_handoff_exp.clicked.connect(self._handoff_to_exposure)

        self.btn_dem_open_folder = QPushButton("📂 打开所在文件夹")
        self.btn_dem_open_folder.setFixedHeight(38)
        self.btn_dem_open_folder.setObjectName("btn_secondary")
        self.btn_dem_open_folder.clicked.connect(self._open_dem_output_folder)

        box_handoff.addWidget(self.btn_handoff_inund, stretch=2)
        box_handoff.addWidget(self.btn_handoff_exp, stretch=2)
        box_handoff.addWidget(self.btn_dem_open_folder, stretch=1)
        layout_res.addLayout(box_handoff)

        layout_main.addWidget(self.grp_dem_results)
        self.grp_dem_results.setVisible(False)

        layout_main.addStretch()

        scroll.setWidget(panel)
        tab_layout = QVBoxLayout(self.tab_dem_convert)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        tab_layout.addWidget(scroll)

    def _browse_dem_input(self):
        f, _ = QFileDialog.getOpenFileName(
            self, "选择输入 DEM 影像 (EGM2008 基准)", "", "GeoTIFF (*.tif *.tiff *.geotiff);;All Files (*.*)"
        )
        if f:
            self.edit_dem_input.setText(f)

    def _browse_dem_output(self):
        cur = self.edit_dem_output.text().strip()
        f, _ = QFileDialog.getSaveFileName(
            self, "保存输出 DEM_MSL 影像", cur, "GeoTIFF (*.tif *.tiff);;All Files (*.*)"
        )
        if f:
            self.edit_dem_output.setText(f)

    def _browse_dem_qc_output(self):
        cur = self.edit_dem_qc_output.text().strip()
        f, _ = QFileDialog.getSaveFileName(
            self, "保存 QC 掩膜影像", cur, "GeoTIFF (*.tif *.tiff);;All Files (*.*)"
        )
        if f:
            self.edit_dem_qc_output.setText(f)

    def _on_dem_save_qc_toggled(self, state):
        is_checked = (state == Qt.CheckState.Checked.value or state == True or state == 2)
        self.lbl_dem_qc_output.setVisible(is_checked)
        self.edit_dem_qc_output.setVisible(is_checked)
        self.btn_browse_dem_qc.setVisible(is_checked)

    def _on_dem_input_changed(self, text):
        path = text.strip()
        if os.path.exists(path) and os.path.isfile(path):
            base, ext = os.path.splitext(path)
            self.edit_dem_output.setText(f"{base}_MSL{ext}")
            self.edit_dem_qc_output.setText(f"{base}_MSL_qc{ext}")
            self._inspect_dem_input_ui(path)

    def _inspect_dem_input_ui(self, target_path=None):
        path = target_path if isinstance(target_path, str) else self.edit_dem_input.text().strip()
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, "文件无效", "请选择有效的 DEM GeoTIFF 文件")
            return
        try:
            import rasterio
            with rasterio.open(path) as src:
                w, h = src.width, src.height
                bands = src.count
                crs = src.crs
                res = src.res
                nodata = src.nodata
                b = src.bounds
                tags = src.tags()

            self.lbl_dem_dims.setText(f"{w} × {h} (总计 {w*h:,} 像元)")
            if bands == 1:
                self.lbl_dem_bands.setText(f"1 波段 (单波段高程 DEM - 正常)")
                self.lbl_dem_bands.setStyleSheet("font-weight: bold; color: #4ade80;")
            else:
                self.lbl_dem_bands.setText(f"⚠️ {bands} 波段 (DEM 通常必须为单波段)")
                self.lbl_dem_bands.setStyleSheet("font-weight: bold; color: #f59e0b;")

            crs_str = f"{crs.to_string()}" if crs else "未定义 (None)"
            if crs and crs.is_projected:
                self.lbl_dem_crs.setText(f"投影坐标系: {crs_str}")
            elif crs:
                self.lbl_dem_crs.setText(f"地理坐标系: {crs_str}")
            else:
                self.lbl_dem_crs.setText(f"⚠️ 坐标系未定义")
            self.lbl_dem_crs.setStyleSheet("font-weight: bold; color: #a78bfa;")

            is_proj = crs.is_projected if crs else False
            unit = "米" if is_proj else "度"
            self.lbl_dem_res.setText(f"({res[0]:.6g}, {res[1]:.6g}) [单位: {unit}]")
            self.lbl_dem_nodata.setText(f"{nodata}" if nodata is not None else "未指定 (None)")
            self.lbl_dem_bounds.setText(f"[{b.left:.4f}, {b.bottom:.4f}] -> [{b.right:.4f}, {b.top:.4f}]")

            # 双重转换检测 (Double Conversion Guard)
            datum_tag = str(tags.get('DATUM', '')).upper()
            target_tag = str(tags.get('TARGET_VERTICAL_DATUM', '')).upper()
            ref_tag = str(tags.get('ANALYSIS_REFERENCE', '')).upper()
            fn_upper = os.path.basename(path).upper()
            is_already_msl = (datum_tag == 'MSL' or target_tag == 'MSL' or ref_tag == 'MSL' or '_MSL' in fn_upper)

            if is_already_msl:
                self.frame_dem_warning.setVisible(True)
                tag_info = f"DATUM={datum_tag}" if datum_tag else "文件名含 _MSL"
                self.lbl_dem_warning.setText(
                    f"⚠️ 提示: 输入 DEM 元数据或文件名显示其已处于 MSL 局部平均海平面基准 ({tag_info})。\n"
                    f"无需重复执行基准转换！您可以直接点击下方直通按钮将该 DEM 载入淹没频率或露出时间分析，"
                    f"亦可点击下方的强制重新转换按钮。"
                )
                self.lbl_dem_detected_datum.setText("MSL (局部平均海平面 - 已是MSL基准)")
                self.lbl_dem_detected_datum.setStyleSheet("color: #4ade80; font-weight: bold;")
                self.btn_run_dem_convert.setText("⚠️ 强制重新转换 DEM 基准 (Force Re-convert)")
                # 展现直通卡片，方便用户直接使用当前 DEM
                self.grp_dem_results.setVisible(True)
                self.lbl_res_dem_path.setText(path)
                self.lbl_res_dem_dims.setText(f"{w} × {h} ({w*h:,} 像元)")
                self.lbl_res_dem_valid.setText("已就绪 (无需转换)")
                self.lbl_res_dem_native.setText("-")
                self.lbl_res_dem_extrap.setText("-")
                self.lbl_res_dem_nodata.setText(f"{nodata}")
                self.lbl_res_dem_elapsed.setText("0.00 s (已存在产物)")
            else:
                self.frame_dem_warning.setVisible(False)
                self.lbl_dem_detected_datum.setText("EGM2008 (大地水准面正高 - 待转换)")
                self.lbl_dem_detected_datum.setStyleSheet("color: #38bdf8; font-weight: bold;")
                self.btn_run_dem_convert.setText("🚀 开始 DEM 基准转换 (EGM2008 → MSL)")
                self.btn_run_dem_convert.setEnabled(True)

            self.status_bar.showMessage(f"已就绪: 已检查输入 DEM 元数据 ({os.path.basename(path)})")
        except Exception as e:
            QMessageBox.critical(self, "检查失败", f"无法解析 DEM GeoTIFF 元数据:\n{e}")

    def _run_dem_conversion(self):
        in_path = self.edit_dem_input.text().strip()
        out_path = self.edit_dem_output.text().strip()
        if not in_path or not os.path.exists(in_path):
            QMessageBox.warning(self, "输入无效", "请选择有效的输入 DEM GeoTIFF 文件。")
            return
        if not out_path:
            QMessageBox.warning(self, "输出路径无效", "请指定输出 DEM_MSL GeoTIFF 路径。")
            return

        max_dist_km = min(100.0, float(self.spin_dem_max_dist.value()))
        block_size = int(self.spin_dem_block_size.value())
        save_qc = self.chk_dem_save_qc.isChecked()
        qc_out = self.edit_dem_qc_output.text().strip() if save_qc else None

        self.btn_run_dem_convert.setEnabled(False)
        self.btn_cancel_dem_convert.setEnabled(True)
        self.prog_dem_convert.setValue(0)
        self.lbl_dem_status.setText("准备开始 DEM 垂直基准转换...")
        self.status_bar.showMessage("DEM 垂直基准转换进行中...")

        params = {
            'input_path': in_path,
            'output_path': out_path,
            'qc_output_path': qc_out,
            'max_dist_km': max_dist_km,
            'block_size': block_size,
            'allow_overwrite': True,
            'save_qc': save_qc
        }

        self.dem_worker = DEMDatumConversionWorker(params)
        self.dem_worker.progress.connect(self._on_dem_conversion_progress)
        self.dem_worker.finished.connect(self._on_dem_conversion_finished)
        self.dem_worker.error.connect(self._on_dem_conversion_error)
        self.dem_worker.cancelled.connect(self._on_dem_conversion_cancelled)
        self.dem_worker.start()

    def _cancel_dem_conversion(self):
        if self.dem_worker and self.dem_worker.isRunning():
            self.lbl_dem_status.setText("正在取消 DEM 基准转换任务...")
            self.btn_cancel_dem_convert.setEnabled(False)
            self.dem_worker.cancel()

    def _on_dem_conversion_progress(self, percent, msg):
        self.prog_dem_convert.setValue(percent)
        self.lbl_dem_status.setText(msg)

    def _on_dem_conversion_finished(self, summary):
        self.current_dem_summary = summary
        self.btn_run_dem_convert.setEnabled(True)
        self.btn_cancel_dem_convert.setEnabled(False)
        self.prog_dem_convert.setValue(100)
        self.lbl_dem_status.setText(f"基准转换成功完成！耗时: {summary.elapsed_seconds:.2f}s")
        self.status_bar.showMessage(f"DEM 基准转换成功: {os.path.basename(summary.output_path)}")

        # 展示结果卡片
        self.grp_dem_results.setVisible(True)
        self.lbl_res_dem_path.setText(summary.output_path)
        self.lbl_res_dem_dims.setText(f"{summary.width} × {summary.height} ({summary.total_pixels:,} 像元)")
        valid_pct = (summary.valid_dem_pixels / max(1, summary.total_pixels)) * 100.0
        self.lbl_res_dem_valid.setText(f"{summary.valid_dem_pixels:,} ({valid_pct:.1f}%)")
        native_pct = (summary.native_mdt_pixels / max(1, summary.valid_dem_pixels)) * 100.0
        self.lbl_res_dem_native.setText(f"{summary.native_mdt_pixels:,} ({native_pct:.1f}%)")
        extrap_pct = (summary.extrapolated_mdt_pixels / max(1, summary.valid_dem_pixels)) * 100.0
        self.lbl_res_dem_extrap.setText(f"{summary.extrapolated_mdt_pixels:,} ({extrap_pct:.1f}%)")
        self.lbl_res_dem_nodata.setText(f"{summary.nodata_pixels:,}")
        self.lbl_res_dem_elapsed.setText(f"{summary.elapsed_seconds:.2f} s")

    def _on_dem_conversion_error(self, err_msg):
        self.btn_run_dem_convert.setEnabled(True)
        self.btn_cancel_dem_convert.setEnabled(False)
        self.lbl_dem_status.setText(f"转换失败: {err_msg}")
        self.status_bar.showMessage("DEM 基准转换失败")
        QMessageBox.critical(self, "转换失败", f"DEM 基准转换发生错误:\n{err_msg}")

    def _on_dem_conversion_cancelled(self):
        self.btn_run_dem_convert.setEnabled(True)
        self.btn_cancel_dem_convert.setEnabled(False)
        self.lbl_dem_status.setText("DEM 基准转换已被用户取消。")
        self.status_bar.showMessage("DEM 基准转换已取消")

    def _handoff_to_inundation(self):
        out_path = self.lbl_res_dem_path.text().strip()
        if not out_path or not os.path.exists(out_path):
            out_path = self.edit_dem_output.text().strip()
        if not out_path or not os.path.exists(out_path):
            QMessageBox.warning(self, "文件未就绪", "转换后的 DEM_MSL 文件尚不存在，请先执行基准转换。")
            return

        self.tabs.setCurrentWidget(self.tab_raster)
        self.edit_raster_input.setText(out_path)
        idx_inund = self.combo_raster_mode.findData('inundation')
        if idx_inund >= 0:
            self.combo_raster_mode.setCurrentIndex(idx_inund)
        idx_msl = self.combo_inund_datum.findData('msl')
        if idx_msl >= 0:
            self.combo_inund_datum.setCurrentIndex(idx_msl)
        self._inspect_raster_ui(out_path)
        self.status_bar.showMessage(f"已就绪: 已将转换后的 DEM_MSL 载入单影像淹没频率分析模式")

    def _handoff_to_exposure(self):
        out_path = self.lbl_res_dem_path.text().strip()
        if not out_path or not os.path.exists(out_path):
            out_path = self.edit_dem_output.text().strip()
        if not out_path or not os.path.exists(out_path):
            QMessageBox.warning(self, "文件未就绪", "转换后的 DEM_MSL 文件尚不存在，请先执行基准转换。")
            return

        self.tabs.setCurrentWidget(self.tab_raster)
        self.edit_raster_input.setText(out_path)
        idx_exp = self.combo_raster_mode.findData('exposure')
        if idx_exp >= 0:
            self.combo_raster_mode.setCurrentIndex(idx_exp)
        idx_msl = self.combo_inund_datum.findData('msl')
        if idx_msl >= 0:
            self.combo_inund_datum.setCurrentIndex(idx_msl)
        self._inspect_raster_ui(out_path)
        self.status_bar.showMessage(f"已就绪: 已将转换后的 DEM_MSL 载入单影像露出时间分析模式")

    def _open_dem_output_folder(self):
        out_path = self.lbl_res_dem_path.text().strip()
        if not out_path:
            out_path = self.edit_dem_output.text().strip()
        folder = os.path.dirname(os.path.abspath(out_path)) if out_path else os.getcwd()
        if os.path.exists(folder):
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _setup_raster_tab(self):
        scroll = QScrollArea(self.tab_raster)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        panel = QWidget()
        layout_main = QVBoxLayout(panel)
        layout_main.setContentsMargins(10, 10, 10, 10)
        layout_main.setSpacing(12)

        # 1. 输入栅格与空间属性卡片
        grp_in = QGroupBox("1. 输入栅格与空间元数据检查")
        layout_in = QGridLayout(grp_in)
        layout_in.setSpacing(8)

        layout_in.addWidget(QLabel("输入 GeoTIFF 文件:"), 0, 0)
        self.edit_raster_input = QLineEdit()
        self.edit_raster_input.setPlaceholderText("请选择具备有效坐标参考系 (CRS) 的 GeoTIFF 影像或 DEM...")
        self.edit_raster_input.textChanged.connect(self._on_raster_input_changed)
        layout_in.addWidget(self.edit_raster_input, 0, 1)

        btn_browse_in = QPushButton("浏览文件...")
        btn_browse_in.setObjectName("btn_secondary")
        btn_browse_in.clicked.connect(self._browse_raster_input)
        layout_in.addWidget(btn_browse_in, 0, 2)

        btn_inspect = QPushButton("🔍 检查元数据")
        btn_inspect.setObjectName("btn_secondary")
        btn_inspect.clicked.connect(self._inspect_raster_ui)
        layout_in.addWidget(btn_inspect, 0, 3)

        # 元数据展示卡片
        frame_meta = QFrame()
        frame_meta.setStyleSheet("background-color: #1a1d24; border: 1px solid #334155; border-radius: 6px; padding: 6px;")
        layout_meta = QGridLayout(frame_meta)
        layout_meta.setSpacing(6)

        layout_meta.addWidget(QLabel("影像规格:"), 0, 0)
        self.lbl_raster_dims = QLabel("-")
        self.lbl_raster_dims.setStyleSheet("font-weight: bold; color: #38bdf8;")
        layout_meta.addWidget(self.lbl_raster_dims, 0, 1)

        layout_meta.addWidget(QLabel("坐标系统 (CRS):"), 0, 2)
        self.lbl_raster_crs = QLabel("-")
        self.lbl_raster_crs.setStyleSheet("font-weight: bold; color: #a78bfa;")
        layout_meta.addWidget(self.lbl_raster_crs, 0, 3)

        layout_meta.addWidget(QLabel("空间分辨率:"), 1, 0)
        self.lbl_raster_res = QLabel("-")
        layout_meta.addWidget(self.lbl_raster_res, 1, 1)

        layout_meta.addWidget(QLabel("NoData 值:"), 1, 2)
        self.lbl_raster_nodata = QLabel("-")
        layout_meta.addWidget(self.lbl_raster_nodata, 1, 3)

        layout_meta.addWidget(QLabel("空间范围 (Bounds):"), 2, 0)
        self.lbl_raster_bounds = QLabel("-")
        layout_meta.addWidget(self.lbl_raster_bounds, 2, 1, 1, 3)

        layout_in.addWidget(frame_meta, 1, 0, 1, 4)
        layout_main.addWidget(grp_in)

        # 2. 空间解算模式选择
        grp_mode = QGroupBox("2. 空间栅格解算任务模式")
        layout_mode = QGridLayout(grp_mode)
        layout_mode.setSpacing(8)

        layout_mode.addWidget(QLabel("任务类型:"), 0, 0)
        self.combo_raster_mode = QComboBox()
        self.combo_raster_mode.addItem("🌊 单时刻空间潮位 / 水面高程 (Snapshot Raster Mode)", "snapshot")
        self.combo_raster_mode.addItem("📊 潜在天文潮淹没频率 (Annual / Period Inundation Frequency)", "inundation")
        self.combo_raster_mode.addItem("⏳ 潜在天文潮露出时间域分析 (Exposure Duration & Events)", "exposure")
        self.combo_raster_mode.currentIndexChanged.connect(self._on_raster_mode_changed)
        layout_mode.addWidget(self.combo_raster_mode, 0, 1)

        layout_main.addWidget(grp_mode)

        # 3. 模式专属参数配置
        self.grp_params = QGroupBox("3. 模拟计算参数配置")
        layout_params = QVBoxLayout(self.grp_params)

        # 3.1 Snapshot 容器
        self.container_snapshot = QWidget()
        layout_snap = QGridLayout(self.container_snapshot)
        layout_snap.setContentsMargins(0, 0, 0, 0)
        layout_snap.setSpacing(8)

        layout_snap.addWidget(QLabel("解算快照时刻:"), 0, 0)
        self.time_raster_snap = QDateTimeEdit(QDateTime.currentDateTimeUtc())
        self.time_raster_snap.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.time_raster_snap.setCalendarPopup(True)
        layout_snap.addWidget(self.time_raster_snap, 0, 1)

        layout_snap.addWidget(QLabel("输入时刻时区:"), 0, 2)
        self.combo_snap_tz = QComboBox()
        self.combo_snap_tz.addItem("UTC (世界标准时)", "UTC")
        self.combo_snap_tz.addItem("本地时间 (Local Time)", "local")
        layout_snap.addWidget(self.combo_snap_tz, 0, 3)

        layout_snap.addWidget(QLabel("目标垂直基准:"), 1, 0)
        self.combo_snap_datum = QComboBox()
        self.combo_snap_datum.addItem("EGM2008 (大地水准面严密海拔正高)", "egm2008")
        self.combo_snap_datum.addItem("MSL (相对平均海平面)", "msl")
        self.combo_snap_datum.addItem("GOCO06s/EIGEN-6C4 (Tide+MDT)", "goco06s")
        self.combo_snap_datum.addItem("WGS84 (空间几何椭球高)", "wgs84")
        layout_snap.addWidget(self.combo_snap_datum, 1, 1)

        layout_snap.addWidget(QLabel("分潮方案:"), 1, 2)
        self.combo_snap_const = QComboBox()
        self.combo_snap_const.addItem("全部 34 个主分潮 (全精度)", "all")
        self.combo_snap_const.addItem("8 个核心主分潮 (快速预览)", "major8")
        layout_snap.addWidget(self.combo_snap_const, 1, 3)

        layout_params.addWidget(self.container_snapshot)

        # 3.2 Inundation 容器
        self.container_inund = QWidget()
        layout_inund = QGridLayout(self.container_inund)
        layout_inund.setContentsMargins(0, 0, 0, 0)
        layout_inund.setSpacing(8)

        layout_inund.addWidget(QLabel("时段模式:"), 0, 0)
        self.combo_inund_time_mode = QComboBox()
        self.combo_inund_time_mode.addItem("整年快捷模式 (Year Mode)", "year")
        self.combo_inund_time_mode.addItem("自定义时段 (Custom Period)", "period")
        self.combo_inund_time_mode.currentIndexChanged.connect(self._on_inund_time_mode_changed)
        layout_inund.addWidget(self.combo_inund_time_mode, 0, 1)

        self.lbl_inund_year = QLabel("预测年份:")
        layout_inund.addWidget(self.lbl_inund_year, 0, 2)
        self.spin_inund_year = QSpinBox()
        self.spin_inund_year.setRange(1950, 2099)
        self.spin_inund_year.setValue(2024)
        layout_inund.addWidget(self.spin_inund_year, 0, 3)

        self.lbl_inund_start = QLabel("起始时间:")
        layout_inund.addWidget(self.lbl_inund_start, 1, 0)
        self.time_inund_start = QDateTimeEdit(QDateTime.currentDateTimeUtc())
        self.time_inund_start.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.time_inund_start.setCalendarPopup(True)
        layout_inund.addWidget(self.time_inund_start, 1, 1)

        self.lbl_inund_end = QLabel("结束时间:")
        layout_inund.addWidget(self.lbl_inund_end, 1, 2)
        self.time_inund_end = QDateTimeEdit(QDateTime.currentDateTimeUtc().addDays(30))
        self.time_inund_end.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.time_inund_end.setCalendarPopup(True)
        layout_inund.addWidget(self.time_inund_end, 1, 3)

        self.lbl_inund_start.setVisible(False)
        self.time_inund_start.setVisible(False)
        self.lbl_inund_end.setVisible(False)
        self.time_inund_end.setVisible(False)

        layout_inund.addWidget(QLabel("采样步长:"), 2, 0)
        self.combo_inund_freq = QComboBox()
        self.combo_inund_freq.addItem("30分钟 (30min - 标准推荐)", "30min")
        self.combo_inund_freq.addItem("1小时 (1h - 快速解算)", "1h")
        self.combo_inund_freq.addItem("15分钟 (15min - 高精度)", "15min")
        self.combo_inund_freq.addItem("10分钟 (10min)", "10min")
        self.combo_inund_freq.addItem("5分钟 (5min)", "5min")
        layout_inund.addWidget(self.combo_inund_freq, 2, 1)

        layout_inund.addWidget(QLabel("DEM基准面:"), 2, 2)
        self.combo_inund_datum = QComboBox()
        self.combo_inund_datum.addItem("MSL (相对平均海平面 - v1.7推荐)", "msl")
        self.combo_inund_datum.addItem("EGM2008 (相对 EGM2008 参考面 - 兼容模式)", "egm2008")
        self.combo_inund_datum.addItem("GOCO06s/EIGEN-6C4 (Tide+MDT)", "goco06s")
        self.combo_inund_datum.addItem("WGS84 (空间几何椭球高)", "wgs84")
        self.combo_inund_datum.currentIndexChanged.connect(self._on_inund_datum_changed)
        layout_inund.addWidget(self.combo_inund_datum, 2, 3)

        layout_inund.addWidget(QLabel("目标区域:"), 3, 0)
        self.combo_inund_target_mode = QComboBox()
        self.combo_inund_target_mode.addItem("潮间带模式 (intertidal - 推荐)", "intertidal")
        self.combo_inund_target_mode.addItem("全域网格模式 (standard)", "standard")
        self.combo_inund_target_mode.setToolTip("长周期栅格产品目标区域解算模式 (支持潜在淹没频率与潜在露出时长)")
        self.combo_raster_target_mode = self.combo_inund_target_mode
        layout_inund.addWidget(self.combo_inund_target_mode, 3, 1, 1, 3)

        self.lbl_inund_qc = QLabel("QC掩膜输出:")
        layout_inund.addWidget(self.lbl_inund_qc, 4, 0)
        self.edit_inund_qc = QLineEdit()
        self.edit_inund_qc.setPlaceholderText("留空则自动保存为 <主输出>_qc.tif")
        layout_inund.addWidget(self.edit_inund_qc, 4, 1, 1, 2)

        self.btn_browse_qc = QPushButton("浏览...")
        self.btn_browse_qc.setObjectName("btn_secondary")
        self.btn_browse_qc.clicked.connect(self._browse_inund_qc)
        layout_inund.addWidget(self.btn_browse_qc, 4, 3)

        self.lbl_inund_datum_hint = QLabel("✅ MSL 推荐模式：高程基准严密对齐，水深与淹没直接对比 DEM_MSL (Seeger & Minderhoud, Nature, 2026 范式)。")
        self.lbl_inund_datum_hint.setStyleSheet("color: #4ade80; font-size: 11px;")
        self.lbl_inund_datum_hint.setWordWrap(True)
        layout_inund.addWidget(self.lbl_inund_datum_hint, 5, 0, 1, 4)

        layout_params.addWidget(self.container_inund)
        self.container_inund.setVisible(False)

        layout_main.addWidget(self.grp_params)

        # 4. 自适应网格高级参数
        self.grp_grid = QGroupBox("4. 自适应潮位控制网格与性能优化参数")
        layout_grid = QGridLayout(self.grp_grid)
        cfg = load_app_config()
        raster_cfg = cfg.get('raster', {})
        init_sp = float(raster_cfg.get('initial_control_spacing_m', 4000.0))
        min_sp = float(raster_cfg.get('min_control_spacing_m', 500.0))
        tol = float(raster_cfg.get('inundation_error_tolerance_pct', 1.0))
        blk = int(raster_cfg.get('default_block_size', 512))

        layout_grid.addWidget(QLabel("初始网格间距:"), 0, 0)
        self.spin_grid_init = QDoubleSpinBox()
        self.spin_grid_init.setRange(500.0, 50000.0)
        self.spin_grid_init.setValue(init_sp)
        self.spin_grid_init.setSingleStep(500.0)
        self.spin_grid_init.setSuffix(" m")
        layout_grid.addWidget(self.spin_grid_init, 0, 1)

        layout_grid.addWidget(QLabel("最小允许间距:"), 0, 2)
        self.spin_grid_min = QDoubleSpinBox()
        self.spin_grid_min.setRange(50.0, 10000.0)
        self.spin_grid_min.setValue(min_sp)
        self.spin_grid_min.setSingleStep(100.0)
        self.spin_grid_min.setSuffix(" m")
        layout_grid.addWidget(self.spin_grid_min, 0, 3)

        layout_grid.addWidget(QLabel("容错误差阈值:"), 1, 0)
        self.spin_grid_tol = QDoubleSpinBox()
        self.spin_grid_tol.setRange(0.1, 20.0)
        self.spin_grid_tol.setValue(tol)
        self.spin_grid_tol.setSingleStep(0.2)
        self.spin_grid_tol.setSuffix(" %")
        layout_grid.addWidget(self.spin_grid_tol, 1, 1)

        layout_grid.addWidget(QLabel("2D 分块大小:"), 1, 2)
        self.spin_grid_block = QSpinBox()
        self.spin_grid_block.setRange(64, 4096)
        self.spin_grid_block.setValue(blk)
        self.spin_grid_block.setSingleStep(64)
        self.spin_grid_block.setSuffix(" px")
        layout_grid.addWidget(self.spin_grid_block, 1, 3)

        self.chk_raster_strict = QCheckBox("严密基准校验 (若关键大地水准面/差值网格缺失则中断拦截，防止粗糙外推)")
        self.chk_raster_strict.setChecked(True)
        layout_grid.addWidget(self.chk_raster_strict, 2, 0, 1, 4)

        layout_main.addWidget(self.grp_grid)
        self.grp_grid.setVisible(False)

        # 5. 输出路径与任务执行
        grp_exec = QGroupBox("5. 输出路径配置与任务执行")
        layout_exec = QGridLayout(grp_exec)
        layout_exec.setSpacing(8)

        self.lbl_raster_output = QLabel("输出 GeoTIFF 文件:")
        layout_exec.addWidget(self.lbl_raster_output, 0, 0)
        self.edit_raster_output = QLineEdit()
        self.edit_raster_output.setPlaceholderText("输出 GeoTIFF 路径...")
        layout_exec.addWidget(self.edit_raster_output, 0, 1)

        btn_browse_out = QPushButton("浏览...")
        btn_browse_out.setObjectName("btn_secondary")
        btn_browse_out.clicked.connect(self._browse_raster_output)
        layout_exec.addWidget(btn_browse_out, 0, 2)

        # 执行与取消按钮
        btn_box = QHBoxLayout()
        self.btn_run_raster = QPushButton("🚀 开始空间栅格解算")
        self.btn_run_raster.setFixedHeight(40)
        self.btn_run_raster.clicked.connect(self._run_raster_simulation)

        self.btn_cancel_raster = QPushButton("🛑 取消任务")
        self.btn_cancel_raster.setFixedHeight(40)
        self.btn_cancel_raster.setEnabled(False)
        self.btn_cancel_raster.setStyleSheet("background-color: #ef4444; color: white; font-weight: bold;")
        self.btn_cancel_raster.clicked.connect(self._cancel_raster_simulation)

        btn_box.addWidget(self.btn_run_raster, stretch=3)
        btn_box.addWidget(self.btn_cancel_raster, stretch=1)
        layout_exec.addLayout(btn_box, 1, 0, 1, 3)

        self.prog_raster = QProgressBar()
        self.prog_raster.setValue(0)
        self.prog_raster.setTextVisible(True)
        layout_exec.addWidget(self.prog_raster, 2, 0, 1, 3)

        self.lbl_raster_status = QLabel("就绪 - 请选择输入 GeoTIFF 影像并配置解算参数")
        self.lbl_raster_status.setStyleSheet("color: #94a3b8; font-size: 12px;")
        layout_exec.addWidget(self.lbl_raster_status, 3, 0, 1, 3)

        layout_main.addWidget(grp_exec)
        layout_main.addStretch()

        scroll.setWidget(panel)
        tab_layout = QVBoxLayout(self.tab_raster)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        tab_layout.addWidget(scroll)

    def _set_default_values(self):
        app_cfg = load_app_config()
        gui_cfg = app_cfg.get('gui', {})
        tide_cfg = app_cfg.get('tide', {})

        default_loc = gui_cfg.get('default_location_name', "长江口 (Changjiang Estuary)")
        idx = self.combo_presets.findText(default_loc)
        if idx >= 0:
            self.combo_presets.setCurrentIndex(idx)

        default_tz = gui_cfg.get('default_timezone', 'UTC')
        self._current_tz_mode = default_tz
        idx_tz = self.combo_tz.findData(default_tz)
        if idx_tz >= 0:
            self.combo_tz.setCurrentIndex(idx_tz)

        # 联动 config.yaml 分潮配置
        def_const = tide_cfg.get('default_constituents', 'all')
        idx_const = self.combo_const.findData(def_const)
        if idx_const >= 0:
            self.combo_const.setCurrentIndex(idx_const)

        # 联动 config.yaml 采样步长配置
        def_freq = tide_cfg.get('default_freq', '30min')
        self._user_selected_freq = def_freq
        idx_freq = self.combo_freq.findData(def_freq)
        if idx_freq >= 0:
            self.combo_freq.setCurrentIndex(idx_freq)

        if default_tz == 'UTC':
            now_dt = QDateTime.currentDateTimeUtc()
        else:
            now_dt = QDateTime.currentDateTime()

        self.time_start.setDateTime(now_dt)
        self.time_end.setDateTime(now_dt.addDays(1))

        self.lbl_year.setVisible(False)
        self.spin_year.setVisible(False)
        self.current_scalar_datum = {}
        self._update_sample_estimate()

    def _on_freq_user_changed(self, index):
        self._user_selected_freq = self.combo_freq.currentData()
        self._update_sample_estimate()

    def _on_compute_datum_changed(self):
        compute_mode = self.combo_compute_datum.currentData()
        current_display = self.combo_display_datum.currentData()
        self.combo_display_datum.blockSignals(True)
        self.combo_display_datum.clear()

        if compute_mode == 'msl':
            options = [("MSL (相对平均海平面)", "msl")]
        elif compute_mode in ['egm2008', 'both']:
            options = [
                ("EGM2008 (大地水准面正高)", "egm"),
                ("MSL (相对平均海平面)", "msl")
            ]
        elif compute_mode == 'mdt_ref':
            options = [
                ("GOCO06s/EIGEN-6C4 (Tide+MDT)", "goco"),
                ("MSL (相对平均海平面)", "msl")
            ]
        elif compute_mode == 'all':
            options = [
                ("EGM2008 (大地水准面正高)", "egm"),
                ("MSL (相对平均海平面)", "msl"),
                ("GOCO06s/EIGEN-6C4 (Tide+MDT)", "goco"),
                ("WGS84 (空间几何椭球高)", "wgs")
            ]
        else:
            options = [
                ("EGM2008 (大地水准面正高)", "egm"),
                ("MSL (相对平均海平面)", "msl")
            ]

        for text, key in options:
            self.combo_display_datum.addItem(text, key)

        new_idx = self.combo_display_datum.findData(current_display)
        if new_idx >= 0:
            self.combo_display_datum.setCurrentIndex(new_idx)
        else:
            self.combo_display_datum.setCurrentIndex(0)

        self.combo_display_datum.setEnabled(len(options) > 1)
        self.combo_display_datum.blockSignals(False)

        if self.current_result_df is not None:
            self._update_stat_cards(self.current_result_df)
            self._update_chart()

    def _on_time_mode_changed(self):
        mode = self.combo_time_mode.currentData()
        is_year = (mode == 'year')
        self.lbl_start.setVisible(not is_year)
        self.time_start.setVisible(not is_year)
        self.lbl_end.setVisible(not is_year)
        self.time_end.setVisible(not is_year)
        self.lbl_year.setVisible(is_year)
        self.spin_year.setVisible(is_year)

        if is_year:
            if self.combo_freq.currentData() != '30min':
                idx = self.combo_freq.findData('30min')
                if idx >= 0:
                    self.combo_freq.blockSignals(True)
                    self.combo_freq.setCurrentIndex(idx)
                    self.combo_freq.blockSignals(False)
                self.status_bar.showMessage("已自动切换至整年模式推荐采样间隔 (30min)。")
        else:
            if hasattr(self, '_user_selected_freq') and self._user_selected_freq:
                idx = self.combo_freq.findData(self._user_selected_freq)
                if idx >= 0:
                    self.combo_freq.blockSignals(True)
                    self.combo_freq.setCurrentIndex(idx)
                    self.combo_freq.blockSignals(False)

        self._update_sample_estimate()

    def _update_sample_estimate(self):
        mode = self.combo_time_mode.currentData()
        freq = self.combo_freq.currentData() or "30min"
        freq_minutes = 30
        if 'min' in freq:
            try:
                freq_minutes = int(freq.replace('min', ''))
            except Exception:
                freq_minutes = 30
        elif 'h' in freq:
            try:
                freq_minutes = int(freq.replace('h', '')) * 60
            except Exception:
                freq_minutes = 60

        if mode == 'year':
            year = self.spin_year.value()
            import calendar
            days = 366 if calendar.isleap(year) else 365
            total_samples = int(days * 24 * (60 / freq_minutes))
            self.lbl_sample_count.setText(f"{total_samples:,} 点 (半开区间)")
        else:
            dt_start = self.time_start.dateTime().toPyDateTime()
            dt_end = self.time_end.dateTime().toPyDateTime()
            diff_sec = (dt_end - dt_start).total_seconds()
            if diff_sec <= 0:
                self.lbl_sample_count.setText("非法 (结束需晚于起始)")
            else:
                samples = int(diff_sec / (freq_minutes * 60)) + 1
                self.lbl_sample_count.setText(f"约 {samples:,} 点 (闭区间)")

    def _on_timezone_changed(self):
        new_tz = self.combo_tz.currentData()
        if not hasattr(self, '_current_tz_mode'):
            self._current_tz_mode = new_tz
            return
        if new_tz == self._current_tz_mode:
            return

        from datetime import timezone
        if new_tz == 'local' and self._current_tz_mode == 'UTC':
            # 将当前界面显示的 UTC 时间转换为本地时间显示
            start_pydt = self.time_start.dateTime().toPyDateTime().replace(tzinfo=timezone.utc).astimezone()
            end_pydt = self.time_end.dateTime().toPyDateTime().replace(tzinfo=timezone.utc).astimezone()
            self.time_start.setDateTime(QDateTime(start_pydt.year, start_pydt.month, start_pydt.day, start_pydt.hour, start_pydt.minute, start_pydt.second))
            self.time_end.setDateTime(QDateTime(end_pydt.year, end_pydt.month, end_pydt.day, end_pydt.hour, end_pydt.minute, end_pydt.second))
        elif new_tz == 'UTC' and self._current_tz_mode == 'local':
            # 将当前界面显示的本地时间转换为 UTC 时间显示
            start_pydt = self.time_start.dateTime().toPyDateTime().astimezone().astimezone(timezone.utc)
            end_pydt = self.time_end.dateTime().toPyDateTime().astimezone().astimezone(timezone.utc)
            self.time_start.setDateTime(QDateTime(start_pydt.year, start_pydt.month, start_pydt.day, start_pydt.hour, start_pydt.minute, start_pydt.second))
            self.time_end.setDateTime(QDateTime(end_pydt.year, end_pydt.month, end_pydt.day, end_pydt.hour, end_pydt.minute, end_pydt.second))

        self._current_tz_mode = new_tz
        if self.current_result_df is not None:
            # 动态根据当前选定时区重算 datetime_input 列并刷新表格与图表
            if new_tz == 'local':
                utc_series = pd.to_datetime(self.current_result_df['datetime_utc'])
                tz_loc = dateutil.tz.tzlocal()
                self.current_result_df['datetime_input'] = (
                    utc_series.dt.tz_localize('UTC').dt.tz_convert(tz_loc).dt.tz_localize(None)
                )
            else:
                self.current_result_df['datetime_input'] = pd.to_datetime(self.current_result_df['datetime_utc'])

            self._populate_table(self.current_result_df)
            self._update_chart()

    def _on_preset_changed(self, index):
        name = self.combo_presets.currentText()
        if name in COASTAL_PRESETS:
            preset = COASTAL_PRESETS[name]
            self.edit_lon.setText(f"{preset['lon']:.4f}")
            self.edit_lat.setText(f"{preset['lat']:.4f}")
            self.status_bar.showMessage(f"已选择预设: {name} - {preset['desc']}")

    def _on_datum_display_changed(self):
        if self.current_result_df is not None:
            self._update_stat_cards(self.current_result_df)
            self._update_chart()

    def _run_single_simulation(self):
        try:
            lon = float(self.edit_lon.text().strip())
            lat = float(self.edit_lat.text().strip())
        except ValueError:
            QMessageBox.warning(self, "输入错误", "请输入有效的十进制经纬度数字！")
            return

        if not (-90.0 <= lat <= 90.0):
            QMessageBox.warning(self, "纬度超界", "纬度范围必须在 -90° 到 +90° 之间！")
            return

        mode = self.combo_time_mode.currentData()
        freq = self.combo_freq.currentData() or "30min"
        constituents = self.combo_const.currentData()
        source_tz = self.combo_tz.currentData()
        datum_mode = self.combo_compute_datum.currentData()

        if mode == 'year':
            year = self.spin_year.value()
            t_start = f"{year:04d}-01-01 00:00:00"
            t_end = f"{year + 1:04d}-01-01 00:00:00"
            inclusive = 'left'
        else:
            t_start = self.time_start.dateTime().toString("yyyy-MM-dd HH:mm:ss")
            t_end = self.time_end.dateTime().toString("yyyy-MM-dd HH:mm:ss")
            inclusive = 'both'
            if self.time_start.dateTime() >= self.time_end.dateTime():
                QMessageBox.warning(self, "时间错误", "起始时间必须早于结束时间！")
                return

        self.btn_run_single.setEnabled(False)
        self.prog_single.setValue(5)
        self.status_bar.showMessage("正在后台加载网格并解算潮位...")

        self.worker = SingleTideWorker(
            lon=lon,
            lat=lat,
            start_time=t_start,
            end_time=t_end,
            freq=freq,
            constituents=constituents,
            source_tz=source_tz,
            datum_mode=datum_mode,
            inclusive=inclusive
        )
        self.worker.progress.connect(self._on_single_progress)
        self.worker.finished.connect(self._on_single_finished)
        self.worker.error.connect(self._on_single_error)
        self.worker.start()

    def _on_single_progress(self, percent, msg):
        self.prog_single.setValue(percent)
        self.status_bar.showMessage(msg)

    def _on_single_finished(self, df, scalar_datum):
        self.current_result_df = df
        self.current_scalar_datum = scalar_datum
        self.btn_run_single.setEnabled(True)
        self.btn_export_csv.setEnabled(True)
        self.btn_export_excel.setEnabled(True)
        self.prog_single.setValue(100)
        self.status_bar.showMessage(f"模拟计算完成！共生成 {len(df):,} 个时间步长点。")

        # 更新极值指标卡片与常数
        self._update_stat_cards(df)

        # 刷新图表与表格
        self._update_chart()
        self._populate_table(df)

    def _update_stat_cards(self, df):
        """根据用户选定的显示基准面，动态更新极值统计卡片"""
        datum_mode = self.combo_display_datum.currentData()

        if datum_mode == 'msl':
            col = 'tide_msl_m' if 'tide_msl_m' in df.columns else 'tide_total_m'
            target_name = "MSL (相对海平面)"
        elif datum_mode in ['goco', 'mdt_ref']:
            col = 'h_mdt_ref_m' if 'h_mdt_ref_m' in df.columns else ('h_goco06s_m' if 'h_goco06s_m' in df.columns else None)
            target_name = "MDT原始参考面 (GOCO06s / EIGEN-6C4)"
        elif datum_mode == 'wgs':
            col = 'h_wgs84_m'
            target_name = "WGS84 (几何空间椭球高)"
        else:  # 'both', 'egm', 'all'
            col = 'h_egm2008_m'
            target_name = "EGM2008 (大地水准面正高)"

        self.lbl_stat_target.setText(f"<b>{target_name}</b>")

        if col and col in df.columns:
            vals = df[col].values
            valid_vals = vals[~np.isnan(vals)]
            if len(valid_vals) > 0:
                max_val = np.max(valid_vals)
                min_val = np.min(valid_vals)
                range_val = max_val - min_val
                self.lbl_max.setText(f"<b style='color:#ef4444;'>{max_val:+.2f} m</b>")
                self.lbl_min.setText(f"<b style='color:#10b981;'>{min_val:+.2f} m</b>")
                self.lbl_range.setText(f"<b>{range_val:.2f} m</b>")
            else:
                if 'tide_msl_m' in df.columns and datum_mode != 'msl':
                    hint = "未在计算时包含大地基准 (仅选了MSL)"
                else:
                    hint = "NaN (陆地/无数据)"
                self.lbl_max.setText(f"<span style='color:#94a3b8;'>{hint}</span>")
                self.lbl_min.setText(f"<span style='color:#94a3b8;'>{hint}</span>")
                self.lbl_range.setText("<span style='color:#94a3b8;'>-</span>")
        else:
            hint = "未在本次模拟中解算该基准面"
            self.lbl_max.setText(f"<span style='color:#94a3b8;'>{hint}</span>")
            self.lbl_min.setText(f"<span style='color:#94a3b8;'>{hint}</span>")
            self.lbl_range.setText("<span style='color:#94a3b8;'>-</span>")

        # 更新静态高程基准参数
        mdt_v = _safe_float(self.current_scalar_datum.get('mdt_m', np.nan))
        dn_v = _safe_float(self.current_scalar_datum.get('delta_n_m', np.nan))
        geoid_v = _safe_float(self.current_scalar_datum.get('n_egm2008_m', np.nan))
        ref_g = str(self.current_scalar_datum.get('datum_ref_geoid', 'GOCO06s'))

        self.lbl_mdt.setText(f"<b>{mdt_v:+.4f} m</b>" if np.isfinite(mdt_v) else "<span style='color:#94a3b8;'>NaN</span>")
        if np.isfinite(dn_v):
            self.lbl_delta_n.setText(f"<b>{dn_v:+.4f} m</b> <span style='font-size:10px;color:#94a3b8;'>({ref_g})</span>")
        else:
            self.lbl_delta_n.setText(f"<span style='color:#94a3b8;'>NaN ({ref_g})</span>")
        self.lbl_geoid_n.setText(f"<b>{geoid_v:+.3f} m</b>" if np.isfinite(geoid_v) else "<span style='color:#94a3b8;'>NaN</span>")

        # 更新网格质量评价标识 (严密词汇)
        qc_warn = self.current_scalar_datum.get('qc_warning', 'NORMAL')
        if qc_warn == 'AUTHORITATIVE_MASK':
            self.lbl_qc_status.setText(f"<span style='color:#10b981;font-weight:bold;'>✅ {ref_g} (权威来源掩膜)</span>")
        elif qc_warn in ['QC_DATUM_SOURCE_APPROX', 'POLYGON_FALLBACK', 'NORMAL', 'QC_MED_BLACK_SEA_EIGEN6C4', 'QC_DATUM_SOURCE_NORMAL']:
            self.lbl_qc_status.setText(f"<span style='color:#38bdf8;font-weight:bold;'>ℹ️ {ref_g} (几何多边形近似判定)</span>")
        elif qc_warn in ['QC_DATUM_INVALID', 'INVALID']:
            self.lbl_qc_status.setText("<span style='color:#ef4444;font-weight:bold;'>❌ 无法确定 / 无效基准</span>")
        elif 'quality_flag' in df.columns:
            flags = df['quality_flag'].values
            if (flags == 0).any():
                self.lbl_qc_status.setText("<span style='color:#ef4444;font-weight:bold;'>⚠️ 包含无数据/陆地点 (Flag 0)</span>")
            elif (flags < 0).any():
                self.lbl_qc_status.setText("<span style='color:#f59e0b;font-weight:bold;'>⚠️ 存在近岸动力学外推 (Flag < 0)</span>")
            else:
                self.lbl_qc_status.setText("<span style='color:#10b981;font-weight:bold;'>✅ 全程高保真有效 (Flag 1~6)</span>")
        else:
            self.lbl_qc_status.setText("-")

    def _on_single_error(self, err_msg):
        self.btn_run_single.setEnabled(True)
        self.prog_single.setValue(0)
        self.status_bar.showMessage("解算发生错误")
        QMessageBox.critical(self, "解算错误", f"潮位模拟失败:\n{err_msg}")

    def _update_chart(self):
        if self.current_result_df is None:
            return

        datum_mode = self.combo_display_datum.currentData()
        time_mode = self.combo_tz.currentData()
        time_col = 'datetime_utc' if time_mode == 'UTC' else 'datetime_input'

        preset_name = self.combo_presets.currentText()
        loc_title = preset_name if preset_name != "自定义坐标..." else f"({self.edit_lon.text()}°, {self.edit_lat.text()}°)"

        self.chart_widget.plot_tide_series(
            self.current_result_df,
            location_title=loc_title,
            datum_mode=datum_mode,
            time_col=time_col
        )

    def _populate_table(self, df):
        preview_limit = 2000
        total_rows = len(df)
        df_view = df.iloc[:preview_limit] if total_rows > preview_limit else df

        self.table_single.setRowCount(0)
        self.table_single.setRowCount(len(df_view))

        def _fmt(val, decimals=3):
            if val is None or pd.isna(val):
                return "NaN"
            return f"{float(val):+.{decimals}f}"

        for row_idx in range(len(df_view)):
            row = df_view.iloc[row_idx]
            t_utc = str(row.get('datetime_utc', ''))[:19]
            t_inp = str(row.get('datetime_input', row.get('datetime', '')))[:19]
            msl_val = _fmt(row.get('tide_msl_m', row.get('tide_total_m', np.nan)), 3)
            mdt_val = _fmt(row.get('mdt_m', np.nan), 3)
            dn_val = _fmt(row.get('delta_n_m', np.nan), 3)
            egm_val = _fmt(row.get('h_egm2008_m', np.nan), 3)
            wgs_val = _fmt(row.get('h_wgs84_m', np.nan), 3)
            flag_raw = row.get('quality_flag', 0)
            flag_val = int(flag_raw) if not pd.isna(flag_raw) else 0

            text_color = None
            if flag_val == 0:
                text_color = QColor("#ef4444")
            elif flag_val < 0:
                text_color = QColor("#f59e0b")

            items = [t_utc, t_inp, msl_val, mdt_val, dn_val, egm_val, wgs_val, str(flag_val)]
            for col_idx, text in enumerate(items):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if text_color is not None:
                    item.setForeground(text_color)
                self.table_single.setItem(row_idx, col_idx, item)

        if total_rows > preview_limit:
            self.status_bar.showMessage(
                f"模拟计算完成！共生成 {total_rows:,} 个时间步长点 (界面表格已截断预览前 {preview_limit:,} 行，可通过导出按钮全量保存)。"
            )

    def _export_csv(self):
        if self.current_result_df is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出为 CSV 文件", "predicted_tide.csv", "CSV Files (*.csv)")
        if path:
            export_dataframe(self.current_result_df, path)
            QMessageBox.information(self, "导出成功", f"结果已保存至:\n{path}")

    def _export_excel(self):
        if self.current_result_df is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出为 Excel 文件", "predicted_tide.xlsx", "Excel Files (*.xlsx)")
        if path:
            export_dataframe(self.current_result_df, path)
            QMessageBox.information(self, "导出成功", f"结果已保存至:\n{path}")

    # 批量处理逻辑
    def _browse_batch_csv(self):
        f, _ = QFileDialog.getOpenFileName(self, "打开批量点 CSV 文件", "", "CSV Files (*.csv)")
        if f:
            self.edit_batch_file.setText(f)
            try:
                df_temp = pd.read_csv(f, nrows=5)
                cols = list(df_temp.columns)

                self.combo_col_lon.clear()
                self.combo_col_lat.clear()
                self.combo_col_time.clear()

                self.combo_col_lon.addItems(cols)
                self.combo_col_lat.addItems(cols)
                self.combo_col_time.addItems(cols)

                # 智能推断列名
                for col in cols:
                    cl = col.lower()
                    if 'lon' in cl or '经度' in cl:
                        self.combo_col_lon.setCurrentText(col)
                    elif 'lat' in cl or '纬度' in cl:
                        self.combo_col_lat.setCurrentText(col)
                    elif 'time' in cl or 'date' in cl:
                        self.combo_col_time.setCurrentText(col)

                self.btn_run_batch.setEnabled(True)
                self.status_bar.showMessage(f"已加载输入表格，共识别到 {len(cols)} 个字段。")
            except Exception as e:
                QMessageBox.critical(self, "读取错误", f"解析 CSV 表头失败: {e}")

    def _run_batch_simulation(self):
        csv_path = self.edit_batch_file.text().strip()
        try:
            df_full = pd.read_csv(csv_path)
        except Exception as e:
            QMessageBox.critical(self, "读取错误", f"读取 CSV 失败: {e}")
            return

        lon_col = self.combo_col_lon.currentText()
        lat_col = self.combo_col_lat.currentText()
        time_col = self.combo_col_time.currentText()
        source_tz = self.combo_batch_tz.currentData()
        datum_mode = self.combo_batch_datum.currentData()

        self.btn_run_batch.setEnabled(False)
        self.prog_batch.setValue(10)

        self.batch_worker = BatchTideWorker(
            df_records=df_full,
            lon_col=lon_col,
            lat_col=lat_col,
            time_col=time_col,
            constituents='all',
            source_tz=source_tz,
            datum_mode=datum_mode
        )
        self.batch_worker.progress.connect(lambda p, m: (self.prog_batch.setValue(p), self.status_bar.showMessage(m)))
        self.batch_worker.finished.connect(self._on_batch_finished)
        self.batch_worker.error.connect(self._on_batch_error)
        self.batch_worker.start()

    def _on_batch_finished(self, df_out):
        self.batch_result_df = df_out
        self.btn_run_batch.setEnabled(True)
        self.btn_export_batch.setEnabled(True)
        self.prog_batch.setValue(100)
        self.status_bar.showMessage(f"批量解算完成！共处理 {len(df_out)} 行记录。")

        # 预览表格展示前 50 行
        self.table_batch.setRowCount(0)
        self.table_batch.setColumnCount(len(df_out.columns))
        self.table_batch.setHorizontalHeaderLabels(list(df_out.columns))

        preview_rows = min(50, len(df_out))
        self.table_batch.setRowCount(preview_rows)
        for r in range(preview_rows):
            for c, col in enumerate(df_out.columns):
                val = str(df_out.iloc[r][col])
                self.table_batch.setItem(r, c, QTableWidgetItem(val))

    def _on_batch_error(self, err_msg):
        self.btn_run_batch.setEnabled(True)
        self.prog_batch.setValue(0)
        QMessageBox.critical(self, "批量解算错误", f"批量计算失败:\n{err_msg}")

    def _export_batch_result(self):
        if self.batch_result_df is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出批量解算结果", "batch_tide_result.csv", "CSV Files (*.csv);;Excel Files (*.xlsx)")
        if path:
            export_dataframe(self.batch_result_df, path)
            QMessageBox.information(self, "导出成功", f"批量结果已导出至:\n{path}")

    # ================= 空间栅格解算逻辑 (Raster Engine) =================
    def _on_raster_input_changed(self, text):
        path = text.strip()
        if os.path.exists(path) and os.path.isfile(path):
            self._propose_raster_output(path)
            self._inspect_raster_ui(path)

    def _propose_raster_output(self, input_path):
        base, ext = os.path.splitext(input_path)
        mode = self.combo_raster_mode.currentData()
        if mode == 'snapshot':
            self.edit_raster_output.setText(f"{base}_tide_snapshot{ext}")
        elif mode == 'exposure':
            exp_dir = f"{base}_CoastTideX_exposure"
            self.edit_raster_output.setText(exp_dir)
        else:
            year = self.spin_inund_year.value() if self.combo_inund_time_mode.currentData() == 'year' else 'period'
            self.edit_raster_output.setText(f"{base}_inundation_{year}{ext}")
            self.edit_inund_qc.setText(f"{base}_inundation_{year}_qc{ext}")

    def _browse_raster_input(self):
        f, _ = QFileDialog.getOpenFileName(self, "选择输入 GeoTIFF 影像", "", "GeoTIFF (*.tif *.tiff *.geotiff);;All Files (*.*)")
        if f:
            self.edit_raster_input.setText(f)

    def _browse_raster_output(self):
        mode = self.combo_raster_mode.currentData()
        if mode == 'exposure':
            d = QFileDialog.getExistingDirectory(self, "指定 Exposure 7 项产物输出目录", self.edit_raster_output.text().strip() or "")
            if d:
                self.edit_raster_output.setText(d)
        else:
            f, _ = QFileDialog.getSaveFileName(self, "指定输出 GeoTIFF 路径", self.edit_raster_output.text().strip() or "output.tif", "GeoTIFF (*.tif *.tiff)")
            if f:
                self.edit_raster_output.setText(f)

    def _browse_inund_qc(self):
        f, _ = QFileDialog.getSaveFileName(self, "指定 QC 质量掩膜路径", self.edit_inund_qc.text().strip() or "output_qc.tif", "GeoTIFF (*.tif *.tiff)")
        if f:
            self.edit_inund_qc.setText(f)

    def _inspect_raster_ui(self, target_path=None):
        path = target_path if isinstance(target_path, str) else self.edit_raster_input.text().strip()
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, "文件不存在", "请先选择有效的输入 GeoTIFF 文件！")
            return
        try:
            engine = RasterTideEngine()
            info = engine.inspect_raster(path, compute_valid_count=False)
            self.current_raster_info = info
            self.lbl_raster_dims.setText(f"{info.width} × {info.height} (总计 {info.total_pixel_count:,} 像元)")
            self.lbl_raster_crs.setText(info.formatted_crs)
            self.lbl_raster_crs.setToolTip(f"完整坐标参考系统定义 (CRS):\n{info.crs}")
            self.lbl_raster_res.setText(info.formatted_resolution)
            self.lbl_raster_res.setToolTip(
                f"原始分辨率数值: ({info.resolution[0]}, {info.resolution[1]})\n"
                f"坐标系类型: {'投影坐标系 (Projected, 单位: 米)' if info.is_projected else '地理坐标系 (Geographic, 单位: 度)'}"
            )
            nodata_str = f"{info.nodata}" if info.nodata is not None else "未定义 (None)"
            self.lbl_raster_nodata.setText(nodata_str)
            b = info.bounds
            self.lbl_raster_bounds.setText(f"[{b[0]:.4f}, {b[1]:.4f}] -> [{b[2]:.4f}, {b[3]:.4f}]")

            # 检测输入 DEM 是否具有 MSL 基准标签 (v1.7 智能联动)
            try:
                import rasterio
                with rasterio.open(path) as src_tags:
                    chk_tags = src_tags.tags()
                    datum_tag = str(chk_tags.get('DATUM', '')).upper()
                    target_tag = str(chk_tags.get('TARGET_VERTICAL_DATUM', '')).upper()
                    ref_tag = str(chk_tags.get('ANALYSIS_REFERENCE', '')).upper()
                    fn_upper = os.path.basename(path).upper()
                    if datum_tag == 'MSL' or target_tag == 'MSL' or ref_tag == 'MSL' or '_MSL' in fn_upper:
                        idx_msl = self.combo_inund_datum.findData('msl')
                        if idx_msl >= 0 and self.combo_inund_datum.currentIndex() != idx_msl:
                            self.combo_inund_datum.setCurrentIndex(idx_msl)
                        self.status_bar.showMessage(f"已检测到 MSL 基准 DEM ({os.path.basename(path)})，已自动切换为 MSL 模式")
            except Exception:
                pass

            self.status_bar.showMessage(f"已加载栅格元数据: {os.path.basename(path)}")
        except Exception as e:
            QMessageBox.critical(self, "检查失败", f"无法解析 GeoTIFF 元数据:\n{e}")

    def _on_raster_mode_changed(self):
        mode = self.combo_raster_mode.currentData()
        is_snap = (mode == 'snapshot')
        is_exp = (mode == 'exposure')
        self.container_snapshot.setVisible(is_snap)
        self.container_inund.setVisible(not is_snap)
        self.grp_grid.setVisible(not is_snap)

        # 隐藏/显示 Inundation 独有的 QC 独立文件框
        self.lbl_inund_qc.setVisible(not is_snap and not is_exp)
        self.edit_inund_qc.setVisible(not is_snap and not is_exp)
        self.btn_browse_qc.setVisible(not is_snap and not is_exp)

        # 动态更新输出目标标签与占位提示
        if is_exp:
            self.lbl_raster_output.setText("输出产品目录 (Output Directory):")
            self.edit_raster_output.setPlaceholderText("指定 Exposure 7 项产物输出目录 (如 <DEM_DIR>/<DEM_STEM>_CoastTideX_exposure)...")
        elif is_snap:
            self.lbl_raster_output.setText("输出 GeoTIFF 文件:")
            self.edit_raster_output.setPlaceholderText("输出单时刻潮位 GeoTIFF 路径...")
        else:
            self.lbl_raster_output.setText("输出淹没频率 GeoTIFF:")
            self.edit_raster_output.setPlaceholderText("输出潜在天文潮淹没频率 GeoTIFF 路径 (*_inundation_2024.tif)...")

        inp = self.edit_raster_input.text().strip()
        if inp:
            self._propose_raster_output(inp)

    def _on_inund_time_mode_changed(self):
        mode = self.combo_inund_time_mode.currentData()
        is_year = (mode == 'year')
        self.lbl_inund_year.setVisible(is_year)
        self.spin_inund_year.setVisible(is_year)
        self.lbl_inund_start.setVisible(not is_year)
        self.time_inund_start.setVisible(not is_year)
        self.lbl_inund_end.setVisible(not is_year)
        self.time_inund_end.setVisible(not is_year)
        inp = self.edit_raster_input.text().strip()
        if inp:
            self._propose_raster_output(inp)

    def _on_inund_datum_changed(self):
        val = self.combo_inund_datum.currentData()
        if val == 'msl':
            self.lbl_inund_datum_hint.setText("✅ MSL 推荐模式：高程基准严密对齐，水深与淹没直接对比 DEM_MSL (Seeger & Minderhoud, Nature, 2026 范式)。")
            self.lbl_inund_datum_hint.setStyleSheet("color: #4ade80; font-size: 11px;")
        elif val == 'egm2008':
            self.lbl_inund_datum_hint.setText("⚠️ EGM2008 为兼容模式。v1.7 推荐先使用 [DEM 基准转换] 标签页将 DEM 转换至 MSL 基准，以消除沿岸潮位-高程系统偏差。")
            self.lbl_inund_datum_hint.setStyleSheet("color: #fbbf24; font-size: 11px;")
        else:
            self.lbl_inund_datum_hint.setText("")

    def _run_raster_simulation(self):
        inp_path = self.edit_raster_input.text().strip()
        out_path = self.edit_raster_output.text().strip()
        if not inp_path or not os.path.exists(inp_path):
            QMessageBox.warning(self, "输入错误", "请输入并确认有效的 GeoTIFF 栅格路径！")
            return
        if not out_path:
            QMessageBox.warning(self, "输入错误", "请指定输出 GeoTIFF 文件或产品目录路径！")
            return

        mode = self.combo_raster_mode.currentData()
        strict = self.chk_raster_strict.isChecked()

        if mode == 'snapshot':
            snap_time = self.time_raster_snap.dateTime().toString("yyyy-MM-dd HH:mm:ss")
            params = {
                'input_path': inp_path,
                'output_path': out_path,
                'timestamp': snap_time,
                'datum_target': self.combo_snap_datum.currentData(),
                'constituents': self.combo_snap_const.currentData(),
                'source_tz': self.combo_snap_tz.currentData(),
                'block_size': self.spin_grid_block.value(),
                'strict': strict
            }
        elif mode == 'exposure':
            # 检查输出目录下是否已存在 7 项 Exposure 产物，避免静默覆盖
            from core.exposure_engine import ExposureProductPaths
            stem = Path(inp_path).stem
            exp_paths = ExposureProductPaths.from_directory(out_path, stem)
            existing_conflicts = [p for p in exp_paths.all_paths if os.path.exists(p)]
            if existing_conflicts:
                res = QMessageBox.question(
                    self,
                    "产物已存在确认",
                    f"检测到输出目录下已存在 {len(existing_conflicts)} 个露出分析产物：\n" +
                    "\n".join([os.path.basename(p) for p in existing_conflicts[:5]]) +
                    ("\n..." if len(existing_conflicts) > 5 else "") +
                    "\n\n是否确认覆盖已有产物？若取消将中止本次解算。",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No
                )
                if res != QMessageBox.StandardButton.Yes:
                    return

            time_mode = self.combo_inund_time_mode.currentData()
            target_mode = self.combo_inund_target_mode.currentData() if hasattr(self, 'combo_inund_target_mode') else 'intertidal'
            params = {
                'input_path': inp_path,
                'output_dir': out_path,
                'freq': self.combo_inund_freq.currentData(),
                'dem_datum': self.combo_inund_datum.currentData(),
                'constituents': 'all',
                'source_tz': 'UTC',
                'target_mode': target_mode,
                'initial_control_spacing_m': self.spin_grid_init.value(),
                'min_control_spacing_m': self.spin_grid_min.value(),
                'inundation_error_tolerance_pct': self.spin_grid_tol.value(),
                'block_size': self.spin_grid_block.value(),
                'strict': strict,
                'allow_overwrite': True
            }
            if time_mode == 'year':
                params['year'] = self.spin_inund_year.value()
                params['start_time'] = None
                params['end_time'] = None
            else:
                params['year'] = None
                params['start_time'] = self.time_inund_start.dateTime().toString("yyyy-MM-dd HH:mm:ss")
                params['end_time'] = self.time_inund_end.dateTime().toString("yyyy-MM-dd HH:mm:ss")
                if self.time_inund_start.dateTime() >= self.time_inund_end.dateTime():
                    QMessageBox.warning(self, "时间错误", "起始时间必须早于结束时间！")
                    return
        else:
            time_mode = self.combo_inund_time_mode.currentData()
            qc_out = self.edit_inund_qc.text().strip() or None
            target_mode = self.combo_inund_target_mode.currentData() if hasattr(self, 'combo_inund_target_mode') else 'intertidal'
            params = {
                'input_path': inp_path,
                'output_path': out_path,
                'qc_output_path': qc_out,
                'freq': self.combo_inund_freq.currentData(),
                'dem_datum': self.combo_inund_datum.currentData(),
                'constituents': 'all',
                'source_tz': 'UTC',
                'target_mode': target_mode,
                'initial_control_spacing_m': self.spin_grid_init.value(),
                'min_control_spacing_m': self.spin_grid_min.value(),
                'inundation_error_tolerance_pct': self.spin_grid_tol.value(),
                'block_size': self.spin_grid_block.value(),
                'strict': strict
            }
            if time_mode == 'year':
                params['year'] = self.spin_inund_year.value()
                params['start_time'] = None
                params['end_time'] = None
            else:
                params['year'] = None
                params['start_time'] = self.time_inund_start.dateTime().toString("yyyy-MM-dd HH:mm:ss")
                params['end_time'] = self.time_inund_end.dateTime().toString("yyyy-MM-dd HH:mm:ss")
                if self.time_inund_start.dateTime() >= self.time_inund_end.dateTime():
                    QMessageBox.warning(self, "时间错误", "起始时间必须早于结束时间！")
                    return

        self.btn_run_raster.setEnabled(False)
        self.btn_cancel_raster.setEnabled(True)
        self.prog_raster.setValue(5)
        self.lbl_raster_status.setText("正在初始化空间解算引擎...")

        self.raster_worker = RasterTideWorker(mode=mode, params=params)
        self.raster_worker.progress.connect(self._on_raster_progress)
        self.raster_worker.finished.connect(self._on_raster_finished)
        self.raster_worker.cancelled.connect(self._on_raster_cancelled)
        self.raster_worker.error.connect(self._on_raster_error)
        self.raster_worker.start()

    def _cancel_raster_simulation(self):
        if self.raster_worker and self.raster_worker.isRunning():
            self.raster_worker.cancel()
            self.lbl_raster_status.setText("正在取消任务并清理临时文件...")
            self.btn_cancel_raster.setEnabled(False)

    def _on_raster_cancelled(self):
        self.btn_run_raster.setEnabled(True)
        self.btn_cancel_raster.setEnabled(False)
        self.prog_raster.setValue(0)
        self.lbl_raster_status.setText("用户已取消空间栅格解算任务并已清理临时文件。")
        self.status_bar.showMessage("已取消空间栅格解算任务")
        QMessageBox.information(self, "任务已取消", "空间栅格解算任务已被成功取消，临时中间文件已安全清理。")

    def _on_raster_progress(self, pct, msg):
        self.prog_raster.setValue(pct)
        self.lbl_raster_status.setText(msg)
        self.status_bar.showMessage(msg)

    def _on_raster_finished(self, summary):
        try:
            self.btn_run_raster.setEnabled(True)
            self.btn_cancel_raster.setEnabled(False)
            self.prog_raster.setValue(100)

            is_dict = isinstance(summary, dict)
            mode = summary.get('mode', self.combo_raster_mode.currentData()) if is_dict else getattr(summary, 'mode', 'snapshot')
            elapsed = summary.get('elapsed_seconds', 0.0) if is_dict else getattr(summary, 'elapsed_seconds', 0.0)
            self.lbl_raster_status.setText(f"解算圆满完成！耗时 {elapsed:.2f} 秒。")
            self.status_bar.showMessage("空间栅格解算圆满完成！")

            if mode == 'exposure':
                prods = summary.get('products')
                out_dir = summary.get('output_dir', '')
                if not out_dir and prods:
                    out_dir = os.path.dirname(os.path.abspath(prods.exposure_fraction_path))
                cache_p = summary.get('export_tide_cache_path')
                cache_line = f"<li><b>Tide Cache 缓存</b>: <code>{cache_p}</code></li>" if cache_p else ""

                prod_items = ""
                if prods:
                    prod_items = (
                        f"<li><b>暴露比例 (Fraction)</b>: <code>{os.path.basename(prods.exposure_fraction_path)}</code></li>"
                        f"<li><b>累积时长 (Duration)</b>: <code>{os.path.basename(prods.exposure_duration_h_path)}</code></li>"
                        f"<li><b>最大单次时长 (Max Cont)</b>: <code>{os.path.basename(prods.exposure_max_continuous_h_path)}</code></li>"
                        f"<li><b>平均事件时长 (Mean Event)</b>: <code>{os.path.basename(prods.exposure_mean_event_h_path)}</code></li>"
                        f"<li><b>事件发生次数 (Event Count)</b>: <code>{os.path.basename(prods.exposure_event_count_path)}</code></li>"
                        f"<li><b>有效时间比例 (Valid Time Frac)</b>: <code>{os.path.basename(prods.exposure_valid_time_fraction_path)}</code></li>"
                        f"<li><b>质量控制掩膜 (QC Mask)</b>: <code>{os.path.basename(prods.exposure_qc_path)}</code></li>"
                    )

                info_box = QMessageBox(self)
                info_box.setWindowTitle("露出时间域解算完成")
                info_box.setIcon(QMessageBox.Icon.Information)
                info_box.setText("<h3>🎉 潜在天文潮露出时间域 7 项空间栅格解算成功！</h3>")
                info_box.setInformativeText(
                    f"<p><b>任务模式</b>: 潜在天文潮露出时间域分析 (7 项科学产物)</p>"
                    f"<p><b>产物保存目录</b>: <code>{out_dir}</code></p>"
                    f"<ul>"
                    f"{prod_items}"
                    f"{cache_line}"
                    f"<li><b>解算总耗时</b>: {elapsed:.2f} 秒</li>"
                    f"</ul>"
                )
                btn_open_dir = info_box.addButton("打开输出目录", QMessageBox.ButtonRole.ActionRole)
                info_box.addButton(QMessageBox.StandardButton.Ok)
                info_box.exec()
                if info_box.clickedButton() == btn_open_dir:
                    self._open_directory(out_dir)
            else:
                mode_name = "单时刻空间潮位" if summary.mode == 'snapshot' else "潜在天文潮淹没频率"
                qc_line = f"<li><b>质量控制掩膜</b>: <code>{summary.qc_output_path}</code></li>" if summary.qc_output_path else ""
                nodes_cnt = summary.control_nodes_count
                if nodes_cnt is not None and nodes_cnt > 0:
                    nodes_line = f"<li><b>控制节点总数</b>: {nodes_cnt:,} 个</li>"
                else:
                    nodes_line = ""

                info_box = QMessageBox(self)
                info_box.setWindowTitle("解算完成")
                info_box.setIcon(QMessageBox.Icon.Information)
                info_box.setText(f"<h3>🎉 空间栅格解算成功！</h3>")
                info_box.setInformativeText(
                    f"<p><b>任务模式</b>: {mode_name}</p>"
                    f"<ul>"
                    f"<li><b>影像规格</b>: {summary.width} × {summary.height} ({summary.total_pixels:,} 像元)</li>"
                    f"<li><b>有效解算像元</b>: {summary.valid_pixels:,}</li>"
                    f"{nodes_line}"
                    f"<li><b>解算总耗时</b>: {summary.elapsed_seconds:.2f} 秒</li>"
                    f"<li><b>输出文件路径</b>: <code>{summary.output_path}</code></li>"
                    f"{qc_line}"
                    f"</ul>"
                )
                btn_open_dir = info_box.addButton("打开输出目录", QMessageBox.ButtonRole.ActionRole)
                info_box.addButton(QMessageBox.StandardButton.Ok)
                info_box.exec()

                if info_box.clickedButton() == btn_open_dir:
                    out_p = getattr(summary, 'output_path', '')
                    self._open_directory(out_p)
        except Exception as e:
            import traceback
            traceback.print_exc()
            if isinstance(summary, dict):
                out_p = summary.get('output_path') or summary.get('output_dir') or ''
            else:
                out_p = getattr(summary, 'output_path', getattr(summary, 'output_dir', ''))
            QMessageBox.warning(self, "显示完成信息异常", f"解算已完成并保存至:\n{out_p}\n\n但弹窗提示异常: {e}")

    def _on_raster_error(self, err_msg):
        self.btn_run_raster.setEnabled(True)
        self.btn_cancel_raster.setEnabled(False)
        self.prog_raster.setValue(0)
        if "RasterCalculationCancelled" in err_msg or "取消" in err_msg:
            self._on_raster_cancelled()
            return
        self.lbl_raster_status.setText("解算失败")
        self.status_bar.showMessage("栅格解算发生错误")
        QMessageBox.critical(self, "解算错误", f"空间栅格解算失败:\n{err_msg}")

    def _open_directory(self, path: str):
        """跨平台打开本地文件或目录（兼容 Windows、macOS 与 Linux）"""
        if not path or not os.path.exists(path):
            QMessageBox.information(self, "提示", "指定的输出目录或文件尚未生成或不存在。")
            return
        abs_p = os.path.abspath(path)
        if not os.path.isdir(abs_p):
            abs_p = os.path.dirname(abs_p)
        if not os.path.exists(abs_p):
            QMessageBox.information(self, "提示", "指定的输出目录尚未生成或不存在。")
            return
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(abs_p))

    def _open_settings(self):
        dialog = SettingsDialog(self)
        dialog.exec()

    def _show_manual(self):
        dialog = ManualDialog(self)
        dialog.exec()

    def _show_about(self):
        about_text = (
            "<h3>CoastTideX v1.7</h3>"
            "<p><b>全球海岸带空间栅格潮位模拟与高程基准转换系统 (MSL Reference Workflow)</b></p>"
            "<p>致力于为海洋工程、海岸带遥感、大地测量与潮滩生态演变建模提供高保真度的空间潮汐预测与严密基准转换工具。</p>"
            "<ul>"
            "<li><b>v1.7 MSL 统一基准架构</b>: "
            "<ul>"
            "<li>前置陆地 DEM 垂直基准转换 (EGM2008 &rarr; MSL)，公式: <code>Z_MSL = Z_EGM2008 - MDT - ΔN</code>；</li>"
            "<li>借鉴 Seeger & Minderhoud (Nature, 2026) 理论范式，大洋区双线性插值，沿岸 100 km 球面 3D-IDW 保守外推；</li>"
            "<li>FES 原生 MSL 潮位与 DEM_MSL 直接比较，消除潮位逐时空计算中的基准转换开销并保障物理边界严密一致；</li>"
            "<li>FES ParentBBox 模型空间复用优化，显著降低大范围分块加载延迟。</li>"
            "</ul></li>"
            "<li><b>潮汐动力学</b>: FES2022b 原生非结构有限元三角形网格 (LGP2, 34分潮)</li>"
            "<li><b>四大多元基准体系</b>: "
            "<ul>"
            "<li>MSL (相对平均海平面)</li>"
            "<li>MDT 原始大地水准面基准 (全球大洋 GOCO06s / 地中海与黑海 EIGEN-6C4)</li>"
            "<li>EGM2008 (经 ΔN 改正的严密海拔正高)</li>"
            "<li>WGS84 (GNSS 空间几何三维椭球高)</li>"
            "</ul></li>"
            "<li><b>平均动态地形</b>: CNES-CLS22 MDT (全球大洋与边缘海混合产品，可选配置 Hybrid MDT 来源分类栅格；未配置时使用几何多边形备用并标记质量预警)</li>"
            "<li><b>高精度水准面栅格</b>: NGA EGM2008 2.5' 全球全分辨率网格</li>"
            "<li><b>潜在天文潮露出时间域分析引擎</b>: "
            "<ul>"
            "<li>固定代表性地形条件下的潜在天文潮露出时长 (Exposure Duration)、最长连续露出、平均事件时长、发生频次与有效时间覆盖率等 7 大独立 GeoTIFF 空间栅格产物；</li>"
            "<li>高精度时间跨界线性插值 (Linear Crossing Interpolation) 与空间双线性流式累加；</li>"
            "<li>全系统严格遵循半开区间 [start, end) 时间采样语义，彻底消除末端双重统计。</li>"
            "</ul></li>"
            "<li><b>批量潮间带栅格引擎与 Tide Cache (Schema 1.2)</b>: "
            "<ul>"
            "<li>文件夹级自动化发现与轻量扫描，单瓦片顺序推进 (max_parallel_tiles = 1)；</li>"
            "<li>严格二阶段解耦架构：Stage 1 生成持久化 NetCDF Tide Cache，Stage 2 零 FES 快速反演淹没频率与潜在露出时长；</li>"
            "<li>全要素规范兼容性签名 (SHA-256)、单瓦片失败隔离与防篡改断点恢复。</li>"
            "</ul></li>"
            "</ul>"
            "<p>作者 / 开发者：<b>王宇浩</b> (Yuhao Wang) | 核心引擎：CNES/AVISO pyfes, rasterio, pyproj & scipy</p>"
        )
        QMessageBox.about(self, "关于 CoastTideX", about_text)


    def _setup_batch_raster_tab(self):
        """初始化 v1.5 批量潮间带栅格解算与 Tide Cache 选项卡"""
        layout = QHBoxLayout(self.tab_batch_raster)
        layout.setSpacing(10)

        # 左侧控制面板 (包装在 QScrollArea 内)
        scroll_left = QScrollArea()
        scroll_left.setWidgetResizable(True)
        scroll_left.setFrameShape(QFrame.Shape.NoFrame)
        scroll_left.setMinimumWidth(390)
        scroll_left.setMaximumWidth(450)

        panel_widget = QWidget()
        panel_layout = QVBoxLayout(panel_widget)
        panel_layout.setSpacing(10)
        panel_layout.setContentsMargins(5, 5, 5, 5)

        # 1. 文件夹输入
        grp_input = QGroupBox("📂 批量输入与输出目录 / Directories")
        vbox_input = QVBoxLayout(grp_input)
        
        vbox_input.addWidget(QLabel("输入 GeoTIFF 文件夹路径:"))
        h_in = QHBoxLayout()
        self.txt_batch_in_dir = QLineEdit()
        self.txt_batch_in_dir.setPlaceholderText("选择包含沙滩/潮滩 DEM 的文件夹...")
        self.btn_browse_batch_in = QPushButton("浏览...")
        self.btn_browse_batch_in.clicked.connect(self._on_browse_batch_input)
        h_in.addWidget(self.txt_batch_in_dir)
        h_in.addWidget(self.btn_browse_batch_in)
        vbox_input.addLayout(h_in)

        self.chk_batch_recursive = QCheckBox("递归扫描子目录 (Recursive)")
        vbox_input.addWidget(self.chk_batch_recursive)

        self.btn_scan_batch = QPushButton("🔍 扫描文件夹 (Scan GeoTIFFs)")
        self.btn_scan_batch.setObjectName("btn_batch_scan")
        self.btn_scan_batch.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_scan_batch.setToolTip("扫描并预览当前目录中将参与批量解算的 GeoTIFF；不会开始潮位计算，只读取文件路径与 GeoTIFF 头信息。")
        self.btn_scan_batch.clicked.connect(self._on_scan_batch_rasters)
        vbox_input.addWidget(self.btn_scan_batch)

        vbox_input.addWidget(QLabel("输出文件夹路径 (默认: <input>/CoastTideX_output):"))
        h_out = QHBoxLayout()
        self.txt_batch_out_dir = QLineEdit()
        self.txt_batch_out_dir.setPlaceholderText("留空自动在输入目录下创建 CoastTideX_output...")
        self.btn_browse_batch_out = QPushButton("更改...")
        self.btn_browse_batch_out.clicked.connect(self._on_browse_batch_output)
        h_out.addWidget(self.txt_batch_out_dir)
        h_out.addWidget(self.btn_browse_batch_out)
        vbox_input.addLayout(h_out)

        self.txt_batch_in_dir.textChanged.connect(self._invalidate_batch_scan)
        self.txt_batch_out_dir.textChanged.connect(self._invalidate_batch_scan)
        self.chk_batch_recursive.toggled.connect(self._invalidate_batch_scan)

        panel_layout.addWidget(grp_input)

        # 2. 预测时间与时间步长
        self.grp_batch_time = QGroupBox("⏱️ 预测时段与时间步长 / Temporal Scope")
        vbox_time = QVBoxLayout(self.grp_batch_time)

        h_tm = QHBoxLayout()
        h_tm.addWidget(QLabel("时段模式:"))
        self.combo_batch_time_mode = QComboBox()
        self.combo_batch_time_mode.addItem("整年快捷模式 (Year Mode)", "year")
        self.combo_batch_time_mode.addItem("自定义时段 (Custom Period)", "period")
        self.combo_batch_time_mode.currentIndexChanged.connect(self._on_batch_time_mode_changed)
        h_tm.addWidget(self.combo_batch_time_mode)
        vbox_time.addLayout(h_tm)

        self.wgt_batch_year = QWidget()
        h_yr = QHBoxLayout(self.wgt_batch_year)
        h_yr.setContentsMargins(0, 0, 0, 0)
        h_yr.addWidget(QLabel("整年预测年份:"))
        self.spn_batch_year = QSpinBox()
        self.spn_batch_year.setRange(1950, 2099)
        self.spn_batch_year.setValue(2024)
        self.spn_batch_year.valueChanged.connect(self._update_batch_expected_samples)
        h_yr.addWidget(self.spn_batch_year)
        vbox_time.addWidget(self.wgt_batch_year)

        self.wgt_batch_period = QWidget()
        vbox_period = QVBoxLayout(self.wgt_batch_period)
        vbox_period.setContentsMargins(0, 0, 0, 0)
        h_st = QHBoxLayout()
        h_st.addWidget(QLabel("起始时间 (UTC):"))
        self.time_batch_start = QDateTimeEdit(QDateTime.currentDateTimeUtc())
        self.time_batch_start.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.time_batch_start.setCalendarPopup(True)
        self.time_batch_start.dateTimeChanged.connect(self._update_batch_expected_samples)
        h_st.addWidget(self.time_batch_start)
        vbox_period.addLayout(h_st)

        h_et = QHBoxLayout()
        h_et.addWidget(QLabel("结束时间 (UTC):"))
        self.time_batch_end = QDateTimeEdit(QDateTime.currentDateTimeUtc().addDays(30))
        self.time_batch_end.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.time_batch_end.setCalendarPopup(True)
        self.time_batch_end.dateTimeChanged.connect(self._update_batch_expected_samples)
        h_et.addWidget(self.time_batch_end)
        vbox_period.addLayout(h_et)

        self.wgt_batch_period.setVisible(False)
        vbox_time.addWidget(self.wgt_batch_period)

        h_step = QHBoxLayout()
        h_step.addWidget(QLabel("采样间隔 (步长):"))
        self.cmb_batch_step = QComboBox()
        self.cmb_batch_step.addItems(["30min (推荐)", "1h", "15min", "10min", "2h", "Custom... (自定义)"])
        self.cmb_batch_step.currentIndexChanged.connect(self._on_batch_step_changed)
        h_step.addWidget(self.cmb_batch_step)
        vbox_time.addLayout(h_step)

        self.lbl_batch_samples = QLabel("预期采样步数: 17,568 步 (2024 全年 30min)")
        self.lbl_batch_samples.setStyleSheet("color: #4CAF50; font-weight: bold;")
        self.lbl_batch_samples.setWordWrap(True)
        vbox_time.addWidget(self.lbl_batch_samples)

        panel_layout.addWidget(self.grp_batch_time)

        # 3. 科学参数与目标感知
        self.grp_batch_sci = QGroupBox("⚙️ 科学参数与目标模式 / Scientific Options")
        vbox_sci = QVBoxLayout(self.grp_batch_sci)

        h_datum = QHBoxLayout()
        h_datum.addWidget(QLabel("DEM 高程基准:"))
        self.cmb_batch_datum = QComboBox()
        self.cmb_batch_datum.addItems(["MSL (推荐 - v1.7 统一基准)", "EGM2008 (兼容模式)", "GOCO06s (全球大洋)", "WGS84 椭球高"])
        h_datum.addWidget(self.cmb_batch_datum)
        vbox_sci.addLayout(h_datum)

        h_const = QHBoxLayout()
        h_const.addWidget(QLabel("天文分潮集合:"))
        self.cmb_batch_const = QComboBox()
        self.cmb_batch_const.addItems(["all (全套 34 分潮)", "major8 (8大主分潮)"])
        h_const.addWidget(self.cmb_batch_const)
        vbox_sci.addLayout(h_const)

        h_target = QHBoxLayout()
        h_target.addWidget(QLabel("目标区域模式:"))
        self.cmb_batch_target_mode = QComboBox()
        self.cmb_batch_target_mode.addItems(["intertidal (沙滩/潮间带目标感知, 默认)", "standard (标准全网格自适应)"])
        h_target.addWidget(self.cmb_batch_target_mode)
        vbox_sci.addLayout(h_target)

        # 沿岸外推 Fallback (根据审查结果禁用)
        self.chk_batch_fallback = QCheckBox("允许官方沿岸外推 FES 回退 (Coastal Fallback)")
        self.chk_batch_fallback.setChecked(False)
        self.chk_batch_fallback.setEnabled(False)
        self.chk_batch_fallback.setToolTip("【只读审查结论】本地 ocean_tide_extrapolated 均为 .nc.xz 压缩包且掩膜为规则网格，Phase 1 维持原生 LGP2 高阶非结构有限元网格，回退机制暂未激活。")
        vbox_sci.addWidget(self.chk_batch_fallback)

        panel_layout.addWidget(self.grp_batch_sci)

        # 4. 任务模式与调度
        grp_job = QGroupBox("📋 运行模式与输出策略 / Job Mode & Policy")
        vbox_job = QVBoxLayout(grp_job)

        h_jm = QHBoxLayout()
        h_jm.addWidget(QLabel("解算流程:"))
        self.cmb_batch_job_mode = QComboBox()
        self.cmb_batch_job_mode.addItem("1. 完整流程: Tide Cache + 潜在淹没频率 (默认)", "tide-inundation")
        self.cmb_batch_job_mode.addItem("2. 仅解算控制节点潮位 (生成 *_tide.nc)", "tide")
        self.cmb_batch_job_mode.addItem("3. 基于已有 Tide Cache 解算淹没频率 (零 FES 开销)", "inundation-from-cache")
        self.cmb_batch_job_mode.addItem("4. 完整流程: Tide Cache + 潜在露出分析 (tide-exposure)", "tide-exposure")
        self.cmb_batch_job_mode.addItem("5. 基于已有 Tide Cache 解算潜在露出 (零 FES 开销)", "exposure-from-cache")
        self.cmb_batch_job_mode.addItem("6. 全要素产物包 (Tide Cache + 淹没频率 + 潜在露出)", "all")
        self.cmb_batch_job_mode.currentIndexChanged.connect(self._on_batch_job_mode_changed)
        h_jm.addWidget(self.cmb_batch_job_mode)
        vbox_job.addLayout(h_jm)

        # 模式专属提示横幅
        self.lbl_batch_job_mode_tip = QLabel("💡 Mode 1 完整两阶段：先生成并保存 Tide Cache (*_tide.nc)，再基于缓存解算淹没频率 GeoTIFF。")
        self.lbl_batch_job_mode_tip.setWordWrap(True)
        self.lbl_batch_job_mode_tip.setStyleSheet("color: #00796B; font-weight: bold; background-color: #E0F2F1; padding: 6px; border-radius: 4px;")
        vbox_job.addWidget(self.lbl_batch_job_mode_tip)

        # 统一 ExistingOutputPolicy 策略选择下拉框 (取代两个相互冲突的 CheckBox)
        h_policy = QHBoxLayout()
        h_policy.addWidget(QLabel("已有产物策略:"))
        self.cmb_batch_existing_policy = QComboBox()
        self.cmb_batch_existing_policy.addItem("断点恢复 (Resume, 跳过已有完整产物) [默认]", "resume")
        self.cmb_batch_existing_policy.addItem("冲突报错 (Error if exists, 拒绝覆写)", "error_if_exists")
        self.cmb_batch_existing_policy.addItem("强制覆盖 (Overwrite, 重新计算并替换)", "overwrite")
        h_policy.addWidget(self.cmb_batch_existing_policy)
        vbox_job.addLayout(h_policy)

        panel_layout.addWidget(grp_job)

        # 5. 执行控制与进度
        grp_exec = QGroupBox("🚀 批处理调度控制 / Execution Control")
        vbox_exec = QVBoxLayout(grp_exec)

        h_btns = QHBoxLayout()
        self.btn_start_batch = QPushButton("🚀 开始批量解算")
        self.btn_start_batch.setObjectName("btn_batch_start")
        self.btn_start_batch.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_start_batch.setEnabled(False)  # 严格依赖 Fresh Scan 启用
        self.btn_start_batch.clicked.connect(self._on_start_batch)

        self.btn_cancel_batch = QPushButton("⏹️ 取消")
        self.btn_cancel_batch.setObjectName("btn_batch_cancel")
        self.btn_cancel_batch.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel_batch.setEnabled(False)
        self.btn_cancel_batch.clicked.connect(self._on_cancel_batch)

        h_btns.addWidget(self.btn_start_batch)
        h_btns.addWidget(self.btn_cancel_batch)
        vbox_exec.addLayout(h_btns)

        vbox_exec.addWidget(QLabel("总览进度 (Overall Progress):"))
        self.bar_batch_overall = QProgressBar()
        self.bar_batch_overall.setValue(0)
        vbox_exec.addWidget(self.bar_batch_overall)

        vbox_exec.addWidget(QLabel("当前瓦片进度 (Current Tile):"))
        self.bar_batch_tile = QProgressBar()
        self.bar_batch_tile.setValue(0)
        vbox_exec.addWidget(self.bar_batch_tile)

        self.lbl_batch_status = QLabel("就绪: 请选择输入目录并点击扫描")
        self.lbl_batch_status.setWordWrap(True)
        vbox_exec.addWidget(self.lbl_batch_status)

        self.lbl_batch_counts = QLabel("总文件: 0 | 完成: 0 | 失败: 0 | 跳过: 0")
        self.lbl_batch_counts.setStyleSheet("font-weight: bold;")
        vbox_exec.addWidget(self.lbl_batch_counts)

        panel_layout.addWidget(grp_exec)
        panel_layout.addStretch()

        scroll_left.setWidget(panel_widget)
        layout.addWidget(scroll_left)

        # 右侧：影像文件表格视图
        grp_right = QGroupBox("📋 影像文件清单与实时解算状态 / Raster Tiles Queue")
        vbox_right = QVBoxLayout(grp_right)

        self.table_batch_rasters = QTableWidget()
        self.table_batch_rasters.setColumnCount(9)
        self.table_batch_rasters.setHorizontalHeaderLabels([
            "相对路径 / Relative Path", "大小", "栅格尺寸", "坐标系", "分辨率", "当前状态", "Tide Cache", "淹没频率输出", "潜在露出产物 / Exposure"
        ])
        self.table_batch_rasters.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table_batch_rasters.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.table_batch_rasters.horizontalHeader().setStretchLastSection(True)
        vbox_right.addWidget(self.table_batch_rasters)

        h_bot_right = QHBoxLayout()
        self.btn_open_batch_out = QPushButton("📂 打开输出文件夹")
        self.btn_open_batch_out.clicked.connect(self._on_open_batch_output_folder)
        h_bot_right.addStretch()
        h_bot_right.addWidget(self.btn_open_batch_out)
        vbox_right.addLayout(h_bot_right)

        layout.addWidget(grp_right, stretch=1)

        self.batch_worker = None
        self.scan_worker = None
        self.scan_fresh = False
        self.scan_spec = {}
        self.discovered_batch_files = []
        self._batch_row_by_relative_path = {}

    def _on_batch_time_mode_changed(self):
        mode = self.combo_batch_time_mode.currentData()
        is_year = (mode == "year")
        self.wgt_batch_year.setVisible(is_year)
        self.wgt_batch_period.setVisible(not is_year)
        self._update_batch_expected_samples()

    def _apply_batch_mode_constraints(self, job_mode: Optional[str] = None):
        """根据当前选择的批量模式动态约束参数控件启用状态"""
        if job_mode is None:
            job_mode = self.cmb_batch_job_mode.currentData() if hasattr(self, 'cmb_batch_job_mode') else None
        is_from_cache = (job_mode in ("inundation-from-cache", "exposure-from-cache"))
        self.grp_batch_time.setEnabled(not is_from_cache)
        self.grp_batch_sci.setEnabled(not is_from_cache)
        self._update_batch_expected_samples()

    def _on_batch_job_mode_changed(self):
        job_mode = self.cmb_batch_job_mode.currentData()
        self._apply_batch_mode_constraints(job_mode)
        if job_mode == "tide":
            # 仅解算控制节点潮位 (生成 Cache)
            self.grp_batch_time.setEnabled(True)
            self.grp_batch_sci.setEnabled(True)
            self.lbl_batch_job_mode_tip.setText(
                "💡 仅解算控制网格潮位 (tide)：仅构建自适应控制网格与潮位时序并导出 *_tide.nc，不生成 Inundation 或 Exposure 空间栅格产品。"
            )
            self.lbl_batch_job_mode_tip.setStyleSheet(
                "color: #2E7D32; font-weight: bold; background-color: #E8F5E9; padding: 6px; border-radius: 4px; border: 1px solid #A5D6A7;"
            )
        elif job_mode == "tide-inundation":
            # 完整两阶段淹没流程
            self.grp_batch_time.setEnabled(True)
            self.grp_batch_sci.setEnabled(True)
            self.lbl_batch_job_mode_tip.setText(
                "💡 完整两阶段淹没流程 (tide-inundation)：Stage 1 解算并生成 Tide Cache (*_tide.nc)，Stage 2a 基于缓存解算潜在天文潮淹没频率与 QC GeoTIFF。"
            )
            self.lbl_batch_job_mode_tip.setStyleSheet(
                "color: #00796B; font-weight: bold; background-color: #E0F2F1; padding: 6px; border-radius: 4px; border: 1px solid #80CBC4;"
            )
        elif job_mode == "inundation-from-cache":
            # 基于已有 Tide Cache 解算淹没频率 (零 FES 开销)
            self.grp_batch_time.setEnabled(False)
            self.grp_batch_sci.setEnabled(False)
            self.lbl_batch_job_mode_tip.setText(
                "💡 从已有 Tide Cache 解算淹没频率 (inundation-from-cache)：复用已存在的 *_tide.nc 科学配置与时间序列，Stage 2 零 FES 外部调用，不覆写潮位缓存。"
            )
            self.lbl_batch_job_mode_tip.setStyleSheet(
                "color: #1565C0; font-weight: bold; background-color: #E3F2FD; padding: 6px; border-radius: 4px; border: 1px solid #90CAF9;"
            )
        elif job_mode == "tide-exposure":
            # 完整两阶段露出流程
            self.grp_batch_time.setEnabled(True)
            self.grp_batch_sci.setEnabled(True)
            self.lbl_batch_job_mode_tip.setText(
                "💡 完整两阶段露出流程 (tide-exposure)：Stage 1 解算并生成 Tide Cache (*_tide.nc)，Stage 2b 基于缓存解算潜在天文潮露出时间域 7 项空间栅格产品。"
            )
            self.lbl_batch_job_mode_tip.setStyleSheet(
                "color: #6A1B9A; font-weight: bold; background-color: #F3E5F5; padding: 6px; border-radius: 4px; border: 1px solid #CE93D8;"
            )
        elif job_mode == "exposure-from-cache":
            # 基于已有 Tide Cache 解算潜在露出 (零 FES 开销)
            self.grp_batch_time.setEnabled(False)
            self.grp_batch_sci.setEnabled(False)
            self.lbl_batch_job_mode_tip.setText(
                "💡 从已有 Tide Cache 解算潜在露出 (exposure-from-cache)：复用已存在的 *_tide.nc 科学配置与时间序列，Stage 2 零 FES 外部调用，输出 7 项 Exposure GeoTIFF。"
            )
            self.lbl_batch_job_mode_tip.setStyleSheet(
                "color: #E65100; font-weight: bold; background-color: #FFF3E0; padding: 6px; border-radius: 4px; border: 1px solid #FFCC80;"
            )
        elif job_mode == "all":
            # 全要素产物包
            self.grp_batch_time.setEnabled(True)
            self.grp_batch_sci.setEnabled(True)
            self.lbl_batch_job_mode_tip.setText(
                "💡 全要素产物包 (all)：Stage 1 解算并保存 Tide Cache (*_tide.nc)，随后 Stage 2a (淹没频率) 与 Stage 2b (露出时间域 7 项产品) 共同复用该缓存。"
            )
            self.lbl_batch_job_mode_tip.setStyleSheet(
                "color: #00695C; font-weight: bold; background-color: #E0F2F1; padding: 6px; border-radius: 4px; border: 1px solid #4DB6AC;"
            )
        else:
            self.grp_batch_time.setEnabled(True)
            self.grp_batch_sci.setEnabled(True)

    def _get_effective_batch_output_dir(self) -> str:
        """获取当前有效的输出目录（用户指定优先，默认回退至 <input>/CoastTideX_output）"""
        out_text = self.txt_batch_out_dir.text().strip()
        if out_text:
            return out_text
        in_text = self.txt_batch_in_dir.text().strip()
        if in_text:
            return os.path.join(in_text, "CoastTideX_output")
        return ""

    def _get_batch_frequency(self) -> str:
        """获取当前配置的采样时间步长"""
        step_data = self.cmb_batch_step.currentData()
        if step_data:
            return str(step_data)
        txt = self.cmb_batch_step.currentText()
        if "Custom" in txt and "(" in txt and ")" in txt:
            # 如 "Custom (45min)"
            inner = txt.split("(")[1].split(")")[0].strip()
            if inner:
                return inner
        return txt.split()[0]

    def _on_batch_step_changed(self):
        txt = self.cmb_batch_step.currentText()
        if "Custom" in txt:
            val, ok = QInputDialog.getText(
                self,
                "自定义采样步长 (Custom Step)",
                "请输入固定时间步长 (支持正整数分钟或小时，如 5min, 20min, 45min, 90min, 3h):",
                text="45min"
            )
            if ok and val.strip():
                val_clean = val.strip().lower()
                m = re.match(r"^(\d+)\s*(min|h|m)$", val_clean)
                if not m or int(m.group(1)) <= 0:
                    QMessageBox.warning(self, "格式错误", "仅支持正整数分钟或小时步长 (例如: 20min, 45min, 3h)。")
                    self.cmb_batch_step.setCurrentIndex(0)
                    return
                unit = "min" if m.group(2) in ("min", "m") else "h"
                freq_str = f"{m.group(1)}{unit}"
                idx = self.cmb_batch_step.currentIndex()
                self.cmb_batch_step.setItemText(idx, f"Custom ({freq_str})")
                self.cmb_batch_step.setItemData(idx, freq_str)
            else:
                self.cmb_batch_step.setCurrentIndex(0)
                return

        self._update_batch_expected_samples()

    def _update_batch_expected_samples(self):
        job_mode = self.cmb_batch_job_mode.currentData() if hasattr(self, 'cmb_batch_job_mode') else None
        if job_mode in ("inundation-from-cache", "exposure-from-cache"):
            self.lbl_batch_samples.setText("时间采样与科学配置：读取自已存在的 Tide Cache")
            return

        freq = self._get_batch_frequency()
        time_mode = self.combo_batch_time_mode.currentData() if hasattr(self, 'combo_batch_time_mode') else 'year'

        try:
            if time_mode == "year":
                yr = self.spn_batch_year.value()
                t_start = f"{yr:04d}-01-01 00:00:00"
                t_end = f"{yr+1:04d}-01-01 00:00:00"
                desc = f"{yr} 全年"
            else:
                t_start = self.time_batch_start.dateTime().toPyDateTime().strftime("%Y-%m-%d %H:%M:%S")
                t_end = self.time_batch_end.dateTime().toPyDateTime().strftime("%Y-%m-%d %H:%M:%S")
                desc = f"{t_start} 至 {t_end}"

            from core.utils import build_time_index
            t_idx, _, _ = build_time_index(t_start, t_end, freq=freq, inclusive='left', source_tz='UTC')
            n = len(t_idx)
            raw_kb = (n * 8) / 1024.0
            raw_100_mb = (n * 8 * 100) / (1024.0 * 1024.0)
            self.lbl_batch_samples.setText(
                f"预期采样步数: {n:,} 步 ({desc} @ {freq}, [start,end) | 双数组常驻 ~{raw_kb:.1f} KB/节点, 100节点 ~{raw_100_mb:.1f} MB)"
            )
        except Exception:
            self.lbl_batch_samples.setText(f"采样步长: {freq}")

    def _invalidate_batch_scan(self):
        """当输入目录、输出目录或递归选项更改时，使现有扫描快照立即失效"""
        self.scan_fresh = False
        self.discovered_batch_files = []
        self._batch_row_by_relative_path = {}
        self.table_batch_rasters.setRowCount(0)
        self.btn_start_batch.setEnabled(False)
        self.lbl_batch_status.setText("⚠️ 目录或扫描配置已改变，请点击“扫描文件夹”构建/刷新任务队列。")
        self.lbl_batch_counts.setText("总文件: 0 | 完成: 0 | 失败: 0 | 跳过: 0")
        self.btn_scan_batch.setText("🔍 扫描文件夹 (Scan GeoTIFFs)")

    def _on_browse_batch_input(self):
        d = QFileDialog.getExistingDirectory(self, "选择输入 GeoTIFF 目录")
        if d:
            self.txt_batch_in_dir.setText(d)
            # 保持输出目录输入框为空，由统一 helper 动态回退至 <input>/CoastTideX_output

    def _on_browse_batch_output(self):
        d = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if d:
            self.txt_batch_out_dir.setText(d)

    def _on_scan_batch_rasters(self):
        in_dir = self.txt_batch_in_dir.text().strip()
        if not in_dir or not os.path.exists(in_dir):
            QMessageBox.warning(self, "警告", "请先选择有效的输入文件夹！")
            return

        out_dir = self._get_effective_batch_output_dir()
        recursive = self.chk_batch_recursive.isChecked()

        self.btn_scan_batch.setEnabled(False)
        self.btn_scan_batch.setText("⏳ 正在扫描...")
        self.lbl_batch_status.setText("正在后台读取 GeoTIFF 头信息，请稍候...")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)

        self.scan_worker = BatchScanWorker(in_dir, out_dir, recursive)
        self.scan_worker.finished.connect(self._on_scan_finished)
        self.scan_worker.error.connect(self._on_scan_error)
        self.scan_worker.start()

    def _on_scan_finished(self, discovered_list, spec=None):
        QApplication.restoreOverrideCursor()
        self.btn_scan_batch.setEnabled(True)
        self.btn_scan_batch.setText("🔄 重新扫描 / 刷新队列 (Refresh Queue)")

        cur_in = self.txt_batch_in_dir.text().strip()
        cur_out = self._get_effective_batch_output_dir()
        cur_rec = self.chk_batch_recursive.isChecked()

        if spec:
            try:
                same_in = os.path.samefile(spec.get("in_dir", ""), cur_in) if (os.path.exists(spec.get("in_dir", "")) and os.path.exists(cur_in)) else (os.path.abspath(spec.get("in_dir", "")) == os.path.abspath(cur_in))
            except Exception:
                same_in = (os.path.abspath(spec.get("in_dir", "")) == os.path.abspath(cur_in))

            same_out = (os.path.abspath(spec.get("out_dir", "")) == os.path.abspath(cur_out))
            same_rec = (spec.get("recursive") == cur_rec)

            if not (same_in and same_out and same_rec):
                self._invalidate_batch_scan()
                self.lbl_batch_status.setText("⚠️ 目录或扫描配置已更改，已丢弃旧的扫描结果，请重新扫描。")
                return

        self.discovered_batch_files = discovered_list
        self.scan_spec = spec
        self.scan_fresh = True
        self._batch_row_by_relative_path = {}

        self.table_batch_rasters.setRowCount(len(discovered_list))
        valid_count = 0
        invalid_count = 0

        for r_idx, meta in enumerate(discovered_list):
            rel_p = meta.get("relative_path", meta.get("filename", ""))
            self._batch_row_by_relative_path[rel_p] = r_idx

            item_path = QTableWidgetItem(rel_p)
            item_path.setToolTip(meta.get("input_path", ""))
            self.table_batch_rasters.setItem(r_idx, 0, item_path)
            self.table_batch_rasters.setItem(r_idx, 1, QTableWidgetItem(f"{meta['file_size_mb']:.1f} MB"))
            self.table_batch_rasters.setItem(r_idx, 2, QTableWidgetItem(f"{meta['width']}×{meta['height']}"))
            self.table_batch_rasters.setItem(r_idx, 3, QTableWidgetItem(str(meta['crs'])[:20]))
            self.table_batch_rasters.setItem(r_idx, 4, QTableWidgetItem(f"{meta['resolution'][0]:.4f}"))

            if meta.get("valid", False):
                valid_count += 1
                status_item = QTableWidgetItem("就绪 (Ready)")
            else:
                invalid_count += 1
                status_item = QTableWidgetItem("无效 (Invalid)")
                status_item.setForeground(QColor("#ef4444"))
                status_item.setToolTip(f"格式错误: {meta.get('error', '未知错误')}")

            self.table_batch_rasters.setItem(r_idx, 5, status_item)
            self.table_batch_rasters.setItem(r_idx, 6, QTableWidgetItem("-"))
            self.table_batch_rasters.setItem(r_idx, 7, QTableWidgetItem("-"))
            self.table_batch_rasters.setItem(r_idx, 8, QTableWidgetItem("-"))

        self.lbl_batch_status.setText(f"扫描完成: 发现 {len(discovered_list)} 个 GeoTIFF (有效 {valid_count}, 无效 {invalid_count})。")
        self.lbl_batch_counts.setText(f"总文件: {len(discovered_list)} | 完成: 0 | 失败: 0 | 跳过: 0")
        self.btn_start_batch.setEnabled(valid_count > 0)

    def _on_scan_error(self, err_msg):
        QApplication.restoreOverrideCursor()
        self.btn_scan_batch.setEnabled(True)
        self.btn_scan_batch.setText("🔍 扫描文件夹 (Scan GeoTIFFs)")
        self._invalidate_batch_scan()
        QMessageBox.critical(self, "扫描错误", f"后台扫描目录失败: {err_msg}")

    def _set_batch_controls_running(self, is_running: bool):
        """批量任务运行期间锁定配置控件，防止用户误修改"""
        self.txt_batch_in_dir.setEnabled(not is_running)
        self.btn_browse_batch_in.setEnabled(not is_running)
        self.txt_batch_out_dir.setEnabled(not is_running)
        self.btn_browse_batch_out.setEnabled(not is_running)
        self.chk_batch_recursive.setEnabled(not is_running)
        self.btn_scan_batch.setEnabled(not is_running)
        self.cmb_batch_job_mode.setEnabled(not is_running)
        self.cmb_batch_existing_policy.setEnabled(not is_running)

        if is_running:
            self.grp_batch_time.setEnabled(False)
            self.grp_batch_sci.setEnabled(False)
            self.btn_start_batch.setEnabled(False)
            self.btn_cancel_batch.setEnabled(True)
        else:
            self._apply_batch_mode_constraints()
            self.btn_start_batch.setEnabled(True)
            self.btn_cancel_batch.setEnabled(False)

    def _on_start_batch(self):
        in_dir = self.txt_batch_in_dir.text().strip()
        if not in_dir or not os.path.exists(in_dir):
            QMessageBox.warning(self, "警告", "请先选择有效的输入文件夹！")
            return

        if not self.scan_fresh or not self.discovered_batch_files:
            QMessageBox.warning(self, "警告", "当前任务队列尚未扫描或已失效，请先点击“扫描文件夹”构建/刷新任务队列！")
            return

        out_dir = self._get_effective_batch_output_dir()
        job_mode = self.cmb_batch_job_mode.currentData()
        step_raw = self._get_batch_frequency()
        dem_datum = self.cmb_batch_datum.currentText().split()[0].lower()
        const_raw = self.cmb_batch_const.currentText().split()[0]
        target_mode = self.cmb_batch_target_mode.currentText().split()[0]
        existing_policy = self.cmb_batch_existing_policy.currentData()

        time_mode = self.combo_batch_time_mode.currentData()
        if time_mode == "year":
            yr = self.spn_batch_year.value()
            st_str = None
            et_str = None
        else:
            yr = self.time_batch_start.dateTime().date().year()
            st_str = self.time_batch_start.dateTime().toPyDateTime().strftime("%Y-%m-%d %H:%M:%S")
            et_str = self.time_batch_end.dateTime().toPyDateTime().strftime("%Y-%m-%d %H:%M:%S")

        params = {
            "input_folder": in_dir,
            "output_folder": out_dir,
            "job_mode": job_mode,
            "year": yr,
            "start_time": st_str,
            "end_time": et_str,
            "freq": step_raw,
            "dem_datum": dem_datum,
            "constituents": const_raw,
            "target_mode": target_mode,
            "existing_policy": existing_policy,
            "recursive": self.chk_batch_recursive.isChecked(),
            "discovered_files": self.discovered_batch_files  # 使用 GUI 显式扫描快照，禁止第二次全盘扫描
        }

        self._set_batch_controls_running(True)
        self.bar_batch_overall.setValue(0)
        self.bar_batch_tile.setValue(0)

        self.batch_worker = BatchRasterWorker(params)
        self.batch_worker.progress.connect(self._on_batch_progress)
        self.batch_worker.finished.connect(self._on_batch_finished)
        self.batch_worker.error.connect(self._on_batch_error)
        self.batch_worker.cancelled.connect(self._on_batch_cancelled)
        self.batch_worker.start()

    def _on_batch_progress(self, overall_pct, tile_pct, rel_path, msg, counts):
        self.bar_batch_overall.setValue(overall_pct)
        self.bar_batch_tile.setValue(tile_pct)
        if rel_path:
            self.lbl_batch_status.setText(f"[{rel_path}] {msg}")
        else:
            self.lbl_batch_status.setText(msg)
        c_done = counts.get('completed', counts.get('succeeded', 0))
        self.lbl_batch_counts.setText(
            f"总文件: {counts.get('total', 0)} | 完成: {c_done} | 失败: {counts.get('failed', 0)} | 跳过: {counts.get('skipped', 0)}"
        )

        # 基于 relative_path O(1) 字典查找更新表格对应行
        if rel_path and rel_path in self._batch_row_by_relative_path:
            r_idx = self._batch_row_by_relative_path[rel_path]
            status_item = self.table_batch_rasters.item(r_idx, 5)
            if status_item:
                status_item.setText(msg[:25])

            cache_item = self.table_batch_rasters.item(r_idx, 6)
            inund_item = self.table_batch_rasters.item(r_idx, 7)
            exp_item = self.table_batch_rasters.item(r_idx, 8)
            if "Stage 1" in msg or "Tide Cache" in msg:
                if cache_item:
                    cache_item.setText("COMPUTING")
            elif "Stage 2a" in msg:
                if cache_item:
                    cache_item.setText("READY")
                if inund_item:
                    inund_item.setText("COMPUTING")
            elif "Stage 2b" in msg or "露出" in msg or "Exposure" in msg:
                if cache_item:
                    cache_item.setText("READY")
                if exp_item:
                    exp_item.setText("COMPUTING")
            elif "Stage 2" in msg:
                if cache_item:
                    cache_item.setText("READY")
                if inund_item and inund_item.text() == "-":
                    inund_item.setText("COMPUTING")
            elif "完成" in msg or "PROCESSED" in msg:
                if cache_item and cache_item.text() in ("-", "COMPUTING"):
                    cache_item.setText("READY")
                if inund_item and inund_item.text() == "COMPUTING":
                    inund_item.setText("DONE")
                if exp_item and exp_item.text() == "COMPUTING":
                    exp_item.setText("DONE (7 prod)")

    def _on_batch_finished(self, res):
        self._set_batch_controls_running(False)
        self.bar_batch_overall.setValue(100)
        self.bar_batch_tile.setValue(100)
        self.lbl_batch_status.setText("批量解算任务全部完成！")
        c = res["counts"]
        QMessageBox.information(
            self,
            "批量解算完成",
            f"批量栅格解算任务执行完毕！\n\n"
            f"• 总计瓦片: {c['total']}\n"
            f"• 成功完成: {c['completed']}\n"
            f"• 异常失败: {c['failed']}\n"
            f"• 断点跳过: {c['skipped']}\n\n"
            f"任务清单已保存至:\n{res['manifest_json']}"
        )

    def _on_batch_error(self, err_msg):
        self._set_batch_controls_running(False)
        self.lbl_batch_status.setText("批量任务发生异常中断！")
        QMessageBox.critical(self, "批量任务错误", err_msg)

    def _on_batch_cancelled(self):
        self._set_batch_controls_running(False)
        self.lbl_batch_status.setText("批量任务已被用户取消。")
        QMessageBox.warning(self, "任务取消", "批量解算已被用户终止。已完成的瓦片与 Tide Cache 已安全保留。")

    def _on_cancel_batch(self):
        if self.batch_worker and self.batch_worker.isRunning():
            self.lbl_batch_status.setText("正在取消批量任务，等待当前操作回滚退出...")
            self.btn_cancel_batch.setEnabled(False)
            self.batch_worker.cancel()

    def _on_open_batch_output_folder(self):
        out_dir = self._get_effective_batch_output_dir()
        self._open_directory(out_dir)
