"""
CoastTideX 交互式潮位波形绘制组件 (Interactive Tide Chart Widget)
基于 Matplotlib 与 PyQt6，提供高平滑度、极值标注与多基准面比对曲线。
"""

import numpy as np
import pandas as pd
from scipy.signal import find_peaks

import matplotlib
matplotlib.use('QtAgg')
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
        show_msl: bool = True,
        show_egm: bool = True
    ):
        """
        绘制潮位时间序列曲线，并自动标记高潮点（波峰）与低潮点（波谷）。
        """
        self.ax.clear()
        self.ax.set_facecolor('#0f172a')

        times = pd.to_datetime(df['datetime']).to_numpy()

        # 绘制平均海平面 (MSL) 相对潮高
        if show_msl and 'tide_total_m' in df.columns:
            msl_elev = df['tide_total_m'].values
            self.ax.plot(
                times, msl_elev, label='潮位 (MSL 基准, m)',
                color='#38bdf8', linewidth=2.2, zorder=3
            )

        # 绘制 EGM2008 绝对海拔高
        if show_egm and 'h_egm2008_m' in df.columns:
            egm_elev = df['h_egm2008_m'].values
            self.ax.plot(
                times, egm_elev, label='高程 (EGM2008 基准, m)',
                color='#f59e0b', linewidth=2.0, linestyle='--', zorder=3
            )

        # 寻找极值点（以主要绘制序列为准）
        primary_series = df['h_egm2008_m'].values if (show_egm and 'h_egm2008_m' in df.columns) else df['tide_total_m'].values
        if len(primary_series) > 3:
            peaks, _ = find_peaks(primary_series, distance=max(1, len(primary_series)//24))
            troughs, _ = find_peaks(-primary_series, distance=max(1, len(primary_series)//24))

            # 标记高潮点 (波峰)
            if len(peaks) > 0:
                self.ax.scatter(times[peaks], primary_series[peaks], color='#ef4444', s=45, zorder=5, label='高潮点 (High Tide)')
                for p in peaks:
                    t_str = pd.to_datetime(times[p]).strftime('%m-%d %H:%M')
                    self.ax.annotate(
                        f"{primary_series[p]:.2f}m\n{t_str}",
                        (times[p], primary_series[p]),
                        textcoords="offset points", xytext=(0, 8),
                        ha='center', fontsize=8, color='#fca5a5',
                        bbox=dict(boxstyle='round,pad=0.2', fc='#7f1d1d', alpha=0.7, ec='none')
                    )

            # 标记低潮点 (波谷)
            if len(troughs) > 0:
                self.ax.scatter(times[troughs], primary_series[troughs], color='#10b981', s=45, zorder=5, label='低潮点 (Low Tide)')
                for tr in troughs:
                    t_str = pd.to_datetime(times[tr]).strftime('%m-%d %H:%M')
                    self.ax.annotate(
                        f"{primary_series[tr]:.2f}m\n{t_str}",
                        (times[tr], primary_series[tr]),
                        textcoords="offset points", xytext=(0, -18),
                        ha='center', fontsize=8, color='#86efac',
                        bbox=dict(boxstyle='round,pad=0.2', fc='#064e3b', alpha=0.7, ec='none')
                    )

        # 零高程参考辅助线
        self.ax.axhline(0, color='#64748b', linestyle=':', linewidth=1.2, alpha=0.8, zorder=1)

        # 轴标题与网格修饰
        self.ax.set_title(f"潮位模拟波形曲线 - {location_title}", color='#f8fafc', fontsize=13, pad=12, fontweight='bold')
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
