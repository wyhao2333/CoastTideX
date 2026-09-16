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

<h1>📖 CoastTideX 用户操作手册与科学原理文档 (v1.6)</h1>

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
CNES-CLS22 MDT 的参考重力场在大洋为 <b>GOCO06s (d/o=300)</b>，而在地中海与黑海为 <b>EIGEN-6C4 (d/o=2190)</b>，绝非 EGM2008！全球范围内水准面差距（ΔN）在 -6.6m ~ +6.8m 之间。<br>
CoastTideX 可配置外部 <code>hybrid_mdt_source_mask.tif</code> 来源掩膜（若未配置则平滑回退至精细闭合矢量多边形判别，并显式标记 <code>QC_DATUM_SOURCE_APPROX</code> 质量预警），杜绝加的斯湾、直布罗陀海峡西口、比斯开湾与红海被误判。<br>
在 EIGEN-6C4 区域，<code>h_goco06s_m</code> 字段严格赋予 NaN（坚决不冒充 GOCO06s）；若需换算至 EGM2008/WGS84，可配置外部权威 <code>data/geoid/delta_n_eigen6c4_minus_egm2008.tif</code> 差值文件（支持通过 <code>scripts/generate_delta_n.py</code> 本地生成）；未配置该外部差值文件时，严格模式 (strict=True) 明确抛出异常，非严格模式返回 NaN。
</div>

<h3>3. 关键数据来源与双掩膜科学边界澄清 (权威区分两类掩膜)</h3>
<p>系统涉及两个极易混淆但本质截然不同的掩膜文件，其物理语义与在系统中的作用具有严格边界：</p>
<table>
    <tr><th>掩膜类型</th><th>文件格式与典型路径</th><th>内部编码与含义</th><th>科学功能与作用边界</th></tr>
    <tr>
        <td><b>Hybrid MDT 来源掩膜<br>(Geoid Source Mask)</b></td>
        <td>GeoTIFF 栅格<br><code>data/geoid/hybrid_mdt_source_mask.tif</code><br>(可选外部配置，当前未内置)</td>
        <td><code>0: UNKNOWN</code><br><code>1: GOCO06s (全球大洋)</code><br><code>2: EIGEN-6C4 (地中海)</code><br><code>3: EIGEN-6C4 (黑海)</code><br><code>255: NoData</code></td>
        <td><b>唯一用于基准判别</b>：决定 CNES-CLS22 MDT 在给定位置采用 GOCO06s 还是 EIGEN-6C4 作为参考水准面，直接关联 ΔN 差值改正。<br><b>未配置时</b>：系统自动采用几何多边形 Fallback 并标记 <code>QC_DATUM_SOURCE_APPROX</code>。<br><b>注意</b>：绝非 FES 潮位外推掩膜！</td>
    </tr>
    <tr>
        <td><b>FES2022b 潮位外推掩膜<br>(Tide Extrapolation Mask)</b></td>
        <td>NetCDF 文件<br><code>fes2022b/mask_fes2022B.nc</code><br>(1/30° 规则网格外部参考)</td>
        <td><code>0: Native Ocean (原生海洋)</code><br><code>1: Extrapolated Tide (外推潮位)</code><br><code>2: Land (陆地)</code><br><code>3: Lake (湖泊)</code></td>
        <td><b>仅用于描述 1/30° 规则网格来源</b>：标明 FES2022b 规则经纬度网格的插值溯源与陆地边界。<br><b>边界澄清</b>：<b>既不是 MDT 掩膜，亦不参与大地水准面基准选择</b>。当前 CoastTideX Native LGP2 主解算流程不使用此文件参与计算，外推回退处于禁用状态。</td>
    </tr>
</table>

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

