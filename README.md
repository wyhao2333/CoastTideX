# CoastTideX: 全球海岸带高精度潮位模拟与高程基准转换系统

<p align="center">
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

## ✨ 核心特性 (Key Features)

* 🌊 **FES2022b 原生非结构网格支持**：直读 3.77 GB 原生三角网格，在复杂海岸带具备最高空间保真度，支持全部 34 个全日潮、半日潮、浅海非线性潮与长周期平衡潮。
* ⚡ **自适应局部加速算法 (Bounding-Box Caching)**：根据目标点位自动推求最小包围框，仅在内存中建立局部空间拓扑索引，实现秒级加载与极低内存占用。
* 📐 **严密双基准面转换体系**：
  * **MSL 基准**：相对局部平均海平面的瞬时潮汐起伏高度；
  * **EGM2008 基准**：深度融合 CNES-CLS22 MDT 模型，输出大地测量认可的绝对海拔高程，可直接与陆地 LiDAR、Copernicus DEM 无缝拼接进行淹没分析。
* 🖥️ **现代化 PyQt6 交互界面**：
  * 内置全球十余个典型强潮河口/重要港口一键预设（长江口、珠江口、杭州湾、渤海湾、鹿特丹、纽约等）；
  * 嵌入 Matplotlib 交互式波形画布，支持缩放、平移并**自动标注天文高潮（波峰）与低潮（波谷）极值点**；
  * 支持多线程异步计算，界面流畅不卡顿，配备实时进度条与统计指标卡片。
* 📑 **批量多点离散解算与导出**：支持加载包含成千上万个经纬度及时间点的 CSV 表格，自动批量解算并一键导出为标准 CSV / Excel。
* 📦 **开箱即用与独立打包**：支持通过 `run_gui.bat` 一键启动，并提供完整的 `build_exe.bat` 脚本，可快速打包为无需 Python 环境的独立 Windows `.exe` 程序。

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
   ┌─────────┴─────────┐                                       ┌─────────┴─────────┐
   ▼                   ▼                                       ▼                   ▼
FES2022b 原生网格   局部 BBox 索引                        CNES-CLS22 MDT     EGM2008 GeoTIFF
(569万节点/34分潮)  (秒级加载/低内存)                      (洋流与风海面偏置)   (大地水准面起伏 N)
             │                                                           │
             └─────────────────────────────┬─────────────────────────────┘
                                           ▼
                       ┌───────────────────────────────────────┐
                       │  多基准面潮位输出 (MSL / EGM2008 / m) │
                       │    交互式波形图 / 报表导出 (CSV/XLSX) │
                       └───────────────────────────────────────┘
```

---

## 📐 科学原理与转换公式 (Methodology)

### 1. 潮汐调和预测
任意时刻 $t$、空间坐标 $(\lambda, \phi)$ 处的潮位 $\eta(t)$ 由 34 个分潮线性叠加：

$$\eta(t) = \sum_{i=1}^{34} f_i(t) \cdot H_i(\lambda, \phi) \cdot \cos\left( \omega_i t + V_{0,i}(t_0) + u_i(t) - g_i(\lambda, \phi) \right) + h_{\text{LP}}(t)$$

* $H_i, g_i$：FES2022b 原生网格提供的分潮振幅与格林威治初相迟角；
* $f_i(t), u_i(t)$：月球 18.61 年交点调制因子与交点修正角；
* $h_{\text{LP}}(t)$：长周期平衡潮。

### 2. 垂直基准转换至 EGM2008
FES2022 默认输出的是相对平均海平面（MSL）的偏差，转化为 EGM2008 正高需叠加全球平均动态地形（MDT）：

$$H_{\text{EGM2008}}(\lambda, \phi, t) = \text{MDT}(\lambda, \phi) + \eta_{\text{tide}}(\lambda, \phi, t)$$

* 若需转换为 WGS84 几何椭球高 $h_{\text{WGS84}}$：
  $$h_{\text{WGS84}} = H_{\text{EGM2008}} + N_{\text{EGM2008}}(\lambda, \phi)$$
  （其中 $N_{\text{EGM2008}}$ 从项目内置的 `us_nga_egm08_25.tif` 中直接双线性插值提取）。

---

## 🚀 快速上手 (Quick Start)

### 1. 环境准备 (支持一键初始化)
在项目根目录下双击运行 `setup_env.bat`，脚本将自动基于 Python 3.11 构建独立的 `.venv` 虚拟环境并配置完整依赖。

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
系统提供了易用的 `cli.py` 脚本，可直接用于自动化工作流：

* **单点时序预测**：
  ```bash
  python cli.py single --lon 122.0 --lat 31.0 --start "2026-09-10 00:00:00" --end "2026-09-11 00:00:00" --step 1h --output output.csv
  ```
* **批量表格计算**：
  ```bash
  python cli.py batch --input points.csv --lon-col longitude --lat-col latitude --time-col datetime --output batch_out.csv
  ```

### 4. Python API 代码集成
```python
from core.tide_engine import FESTidePredictor
from core.datum_engine import DatumTransformer

# 初始化解算引擎
predictor = FESTidePredictor()
transformer = DatumTransformer()

# 预测长江口未来 24 小时潮位
df = predictor.predict_series(
    lon=122.0, lat=31.0,
    start_time="2026-09-10 00:00:00",
    end_time="2026-09-11 00:00:00",
    freq="1h",
    constituents="all"
)

# 转换至 EGM2008 绝对海拔高
egm_tide, mdt = transformer.convert_msl_to_egm2008(df['tide_total_m'].values, 122.0, 31.0)
df['h_egm2008_m'] = egm_tide
print(df[['datetime', 'tide_total_m', 'h_egm2008_m']].head())
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
├── data/
│   └── geoid/
│       └── us_nga_egm08_25.tif     # NGA EGM2008 2.5分全球大地水准面栅格 (76.8MB)
├── core/                           # 核心计算包
│   ├── tide_engine.py              # FES2022b 局部加速与高精度潮位预测
│   ├── datum_engine.py             # MDT / EGM2008 / WGS84 基准转换
│   └── utils.py                    # 预设港口、坐标规范化与报表导出
├── gui/                            # PyQt6 桌面应用包
│   ├── main_window.py              # 桌面主窗口与双选项卡实现
│   ├── chart_widget.py             # Matplotlib 交互式波形组件
│   ├── settings_dialog.py          # 数据源可视化配置弹窗
│   └── styles.py                   # 扁平科技感深色 QSS 样式表
└── tests/                          # 自动化单元与集成测试
    └── test_engines.py             # 核心引擎全流程检验测试
```

---

## 📚 引用与致谢 (Citations & Acknowledgements)

如果您在学术研究、科学论文或工程报告中使用了本软件，请致谢并引用以下数据源：

1. **FES2022b Tide Model**:
   > *"The FES2022 Tide product was funded by CNES, produced by LEGOS, NOVELTIS and CLS and made freely available by AVISO."* (DOI: `10.24400/527896/a01-2024.004`)
2. **CNES-CLS22 MDT**:
   > *"The Mean Dynamic Topography CNES-CLS22 was produced by CLS and CNES."* (DOI: `10.24400/527896/a01-2023.003`)
3. **EGM2008 Geoid**:
   > Pavlis, N. K., Holmes, S. A., Kenyon, S. C., & Factor, J. K. (2012). The development and evaluation of the Earth Gravitational Model 2008 (EGM2008). *Journal of Geophysical Research: Solid Earth*, 117(B4).
