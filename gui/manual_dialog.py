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

<h1>📖 CoastTideX 用户操作手册与科学原理文档 (v1.4)</h1>

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

<h3>2. 严密的垂直基准换算关系 (科学严防基准张冠李戴)</h3>
<p>在海洋学与大地测量中，不同基准面之间存在天然的物理差异：</p>
<table>
    <tr><th>基准面名称</th><th>物理定义</th><th>换算关系式</th></tr>
    <tr><td><b>Tide (MSL)</b></td><td>相对局部平均海平面的瞬时潮位起伏</td><td>由 FES2022b 调和分析直接得出 (m)</td></tr>
    <tr><td><b>H_MDT_REF</b></td><td>相对当地 MDT 原始参考水准面的瞬时海面高</td><td><code>H_MDT_REF = Tide_MSL + MDT</code></td></tr>
    <tr><td><b>H_EGM2008</b></td><td>相对 EGM2008 大地水准面的绝对海拔正高</td><td><code>H_EGM2008 = H_MDT_REF + ΔN</code><br>大洋: <code>ΔN = N_GOCO06s - N_EGM2008</code><br>地中海/黑海: <code>ΔN = N_EIGEN6C4 - N_EGM2008</code></td></tr>
    <tr><td><b>h_WGS84</b></td><td>WGS84 几何空间三维椭球高 (GNSS常用)</td><td><code>h_WGS84 = H_EGM2008 + N_EGM2008</code></td></tr>
</table>

<div class="callout-warn">
<b>⚠️ 科学严密性提醒 (大地水准面差值改正与双水准面体系)：</b><br>
CNES-CLS22 MDT 的参考重力场在大洋为 <b>GOCO06s (d/o=800)</b>，而在地中海与黑海为 <b>EIGEN-6C4 (d/o=2190)</b>，绝非 EGM2008！全球范围内水准面差距（ΔN）在 -6.6m ~ +6.8m 之间。<br>
CoastTideX v1.4 优先读取官方 <code>hybrid_mdt_source_mask.tif</code> 掩膜，辅以闭合矢量多边形判定，杜绝加的斯湾、直布罗陀海峡西口、比斯开湾与红海被误判。<br>
在 EIGEN-6C4 区域，<code>h_goco06s_m</code> 字段严格赋予 NaN（不冒充），系统内置 <code>data/geoid/delta_n_eigen6c4_minus_egm2008.tif</code> 差值文件支持无缝高精度换算至 EGM2008，并给出质量提示 <code>QC_MED_BLACK_SEA_EIGEN6C4</code>。
</div>

<h2>二、 时区规范与长时序/整年高分辨率预测</h2>
<p>
PyFES 天文潮汐引潮力计算严格基于 <b>UTC (协调世界时)</b>。
</p>
<ul>
    <li><b>UTC 模式 (推荐)</b>：直接输入 UTC 时间，计算结果的相位严格与格林威治天文历元对齐。</li>
    <li><b>本地时区模式 (Local Time)</b>：系统根据您当前电脑系统时区，自动换算为 UTC 时间送入模型，彻底杜绝 8 小时潮汐相位颠倒错误。</li>
    <li><b>整年高分辨率预测模式 (v1.3/v1.4)</b>：支持快捷整年预测（例如选择 2024 年，系统自动按半开区间 <code>[2024-01-01 00:00, 2025-01-01 00:00)</code> 生成严密时网，以 30min 采样率计算，闰年 366 天严格生成 <b>17,568</b> 个连续无缝样本点）。切换整年时系统自动推荐 30min 采样率。</li>
    <li><b>动态时间分块机制 (Time-Chunking)</b>：底层解算核心引入 5,000 点动态分块流式迭代，单点无论是 1 年、多年还是高密度（5min/6min/10min）预测，均逐块解算并实时更新进度，彻底避免系统卡顿与未知崩溃。</li>
    <li><b>夏令时 (DST) 物理连续性</b>：采用严格递增时间戳构建器，穿越夏令时切换日时保持物理流逝时间无间断递增与点数正确性。</li>
</ul>