<h2>三、 空间栅格潮位与潜在天文潮淹没频率分析 (v1.4 Spatial Raster Engine, RC)</h2>
<p>
CoastTideX v1.4 正式引入空间栅格潮位引擎验证版本 (<code>RasterTideEngine</code>，Release Candidate)，支持对任意带有标准地理参考 (CRS) 的 GeoTIFF 影像进行二维高保真解算：
</p>
<ul>
    <li><b>指定时刻瞬时栅格潮位快照 (Snapshot)</b>：
        采用像元中心严格重投影 (<code>xy(..., offset='center')</code>)，512×512 内存安全窗口流式解算，计算整景影像范围内真实空间变化的潮位/水面高程，严格继承输入栅格的投影与分辨率输出 GeoTIFF。
    </li>
    <li><b>整年/指定时段潜在天文潮淹没频率 (Inundation Frequency)</b>：
        针对千万像元级的 10m/30m 高分辨率沿海 DEM，系统创新采用<b>自适应潮位控制网格 (Adaptive Tide Control Grid)</b>：
        <ol>
            <li>在 DEM 覆盖范围内以指定间距（默认 4,000 米）自适应提取水域/潮滩控制节点；</li>
            <li>在控制网格上批量流式计算完整的年际时间序列；</li>
            <li>控制节点就地预排序水位时序，像元高程通过二分检索 (<code>np.searchsorted</code>) 快速获取控制节点淹没概率，在四叉树叶单元内部采用双线性空间平滑插值解算潜在天文潮淹没频率（0% ~ 100%）；</li>
            <li>输出严格标明为「潜在天文潮淹没频率 (Potential Astronomical Tidal Inundation Frequency)」，并在 TIFF 标签中完整记录计算溯源与常驻内存指标。</li>
        </ol>
    </li>
    <li><b>有效像元拓扑连通防护 (Valid-mask Topology-aware Guard)</b>：基于输入 DEM 的有效像元/NoData 连通域阻断跨越 NoData 屏障的潮位泄漏（注：依赖 DEM NoData 拓扑结构，非二维浅水方程水动力学模拟；堤坝若有 DEM 赋值则不自动视为隔离屏障）；陆地无效像元保持 NoData 传播并生成 UInt16 质量位掩膜。</li>
</ul>


