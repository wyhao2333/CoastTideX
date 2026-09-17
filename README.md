# CoastTideX: 全球海岸带高精度潮位模拟与高程基准转换系统

<p align="center">
  <a href="https://github.com/wyhao2333/CoastTideX/actions"><img src="https://github.com/wyhao2333/CoastTideX/actions/workflows/ci.yml/badge.svg" alt="GitHub Actions CI"></a>
  <img src="https://img.shields.io/badge/Release-v1.6--beta-0284c7.svg" alt="Release v1.6-beta">
  <img src="https://img.shields.io/badge/Tests-136%20Passing-10b981.svg" alt="136 Tests Passing">
  <img src="https://img.shields.io/badge/Python-3.11-blue.svg" alt="Python 3.11">
  <img src="https://img.shields.io/badge/GUI-PyQt6-green.svg" alt="PyQt6">
  <img src="https://img.shields.io/badge/Tide%20Model-FES2022b%20LGP2-0284c7.svg" alt="FES2022b LGP2">
  <img src="https://img.shields.io/badge/MDT-CNES--CLS22-8b5cf6.svg" alt="CNES-CLS22 MDT">
  <img src="https://img.shields.io/badge/Datum-MSL%20%7C%20EGM2008%20%7C%20WGS84-f59e0b.svg" alt="Vertical Datum">
  <img src="https://img.shields.io/badge/License-MIT-emerald.svg" alt="MIT License">
</p>

<p align="center">
  <b>[ 中文版 (Chinese) ]</b> | <a href="README_EN.md">English Version</a>
</p>

---

## 1. 项目定位与科学目标 (Project Overview & Scientific Mission)

**CoastTideX** 是一款面向**海岸带遥感、海洋测绘、沿海潮滩生态演变与水下水文建模**研发的高性能空间潮位模拟与大地测量垂直基准严密转换系统。

系统以法国 CNES/AVISO 国际权威的 **FES2022b 全球流体潮汐动力学模型（包含 34 个主分潮的 LGP2 二阶非结构有限元网格）** 为核心动力学引擎，攻克了传统规则经纬度网格在曲折海岸线、喇叭形海湾与河口区域由“阶梯锯齿逼近”引发的严重近岸潮位畸变。同时，系统无缝集成 **CNES-CLS22 全球平均动态地形 (MDT)** 与 **NGA EGM2008 2.5分高阶大地水准面**，构建了连接局部瞬时平均海平面 (MSL)、大地水准面正高与 WGS84 三维几何椭球高的四大多元基准级联转换链条。

在 **CoastTideX v1.6** 中，系统全面拓展至**时间域分析**，正式引入**潮滩/沙滩潜在天文潮露出时长 (Exposure Duration) 分析引擎**、**严格统一的半开区间 `[start, end)` 采样语义** 以及升级的 **Tide Cache Schema 1.2（含终端时刻采样）**，实现面向千万级像元海岸带高分辨率 DEM 的高保真、零 FES 重复开销时空反演。

> [!NOTE]
> 当前阶段定义为 **CoastTideX v1.6 Beta / Feature 分支阶段**。系统具备完整工业级防御架构与验证套件，可直接用于科研分析与业务原型生产。

---

## 2. 核心科学原理与四大多元基准体系 (Core Scientific Foundations & Four Datums)

在大地测量学与海洋物理学中，不同基准面承载着截然不同的物理内涵：

```text
               h_WGS84 (空间几何椭球高，GNSS测量)
                    ▲
                    │  + N_EGM2008 (大地水准面起伏)
                    ▼
               H_EGM2008 (海拔正高，陆海工程测绘基准)
                    ▲
                    │  + ΔN (GOCO06s/EIGEN-6C4 与 EGM2008 水准面差值)
                    ▼
               H_MDT_REF (相对 MDT 原始基准面海面高)
                    ▲
                    │  + MDT (平均动态地形)
                    ▼
               Tide_MSL (相对局部平均海平面瞬时潮位)
```

