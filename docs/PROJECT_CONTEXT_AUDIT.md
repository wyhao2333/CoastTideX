# CoastTideX 全项目完整上下文与系统审计报告
# Complete Project Context, Architecture, Methodology & Code Change Audit

> [!WARNING]
> **历史审计归档说明 / Historical Archival Notice**:
> 本文档为 CoastTideX v1.4 / v1.5 Beta 阶段的历史审计与全景演进归档文档。
> 文档中记录的部分路径、分支状态、暂定参数与早期临时方案仅供历史追溯与科学审计。
> 最新系统功能、数据依赖关系与开发规范请以项目根目录下的 `README.md`、`README_EN.md`、`CHANGELOG.md` 及 `docs/DEVELOPMENT_GUIDE.md` 为准。

**文档密级/属性**：内部研发与科学审计文档 (Internal R&D and Scientific Audit Document)  
**系统名称**：CoastTideX 全球海岸带高精度潮位模拟与高程基准转换系统  
**当前版本阶段**：**CoastTideX v1.5 Beta (Controlled Real-FES / Real-Intertidal Functional & Scientific Prototype)**  
**文档生成时间**：2026-09-16 01:00:00 UTC+8  
**工作区根目录**：`I:\Test_tide_model`  
**Git 当前分支**：`test/v1.5-beta-real-fes`  
**Git 最新提交**：`cab590fac13b6cc04207c1b4b311f039958022d8`  
**系统架构师 / 作者**：王宇浩 (Wang Yuhao)  
**开源授权协议**：MIT License  

---

## 目录 (Table of Contents)

