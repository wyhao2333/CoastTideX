"""
CoastTideX 桌面主窗口 (Main Window)
集成单点连续预测、批量多点解算、交互式波形分析、基准转换与报表导出。
"""

import os
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

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QDateTime
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QTabWidget, QGroupBox, QLabel, QLineEdit, QComboBox,
    QDateTimeEdit, QPushButton, QProgressBar, QTableWidget,
    QTableWidgetItem, QHeaderView, QFileDialog, QMessageBox,
    QSplitter, QStatusBar, QScrollArea, QFrame, QSpinBox,
    QCheckBox, QDoubleSpinBox
)
from PyQt6.QtGui import QIcon, QFont, QAction, QColor
import threading

from core.tide_engine import FESTidePredictor
from core.datum_engine import DatumTransformer
from core.raster_engine import RasterTideEngine, RasterInfo, RasterResultSummary
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
            datum_res = transformer.convert_tide_datums(
                df['tide_total_m'].values, self.lon, self.lat, datum_target=self.datum_mode, strict=False
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

            datum_res = transformer.convert_tide_datums(tide_msl, lons, lats, datum_target=self.datum_mode, strict=False)
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
    """空间栅格解算后台工作线程 (Snapshot / Inundation)"""
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(object)  # RasterResultSummary
    error = pyqtSignal(str)

    def __init__(self, mode: str, params: dict):
        super().__init__()
        self.mode = mode
        self.params = params
        self.cancel_event = threading.Event()
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True
        self.cancel_event.set()

    def run(self):
        try:
            engine = RasterTideEngine()

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
                    initial_control_spacing_m=self.params.get('initial_control_spacing_m', 4000.0),
                    min_control_spacing_m=self.params.get('min_control_spacing_m', 500.0),
                    inundation_error_tolerance_pct=self.params.get('inundation_error_tolerance_pct', 1.0),
                    block_size=self.params.get('block_size', 512),
                    strict=self.params.get('strict', True),
                    progress_callback=p_cb,
                    cancel_event=self.cancel_event
                )
            else:
                raise ValueError(f"未知栅格模式: {self.mode}")

            if not self._is_cancelled:
                self.finished.emit(summary)
        except Exception as e:
            if not self._is_cancelled:
                self.error.emit(str(e))