### 级联转换严密数学关系式：
1. **瞬时海面相对局部平均海平面 (MSL)**：
   $$\text{Tide}(t, \lambda, \varphi) = \sum_{k=1}^{34} f_k(t) A_k(\lambda, \varphi) \cos\left( \omega_k t + v_k(t) + u_k(t) - G_k(\lambda, \varphi) \right)$$
2. **瞬时海面相对 MDT 原始水准面 (Global Ocean: GOCO06s; Med/Black Sea: EIGEN-6C4)**：
   $$H_{\text{MDT\_REF}}(t, \lambda, \varphi) = \text{Tide}(t, \lambda, \varphi) + \text{MDT}_{\text{CLS22}}(\lambda, \varphi)$$
3. **严密改正至 EGM2008 大地水准面正高 (海拔高)**：
   $$H_{\text{EGM2008}}(t, \lambda, \varphi) = H_{\text{MDT\_REF}}(t, \lambda, \varphi) + \Delta N(\lambda, \varphi)$$
   - 全球大洋：$\Delta N(\lambda, \varphi) = N_{\text{GOCO06s}}(\lambda, \varphi) - N_{\text{EGM2008}}(\lambda, \varphi)$
   - 地中海/黑海：$\Delta N(\lambda, \varphi) = N_{\text{EIGEN-6C4}}(\lambda, \varphi) - N_{\text{EGM2008}}(\lambda, \varphi)$
4. **换算至 WGS84 几何空间三维椭球高**：
   $$h_{\text{WGS84}}(t, \lambda, \varphi) = H_{\text{EGM2008}}(t, \lambda, \varphi) + N_{\text{EGM2008}}(\lambda, \varphi)$$
---

## 3. 为什么选择 FES2022b 原生非结构有限元网格 (Why FES2022b Native LGP2 Mesh)

FES2022b 提供了两种分发形式：
- **1/30° 规则经纬度网格 (Regular Grid)**；
- **原生非结构有限元三角形网格 (Native LGP2 Unstructured Mesh)**。

CoastTideX 核心解算器直接驱动 **FES2022b 原生非结构网格 (3.77 GB NS-grid)**，理由如下：
1. **物理真实性与多项式插值**：采用 LGP2 (Lagrange Polynomial of Degree 2) 二阶连续有限元多项式，真实还原浅海潮波能量在复杂边界处的反射、共振与非线性浅水潮（如 $M_4, MS_4$）。
2. **空间分辨率自适应跨越**：有限元单元在深海大洋稀疏展开（几十公里），在大陆架、海岸岛礁、狭窄海峡自适应加密至数百米，网格边界严丝合缝贴合真实海岸线。
3. **杜绝内插失真**：规则 1/30° 网格本身是通过对有限元网格进行二次采样插值生成的产物，丢失了高阶导数信息并在近岸陆地边界混入了人工外推假值。

---

## 4. 为什么传统规则网格无法满足高精度近岸要求 (Why Regular Grids Fail in Nearshore)

传统海洋软件广泛采用规则矩形网格（如 1/16°、1/30°），在沿岸沙滩和潮间带存在天然缺陷：
- **阶梯锯齿误差 (Staircase Artifacts)**：矩形像元将平滑倾斜的自然海岸切成方块阶梯，导致狭长潮沟、潮滩前沿的水位产生数公里的人为断裂；
- **陆地外推污染 (Land Contamination & Extrapolation Blow-up)**：规则网格在海岸陆地像元缺少动力学解，外推算法在浅水陡坡带极易产生数值发散（动辄数十厘米虚假增减水）；
- **潮滩边界不连续**：当卫星遥感像元尺度达到 10m/30m 时，1/30° (~3.7 km) 规则格网的粗暴双线性插值会彻底抹平微地貌潮沟的潮波相位差。

---

## 5. 空间栅格单时刻快照解算 (Spatial Raster Snapshot Engine)

