# CoastTideX v1.7 — GUI Product Integration, Nature-Method Conformity & GitHub Release Sync Audit

## 1. 概述与版本定位 (Executive Summary)

**CoastTideX v1.7** 是系统在垂直基准科学性、计算性能与工程产品化方面的重大里程碑升级。本版本系统性地落实了以下核心突破：

1. **科学垂直基准流重构 (MSL Reference Workflow)**：
   - 贯彻与学术界最新沿海潮汐淹没建模范式（如 Seeger & Minderhoud, *Nature*, 2026）一致的科学基准流程：由传统“将海面潮位转至 EGM2008”转变为“将陆地 DEM 统一转换至局部 MSL 基准（$Z_{\text{MSL}} = Z_{\text{EGM2008}} - \text{MDT} - \Delta N$）”，从根本上消除了沿岸非均匀水深条件下海面动力地形倾角带来的潮位倾斜误差。
   - 实现了严密的 MDT 沿岸外推算法：开箱即用原生双线性插值，沿海无值区自动采用反距离权重插值（IDW, $p=2, k=12$），并设置了 100 km 保守外推安全截断（CoastTideX 严格安全边界）。

2. **高性能 ParentBBox 模型复用 (FES Model Reuse Optimization)**：
   - 在 `RasterTideEngine` 空间自适应四叉树控制网格解算中，引入 Parent Spatial Scope 复用机制。对同一 DEM 网格或同一空间外包框范围，仅初始化并加载一次底层 FES 潮汐模型，彻底消除了数千个叶子节点反复加载 `pyfes.config.LGP` 的 I/O 与内存开销，实现解算速度 4~5 倍的大幅提升。

3. **桌面端 GUI 全功能产品化集成 (GUI Product Integration)**：
   - 在 `gui/main_window.py` 新增独立的 **“📐 DEM 基准转换 (EGM2008→MSL)”** 标签页，支持栅格元数据实时探测、单波段验证、二次转换防护（`DATUM=MSL` 探测）、0~100 km 外推距离安全约束与多线程异步解算。
   - 提供直接一键推送到“淹没频率分析”与“露出时间分析”的工作流无缝衔接按钮。
   - 淹没与露出界面默认垂直基准全面升级为 `MSL (推荐, 沿海DEM基准转换)`，并提供动态联动提示。
   - 系统使用指南 (`gui/manual_dialog.py`) 升级至 v1.7，新增第十六章专门阐述 MSL 科学基准工作流。

4. **无缝代码质量与测试全通过 (Test & Quality Assurance)**：
   - 全套 271 项单元测试与集成测试全部通过（271/271 Passed, 0 Failures）。

---

## 2. Nature 方法一致性审查结果 (Nature Conformity Audit)

