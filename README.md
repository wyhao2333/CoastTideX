# CoastTideX: 全球海岸带高精度潮位模拟与高程基准转换系统

<p align="center">
  <img src="https://img.shields.io/badge/Release-v1.3-blue.svg" alt="Release v1.3">
  <img src="https://img.shields.io/badge/Python-3.11-blue.svg" alt="Python 3.11">
  <img src="https://img.shields.io/badge/GUI-PyQt6-green.svg" alt="PyQt6">
  <img src="https://img.shields.io/badge/Tide%20Model-FES2022b%20LGP2-0284c7.svg" alt="FES2022b">
  <img src="https://img.shields.io/badge/MDT-CNES--CLS22-8b5cf6.svg" alt="CNES-CLS22 MDT">
  <img src="https://img.shields.io/badge/Datum-MSL%20%7C%20EGM2008-f59e0b.svg" alt="Vertical Datum">
  <img src="https://img.shields.io/badge/License-MIT-emerald.svg" alt="MIT License">
</p>

<p align="center">
  <b>[ 中文版 (Chinese) ]</b> | <a href="README_EN.md">English Version</a>
</p>

---

## 📖 项目简介 (Overview)

**CoastTideX** 是一款专为**海洋工程、海岸带遥感、大地测量基准统一与水下水文建模**设计的高性能潮位模拟与垂直基准转换桌面系统。

系统基于国际权威的法国 CNES/AVISO **FES2022b 全球海洋潮汐模型**（包含全部 34 个主分潮的非结构有限元三角形网格 LGP2 二阶多项式解），攻克了传统规则方格网在曲折复杂海岸线、河口湾区由于“阶梯锯齿误差”导致的潮位失真问题。同时，系统内嵌 **CNES-CLS22 全球平均动态地形 (MDT)** 模型与 **NGA EGM2008 2.5分超高精度大地水准面栅格**，实现了从**局部平均海平面 (MSL)** 到 **EGM2008 大地水准面绝对海拔高**的一键高精度无缝转换。

---

### ✨ 核心特性 (Key Features)

* 🌊 **FES2022b 原生非结构网格支持**：直读 3.77 GB 原生三角网格，在复杂海岸带具备最高空间保真度，支持全部 34 个全日潮、半日潮、浅海非线性潮与长周期平衡潮。
* 📅 **整年与长时序高密度潮位序列预测 (v1.3)**：
  * **自定义时段与整年快捷模式**：支持任意起止时间与快捷整年（如 2024 年）一键生成；
  * **严格半开区间与点数保真**：采用 $[start, end)$ 半开区间，严密保证 2024 闰年 30min 步长精确生成 **17,568** 个连续采样点（平年 17,520 个点），杜绝跨年边界重复与漏点；
  * **实时动态点数预算**：时间或频率改变时即时显示「预期样本: XX 点」，有效预防内存误操作。
* ⏳ **自适应时间分块（Time-Chunking）流式解算 (v1.3)**：
  * 底层解算核心自动引入 5,000 点动态分块流式迭代，超长序列（单年、多年或 5min/6min/10min 高密度步长）均逐块解算并实时汇报进度，彻底消除界面卡顿或未捕获异常退出风险；
  * 经 2023–2024 连续两年（35,089 个连续点）高压测试，解算全程平稳无卡顿。
* ⚡ **自适应空间分块与局部 BBox 加速**：
  * **单点时序预测**：自动推求最小包围框，仅在内存中建立局部空间拓扑索引，实现秒级加载与极低内存占用；
  * **全球批量离散点**：采用 $5^\circ \times 5^\circ$ 自适应空间网格分块聚类 (Spatial Chunking)，彻底杜绝全球散点退化为全地球加载的内存爆炸陷阱。