在单影像空间栅格模式下，用户可输入任意标准 GeoTIFF 格式 DEM 或卫星影像：
- **逐像元严格空间投影**：自动识别投影坐标系（如 UTM / CGCS2000 / Gauss-Kruger），自动按像元几何中心逆投影至 WGS84 椭球面并求取 FES 空间非结构网格坐标；
- **瞬时水面高程栅格生成**：输出与输入 DEM 具有相同网格仿射矩阵、分辨率与尺寸的瞬时空间潮位或水面高程 GeoTIFF；
- **流式分块内存防护**：按 512×512 像元窗口流式评估与原子级安全写入，支持单幅超过 10,000×10,000 像元的超大卫星景幅。

---

## 6. 自适应四叉树控制网格与潜在淹没频率 (Adaptive Control Grid & Inundation Frequency)

对于千万级像元的 10m/30m DEM，若对全图所有像元逐一进行 17,568 个时间步的 FES 调和分析，单幅影像计算需耗时数天且消耗数百 GB 内存。

CoastTideX 创新实现了**自适应四叉树控制网格与经验互补分布 (CCDF) 解耦反演**：
1. **控制网格自适应加密**：在地形变化剧烈与水陆交界带，网格从初始 4000m 递归细分至 500m；平缓大洋保持稀疏；
2. **时序就地排序与 CCDF 向量化检索**：在控制节点计算完全年时序后立即原地排序 (`node.water_levels_sorted.sort()`)，释放未排序大数组；
3. **像元快速二分插值**：像元水位通过四角节点双线性插值获取，像元高程 $z$ 直接在四角节点 CCDF 中通过 `np.searchsorted` 快速索引求得淹没频率 $P(H(t) > z)$；
4. **单景 DEM 运算提速 100~500 倍**，且精度与逐像元直接反演相比误差严格控制在 $< 1.0\%$。

---

## 7. 潮滩/沙滩潜在天文潮露出时间域分析引擎 (Exposure Duration Time-Domain Engine)

### 科学定义与术语界定 / Strict Terminology Boundary
> [!WARNING]
> **科学严谨性声明**：本功能产物严格命名为**“固定代表性地形条件下的潜在天文潮露出时长 (Potential Astronomical Tidal Exposure Duration under a Fixed Representative Terrain)”**。<br>
> 本系统基于代表性地形高程 $z$ 与纯天文潮位序列 $H(t)$ 进行高保真连续几何跨界求交与事件积分。
> **严禁在学术报告或生产中混淆为“沙滩干燥时长 (Beach Drying Time)”或“二维水动力退水过程 (2D Hydrodynamic Flooding/Drying)”**，因为实际沙滩沉积物孔隙水渗流、波浪爬高破碎带、地下水位入渗以及气象风暴增水均属于复杂多物理场耦合，非纯天文潮静态几何所能单独决定。

### 状态判定与严格边界条件：
- **淹没状态 (Inundated)**: $H(t) > z$
- **露出状态 (Exposed)**: $H(t) \le z$
- **等高边界严格归属**: 当水面高程恰好等于地形高程 ($H(t) == z$) 时，**必须严格归属于露出状态 (Exposed)**，严禁归入淹没。

### 7 大独立空间栅格产品体系：
| 产物文件名后缀 | 数据类型 | 单位 | 科学含义与物理说明 |
| :--- | :---: | :---: | :--- |
| `*_exposure_fraction.tif` | Float32 | % | 累计潜在露出时间百分比：$\frac{\text{累计露出秒数}}{\text{有效时间秒数}} \times 100\%$ |
| `*_exposure_duration_h.tif` | Float32 | hours | 累计有效潜在露出总时长 (小时) |
| `*_exposure_max_continuous_h.tif` | Float32 | hours | 单次最长连续潜在露出时长 (反映滩涂生物耐干旱极限与作业窗口) |
| `*_exposure_mean_event_h.tif` | Float32 | hours | 平均单次露出事件时长：$\frac{\text{累计露出时长}}{\text{完整露出事件发生次数}}$ |
| `*_exposure_event_count.tif` | UInt32 | 次 (count) | 周期内露出事件完整发生频次 (潮周期交替次数) |
| `*_exposure_valid_time_fraction.tif` | Float32 | % | 有效时序数据时间覆盖率 (检验时间序列是否存在 NaN 断缺) |
| `*_exposure_qc.tif` | UInt16 | bitmask | 露出分析专属质量控制位掩膜 (0 表示高保真解算) |