<h2>三、 空间栅格潮位与潜在天文潮淹没频率分析 (v1.4 Spatial Raster Engine)</h2>
<p>
CoastTideX v1.4 正式引入工业级空间栅格潮位引擎 (<code>RasterTideEngine</code>)，支持对任意带有标准地理参考 (CRS) 的 GeoTIFF 影像进行二维高保真解算：
</p>
<ul>
    <li><b>指定时刻瞬时栅格潮位快照 (Snapshot)</b>：
        采用像元中心严格重投影 (<code>xy(..., offset='center')</code>)，512×512 内存安全窗口流式解算，计算整景影像范围内真实空间变化的潮位/水面高程，严格继承输入栅格的投影与分辨率输出 GeoTIFF。
    </li>
    <li><b>整年/指定时段潜在天文潮淹没频率 (Inundation Frequency)</b>：
        针对千万像元级的 10m/30m 高分辨率沿海 DEM，系统创新采用<b>自适应潮位控制网格 (Adaptive Tide Control Grid)</b>：
        <ol>
            <li>在 DEM 覆盖范围内以指定间距（默认 4,000 米）自适应提取水域/潮滩控制节点；</li>
            <li>在控制网格上批量计算完整的年际时间序列；</li>
            <li>像元计算时采用反距离权重 (IDW) 空间插值，随后通过严密互补累积分布 (CCDF) 计算潜在天文潮淹没频率（0% ~ 100%）；</li>
            <li>输出严格标明为「潜在天文潮淹没频率 (Potential Astronomical Tidal Inundation Frequency)」，并在 TIFF 标签中完整记录计算溯源。</li>
        </ol>
    </li>
    <li><b>阻隔与内陆物理保护</b>：严禁盲目向闭流洼地、被水工建筑物隔断的水塘进行潮汐外插；陆地无效像元保持 NoData 传播。</li>
</ul>

<h2>四、 💻 电脑硬件配置与内存需求 (System Requirements)</h2>
<table>
    <tr><th>工作模式</th><th>最低硬件建议</th><th>详细说明</th></tr>
    <tr><td><b>单点 / 局域连续时序模式</b></td><td><b>最低 4 GB RAM<br>(推荐 8 GB)</b></td><td>系统采用<b>自适应局部包围框 (BBox)</b> 动态裁剪技术，单点预测仅在内存中构建目标点周围 ±1° 的局部有限元网格拓扑。解算运行时常驻内存约 <b>1.2 GB</b>。</td></tr>
    <tr><td><b>整年高分辨率预测 (17,568点)</b></td><td><b>推荐 8 GB RAM</b></td><td>动态时间分块与 GUI 表格 <b>2,000 行极速预览</b> 双重防护，全量数据导出至 CSV/Excel，内存稳定，界面丝滑无卡顿。</td></tr>
    <tr><td><b>全球散点批量解算模式</b></td><td><b>推荐 8 GB RAM<br>或以上</b></td><td>采用<b>自适应空间分块聚类逐块解算 (Spatial Chunking)</b>，对空间上邻近的点集聚类成 5°×5° 窗口分批处理，内存占用平稳可控。</td></tr>
    <tr><td><b>空间栅格潮位与淹没解算 (v1.4)</b></td><td><b>推荐 8 ~ 16 GB RAM</b></td><td>采用 512×512 窗口流式写入与自适应控制网格，内存消耗与整景影像尺寸解耦，支持超大范围 10m DEM。</td></tr>
</table>

<h2>五、 界面操作与主要功能说明</h2>
<h3>1. 单点/时段潮位时序模拟</h3>
<ol>
    <li>在左侧面板选择“预设站点”或输入经纬度；</li>
    <li>选择“时间模式”：<b>自定义时段</b>或<b>整年快捷模式</b>（可指定年份如 2024）；</li>
    <li>选择采样间隔（5分/6分/10分/15分/30分/1小时/2小时），界面实时提示预期样本点数；</li>
    <li>选择计算基准面与展示基准面（支持 MSL, MDT_REF, EGM2008, WGS84）；</li>
    <li>点击<b>「开始潮位模拟计算」</b>；</li>
    <li>右侧交互式波形图自动渲染，长时序下智能标记极值高低潮点；</li>
    <li>表格提供前 2,000 行快速检视，下方按钮可一键将全量数据导出为标准 CSV 或 Excel。</li>
</ol>

<h3>2. 批量站点多时刻解算</h3>
<ol>
    <li>切换至“批量站点多时刻解算”选项卡；</li>
    <li>导入包含经度、纬度、时间的 CSV 表格，映射字段；</li>
    <li>点击<b>「开始批量解算」</b>，支持自动标记地中海/黑海 EIGEN-6C4 质检警示；</li>
    <li>解算完成后全量导出包含各大基准面的结果报表。</li>
</ol>