* 📐 **双重水准面 Hybrid MDT 严密科学转换体系 (v1.3)**：
  * **全球大洋基准**：GOCO06s 基准 ($H_{\text{EGM2008}} = \text{Tide} + \text{MDT} + \Delta N_{\text{GOCO06s}\rightarrow\text{EGM2008}}$)；
  * **地中海/黑海高阶基准**：EIGEN-6C4 ($d/o=2190$) 严密基准 ($H_{\text{EGM2008}} = \text{Tide} + \text{MDT} + \Delta N_{\text{EIGEN-6C4}\rightarrow\text{EGM2008}}$，支持本地配置 `data/geoid/delta_n_eigen6c4_minus_egm2008.tif`)；
  * **统一标称主变量与严格语义真值**：引入 `h_mdt_ref_m` 代表相对 MDT 原始基准面海面高；在欧陆混合区严禁伪造 `h_goco06s_m`（地中海/黑海严格输出 `NaN`）；深陆点全要素严格置为 `NaN`；
  * **精细多边形掩码**：基于纯 NumPy 闭合多边形射线法判别，零额外库依赖，彻底解决加的斯湾、直布罗陀西侧、比斯开湾、红海等被矩形 BBox 误判的隐患。
* 🌊 **潜在天文潮淹没概率分析与潮滩 10m DEM 栅格解算 (v1.3)**：
  * 内置向量化经验互补累积分布算法 (`compute_inundation_frequency`)，秒级支持海岸带地形高程点在长期天文潮作用下的淹没频率/概率计算；
  * 提供专用脚本 `scripts/calculate_inundation_raster.py`，支持大范围 10m/30m DEM 分块流式解算，直接输出整年潜在天文潮淹没频率 (0~100%) GeoTIFF 空间栅格。
* 🎯 **高精度栅格双线性空间插值与目标敏感基准解耦 (v1.3)**：
  * 对 EGM2008 与 $\Delta N$ 栅格执行真双线性插值 (`map_coordinates(order=1)`)；
  * **目标敏感加载**：仅计算 MSL 或 EGM2008 时，不强制要求 WGS84 栅格；缺失关键栅格时给出严谨的 `qc_warning` 或在 `strict=True` 下抛出 `DatumDataError`。
* 🖥️ **现代化 PyQt6 交互界面与海量数据防护 (v1.3)**：
  * **数据预览安全截断**：长序列计算后仅在 GUI 表格中渲染前 2,000 行并附带友好提示，导出 CSV/Excel 仍保持 100% 完整全量输出，彻底杜绝界面卡死；
  * **图表交互智能优化**：波形图支持万级点自适应降采样与峰谷标注智能过滤，缩放平移丝滑无卡顿；
  * **自适应滚动面板**：控制面板封装于 `QScrollArea`，彻底解除窗口纵向锁定，各类屏幕均可自由拉伸；
  * **动态时区与 DST 稳健过渡**：计算后切换时区即时自动重构时间轴，消除夏令时跳变时刻的静默 NaT 风险。
* 📑 **批量多点离散解算与导出**：支持加载包含成千上万个经纬度及时间点的 CSV 表格，自动批量向量化解算并一键导出为标准 CSV / Excel。
* 📦 **开箱即用与独立打包**：支持通过 `run_gui.bat` 一键启动，并提供完整的 `build_exe.bat` 脚本，可快速打包为无需 Python 环境的独立 Windows `.exe` 程序。

---

## 💻 硬件配置与内存需求 (System & Hardware Requirements)

CoastTideX 针对不同应用场景设计了精细的内存管理与空间拓扑裁剪策略，推荐配置如下：

| 应用场景 (Scenario) | 最低内存 (Min RAM) | 推荐内存 (Rec RAM) | 算力与存储建议 (CPU & Disk) | 说明 (Details) |
| :--- | :---: | :---: | :--- | :--- |
| **单点连续时序预测**<br>*(Single Point Mode)* | **4 GB** | **8 GB** | 双核 CPU 及以上<br>SSD 剩余空间 ≥ 10 GB | 依靠局部 BBox 裁剪，仅载入目标点周边小区域网格拓扑，运行时常驻内存仅需 ~1.2 GB。 |
| **局部区域批量解算**<br>*(Local Batch Mode, ≤8°跨度)* | **4 GB** | **8 GB** | 四核 CPU 及以上<br>SSD 剩余空间 ≥ 10 GB | 空间跨度在 8° 以内时，一次性构建局部包围框，内存开销轻量。 |
| **全球离散散点批量解算**<br>*(Global Discrete Batch Mode)* | **8 GB** | **16 GB** | 四核至八核 CPU<br>高速 NVMe SSD 优先 | 系统激活 $5^\circ \times 5^\circ$ 自适应空间网格分块聚类，逐块加载与释放，峰值内存受控于单块大小。 |
| **全地球无约束大范围网格展开**<br>*(Full Unconstrained Grid)* | **16 GB** | **32 GB** | 八核 CPU 及以上<br>高速 NVMe SSD | 若强行一次性请求全球无约束范围，FES2022b 569 万节点及 34 分潮全展开需要约 6~8 GB 连续物理内存。 |