### 跨界线性插值 (Linear Crossing Interpolation)：
在离散采样步 $[t_0, t_1]$（如步长 $\Delta t = 30\text{min}$）间，当水面高程跨越高程 $z$ 时，系统通过精确一阶线性插值求解交点时刻 $t^*$：
$$r = \frac{z - H(t_0)}{H(t_1) - H(t_0)}, \quad t^* = t_0 + r \Delta t$$
杜绝了将 30 分钟粗暴截断为整点台阶所带来的离散量化失真。

---

## 8. 严格时间采样语义与半开区间 `[start, end)` (Strict Temporal Semantics)

在 CoastTideX v1.6 中，全系统科学产品（无论是整年预测、长时序还是自定义时段）全面统一切片时间语义为严格**半开区间 `[start, end)` (即 `inclusive="left"`)**。

### 核心科学依据：
1. **样本权重均等**：以 2024 闰年为例，时段为 `2024-01-01 00:00:00` 至 `2025-01-01 00:00:00`，步长为 `30min`。半开区间精确包含 **17,568** 个采样点，每个点代表后续 30 分钟的时间窗口积分，全年等权重；
2. **杜绝跨年重复累加**：若采用双闭区间 `[start, end]`，会导致最后一年的 `00:00:00` 被当前年与下一年重复统计两次；
3. **终端跨界采样支撑**：在半开区间下，最后一个子区间 $[t_{N-1}, t_{\text{end}})$ 的末端水面高程 $H(t_{\text{end}})$ 专门由 Tide Cache Schema 1.2 的 `tide_msl_terminal_m` 变量承载，既确保了时间点数统计的严密性，又保障了跨界插值的完全连续性。

---

## 9. Tide Cache 二阶段架构与 Schema 1.2 (Tide Cache Architecture & Schema 1.2)

针对批量处理场景，CoastTideX 实施严格的二阶段解耦架构：

```text
       输入 DEM 栅格
             │
      [ Stage 1 ]  构建自适应四叉树控制网格 ───► FES2022b 调和分析与基准转换
             │                                           │
             ▼                                           ▼
      持久化 NetCDF Tide Cache (*_tide.nc, Schema 1.2, 包含 terminal_tide)
             │
             ├──────────────────────────┬──────────────────────────┐
             ▼                          ▼                          ▼
      [ Stage 2a ]               [ Stage 2b ]               [ Stage 2c ]
   潜在天文潮淹没频率          潜在天文潮露出时间域       后续水动力/遥感多期校准
   (*_inundation.tif)          (7大 Exposure GeoTIFF)      (零 FES 重复调用)
```

### Tide Cache Schema 1.2 关键规范：
- **`CACHE_SCHEMA_VERSION`**: `"1.2"` (完全向下兼容读取 Schema 1.1)；
- **全要素规范签名 (`CACHE_SIGNATURE`)**: 对源 DEM 文件大小、修改时间、CRS、仿射矩阵、时段、采样率、FES 模型、拓扑分辨率等参数进行确定性 SHA-256 杂凑计算，杜绝参数漂移与缓存错配；
- **`tide_msl_terminal_m(node)`**: 存储 $t_{\text{end}}$ 时刻各控制节点的瞬时潮位，用于时间域连续性闭合；
- **流式节点块读取 (Node-chunk streaming)**: 读取缓存时不一次性拉取整个时序矩阵，内存开销极低。

---

## 10. 批量潮间带栅格解算与断点恢复 (Batch Processing & ExistingOutputPolicy)