1. [项目概况与审计范围 (Executive Overview & Audit Scope)](#1-项目概况与审计范围-executive-overview--audit-scope)
2. [科学理论体系与核心数学公式 (Scientific Foundation & Methodology)](#2-科学理论体系与核心数学公式-scientific-foundation--methodology)
   - 2.1 FES2022b 原生非结构三角形网格与 34 分潮调和叠加
   - 2.2 双重水准面 Hybrid MDT 四大垂直基准转换体系
   - 2.3 潜在天文潮淹没频率物理定义与快速 CCDF 算法
   - 2.4 自适应四叉树控制网格与拓扑连通防护
   - 2.5 潮间带目标感知自适应细分 (Target-Aware Refinement)
3. [核心架构与两阶段解耦设计 (Architecture & Two-Stage Decoupled Pipeline)](#3-核心架构与两阶段解耦设计-architecture--two-stage-decoupled-pipeline)
   - 3.1 水动力长波场与 10m 微地形尺度解耦
   - 3.2 Stage 1: 控制网格 FES 解算与持久化 NetCDF4 Tide Cache
   - 3.3 Stage 2: 基于缓存的流式分块淹没频率解算 (零 FES 调用)
   - 3.4 单瓦片失败隔离机制与断点恢复清单状态机
4. [全生命周期代码变更全景记录 (Complete Code Evolution & Git History)](#4-全生命周期代码变更全景记录-complete-code-evolution--git-history)
   - 4.1 Git 历史提交全谱系追溯 (v1.0 至 v1.5 Beta)
   - 4.2 关键里程碑演进与重大架构重构详析
5. [全模块与代码文件逐一审计 (Comprehensive Module & Code Audit)](#5-全模块与代码文件逐一审计-comprehensive-module--code-audit)
   - 5.1 核心算法层 (`core/`)
   - 5.2 图形界面与用户交互层 (`gui/` & `app.py`)
   - 5.3 命令行与批处理工具 (`cli.py` & `scripts/`)
   - 5.4 自动化测试与 CI 流水线 (`tests/` & `.github/workflows/`)
   - 5.5 配置体系与底层数据资产 (`config.yaml` & `data/`)
6. [受控真实 FES2022b 科学验证指标 (Empirical Scientific Validation Metrics)](#6-受控真实-fes2022b-科学验证指标-empirical-scientific-validation-metrics)
   - 6.1 Level 1 全球典型岸段原生网格散点冒烟实测
   - 6.2 Level 3 Direct Sampled-Pixel FES Oracle 真值对比与残差分解
   - 6.3 Level 3b Tide-Cache 阶段解耦序列化保真度门禁
   - 6.4 Level 4 2024 全年 17,568 步长时序可行性与内存安全性
   - 6.5 Level 5 瓦片切缝连续性与重合拼合实测
   - 6.6 断点恢复零冗余与参数防篡改安全验证
7. [数据资产与掩膜科学语义审计 (Data Assets & Mask Semantics Audit)](#7-数据资产与掩膜科学语义审计-data-assets--mask-semantics-audit)
   - 7.1 本地 20.56 GB FES2022b 数据包结构
   - 7.2 原生非结构网格 vs 规则外推网格 (.nc.xz 压缩限制与禁用决策)
   - 7.3 `mask_fes2022B.nc` 四分类语义与 Hybrid MDT Source Mask 的严格解耦
8. [系统边界条件、已知局限性与项目行为铁律 (Boundaries, Limitations & Iron Rules)](#8-系统边界条件已知局限性与项目行为铁律-boundaries-limitations--iron-rules)
   - 8.1 核心铁律：GitHub Actions CI 绿标确认准则
   - 8.2 项目专属 Python 虚拟环境准则
   - 8.3 科学语义边界：固定地形纯静水天文潮潜在淹没
   - 8.4 当前版本成熟度定级：v1.5 Beta 严禁宣称 Global Production Ready

---

## 1. 项目概况与审计范围 (Executive Overview & Audit Scope)

### 1.1 研发背景与应用场景
**CoastTideX** 是一款专为**海洋工程、海岸带高分辨率卫星遥感、大地测量基准统一与滨海潮间带水文/生态建模**设计的桌面与命令行系统。

传统全球潮波解算多基于 $1/16^\circ$ 或 $1/30^\circ$ 规则方格网，在面对复杂蜿蜒的峡湾、群岛与浅滩时存在显著的“阶梯锯齿误差”；同时，传统海洋水文模型输出的潮高基于局部平均海平面 (MSL)，与陆地测绘通用的大地水准面（如 EGM2008）或空间几何椭球面（如 WGS84）脱节，导致海陆过渡带高程基准不统一。

CoastTideX 解决了上述痛点：
1. **动力学基底**：直读法国 CNES/AVISO 最新一代 **FES2022b 全球海洋潮汐模型原生非结构有限元三角形网格 (LGP2)**；
2. **基准统一**：集成 CNES-CLS22 全球平均动态地形 (MDT) 与 NGA EGM2008 超高精度大地水准面，实现 MSL 到 EGM2008/WGS84 的严密双向转换；
3. **栅格与批处理**：针对全球 10m/30m 沿海 DEM，研发了**自适应四叉树控制网格**与**二阶段持久化 Tide Cache 批处理引擎**，攻克千万至亿级像元长时序潮位反演的“算力爆炸”难题。

### 1.2 审计范围与原则
本次代码与上下文审查覆盖 `I:\Test_tide_model` 仓库的全部核心模块、配置文件、测试套件与技术报告，严格执行以下原则：
- **第一手源码读取**：逐行检视 Python 核心算法与 Markdown 文档，杜绝凭记忆模糊概括；
- **科学定义严格保真**：对物理量定义、基准转换公式、掩膜类别代码进行严密核对；
- **Git 演进完整复盘**：追溯全部 32 个主要 Commit 的演化路径与架构决策；
- **科研数据绝对只读**：严禁修改或删除本地 20.56 GB FES 数据集及重力场栅格。

---

## 2. 科学理论体系与核心数学公式 (Scientific Foundation & Methodology)

### 2.1 FES2022b 原生非结构三角形网格与 34 分潮调和叠加
在任意空间坐标 $(\lambda, \phi)$ 与时刻 $t$，系统采用调和分析第一性原理进行瞬时天文潮高计算：

$$\eta(\lambda, \phi, t) = \sum_{i=1}^{34} f_i(t) \cdot H_i(\lambda, \phi) \cdot \cos\left( \omega_i t + V_{0,i}(t_0) + u_i(t) - g_i(\lambda, \phi) \right) + h_{\text{LP}}(t)$$

- **分潮集合 (34 Constituents)**：
  - 8 大主分潮：$M_2, S_2, K_1, O_1, N_2, K_2, P_1, Q_1$；
  - 浅海高阶非线性分潮：$M_4, M_6, M_8, MN_4, MS_4, S_4, MKS_2$ 等；
  - 长周期平衡潮：$M_m, M_f, MS_f, S_a, S_{sa}$ 等。
- **高阶有限元插值 (LGP2)**：
  FES2022b 采用非结构三角形单元上的二阶勒让德多项式基函数（LGP2，每个三角形包含 6 个高阶自由度），在海陆分界线处实现网格尺度平滑过渡（开阔大洋数十公里，近岸陆架收敛至数百米）。
- **节点调制 ($f_i, u_i$)**：
  严密解算月球升交点 18.61 年周期摄动调制，由底层 C 库 `pyfes` 内部时间常数引擎保证高精度。

### 2.2 双重水准面 Hybrid MDT 四大垂直基准转换体系
在传统海洋测绘中常将 $\text{Tide} + \text{MDT}$ 直接视为 EGM2008 高程，**这在物理上是不严密的**。法国 CNES-CLS22 MDT 模型本质上是一个混合基准模型（Hybrid Model）：
1. **全球开阔大洋**：采用 **GOCO06s 卫星重力大地水准面**作为参考零面；
2. **地中海与黑海半封闭海域**：采用超高阶局部重力场模型 **EIGEN-6C4 ($d/o=2190$)** 作为参考零面。

由于 GOCO06s 与 EGM2008 在全球范围内存在 $-6.63\text{m} \sim +6.79\text{m}$ 的物理起伏差异，CoastTideX 建立了严密的四大垂直基准转换链：

```
[瞬时潮位 Tide_MSL]
        │ + MDT_CLS22
        ▼
[相对 MDT 原始基准面海面高 H_MDT_REF]
        │ + ΔN (大地水准面高差改正: GOCO06s 或 EIGEN-6C4 - EGM2008)
        ▼
[相对 EGM2008 大地水准面海拔正高 H_EGM2008]
        │ + N_EGM2008 (NGA 2.5' 栅格双线性插值起伏高)
        ▼
[WGS84 三维空间几何椭球高 h_WGS84]
```

数学公式表述：
$$H_{\text{MDT-REF}}(\lambda, \phi, t) = \text{Tide}_{\text{MSL}}(\lambda, \phi, t) + \text{MDT}_{\text{CLS22}}(\lambda, \phi)$$
$$H_{\text{EGM2008}}(\lambda, \phi, t) = H_{\text{MDT-REF}}(\lambda, \phi, t) + \Delta N(\lambda, \phi)$$
$$h_{\text{WGS84}}(\lambda, \phi, t) = H_{\text{EGM2008}}(\lambda, \phi, t) + N_{\text{EGM2008}}(\lambda, \phi)$$

- 全球大洋区：$\Delta N(\lambda, \phi) = N_{\text{GOCO06S}}(\lambda, \phi) - N_{\text{EGM2008}}(\lambda, \phi)$；
- 地中海/黑海区：$\Delta N(\lambda, \phi) = N_{\text{EIGEN-6C4}}(\lambda, \phi) - N_{\text{EGM2008}}(\lambda, \phi)$。在欧陆混合区，地中海点严格将 `h_goco06s_m` 赋为 `NaN`，杜绝基准伪造。

### 2.3 潜在天文潮淹没频率物理定义与快速 CCDF 算法
对于任意给定空间位置像元 $(x, y)$，其在时间区间 $[t_{\text{start}}, t_{\text{end}}]$（离散采样步长 $\Delta t$）内的潜在天文潮淹没频率定义为瞬时水面高程大于地表高程的时间占比：

$$\text{Freq}_{\text{inundation}}(x, y) = P\left( H(x, y, t) > z(x, y) \right) = \frac{1}{K}\sum_{k=1}^K \mathbb{I}\left( H(x, y, t_k) > z(x, y) \right) \times 100\%$$

- **算力瓶颈与突破**：直接计算需遍历 $K$ 个时间点进行逐一比较。系统在控制节点预先对时间序列 $H_t$ 进行一次升序排序（$O(K \log K)$），在反演像元高程 $z$ 时，调用 `np.searchsorted` 执行二分查找：
  $$\text{Index} = \text{searchsorted}(H_{\text{sorted}}, z)$$
  $$P(H > z) = \frac{K - \text{Index}}{K}$$
  单像元求值复杂度瞬间降为 $O(\log K)$，为千万级像元的高效反演提供了算法基础。

### 2.4 自适应四叉树控制网格与拓扑连通防护
1. **物理尺度匹配**：沿海 10m/30m 高程起伏剧烈，但近海天文潮面波长达数百公里，在数公里范围内极其平缓。因此，系统无须逐像元解算 FES，而是在空间构建自适应四叉树控制网格（初始间距通常为 4km）。
2. **递归细分准则**：在包含水域有效像元的单元中，评估四个角点在关键高程上的淹没频率差异 $\Delta F$。若 $\Delta F > \text{tolerance}$（默认 1.0 百分点）且单元尺寸大于最小间距（默认 500m），则递归四等分细分。
3. **双线性空间插值**：在四叉树叶子单元内部，像元 $(x, y)$ 的淹没频率由四个角点预计算的频率通过双线性插值平滑获得：
   $$F(x, y) = (1 - u)(1 - v)F_{00} + u(1 - v)F_{10} + (1 - u)vF_{01} + uvF_{11}$$
4. **有效掩膜拓扑连通防护 (Topology Guard)**：
   分析输入 DEM 的有效像元/NoData 掩膜连通域。当一个控制单元跨越了陆地 NoData 屏障（如狭窄半岛、岬角阻隔的两个独立海湾）时，系统自动识别拓扑阻断，禁止跨屏障双线性插值，有效防止潮位“穿墙泄漏”。

### 2.5 潮间带目标感知自适应细分 (Target-Aware Refinement)
在 v1.5 中，针对狭长沙滩与潮间带细分过度的问题，引入了 `target_mode="intertidal"`：
- 传统自适应网格只要相邻节点有潮差即不断细分，导致在干燥陆地边缘产生大量无意义的 500m 节点；
- 目标感知模式下，仅当**单元内 DEM 的实际高程区间落在潮位波动区间内**（即像元属于活跃潮滩/沙滩）时，才允许深入细分；若单元内像元均为深水或极高陆地，则直接终止细分，节点数大幅削减 40%~60%。

---

## 3. 核心架构与两阶段解耦设计 (Architecture & Two-Stage Decoupled Pipeline)

### 3.1 水动力长波场与 10m 微地形尺度解耦
CoastTideX 系统全貌如下图所示：

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                          CoastTideX 主调度入口 (GUI / CLI)                  │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                ┌──────────────────────┴──────────────────────┐
                ▼                                             ▼
┌──────────────────────────────┐              ┌──────────────────────────────┐
│  单景影像引擎 (v1.4 Raster)  │              │  批量潮间带引擎 (v1.5 Alpha) │
│  - 单时刻水面快照 (Snapshot) │              │  - 目录扫描快照 (ScanSnapshot)│
│  - 单景 DEM 淹没频率         │              │  - 顺序瓦片流水线推进        │
└───────────────┬──────────────┘              └───────────────┬──────────────┘
                │                                             │
                │     ┌───────────────────────────────────────┘
                ▼     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│             Stage 1: 控制网格生成与 Tide Cache 序列化 (Tide Stage)           │
│  1. DEM 空间元数据与有效掩膜提取 (RasterInfo & BBox)                        │
│  2. 自适应四叉树构建与潮间带目标感知细分 (Adaptive Quadtree Refinement)     │
│  3. 批量多节点 FES2022b 34 分潮长时序解算 (FESTidePredictor)                │
│  4. 静态垂直基准偏移量绑定 (DatumTransformer: MSL / EGM2008)               │
│  5. 持久化原子写入 NetCDF4 Tide Cache (*_tide.nc + CACHE_COMPLETE 标记)     │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼ (磁盘持久化解耦，零 FES 重复开销)
┌─────────────────────────────────────────────────────────────────────────────┐
│             Stage 2: 基于缓存的高分辨率像元流式反演 (Inundation Stage)      │
│  1. 快速检视与读取 NetCDF Cache 头信息 (轻量元数据校验)                     │
│  2. 全要素兼容性签名防篡改校验 (Signature Verification)                    │
│  3. 2,000 步流式分块累加 (Chunked Accumulation, 内存封顶 < 100 MB)          │
│  4. 像元级 CCDF 检索与双线性空间平滑插值 (Bilinear Interpolation)           │
│  5. 512x512 窗口流式写入 GeoTIFF (*_inundation.tif + *_qc.tif)              │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│            任务清单状态机与故障隔离 (Batch Manifest & Safety State)          │
│  - PENDING -> TIDE_RUNNING -> TIDE_READY -> FREQUENCY_RUNNING -> DONE       │
│  - 单瓦片崩溃隔离 (FAILED 不中断批处理)                                     │
│  - ExistingOutputPolicy: RESUME (零冗余) / ERROR_IF_EXISTS / OVERWRITE      │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 Stage 1: 控制网格 FES 解算与持久化 NetCDF4 Tide Cache
- **缓存文件定义**：每个瓦片对应一个 `[tile_stem]_tide.nc`；
- **存储维度与变量**：
  - 维度：`nodes`（控制节点数，通常 300~1,500），`time`（采样时间步长数，全年为 17,568）；
  - 节点坐标：`node_x`, `node_y`, `node_lon`, `node_lat`；
  - 潮位数据：`tide_msl`（原始 FES MSL 潮位序列），`tide_target`（施加 MDT+$\Delta N$ 改正后的目标基准潮位序列）；
  - 拓扑描述：`cells`（四叉树叶节点单元角点索引与空间范围）；
  - 完整性标记：全局属性 `CACHE_COMPLETE = "true"`。只有当文件完全写毕时才打上该标记，未完成的半成品文件在后续读取时被自动识别并拒绝。

### 3.3 Stage 2: 基于缓存的流式分块淹没频率解算 (零 FES 调用)
- **解耦优势**：Stage 2 彻底脱离了对底层的 `pyfes` C 扩展与 3.95 GB 网格文件的依赖，可在任意纯净 Python 环境下极速运行；
- **流式分块内存硬防护**：在全年 17,568 步情况下，一次性将所有节点的潮位展开为像元级浮点矩阵会导致几个 GB 的瞬时内存。Stage 2 引入以 2,000 个时间步为单位的分块累加（Chunked Accumulation），像元频率累加在原地完成，峰值内存被牢牢限制在 **100 MB 以内**。

### 3.4 单瓦片失败隔离机制与断点恢复清单状态机
- **失败隔离**：单个损坏的 GeoTIFF 瓦片（如破损文件头、全 NoData、超出地理界限）被 `try...except Exception` 独立包裹，将其错误信息写入清单的 `last_error` 字段，标记为 `FAILED`，程序绝不崩溃中断，立即平稳调度下一瓦片；
- **状态机跃迁**：
  $$\text{PENDING} \longrightarrow \text{TIDE\_READY (Stage 1 完成)} \longrightarrow \text{DONE (Stage 2 完成)}$$
- **断点恢复 (RESUME)**：重新运行批处理时，若已存在完整产物且签名匹配，直接标记 `SKIPPED`；若已存在完整的 `_tide.nc` 缓存但缺少 GeoTIFF，直接跳过 Stage 1，从 Stage 2 极速接续。

---

## 4. 全生命周期代码变更全景记录 (Complete Code Evolution & Git History)

### 4.1 Git 历史提交全谱系追溯 (v1.0 至 v1.5 Beta)

以下为使用 `git log` 实测提取的完整 32 个主要提交历史：

| Commit Hash | 提交日期 | 作者 | 提交信息与核心改动说明 |
| :--- | :---: | :---: | :--- |
| `70c312d` | 2026-09-10 | wyhao2333 | **feat: Initial commit for CoastTideX v1.0**<br>首发版本，包含 FES2022b 原生解算、MDT/EGM2008 基准转换与 PyQt6 基础 GUI。 |
| `cb7db00` | 2026-09-10 | wyhao2333 | **fix: PyQt6 与 pyfes C++ 运行时导入冲突**<br>解决 Windows 平台动态链接库加载冲突，图表组件集成中文字体渲染。 |
| `e80f4f4` | 2026-09-10 | wyhao2333 | **fix: 规范化 Windows 批处理脚本**<br>统一 `run_gui.bat` 与 `setup_env.bat` 为 CRLF 换行符与干净 ASCII 编码。 |
| `711fb84` | 2026-09-11 | wyhao2333 | **feat(v1.1): 科学基准重构与 Delta-N 改正**<br>引入 $\Delta N = N_{\text{GOCO06s}} - N_{\text{EGM2008}}$ 改正项，支持时区处理与内存规格说明。 |
| `ff3cb0a` | 2026-09-11 | wyhao2333 | **fix(core,gui): 修复 10 项大地测量与栅格对齐关键缺陷 (v1.1.1)**<br>解决栅格半像元 1.25′ 位移偏差，解耦测试套件。 |
| `9597248` | 2026-09-11 | wyhao2333 | **fix(ci): 使 pyfes 导入可选以兼容无编译环境 CI**<br>在 GitHub Actions Linux 干净无 C++ 扩展环境下优雅降级。 |
| `0f3a00c` | 2026-09-11 | wyhao2333 | **fix(ci): 解耦闭合测试对 MDT 大文件的依赖**<br>抑制 `affine` 弃用告警，保证离线自动化测试稳定。 |
| `adee79c` | 2026-09-11 | wyhao2333 | **fix(env): 确保 setup_env.bat 创建干净独立的 .venv**<br>杜绝环境混用，实现直接 pip 包安装。 |
| `438705c` | 2026-09-11 | wyhao2333 | **fix(core,gui): 窗口纵向缩放解除锁定与地中海基准解耦 (v1.2)**<br>引入 `QScrollArea` 自适应缩放，识别地中海/黑海 EIGEN-6C4 基准，解耦纯 MSL 模式。 |
| `ee21804` | 2026-09-11 | wyhao2333 | **chore(release): 升级版本号至 v1.2 并更新手册文档**<br>完善用户手册、采样步长国际文献指南。 |
| `7329f42` | 2026-09-12 | wyhao2333 | **feat(release): 升级版本至 v1.3 (整年预测与时间分块)**<br>新增整年 17,568 样本点半开区间预测、5,000 点时间分块流式解算、Ray-casting 闭合多边形判定与潮滩 DEM 潜在淹没频率工具。 |
| `ad67ff6` | 2026-09-12 | wyhao2333 | **fix(ci,core): 移除 core 对 matplotlib 依赖**<br>用纯 NumPy 射线法重写多边形判定，排除大体积栅格进入 Git 库。 |
| `f43db8c` | 2026-09-12 | wyhao2333 | **feat(release): 升级版本至 v1.4 (空间栅格潮位引擎)**<br>新增 `RasterTideEngine`，支持水面快照 (Snapshot) 与自适应四叉树沿海 DEM 淹没分析。 |
| `961af7c` | 2026-09-12 | wyhao2333 | **fix(ci,tests): CI 流水线补充 PyQt6 并守卫 pyfes 依赖**<br>解决无图形界面 CI 环境下的 GUI 与底层 C 扩展导入问题。 |
| `612d8e6` | 2026-09-12 | wyhao2333 | **docs: 新增项目核心铁律与行为规范 (AGENTS.md / GEMINI.md)**<br>确立 GitHub 推送必等 CI 绿标与必须使用项目 `.venv` 两大核心准则。 |
| `d732b2e` | 2026-09-12 | wyhao2333 | **fix(v1.4): 科学稳定化发布候选版 (41 项测试全绿)**<br>强化自适应控制网格细分终止条件与拓扑连通防护。 |
| `5c8ea4e` | 2026-09-12 | wyhao2333 | **fix(ci,tests): Linux CI 补充 libegl1/libgl1 动态库**<br>解决 GitHub Actions 无头环境下 PyQt6 QtGui 缺失底层共享库报错。 |
| `fba17e7` | 2026-09-13 | wyhao2333 | **fix(raster,core,tests): 生产化收口 v1.4 栅格引擎**<br>硬化边界细分逻辑，完善多边形与经度跨界测试。 |
| `fc6b006` | 2026-09-13 | wyhao2333 | **fix(v1.4): 内存硬防护与真值预言机最终收口 (v1.4 RC)**<br>增加 `max_in_memory_control_nodes` 硬防护，引入抽样像元真值预言机，测试扩充至 50/50。 |
| `b95943e` | 2026-09-14 | wyhao2333 | **feat(v1.5): 引入持久化 NetCDF Tide Cache 与目标感知细分**<br>开启 v1.5 研发：实现 Stage 1/Stage 2 解耦架构与 `target_mode="intertidal"`。 |
| `9465d20` | 2026-09-14 | wyhao2333 | **feat(v1.5): 新增批量栅格引擎、失败隔离与恢复清单**<br>实现 `BatchRasterEngine`，多文件顺序推进与单瓦片异常隔离。 |
| `70d5cca` | 2026-09-14 | wyhao2333 | **feat(gui): 新增批量潮间带栅格专用选项卡与 CLI 批处理命令**<br>主窗口扩展 Tab 4，增加 `raster batch` 命令行接口。 |
| `dda7940` | 2026-09-14 | wyhao2333 | **test(v1.5): 新增批量栅格与 Tide Cache 综合测试套件**<br>单元测试扩充至 61 项，全面覆盖往返一致性与零 FES 硬性验证。 |
| `7944577` | 2026-09-14 | wyhao2333 | **docs(v1.5): 输出 FES 本地审查报告与批量引擎技术文档**<br>形成 `docs/FES2022B_LOCAL_AUDIT_V1_5.md`，明确禁用 XZ 压缩外推回退。 |
| `03628f4` | 2026-09-14 | wyhao2333 | **feat(v1.5): 硬化 ExistingOutputPolicy 与 Stage 2 缓存一致性**<br>统一输出策略枚举，强化防篡改保护与 Stage 2 零冗余执行。 |
| `8dac9b5` | 2026-09-14 | wyhao2333 | **feat(v1.5): Round 2 队列 UX、异步扫描与内存硬防护**<br>解决扫描卡死，引入 `ScanSnapshot` 静态队列快照与 Stage 2 分块累加。 |
| `0867461` | 2026-09-14 | wyhao2333 | **test(v1.5): Mock 静态基准偏移以兼容 CI 纯净环境**<br>确保 `test_v15_hardening_r2.py` 在缺失大文件重力场栅格的 CI 环境下顺利执行。 |
| `e943967` | 2026-09-14 | wyhao2333 | **fix(v1.5): Round 2.1 掩膜语义澄清、安全收口与帮助手册清理**<br>澄清 FES 掩膜与 MDT 掩膜科学区别，清理无效默认路径，作者姓名更新为中文“王宇浩”。 |
| `7522857` | 2026-09-15 | wyhao2333 | **fix(v1.5): 恢复 EIGEN Delta-N 规范默认路径与元数据修正**<br>极小范围收口，规整 `config.yaml` 默认路径。 |
| `919b3f8` | 2026-09-15 | wyhao2333 | **test(v1.5): 构建受控真实 FES 验证工具链与自动化测试**<br>新增 `scripts/validate_v15_beta_real_fes.py` 与 `tests/test_v15_beta_validation_harness.py`。 |
| `bf6adc5` | 2026-09-15 | wyhao2333 | **docs(v1.5): 输出受控真实 FES 潮间带 Beta 验证报告**<br>记录包含章节 A 至 S 的全面真实验证报告 `docs/V1_5_BETA_REAL_FES_VALIDATION.md`。 |
| `cab590f` | 2026-09-15 | wyhao2333 | **ci: 扩展 CI 触发分支规则覆盖 test/\*\***<br>确保测试分支推送时自动触发 GitHub Actions 并实时监听绿标。 |

---

## 5. 全模块与代码文件逐一审计 (Comprehensive Module & Code Audit)

### 5.1 核心算法层 (`core/`)

#### 1. `core/utils.py` (通用工具与基础支撑)
- **主要职责**：跨平台绝对/相对路径解析、经纬度严密校验、严格单调时区时间轴构建、经验互补累积分布 (CCDF) 计算与配置文件加载；
- **关键函数清单**：
  - `get_project_root() -> str` / `get_app_root() -> str` / `get_resource_root() -> str`：智能区分 PyInstaller `_MEIPASS` 打包运行与源码运行；
  - `resolve_project_path(p: str, prefer_resource: bool = False) -> str`：将相对路径解析为存在的物理绝对路径；
  - `validate_coordinates(lon: float, lat: float) -> tuple[float, float]`：阻断 NaN/Inf 坐标及超出 $[-90, +90]$ 纬度；
  - `build_time_index(start, end, freq, source_tz, inclusive) -> tuple`：构建严格物理连续且支持夏令时安全过渡的 DatetimeIndex；
  - `compute_inundation_frequency(water_levels, terrain_elevations, as_percentage=True) -> np.ndarray`：向量化二分查找计算潜在淹没概率；
  - `normalize_longitude(lon, to_360=False)`：在 $[-180, +180]$ 与 $[0, 360)$ 经度坐标系间精准无缝归一化。

#### 2. `core/datum_engine.py` (垂直基准转换引擎)
- **主要职责**：管理 MDT_CLS22、EGM2008 及 GOCO06s/EIGEN-6C4 重力水准面差值栅格的双线性空间插值与基准转换；
- **核心类与函数**：
  - `class DatumTransformer`：
    - `__init__(mdt_path, egm2008_path, delta_n_goco_path, delta_n_eigen_path, ...)`：自动按需加载模型；
    - `get_static_datum_offsets(lons, lats, target, strict=False) -> dict`：快速提取静态偏移场 $\text{MDT} + \Delta N$；
    - `convert_tide_datums(tide_msl_m, lons, lats, target_datum='both', strict=False) -> dict`：实现四维基准全量转换，严格保持地中海区 `h_goco06s_m` 为 NaN；
    - `get_mdt(lons, lats)` / `get_egm2008_geoid_height(lons, lats)` / `get_delta_n(lons, lats)`：双线性插值获取各层大地水准面参数；
  - `_points_in_polygon(lons, lats, poly_verts)`：纯 NumPy 向量化射线交叉法 (PNPOLY)，零外部 GUI/绘图库依赖；
  - `get_mdt_reference_geoid(lon, lat)`：权威掩膜优先判定 + 地中海/黑海高精闭合多边形几何保底判别。

#### 3. `core/tide_engine.py` (FES 潮位解算核心)
- **主要职责**：基于 `pyfes` 底层 C 扩展调用 FES2022b 原生非结构三角形网格 (LGP2) 解算高保真潮汐；
- **核心类与函数**：
  - `class FESTidePredictor`：
    - `__init__(ns_grid_path)`：初始化并校验网格文件存在性；
    - `_get_model(bbox, constituents)`：空间包围框动态拓扑缓存，避免频繁重新解析 3.95 GB NetCDF 文件；
    - `predict_series(lon, lat, start_time, end_time, freq, ...)`：单点连续时序预测，内嵌 5,000 点自适应时间分块（Time-Chunking）流式解算；
    - `predict_year(lon, lat, year, freq, ...)`：整年预测快捷入口，严格按半开区间 $[start, end)$ 保真生成精确点数（如 2024 闰年 17,568 点）；
    - `predict_points_period(lons, lats, start_time, end_time, ...)`：多空间离散点批量时序预测（自适应控制网格解算的核心底座）；
    - `predict_batch(df, lon_col, lat_col, time_col, ...)`：外部离散观测点表格批量解算（带 $5^\circ \times 5^\circ$ 空间网格聚类加速）。

#### 4. `core/raster_engine.py` (单景空间栅格潮位引擎)
- **主要职责**：处理单景 GeoTIFF 空间二维影像的水面快照解算与自适应控制网格沿海 DEM 淹没频率反演；
- **核心数据结构与类**：
  - `class ControlNode`：自适应控制网格节点，存储地理坐标、排序后的潮位数组及有效性质量位；
  - `class QuadCell`：四叉树单元，管理四角节点索引、子单元指针及分裂决策；
  - `class RasterTideEngine`：
    - `calculate_snapshot_raster(input_raster, output_raster, timestamp, datum)`：像元中心精确对齐，512×512 窗口流式原子生成指定时刻的二维水面高程图；
    - `calculate_inundation_raster(dem_path, output_path, start_time, end_time, ...)`：自适应控制网格剖分 $\rightarrow$ 多节点批量 FES 解算 $\rightarrow$ 逐像元 CCDF 双线性空间插值 $\rightarrow$ 原子写入 Float32 频率图与 UInt8 QC 掩膜图；
    - `build_support_topology(...)` / `stream_inundation_frequency_interpolation(...)`：自适应四叉树构建与流式空间插值底层执行函数。

#### 5. `core/tide_cache.py` (Tide Cache 缓存管理与二阶段解耦)
- **主要职责**：控制网格节点时间序列的 NetCDF4 序列化与反序列化、元数据防篡改签名与基于缓存的 Stage 2 纯流式解算；
- **关键函数清单**：
  - `estimate_tide_cache_size(node_count, time_samples) -> dict`：精确预估缓存数组物理驻留内存；
  - `write_tide_cache(cache_path, raster_info, nodes, cells, spec, ...)`：原子写入 NetCDF4 格式的 `*_tide.nc`，成功后写入 `CACHE_COMPLETE=true`；
  - `is_cache_complete(cache_path) -> bool`：校验缓存文件是否存在且完全写毕；
  - `generate_tide_cache_signature(info, start_time, end_time, freq, datum, ...)`：基于 DEM 空间属性与解算参数计算唯一 SHA-256 签名，用于断点防篡改；
  - `validate_tide_cache_compatibility(cache_path, expected_spec)`：深度校验已有缓存是否与当前任务参数兼容（严格拦截错误步长复用）；
  - `calculate_inundation_from_tide_cache(cache_path, dem_path, output_path, ...)`：Stage 2 核心入口，**零 FES 重复调用**，2,000 步分块累加生成最终淹没图。

#### 6. `core/batch_raster_engine.py` (批量潮间带调度引擎)
- **主要职责**：文件夹级批量 GeoTIFF 自动化扫描、队列防竞态冻结、按序单瓦片推进调度、单文件异常隔离与清单状态机维护；
- **核心数据结构与类**：
  - `class ExistingOutputPolicy(Enum)`：`RESUME`（断点恢复）、`ERROR_IF_EXISTS`（存在报错）、`OVERWRITE`（强制覆盖）；
  - `class BatchManifest`：双格式（`batch_manifest.json` 与 `.csv`）实时持久化批量运行记录；
  - `class BatchRasterEngine`：
    - `scan_input_folder(input_folder) -> ScanSnapshot`：轻量读取 GeoTIFF 头信息，提取栅格尺寸、CRS、分辨率、修改时间并执行防竞态哈希冻结；
    - `run_batch(input_folder, output_folder, existing_policy, ...)`：批处理主调度器，控制 Stage 1 与 Stage 2 逐瓦片顺序推进，实现 100% 失败隔离。

---

### 5.2 图形界面与用户交互层 (`gui/` & `app.py`)

- **`app.py`**：应用程序主入口，处理高 DPI 缩放策略、全局异常捕获以及启动 `MainWindow`；
- **`gui/main_window.py`**：基于 PyQt6 的现代化深色桌面客户端，包含 4 大功能选项卡：
  - **Tab 1: 潮汐调和预测**（单点任意时段、整年 17,568 点快捷预测、四维垂直基准切换、时区动态重构）；
  - **Tab 2: 全球批量预测**（外部 CSV/Excel 点位表格上传、多列自动映射、空间网格加速解算）；
  - **Tab 3: 空间栅格潮位引擎**（单景 GeoTIFF 快照生成与 DEM 潜在天文潮淹没分析）；
  - **Tab 4: 批量潮间带栅格解算**（文件夹批量扫描、异步 Worker 扫描队列、ExistingOutputPolicy 策略单选框、任务进度条与实时流式日志卡片）；
- **`gui/chart_widget.py`**：基于 Matplotlib 的高性能动态波形组件，内嵌万级点自适应降采样算法（大跨度时自动稀疏渲染，放大后恢复高保真细节）与波峰/波谷潮时潮高极值标注卡；
- **`gui/settings_dialog.py`**：数据源与路径可视化配置中心，内嵌 `_deep_validate_file` 对 NetCDF 与 GeoTIFF 进行底层格式深层有效性探针探测；
- **`gui/manual_dialog.py`**：软件内置使用说明书对话框，集成科学原理、垂直基准换算公式、国际规范采样步长指南与作者版权声明；
- **`gui/styles.py`**：科技感扁平深色主题 QSS 样式表，针对高分屏进行了精细控件内边距与字体渲染优化。

---

### 5.3 命令行与批处理工具 (`cli.py` & `scripts/`)

- **`cli.py`**：全面支持无头服务器与生产流水线调用的多级命令行接口：
  - `cli.py single`：单点时序预测（支持 `--year` 整年模式或 `--start`/`--end` 自定义区间）；
  - `cli.py batch`：表格散点批量预测；
  - `cli.py raster snapshot`：单景空间水面快照反演；
  - `cli.py raster inundation`：单景 DEM 潜在淹没频率反演；
  - `cli.py raster batch`：文件夹级批量潮间带 DEM 流水线解算；
- **`scripts/calculate_inundation_raster.py`**：轻量级 CLI 薄封装，向后兼容早期单 DEM 淹没脚本调用；
- **`scripts/generate_delta_n.py`**：通用大地水准面高差残差场计算工具，基于 ICGEM 网格生成 $\Delta N = N_{\text{model}} - N_{\text{EGM2008}}$ 改正 GeoTIFF；
- **`scripts/validate_v15_beta_real_fes.py`**：v1.5 Beta 核心真实验证工具链，驱动本地 3.95 GB 原生 FES 与近岸地形执行从 Level 1 冒烟到 Level 5 切缝拼合的完整验收。

---

### 5.4 自动化测试与 CI 流水线 (`tests/` & `.github/workflows/`)

项目维护了覆盖全系统的 6 大测试套件，在无图形界面和无大文件数据环境下具备完整的 Mock/合成预言机防御体系：
1. **`tests/test_engines.py` (47 项测试)**：
   覆盖 FES 预测引擎、整年 17,568 样本点半开区间保真度、四维基准转换闭合性、地中海闭合多边形判定、射线法 PNPOLY、空间栅格快照像元中心对齐、自适应四叉树分裂与收敛、拓扑阻隔防泄漏等；
2. **`tests/test_batch_raster_v15.py` (14 项测试)**：
   覆盖批量文件夹扫描过滤、确定性排序、Tide Cache 序列化读写往返、Stage 2 零 FES 硬性验证、瓦片失败隔离、断点清单恢复；
3. **`tests/test_v15_hardening.py` (12 项测试)**：
   覆盖输出策略冲突规范化、内存预算评估、全要素签名算法、篡改检测与拦截；
4. **`tests/test_v15_hardening_r2.py` (18 项测试)**：
   覆盖 `ScanSnapshot` 静态快照冻结、异步扫描防竞态、NoData 冲突防御、Stage 2 分块累加内存保护；
5. **`tests/test_v15_r21_datasource_help.py` (15 项测试)**：
   覆盖 FES 外推掩膜四分类语义、Help/About 对话框文本合规性、作者姓名审查、设置对话框深层文件校验；
6. **`tests/test_v15_beta_validation_harness.py` (10 项测试)**：
   覆盖验证工具链环境审计、目标掩膜几何分类器（空、细长带、边界、大面积等）、合成代表性 DEM 生成、Direct Oracle 误差指标计算与门禁状态判定、切缝连续性计算。

**测试执行汇总**：本地专属虚拟环境实测 **`Ran 116 tests in 26.368s: OK` (116/116 全部通过，通过率 100%)**。

---

### 5.5 配置体系与底层数据资产 (`config.yaml` & `data/`)

- **`config.yaml`**：中央配置文件，严格管理数据路径、分潮默认参数、空间缓冲跨度、四叉树网格步长（初始 4000m，最小 500m，容差 1.0 pp）、内存硬防护阈值（50,000 节点）；
- **`data/geoid/README_GEOID.md`**：大地测量基准白皮书，详细记录了 NGA EGM2008、GOCO06s 及 EIGEN-6C4 模型的权威数学定义、ICGEM 下载链接与差值生成原理。

---

## 6. 受控真实 FES2022b 科学验证指标 (Empirical Scientific Validation Metrics)

在最新执行的受控真实 FES2022b 验证任务（Run ID: `run_20260915_032547`）中，系统取得了以下详实的经验科学指标：

### 6.1 Level 1 全球典型岸段原生网格散点冒烟实测

| 站点名称 | 经度 ($^\circ\text{E}$) | 纬度 ($^\circ\text{N}$) | 潮差极值区间 ($H_{\text{min}} \sim H_{\text{max}}$) | 样本有效率 | 单核耗时 | 判定状态 | 物理成因与地理特征 |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **中国长江口** | 122.000 | 31.000 | $-0.696\text{ m} \sim +0.926\text{ m}$ | 97 / 97 | 28.55s | **VALID** | 浅水开阔陆架混合潮，波形高度平滑守恒 |
| **中国珠江口** | 113.800 | 22.300 | $-0.993\text{ m} \sim +0.988\text{ m}$ | 97 / 97 | 29.16s | **VALID** | 亚热带喇叭形强潮海湾，波幅动力响应良好 |
| **中国渤海湾** | 119.000 | 38.500 | $-0.615\text{ m} \sim +0.323\text{ m}$ | 97 / 97 | 29.15s | **VALID** | 半封闭浅水盆地规则半日潮，相位延迟精准 |
| **地中海克里特** | 24.000 | 35.000 | $-0.023\text{ m} \sim +0.028\text{ m}$ | 97 / 97 | 29.81s | **VALID** | 典型地中海微潮区，潮差仅 5 cm，无异常噪声 |
| **北海荷兰海岸** | 4.000 | 52.500 | $-0.713\text{ m} \sim +0.673\text{ m}$ | 97 / 97 | 29.54s | **VALID** | 浅水陆架大潮区，动力学超前效应显著 |
| **大西洋哈特拉斯角** | -75.500 | 35.200 | $-0.346\text{ m} \sim +0.401\text{ m}$ | 97 / 97 | 29.89s | **VALID** | 开阔大洋近岸混合潮，极值匹配自然规律 |
| **澳大利亚大堡礁** | 146.000 | -18.000 | $\text{NaN}$ | 0 / 97 | 29.52s | **INVALID_ALL_NAN** | 极复杂珊瑚礁盘未覆盖，非结构网格边界安全截断 |
| **加拿大芬迪湾** | -65.000 | 45.000 | $\text{NaN}$ | 0 / 97 | 29.76s | **INVALID_ALL_NAN** | 极窄海湾顶部陆缘网格截断，系统捕获未崩溃 |

### 6.2 Level 3 Direct Sampled-Pixel FES Oracle 真值对比与残差分解
在有效近岸潮间带 DEM 范围内均匀抽取 100 个真实像元，绕过自适应控制网格与空间插值，直接调用 Native FES 解算 100 个像元的严密真值频率，与自适应插值频率比对：

- **有效比对像元**：100 / 100 ($100\%$)
- **平均偏差 (Mean Bias)**：$-0.0266\text{ pp}$
- **平均绝对误差 (MAE)**：**$0.1274\text{ pp}$**（远优于 $\le 0.50\text{ pp}$ 的门禁要求）
- **均方根误差 (RMSE)**：$0.3661\text{ pp}$
- **中位数绝对误差 (MedAE)**：$0.0000\text{ pp}$（超过 50% 的像元绝对误差为零）
- **95分位数误差 (P95)**：**$0.6626\text{ pp}$**（远优于 $\le 1.00\text{ pp}$ 的门禁要求）
- **最大绝对误差 (Max Error)**：**$2.5591\text{ pp}$**（优于 $\le 5.00\text{ pp}$ 的门禁要求）
- **门禁评级**：**STRONG PASS**

**Top 5 极值残差归因**：
最大误差像元（2.56 pp 与 1.33 pp）均严格分布于大潮高潮线附近的极端陡坡地貌边缘。在临界淹没水深处，微小的连续插值波动跨越了阶跃指示函数 $H > z$ 的判定阈值，这属于数值离散化的自然截断现象，自适应网格在全潮滩范围完整保持了水动力梯度。

### 6.3 Level 3b Tide-Cache 阶段解耦序列化保真度门禁
对比单步流式直接解算与 Stage 1 $\rightarrow$ NetCDF4 缓存 $\rightarrow$ Stage 2 解耦解算生成的 256,215 个公共像元：
- **最大绝对偏差 (Max Diff)**：**$0.000000\text{ pp}$**
- **99分位数偏差 (P99 Diff)**：**$0.000000\text{ pp}$**
- **判定结论**：**PASS (实现浮点零误差绝对保真)**

### 6.4 Level 4 2024 全年 17,568 步长时序可行性与内存安全性
- **单点全年连续时序实测**：17,569 个时间步（30min 步长），单核耗时仅 **$28.80\text{ 秒}$**；
- **全瓦片年潮位缓存预算**：500 控制节点 $\times$ 17,568 步 $\times$ 8 字节 $\approx 70.27\text{ MB}$（压缩后 NetCDF 体积 $\approx 35\text{ MB}$）；
- **Stage 2 内存硬防护**：2,000 步流式分块累加机制将像元级峰值 RAM 占用彻底封顶在 **$< 100\text{ MB}$**。

### 6.5 Level 5 瓦片切缝连续性与重合拼合实测
将整幅 DEM 垂直对半分开为 Tile A 与 Tile B，分别独立完成网格剖分与反演后水平拼接，与整幅直接反演结果作逐像元差分：
- **拼合平均绝对误差 (Composite MAE)**：$0.1121\text{ pp}$
- **拼合 95 分位数误差 (Composite P95)**：**$0.7024\text{ pp}$**（满足 $\le 1.00\text{ pp}$）
- **拼合最大绝对误差 (Composite Max)**：$6.6170\text{ pp}$（满足 $\le 10.0\text{ pp}$）
- **接缝阶跃跳变 P95**：$3.3199\text{ pp}$
- **判定结论**：**PASS**

### 6.6 断点恢复零冗余与参数防篡改安全验证
- **恢复零重复**：重复执行 RESUME 模式，任务耗时接近 0 秒，`completed: 0, skipped: 1`，跳过率 100%，前后产物 SHA-256 完全相同；
- **防篡改安全拦截**：篡改时间采样步长（30min 变为 1h），引擎在 RESUME 模式下通过兼容性签名瞬间捕获不匹配，安全拒绝复用损坏缓存，触发全量重新解算。

---

## 7. 数据资产与掩膜科学语义审计 (Data Assets & Mask Semantics Audit)

### 7.1 本地 20.56 GB FES2022b 数据包结构
本地 `I:\Test_tide_model\fes2022b` 资产严格审计明细如下：
1. `ocean_tide_non_structured/FES2022b_OceanTide_NSgrid.nc` (3.682 GB)：**原生非结构三角网格 (Native LGP2)**，包含 5,691,517 节点、11,056,490 三角形与全部 34 分潮，**CoastTideX 当前核心潮汐解算唯一驱动源**；
2. `mask_fes2022B.nc` (0.98 MB)：官方 $1/30^\circ$ 全球规则网格掩膜；
3. `ocean_tide_extrapolated/` (5.264 GB)：官方 35 个分潮沿岸外推规则网格，格式为 **`.nc.xz` (XZ/LZMA 压缩)**；
4. `ocean_tide_20241025/` (4.944 GB)：官方 34 分潮非外推规则网格，格式为 `.nc.xz`；
5. `load_tide/` (6.673 GB)：官方 34 分潮负荷潮规则网格，格式为 `.nc.xz`。

### 7.2 原生非结构网格 vs 规则外推网格 (.nc.xz 压缩限制与禁用决策)
- **核心审计结论**：`ocean_tide_extrapolated` 目录下的所有文件均为 `.nc.xz` 压缩归档。底层 C 库 NetCDF4 与 `pyfes` 无法直接挂载 `.nc.xz` 压缩包（直接读取会抛出 `OSError: NetCDF: Unknown file format`）；
- **安全决策**：遵循科研底层数据严格只读、严禁原地解压占用几十 GB 空间的铁律，**在 v1.5 Phase 1 中严格禁用未集成的外推回退 (Extrapolated Fallback DISABLED)**，保持原生非结构网格优先解算，GUI 中对应回退勾选项永久置灰并提示说明。

### 7.3 `mask_fes2022B.nc` 四分类语义与 Hybrid MDT Source Mask 的严格解耦
审计发现早期文档存在将 FES 外推掩膜与 MDT 基准掩膜混淆的表述，现已在代码与文档中彻底厘清：
1. **`mask_fes2022B.nc` (FES 规则外推掩膜)**：
   - 形状：$(5401, 10800)$，步长 $1/30^\circ$ (2 角分)；
   - 类别定义：**`0 = Ocean native data`**, **`1 = Extrapolated data`**, **`2 = Land`**, **`3 = Lake`**；
   - 科学用途：仅用于区分规则网格产品中哪些像元来自外推，不参与当前 Native LGP2 非结构网格解算。
2. **`hybrid_mdt_source_mask.tif` (可选 Hybrid MDT 基准来源掩膜)**：
   - 科学用途：用于判别全球大洋（GOCO06s）与欧陆海域（EIGEN-6C4）的重力参考零面；
   - 状态：当前为可选外部文件，未配置时系统依靠精细多边形射线法进行连续判定。

---

## 8. 系统边界条件、已知局限性与项目行为铁律 (Boundaries, Limitations & Iron Rules)

### 8.1 核心铁律：GitHub Actions CI 绿标确认准则
> **【核心铁律】在所有涉及向 GitHub 远程仓库推送代码的任务中，严禁在执行 `git push` 后立即判定任务完成或向用户汇报。必须确认 GitHub 远程完全推送成功且 CI 流水线完全执行成功（绿标 `completed success`）后，方可做下一步决定或向用户汇报！**
- 每次推送后必须运行 `git ls-remote` 核对远端 HEAD Hash；
- 必须使用 GitHub CLI 持续监听流水线：`gh run watch <run-id>`；
- 若出现非 `success` 状态，必须立即调用 `gh run view <run-id> --log-failed` 排查闭环修复。

### 8.2 项目专属 Python 虚拟环境准则
- **最高优先级路径**：
  - Python 解释器：`I:\Test_tide_model\.venv\Scripts\python.exe`
  - Pip 管理器：`I:\Test_tide_model\.venv\Scripts\pip.exe`
- 在本仓库内执行任何 Python 脚本、测试套件或 CLI 命令时，**必须且唯一使用该专属虚拟环境**，显式覆盖全局默认环境。

### 8.3 科学语义边界：固定地形纯静水天文潮潜在淹没
- **静水淹没假定**：系统反演的淹没频率严格代表**“在固定代表性地形条件下的纯天文潮潜在淹没频率”**；
- **动力学排除项**：本系统不是二维浅水波流水动力学数值求解器，不包含风暴潮风涌增水、气压逆变效应、波浪破碎爬高、降雨地表径流汇水及河道/潮沟底摩擦曼宁阻力造成的沿程波形衰减与相位滞后；
- **地形阻隔限制**：拓扑防泄漏机制严格依赖 DEM 内部的 NoData 掩膜连通性；若人工水工建筑（如海堤、水闸）在 DEM 中具有有效地形高程值，系统无法自动将其作为阻水屏障隔离。

### 8.4 当前版本成熟度定级：v1.5 Beta 严禁宣称 Global Production Ready
- **当前定级**：**CoastTideX v1.5 Beta (Controlled Real-FES / Real-Intertidal Functional & Scientific Prototype)**；
- **客观成熟度评估**：系统在工程架构、两阶段解耦、内存控制及代表性潮滩的自适应数值保真度上已通过全面验证；但由于尚未在大规模全球实测验潮站（如 UHSLC / GESLA 数据集）下进行绝对潮位观测比对，**严禁在对外宣传、技术交流或论文中声称系统已达 Global Production Ready**。

---

## 9. 审计签署与元数据确认 (Sign-Off & Verification Metadata)

- **审计执行人**：王宇浩 (Wang Yuhao)
- **代码库根目录**：`I:\Test_tide_model`
- **审计基线 Git Commit**：`cab590fac13b6cc04207c1b4b311f039958022d8`
- **GitHub Actions 最新 CI 运行**：Run ID `34888969929` (`completed success`)
- **声明**：
  > “本人已全面完成对 CoastTideX 软件体系全部 Python 源码、Markdown 技术报告、配置文件与 Git 演进记录的审查与核对。本审计报告所载之数学公式、代码架构、功能方法、量化指标及历史演进均与物理文件真实内容严格一致，无任何编造或夸大陈述。”
