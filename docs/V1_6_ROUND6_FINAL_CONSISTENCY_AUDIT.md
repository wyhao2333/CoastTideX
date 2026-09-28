# CoastTideX v1.6 Beta 第六轮一致性收尾与权威架构审计报告
# Final Architectural Consistency & Production Hardening Audit Report (Round 6)

- **系统名称**：CoastTideX (全球潮汐与高程基准空间模拟系统)
- **软件版本**：v1.6 Beta
- **工作分支**：`fix/v1.6-round6-final-consistency`
- **审计基准时间**：2026-09-18
- **责任开发者**：王宇浩 (Wang Yuhao)
- **代码仓库**：`wyhao2333/CoastTideX`
- **工作区路径**：`.` (CoastTideX Project Root)
- **最终就绪判定**：`READY FOR CONTROLLED V1.6 BETA VALIDATION` (受控测试就绪，严禁虚标 Production Ready)
- **文档状态**：`SUPERSEDED`

> [!NOTE]
> **历史审计报告声明 (Superseded Notice)**：<br>
> 本审计报告已被 Round 7 和 Round 8 审计覆盖，最新基线与结论以 [`docs/V1_6_ROUND8_RELEASE_CANDIDATE_AUDIT.md`](V1_6_ROUND8_RELEASE_CANDIDATE_AUDIT.md) 为准。

---

## 一、审计背景与本轮工作原则 (Background & Principles)

在经过前五轮关于暴露时间域核心算法、半开区间采样语义、流式状态机、Tide Cache NetCDF 原子读写与 GUI 对齐的迭代后，CoastTideX v1.6 已经建立了坚实的科学数学底座。
本轮（Round 6 Final Consistency Fix）严格遵循“**不破坏、不重写已经验证稳定的 Exposure 数学核心**”的前提，聚焦于剩余系统性一致性、批处理从缓存解算语义纠偏、空间检索效率瓶颈优化、真实行为测试闭环以及文档与代码中非学术夸大修辞的彻底清退。

---

## 二、Round 6 十大核心改进与闭环修复 (Core Hardening Deliverables)

### 1. 修复 Batch `*-from-cache` 的权威缓存语义 (Authoritative Cache Semantics)
- **核心问题**：在 `inundation-from-cache` 与 `exposure-from-cache` 模式下，旧逻辑曾使用当前 GUI 或 CLI 传入的 `year`/`start_time`/`end_time`/`freq`/`dem_datum`/`constituents` 构造 `expected_spec` 并对已存在的 Cache 进行比对，导致用户在界面上哪怕只是点了不同的时间或基准面，合法 Cache 就会被错误拒绝；而在下游 `RESUME` 检查时又使用不匹配的签名进行产物比对。
- **闭环修复**：
  1. `from-cache` 模式下，Tide Cache 为权威输入。完全忽略外部传入的时间、基准面、分潮等潮位生成参数。
  2. 显式校验 Cache 的物理存在性与 `CACHE_COMPLETE` 完整性标记。
  3. 通过 `inspect_tide_cache_metadata` 读取权威签名与全局元数据，并校验节点数与单元数大于 0。
  4. 仅比对 DEM 的空间几何（width, height, crs, transform, bounds, nodata）及文件身份（size, mtime），确保 Cache 与当前 DEM 物理对应。
  5. 采用 Cache 自带的 `actual_cache_sig` 执行下游产物验证与 RESUME 断点恢复检查。
  6. 明确政策无法改变科学兼容性：无论策略是 `RESUME`、`OVERWRITE` 还是 `ERROR_IF_EXISTS`，若 Cache 与 DEM 不匹配，必须立即报失败，绝不静默覆盖或错误跳过。

### 2. 统一 GUI 批处理控件状态恢复 (`_apply_batch_mode_constraints`)
- **核心问题**：在批处理任务执行完毕 (`_set_batch_controls_running(False)`) 后，旧代码无条件启用了全部控件（包括 `grp_batch_time` 和 `grp_batch_sci`），破坏了从缓存解算模式下禁用这些控件的约束。
- **闭环修复**：抽象出 `_apply_batch_mode_constraints(job_mode)`，在模式切换及任务完成/错误/取消时统一调用，使 `inundation-from-cache` 和 `exposure-from-cache` 在任务结束恢复交互后，时间与潮汐参数组依然保持严格禁用状态。

### 3. 单影像面板 (Tab 3) 目标区域模式与露出覆盖确认
- **目标区域下拉框**：在 Tab 3 淹没/露出参数面板新增 `combo_inund_target_mode`（默认 `intertidal` 潮间带模式，可选 `standard` 全域网格模式），与 Tab 4 批处理及 CLI 保持一致。
- **工作线程参数透传**：`RasterTideWorker` 分别向 `calculate_inundation_raster` 与 `calculate_exposure_raster` 显式透传 `target_mode`。
- **防止露出产物静默覆盖**：在单影像 `exposure` 模式启动前，预检输出目录下是否存在已有的 7 项产物；若存在且未明确确认，弹出确认对话框提示用户，若用户取消则安全终止，杜绝静默覆盖风险。