`BatchRasterEngine` 支持大规模文件夹级自动化解算：
- **单瓦片顺序推进 (`max_parallel_tiles = 1`)**: 保障单张瓦片独占内存与 CPU，杜绝多进程抢占 FES 内存导致 OOM；
- **单瓦片失败隔离 (Failure Isolation)**: 遇局部坏图或异常瓦片自动记录 `FAILED` 并隔离，后续瓦片继续平稳运行；
- **双格式任务清单 (Batch Manifest)**: 输出目录同步维护 `batch_manifest.json` 与 `batch_manifest.csv`；
- **现有产物处理策略 (`ExistingOutputPolicy`)**:
  - `RESUME` (默认): 自动跳过已完工瓦片，未完工瓦片接续计算；
  - `ERROR_IF_EXISTS`: 发现目标产物已存在时立即报错并终止；
  - `OVERWRITE`: 强制重新解算并安全原子覆写。

---

## 11. 外部科学数据依赖关系与下载指引 (External Scientific Data Dependencies)

CoastTideX 严格区分四类科学数据：

| 数据类别 | 文件相对路径 | 存储属性 | 用途与约束说明 | 获取与下载途径 |
| :--- | :--- | :---: | :--- | :--- |
| **仓储自带数据 (Bundled)** | `data/geoid/us_nga_egm08_25.tif` | Git 仓库自带 (76.86 MB) | 全球 2.5' EGM2008 大地水准面起伏 $N$，用于海拔正高与椭球高换算 | 随仓库克隆自带，无需额外下载 |
| **仓储自带数据 (Bundled)** | `data/geoid/delta_n_goco06s_minus_egm2008.tif` | Git 仓库自带 (22.66 MB) | 全球大洋 GOCO06s 与 EGM2008 水准面差值改正栅格 $\Delta N$ | 随仓库克隆自带，无需额外下载 |
| **外部必须数据 (Mandatory)** | `fes2022b/ocean_tide_non_structured/...` | 外部数据 (约 3.77 GB) | FES2022b 原生非结构有限元网格 NetCDF，提供 34 分潮调和常数 | 访问 AVISO+ 官网申请授权下载 FES2022b 原生包 |
| **外部必须数据 (Mandatory)** | `mdt_cls22/...` | 外部数据 (约 700 MB) | CNES-CLS22 全球平均动态地形 (MDT)，连接 MSL 与水准面 | 访问 AVISO+ / CMEMS 官网下载 CNES-CLS22 MDT |
| **外部可选数据 (Optional)** | `config.yaml -> paths.hybrid_mdt_source_mask` | 外部可选 (未内置) | Hybrid MDT 权威分类掩膜。留空时系统自动采用地理多边形判定并输出 QC 预警 | 用户若有官方分类源栅格可显式配置 |
| **外部可选数据 (Optional)** | `fes2022b/mask_fes2022B.nc` | 外部参考 (约 0.98 MB, 1,027,081 字节) | FES2022b 1/30° 规则网格外推掩膜。当前原生 LGP2 有限元流程不依赖此文件 (详见 [docs/FES_MASK_METADATA_AUDIT.md](docs/FES_MASK_METADATA_AUDIT.md)) | FES2022b 补充参考包 |
| **预处理重现数据 (Reproduction)** | `data/geoid/delta_n_eigen6c4_minus_egm2008.tif` | 本地预处理 (Git 已忽略) | 地中海与黑海专用差值改正栅格，为保持仓库轻量未强制入库 | 可使用 `python scripts/generate_delta_n.py` 随时本地生成 |

---

## 12. 拓扑屏障保护与连通域防护 (Valid-Mask Topology Guard)

在河口、半岛、狭窄沙咀与岛礁区域，若单纯依靠几何欧氏距离进行空间反距离或双线性插值，会导致海陆两侧或不同水体间发生潮位“穿墙泄漏”。

CoastTideX 引入了**物理尺度拓扑连通防护 (Topology Guard)**：
1. **物理尺度掩膜构建**: 按 `topology_max_resolution_m` (默认 100m) 构建保守二值粗掩膜；
2. **形态学与连通域分割**: 通过 `scipy.ndimage.label` 标识水体独立连通分量 (Component ID)；
3. **屏障跨越阻断**: 控制网格节点仅能对同属于同一连通水体域的像元进行空间插值；跨越陆地 NoData 屏障时自动回退为局部单侧插值并标记 `QC_BIT_CONNECTIVITY_FALLBACK`。

---

