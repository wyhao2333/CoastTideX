# CoastTideX v1.6 Beta — Round 9 最终证据闭环、元数据与文档一致性审计报告
# Final Evidence, Metadata & Documentation Closure Audit Report (Round 9)

- **系统名称**：CoastTideX (全球海岸带天文潮空间模拟与垂直基准转换系统)
- **软件版本**：v1.6 Beta (Round 9 Final Closure)
- **工作分支**：`fix/v1.6-round9-final-closure`
- **基线 Commit**：`d1f7ec792e8623282de6a54158b148a0dbac7d8a` (`origin/fix/v1.6-round8-release-candidate`)
- **环境**：Python 3.11.9 (`I:\Test_tide_model\.venv`), GDAL 3.9.1, Rasterio 1.3.10, NetCDF4 1.7.1, NumPy 2.4.6, SciPy 1.17.1
- **责任开发者**：王宇浩 (Wang Yuhao)
- **代码仓库**：`wyhao2333/CoastTideX`
- **本地自动化测试验证**：219 项全套测试运行完成，**219 项 100% 通过** (0 失败，0 错误，0 跳过)
- **最终门禁判定**：**READY FOR MERGE INTO MAIN AS CONTROLLED V1.6 BETA** (所有代码、结构核验、文档与测试完成严格事实级闭环；严禁声明未经全球广泛近岸实测标定的绝对 Production Ready)

---

## 一、审计背景与本轮工作原则 (Background & Principles)

本轮（Round 9 Final Evidence, Metadata & Documentation Closure）为 CoastTideX v1.6 合并入主干前的最终“证据闭环”收敛轮次。
在 Round 8 建立空间索引等价性回归与时间溯源对齐后，本轮针对前序遗留的事实级细节、结构验证完整性、GeoTIFF 物理属性、文档规范用词及测试证据链实施彻底收网：

1. **不新增科学功能**：保持现有功能特性集冻结；
2. **不重写 Exposure 数学核心**：严格保持 1D 线性跨界交点解析解与 2D 流式向量化状态机的算法与数学逻辑不变；
3. **不重构已稳定的 Inundation / Exposure 算法**：保护已高度稳定的两阶段解算流程；
4. **只修经过审计确认存在的事实问题**：结构核验防御漏洞、文档与代码注释中的未经测算修辞、时区夏令时与边界校验细节；
5. **多端完全一致性**：代码、单元测试、NetCDF/GeoTIFF metadata、README、README_EN、GUI Manual、Audit 报告严格互为镜像、完全自洽；
6. **严谨审慎的措辞界限**：绝对禁止使用比真实代码和真实 CI 测试更强的修饰词（如“生产级”、“Production Ready”、“极端耐干条件”、“防篡改”、“完整识别”、“硬性保证”、“高标准”、“彻底消除”等）。

---

## 二、Round 9 核心闭环与加固审计细节 (Core Audit Deliverables)

### 1. `validate_tide_cache_structure` 规范结构核验闭环
对 `core/tide_cache.py` 中的 `validate_tide_cache_structure(cache_path)` 实施全面的规范物理与拓扑结构核验加固：
- **必需维度核验**：严格检查 `time > 0`, `node > 0`, `cell > 0`, `bounds_dim == 4`, `corners_dim == 4` 五大基础维度，缺失或长度非合法直接抛出 `TideCacheIntegrityError`；
- **15 大必需变量全量核验**：覆盖控制节点变量 (`node_x`, `node_y`, `node_lon`, `node_lat`, `node_valid`, `static_offset_m`, `component_id`, `node_qc`)、单元变量 (`cell_level`, `cell_qc`, `cell_max_error`, `cell_bounds`, `cell_node_indices`)、时间轴 (`time`) 与潮位矩阵 (`tide_msl_m`)，对各变量的形状与存在性做逐一轻量检验；
- **拓扑引用类型与越界核验**：强制校验 `cell_node_indices` 必须为整数类型 (`np.issubdtype(..., np.integer)`)，形状严格为 `(n_cell, 4)`，且角点引用的控制节点 ID 必须严格位于合法索引范围 `[0, n_node - 1]` 内；
- **`terminal_tide` 形状与一致性核验**：校验 `tide_msl_terminal_m` 必须严格为一维 `(n_node,)`，拒收二维 `(1, n_node)`；校验全局属性 `HAS_TERMINAL_TIDE` 的布尔值与变量物理存在性 100% 互锁；
- **时间轴与元数据闭环核验**：
  - 核验 `TIME_SAMPLES` 属性与时间轴维度长度一致；
  - 核验时间轴一维数组严格单调递增、有限无 NaN；
  - 核验步长与 `TIME_STEP_SECONDS` 标称步长偏差不超过 `1e-3s` (1ms)；
  - 核验 `TIME_START_UTC_EPOCH` 与 `time[0]` 偏差不超过 `1e-3s`；
  - 核验 `TIME_END_UTC_EPOCH` 在半开区间 `[start, end)` 规范下与 `time[-1] + step` 偏差不超过 `1e-3s`（对 Schema 1.1 历史缓存做向前兼容容忍）；
  - 核验 `TIME_INTERVAL_SEMANTICS` 与 `TIME_INCLUSIVE` 自相矛盾校验。

