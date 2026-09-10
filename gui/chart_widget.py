"""
CoastTideX 交互式潮位波形绘制组件 (Interactive Tide Chart Widget)
基于 Matplotlib 与 PyQt6，提供高平滑度、极值标注与多基准面比对曲线。
"""

import numpy as np
import pandas as pd
from scipy.signal import find_peaks

import matplotlib
matplotlib.use('QtAgg')
# 支持中文字体与负号正常显示
matplotlib.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Arial Unicode MS', 'DejaVu Sans', 'sans-serif']
matplotlib.rcParams['axes.unicode_minus'] = False

from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib import dates as mdates

from PyQt6.QtWidgets import QWidget, QVBoxLayout


class TideChartWidget(QWidget):
    """
    嵌在 PyQt6 界面内的交互式潮位波形图表。
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        self.figure = Figure(figsize=(8, 4.5), dpi=100)
        self.figure.patch.set_facecolor('#1a1d24')

        self.canvas = FigureCanvasQTAgg(self.figure)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        self.toolbar.setStyleSheet("""
            QToolBar { background-color: #20242e; border: none; padding: 2px; }
            QToolButton { background-color: transparent; border-radius: 4px; margin: 1px; }
            QToolButton:hover { background-color: #334155; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas)

        self.ax = self.figure.add_subplot(111)
        self._init_empty_chart()

    def _init_empty_chart(self):
        """初始化空图表占位"""
        self.ax.clear()
        self.ax.set_facecolor('#0f172a')
        self.ax.text(
            0.5, 0.5, "请设置参数并点击「开始潮位模拟」\n等待绘制潮汐波形曲线",
            horizontalalignment='center', verticalalignment='center',
            transform=self.ax.transAxes, color='#94a3b8', fontsize=12
        )
        self.ax.set_xticks([])
        self.ax.set_yticks([])
        for spine in self.ax.spines.values():
            spine.set_color('#334155')
        self.canvas.draw()

    def plot_tide_series(
        self,
        df: pd.DataFrame,
        location_title: str = "目标海岸带",
        datum_mode: str = "both",
        time_col: str = "datetime_input",
        show_msl: bool = None,
        show_egm: bool = None
    ):
        """
        绘制潮位时间序列曲线，支持多基准面（MSL, EGM2008, GOCO06s, WGS84）对比，
        并按半日潮物理极值间隔 (~10-12小时) 自适应标记高潮点（波峰）与低潮点（波谷）。
        """
        self.ax.clear()
        self.ax.set_facecolor('#0f172a')

        # 兼容旧参数调用
        if show_msl is not None or show_egm is not None:
            if show_msl and show_egm:
                datum_mode = 'both'
            elif show_msl:
                datum_mode = 'msl'
            elif show_egm:
                datum_mode = 'egm'

        # 识别时间列
        if time_col in df.columns:
            raw_times = df[time_col]
        elif 'datetime_input' in df.columns:
            raw_times = df['datetime_input']
        elif 'datetime_utc' in df.columns:
            raw_times = df['datetime_utc']
        elif 'datetime' in df.columns:
            raw_times = df['datetime']
        else:
            raw_times = df.iloc[:, 0]

        times = pd.to_datetime(raw_times).to_numpy()

        # 识别潮位列
        msl_col = 'tide_msl_m' if 'tide_msl_m' in df.columns else ('tide_total_m' if 'tide_total_m' in df.columns else None)
        egm_col = 'h_egm2008_m' if 'h_egm2008_m' in df.columns else None
        goco_col = 'h_goco06s_m' if 'h_goco06s_m' in df.columns else None
        wgs_col = 'h_wgs84_m' if 'h_wgs84_m' in df.columns else None

        # 1. 绘制平均海平面 (MSL) 相对潮高
        if datum_mode in ['both', 'msl', 'all'] and msl_col is not None:
            msl_elev = df[msl_col].values
            self.ax.plot(
                times, msl_elev, label='MSL 潮位 (相对海平面, m)',
                color='#38bdf8', linewidth=2.0, zorder=3
            )

        # 2. 绘制 GOCO06s 基准面海面高
        if datum_mode in ['goco', 'all'] and goco_col is not None:
            goco_elev = df[goco_col].values
            self.ax.plot(
                times, goco_elev, label='GOCO06s 海面高 (Tide+MDT, m)',
                color='#a855f7', linewidth=1.8, linestyle=':', zorder=3
            )

        # 3. 绘制 EGM2008 绝对海拔正高
        if datum_mode in ['both', 'egm', 'all'] and egm_col is not None:
            egm_elev = df[egm_col].values
            self.ax.plot(
                times, egm_elev, label='EGM2008 正高 (绝对海拔, m)',
                color='#f59e0b', linewidth=2.2, linestyle='--', zorder=4
            )

        # 4. 绘制 WGS84 几何空间椭球高
        if datum_mode in ['wgs', 'all'] and wgs_col is not None:
            wgs_elev = df[wgs_col].values
            self.ax.plot(
                times, wgs_elev, label='WGS84 椭球高 (空间几何高, m)',
                color='#ec4899', linewidth=1.8, linestyle='-.', zorder=3
            )

        # 确定用于极值标注的主分析序列
        primary_col = egm_col
        if datum_mode == 'msl' or primary_col is None:
            primary_col = msl_col
        elif datum_mode == 'goco' and goco_col is not None:
            primary_col = goco_col
        elif datum_mode == 'wgs' and wgs_col is not None:
            primary_col = wgs_col

        if primary_col is not None and primary_col in df.columns:
            primary_series = df[primary_col].values
            valid_mask = ~np.isnan(primary_series)

            if np.sum(valid_mask) > 3:
                # 计算步长小时数并依据半日潮周期 (~10-12小时) 设定物理极值窗口
                if len(times) > 1:
                    dt_sec = abs((pd.to_datetime(times[1]) - pd.to_datetime(times[0])).total_seconds())
                    step_hours = max(0.001, dt_sec / 3600.0)
                else:
                    step_hours = 1.0
                peak_distance = max(1, int(10.0 / step_hours))

                clean_series = np.where(valid_mask, primary_series, -9999.0)
                peaks, _ = find_peaks(clean_series, distance=peak_distance)

                clean_neg_series = np.where(valid_mask, -primary_series, -9999.0)
                troughs, _ = find_peaks(clean_neg_series, distance=peak_distance)

                # 仅保留有效值点的极值
                peaks = [p for p in peaks if valid_mask[p]]
                troughs = [t for t in troughs if valid_mask[t]]

                # 标记高潮点 (波峰)
                if len(peaks) > 0:
                    self.ax.scatter(times[peaks], primary_series[peaks], color='#ef4444', s=45, zorder=6, label='高潮点 (High Tide)')
                    for p in peaks:
                        t_str = pd.to_datetime(times[p]).strftime('%m-%d %H:%M')
                        self.ax.annotate(
                            f"{primary_series[p]:.2f}m\n{t_str}",
                            (times[p], primary_series[p]),
                            textcoords="offset points", xytext=(0, 8),
                            ha='center', fontsize=8, color='#fca5a5',
                            bbox=dict(boxstyle='round,pad=0.2', fc='#7f1d1d', alpha=0.75, ec='none')
                        )

                # 标记低潮点 (波谷)
                if len(troughs) > 0:
                    self.ax.scatter(times[troughs], primary_series[troughs], color='#10b981', s=45, zorder=6, label='低潮点 (Low Tide)')
                    for tr in troughs:
                        t_str = pd.to_datetime(times[tr]).strftime('%m-%d %H:%M')
                        self.ax.annotate(
                            f"{primary_series[tr]:.2f}m\n{t_str}",
                            (times[tr], primary_series[tr]),
                            textcoords="offset points", xytext=(0, -18),
                            ha='center', fontsize=8, color='#86efac',
                            bbox=dict(boxstyle='round,pad=0.2', fc='#064e3b', alpha=0.75, ec='none')
                        )

        # 零高程参考辅助线
        self.ax.axhline(0, color='#64748b', linestyle=':', linewidth=1.2, alpha=0.8, zorder=1)

        # 轴标题与网格修饰
        tz_label = "UTC" if time_col == "datetime_utc" else "输入/本地时区"
        self.ax.set_title(f"潮位模拟波形曲线 - {location_title} ({tz_label})", color='#f8fafc', fontsize=13, pad=12, fontweight='bold')
        self.ax.set_ylabel("高程 / 水位高度 (米, m)", color='#cbd5e1', fontsize=11)

        # 时间格式化
        self.ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
        self.figure.autofmt_xdate(rotation=20, ha='right')

        self.ax.grid(True, linestyle='--', alpha=0.25, color='#94a3b8')
        for spine in self.ax.spines.values():
            spine.set_color('#334155')
        self.ax.tick_params(colors='#94a3b8', labelsize=9)

        # 图例设计
        self.ax.legend(
            loc='upper right', facecolor='#1e293b', edgecolor='#334155',
            labelcolor='#e2e8f0', fontsize=9
        )

        self.figure.tight_layout()
        self.canvas.draw()