* **支持操作系统**：Windows 10/11 64-bit、Ubuntu 20.04+、macOS (x86_64 / Apple Silicon via Rosetta 2)。
* **环境兼容性**：Python 3.11。

---

## 🏛️ 系统架构 (Architecture)

```text
                                ┌────────────────────────┐
                                │   CoastTideX (GUI/CLI) │
                                └───────────┬────────────┘
                                            │
             ┌─────────────────────────────┴─────────────────────────────┐
             ▼                                                           ▼
  ┌───────────────────────┐                                   ┌───────────────────────┐
  │  潮位解算核心 (Tide)  │                                   │  基准转换核心 (Datum) │
  └──────────┬────────────┘                                   └──────────┬────────────┘
             │                                                           │
   ┌─────────┴─────────┐                                ┌────────┬───────┴────────┬────────┐
   ▼                   ▼                                ▼        ▼                ▼        ▼
FES2022b 原生网格   局部 BBox 索引                   CNES-CLS22 MDT   ΔN 改正栅格   EGM2008 GeoTIFF
(569万节点/34分潮)  (秒级加载/分块聚类)              (GOCO06s基准)  (GOCO减EGM)  (水准面起伏 N)
             │                                                           │
             └─────────────────────────────┬─────────────────────────────┘
                                           ▼
                       ┌───────────────────────────────────────┐
                       │  四大垂直基准统一输出体系             │
                       │  (MSL / GOCO06s / EGM2008 / WGS84)    │
                       │  交互式波形图 / 报表导出 (CSV/XLSX)   │
                       └───────────────────────────────────────┘
```

---

## 📐 科学原理与转换公式 (Methodology)

### 1. 潮汐调和预测
任意时刻 $t$、空间坐标 $(\lambda, \phi)$ 处的潮位 $\eta(t)$ 由 34 个分潮线性叠加：

$$\eta(t) = \sum_{i=1}^{34} f_i(t) \cdot H_i(\lambda, \phi) \cdot \cos\left( \omega_i t + V_{0,i}(t_0) + u_i(t) - g_i(\lambda, \phi) \right) + h_{\text{LP}}(t)$$

* $H_i, g_i$：FES2022b 原生非结构三角形网格 (LGP2) 提供的各分潮振幅与格林威治初相迟角；
* $f_i(t), u_i(t)$：月球 18.61 年交点调制因子与交点修正角；
* $h_{\text{LP}}(t)$：长周期平衡潮。

### 2. 双重水准面 Hybrid MDT 严密垂直基准转换体系 (v1.3)
在传统粗糙计算中，常将 $\text{Tide} + \text{MDT}$ 直接视为 EGM2008 基准下的高程。**这是在科学定义上不成立的**。
法国 CNES-CLS22 全球平均动态地形 (MDT) 是一个 **混合重力基准模型 (Hybrid Model)**：
* **全球开阔大洋**：基于 **GOCO06s 卫星重力大地水准面**，其与全球普遍使用的 **EGM2008 大地水准面**之间在全球存在 $-6.63\text{m} \sim +6.79\text{m}$（标准差 $0.34\text{m}$）的显著物理差异；
* **地中海与黑海半封闭海域**：采用超高阶局部重力场模型 **EIGEN-6C4 ($d/o=2190$)** 作为其大地水准面参考。

为此，CoastTideX v1.3 构建了完备的双重水准面转换体系：

1. **瞬时平均海平面起伏 (MSL)**：
   $$\text{Tide}_{\text{MSL}}(\lambda, \phi, t) = \eta(t)$$
2. **相对 MDT 原始基准面海面高 (标称主变量)**：
   $$H_{\text{MDT-REF}}(\lambda, \phi, t) = \text{Tide}_{\text{MSL}}(\lambda, \phi, t) + \text{MDT}_{\text{CLS22}}(\lambda, \phi)$$
   * 全球大洋区该值即为相对 GOCO06s 基准面海面高 ($H_{\text{GOCO06S}}$)；
   * 在地中海与黑海，系统严格置 `h_goco06s_m` 为 `NaN`（严禁伪造），并自动切换为 EIGEN-6C4 改正基准。