### 2. `inclusive_to_interval_semantics` 权威数学映射器
在 `core/tide_cache.py` 与 `core/__init__.py` 中规范并导出权威数学映射器：
```python
def inclusive_to_interval_semantics(inclusive: str) -> str:
    """
    将 inclusive 参数映射为规范的时间区间闭合语义字符串。
    'left'    -> '[start, end)'
    'right'   -> '(start, end]'
    'both'    -> '[start, end]'
    'neither' -> '(start, end)'
    若传入不支持的参数，显式抛出 ValueError。
    """
```
在 `write_tide_cache` 写入缓存时，动态写入与当前 `inclusive` 模式严格一致的 `TIME_INTERVAL_SEMANTICS` 属性，并在 `validate_tide_cache_structure` 中进行矛盾拦截。

### 3. `terminal_tide` 一维防御性归一化 (1D Defensive Normalization)
在以下所有输入/回读出口建立严格的防御性一维展平保证：
- `core/tide_cache.py`: `read_tide_cache` 中执行 `np.asarray(tide_msl_terminal).reshape(-1)`；
- `core/tide_cache.py`: `read_tide_cache_structure` 中执行 `np.asarray(tide_msl_terminal).reshape(-1)`；
- `core/exposure_engine.py`: `stream_exposure_metrics_interpolation` 入口处执行 `np.asarray(terminal_node_tides).reshape(-1)`；
- 杜绝因二维 `(1, n_node)` 广播导致的高维切片与内存异常。

