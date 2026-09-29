# CoastTideX v1.7.1 Bilingual README Alignment Audit Report

本报告系统性审查与记录 `README.md` 与 `README_EN.md` 各主要章节与生产源码的对齐状态、修订动作与科学边界界定。

---

## 1. 章节对齐审查矩阵 (Section Alignment Matrix)

| 章节编号与名称 | 中文版状态 (Chinese Status) | 英文版状态 (English Status) | 代码/事实来源 (Code / Source Checked) | 修订动作与说明 (Action Taken) |
| :--- | :--- | :--- | :--- | :--- |
| **0. Header & Badges** | Version-v1.7.1 徽章，撤销 Release 标识 | Version-v1.7.1 badge, release badge removed | Git tags / GitHub Releases | 统一采用 `Version-v1.7.1`，避免在未正式发布前误导用户 |
| **1. Project Overview & Mission** | 聚焦海岸带遥感与潮间带地形分析；强调全部 34 个分潮 LGP2 驱动 | Aligned: scientific toolkit for coastal remote sensing & intertidal analysis | `core/tide_engine.py`, `config.yaml` | 去除未覆盖的“海洋动力学/水文/生态模型”宏大修饰；中英文完全对应 |
| **2. Datums & 2.1 MSL Workflow** | 规范 MSL 参考工作流命名；明确 0–500 km 球面 3D 距离门禁（默认 100 km） | Aligned: MSL Reference Workflow; clarifies 3D spherical distance cutoff vs Nature 2026 | `core/datum_engine.py`, `core/dem_datum_converter.py` | 标题由“核心创新”中性化为“MSL 统一参考工作流”；区分 Nature 500km 岸线范围与 CoastTideX 球面物理距离门限 |
| **3. Native LGP2 Mesh** | 阐明输出栅格为 DEM-conditioned raster output | Aligned: specifies DEM-conditioned raster output | `core/raster_engine.py` | 明确 10m/30m 输出不代表底层 FES2022b 具有 10m/30m 物理水动力分辨率 |
| **4. Regular Grid Limitations** | 阐述阶梯效应、边界外推与相位平滑 | Aligned: Staircase artifacts & resolution mismatch | 潮沟与滩涂水动力学原理 | 保持两版严密一致 |
| **5. Raster Snapshot Engine** | 像元逆投影、瞬时水面高程、512 像元流式分块 | Aligned: per-pixel reprojection, instant water level, streaming I/O | `core/raster_engine.py` | 保持两版严密一致 |
| **6. Adaptive Quadtree & Inundation** | 四叉树加密、CCDF 向量化检索、计算复杂度解耦 | Aligned: quadtree refinement, CCDF vector search, computational decoupling | `core/raster_engine.py` | 保持两版严密一致 |
| **7. Exposure Duration Engine** | 严格限定固定代表性地形潜在露出；定义 7 大产物与等高露出边界 | Aligned: Potential Astronomical Tidal Exposure Duration under Fixed Representative Terrain | `core/exposure_engine.py` | 严禁与“沙滩干燥时长”或“二维退水”混淆；`H(t) == z` 严格为 Exposed |
| **8. Temporal Semantics & `[start, end)`** | 解耦 Inundation 离散采样超越频率与 Exposure 连续时段状态积分 | Aligned: decouples Inundation discrete exceedance from Exposure interval integration | `core/raster_engine.py`, `core/exposure_engine.py` | 消除“每个采样点代表后续30分钟积分”的混淆表述；阐明末端点 $H(t_{\text{end}})$ 闭合机制 |
| **9. Tide Cache & Schema 1.2** | Stage 1 图示更新为 MSL 局部参考天文潮时序解算；说明零 MDT 重复查询 | Aligned: Stage 1 diagram updated to MSL Reference Tide Timeseries | `core/tide_cache.py` | 澄清默认 MSL-first 下 Stage 1 控制节点不再重复执行点位 MDT/ΔN 换算 |
| **10. Batch Raster Engine** | 顺序推进、单瓦片失败隔离、双格式 Manifest、ExistingOutputPolicy | Aligned: sequential execution, failure isolation, dual manifest, policies | `core/batch_raster_engine.py` | 保持两版严密一致 |
| **11. Scientific Data Dependencies** | 区分仓储自带、外部必须、外部可选与预处理重现数据 | Aligned: Bundled, Mandatory, Optional, Reproduction | `config.yaml`, `scripts/generate_delta_n.py` | 明确 FES2022b 34 分潮数据规格与 Stage 2 零 FES 调用边界 |
| **12. Topology Guard Heuristic** | 目标计算掩膜拓扑防护、连通分量分割与插值安全启发式界定 | Aligned: Target-Mask-Derived Topology Guard as interpolation heuristic | `core/raster_engine.py` | 明确说明不等价于真实水动力连通性或物理海堤屏障 |
| **13. Quality Control Bitmasks** | 详细列明 Inundation QC 与 Exposure 专属 QC 各 bit 定义 | Aligned: bit-for-bit definitions for Inundation and Exposure QC | `core/raster_engine.py`, `core/exposure_engine.py` | 明确 QC=0 仅表示未触发定义位，不代表绝对自然环境精度保证 |
| **14. Installation & Environment** | Python 3.11 64-bit 独立虚拟环境安装与 requirements 指引 | Aligned: Python 3.11 virtual environment setup | `requirements.txt` | 保持两版严密一致 |
| **15. CLI Guide** | 包含 convert-dem, convert-dem-batch, exposure, snapshot, inundation, batch | Aligned: full CLI commands and arguments | `cli.py` | 包含 v1.7.1 新增的批量 DEM 转换命令 |
| **16. GUI Quick Start Guide** | **完整包含全部 5 个选项卡**，顺序与命名严格对齐 `gui/main_window.py` | **Full 5 Tabs documented**, strictly matching `gui/main_window.py` | `gui/main_window.py` lines 608-612 | 补全遗漏的 Tab 3 “DEM 基准转换 (EGM2008→MSL)”，修正后两项编号 |
| **17. Typical Applications** | 修正为遥感校正、生态环境协变量分析与陆海基准统一 | Aligned: satellite SDB correction, ecological covariates, geodetic harmonization | 遥感与海洋测绘应用事实 | 剔除“像元级绝对正高”、“潜在耐干时长”、“港珠澳大桥”等过强或特定暗示措辞 |
| **18. Efficiency & Memory Safety** | 澄清 99% FES 计算量降低为基准测试特定场景，而非无条件普遍结论 | Aligned: contextualizes 99% reduction to Chongming Island benchmark conditions | `core/raster_engine.py` | 去绝对化，标明实际收益取决于地形、容差与控制网格细分设置 |
| **19. Unit Testing & Verification** | 规范表述为“自动化单元与集成测试套件” | Aligned: automated unit and integration test suite | `tests/` test suites | 避免使用未经覆盖率百分比证明的“全面测试覆盖” |
| **20. Changelog Summary** | 概括 v1.3 至 v1.7.1 演进；与 CHANGELOG.md 保持事实级同步 | Aligned: v1.3 to v1.7.1 evolution | `CHANGELOG.md` | 保持两版严密一致 |
| **21. Citation & Acknowledgements** | BibTeX 软件引用格式与 FES/MDT/Geoid 数据源 DOI 引用 | Aligned: BibTeX citation and official data DOIs | 官方技术文献与 DOI | 保持两版严密一致 |
| **22. Author & License** | **研究方向修改为：海岸带遥感 (Coastal Remote Sensing)** | **Research Field: Coastal Remote Sensing** | 用户明确指示 | 彻底修正原“沿海海洋动力学与大地测量学”不准确表述 |

---

## 2. 结论

`README.md` 与 `README_EN.md` 已全面完成逐段深度审计与修订，中英文章节结构、数学公式、科学适用边界与 GUI 功能选项卡完全对齐，生产代码零变动。