3. **相对 EGM2008 大地水准面绝对海拔正高**（严格加入水准面差值改正项 $\Delta N$）：
   $$H_{\text{EGM2008}}(\lambda, \phi, t) = H_{\text{MDT-REF}}(\lambda, \phi, t) + \Delta N(\lambda, \phi)$$
   * 全球大洋区：$\Delta N(\lambda, \phi) = N_{\text{GOCO06S}}(\lambda, \phi) - N_{\text{EGM2008}}(\lambda, \phi)$；
   * 地中海与黑海：$\Delta N(\lambda, \phi) = N_{\text{EIGEN-6C4}}(\lambda, \phi) - N_{\text{EGM2008}}(\lambda, \phi)$。
4. **WGS84 几何空间三维椭球高**（直接对接 GNSS/RTK）：
   $$h_{\text{WGS84}}(\lambda, \phi, t) = H_{\text{EGM2008}}(\lambda, \phi, t) + N_{\text{EGM2008}}(\lambda, \phi)$$
   其中 $N_{\text{EGM2008}}$ 由系统内置的全球 2.5 分 EGM2008 栅格经双线性插值提取（严格消除半像元 1.25' 空间位移）。

### 3. 网格插值质量控制标识 (Quality Flag)
系统在单点和批量解算中均输出 `quality_flag` 与 `qc_warning`，用于衡量动力学与基准转换的可信度：

| 质量标识 (Quality Flag) | 状态定义 | 科学意义与处理行为 |
| :---: | :---: | :--- |
| **Flag 1 ~ 6** | 高保真内插 (Valid) | 目标点位于非结构有限元三角形网格单元内部，解算精度最高，质量完全合格。 |
| **Flag < 0** | 近岸动力学外推 (Extrapolated) | 目标点位于复杂海岸线边缘或极浅滩涂，由动力学外推获得，GUI 以黄色高亮警示。 |
| **Flag = 0** | 陆地/无数据 (Missing) | 目标点位于深内陆或无潮汐解区域，潮位及高程严格置为 `NaN`（杜绝静默返回 0.0），GUI 以红色警示。 |

### 4. 国际标准潮位预测时间采样步长指南 (Literature Benchmarks)
系统在单点预测时提供 5分/6分/10分/15分/30分/1小时/2小时 多种采样步长，对应权威国际海洋规范与学术证据：

| 推荐步长 (Interval) | 权威标准与应用场景 | 科学依据与文献证据 (Literature Citations) |
| :--- | :--- | :--- |
| **6 分钟 (0.1 小时)** | **NOAA 业务化实时验潮与预报** | **美国 NOAA CO-OPS 业务化规范**：全美验潮站实时水位监测与天文潮位预测的核心标准时间步长。高密度采样能精准刻画由浅海非线性效应产生的微弱高阶分潮波形畸变（如 $M_4, MS_4, M_6$）与驻波转折极值。 |
| **10 ~ 15 分钟** | **IOC / GLOSS 验潮站标准** | **联合国教科文组织 IOC / GLOSS 规范**：全球海平面观测系统（GLOSS）推荐的标准业务化观测步长。在确保波形极值精度的同时，显著降低长期海量时序存储和计算开销。 |
| **30 分钟 (0.5 小时)** | **整年潮汐长序列工程模拟 (v1.3)** | **工程水文与长序列高密度仿真**：计算效率与波形保真度的最优平衡。2024 闰年整年恰好生成 17,568 个点，能完整覆盖所有半日潮波峰波谷极值。 |
| **1 小时 (60 分钟)** | **经典调和分析与长期海平面研究** | **Foreman (1977) 与 Pawlowicz et al. (2002, T_TIDE)**：经典潮汐调和分析的标准输入步长，适用于天级别至年代际的宏观天文潮演化研究。 |

### 5. 潜在天文潮淹没概率/历时分析 (Inundation Frequency)
在滨海湿地生态演替、红树林宜林带评估与海岸防汛工程中，常需评估特定地形高程点在长期天文潮作用下的被淹没历时比率。
CoastTideX 内置高效向量化互补经验累积分布函数（CCDF / 1 - ECDF）：