<h3>3. 空间栅格潮位与淹没分析 (v1.4 新增)</h3>
<ol>
    <li>切换至“空间栅格潮位与淹没分析”选项卡；</li>
    <li>导入待计算的 GeoTIFF 影像（自动解析 CRS、像元大小、范围与波段）；</li>
    <li>选择计算任务：<b>单时刻空间水面高程快照</b> 或 <b>DEM 时段/整年潜在天文潮淹没频率</b>；</li>
    <li>配置目标基准面、时间/年份、采样率与控制网格间距；</li>
    <li>点击<b>「开始栅格解算」</b>，支持实时进度条显示与中途安全取消；</li>
    <li>解算完成后弹出结果摘要卡片，包含有效像元数、极值统计与耗时统计。</li>
</ol>

<h2>六、 版本重要更新日志 (Changelog)</h2>
<h3>v1.4 (2026-09)</h3>
<ul>
    <li><b>[新增] 空间栅格潮位引擎 (RasterTideEngine)</b>：支持输入 GeoTIFF 影像，在指定时刻进行真空间变化的水面高程快照 (Snapshot) 计算，严格遵循像元中心定位并流式写入输出 GeoTIFF；</li>
    <li><b>[新增] DEM 潜在天文潮淹没频率栅格分析</b>：基于自适应潮位控制网格 (Adaptive Tide Control Grid) 与 CCDF 算法，支持 10m/30m DEM 的高分辨率年际潜在天文潮淹没频率（0% ~ 100%）流式解算；</li>
    <li><b>[优化] 科学基准严格广播与目标解耦</b>：修正非标量输入严格维度校验，解耦 EGM2008 与 WGS84 依赖；优先读取权威官方 <code>hybrid_mdt_source_mask.tif</code>，辅以闭合多边形保底；</li>
    <li><b>[优化] 设置面板深层校验与配置同步</b>：数据源配置增加 NetCDF/GeoTIFF 深度有效性校验与重置默认键名统一；</li>
    <li><b>[新增] GUI 栅格潮位专用面板 (Tab 3)</b>：提供 GeoTIFF 元数据检视卡、快照/淹没双模式面板、自适应网格参数配置、进度条与中途取消支持；</li>
    <li><b>[优化] 独立计算基准与展示基准</b>：GUI 单点计算区分计算基准与展示基准，切换整年时自动推荐 30min 步长并具备用户修改记忆；</li>
    <li><b>[CLI] 命令行全量扩展</b>：CLI 新增 <code>raster snapshot</code> 与 <code>raster inundation</code> 子命令；<code>scripts/calculate_inundation_raster.py</code> 升级为规范薄封装；</li>
    <li><b>[测试] 单元测试套件扩展至 29 项全通过</b>：覆盖合成 GeoTIFF 元数据提取、像元中心对齐、快照 Mock、CCDF 预言机、阻隔水体不外插与全链路集成。</li>
</ul>

<h3>v1.3 (2026-09)</h3>
<ul>
    <li><b>[重构] 混合 MDT 双大地水准面基准区分</b>：引入高精度矢量多边形判定，大洋严格对应 GOCO06s，地中海与黑海严格对应 EIGEN-6C4，内置 <code>delta_n_eigen6c4_minus_egm2008.tif</code> 差值栅格；</li>
    <li><b>[语义] 标准基准字段重命名</b>：主推 <code>h_mdt_ref_m</code>，地中海区域 <code>h_goco06s_m</code> 严格置为 NaN，杜绝张冠李戴；</li>
    <li><b>[新增] 整年高分辨率预测模式</b>：支持指定年份半开区间 <code>[start, end)</code> 模拟，2024 闰年 30min 严格输出 17,568 样本点；</li>
    <li><b>[核心] 动态时间分块 (Time-Chunking)</b>：底层潮位解算引入 5,000 点动态分块流式处理与逐块进度汇报，保障年际与高密度时序计算平稳；</li>
    <li><b>[算法] 潜在天文潮淹没频率分析</b>：集成高效 ECDF/CCDF 向量化计算函数，并提供 <code>scripts/calculate_inundation_raster.py</code> 潮滩 DEM 淹没频率栅格工具；</li>
    <li><b>[体验] 表格极速预览与全量导出解耦</b>：GUI 表格限制展示前 2,000 行，导出功能 100% 完整保留；</li>
    <li><b>[泛化] Delta N 栅格生成工具强化</b>：泛化支持任意参考与目标大地水准面差值计算，增加空间一致性校验与有效格网点检查。</li>
</ul>

<h2>七、 科学引用与致谢</h2>
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
