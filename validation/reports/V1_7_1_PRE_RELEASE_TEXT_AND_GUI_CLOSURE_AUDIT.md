# CoastTideX v1.7.1 Pre-Release Text & GUI Closure Audit Report

本报告系统记录 CoastTideX 在正式 Release 之前的最后一次全库科学表述、GUI 文案、用户手册与 CLI 一致性审计 (Scientific Wording & GUI Consistency Closure)。

---

## 1. 基础 Git 状态与溯源 (Provenance)

- **工作分支**: `docs/v1.7.1-readme-finalization`
- **起始 HEAD SHA**: `f39c654deb4b818cc09a67d3d44b552c4a9c6469` (`docs(v1.7.1): finalize bilingual README before release`)
- **远端追踪分支**: `origin/docs/v1.7.1-readme-finalization`
- **origin/main HEAD SHA**: `b8042cd90b4ba4e57ceb0431ede84517bfe924e0`
- **Tag 状态**: 严禁且未创建任何 Tag (`v1.7.1` tag 不存在)
- **Release 状态**: 严禁且未创建任何 GitHub Release (`v1.7.1` release 不存在)

---

## 2. 修改文件清单 (Modified Files)

1. `CHANGELOG.md`: 修正 `v1.7.1 - Unreleased` 章节中的科学表述与距离定义，剔除夸大词汇；
2. `README.md`: 统一科学定位、规范 Topology Guard 与 QC 术语、明确 DEM 条件化输出尺度语义、统一 CLI 示例为 MSL-first；
3. `README_EN.md`: 同步英文版科学定位、Topology Guard 连通性语义、QC 65535 解释、尺度语义与 CLI MSL-first 示例；
4. `cli.py`: 顶部 docstring 示例更新为 MSL-first，中性化 CLI 参数帮助文案（移除“严密转换”，补充 batch-raster 向下兼容说明）；
5. `core/dem_datum_converter.py`: Docstring 与异常报错文案中性化（默认配置 100 km，阐明与 Nature 2026 海岸线 500 km 范围的定义差异）；
6. `docs/V1_7_MSL_REFERENCE_WORKFLOW.md`: 中性化表格中关于比较物理基准的科学描述；
7. `gui/main_window.py`: 更新 `_show_about` 对话框文案，统一科学定位、MSL 描述、EGM2008 起伏格网与 Exposure 轨迹重构；
8. `gui/manual_dialog.py`: 系统升级用户手册 HTML 文案，统一科学定位、34分潮配置、10m/30m尺度边界、QC=0/1/2 科学释义、拓扑防线与工作流；
9. `tests/test_v171_release_text_consistency.py`: 增加测试 17 (`test_pre_release_scientific_claims_and_terminology_closure`)，全面锁定禁止词与规范词。

---

## 3. 核心科学表述与文案修订审计

### 3.1 项目统一科学定位
- **中文统一规范**: CoastTideX 是一套面向海岸带遥感与潮间带地形分析的天文潮空间模拟和垂直基准转换工具，主要提供 FES2022b 潮位模拟、DEM 垂直基准统一、潜在天文潮淹没频率和 Exposure 时间域分析等功能。
- **英文统一规范**: CoastTideX is a scientific software toolkit for coastal remote sensing and intertidal terrain analysis, providing FES2022b astronomical tide simulation, DEM vertical-datum harmonization, potential astronomical tidal inundation frequency, and exposure time-domain analysis.
- **界限排除**: 严禁泛化为“完整海洋动力学模式”、“水文学模型”、“潮间带生态水文模型”、“沙滩动力学模型”、“泥沙动力学模型”、“二维 flooding/drying 模型”或“工程级水动力仿真软件”。海洋工程与生态研究仅作为应用场景。