<h2>四、 批量潮间带栅格解算与持久化 Tide Cache (v1.6 新增)</h2>
<p>
针对狭长沙滩、沿海潮滩与潮间带的高分辨率 (10m/30m) DEM 批量处理需求，CoastTideX v1.6 引入了<b>批量潮间带栅格引擎 (BatchRasterEngine)</b> 与<b>持久化 NetCDF Tide Cache</b>：
</p>
<ul>
    <li><b>高分辨率地形与平缓潮位场解耦机制</b>：
        全球开阔水域 FES 天文潮位通常在公里级尺度上平缓变化（~4km），而沿海潮滩沙滩高程在 10m/30m 像元尺度上急剧起伏。系统在空间自适应四叉树宏观控制网格（4km 初始间距，沿强梯度处细分至 500m）上批量解算 FES 潮位时序，并在像元尺度上逐像元通过二分查找 (<code>np.searchsorted</code>) 对比 DEM 高程与已排序水位时序，既杜绝了盲目加密至 10m 的天文数字级计算崩溃，又严密保真了 10m DEM 的细微地形起伏与淹没边界。
    </li>
    <li><b>严格二阶段执行 (Strict Two-Stage Execution)</b>：
        <ol>
            <li><b>Stage 1: 控制网格 FES 解算与持久化 Tide Cache 生成</b>：构建四叉树控制网格并批量解算各控制节点的 FES 潮位时序，原子写入 NetCDF 格式的 <code>*_tide.nc</code> 缓存（包含节点坐标、原始与 MSL 潮位时序、基准静态偏移、单元拓扑与 <code>CACHE_COMPLETE</code> 完整性标记）；</li>
            <li><b>Stage 2: 基于 Tide Cache 解算潜在天文潮淹没频率</b>：流式逐分块读取 DEM 高程与 Tide Cache，双线性空间平滑插值解算潜在天文潮淹没频率 (<code>*_inundation.tif</code>) 与质量位掩膜 (<code>*_inundation_qc.tif</code>)。本阶段<b>零 FES 调用</b>，速度提升数倍至数十倍。</li>
        </ol>
    </li>
    <li><b>文件夹级轻量扫描与排重过滤</b>：
        仅读取 GeoTIFF 头文件元数据，绝不扫描全像元；自动排除输出目录文件、衍生文件 (<code>*_inundation.tif</code>, <code>*_qc.tif</code>, <code>*_tide.nc</code>) 与临时文件，按字母字典序确定性排序。GUI 后台工作线程扫描，若用户在中途修改目录或递归设置，系统立即判定快照失效并提示重新扫描，杜绝竞态脏数据。
    </li>
    <li><b>三种现有输出策略与严密断点恢复 (Existing Output Policies & Resume Hardening)</b>：
        <ul>
            <li><code>error_if_exists</code>: 若目标产物存在则严格拦截报错，防止意外覆盖；</li>
            <li><code>overwrite</code>: 强制清除旧文件并全新重算；</li>
            <li><code>resume</code>: 严密断点恢复。深度校验已有 Tide Cache 的参数集签名 (<code>signature</code>) 与兼容性；若缓存属于不同年份或参数，系统拒绝静默跳过并报错阻断；同时对已有 <code>*_inundation.tif</code> 和 <code>*_qc.tif</code> 严格核验尺寸、CRS、Transform 仿射变换矩阵、数据类型 (Float32 / UInt16) 及缓存签名，确认无损后方执行 <code>SKIPPED_EXISTING</code> 跳过。</li>
        </ul>
    </li>
    <li><b>DEM NoData 科学保护</b>：
        若输入 DEM 的 NoData 值恰好落入潜在天文潮淹没频率的物理有效区间 <code>[0.0, 100.0]</code>%（例如 0 或 100），系统自动回退输出 NoData 为 <code>NaN</code>，彻底杜绝 0% 淹没或 100% 淹没正常像元被误当做 NoData 的严重冲突。
    </li>
    <li><b>单瓦片失败隔离 (Failure Isolation)</b>：
        批量运行中单个瓦片若遇到损坏、非法投影或读取异常，系统自动捕获并在 <code>batch_manifest.json</code> 与 CSV 清单中标记 <code>FAILED</code>，严密隔离故障并立即继续执行后续瓦片，杜绝整批任务因单个异常文件半途废弃。
    </li>
    <li><b>FES2022b 本地数据包与近岸外推边界</b>：
        经本地完整数据包审查 (<code>docs/FES2022B_LOCAL_AUDIT_V1_5.md</code>)，FES2022b 外推分潮数据为压缩 <code>.nc.xz</code> 格式，且掩膜具有四分类物理含义。v1.6 阶段近岸外推回退机制保持<b>禁用与未集成</b>状态，原生 FES 具备完整的有效控制网格拓扑支撑。
    </li>
</ul>

<h2>五、 💻 电脑硬件配置与内存需求 (System Requirements)</h2>
<table>
    <tr><th>工作模式</th><th>最低硬件建议</th><th>详细说明</th></tr>
    <tr><td><b>单点 / 局域连续时序模式</b></td><td><b>最低 4 GB RAM<br>(推荐 8 GB)</b></td><td>系统采用<b>自适应局部包围框 (BBox)</b> 动态裁剪技术，单点预测仅在内存中构建目标点周围 ±1° 的局部有限元网格拓扑。解算运行时常驻内存约 <b>1.2 GB</b>。</td></tr>
    <tr><td><b>整年高分辨率预测 (17,568点)</b></td><td><b>推荐 8 GB RAM</b></td><td>动态时间分块与 GUI 表格 <b>2,000 行极速预览</b> 双重防护，全量数据导出至 CSV/Excel，内存稳定，界面丝滑无卡顿。</td></tr>
    <tr><td><b>全球散点批量解算模式</b></td><td><b>推荐 8 GB RAM<br>或以上</b></td><td>采用<b>自适应空间分块聚类逐块解算 (Spatial Chunking)</b>，对空间上邻近的点集聚类成 5°×5° 窗口分批处理，内存占用平稳可控。</td></tr>
    <tr><td><b>空间栅格潮位与淹没解算 (v1.4)</b></td><td><b>推荐 8 ~ 16 GB RAM</b></td><td>采用 512×512 窗口流式写入与自适应控制网格，内存消耗与整景影像尺寸解耦，支持超大范围 10m DEM。</td></tr>