### 4. 真实外部 GeoTIFF 物理属性核验与大地测量术语修正
- **实测文件**：`data/geoid/us_nga_egm08_25.tif`
- **实际规格实测**：
  - 驱动：GTiff
  - 坐标系 (CRS)：`EPSG:4979` (WGS84 3D 经纬度椭球高系统)
  - 像素尺寸：`8640 × 4321` (全球 2.5' 格网)
  - 数据类型：`float32` (波段数: 1)
  - 文件大小：精确为 `80,591,169` 字节 (约 76.86 MB)
  - NoData：`None` (全球水准面起伏连续覆盖，无掩膜缺测)
- **文档更新**：在 `data/geoid/README_GEOID.md` 中完整记录上述实测元数据；在大地测量学概念上严谨修正为“相对 EGM2008 大地水准面的海拔正高近似 (EGM2008-referenced geoid height / orthometric-height approximation)”，明确区别于严格地面实测正高与法高。

### 5. 文档与界面手册事实级中性化收敛 (Docs & GUI Manual De-sensationalization)
对 `README.md`, `README_EN.md`, `gui/manual_dialog.py` 与 `core/tide_cache.py` 实施事实级清理：
- **标题中性化**：统一为 `CoastTideX: 全球海岸带天文潮空间模拟与垂直基准转换系统` / `CoastTideX: Global Coastal Astronomical Tide Spatial Simulation and Multi-Datum Transformation System`，移除未经验证的 "High-Precision"；
- **删除未证实与夸大修饰词**：全面剔除“生产级”、“Production Ready”、“极端耐干条件”、“防篡改”、“完整识别”、“硬性保证”、“高标准”、“彻底消除”；
- **四叉树容差客观化**：将“同时将空间反演误差严格控制在设置的容差（默认 < 1.0%）以内”修正为“结合设置的四叉树细分容差（默认容差 1.0%）进行网格细分与空间插值反演”；
- **官方 FES2022 分辨率口径对齐**：引用官方技术文档 (FES2022 Product Handbook, AVISO/CNES, 2024)，明确 FES2022b 原生有限元网格分辨率为：大洋深水区约 30 km，大陆架约 10 km，大陆坡约 6 km，沿岸目标海区约 4 km，重点海峡与复杂近岸局部加密至约 2 km 至 500 m；明确四叉树 500m 间距为 DEM 尺度空间插值控制密度，而非改变 FES 底层潮汐动力学网格；
- **数据依赖清晰化**：在数据依赖表中明确区分 Stage 1 (需要 FES2022b 与 MDT) 与 Stage 2 (已有 Tide Cache 时完全零 FES 与零 MDT 调用)，标明 MDT 单分块约 99.6 MB，全球完整包约 700 MB；
- **ExistingOutputPolicy 边界澄清**：明确 `ERROR_IF_EXISTS` 在新建解算任务与基于缓存反演任务中的不同拦截范围（Stage 2 中已有 Cache 属于只读输入源，不视为冲突）。

---

## 三、Round 9 专属闭环测试套件验证 (`test_v16_round9_final_closure.py`)

新增的 `tests/test_v16_round9_final_closure.py` 测试套件涵盖 20 项专项事实闭环测试用例，运行耗时 0.38 秒，全部顺利通过：

| 测试类 (Test Class) | 测试用例名称 (Method) | 验证要点与断言证据 | 结果 |
| :--- | :--- | :--- | :---: |
| `TestCanonicalTideCacheValidation` | `test_valid_cache_passes_validation` | 合法标准 NetCDF 缓存完全通过结构核验 | **PASS** |
| `TestCanonicalTideCacheValidation` | `test_missing_canonical_dimensions` | 依次剔除 5 大必需维度，校验均抛出 `TideCacheIntegrityError`；校验 `bounds_dim != 4` 和 `corners_dim != 4` 拒绝 | **PASS** |
| `TestCanonicalTideCacheValidation` | `test_missing_each_required_variable_individually` | 15 个必需变量逐一剔除，全部准确触发 `TideCacheIntegrityError` | **PASS** |
| `TestCanonicalTideCacheValidation` | `test_cell_node_indices_shape_and_dtype_and_bounds` | 校验单元拓扑索引非 (n_cell, 4) 形状、非整数类型及越界 ID 拦截 | **PASS** |
| `TestTerminalAndTemporalMetadataValidation` | `test_terminal_tide_shape_validation` | 拒绝二维 `(1, n_node)` 终端潮位，必须严格为 `(n_node,)` | **PASS** |
| `TestTerminalAndTemporalMetadataValidation` | `test_has_terminal_tide_consistency` | `HAS_TERMINAL_TIDE` 与变量存在性严格互锁校验 | **PASS** |
| `TestTerminalAndTemporalMetadataValidation` | `test_time_samples_metadata_mismatch` | `TIME_SAMPLES` 与时间轴维度长度不符拦截 | **PASS** |
| `TestTerminalAndTemporalMetadataValidation` | `test_start_and_end_epoch_tolerance` | 起止 UTC 戳偏差 `<= 1ms` 容忍，`> 1ms` 抛出 `TideCacheIntegrityError` | **PASS** |
| `TestTerminalAndTemporalMetadataValidation` | `test_cadence_tolerance_and_monotonicity` | 校验时间轴严格单调递增，步长与元数据指定值偏差 `<= 1ms` | **PASS** |
| `TestIntervalSemanticsAndTerminalTideDefensiveNormalization` | `test_inclusive_to_interval_semantics_mapping` | 验证 `left`, `right`, `both`, `neither` 映射及无效参数抛出 `ValueError` | **PASS** |
| `TestIntervalSemanticsAndTerminalTideDefensiveNormalization` | `test_time_interval_semantics_contradiction` | `TIME_INTERVAL_SEMANTICS` 与 `TIME_INCLUSIVE` 矛盾拦截 | **PASS** |
| `TestIntervalSemanticsAndTerminalTideDefensiveNormalization` | `test_terminal_tide_1d_defensive_normalization` | 校验 `read_tide_cache` 与 `read_tide_cache_structure` 始终产出一维终端潮位 | **PASS** |
| `TestExposureEngineSemanticsAndSchemaCompatibility` | `test_exposure_engine_rejects_legacy_both` | Exposure 引擎对历史 `inclusive='both'` 缓存明确拦截报错 | **PASS** |
| `TestExposureEngineSemanticsAndSchemaCompatibility` | `test_exposure_engine_schema11_left_fallback` | Exposure 引擎对缺失终端潮位的 Schema 1.1 left 缓存正常解算并标记 `QC_EXP_TERMINAL_UNAVAILABLE` (值 8) | **PASS** |
| `TestTimezoneProvenanceAndDSTTransitions` | `test_america_new_york_dst_spring_forward` | 2024-03-10 美东夏令时跳前验证：当天仅 23 小时 (46 步长，30min 步长)，UTC 转换精确为 23h = 82800s | **PASS** |
| `TestTimezoneProvenanceAndDSTTransitions` | `test_america_new_york_dst_fall_back` | 2024-11-03 美东夏令时回退验证：当天有 25 小时 (50 步长，30min 步长)，UTC 转换精确为 25h = 90000s | **PASS** |
| `TestSpatialIndexProductionEquivalenceWithNoData` | `test_production_spatial_index_equivalence_with_nodata` | 多单元网格与显式 NoData 掩膜下，`LeafCellSpatialIndex` 与 `BruteForceSpatialIndex` 候选检索完全等价 | **PASS** |
| `TestDocumentationAndMetadataIntegrityLinting` | `test_no_forbidden_marketing_phrases_in_docs` | 自动化 Linting 检查 README / README_EN / manual_dialog 中零违规词汇 | **PASS** |
| `TestDocumentationAndMetadataIntegrityLinting` | `test_read_tide_cache_structure_docstring_neutrality` | 校验 `read_tide_cache_structure` 注释无未经测算的 `< 5MB` 宣称 | **PASS** |
| `TestDocumentationAndMetadataIntegrityLinting` | `test_geoid_readme_contains_verified_metadata` | 校验 `README_GEOID.md` 包含实测 `EPSG:4979` 与 `80,591,169` 字节 | **PASS** |

---

## 四、全系统自动化测试矩阵汇总 (Full Test Matrix Summary)

本地环境完整测试发现与回归运行结果：
```powershell
& "I:\Test_tide_model\.venv\Scripts\python.exe" -m unittest discover -s tests -p "test_*.py"
----------------------------------------------------------------------
Ran 219 tests in 45.711s

OK
```

### 测试集分类明细：
1. **`tests/test_v16_round9_final_closure.py`**: 20 项测试全部通过 (0 失败，0 错误)；
2. **`tests/test_v16_round8_release_candidate.py`**: 13 项测试全部通过 (0 失败，0 错误)；
3. **`tests/test_v16_round7_merge_gate.py`**: 13 项测试全部通过 (0 失败，0 错误)；
4. **`tests/test_v16_round6_consistency.py`**: 12 项测试全部通过 (0 失败，0 错误)；
5. **`tests/test_v16_gui_docs_alignment.py`**: 13 项测试全部通过 (0 失败，0 错误)；
6. **`tests/test_exposure_v16.py`**: 18 项测试全部通过 (0 失败，0 错误)；
7. **`tests/test_batch_raster_v15.py`**: 15 项测试全部通过 (0 失败，0 错误)；
8. **`tests/test_v15_hardening.py`**: 14 项测试全部通过 (0 失败，0 错误)；
9. **`tests/test_v15_hardening_r2.py`**: 16 项测试全部通过 (0 失败，0 错误)；
10. **`tests/test_v15_r21_datasource_help.py`**: 11 项测试全部通过 (0 失败，0 错误)；
11. **`tests/test_engines.py`**: 15 项测试全部通过 (0 失败，0 错误)；
12. **`tests/test_v15_beta_validation_harness.py`**: 59 项测试全部通过 (0 失败，0 错误)。

*(注：在远端 GitHub Actions Linux Runner 上，由于无真实 3.77 GB FES2022b 二进制网格与无图形环境，219 个测试中 3 个实测网格相关用例将受守卫安全跳过，预期通过数为 216 项，具体以 CI 实时运行为准)*。

---

## 五、最终门禁结论 (Final Merge-Gate Verdict)

> **【最终合并门禁结论】**
>
> 判定状态：**READY FOR MERGE INTO MAIN AS CONTROLLED V1.6 BETA**
>
> 判定依据与边界：
> 1. 本地 `.venv` 全要素 219 项自动化测试 100% 绿标通过（0 Failures, 0 Errors, 0 Skipped）；
> 2. `validate_tide_cache_structure` 实现了对 NetCDF 维度、变量、形状、时区、步长、半开区间端点以及拓扑索引合法性的轻量级闭环防御；
> 3. 代码、注释、测试、GeoTIFF/NetCDF 元数据、双语 README、GUI 操作手册已实现事实级完全对齐；
> 4. 严守学术审慎原则，所有关于模型精度的表述均严格限定于受控实验基准比测与数值反演，杜绝任何“Production Ready”绝对化修饰；
> 5. 系统已完全具备合并入 `main` 分支作为受控 **v1.6 Beta** 版本的全部技术与工程条件。