## 13. 质量控制体系与 UInt16 位掩码编码 (Quality Control System & Bitmasks)

系统产出的每一个像素均配备可追溯的质量编码（UInt16 Bitmask），支持在 GIS 中按位与 (`&`) 运算精准过滤：

### 淹没频率与快照 QC 位定义 (`*_inundation_qc.tif`, `*_qc.tif`)：
- `bit 0 (1)`: FES 动力学外推点 (`QC_BIT_FES_EXTRAPOLATED`)
- `bit 1 (2)`: 空间插值降级 (`QC_BIT_SPATIAL_FALLBACK`)
- `bit 2 (4)`: 周围缺乏有效控制节点 (`QC_BIT_INSUFFICIENT_NODES`)
- `bit 3 (8)`: 垂直基准转换无效 (`QC_BIT_DATUM_INVALID`)
- `bit 4 (16)`: 采用几何多边形近似基准源 (`QC_BIT_DATUM_SOURCE_APPROX`)
- `bit 5 (32)`: 达到最小允许间距仍未达公差 (`QC_BIT_MIN_SPACING_REACHED`)
- `bit 6 (64)`: 跨越拓扑阻隔回退 (`QC_BIT_CONNECTIVITY_FALLBACK`)
- `bit 7 (128)`: FES 海洋突变边界 (`QC_BIT_FES_VALIDITY_BOUNDARY`)
- `bit 8 (256)`: 达到最大网格细分深度限制 (`QC_BIT_MAX_REFINEMENT_REACHED`)
- `65535`: 陆地 / NoData 区域

### 潜在露出时间域专属 QC 位定义 (`*_exposure_qc.tif`)：
- `0`: 正常高保真解算 (`QC_EXP_VALID`)
- `bit 0 (1)`: 四角点降级插值 (`QC_EXP_DEGRADED_CELL`)
- `bit 1 (2)`: 控制节点不足 (`QC_EXP_INSUFFICIENT_NODES`)
- `bit 2 (4)`: 基准面多边形近似 (`QC_EXP_DATUM_APPROX`)
- `bit 3 (8)`: 终端时刻水位平推近似 (`QC_EXP_TERMINAL_APPROX`)
- `bit 4 (16)`: 序列含无效数据间隙 (`QC_EXP_PARTIAL_VALID_TIME`)
- `bit 5 (32)`: 全时段常时淹没像元 (`QC_EXP_PERMANENTLY_SUBMERGED`)
- `bit 6 (64)`: 全时段常时露出像元 (`QC_EXP_PERMANENTLY_EXPOSED`)
- `65535`: 陆地 / NoData 像元 (`QC_EXP_NODATA`)

---

## 14. 软件安装与环境依赖配置 (Installation & Setup)

### 推荐 Python 环境：
- **Python 版本**: 3.11 64-bit
- **项目专属虚拟环境**: `I:\Test_tide_model\.venv` (开发环境) 或本地标准 venv

### 安装依赖：
```bash
git clone https://github.com/wyhao2333/CoastTideX.git
cd CoastTideX

python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux / macOS:
source .venv/bin/activate

pip install -r requirements.txt
```

---

## 15. 快速上手：CLI 命令行完全指南 (Quick Start: CLI Guide)

CoastTideX 提供完整无头运行能力的命令行工具 `cli.py`：

### 1. 潜在天文潮露出时间域分析 (v1.6 新增)
```bash
# 从已有 Tide Cache 执行零 FES 快速露出分析
python cli.py raster exposure \
    --dem path/to/beach_dem.tif \
    --cache path/to/beach_dem_tide.nc \
    --output-dir path/to/output_dir

# 从 DEM 直接执行完整露出分析
python cli.py raster exposure \
    --dem path/to/beach_dem.tif \
    --year 2024 --step 30min \
    --dem-datum egm2008 \
    --output-dir path/to/output_dir
```

### 2. 单时刻空间水面快照 (Snapshot)
```bash
python cli.py raster snapshot \
    --input path/to/input_dem.tif \
    -o path/to/snapshot_20240615.tif \
    --time "2024-06-15 12:00:00" \
    --datum egm2008
```

