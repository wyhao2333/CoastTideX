# CoastTideX v1.7 — Nature (2026) 方法一致性与合规性审查报告
(Nature-Method Conformity & Attribution Audit Report)

---

## 一、审查背景与目标

针对 CoastTideX v1.7 引入的基于平均动态地形 (MDT) 的近岸陆面垂直基准统一框架，本报告对标国际权威文献及其公开复现资源，开展严格的方法一致性、参数完备性与学术归因审查：

- **核心文献**：
  Katharina Seeger & Philip S. J. Minderhoud, *"Sea level much higher than assumed in most coastal hazard assessments"*, *Nature*, 2026. DOI: [10.1038/s41586-026-10196-1](https://doi.org/10.1038/s41586-026-10196-1)
- **公开代码与复现包**：
  Katharina Seeger & Philip S. J. Minderhoud, *"Model workflow codes for vertical datum conversion of digital elevation models to a sea-level reference"*, Zenodo, 2026. DOI: [10.5281/zenodo.17953234](https://doi.org/10.5281/zenodo.17953234)

本审查的核心目标是：**核验公开资料中关于 MDT 插值与向陆外推的全部关键技术参数，杜绝臆测，确定本系统实现的方法归因结论（Case A 严格参数复现 vs Case B 改编框架），并彻底清理已有文档中可能存在的过度归因**。

---

## 二、公开代码与文献技术细节全面核验

### 1. Zenodo 公开代码仓 (DOI: 10.5281/zenodo.17953234) 核查结果
通过对 Zenodo 官方归档（Record 17953235）的文件抓取与逐行代码审查，该代码包由 3 个文件构成：
1. `README.txt`：
   - 明确指出该工作流是为全球 500 km 近岸带 DEM 转换为局部海平面基准（MSL）而设计；
   - 代码是在 ArcGIS Pro 环境中通过 ModelBuilder 构建并导出为 Python 脚本。
2. `Model_DatumConversion1.py`：
   - 核心语句：`_name_DatumConversion1_tif = Raster + DatumOffsetRaster_tif`；
   - 作用：将原始 DEM 从其初始垂直基准对齐到 MDT 数据的基准面（对应重力水准面差值改正 $\Delta N$）。
3. `Model_DatumConversion2.py`：
   - 核心语句：`_name_DatumConversion2_tif = Raster - MDTRaster_tif`；
   - 作用：从已对齐基准的 DEM 中减去 MDT 栅格（`MDTRaster.tif`）。

**关键事实发现**：
Zenodo 公开代码仓仅包含**下游栅格代数运算**（加偏移栅格、减 MDT 栅格），而**上游如何从原始 CNES-CLS22 MDT 离散点/网格生成 `MDTRaster.tif` 的完整 ArcGIS 插值与外推参数（例如 ModelBuilder 中的 Geostatistical / Spatial Analyst 详细配置）并未内嵌在导出的 Python 脚本中**。

### 2. Nature 正文与方法论 (Methods) 核查结果
论文正文与方法描述中明确了以下要素：
- **基础数据**：采用 CNES/CLS 发布的 HYBRID-CNES-CLS2022 全球平均动态地形模型；
- **原生开阔水域**：开阔大洋有效 MDT 点位采用双线性插值 (Bilinear Interpolation)；
- **向陆外推算法**：在评估了经验贝叶斯克里金 (EBK)、径向基函数 (RBF) 及反距离加权 (IDW) 后，作者选用反距离加权 (IDW) 算法向陆地外推 MDT；
- **邻域平滑与权重**：采用了平滑邻域 (Smooth Neighbourhood)，并将平滑因子 (Smoothing factor) 设为 0.5（作者指出该值在平滑外推与保持数据保真度之间取得了最佳平衡）；
- **外推空间范围**：原论文针对全球宏观尺度，设定了 500 km 的近岸外推缓冲区。

### 3. 未公开的 ArcGIS 内部参数项
在 ArcGIS Pro 的 Geostatistical Analyst / Spatial Analyst 平台中，IDW 配合 Smooth Neighbourhood 时需要配置一系列专有几何与搜索参数：
- 主半轴 (Major semiaxis) 与次半轴 (Minor semiaxis)；
- 搜索方向角 (Angle)；
- 邻域平滑函数衰减半径与距离权重；
- 最小/最大参与邻域点数 (Min/Max neighbours)；
- 幂参数 (Power)。

由于 Zenodo 脚本直接以已生成的 `MDTRaster.tif` 为输入，上述专有商业 GIS 参数在公开代码中并未提供具体数值。

---

## 三、审查最终结论：Case B（改编框架）

依据审查准则，在无法从公开资料直接确定全部 ArcGIS 专有参数时，**严禁凭空臆测参数，严禁声称“严格复现”**。

### 结论判定：**Case B — Adapted Framework**
1. **算法定位**：
   CoastTideX v1.7 采用的是：
   **“MDT-based vertical datum transformation framework adapted from Seeger & Minderhoud (2026)”**
   （基于 Seeger & Minderhoud (2026) 理论改编的近岸 MDT 垂直基准转换框架）。
2. **严禁措辞**：
   - 严禁声称 “exact reproduction”（严格复现）；
   - 严禁声称 “strictly identical to Nature workflow”（与 Nature 工作流完全一致）；
   - 严禁声称 “Nature algorithm”（Nature 算法）。
3. **保留 CoastTideX 高精度工程实现**：
   - 保持 CoastTideX 独立研发的球面三维空间直角坐标 $(X, Y, Z)$ KDTree 空间反距离加权实现（幂次 $p=2.0$，$k=8$）；
   - 保持针对高分辨率近岸潮滩生态遥感的 **100 km 保守物理外推硬门禁**（原论文全球宏观尺度使用 500 km）；
   - 保持 2D 分块流式 GeoTIFF 吞吐与原子写入保护。

---

## 四、文档过度归因专项清理清单

对全项目文档进行了系统性拉网式审查，坚决剔除未经论文直接证明的过度推断，明确学术边界：

| 审查文件 | 原始过度表述 / 潜在偏差 | 规范化修正后表述 | 修正科学依据 |
| :--- | :--- | :--- | :--- |
| **README.md** | 称 100km 外推门禁由 Nature 证实 | 明确 100km 为 CoastTideX 针对高分辨率潮滩遥感的保守工程截断；Nature 原文采用 500km 范围 | 尊重原文 500km 设定，避免误导读者 |
| **README_EN.md** | 称 workflow "strictly identical" | 修正为 "adapted from Seeger & Minderhoud (Nature, 2026)" | 符合 Case B 准则 |
| **docs/V1_7_MSL_REFERENCE_WORKFLOW.md** | 称 Nature 证明旧方法逐节点外推产生虚假阶梯与性能瓶颈 | 修正为：Nature 阐明近岸海平面基准需与陆面统一；虚假台阶与节点查询瓶颈为高分辨率栅格计算领域的工程观察 | 区分文献科学结论与软件工程优化动机 |
| **docs/V1_7_MSL_REFERENCE_IMPLEMENTATION_AUDIT.md** | 表述暗示 100km 是 Nature 提出的最佳阈值 | 明确标记：100km 是 CoastTideX 应用特定的保守设计，原研究为 500km | 严格遵守论文证据边界 |
| **core/dem_datum_converter.py** | Docstring 标注 "严格复现" | 修正 Docstring 与元数据标签为 "adapted from Seeger & Minderhoud (Nature, 2026)" | 代码与学术规范完全一致 |

---

## 五、Nature 论文实际支持的核心科学结论

本系统确认并继承的 Nature (2026) 核心科学理论包括：
1. **基准统一必要性**：全球 99% 的海岸带灾害评估忽视了海平面（MSL）与陆面高程基准（大地水准面）的物理偏差，DEM 必须前置转换至局部平均海平面参考系；
2. **MDT 的物理基准作用**：平均动态地形（MDT）是连接重力大地水准面与局部平均海平面的权威物理桥梁；
3. **MDT 沿岸外推有效性**：在近岸微浅滩涂与陆面交界处，MDT 可通过反距离加权（IDW）向陆地外推，且外推可靠性随离岸测地距离增大而递减；
4. **计算数学形式**：$Z_{\text{MSL}} = Z_{\text{EGM2008}} - \text{MDT} - \Delta N$，随后以 $T(t) > Z_{\text{MSL}}$ 作为统一空间物理基准下的淹没判据。

本报告已归档并作为 CoastTideX v1.7 最终验收的关键科学合规依据。