</table>

<h2>六、 界面操作与主要功能说明</h2>
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

<h3>3. 单影像空间栅格潮位与淹没分析 (v1.4)</h3>
<ol>
    <li>切换至“空间栅格潮位与淹没分析”选项卡；</li>
    <li>导入待计算的 GeoTIFF 影像（自动解析 CRS、像元大小、范围与波段）；</li>
    <li>选择计算任务：<b>单时刻空间水面高程快照</b> 或 <b>DEM 时段/整年潜在天文潮淹没频率</b>；</li>
    <li>配置目标基准面、时间/年份、采样率与控制网格间距；</li>
    <li>点击<b>「开始栅格解算」</b>，支持实时进度条显示与中途安全取消；</li>
    <li>解算完成后弹出结果摘要卡片，包含有效像元数、极值统计与耗时统计。</li>
</ol>

<h3>4. 批量潮间带栅格解算 (v1.6 新增)</h3>
<ol>
    <li>切换至“批量潮间带栅格解算”选项卡；</li>
    <li>选择包含待解算沙滩/潮滩 DEM 的输入文件夹（系统自动快速扫描并展示文件列表）；</li>
    <li>选择任务模式：<b>Tide + Inundation (推荐完整流程)</b>、<b>Tide Only (仅生成 Tide Cache)</b> 或 <b>Inundation from Cache (仅由 Cache 解算频率)</b>；</li>
    <li>配置年份或自定义时段、采样步长、目标高程基准面与自适应控制网格参数；</li>
    <li>勾选“断点恢复”以跳过已完成瓦片，点击<b>「开始批量解算」</b>；</li>
    <li>界面双进度条展示总体批处理进度与当前文件细粒度进度，下方表格实时同步各瓦片状态；</li>
    <li>支持随时安全取消，已完成瓦片与当前未受损缓存完全保留。</li>
</ol>


<h2>七、 版本重要更新日志 (Changelog)</h2>
<h3>v1.6 (2026-09)</h3>
<ul>
    <li><b>[新增] 批量潮间带栅格解算引擎 (BatchRasterEngine)</b>：支持文件夹级全自动化扫描、过滤与确定性排序，以单瓦片顺序推进 (max_parallel_tiles = 1) 模式执行高分辨率沙滩/潮滩 DEM 批量解算；</li>
    <li><b>[新增] 持久化 NetCDF4 Tide Cache (*_tide.nc)</b>：实现 Stage 1 (Tide Cache) 与 Stage 2 (Inundation Frequency) 严格二阶段分离，Stage 2 解算硬性保证零 FES 调用；</li>
    <li><b>[新增] 单瓦片失败隔离与断点恢复清单</b>：内置 <code>batch_manifest.json</code> 与 CSV 清单，单影像异常不影响后续处理，断点恢复模式下已完成瓦片自动跳过、TIDE_READY 瓦片直接计算频率；</li>
    <li><b>[算法] 潮间带目标感知自适应细分优化</b>：在 <code>target_mode="intertidal"</code> 下以目标高程频率误差为核心指标，避免单一海陆交界触发无效的 500m 过度细分，显著节约近岸节点；</li>
    <li><b>[审查] 本地完整 FES2022b 数据包只读审计</b>：完成本地完整数据包审计并沉淀 <code>docs/FES2022B_LOCAL_AUDIT_V1_5.md</code>，明确禁用未就绪的 XZ 压缩外推回退；</li>
    <li><b>[GUI/CLI] 批量选项卡与命令行接口扩充</b>：新增批量潮间带专属 Tab 与 <code>raster batch</code> / <code>raster batch-intertidal</code> 命令行工具；</li>
    <li><b>[测试] 单元测试套件扩充至 61 项全通过</b>：新增 11 项涵盖轻量扫描、整年时间采样保真、Tide Cache 往返、Stage 2 零 FES 硬性验收、空间属性继承、狭长沙滩梯度细分、单瓦片失败隔离、断点恢复状态机与接缝连续性评估测试。</li>
