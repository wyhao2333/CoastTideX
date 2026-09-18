"""
CoastTideX 功能说明文档与操作手册对话框 (User Manual Dialog v1.6 Beta)
为用户提供系统级科学原理、高程基准定义、操作指引、时区规范、露出时间域分析与内存配置说明。
"""

from PyQt6.QtWidgets import QDialog, QVBoxLayout, QTextBrowser, QPushButton, QHBoxLayout
from PyQt6.QtCore import Qt


MANUAL_HTML = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
    body {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
        color: #e2e8f0;
        background-color: #1a1d24;
        line-height: 1.6;
        padding: 14px 18px;
    }
    h1 { color: #38bdf8; border-bottom: 2px solid #0284c7; padding-bottom: 8px; font-size: 22px; margin-top: 5px; }
    h2 { color: #60a5fa; margin-top: 24px; font-size: 16px; border-bottom: 1px solid #334155; padding-bottom: 5px; }
    h3 { color: #f59e0b; font-size: 13.5px; margin-top: 14px; margin-bottom: 6px; }
    p, li { font-size: 12.5px; color: #cbd5e1; }
    ul, ol { padding-left: 20px; margin-top: 4px; margin-bottom: 8px; }
    li { margin-bottom: 4px; }
    code { background-color: #0f172a; color: #38bdf8; padding: 2px 5px; border-radius: 4px; font-family: Consolas, Monaco, monospace; font-size: 12px; }
    pre { background-color: #0f172a; border: 1px solid #334155; padding: 10px; border-radius: 6px; color: #f8fafc; font-family: Consolas, Monaco, monospace; font-size: 12px; overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; margin: 10px 0; font-size: 12px; }
    th, td { border: 1px solid #334155; padding: 7px 10px; text-align: left; vertical-align: top; }
    th { background-color: #1e293b; color: #38bdf8; font-weight: 600; }
    tr:nth-child(even) { background-color: #141820; }
    .callout-info { background-color: #172554; border-left: 4px solid #38bdf8; padding: 10px 14px; border-radius: 4px; margin: 12px 0; font-size: 12.5px; }
    .callout-warn { background-color: #451a03; border-left: 4px solid #f59e0b; padding: 10px 14px; border-radius: 4px; margin: 12px 0; font-size: 12.5px; }
    .callout-success { background-color: #064e3b; border-left: 4px solid #10b981; padding: 10px 14px; border-radius: 4px; margin: 12px 0; font-size: 12.5px; }
</style>
</head>
<body>

<h1>📖 CoastTideX 用户操作手册与科学原理文档 (v1.6 Beta)</h1>

<div class="callout-info">
<b>CoastTideX (全球潮汐与高程基准空间模拟系统)</b> 是专为海岸带环境遥感、海洋工程、大地测量垂直基准统一与潮间带生态水文模拟研发的高精度桌面与命令行解算系统。
</div>

<h2>一、 系统定位与科学用途 (System Overview & Scientific Scope)</h2>
<p>CoastTideX v1.6 Beta 提供全链路、可追溯、高保真的潮汐动力学模拟与空间栅格产品反演能力，涵盖四大主要业务场景：</p>
<ul>
    <li><b>单点连续潮位模拟</b>：支持全球任意经纬度的自定义时段或整年连续模拟，精确至分/小时级步长，支持 34 个半日潮、日潮与长周期分潮。</li>
    <li><b>批量站点时序解算</b>：面向沿海勘测点、浮标、验潮站，支持导入包含经纬度与时间戳的 CSV 表格，批量进行多基准面矢量化解算。</li>
    <li><b>单景影像空间栅格解算</b>：针对带有标准地理参考 (CRS) 的 GeoTIFF 影像，计算指定时刻空间水面高程快照 (Snapshot)、整年/时段潜在天文潮淹没频率 (Inundation Frequency) 及潜在天文潮露出时间域产品 (Exposure Duration)。</li>
    <li><b>文件夹级批量潮间带栅格解算</b>：面向千万像元级沿海 10m/30m 高分辨率 DEM，通过自适应四叉树控制网格、持久化 NetCDF Tide Cache 架构、断点恢复与任务清单实现大规模无人值守作业。</li>
</ul>

<h2>二、 核心科学定义与边界不变量 (Scientific Definitions & Boundary Invariants)</h2>
<p>本系统严格区分物理状态与边界分类准则，确保时间域统计与淹没分析的数学严密性：</p>

<h3>1. 状态判定准则</h3>
<ul>
    <li><b>淹没状态 (Inundated):</b> 当瞬时水面高程大于地形高程时，判定为淹没，即 <code>H(t) &gt; z</code>。</li>
    <li><b>露出状态 (Exposed):</b> 当瞬时水面高程小于或等于地形高程时，判定为露出，即 <code>H(t) &le; z</code>。</li>
    <li><b>严格等高边界不变量:</b> 当瞬时水面高程严格等于地形高程 (<code>H(t) == z</code>) 时，数学上<b>严格归属于露出状态 (Exposed)</b>，绝不归属于淹没状态 (Inundated)。</li>
</ul>

<div class="callout-warn">
<b>⚠️ 科学术语边界强澄清 (Terminology Boundary):</b><br>
本模块产物严格命名为<b>「固定代表性地形条件下的潜在天文潮露出时长」 (Potential Astronomical Tidal Exposure Duration under a Fixed Representative Terrain)</b>。<br>
<b>严禁混淆为“沙滩干燥时间 (Beach Drying Time)”或“实际水动力淹没/退水过程 (2D Hydrodynamic Flooding/Drying)”</b>。<br>
<b>原因与物理假设边界</b>：
<ol>
    <li>本系统基于固定的代表性 DEM 地形与纯天文潮位时序 <code>H(t)</code> 开展几何跨界积分；</li>
    <li>不包含真实气象风暴潮增减水、外海长周期涌浪与风浪爬高；</li>
    <li>不包含潮间带沉积物孔隙水渗透、地下水补给、蒸发干燥延迟、降雨汇流与三维泥沙冲淤动力学过程。</li>
</ol>
</div>

<h2>三、 潜在天文潮露出 7 大空间栅格产物体系 (Seven Exposure Raster Products)</h2>
<p>在潜在天文潮露出时间域反演模式下，系统一次性原子输出 7 大高保真空间栅格产品，完全继承输入 DEM 的坐标系、仿射变换、空间范围与分辨率：</p>

<table>
    <tr><th>产品文件名后缀</th><th>数据类型</th><th>物理单位</th><th>NoData 值</th><th>科学定义与应用说明</th></tr>
    <tr>
        <td><code>*_exposure_fraction.tif</code></td>
        <td>Float32</td>
        <td>%</td>
        <td>-9999.0</td>
        <td><b>累计潜在露出时间比例</b>：在请求时段有效计算时间内，像元处于露出状态 (H &le; z) 的时间占比 (0.0% ~ 100.0%)。</td>
    </tr>
    <tr>
        <td><code>*_exposure_duration_h.tif</code></td>
        <td>Float32</td>
        <td>hours</td>
        <td>-9999.0</td>
        <td><b>累计有效潜在露出时长</b>：像元在有效计算时间内的累计潜在露出绝对物理时长（小时）。</td>
    </tr>
    <tr>
        <td><code>*_exposure_max_continuous_h.tif</code></td>
        <td>Float32</td>
        <td>hours</td>
        <td>-9999.0</td>
        <td><b>最长单次连续潜在露出时长</b>：在请求时间窗口内识别出的最长单次无间断露出事件持续物理时长（小时）。反映极端耐干条件。</td>
    </tr>
    <tr>
        <td><code>*_exposure_mean_event_h.tif</code></td>
        <td>Float32</td>
        <td>hours</td>
        <td>-9999.0</td>
        <td><b>平均单次连续潜在露出时长</b>：有效露出事件的平均持续时长（小时）。若无露出事件则为 0.0 小时。</td>
    </tr>
    <tr>
        <td><code>*_exposure_event_count.tif</code></td>
        <td>UInt32</td>
        <td>次 (count)</td>
        <td>4294967295</td>
        <td><b>连续潜在露出事件段数量</b>：在请求时间窗口内完整识别到的独立连续露出事件段数量。NoData 采用 UInt32 最大值，与合法 0 次事件彻底解耦。</td>
    </tr>
    <tr>
        <td><code>*_exposure_valid_time_fraction.tif</code></td>
        <td>Float32</td>
        <td>%</td>
        <td>-9999.0</td>
        <td><b>有效时序数据覆盖比例</b>：有效计算时间占请求总窗口跨度的比例 (0.0% ~ 100.0%)。用于评估数据间隙或缺失。</td>
    </tr>
    <tr>
        <td><code>*_exposure_qc.tif</code></td>
        <td>UInt16</td>
        <td>bitmask</td>
        <td>65535</td>
        <td><b>露出分析质量控制位掩膜</b>：多位标记像元计算过程质量（0 为最优高保真状态，非 0 标明降级、基准近似或常时状态）。</td>
    </tr>
</table>

<h2>四、 连续事件段统计口径与时间窗口约束 (Event Segmentation & Window Constraints)</h2>
<p>为了科学捕捉潮汐涨落周期内的间歇性露出特征，系统实现了严格的一维连续事件状态机与跨界线性插值：</p>
<ul>
    <li><b>跨界线性插值 (Sub-timestep Crossing Interpolation)</b>：
        当相邻两采样时刻的水位 <code>[H(t_k), H(t_{k+1})]</code> 跨越地形高程 <code>z</code> 时，系统通过线性插值求解亚步长跨界精确时间比例：
        <pre>r = clip((z - H(t_k)) / (H(t_{k+1}) - H(t_k)), 0.0, 1.0)</pre>
        退潮露出片段时长为 <code>(1.0 - r) * Δt</code>，涨潮淹没前露出片段时长为 <code>r * Δt</code>。
    </li>
    <li><b>连续事件段界定</b>：一次连续露出事件定义为从水位降至 <code>z</code> 以下开始，至水位重新上涨超过 <code>z</code> 为止的连续时间段。</li>
    <li><b>请求时间窗口约束</b>：
        事件统计严格限定在请求时间窗口 <code>[t_start, t_end)</code> 内部。跨越窗口起始或结束边界的露出事件，仅统计其落在窗口内的物理时长；处于窗口外的部分不计入当前统计区间。
    </li>
</ul>

<h2>五、 时间采样语义与 Schema 1.2 终端采样 (Temporal Semantics & Schema 1.2)</h2>
<ul>
    <li><b>统一半开区间规范 <code>[start, end)</code></b>：
        CoastTideX 全系统统一采用半开区间（<code>inclusive="left"</code>）生成等间隔时网。在整年模拟中，例如 <code>[2024-01-01 00:00:00, 2025-01-01 00:00:00)</code>，30min 步长精确生成 <b>17,568</b> 个等权重样本点（闰年 366 天），完全消除跨年点重复统计的偏倚。
    </li>
    <li><b>Schema 1.2 终端采样保真</b>：
        为了在半开区间下正确解算最后一个时间区间 <code>[t_{N-1}, t_end)</code> 的连续跨界，Tide Cache 升级至 <b>Schema 1.2</b>，显式记录终端时刻 <code>t_end</code> 对应的控制节点潮位 <code>tide_msl_terminal_m</code>。
        系统在回读缓存时利用终端采样实现无截断的高保真线性插值，杜绝最后一个时步被虚假丢弃。
    </li>
</ul>

<h2>六、 四大高程基准体系与转换原理 (Vertical Datum Transformation)</h2>
<p>在海洋学与空间地理信息中，不同垂直基准之间存在显著物理差异：</p>
<table>
    <tr><th>基准面名称</th><th>物理定义</th><th>换算关系式</th><th>说明</th></tr>
    <tr><td><b>Tide (MSL)</b></td><td>相对局部平均海平面的瞬时潮位起伏</td><td>由 FES2022b 调和分析直接得出 (m)</td><td>零依赖外部重力场文件</td></tr>
    <tr><td><b>H_MDT_REF</b></td><td>相对当地 MDT 原始参考水准面的瞬时海面高</td><td><code>H_MDT_REF = Tide_MSL + MDT</code></td><td>大洋基于 GOCO06s，地中海/黑海基于 EIGEN-6C4</td></tr>
    <tr><td><b>H_EGM2008</b></td><td>相对 EGM2008 大地水准面的绝对海拔正高</td><td><code>H_EGM2008 = H_MDT_REF + ΔN</code></td><td><code>ΔN = N_ref - N_EGM2008</code> (范围 -6.6m ~ +6.8m)</td></tr>
    <tr><td><b>h_WGS84</b></td><td>WGS84 几何空间三维椭球高 (GNSS常用)</td><td><code>h_WGS84 = H_EGM2008 + N_EGM2008</code></td><td>通过 EGM2008 大地水准面差距换算</td></tr>
</table>

<div class="callout-info">
<b>双掩膜系统边界澄清：</b><br>
1. <b>Hybrid MDT 来源掩膜 (Geoid Source Mask)</b>：GeoTIFF 格式，用于判别给定坐标采用 GOCO06s 还是 EIGEN-6C4 作为参考重力场，直接关联 ΔN 改正。若未配置外部掩膜，系统自动启用闭合矢量多边形判定，并标记 <code>QC_DATUM_SOURCE_APPROX</code>。<br>
2. <b>FES2022b 外推掩膜 (Tide Mask)</b>：NetCDF 格式，仅用于描述 1/30° 规则网格的海陆与外推属性。不参与基准选择，且当前 Native LGP2 流程不使用该外推回退。
</div>

<h2>七、 自适应控制网格与空间拓扑连通防护 (Adaptive Control Grid & Topology Guard)</h2>
<ul>
    <li><b>空间梯度自适应四叉树控制网格</b>：
        在 DEM 覆盖区以初始间距（默认 4,000 米）自适应构建控制节点。在水陆交界、潮滩急剧变化区域根据误差容忍度阈值（默认 1.0%）自动递归细分至最小间距（默认 500 米）。
    </li>
    <li><b>像元级拓扑连通防护 (Target-Mask-Derived Topology Guard)</b>：
        根据 DEM 有效像元与 NoData 陆地屏障，自动构建多连通域拓扑标签。像元在双线性插值时仅使用归属于同一拓扑连通域的有效控制节点，彻底阻断潮位跨越岛礁、海堤或狭窄海峡的错误泄漏。
    </li>
    <li><b>角点重归一化 (Degraded Cell Corner Normalization)</b>：
        当四叉树单元局部角点落在陆地无效区或属于不同连通域时，系统自动剔除无效角点并对剩余可用角点权重进行重新归一化，严禁无效节点以 0m 掺入污染。
    </li>
</ul>

<h2>八、 持久化 Tide Cache 架构与防篡改签名 (Persistent Tide Cache & Signature)</h2>
<ul>
    <li><b>严格二阶段执行 (Two-Stage Execution)</b>：
        <ul>
            <li><b>Stage 1 (Tide Cache)</b>：在自适应控制网格上解算 FES 潮位时序，原子写入 NetCDF4 格式的 <code>*_tide.nc</code> 文件；</li>
            <li><b>Stage 2 / Stage 2b (Inundation / Exposure)</b>：流式逐分块读取 DEM 与 Tide Cache 解算空间产品。<b>硬性保证零 FES 调用</b>，速度提升显著。</li>
        </ul>
    </li>
    <li><b>防篡改参数签名 (CACHE_SIGNATURE)</b>：
        Tide Cache 内部包含基于输入 DEM 尺寸、坐标系、时间范围、采样率、分潮配置与自适应网格参数计算的 SHA256 签名。解算或续跑时深度比对签名，杜绝参数不匹配导致的错误复用。
    </li>
</ul>

<h2>九、 批量处理 6 大运行模式 (Six Batch Processing Modes)</h2>
<p>批量潮间带栅格引擎 (BatchRasterEngine) 现已全面支持 6 种运行模式：</p>
<table>
    <tr><th>模式名称</th><th>CLI 参数值</th><th>核心操作与产物</th><th>适用场景</th></tr>
    <tr>
        <td><b>Tide Cache Only</b></td>
        <td><code>tide</code></td>
        <td>仅解算自适应控制网格并生成 <code>*_tide.nc</code> 缓存文件。</td>
        <td>前期预解算、多任务共享底层潮汐流场。</td>
    </tr>
    <tr>
        <td><b>Tide + Inundation</b></td>
        <td><code>tide-inundation</code></td>
        <td>两阶段流程：先生成 Tide Cache，再解算潜在天文潮淹没频率 (默认)。</td>
        <td>常规淹没频率反演完整作业。</td>
    </tr>
    <tr>
        <td><b>Inundation from Cache</b></td>
        <td><code>inundation-from-cache</code></td>
        <td>直接利用已有完整 Tide Cache 反演淹没频率，零 FES 计算。</td>
        <td>重调容差参数、快速重新制图。</td>
    </tr>
    <tr>
        <td><b>Tide + Exposure</b></td>
        <td><code>tide-exposure</code></td>
        <td>先生成 Tide Cache，再反演潜在天文潮露出时间域 7 大空间产物。</td>
        <td>潮滩生态、沙滩动力学完整时间域分析。</td>
    </tr>
    <tr>
        <td><b>Exposure from Cache</b></td>
        <td><code>exposure-from-cache</code></td>
        <td>直接利用已有完整 Tide Cache 反演露出 7 大空间产物，零 FES 计算。</td>
        <td>已有潮位缓存快速增补时间域露出产物。</td>
    </tr>
    <tr>
        <td><b>All Products</b></td>
        <td><code>all</code></td>
        <td>全要素综合产物包：Tide Cache + 淹没频率 + 潜在露出 7 大空间产物。</td>
        <td>高标准科研与工程交付、全要素建库。</td>
    </tr>
</table>

<h2>十、 现有输出处理策略与断点恢复 (ExistingOutputPolicy & Resume Hardening)</h2>
<p>系统提供三种确定的现有输出处理策略，彻底消除不一致状态：</p>
<ul>
    <li><code>resume</code> (断点恢复，默认推荐)：
        扫描目标目录已存在的产物。对 Tide Cache 进行完整性与参数签名兼容性校验；对已有 GeoTIFF 产物严格核验尺寸、CRS、仿射变换、数据类型、NoData 与缓存签名。仅当所需产物全部无损存在时才标记 <code>SKIPPED_EXISTING</code> 跳过，否则安全接续计算。
    </li>
    <li><code>error_if_exists</code> (冲突报错)：
        在任务执行前进行统一预检防线拦截。若当前模式所需的任何目标文件（包括 <code>*_tide.nc</code>、淹没频率或 7 大露出产物中的任意一个）已存在，立即报错并拒绝覆写，确保历史成果不受意外破坏。
    </li>
    <li><code>overwrite</code> (强制覆盖)：
        允许重新计算，所有空间栅格产物通过 <code>*.tmp.tif</code> 临时写入并原子替换，确保过程无损覆盖。
    </li>
</ul>

<h2>十一、 质量控制掩膜位定义 (QC Bitmask Specifications)</h2>
<p>输出的 <code>*_exposure_qc.tif</code> (UInt16) 采用逐位标记体系（Bitmask）：</p>
<table>
    <tr><th>位 (Bit)</th><th>十进制值</th><th>常量标识</th><th>科学含义与处理说明</th></tr>
    <tr><td>-</td><td>0</td><td><code>QC_EXP_VALID</code></td><td>正常高保真解算，无任何降级或近似。</td></tr>
    <tr><td>bit 0</td><td>1</td><td><code>QC_EXP_DEGRADED_CELL</code></td><td>四叉树控制单元部分角点无效，已自动执行可用角点重归一化。</td></tr>
    <tr><td>bit 1</td><td>2</td><td><code>QC_EXP_INSUFFICIENT_NODES</code></td><td>局部缺少足够同连通域有效控制节点，可能产生外推误差。</td></tr>
    <tr><td>bit 2</td><td>4</td><td><code>QC_EXP_DATUM_APPROX</code></td><td>垂直基准偏移采用了闭合多边形近似判别。</td></tr>
    <tr><td>bit 3</td><td>8</td><td><code>QC_EXP_TERMINAL_UNAVAILABLE</code></td><td>终端时刻采样缺失或不可用，末端时步采用截断估算。</td></tr>
    <tr><td>bit 4</td><td>16</td><td><code>QC_EXP_PARTIAL_VALID_TIME</code></td><td>时间序列存在无效数据间隙，有效时间覆盖率 &lt; 100%。</td></tr>
    <tr><td>bit 5</td><td>32</td><td><code>QC_EXP_PERMANENTLY_SUBMERGED</code></td><td>全有效时段内水面始终高于地形（常时淹没区）。</td></tr>
    <tr><td>bit 6</td><td>64</td><td><code>QC_EXP_PERMANENTLY_EXPOSED</code></td><td>全有效时段内水面始终低于地形（常时露出区）。</td></tr>
    <tr><td>-</td><td>65535</td><td><code>QC_EXP_NODATA</code></td><td>DEM 陆地无效屏蔽区或无数据像元。</td></tr>
</table>

<h2>十二、 批处理清单与产物追溯 (Batch Manifest & Provenance)</h2>
<p>
每次批量运行均在输出目录根节点原子生成并实时同步 <code>batch_manifest.json</code> 与 <code>batch_manifest.csv</code> 清单。
清单详细记录每个 DEM 瓦片的输入文件名、绝对路径、CRS、像元尺寸、NoData、时间步长、控制节点数、各阶段执行耗时、状态、Tide Cache 路径、淹没频率路径、露出产物输出目录 (<code>exposure_output_dir</code>)、露出产物完整性标记 (<code>exposure_products_complete</code>) 及 7 大露出产物路径映射。具备向前向后字段兼容性，支持跨版本断点恢复查看。
</p>

<h2>十三、 系统硬件资源与内存管理 (System Resources & Memory Scaling)</h2>
<table>
    <tr><th>运行模式</th><th>推荐内存</th><th>内存架构与设计特征</th></tr>
    <tr><td><b>单点连续模拟</b></td><td>4 ~ 8 GB</td><td>局部 ±1° BBox 空间裁剪，动态 5,000 点时间分块，常驻内存平稳。</td></tr>
    <tr><td><b>全球散点批处理</b></td><td>8 GB 或以上</td><td>5°×5° 自适应空间聚类分批解算，避免全局全网格展开。</td></tr>
    <tr><td><b>空间栅格解算</b></td><td>8 ~ 16 GB</td><td>采用 512×512 空间分块流式重构，内存占用与总像元规模严格解耦，单步流式重构水位切片，实现可控的有界内存驻留。</td></tr>
    <tr><td><b>批量潮间带解算</b></td><td>8 ~ 16 GB</td><td>单瓦片顺序推进 (max_parallel_tiles = 1) 与原子写入，单瓦片失败自动隔离。</td></tr>
</table>

<h2>十四、 科学局限性与使用边界 (Known Scientific Limitations)</h2>
<ol>
    <li><b>固定代表性地形假设</b>：计算假定 DEM 在模拟时段内保持几何恒定，无法反映台风暴潮引起的高强度泥沙侵蚀与滩涂冲淤变化。</li>
    <li><b>纯天文潮驱动</b>：水位时序完全源于天文引潮力调和常数，未叠加气压骤降、强风增水引起的风暴潮增减水与海啸波浪。</li>
    <li><b>几何视界浸没模型</b>：采用基于连通域保护的水位-高程几何相交，未求解二维浅水动力学 Navier-Stokes 方程，无法模拟水流流速、波浪破碎爬高与退水水力阻力。</li>
    <li><b>沙滩非干燥时间</b>：露出仅代表天文潮水面低于地形，不代表沙滩表面已干燥，沙滩实际湿润状态受地下水位与蒸发控制。</li>
</ol>

<h2>十五、 典型应用场景与推荐工作流 (Recommended Workflows)</h2>
<ul>
    <li><b>工作流 A: 沿海潮滩大范围潜在淹没频率反演</b>：
        准备沿海 10m/30m DEM 文件夹 &rarr; 批量模式选择 <code>tide-inundation</code> &rarr; 基准设为 EGM2008 &rarr; 策略选择 <code>resume</code> &rarr; 运行生成淹没频率与质量掩膜。
    </li>
    <li><b>工作流 B: 潮间带生态/沙滩潜在露出时间域分析</b>：
        若已有 <code>*_tide.nc</code> 缓存，选择 <code>exposure-from-cache</code>；若全新处理选择 <code>tide-exposure</code> 或 <code>all</code> &rarr; 运行生成 7 大时间域露出空间栅格产物。
    </li>
    <li><b>工作流 C: 遥感影像潮汐校正与瞬时水面高程反演</b>：
        在 Tab 3 选择 <code>Snapshot</code> &rarr; 设定遥感卫星过轨时间（UTC）与空间 DEM &rarr; 解算瞬时水面高程栅格。
    </li>
</ul>

</body>
</html>
"""


class ManualDialog(QDialog):
    """功能说明文档与操作手册弹窗 (v1.6 Beta)"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("CoastTideX 功能说明文档与操作手册 - v1.6 Beta")
        self.resize(920, 720)

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