### 3. 单景 DEM 自适应控制网格淹没频率 (Inundation)
```bash
python cli.py raster inundation \
    --dem path/to/input_dem.tif \
    -o path/to/inundation_2024.tif \
    --year 2024 --step 30min \
    --dem-datum egm2008 \
    --export-cache path/to/cache_tide.nc
```

### 4. 批量潮间带栅格与 Tide Cache 流程 (Batch)
```bash
python cli.py raster batch \
    -i path/to/dem_folder \
    -o path/to/output_folder \
    --mode all \
    --year 2024 --step 30min \
    --existing-policy resume
```

---

## 16. 快速上手：GUI 桌面图形界面指南 (Quick Start: GUI Desktop Guide)

双击运行根目录下的 `run_gui.bat`（或在激活的虚拟环境中运行 `python app.py`，命令行批处理亦可使用 `python cli.py --help`）：
1. **选项卡 1：单点/时段潮位序列**：输入经纬度，一键生成潮位折线图、极值标注与高程基准转换表；
2. **选项卡 2：批量站点多时刻解算**：导入 CSV 坐标表，批量解算并导出结果；
3. **选项卡 3：单影像栅格解算 / 验证**：加载 GeoTIFF，自由选择快照解算、潜在淹没频率或潜在露出时间域分析；
4. **选项卡 4：批量潮间带栅格解算**：指定输入影像文件夹与输出目录，选择运行模式与输出策略，全自动后台批处理并可视化进度。

---

## 17. 典型科研与工程应用场景 (Typical Applications)

1. **海岸带卫星遥感水位即时校正 (Satellite SDB / Intertidal Inversion)**：
   利用 Snapshot 功能为 Sentinel-2 / Landsat 过轨时刻提供像元级厘米级水面几何正高，消除沿岸潮汐斜率对水深反演的歪曲。
2. **滨海湿地与潮滩生态演变模拟 (Coastal Wetland & Tidal Flat Ecology)**：
   利用 Exposure Duration 分析引擎量化红树林、盐沼、互花米草或滩涂贝类的潜在耐干旱极限与淹没周期。
3. **海上风电与跨海通道工程标高统一 (Offshore Wind & Bridge Engineering)**：
   实现港珠澳大桥、海上风电打桩点在陆海过渡带与国家大地水准面 (EGM2008) 的严格闭合。

---

## 18. 计算效率与工程内存安全设计 (Computational Efficiency & Memory Safety)

CoastTideX 面向海岸带千万级像元高分辨率遥感影像与长时序模拟，建立了严格的科学降维与工程防护机制：

1. **控制网格与逐像元 FES 动力学解耦**：
   - 传统逐像元暴力计算需对千万级像元全量运行 34 分潮调和展开，计算耗时与内存开销不可接受；
   - CoastTideX 采用自适应四叉树稀疏控制网格与 CCDF 向量化检索，仅需在数百至数千个关键控制节点解算 FES 潮位，像元级淹没频率通过四角节点经验累计分布高效插值求得；
   - 在千万级像元典型沿海影像上，避免了 99% 以上像元的冗余 FES 评估，同时将空间反演误差严格控制在设置的容差（默认 < 1.0%）以内。

2. **时间域流式 2D 状态机与超低内存驻留**：
   - 露出时间域分析引擎彻底杜绝 $(rows, cols, time\_chunk)$ 3D 像元张量分配；
   - 在 512×512 空间计算窗口内，仅维护 2D 像元高程与标量状态，时间轴按时间步纯矢量化流式推进；
   - 全年 17,568 个时间步流式解算过程中，单景瓦片核心解算内存峰值严格受控在 < 2.5 GB 物理内存以内（基于标准 8 核 16GB 典型科研工作站评估）。

---

## 19. 单元测试与质量验证 (Unit Testing & Verification)

CoastTideX 拥有完备的分层自动化测试体系，全工程累计通过 **136 项严谨单元测试**：
```bash
& "I:\Test_tide_model\.venv\Scripts\python.exe" -m unittest discover -s tests -p "test_*.py"
```
```text
Ran 136 tests in ~43.8s
OK
```