$$P_{\text{inundation}}(z) = P(\eta_{\text{tide}} > z) = 1 - F(z) = \frac{1}{N}\sum_{i=1}^N \mathbb{I}(\eta_i > z)$$

系统采用 `np.searchsorted` 实现 $O(M \log N)$ 极限速度计算，支持单点高程或大面积数字高程模型 (DEM) 的瞬时概率反演。

---

## 🚀 快速上手 (Quick Start)

### 1. 环境准备 (支持一键初始化)
在项目根目录下双击运行 `setup_env.bat`，脚本将自动检测 Python 3.11 环境并配置完整依赖。

若手动配置：
```bash
# 创建虚拟环境
python -m venv .venv
# 激活环境 (Windows)
.venv\Scripts\activate
# 安装依赖
pip install -r requirements.txt
```

### 2. 启动桌面客户端
在项目根目录下双击运行 `run_gui.bat`，或在命令行中执行：
```bash
.venv\Scripts\python.exe app.py
```

### 3. 命令行调用 (CLI 批处理)
系统提供了易用的 `cli.py` 脚本，全面支持时区参数、整年模式与四大基准自动输出：

* **单点时序预测 (自定义时段)**：
  ```bash
  python cli.py single --lon 122.0 --lat 31.0 --start "2026-09-10 00:00:00" --end "2026-09-11 00:00:00" --step 1h --tz UTC --output output.csv
  ```
* **单点整年高密度预测 (2024 闰年精确 17,568 采样点)**：
  ```bash
  python cli.py single --lon 122.0 --lat 31.0 --year 2024 --step 30min --output tide_2024.csv
  ```
* **批量表格计算**：
  ```bash
  python cli.py batch --input points.csv --lon-col longitude --lat-col latitude --time-col datetime --tz UTC --output batch_out.csv
  ```
* **潮滩 10m DEM 潜在天文潮淹没频率栅格计算**：
  ```bash
  python scripts/calculate_inundation_raster.py --dem tidal_flat_10m_dem.tif --out inundation_pct_2024.tif --year 2024 --freq 30min --datum egm2008
  ```

### 4. Python API 代码集成

#### (1) 整年 30 分钟高密度潮位序列预测 (2024 闰年精确 17,568 样本点)
```python
from core.tide_engine import FESTidePredictor
from core.datum_engine import DatumTransformer
from core.utils import compute_inundation_frequency

predictor = FESTidePredictor()
transformer = DatumTransformer()

# 一键获取长江口 2024 闰年整年 30 分钟连续潮位 (严格 [2024-01-01, 2025-01-01) 半开区间，17568 个点)
df_year = predictor.predict_year(
    lon=122.0, lat=31.0,
    year=2024,
    freq="30min",
    source_tz="UTC"
)
print(f"2024 整年样本点数: {len(df_year)}")  # 严格输出: 17568

# 严密转换四大垂直基准
datum_res = transformer.convert_tide_datums(
    tide_msl_m=df_year['tide_total_m'].values,
    lons=122.0,
    lats=31.0
)

df_year['tide_msl_m'] = datum_res['tide_msl_m']
df_year['h_mdt_ref_m'] = datum_res['h_mdt_ref_m']
df_year['h_goco06s_m'] = datum_res['h_goco06s_m']
df_year['h_egm2008_m'] = datum_res['h_egm2008_m']
df_year['h_wgs84_m'] = datum_res['h_wgs84_m']

# (2) 计算海岸带特定地形高程点 (如 +1.5m, +2.0m, +2.5m) 的潜在天文潮淹没概率
elevations = [1.5, 2.0, 2.5]
freq_pct = compute_inundation_frequency(
    water_levels_m=df_year['tide_msl_m'].values,
    terrain_elevations_m=elevations,
    as_percentage=True
)
for elev, pct in zip(elevations, freq_pct):
    print(f"高程 {elev:+.1f} m 处的天文潮被淹没频率: {pct:.2f}%")
```

---

## 📦 打包为独立可执行文件 (.exe)