</ul>

<h3>v1.4 (2026-09)</h3>
<ul>
    <li><b>[新增] 空间栅格潮位引擎 (RasterTideEngine)</b>：支持输入 GeoTIFF 影像，在指定时刻进行真空间变化的水面高程快照 (Snapshot) 计算，严格遵循像元中心定位并流式写入输出 GeoTIFF；</li>
    <li><b>[新增] DEM 潜在天文潮淹没频率栅格分析</b>：基于自适应潮位控制网格 (Adaptive Tide Control Grid) 与 CCDF 算法，支持 10m/30m DEM 的高分辨率年际潜在天文潮淹没频率（0% ~ 100%）流式解算；</li>
    <li><b>[优化] 科学基准严格广播与目标解耦</b>：修正非标量输入严格维度校验，解耦 EGM2008 与 WGS84 依赖；优先读取权威官方 <code>hybrid_mdt_source_mask.tif</code>，辅以闭合多边形保底；</li>
    <li><b>[优化] 设置面板深层校验与配置同步</b>：数据源配置增加 NetCDF/GeoTIFF 深度有效性校验与重置默认键名统一；</li>
    <li><b>[新增] GUI 栅格潮位专用面板 (Tab 3)</b>：提供 GeoTIFF 元数据检视卡、快照/淹没双模式面板、自适应网格参数配置、进度条与中途取消支持；</li>
    <li><b>[优化] 独立计算基准与展示基准</b>：GUI 单点计算区分计算基准与展示基准，切换整年时自动推荐 30min 步长并具备用户修改记忆；</li>
    <li><b>[CLI] 命令行全量扩展</b>：CLI 新增 <code>raster snapshot</code> 与 <code>raster inundation</code> 子命令；<code>scripts/calculate_inundation_raster.py</code> 升级为规范薄封装；</li>
    <li><b>[测试] 单元测试套件扩展至 47 项全通过</b>：覆盖自适应四叉树动态细分、最小步长终止、拓扑连通防护、官方掩膜五类规范、投影重投影、经度圆周跨界、像元中心对齐、快照与 CCDF 离线预言机及全链路集成。</li>
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

<h2>八、 潮滩/沙滩潜在天文潮露出时间域分析 (v1.6 Exposure Engine)</h2>
<p>
CoastTideX v1.6 全新引入了面向海岸带潮滩、沙滩生态与遥感潮汐校正的时间域连续分析引擎。
</p>
<div class="callout-warn">
<b>科学严谨性声明 / Terminology Boundary:</b><br>
本产品严格命名为<b>“固定代表性地形条件下的潜在天文潮露出时长” (Potential Astronomical Tidal Exposure Duration under a Fixed Representative Terrain)</b>。<br>
本引擎基于代表性地形高程 z 与纯天文潮位序列 H(t) 进行严密几何跨界求交与事件积分。
<b>严禁混淆为“沙滩干燥时长 (Beach Drying Time)”或“二维浅水动力学退水过程 (2D Hydrodynamic Flooding/Drying)”</b>，因为实际沙滩干燥与退水滞后受沉积物孔隙渗流、波浪爬高增水、地下水位与气象风暴潮多物理场耦合制约。
</div>

<h3>1. 科学状态判定与严格边界条件</h3>
<ul>
    <li><b>淹没状态 (Inundated):</b> <code>H(t) > z</code></li>
    <li><b>露出状态 (Exposed):</b> <code>H(t) &le; z</code></li>
    <li><b>等高边界归属:</b> 当瞬时潮位恰好等于地形高程 (<code>H(t) == z</code>) 时，严格归属于<b>露出状态 (Exposed)</b>，杜绝边界歧义。</li>
</ul>