### 3.2 MSL Reference Workflow 科学优势与代数等价性
- **代数公式**: $Z_{\mathrm{MSL}} = Z_{\mathrm{EGM2008}} - \mathrm{MDT} - \Delta N$，比较准则 $\mathrm{Tide}_{\mathrm{MSL}}(t) > Z_{\mathrm{MSL}}$。
- **等价性澄清**: 在同一点、相同 MDT、相同 $\Delta N$ 以及相同空间支撑条件下，旧 EGM2008 比较准则与 MSL-first 比较准则在代数上完全等价。
- **禁止旧 claim**: 全面删除“旧方法本身存在系统误差”、“新方法修复了旧方法的物理错误”、“消除近岸潮位倾斜系统偏差”、“新公式才是物理正确的”、“科学范式变革”。
- **客观优势定义**: 将静态 DEM 垂直基准转换前置；转换后 DEM_MSL 可复用；Stage 1 控制节点不再重复执行 MDT/$\Delta N$ 查询；垂直参考关系更加清晰直观。

### 3.3 Seeger & Minderhoud (Nature, 2026) 方法归因与 100 km / 500 km 距离区分
- **归因表述**: "MDT-based vertical datum transformation framework adapted from Seeger & Minderhoud (2026)." 或 "借鉴 Seeger & Minderhoud (Nature, 2026) 提出的 MDT 空间外推思路，结合工程设计与应用范围实现球面三维直角坐标 3D k-NN IDW 外推"。
- **距离定义根本差异澄清**:
  - **CoastTideX 距离定义**: 目标位置到最近有效 MDT support 的球面空间物理距离 (nearest-valid-MDT support-distance cutoff)，可配置范围 0.0 ~ 500.0 km，**默认配置值为 100.0 km**（不再称“默认推荐 100 km”或“专为高分辨率潮间带调校”）；
  - **Seeger & Minderhoud (2026) 距离定义**: 基于海岸线的全球宏观应用范围 (coastline-based application extent, ~500 km)。
  - **两者非同一物理距离**，文档中均明确声明区分。

### 3.4 Target-Mask-Derived Topology Guard 术语
- **统一名称**: Target-Mask-Derived Topology Guard / 基于目标计算掩膜派生的拓扑插值安全启发式。
- **替换表述**: “水体独立连通分量”全部更正为“目标计算掩膜连通分量”；“同一连通水体域”全部更正为“同一目标掩膜连通域”。
- **明确本质**: 由输入 DEM 的 valid/NoData mask 派生的空间插值防线与启发式过滤，不是真实水动力 (hydrodynamic) 或水力 (hydraulic) 连通模型；NoData 也不必然代表实际物理防潮海堤或绝对水力阻隔。

### 3.5 10 m / 30 m 输出空间尺度语义
- **统一界定**: 高分辨率 DEM 决定地形条件化输出网格 (terrain-conditioned output grid)，但不会使底层 FES2022b 获得 DEM 像元尺度的新动力学信息。四叉树控制网格 500 m 间距为自适应插值节点密度，非 FES 物理网格动力学分辨率。

### 3.6 QC 编码文案修正
- **QC=0**: 原生有效 MDT support 区域 (native MDT interpolation path)，当目标位置可直接由有效 CNES-CLS22 MDT 网格执行原生插值时标记 QC=0，不作为绝对精度保证；
- **QC=1**: 原生 MDT 缺失、但位于配置 support-distance cutoff 内的目标位置，采用 spherical 3D k-NN IDW 空间外推；
- **QC=2**: 超出配置 support-distance cutoff 的目标位置，输出 NoData；
- **QC=65535**: 统一修正为“NoData / 非计算区域 (NoData / outside target computation region)”，避免机械使用“陆地 / NoData”。

### 3.7 FES2022b 34 constituents 表述
- **统一名称**: “支持 FES2022b 全部 34 个分潮（默认科学配置）”。
- **禁止词汇**: 彻底杜绝“34 个主分潮”、“all 34 major constituents”、“34 个半日潮、日潮与长周期分潮”。核心 8 分潮统称为“8 个主要/核心分潮（快速预览 / 调试）”。