### 测试层次与执行边界说明：
1. **GitHub Actions 远端 CI 流水线 (自动化构建与回归防护)**：
   - 在无图形界面、无真实 `pyfes` C/C++ 扩展编译环境的纯净 Linux runner 上运行；
   - 依靠测试替身（Mock Predictors）、合成潮汐动力学场与数学解析解桩，全面覆盖 4 大高程基准闭合性、四叉树网格拓扑连通防护、Tide Cache NetCDF 流式读写、断点恢复、异常回滚及 2D 向量化状态机。
2. **本地全要素真实科学验证 (Local Full Validation Harness)**：
   - 位于 `tests/test_v15_beta_validation_harness.py`；
   - 专用于在配置有真实 FES2022b 原生非结构网格 (`fes2022b/` 3.77 GB) 与崇明东滩/长兴岛真实 DEM 的本地工作站环境下执行物理真实性端到端校验。
3. **v1.6 生产场景严密覆盖 (Production Scenarios)**：
   - 涵盖时序分块切片读取器 vs 全量 Oracle 0 误差等价性、切片时间跨度上界约束、双盆地山脊拓扑屏障隔离、失效角点权重自动重新归一化、多时区转换与缺失终端潮位分母守恒、`_AtomicExposureWriter` 异常临时文件零残留、陈旧 DEM 修改拦截、Stage 2 零 FES 物理调用不变量以及 NetCDF 节点越界完整性校验等 9 大生产级专项测试。

---

## 20. 项目更新日志与版本演进 (Changelog Summary)

完整历史版本日志请参见独立文档 [CHANGELOG.md](CHANGELOG.md)：
- **v1.6 (Beta / Feature Branch)**: 潜在天文潮露出时间域分析引擎 (7大 GeoTIFF 产物)、跨界线性插值、严格半开区间 `[start, end)` 语义统一、Tide Cache Schema 1.2、双语开发规范与外部数据依赖体系。
- **v1.5 Beta**: 真实 FES2022b 与真实沙滩/潮滩 DEM 科学验证套件与实测比对。
- **v1.5 Alpha**: 批量潮间带栅格引擎、Tide Cache 持久化、防篡改签名与断点恢复。
- **v1.4**: 空间栅格单时刻快照与自适应四叉树潜在天文潮淹没频率解算。
- **v1.3**: 四大多元垂直基准严密转换体系 (MSL / MDT / EGM2008 / WGS84)。

---

## 21. 科学引用与致谢 (Citation & Acknowledgements)

若您在科研论文、工程咨询或开源项目中使用了 CoastTideX，请引用如下工作：

```bibtex
@software{CoastTideX_2026,
  author = {Wang, Yuhao},
  title = {CoastTideX: A High-Precision Coastal Spatial Raster Tide Simulation and Multi-Datum Transformation System},
  year = {2026},
  version = {v1.6},
  url = {https://github.com/wyhao2333/CoastTideX}
}
```

### 动力学模型与数据致谢：
- **FES2022b**: Developed by LEGOS, NOVELTIS, CLS and CNES; distributed by AVISO+ (DOI: [10.24400/527896/a01-2024.004](https://doi.org/10.24400/527896/a01-2024.004));
- **CNES-CLS22 MDT**: Produced by CLS Space Oceanography Division and CNES (DOI: [10.24400/527896/a01-2023.003](https://doi.org/10.24400/527896/a01-2023.003));
- **GOCO06s Gravity Field**: ICGEM, GFZ German Research Centre for Geosciences, Potsdam;
- **EGM2008 Geoid**: National Geospatial-Intelligence Agency (NGA), Pavlis et al. (2012).

---

## 22. 作者信息与开源许可证 (Author & License)

- **作者 / 开发者**: **王宇浩** (Yuhao Wang)
- **专业领域**: 沿海海洋动力学与大地测量学 (Coastal Ocean Dynamics & Geodesy)
- **开源许可证**: [MIT License](LICENSE)
