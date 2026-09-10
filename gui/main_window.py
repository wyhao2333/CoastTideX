"""
CoastTideX 桌面主窗口 (Main Window)
集成单点连续预测、批量多点解算、交互式波形分析、基准转换与报表导出。
"""

import os
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QDateTime
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QTabWidget, QGroupBox, QLabel, QLineEdit, QComboBox,
    QDateTimeEdit, QPushButton, QProgressBar, QTableWidget,
    QTableWidgetItem, QHeaderView, QFileDialog, QMessageBox,
    QSplitter, QStatusBar
)
from PyQt6.QtGui import QIcon, QFont, QAction

from core.tide_engine import FESTidePredictor
from core.datum_engine import DatumTransformer
from core.utils import COASTAL_PRESETS, export_dataframe, load_app_config
from .chart_widget import TideChartWidget
from .settings_dialog import SettingsDialog
from .styles import DARK_THEME_QSS


class SingleTideWorker(QThread):
    """后台单点潮位计算线程，保障界面交互不卡顿"""
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(pd.DataFrame, float, float)
    error = pyqtSignal(str)

    def __init__(self, lon, lat, start_time, end_time, freq, constituents):
        super().__init__()
        self.lon = lon
        self.lat = lat
        self.start_time = start_time
        self.end_time = end_time
        self.freq = freq
        self.constituents = constituents

    def run(self):
        try:
            predictor = FESTidePredictor()
            transformer = DatumTransformer()

            def p_cb(percent, msg):
                self.progress.emit(percent, msg)

            # 1. 运行潮位时间序列预测
            df = predictor.predict_series(
                lon=self.lon,
                lat=self.lat,
                start_time=self.start_time,
                end_time=self.end_time,
                freq=self.freq,
                constituents=self.constituents,
                progress_callback=p_cb
            )

            # 2. 运行垂直基准转换 (MSL -> EGM2008)
            p_cb(85, "转换高程基准至 EGM2008 与大地水准面...")
            egm_elevs, mdt_val = transformer.convert_msl_to_egm2008(
                df['tide_total_m'].values, self.lon, self.lat
            )
            n_geoid = transformer.get_geoid_undulation(self.lon, self.lat)

            df['mdt_m'] = mdt_val
            df['h_egm2008_m'] = egm_elevs
            df['geoid_undulation_n_m'] = n_geoid

            p_cb(100, "全部计算完成！")
            self.finished.emit(df, mdt_val, n_geoid)

        except Exception as e:
            self.error.emit(str(e))


class BatchTideWorker(QThread):
    """后台批量表格计算线程"""
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(pd.DataFrame)
    error = pyqtSignal(str)

    def __init__(self, df_records, lon_col, lat_col, time_col, constituents):
        super().__init__()
        self.df_records = df_records
        self.lon_col = lon_col
        self.lat_col = lat_col
        self.time_col = time_col
        self.constituents = constituents

    def run(self):
        try:
            predictor = FESTidePredictor()
            transformer = DatumTransformer()

            def p_cb(percent, msg):
                self.progress.emit(percent, msg)

            df_out = predictor.predict_batch(
                self.df_records,
                lon_col=self.lon_col,
                lat_col=self.lat_col,
                time_col=self.time_col,
                constituents=self.constituents,
                progress_callback=p_cb
            )

            # 逐点计算基准转换
            p_cb(75, "逐点匹配 MDT 与 EGM2008 基准...")
            mdt_list = []
            egm_list = []
            for _, row in df_out.iterrows():
                lon_val = float(row[self.lon_col])
                lat_val = float(row[self.lat_col])
                tide_m = float(row['tide_total_m'])
                egm_val, mdt_val = transformer.convert_msl_to_egm2008(tide_m, lon_val, lat_val)
                mdt_list.append(mdt_val)
                egm_list.append(egm_val)

            df_out['mdt_m'] = mdt_list
            df_out['h_egm2008_m'] = egm_list

            p_cb(100, "批量计算完成！")
            self.finished.emit(df_out)
        except Exception as e:
            self.error.emit(str(e))