### 3.8 Exposure 科学定义与关于窗口描述
- **科学定义**: Inundated: $H(t) > z$；Exposed: $H(t) \le z$。在像元尺度重构同步潮位轨迹，并结合相邻采样时刻的一阶线性 crossing 进行 Exposure 时间积分和事件统计。
- **严禁词汇**: 严禁描述为“Beach Drying Time”、“drying tolerance”、“actual habitat suitability”、“2D hydrodynamic flooding/drying”。

---

## 4. Batch CLI 默认 dem-datum 专项审计 (Special Batch Datum Audit)

- **现有现状**:
  - `cli.py` 单影像 `inundation`: `default="msl"`；
  - `cli.py` 单影像 `exposure`: `default="msl"`；
  - `cli.py` 批处理 `p_batch_raster`: `default="egm2008"`；
  - `core/batch_raster_engine.py`: `dem_datum: str = "egm2008"`；
  - `GUI` 选项卡 5 (批量栅格): `self.cmb_batch_datum` 第一项为 `MSL (推荐 - v1.7 统一基准)`。
- **向下兼容性风险分析**:
  - 若在 v1.7.1 补丁/小版本中静默修改 `batch-raster` CLI 的 `default="egm2008"` 为 `default="msl"`，将导致依赖历史默认行为的用户脚本在传入未转换的 EGM2008 DEM 时，被错误解释为 MSL，造成 $Z_{\mathrm{MSL}}$ 与 $Z_{\mathrm{EGM2008}}$ 混淆，且会改变基于该参数构建的 `MANIFEST_CRITICAL_FIELDS` 参数签名与缓存复用判断。
- **最终审计结论与决策**:
  - **保持运行时默认行为不变**: `p_batch_raster` 与 `BatchRasterEngine.process_folder` 保留 `default="egm2008"` 作为向后兼容设计；
  - **文档与示例全面导向 MSL**: 在 `README.md`、`README_EN.md` 与 `cli.py` 顶部使用说明中，批量示例均显式添加 `--dem-datum msl`，并清晰注释说明“batch-raster 命令在未显式指定时默认保留为 egm2008 历史兼容模式”；
  - **CLI 参数说明完善**: 在 `cli.py` 中更新 `--dem-datum` 帮助文字为“DEM 高程基准 (默认: egm2008 历史兼容；推荐使用已转为 MSL 的 DEM 并显式指定 msl)”；
  - **未来版本规划**: 将是否在未来次版本 (Minor) 或主版本 (Major) 迁移默认值作为显式 breaking change 处理。
- **运行时行为改变**: **NO（未改变任何运行时默认值或算法行为）**。
- **科学算法修改**: **NO（零算法改动，零逻辑变更）**。

---

## 5. 自动化测试结果 (Automated Test Suite)

- **文字与界面一致性测试 (`tests/test_v171_release_text_consistency.py`)**:
  - 测试项数: 17 项
  - 运行结果: `Ran 17 tests in 1.280s -> OK` (包含新增的 Test 17 严格扫描)
- **本地全要素测试套件 (`tests/test_*.py`)**:
  - 测试项数: 335 项
  - 运行结果: `Ran 335 tests in 38.317s -> OK` (零失败、零错误)
- **Git 差异与格式检查 (`git diff --check`)**:
  - 结果: 0 warnings, 0 errors, 零多余空白字符与换行错误。

---

## 6. 剩余已知局限性 (Known Limitations)

1. **固定代表性地形假设**: 不包含台风暴潮引起的强泥沙冲淤与地形动态演变；
2. **纯天文潮驱动**: 未叠加气象风暴增减水、涌浪爬高与海啸；
3. **静态几何比较模型**: 采用静态几何相交判定，非求解二维浅水 Navier-Stokes 方程的动力学模型；
4. **沙滩湿润度物理界限**: 露出仅代表天文潮位低于地形，不代表沙滩表面已干燥，沉积物含水率受孔隙水和蒸发控制。
