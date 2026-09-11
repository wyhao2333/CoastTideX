"""
CoastTideX 功能说明文档与操作手册对话框 (User Manual Dialog)
为用户提供系统级科学原理、高程基准定义、操作指引、时区规范与内存配置说明。
"""

from PyQt6.QtWidgets import QDialog, QVBoxLayout, QTextBrowser, QPushButton, QHBoxLayout
from PyQt6.QtCore import Qt


MANUAL_HTML = """
<!DOCTYPE html>
<html>
<head>
<style>
    body {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
        color: #e2e8f0;
        background-color: #1a1d24;
        line-height: 1.6;
        padding: 12px;
    }
    h1 { color: #38bdf8; border-bottom: 2px solid #0284c7; padding-bottom: 6px; font-size: 22px; }
    h2 { color: #60a5fa; margin-top: 20px; font-size: 17px; border-bottom: 1px solid #334155; padding-bottom: 4px; }
    h3 { color: #f59e0b; font-size: 14px; margin-top: 14px; }
    p, li { font-size: 13px; color: #cbd5e1; }
    code { background-color: #0f172a; color: #38bdf8; padding: 2px 5px; border-radius: 4px; font-family: Consolas, monospace; }
    pre { background-color: #0f172a; border: 1px solid #334155; padding: 10px; border-radius: 6px; color: #f8fafc; font-family: Consolas, monospace; }
    table { width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 12px; }
    th, td { border: 1px solid #334155; padding: 8px; text-align: left; }
    th { background-color: #1e293b; color: #38bdf8; }
    .callout-info { background-color: #1e3a8a; border-left: 4px solid #38bdf8; padding: 10px; border-radius: 4px; margin: 10px 0; }
    .callout-warn { background-color: #78350f; border-left: 4px solid #f59e0b; padding: 10px; border-radius: 4px; margin: 10px 0; }
    .callout-success { background-color: #064e3b; border-left: 4px solid #10b981; padding: 10px; border-radius: 4px; margin: 10px 0; }
</style>
</head>
<body>

<h1>📖 CoastTideX 用户操作手册与科学原理文档 (v1.2)</h1>

<div class="callout-info">
<b>CoastTideX</b> 是专为海洋工程、海岸带遥感、大地测量基准统一与水下水文模拟研发的高精度潮位解算与垂直基准转换桌面系统。
</div>

<h2>一、 核心科学模型与四大高程基准体系</h2>

<h3>1. 潮汐动力学模型：FES2022b 原生非结构有限元网格</h3>
<p>
本系统直接驱动法国 CNES/AVISO 研制的 <b>FES2022b 原生非结构有限元三角形网格 (LGP2 二阶多项式)</b>。
相比传统 1/30° 规则经纬度网格，原生有限元网格在深海稀疏（几十公里）、在大陆架与复杂海岸线自适应加密至数百米，
完全消除了规则矩形网格在曲折岸线上的“阶梯锯齿误差”。系统计算包含全部 34 个半日潮、日潮、浅海非线性与长周期分潮。
</p>

<h3>2. 严密的四大垂直基准换算关系 (严防基准张冠李戴)</h3>
<p>在海洋学与大地测量中，不同基准面之间存在天然的物理差异：</p>
<table>
    <tr><th>基准面名称</th><th>物理定义</th><th>换算关系式</th></tr>
    <tr><td><b>Tide (MSL)</b></td><td>相对局部平均海平面的瞬时潮位起伏</td><td>由 FES2022b 调和分析直接得出 (m)</td></tr>
    <tr><td><b>H_GOCO06S</b></td><td>相对 CNES-CLS22 MDT 原始参考面 (GOCO06s)</td><td><code>H_GOCO06S = Tide_MSL + MDT</code></td></tr>
    <tr><td><b>H_EGM2008</b></td><td>相对 EGM2008 大地水准面的绝对海拔正高</td><td><code>H_EGM2008 = Tide_MSL + MDT + ΔN</code><br>其中 <code>ΔN = N_GOCO06s - N_EGM2008</code></td></tr>
    <tr><td><b>h_WGS84</b></td><td>WGS84 几何空间椭球高 (GNSS常用)</td><td><code>h_WGS84 = H_EGM2008 + N_EGM2008</code></td></tr>
</table>

<div class="callout-warn">
<b>⚠️ 科学严密性提醒 (大地水准面差值改正)：</b><br>
CNES-CLS22 MDT 的参考重力场为 <b>GOCO06s (d/o=800)</b>，绝非 EGM2008！全球范围内两者大地水准面差距（ΔN）极值在 -6.6m ~ +6.8m 之间，平均离散度达 0.34m。<br>
若直接拿 <code>Tide + MDT</code> 去与陆地 LiDAR / EGM2008 DEM 拼接，将产生严重的米级基准失配！CoastTideX 内置了全球高精度 ΔN 差值改正栅格，真正实现了科学闭环。
</div>

<div class="callout-info">
<b>🌊 混合 MDT (Hybrid MDT) 区域大地水准面基准特性：</b><br>
根据 CNES/CLS 官方发布规范，全球大洋 MDT 参考重力场为 <b>GOCO06s</b>；而在<b>地中海 (CMEMS2020-MED)</b> 与<b>黑海 (CMEMS2020-BLK)</b>，MDT 参考的是超高阶 <b>EIGEN-6C4 (d/o=2190)</b> 综合重力模型。
EIGEN-6C4 与 EGM2008 均为 2190 阶高精度大地水准面，在地中海与黑海的水准面起伏偏差极小（通常在 ±5 cm 以内）。系统在底层自动识别落入该区域的点，并进行严谨标定。
</div>

<h2>二、 时区规范与动态联动 (UTC vs 本地时间)</h2>
<p>
PyFES 天文潮汐引潮力计算严格基于 <b>UTC (协调世界时)</b>。
</p>
<ul>
    <li><b>UTC 模式 (推荐)</b>：直接输入 UTC 时间，计算结果的相位严格与格林威治天文历元对齐。</li>
    <li><b>本地时区模式 (Local Time)</b>：系统根据您当前电脑系统时区（如中国标准时间 UTC+8），自动将输入的本地时刻严格换算为 UTC 时间送入模型，彻底杜绝了 8 小时潮汐相位颠倒（高潮变低潮）的致命错误！</li>
    <li><b>动态时区切换联动 (v1.2)</b>：计算完成后，用户若在时区下拉框中切换时区，系统将<b>实时自动重构图表时间轴和表格时间列</b>，无需重新触发计算，体验极其丝滑。</li>
    <li><b>夏令时 (DST) 稳健支持</b>：在穿越夏令时折返或跳变时刻时，系统采用非歧义平稳回退策略，杜绝产生静默 NaT 或时间倒退。</li>
</ul>

<h2>三、 💻 电脑硬件配置与内存需求 (System Requirements)</h2>
<table>
    <tr><th>工作模式</th><th>最低硬件建议</th><th>详细说明</th></tr>
    <tr><td><b>单点 / 局域连续时序模式</b></td><td><b>最低 4 GB RAM<br>(推荐 8 GB)</b></td><td>系统采用<b>自适应局部包围框 (BBox)</b> 动态裁剪技术，单点预测仅在内存中构建目标点周围 ±1° 的局部有限元网格拓扑。解算运行时常驻内存约 <b>1.2 GB</b>（包含 FES 局部拓扑结构、GDAL/Rasterio 栅格缓存与 Python 运行栈）。</td></tr>
    <tr><td><b>全球散点批量解算模式</b></td><td><b>推荐 8 GB RAM<br>或以上</b></td><td>采用<b>自适应空间分块聚类逐块解算 (Spatial Chunking)</b>，对空间上邻近的点集聚类成 5°×5° 窗口分批处理，内存占用平稳可控，杜绝内存溢出。</td></tr>
    <tr><td><b>无约束全网格全量加载</b></td><td><b>推荐 16 GB RAM</b></td><td>全量载入 3.77 GB NetCDF4 及其二阶有限元全局拓扑节点（569 万节点全量展开）。</td></tr>
</table>

<h2>四、 界面操作与主要功能说明</h2>

<h3>1. 窗口自由调整与自适应滚动 (v1.2)</h3>
<p>
针对笔记本电脑或常见 768p/1080p 显示屏垂直空间受限的问题，v1.2 版本将左侧控制参数面板封装于现代化 <code>QScrollArea</code> 滚动区域内。
窗口支持<b>上下自由拉伸与缩放</b>，彻底解除了最小高度锁定限制，任何分辨率屏幕均可舒适操作。
</p>

<h3>2. 纯 MSL 模式解耦 (v1.2)</h3>
<p>
若仅需快速计算和可视化相对平均海平面 (Tide MSL) 的天文潮位波形，系统不再强行检查 MDT 和 Geoid 栅格文件是否存在。
未勾选“垂直基准转换”时，用户无需配置数十兆的大地水准面栅格即可直接出图与导出，大幅降低轻量化应用的使用门槛。
</p>

<h3>3. 潮位预测时间间隔选择建议 (文献标准与证据)</h3>
<p>国际海洋与验潮业务中常用预测采样步长如下：</p>
<table>
    <tr><th>推荐时间间隔</th><th>应用场景与权威标准</th><th>科学依据与文献来源</th></tr>
    <tr><td><b>6 分钟 (0.1 小时)</b></td><td><b>实时验潮、风暴潮监测与浅海高阶谐波</b></td><td>美国国家海洋和大气局 (NOAA CO-OPS) 业务化验潮与实时潮位预报标准规范。高采样率可精准捕捉浅海非线性潮（M4, MS4, M6）的高频驻波与极值时刻。</td></tr>
    <tr><td><b>10 ~ 15 分钟</b></td><td><b>高精度潮汐分析与港口通航保障</b></td><td>联合国教科文组织政府间海洋学委员会 (IOC) 与全球海平面观测系统 (GLOSS) 推荐采样步长。在确保波形极值精度的同时有效控制长期观测数据量。</td></tr>
    <tr><td><b>1 小时 (60 分钟)</b></td><td><b>经典调和分析与长期海平面变化研究</b></td><td>经典潮汐调和分析（Foreman 1977, Pawlowicz et al. 2002）标准基准步长，适用于天级别至年级别的宏观潮汐规律推求。</td></tr>
</table>

<h3>4. 单点潮位模拟操作流程</h3>
<ol>
    <li>在左侧面板选择“预设站点”或手动输入目标经纬度；</li>
    <li>设置起始时间、结束时间、采样间隔（6分/10分/15分/30分/1小时）与时区；</li>
    <li>选择分潮模式（34全分潮推荐，8大主分潮适合快速预览）；</li>
    <li>如需绝对高程，勾选“执行完整垂直基准转换”；</li>
    <li>点击<b>「开始潮位模拟计算」</b>；</li>
    <li>右侧交互式图表将自动呈现高平滑度波形，并<b>自动检测并标注大潮波峰（高潮）与波谷（低潮）</b>的时间和水位值；</li>
    <li>下方表格支持一键导出为标准 CSV 或 Excel。</li>
</ol>

<h3>5. 批量站点多时刻解算</h3>
<ol>
    <li>切换至“批量站点多时刻解算”选项卡；</li>
    <li>点击“浏览文件”导入包含经度、纬度、时间的 CSV 表格；</li>
    <li>下拉框确认映射字段（支持智能自动识别）；</li>
    <li>点击<b>「开始批量解算」</b>，系统自动通过自适应空间分块聚类算法逐块解算；</li>
    <li>解算完成后可一键导出包含四大高程基准的完整结果报表。</li>
</ol>

<h3>6. 网格插值质量标识 (Quality Flag) 说明</h3>
<table>
    <tr><th>质量 Flag</th><th>物理状态</th><th>处理逻辑与科学意义</th></tr>
    <tr><td><b style="color:#10b981;">Flag 1 ~ 6</b></td><td>正常内插 (有效)</td><td>目标点严格位于高精度有限元三角形网格单元内部，多项式内插精度最高。</td></tr>
    <tr><td><b style="color:#f59e0b;">Flag &lt; 0</b></td><td>近岸外推 (警示)</td><td>目标点位于曲折岸线边缘或极浅滩涂，由动力学外推获得，界面以黄色高亮提示。</td></tr>
    <tr><td><b style="color:#ef4444;">Flag 0</b></td><td>陆地/缺失 (无效)</td><td>无有效潮汐解或完全位于陆地，潮位及高程严格置为 NaN，界面以红色高亮标出。</td></tr>
</table>

<h2>五、 v1.2 版本重要更新日志 (Changelog)</h2>
<ul>
    <li><b>[新增] 窗口垂直自由缩放</b>：控制面板集成 <code>QScrollArea</code> 滚动条，全面适配小尺寸屏幕与各种缩放比例，解除纵向大小调整锁定；</li>
    <li><b>[解耦] 纯 MSL 模式独立运行</b>：解除了纯潮位预测时对 MDT/Geoid 路径的强行依赖，未勾选转换时轻量快速运行；</li>
    <li><b>[科学] 地中海与黑海重力场基准严密识别</b>：系统自动识别 Hybrid MDT 在地中海与黑海采用的 EIGEN-6C4 (d/o=2190) 基准并做准确标定；</li>
    <li><b>[交互] 动态时区切换与图表/表格实时重构</b>：计算后切换时区即时联动刷新时间轴与数据表，无需重新解算；</li>
    <li><b>[稳健] 夏令时 (DST) 边界平稳过渡</b>：消除时区跳变或回折时刻的静默 NaT 风险；</li>
    <li><b>[优化] 修复 Affine 矩阵乘法弃用告警</b>：全面兼容最新版 <code>affine</code> 库；</li>
    <li><b>[升级] 内置国际标准潮位预测采样步长文献指南</b>：加入 NOAA (6分)、IOC/GLOSS (10-15分) 权威依据。</li>
</ul>

<h2>六、 科学引用与致谢</h2>
<ul>
    <li><b>FES2022b:</b> CNES, LEGOS, NOVELTIS & CLS (DOI: 10.24400/527896/a01-2024.004)</li>
    <li><b>CNES-CLS22 MDT:</b> CLS & CNES (DOI: 10.24400/527896/a01-2023.003)</li>
    <li><b>GOCO06s Gravity Field:</b> Kvas et al. (2021), DGK Report, ICGEM GFZ Potsdam.</li>
    <li><b>EGM2008 Geoid:</b> NGA, Pavlis et al. (2012), JGR.</li>
</ul>

</body>
</html>
"""


class ManualDialog(QDialog):
    """功能说明文档与操作手册弹窗"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("CoastTideX 功能说明文档与操作手册")
        self.resize(880, 680)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        self.browser = QTextBrowser()
        self.browser.setHtml(MANUAL_HTML)
        self.browser.setStyleSheet("""
            QTextBrowser {
                background-color: #1a1d24;
                border: 1px solid #334155;
                border-radius: 6px;
                color: #cbd5e1;
            }
        """)
        layout.addWidget(self.browser)

        layout_bottom = QHBoxLayout()
        layout_bottom.addStretch()
        btn_close = QPushButton("关闭手册")
        btn_close.clicked.connect(self.accept)
        btn_close.setStyleSheet("""
            QPushButton {
                background-color: #2563eb;
                color: white;
                border-radius: 5px;
                padding: 6px 18px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #3b82f6; }
        """)
        layout_bottom.addWidget(btn_close)
        layout.addLayout(layout_bottom)