项目内置了完整的 PyInstaller 构建脚本 `build_exe.bat`：
1. 确保 `.venv` 中已包含 `pyinstaller`；
2. 双击运行 `build_exe.bat`；
3. 构建完成后，独立的应用程序将存放在 `dist/CoastTideX/` 目录下，双击 `CoastTideX.exe` 即可在未安装 Python 的 Windows 电脑上直接运行。

---

## 📁 目录结构 (Directory Structure)

```text
CoastTideX/
├── .github/workflows/ci.yml        # GitHub Actions 自动化测试流水线
├── .gitignore                      # 严密排除大体积网格、.venv、中间缓存
├── LICENSE                         # MIT 开源授权协议
├── README.md                       # 中文主文档
├── README_EN.md                    # 英文说明文档
├── requirements.txt                # 依赖包清单
├── setup_env.bat                   # 自动初始化 .venv 脚本
├── run_gui.bat                     # 一键启动 GUI 脚本
├── build_exe.bat                   # PyInstaller 自动打包构建脚本
├── config.yaml                     # 本地数据源路径与运行参数配置
├── app.py                          # 桌面图形界面启动入口
├── cli.py                          # 命令行批处理工具入口
├── scripts/
│   ├── generate_delta_n.py         # ΔN 大地水准面高差栅格通用生成脚本 (支持 GOCO06s/EIGEN-6C4 等)
│   └── calculate_inundation_raster.py # 潮滩 10m/30m 像元潜在天文潮淹没频率栅格计算工具 (支持 DEM 分块流式解算)
├── data/
│   └── geoid/
│       ├── README_GEOID.md         # 大地水准面基准与 ICGEM 来源严密说明
│       ├── us_nga_egm08_25.tif     # NGA EGM2008 2.5分全球大地水准面栅格 (~76.9MB)
│       ├── delta_n_goco06s_minus_egm2008.tif # GOCO06s 与 EGM2008 差值改正栅格 (~22.7MB)
│       └── delta_n_eigen6c4_minus_egm2008.tif # EIGEN-6C4 与 EGM2008 差值改正栅格 (地中海/黑海本地生成，不入 Git 库)
├── core/                           # 核心计算包
│   ├── tide_engine.py              # FES2022b 局部加速、时间分块 (Time-Chunking) 与整年 17,568 点预测
│   ├── datum_engine.py             # 双重水准面 Hybrid MDT 转换体系与精细多边形掩码
│   └── utils.py                    # 预设港口、坐标校验、严格时区 (含DST)、淹没概率分析与元数据安全提取
├── gui/                            # PyQt6 桌面应用包
│   ├── main_window.py              # 桌面主窗口 (单点/时段/整年模式、表格安全截断、QC指示徽章)
│   ├── chart_widget.py             # Matplotlib 交互式波形组件 (智能降采样与自适应极值检测)
│   ├── manual_dialog.py            # 内置功能说明文档与操作手册对话框 (v1.3 最新版)
│   ├── settings_dialog.py          # 数据源可视化配置与连通性校验弹窗
│   └── styles.py                   # 扁平科技感深色 QSS 样式表
└── tests/                          # 自动化单元与集成测试套件
    └── test_engines.py             # 核心引擎全流程检验测试 (覆盖22项严密测试/闰年保真/闭合性)
```

## 📝 版本更新日志 (Changelog)