<h3>2. 7 大独立空间栅格产品体系</h3>
<table>
    <tr><th>产品后缀</th><th>数据类型</th><th>物理单位</th><th>科学含义</th></tr>
    <tr><td><code>*_exposure_fraction.tif</code></td><td>Float32</td><td>%</td><td>累计露出时间比例 (相对时段内有效时间)</td></tr>
    <tr><td><code>*_exposure_duration_h.tif</code></td><td>Float32</td><td>hours</td><td>累计有效潜在露出时长 (小时)</td></tr>
    <tr><td><code>*_exposure_max_continuous_h.tif</code></td><td>Float32</td><td>hours</td><td>单次最长连续潜在露出时长 (小时)</td></tr>
    <tr><td><code>*_exposure_mean_event_h.tif</code></td><td>Float32</td><td>hours</td><td>平均单次露出事件时长 (小时)</td></tr>
    <tr><td><code>*_exposure_event_count.tif</code></td><td>UInt32</td><td>次 (count)</td><td>露出事件完整发生频次</td></tr>
    <tr><td><code>*_exposure_valid_time_fraction.tif</code></td><td>Float32</td><td>%</td><td>有效时序数据覆盖比例 (%)</td></tr>
    <tr><td><code>*_exposure_qc.tif</code></td><td>UInt16</td><td>bitmask</td><td>露出分析质量控制位掩膜 (0=最优)</td></tr>
</table>

<h3>3. 时间采样语义统一与半开区间 [start, end)</h3>
<p>
CoastTideX 全系统科学产品全面统一时间采样语义为严格<b>半开区间 <code>[start, end)</code> (inclusive="left")</b>。
在整年预测 (如 2024-01-01 至 2025-01-01) 下，步长 30min 严格生成 17,568 个等权重样本点，消除跨年重复统计。
同时，Tide Cache 升级至 <b>Schema 1.2</b>，引入终端采样 <code>tide_msl_terminal_m</code> (对应 <code>t_end</code>)，支撑末端区间的无截断高精度线性跨界插值。
</p>

<h2>九、 外部科学数据依赖关系指引</h2>
<table>
    <tr><th>数据名称</th><th>相对路径</th><th>存储属性</th><th>用途说明</th></tr>
    <tr><td><b>EGM2008 2.5' 大地水准面</b></td><td><code>data/geoid/us_nga_egm08_25.tif</code></td><td><span style="color:#10b981;">仓储自带 (Bundled)</span></td><td>全球 WGS84 椭球高与 EGM2008 正高严密转换网格</td></tr>
    <tr><td><b>ΔN (GOCO06s - EGM2008)</b></td><td><code>data/geoid/delta_n_goco06s_minus_egm2008.tif</code></td><td><span style="color:#10b981;">仓储自带 (Bundled)</span></td><td>全球大洋大尺度重力场水准面改正栅格</td></tr>
    <tr><td><b>FES2022b 非结构网格</b></td><td><code>fes2022b/ocean_tide_non_structured/...</code></td><td><span style="color:#f59e0b;">外部必须 (Mandatory)</span></td><td>LGP2 原生非结构网格，提供最高精度潮位调和常数</td></tr>
    <tr><td><b>CNES-CLS22 MDT</b></td><td><code>mdt_cls22/...</code></td><td><span style="color:#f59e0b;">外部必须 (Mandatory)</span></td><td>全球平均动态地形，连接 MSL 与大地水准面基准</td></tr>
    <tr><td><b>ΔN (EIGEN-6C4 - EGM2008)</b></td><td><code>data/geoid/delta_n_eigen6c4_minus_egm2008.tif</code></td><td><span style="color:#38bdf8;">预处理重现 (Ignored)</span></td><td>地中海与黑海专用改正，可由预处理脚本自动生成</td></tr>
    <tr><td><b>Hybrid MDT 来源掩膜</b></td><td><code>config.yaml -> paths.hybrid_mdt_source_mask</code></td><td><span style="color:#94a3b8;">外部可选 (Optional)</span></td><td>留空时系统启用多边形地理边界自动判定并输出 QC 预警</td></tr>
</table>

<h2>十、 科学引用与致谢</h2>
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