class MainWindow(QMainWindow):
    """CoastTideX 桌面客户端主窗口"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("CoastTideX - 全球海岸带潮位模拟与高程基准转换系统 v1.0")
        self.resize(1280, 850)
        self.setStyleSheet(DARK_THEME_QSS)

        self.current_result_df = None
        self.batch_result_df = None

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

        self.tabs.addTab(self.tab_single, " 🌊 单点潮汐模拟与波形分析 ")
        self.tabs.addTab(self.tab_batch, " 📊 批量站点多时刻解算 ")

        self._setup_single_tab()
        self._setup_batch_tab()

        main_layout.addWidget(self.tabs)

        # 底部状态栏
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("就绪 - 欢迎使用 CoastTideX")

    def _setup_single_tab(self):
        layout = QHBoxLayout(self.tab_single)
        layout.setSpacing(10)

        # 左侧控制面板 (固定宽度)
        left_panel = QWidget()
        left_panel.setFixedWidth(360)
        layout_left = QVBoxLayout(left_panel)
        layout_left.setContentsMargins(0, 0, 0, 0)
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

        layout_time.addWidget(QLabel("起始时间:"), 0, 0)
        self.time_start = QDateTimeEdit(QDateTime.currentDateTime())
        self.time_start.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.time_start.setCalendarPopup(True)
        layout_time.addWidget(self.time_start, 0, 1)

        layout_time.addWidget(QLabel("结束时间:"), 1, 0)
        self.time_end = QDateTimeEdit(QDateTime.currentDateTime().addDays(1))
        self.time_end.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.time_end.setCalendarPopup(True)
        layout_time.addWidget(self.time_end, 1, 1)

        layout_time.addWidget(QLabel("采样间隔:"), 2, 0)
        self.combo_freq = QComboBox()
        self.combo_freq.addItems(["10分钟 (10min)", "15分钟 (15min)", "30分钟 (30min)", "1小时 (1h)", "2小时 (2h)"])
        self.combo_freq.setCurrentIndex(3)
        layout_time.addWidget(self.combo_freq, 2, 1)

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

        layout_model.addWidget(QLabel("显示基准面:"), 1, 0)
        self.combo_datum = QComboBox()
        self.combo_datum.addItem("对比输出 (MSL & EGM2008)", "both")
        self.combo_datum.addItem("仅 MSL (相对平均海平面)", "msl")
        self.combo_datum.addItem("仅 EGM2008 (大地水准面绝对高)", "egm")
        self.combo_datum.currentIndexChanged.connect(self._on_datum_display_changed)
        layout_model.addWidget(self.combo_datum, 1, 1)

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
        grp_stat = QGroupBox("4. 统计极值指标")
        layout_stat = QGridLayout(grp_stat)
        self.lbl_max = QLabel("-")
        self.lbl_min = QLabel("-")
        self.lbl_range = QLabel("-")
        self.lbl_mdt = QLabel("-")

        layout_stat.addWidget(QLabel("最高潮位 (峰值):"), 0, 0)
        layout_stat.addWidget(self.lbl_max, 0, 1)
        layout_stat.addWidget(QLabel("最低潮位 (谷值):"), 1, 0)
        layout_stat.addWidget(self.lbl_min, 1, 1)
        layout_stat.addWidget(QLabel("最大潮差 (Range):"), 2, 0)
        layout_stat.addWidget(self.lbl_range, 2, 1)
        layout_stat.addWidget(QLabel("当地 MDT 偏置:"), 3, 0)
        layout_stat.addWidget(self.lbl_mdt, 3, 1)
        layout_left.addWidget(grp_stat)

        layout_left.addStretch()
        layout.addWidget(left_panel)

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
        self.table_single.setColumnCount(7)
        self.table_single.setHorizontalHeaderLabels([
            "时间 (UTC)", "潮位 MSL (m)", "当地 MDT (m)", "高程 EGM2008 (m)",
            "短周期日/半日潮 (cm)", "长周期潮 (cm)", "质量 Flag"
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

        self.btn_run_batch = QPushButton("⚡ 开始批量解算")
        self.btn_run_batch.setFixedHeight(36)
        self.btn_run_batch.setEnabled(False)
        self.btn_run_batch.clicked.connect(self._run_batch_simulation)
        layout_bc.addWidget(self.btn_run_batch, 4, 1)

        self.prog_batch = QProgressBar()
        self.prog_batch.setValue(0)
        layout_bc.addWidget(self.prog_batch, 4, 2)

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

    def _set_default_values(self):
        # 默认选中长江口
        idx = self.combo_presets.findText("长江口 (Changjiang Estuary)")
        if idx >= 0:
            self.combo_presets.setCurrentIndex(idx)

    def _on_preset_changed(self, index):
        name = self.combo_presets.currentText()
        if name in COASTAL_PRESETS:
            preset = COASTAL_PRESETS[name]
            self.edit_lon.setText(f"{preset['lon']:.4f}")
            self.edit_lat.setText(f"{preset['lat']:.4f}")
            self.status_bar.showMessage(f"已选择预设: {name} - {preset['desc']}")

    def _on_datum_display_changed(self):
        if self.current_result_df is not None:
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

        t_start = self.time_start.dateTime().toString("yyyy-MM-dd HH:mm:ss")
        t_end = self.time_end.dateTime().toString("yyyy-MM-dd HH:mm:ss")

        if self.time_start.dateTime() >= self.time_end.dateTime():
            QMessageBox.warning(self, "时间错误", "起始时间必须早于结束时间！")
            return

        freq_map = {
            "10分钟 (10min)": "10min",
            "15分钟 (15min)": "15min",
            "30分钟 (30min)": "30min",
            "1小时 (1h)": "1h",
            "2小时 (2h)": "2h"
        }
        freq = freq_map.get(self.combo_freq.currentText(), "1h")
        constituents = self.combo_const.currentData()

        self.btn_run_single.setEnabled(False)
        self.prog_single.setValue(5)
        self.status_bar.showMessage("正在后台加载网格并解算潮位...")

        self.worker = SingleTideWorker(lon, lat, t_start, t_end, freq, constituents)
        self.worker.progress.connect(self._on_single_progress)
        self.worker.finished.connect(self._on_single_finished)
        self.worker.error.connect(self._on_single_error)
        self.worker.start()

    def _on_single_progress(self, percent, msg):
        self.prog_single.setValue(percent)
        self.status_bar.showMessage(msg)

    def _on_single_finished(self, df, mdt_val, n_geoid):
        self.current_result_df = df
        self.btn_run_single.setEnabled(True)
        self.btn_export_csv.setEnabled(True)
        self.btn_export_excel.setEnabled(True)
        self.prog_single.setValue(100)
        self.status_bar.showMessage(f"模拟计算完成！共生成 {len(df)} 个时间步长点。")

        # 更新极值指标
        max_tide = df['h_egm2008_m'].max()
        min_tide = df['h_egm2008_m'].min()
        tide_range = max_tide - min_tide

        self.lbl_max.setText(f"<b style='color:#ef4444;'>{max_tide:+.2f} m</b>")
        self.lbl_min.setText(f"<b style='color:#10b981;'>{min_tide:+.2f} m</b>")
        self.lbl_range.setText(f"<b>{tide_range:.2f} m</b>")
        self.lbl_mdt.setText(f"<b>{mdt_val:+.4f} m</b>")

        # 刷新图表与表格
        self._update_chart()
        self._populate_table(df)

    def _on_single_error(self, err_msg):
        self.btn_run_single.setEnabled(True)
        self.prog_single.setValue(0)
        self.status_bar.showMessage("解算发生错误")
        QMessageBox.critical(self, "解算错误", f"潮位模拟失败:\n{err_msg}")

    def _update_chart(self):
        if self.current_result_df is None:
            return

        datum_mode = self.combo_datum.currentData()
        show_msl = datum_mode in ['both', 'msl']
        show_egm = datum_mode in ['both', 'egm']

        preset_name = self.combo_presets.currentText()
        loc_title = preset_name if preset_name != "自定义坐标..." else f"({self.edit_lon.text()}°, {self.edit_lat.text()}°)"

        self.chart_widget.plot_tide_series(
            self.current_result_df,
            location_title=loc_title,
            show_msl=show_msl,
            show_egm=show_egm
        )

    def _populate_table(self, df):
        self.table_single.setRowCount(0)
        self.table_single.setRowCount(len(df))

        for row_idx, row in df.iterrows():
            t_str = str(row['datetime'])[:19]
            msl_val = f"{row['tide_total_m']:+.3f}"
            mdt_val = f"{row['mdt_m']:+.3f}"
            egm_val = f"{row['h_egm2008_m']:+.3f}"
            sp_val = f"{row['tide_short_period_cm']:+.2f}"
            lp_val = f"{row['tide_long_period_cm']:+.2f}"
            flag_val = str(int(row['quality_flag']))

            items = [t_str, msl_val, mdt_val, egm_val, sp_val, lp_val, flag_val]
            for col_idx, text in enumerate(items):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table_single.setItem(row_idx, col_idx, item)

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
                    if 'lon' in cl:
                        self.combo_col_lon.setCurrentText(col)
                    elif 'lat' in cl:
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

        self.btn_run_batch.setEnabled(False)
        self.prog_batch.setValue(10)

        self.batch_worker = BatchTideWorker(df_full, lon_col, lat_col, time_col, constituents='all')
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

    def _open_settings(self):
        dialog = SettingsDialog(self)
        dialog.exec()

    def _show_about(self):
        about_text = (
            "<h3>CoastTideX v1.0</h3>"
            "<p><b>全球海岸带潮位模拟与高程基准转换系统</b></p>"
            "<p>致力于为海洋工程、海岸带遥感与水下水文建模提供最高保真度的潮汐预测工具。</p>"
            "<ul>"
            "<li><b>潮汐模型</b>: FES2022b 原生非结构有限元网格 (LGP2)</li>"
            "<li><b>高程基准</b>: 局部平均海平面 (MSL) & EGM2008 大地水准面</li>"
            "<li><b>动态地形</b>: CNES-CLS22 MDT (1993-2012 20年基准)</li>"
            "<li><b>大地水准面</b>: NGA EGM2008 2.5分全分辨率栅格</li>"
            "</ul>"
            "<p>出品：wyhao2333 | 基于 CNES/AVISO pyfes 引擎</p>"
        )
        QMessageBox.about(self, "关于 CoastTideX", about_text)