### v1.3 (2026-09)
* **[长序列与整年模式]** 单点预测新增「整年快捷模式」与「自定义时段」无缝切换，采用严格半开区间 $[start, end)$，2024 闰年 30min 步长精确生成 **17,568** 个连续采样点（平年 17,520 点），附带实时动态样本预算与坐标强校验；
* **[核心时间分块机制]** 底层潮位解算引入自适应时间分块（Time-Chunking，5,000 点/块）流式解算与逐块实时进度汇报，经连续两年（35,089 点）极限压力测试，彻底解决超长序列下潜在的界面假死与崩溃；
* **[双水准面 Hybrid MDT]** 科学解耦全球大洋（GOCO06s）与地中海/黑海（EIGEN-6C4）参考基准，引入统一标称主变量 `h_mdt_ref_m`，欧陆混合区严格保持 `h_goco06s_m` 为 `NaN`，坚决杜绝基准伪造；内置 `data/geoid/delta_n_eigen6c4_minus_egm2008.tif` 差值栅格；
* **[高精闭合多边形识别]** 基于 `matplotlib.path.Path` 构建地中海与黑海高精度闭合多边形，彻底杜绝加的斯湾、直布罗陀西侧、比斯开湾、红海等被矩形 BBox 误判；
* **[潜在天文潮淹没概率与 10m DEM 工具]** 新增 `compute_inundation_frequency` 向量化经验互补累积分布函数（CCDF），并发布 `scripts/calculate_inundation_raster.py`，支持大范围 10m/30m 潮滩 DEM 分块流式反演 2024 整年潜在天文潮淹没频率空间栅格；
* **[GUI 海量数据性能优化]** 表格安全截断为前 2,000 行预览并支持 100% 全量导出，图表组件增加智能降采样与波峰波谷标注自适应阈值，万级点交互顺畅无卡顿；
* **[通用 ΔN 生成脚本]** 泛化 `scripts/generate_delta_n.py` 支持任意参考重力场模型（GOCO06s / EIGEN-6C4 等）与目标基准的差值栅格生成，具备空间配准严密校验与元数据标签写入；
* **[自动化测试全面升级]** 单元与集成测试扩展至 **22/22 全通过**，覆盖真实 FES2022b 网格全流程、2024 闰年 17,568 点保真度、夏令时与相对路径解析。

### v1.2 (2026-09)
* **[UI 自适应缩放]** 左侧控制面板引入 `QScrollArea` 包装，彻底解除主窗口纵向缩放锁定限制，完美适配 768p/1080p 笔记本及各类缩放比例屏幕；
* **[MSL 模式解耦]** 纯潮位预测时不再强制依赖 MDT 与 Geoid 栅格文件，未勾选转换时轻量快速运行与出图；
* **[地中海/黑海科学基准]** 自动识别 Hybrid MDT 在地中海与黑海采用的 EIGEN-6C4 ($d/o=2190$) 超高阶重力基准并予以专属质量标注；
* **[动态时区即时联动]** 计算完成后切换时区下拉框，系统实时重构图表时间轴和表格时间列，无须重复触发耗时计算；
* **[夏令时 DST 稳健过渡]** 解决夏令时跳变与回折边界的潜在时间歧义，消除静默 NaT 风险；
* **[采样步长文献指南]** 界面及文档内置国际主流验潮业务（NOAA 6分钟、IOC/GLOSS 10~15分钟、Foreman 1小时）的标准依据与学术文献；
* **[依赖兼容优化]** 消除新版 `affine` 矩阵乘法弃用警告，单元测试覆盖扩展至 14/14 全通过。

### v1.1 (2026-09)
* 修正平均海平面至 EGM2008 科学基准换算，引入 $\Delta N = N_{\text{GOCO06S}} - N_{\text{EGM2008}}$ 大地水准面差值改正项；
* 优化单点时序预测局部 BBox 索引与全球批量点自适应空间网格分块聚类 (Spatial Chunking)；
* 引入真双线性栅格插值与严格内陆/缺失值 `NaN` 传播机制；
* 添加大潮波峰波谷物理极值自动检测与统计卡片。

---

## 📚 引用与致谢 (Citations & Acknowledgements)

如果您在学术研究、科学论文或工程报告中使用了本软件，请致谢并引用以下数据源：

1. **FES2022b Tide Model**:
   > *"The FES2022 Tide product was funded by CNES, produced by LEGOS, NOVELTIS and CLS and made freely available by AVISO."* (DOI: `10.24400/527896/a01-2024.004`)
2. **CNES-CLS22 MDT**:
   > *"The Mean Dynamic Topography CNES-CLS22 was produced by CLS and CNES."* (DOI: `10.24400/527896/a01-2023.003`)
3. **GOCO06s Satellite Gravity Field**:
   > Kvas, A., et al. (2021). GOCO06s - a satellite-only global gravity field model. *International Centre for Global Earth Models (ICGEM)*, GFZ Potsdam. (DOI: `10.5880/ICGEM.2021.002`)
4. **EGM2008 Geoid**:
   > Pavlis, N. K., Holmes, S. A., Kenyon, S. C., & Factor, J. K. (2012). The development and evaluation of the Earth Gravitational Model 2008 (EGM2008). *Journal of Geophysical Research: Solid Earth*, 117(B4).