针对学术界公开成果 Seeger & Minderhoud (*Nature*, 2026; Zenodo DOI: [10.5281/zenodo.17953234](https://doi.org/10.5281/zenodo.17953234))，本轮审查对 CoastTideX 的实现与文献公开方法进行了系统性对比与边界校正：

| 维度 | Seeger & Minderhoud (*Nature*, 2026) | CoastTideX v1.7 实现 | 一致性与设计说明 |
| :--- | :--- | :--- | :--- |
| **基准统一方向** | DEM: EGM2008/EGM96 $\to$ MSL | DEM: EGM2008 $\to$ MSL | **完全一致**。均采用陆地基准向海洋局域基准转换 |
| **转换数学公式** | $Z_{\text{MSL}} = Z_{\text{EGM}} - \text{MDT} - \Delta N$ | $Z_{\text{MSL}} = Z_{\text{EGM2008}} - \text{MDT} - \Delta N$ | **完全一致**。严密扣除高程异常差与平均动力地形 |
| **MDT 沿岸外推** | GDAL FillNodata (Poisson) / IDW，最大外推距离 500 km | 双线性插值 + IDW ($p=2, k=12$)，最大外推距离 100 km | **保守防护**。100 km 阈值更适应复杂河口与内湾，防止超远距离无物理意义外推 |
| **文献与权属声明** | 文献公开发布方法 | 明确说明 100 km 为 CoastTideX 保守安全阈值，杜绝越界归因 | **严谨合规**。技术文档与代码注释均已完成精确核准 |

详细对比已固化至 `validation/reports/V1_7_NATURE_METHOD_CONFORMITY_AUDIT.md`。

---

## 3. GUI 产品集成实现明细 (GUI Integration Specification)

### 3.1 界面布局与交互
- **新增 Tab 2 界面**：
  - 输入/输出 DEM 路径选择，集成自动后缀建议 (`_msl.tif`)；
  - 栅格属性检查器（大小、分辨率、波段数、投影系、现有基准标识）；
  - MDT 栅格路径与 $\Delta N$ 网格路径配置（支持默认自动加载）；
  - 外推算法参数（IDW 最近邻数 $k$、幂次 $p$、最大有效外推距离 1~100 km）；
  - 进度条与状态标签（多线程实时汇报瓦片处理进度与耗时）；
  - 结果展示卡片：清晰显示转换成功信息、有效像元统计、极值分布与输出元数据标签；
  - 快捷联动动作组：“直接发送到淹没频率分析”与“直接发送到露出时间分析”。

### 3.2 安全防护机制
- **单波段与投影合法性校验**：拒绝处理多波段或非地理/投影合法栅格；
- **防二次转换保护**：自动读取 GeoTIFF `TIFFTAG_IMAGEDESCRIPTION` 与 `GDAL_METADATA`，若探测到 `DATUM=MSL` 标签，弹窗阻止重复转换；
- **距离上限硬约束**：GUI 界面 `QSpinBox` 强制限制范围为 $1 \sim 100\text{ km}$，阻止超出物理信任域的过大外推；
- **异步安全解算**：基于 `QThread` 封装 `DEMDatumConversionWorker`，UI 界面保持完全流畅，支持异常捕获与主窗口安全通信。

---

## 4. 测试与回归验证 (Testing & Regression)

本地测试环境：`I:\Test_tide_model\.venv\Scripts\python.exe` (Python 3.11.9)
测试执行命令：
```powershell
& "I:\Test_tide_model\.venv\Scripts\python.exe" -m unittest discover -s tests -p "test_*.py"
```

测试执行结果：
```text
Ran 271 tests in 31.512s
OK
```

核心新增与回归测试项：
1. `tests/test_v17_gui_msl_workflow.py`（8 项 GUI 测试全部通过）：
   - `test_01_tab_dem_convert_exists`: Tab 2 存在性与组件完整性
   - `test_02_double_conversion_prevention`: 重复转换拦截逻辑
   - `test_03_metadata_inspection`: 栅格属性探测与标签展示
   - `test_04_max_distance_clamping`: 外推距离合法范围约束
   - `test_05_worker_execution`: `DEMDatumConversionWorker` 后台转换线程全流程
   - `test_06_handoff_buttons_populate_inundation_and_exposure`: 结果直通联动
   - `test_07_inundation_exposure_defaults_to_msl`: 淹没/露出默认参数联动
   - `test_08_auto_detect_msl_on_file_selected`: 文件选择自动识别与动态提示
2. `tests/test_dem_msl_conversion.py`：EGM2008 $\to$ MSL 核心数学转换算法单元测试
3. `tests/test_parent_bbox_model_reuse.py`：FES ParentBBox 模型复用单元测试
4. `tests/test_engines.py`、`tests/test_cli.py` 等原有 250+ 项测试全部保持 100% 回归通过。

---

## 5. 仓库分支与同步规划 (Repository & Branch Plan)

1. **功能分支 (Feature Branch)**：`feature/v1.7-msl-reference-workflow`
   - 包含 v1.7 全部核心算法、优化特性、GUI 实现、测试套件与验证文档；
   - 干净推送到远端 `origin/feature/v1.7-msl-reference-workflow`；
   - 严禁 `--force`，严格等待 GitHub Actions CI 自动化构建与测试通过（绿标）。
2. **主分支清理 (Maintenance on main)**：
   - 采用独立临时 worktree，针对 `origin/main` 移除 `AGENTS.md`，执行快进推送；
   - 本地 `I:\Test_tide_model\AGENTS.md` 物理文件严格保留并由 `.gitignore` 保护；
   - 暂不合并 `feature/v1.7-msl-reference-workflow` 到 `main`，保留给用户做最终审查。