### 4. 空间网格单元检索效率优化 (`LeafCellSpatialIndex`)
- **性能瓶颈**：在单瓦片包含数千甚至上万个自适应四叉树叶单元 (`leaf_cells`) 时，每个 512×512 像元栅格分块原先均进行 $\mathcal{O}(N_{\text{cells}})$ 的全局全量扫描求交，导致大景 DEM 耗费大量 CPU 循环在空间遍历上。
- **闭环修复**：
  1. 实现 `LeafCellSpatialIndex` 空间桶索引，将像元范围离散化为规则二维网格桶，叶单元按包围盒预注册进相交桶中。
  2. 针对分块查询通过桶索引与 AABB 几何相交快速过滤候选单元，并保持去重与输入单元顺序的一致性。
  3. 在 `core/raster_engine.py` (Inundation) 与 `core/exposure_engine.py` (Exposure) 中全面接入，消除双重嵌套全扫描开销。

### 5. 批处理引擎快速拦截非法任务模式
- 在 `BatchRasterEngine.run_batch` 入口定义 `VALID_JOB_MODES` 权威集合，规范化字符串后严格判定；对于非法的 `job_mode` 立即抛出 `ValueError`，防止未定义行为渗透至后续瓦片。

### 6. Exposure-Only 批处理任务的像元计数统计闭环
- 在不计算淹没频率的批处理模式下（如 `tide-exposure` 与 `exposure-from-cache`），从 `calculate_exposure_from_tide_cache` 返回的流式像元统计中提取 `input_valid_pixels`，确保 `batch_manifest` 中的 `valid_pixel_count` 被如实记录，不再留空或显示 0。

### 7. 消除 `RasterResultSummary` 属性回退异常
- 修复在任务完成弹窗异常回退处理中，由于在 `getattr(summary, 'output_path', summary.get(...))` 中预先求值 `summary.get()` 导致的 `AttributeError: 'RasterResultSummary' object has no attribute 'get'`，改用显式 `isinstance(summary, dict)` 结构化判断。

### 8. 跨平台目录浏览替代 Windows 硬编码
- 将所有的 `subprocess.Popen('explorer ...')` 统一替换为基于 Qt 原生 API 的 `_open_directory` 方法（通过 `QDesktopServices.openUrl(QUrl.fromLocalFile(...))`），在 Windows、macOS 与 Linux 下均能安全、原生、非阻塞地打开目标文件夹。

### 9. 命令行 CLI 解析器解耦 (`build_parser`)
- 将 `cli.py` 的参数构造逻辑从 `main()` 中独立抽离为 `build_parser() -> argparse.ArgumentParser`，便于自动化测试直接对参数结构、默认值、子命令别名进行真实验证。

### 10. 全面清退不实夸大修辞与开发者本地路径
- **移除夸大修辞**：清退文档与注释中的 “100~500 倍”、“严格误差 <1%” 等修辞，替换为客观的算法复杂度解耦分析与四叉树细分阈值定义；明确说明 FES 对比是针对理论模型的数值逼近，非替代实测验潮站；
- **清理无效结构**：去除文档中虚构的 “Stage 2c” 架构图分支；
- **清退本地绝对路径**：清理 `README.md` 与 `README_EN.md` 中写死的 `I:\Test_tide_model\...` 开发路径，替换为通用标准命令。

---

## 三、验证与测试矩阵 (Verification & Testing Matrix)

| 测试分类 | 测试文件 / 模块 | 验证目的与覆盖范围 | 判定结论 |
| :--- | :--- | :--- | :---: |
| **空间桶索引等价性** | `tests/test_v16_round6_consistency.py` | 验证 `LeafCellSpatialIndex` 与暴力扫描完全等效，边界及重叠求交无丢失 | **PASS** |
| **From-Cache 语义** | `tests/test_v16_round6_consistency.py` | 验证时间/基准参数不匹配时依然能正确复用 Cache，DEM 尺寸改变时严密拦截 | **PASS** |
| **ERROR_IF_EXISTS 策略** | `tests/test_v16_round6_consistency.py` | 验证产物已存在时严格报错并记录 FAILED，不破坏既有文件 | **PASS** |
| **GUI 约束一致性** | `tests/test_v16_round6_consistency.py` | 验证 `_apply_batch_mode_constraints` 在任务结束后正确锁死 from-cache 控件 | **PASS** |
| **CLI 解析器行为** | `tests/test_v16_round6_consistency.py` | 验证 `build_parser()` 包含全部模式与默认值，参数解析符合规范 | **PASS** |
| **文档与路径清洁度** | `tests/test_v16_round6_consistency.py` | 自动化 Lint 检查 README/EN/CHANGELOG/Docs 中无硬编码开发路径与虚标修辞 | **PASS** |
| **全套回归测试集** | `python -m unittest discover -s tests` | 验证存量所有测试用例在新代码与索引下 100% 通过回归 | **PASS** |

---

## 四、科学使用边界再声明 (Scientific Boundaries Re-Affirmation)

1. **数值逼近并非物理实测**：本系统所输出的潜在天文潮淹没与露出产物，代表当前 DEM 与 FES2022b 天文潮位几何关系的理论解算，无法替代考虑了波浪、风暴潮、地下水及沉积物动力学的物理实测数据；
2. **固定地形代表性假定**：未包含滩涂冲淤演变过程，适用于稳定代表性地形条件下的水动力几何边界分析。