class MainWindow(QMainWindow):
    """CoastTideX 桌面客户端主窗口"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("CoastTideX - 全球海岸带潮位模拟与高程基准转换系统 v1.4")
        self.resize(1280, 800)
        self.setMinimumSize(960, 500)
        self.setStyleSheet(DARK_THEME_QSS)

        self.current_result_df = None
        self.batch_result_df = None
        self._current_tz_mode = "UTC"
        self._user_selected_freq = "30min"
        self.raster_worker = None
        self.current_raster_info = None

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
        self.tab_raster = QWidget()

        self.tabs.addTab(self.tab_single, " 🌊 单点/时段潮位序列 ")
        self.tabs.addTab(self.tab_batch, " 📊 批量站点多时刻解算 ")
        self.tabs.addTab(self.tab_raster, " 🗺️ 空间栅格解算 (Raster Engine) ")

        self._setup_single_tab()
        self._setup_batch_tab()
        self._setup_raster_tab()

        main_layout.addWidget(self.tabs)

        # 底部状态栏
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("就绪 - 欢迎使用 CoastTideX v1.4")

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
        self.combo_inund_datum.addItem("EGM2008 (大地水准面绝对正高)", "egm2008")
        self.combo_inund_datum.addItem("MSL (相对平均海平面)", "msl")
        self.combo_inund_datum.addItem("GOCO06s/EIGEN-6C4 (Tide+MDT)", "goco06s")
        self.combo_inund_datum.addItem("WGS84 (空间几何椭球高)", "wgs84")
        layout_inund.addWidget(self.combo_inund_datum, 2, 3)

        layout_inund.addWidget(QLabel("QC掩膜输出:"), 3, 0)
        self.edit_inund_qc = QLineEdit()
        self.edit_inund_qc.setPlaceholderText("留空则自动保存为 <主输出>_qc.tif")
        layout_inund.addWidget(self.edit_inund_qc, 3, 1, 1, 2)

        btn_browse_qc = QPushButton("浏览...")
        btn_browse_qc.setObjectName("btn_secondary")
        btn_browse_qc.clicked.connect(self._browse_inund_qc)
        layout_inund.addWidget(btn_browse_qc, 3, 3)

        layout_params.addWidget(self.container_inund)
        self.container_inund.setVisible(False)

        layout_main.addWidget(self.grp_params)

        # 4. 自适应网格高级参数
        self.grp_grid = QGroupBox("4. 自适应潮位控制网格与性能优化参数")
        layout_grid = QGridLayout(self.grp_grid)
        layout_grid.setSpacing(8)

        layout_grid.addWidget(QLabel("初始网格间距:"), 0, 0)
        self.spin_grid_init = QDoubleSpinBox()
        self.spin_grid_init.setRange(500.0, 50000.0)
        self.spin_grid_init.setValue(4000.0)
        self.spin_grid_init.setSingleStep(500.0)
        self.spin_grid_init.setSuffix(" m")
        layout_grid.addWidget(self.spin_grid_init, 0, 1)

        layout_grid.addWidget(QLabel("最小允许间距:"), 0, 2)
        self.spin_grid_min = QDoubleSpinBox()
        self.spin_grid_min.setRange(50.0, 10000.0)
        self.spin_grid_min.setValue(500.0)
        self.spin_grid_min.setSingleStep(100.0)
        self.spin_grid_min.setSuffix(" m")
        layout_grid.addWidget(self.spin_grid_min, 0, 3)

        layout_grid.addWidget(QLabel("容错误差阈值:"), 1, 0)
        self.spin_grid_tol = QDoubleSpinBox()
        self.spin_grid_tol.setRange(0.1, 20.0)
        self.spin_grid_tol.setValue(1.0)
        self.spin_grid_tol.setSingleStep(0.2)
        self.spin_grid_tol.setSuffix(" %")
        layout_grid.addWidget(self.spin_grid_tol, 1, 1)

        layout_grid.addWidget(QLabel("2D 分块大小:"), 1, 2)
        self.spin_grid_block = QSpinBox()
        self.spin_grid_block.setRange(64, 4096)
        self.spin_grid_block.setValue(512)
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

        layout_exec.addWidget(QLabel("输出 GeoTIFF 文件:"), 0, 0)
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
        if compute_mode == 'msl':
            idx = self.combo_display_datum.findData('msl')
            if idx >= 0:
                self.combo_display_datum.setCurrentIndex(idx)
            self.combo_display_datum.setEnabled(False)
        elif compute_mode == 'egm2008':
            self.combo_display_datum.setEnabled(True)
            cur = self.combo_display_datum.currentData()
            if cur not in ['egm', 'msl']:
                idx = self.combo_display_datum.findData('egm')
                if idx >= 0:
                    self.combo_display_datum.setCurrentIndex(idx)
        else:
            self.combo_display_datum.setEnabled(True)

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
        elif datum_mode == 'goco':
            col = 'h_goco06s_m'
            target_name = "GOCO06s (Tide+MDT)"
        elif datum_mode == 'wgs':
            col = 'h_wgs84_m'
            target_name = "WGS84 (几何空间椭球高)"
        else:  # 'both', 'egm', 'all'
            col = 'h_egm2008_m'
            target_name = "EGM2008 (大地水准面正高)"

        self.lbl_stat_target.setText(f"<b>{target_name}</b>")

        if col in df.columns:
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
                self.lbl_max.setText("<span style='color:#94a3b8;'>NaN (陆地/无数据)</span>")
                self.lbl_min.setText("<span style='color:#94a3b8;'>NaN (陆地/无数据)</span>")
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

        # 更新网格质量评价标识
        qc_warn = self.current_scalar_datum.get('qc_warning', 'NORMAL')
        if qc_warn == 'QC_MED_BLACK_SEA_EIGEN6C4':
            self.lbl_qc_status.setText("<span style='color:#38bdf8;font-weight:bold;'>ℹ️ 地中海/黑海 (MDT参考 EIGEN-6C4)</span>")
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

    # ================= 空间栅格解算逻辑 (Raster Engine v1.4) =================
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
        else:
            year = self.spin_inund_year.value() if self.combo_inund_time_mode.currentData() == 'year' else 'period'
            self.edit_raster_output.setText(f"{base}_inundation_{year}{ext}")
            self.edit_inund_qc.setText(f"{base}_inundation_{year}_qc{ext}")

    def _browse_raster_input(self):
        f, _ = QFileDialog.getOpenFileName(self, "选择输入 GeoTIFF 影像", "", "GeoTIFF (*.tif *.tiff *.geotiff);;All Files (*.*)")
        if f:
            self.edit_raster_input.setText(f)

    def _browse_raster_output(self):
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
            self.status_bar.showMessage(f"已加载栅格元数据: {os.path.basename(path)}")
        except Exception as e:
            QMessageBox.critical(self, "检查失败", f"无法解析 GeoTIFF 元数据:\n{e}")

    def _on_raster_mode_changed(self):
        mode = self.combo_raster_mode.currentData()
        is_snap = (mode == 'snapshot')
        self.container_snapshot.setVisible(is_snap)
        self.container_inund.setVisible(not is_snap)
        self.grp_grid.setVisible(not is_snap)
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

    def _run_raster_simulation(self):
        inp_path = self.edit_raster_input.text().strip()
        out_path = self.edit_raster_output.text().strip()
        if not inp_path or not os.path.exists(inp_path):
            QMessageBox.warning(self, "输入错误", "请输入并确认有效的 GeoTIFF 栅格路径！")
            return
        if not out_path:
            QMessageBox.warning(self, "输入错误", "请指定输出 GeoTIFF 文件路径！")
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
        else:
            time_mode = self.combo_inund_time_mode.currentData()
            qc_out = self.edit_inund_qc.text().strip() or None
            params = {
                'input_path': inp_path,
                'output_path': out_path,
                'qc_output_path': qc_out,
                'freq': self.combo_inund_freq.currentData(),
                'dem_datum': self.combo_inund_datum.currentData(),
                'constituents': 'all',
                'source_tz': 'UTC',
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
        self.raster_worker.error.connect(self._on_raster_error)
        self.raster_worker.start()

    def _cancel_raster_simulation(self):
        if self.raster_worker and self.raster_worker.isRunning():
            self.raster_worker.cancel()
            self.lbl_raster_status.setText("正在取消任务并清理临时文件...")
            self.btn_cancel_raster.setEnabled(False)

    def _on_raster_progress(self, pct, msg):
        self.prog_raster.setValue(pct)
        self.lbl_raster_status.setText(msg)
        self.status_bar.showMessage(msg)

    def _on_raster_finished(self, summary):
        try:
            self.btn_run_raster.setEnabled(True)
            self.btn_cancel_raster.setEnabled(False)
            self.prog_raster.setValue(100)
            self.lbl_raster_status.setText(f"解算圆满完成！耗时 {summary.elapsed_seconds:.2f} 秒。")
            self.status_bar.showMessage("空间栅格解算圆满完成！")

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
                out_dir = os.path.dirname(os.path.abspath(summary.output_path))
                if os.path.exists(out_dir):
                    import subprocess
                    subprocess.Popen(f'explorer "{out_dir}"')
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.warning(self, "显示完成信息异常", f"解算已完成并保存至:\n{summary.output_path}\n\n但弹窗提示异常: {e}")

    def _on_raster_error(self, err_msg):
        self.btn_run_raster.setEnabled(True)
        self.btn_cancel_raster.setEnabled(False)
        self.prog_raster.setValue(0)
        self.lbl_raster_status.setText("解算失败")
        self.status_bar.showMessage("栅格解算发生错误")
        QMessageBox.critical(self, "解算错误", f"空间栅格解算失败:\n{err_msg}")

    def _open_settings(self):
        dialog = SettingsDialog(self)
        dialog.exec()

    def _show_manual(self):
        dialog = ManualDialog(self)
        dialog.exec()

    def _show_about(self):
        about_text = (
            "<h3>CoastTideX v1.4</h3>"
            "<p><b>全球海岸带空间栅格潮位模拟与高程基准转换系统</b></p>"
            "<p>致力于为海洋工程、海岸带遥感、大地测量与水下水文建模提供最高保真度的空间潮汐预测与严密基准转换工具。</p>"
            "<ul>"
            "<li><b>潮汐动力学</b>: FES2022b 原生非结构有限元三角形网格 (LGP2, 34分潮)</li>"
            "<li><b>四大多元基准体系</b>: "
            "<ul>"
            "<li>MSL (相对平均海平面)</li>"
            "<li>MDT 原始大地水准面基准 (全球大洋 GOCO06s / 地中海与黑海 EIGEN-6C4)</li>"
            "<li>EGM2008 (经 ΔN 改正的严密海拔正高)</li>"
            "<li>WGS84 (GNSS 空间几何三维椭球高)</li>"
            "</ul></li>"
            "<li><b>平均动态地形</b>: CNES-CLS22 MDT (全球大洋与边缘海混合产品)</li>"
            "<li><b>高精度水准面栅格</b>: NGA EGM2008 2.5' 全球全分辨率网格</li>"
            "<li><b>v1.4 新特性 (Spatial Raster Engine)</b>: "
            "<ul>"
            "<li><b>空间栅格解算引擎 (Tab 3)</b>: 支持 GeoTIFF 空间单时刻潮位计算与高分辨率 DEM 潜在天文潮淹没频率解算；</li>"
            "<li><b>自适应潮位控制网格 (Adaptive Tide Control Grid)</b>: 采用空间梯度自适应四叉树细分与经验互补分布 (CCDF)，防跨陆地盲插值；</li>"
            "<li><b>基准计算与显示解耦</b>: 单点解算区分计算目标与显示/统计目标，切换显示零计算开销；</li>"
            "<li><b>权威混合 MDT 掩膜优先</b>: 优先加载权威 GeoTIFF 掩膜，多边形作为安全备用并标记 QC_DATUM_SOURCE_APPROX；</li>"
            "<li><b>流式 2D 矩形分块 I/O</b>: 512x512 内存安全分块流式吞吐，支持原子级写入保护与富元数据 (Provenance) 嵌入。</li>"
            "</ul></li>"
            "</ul>"
            "<p>出品：wyhao2333 | 核心引擎：CNES/AVISO pyfes, rasterio, pyproj & scipy</p>"
        )
        QMessageBox.about(self, "关于 CoastTideX", about_text)
