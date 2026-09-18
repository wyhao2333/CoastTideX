# CoastTideX v1.6 Beta 第七轮合并门禁修复前审计报告
# Round 7 Pre-Fix Architectural & Scientific Consistency Audit

- **审计基准分支**: `fix/v1.6-round7-merge-gate` (基于 `origin/fix/v1.6-round6-final-consistency`)
- **审计基准 HEAD**: `a6e34132586f54b5717ba8635c3d8cae399b23b8`
- **审计时间**: 2026-09-19
- **审计人员**: Antigravity (AI Pair Programmer) & 王宇浩 (Wang Yuhao)
- **代码仓库**: `wyhao2333/CoastTideX`
- **审计原则**: 先审计、再修改；不新增功能；不修改已经稳定验证的 Exposure 数学核心；仅修证代码与文档真实存在的问题；所有结论必须具有直接代码与测试证据。

---

## 一、审计问题五分类总览 (Audit Findings by Category)

| 类别 (Category) | 涵盖范围与问题简述 | 严重级别 |
| :--- | :--- | :---: |
| **STILL BROKEN** | 1. `LeafCellSpatialIndex` 在地理坐标系 (EPSG:4326) 下由于硬编码 `100.0` 导致空间桶索引严重退化为单桶全扫描；<br>2. `LeafCellSpatialIndex` 桶索引缺少显式边界钳位 (clamping)，极端情况下可能计算负索引或越界索引；<br>3. `LeafCellSpatialIndex.query_intersecting_cells` 候选遍历使用 `set()` 导致候选返回顺序具有非确定性 (hash order dependency)；<br>4. GUI 从缓存解算模式下预计样本数标签仍根据被禁用的时序控件计算并显示 (stale value)；<br>5. CLI 在 from-cache 模式下对忽略的 Stage 1 请求参数无透明提示。 | **P1-HIGH** |
| **PARTIALLY FIXED** | 1. Stage 2 产物 GeoTIFF tags 中的权威 Cache 元数据传递不完整 (Inundation 缺 schema/time 细项，Exposure 缺 DEM/Cache/Time/Datum/Target-Mode 等关键溯源)；<br>2. `BatchManifest` 已记录部分时空字段，但缺少 `timezone`, `dem_datum`, `target_mode`, `cache_signature`；<br>3. `inspect_tide_cache_metadata` 仅读取签名字符串，缺少元数据篡改自校验；<br>4. Tab 3 `combo_inund_target_mode` 已控制 Inundation 与 Exposure，但命名仍为 Inundation 独占风格且缺少通用提示。 | **P1** |
| **NEWLY FOUND** | 1. Exposure permanent QC 位 (`QC_EXP_PERMANENTLY_SUBMERGED` 与 `QC_EXP_PERMANENTLY_EXPOSED`) 实际是基于“所有有效时间区间”进行判定（即使 `valid_time_fraction < 100%`），而文档曾写为“整个请求窗口永久”，存在口径定义冲突；<br>2. `LeafCellSpatialIndex` 对超大跨度单元 (Giant Cell) 跨越全部桶时的引用过度扩散缺乏防线。 | **P1** |
| **CONFIRMED FIXED** | 1. Batch from-cache 模式忽略外部时空参数差异，以 Cache 为权威数据源；<br>2. DEM 几何不兼容时，无论 `RESUME`、`OVERWRITE` 还是 `ERROR_IF_EXISTS` 均严格报 `FAILED` 拦截；<br>3. `ERROR_IF_EXISTS` 策略在 from-cache 模式下将 Cache 视为输入，不误报冲突；<br>4. Tab 3 Exposure 模式启动前防静默覆盖预检与弹窗拦截确认；<br>5. 跨平台目录浏览统一采用 `QDesktopServices.openUrl(QUrl.fromLocalFile(...))`；<br>6. `cli.py` 成功解耦 `build_parser()`；<br>7. Stage 2 解算实现零 FES 额外调用不变量。 | **VERIFIED** |
| **DOCUMENTATION ONLY**| 1. `README.md` & `README_EN.md` 包含 snapshot “厘米级”、生态 “耐干旱极限”、时间语义 “[start, end) 全系统”、Schema 1.1 “完全向下兼容”、规则网格描述偏绝对、“防篡改签名”等非学术宣传性词汇；<br>2. `gui/manual_dialog.py` 包含拓扑 “彻底阻断”、from-cache 适用场景 “重调容差”、终端不可用 “截断估算”、QC=0 “高保真”、清单 “完全向前向后兼容”、产物 “七大产品整体原子提交”、未经基准验证的绝对内存推荐等口径偏差；<br>3. `data/geoid/README_GEOID.md` 包含 “瞬时平均海平面”、EGM2008 “海拔绝对正高”；<br>4. `CHANGELOG.md` 与 `core/raster_engine.py` 注释中存在的 “100% 拓扑一致”、“near O(N_blocks)” 等表述修正。 | **P2** |

---

## 二、逐项深度技术分析与代码证据

### 1. LeafCellSpatialIndex 物理单位与坐标系退化 (STILL BROKEN / P1-HIGH)
- **代码位置**: `core/raster_engine.py` 第 382-383 行：
  ```python
  self.bucket_size_x = max(100.0, span_x / float(max(1, num_buckets_per_dim)))
  self.bucket_size_y = max(100.0, span_y / float(max(1, num_buckets_per_dim)))
  ```
- **问题机理**:
  - 在投影坐标系（如 UTM / CGCS2000，单位为米）下，`100.0` 代表 100 米，在通常几十公里的 DEM 中可能产生几十个桶；
  - 但在地理坐标系（EPSG:4326，单位为经纬度度数）下，沿海 DEM 景幅通常为 0.1° ~ 0.5°。此时 `span_x = 0.2`，计算得到的 `span_x / 32 = 0.00625`，然而被 `max(100.0, ...)` 强行截断为 `100.0` 度！
  - 结果：对于整个经纬度 DEM，`bucket_size = 100.0`，所有叶单元计算得到的 `bx0 = 0, bx1 = 0, by0 = 0, by1 = 0`，全部挤入唯一的 `(0, 0)` 桶中。空间桶索引彻底退化为包含所有叶单元的单桶，空间索引完全失去分块过滤能力，退化为全局全扫描。
- **改进方案**:
  - 移除对固定数值 `100.0` 的依赖，使索引成为 CRS 坐标单位无关 (CRS-unit agnostic)；
  - `bucket_size_x = max(span_x / num_buckets, span_x * 1e-12, 1e-9)`；
  - 显式钳位桶索引范围至 `[0, num_buckets_per_dim - 1]`，保证数值稳定性；
  - 候选单元去重同时按输入单元顺序保持稳定排序，杜绝非确定性 hash order；
  - 修改 docstring 中关于 “near O(N_blocks)” 的绝对化宣传。

### 2. From-Cache 产物 GeoTIFF Provenance Tags 缺失 (PARTIALLY FIXED / P1)
- **代码位置**: `core/tide_cache.py` 第 1265-1281 行与第 1467-1474 行。
- **问题机理**:
  - `calculate_inundation_from_tide_cache` 虽写入了部分属性，但缺少 `CACHE_SCHEMA_VERSION`、`TIME_START`、`TIME_END`、`TIME_STEP`、`TIMEZONE`、`CONSTITUENTS`、`TIME_INTERVAL_SEMANTICS` 等权威时序回溯元数据；
  - `calculate_exposure_from_tide_cache` 的 `meta_tags` 仅有 6 个键，缺少输入 DEM 名称、源 Cache 名称、时间起止、步长、时区、基准面、目标区域模式等全部科学元数据，导致下游 Exposure GeoTIFF 产物无法通过自身 tags 进行完整科学溯源。
- **改进方案**:
  - 在 `calculate_inundation_from_tide_cache` 与 `calculate_exposure_from_tide_cache` 中从 Cache metadata 中提取完整的权威溯源信息，统一写入所有输出 GeoTIFF 的 tags；
  - 严禁任何外部传入的未生效参数渗入 GeoTIFF tags。

### 3. BatchManifest 科学溯源字段扩展 (PARTIALLY FIXED / P1)
- **代码位置**: `core/batch_raster_engine.py` 第 112-143 行。
- **问题机理**:
  - 当前 `BatchManifest.FIELDS` 记录了时序起止与步长，但缺少 `timezone`、`dem_datum`、`target_mode` 与 `cache_signature`；
  - 导致清单文件中无法直接审计当前瓦片究竟使用了哪种高程基准与目标感知模式。
- **改进方案**:
  - 在 `BatchManifest.FIELDS` 中安全追加 `timezone`、`dem_datum`、`target_mode` 与 `cache_signature`；
  - `load()` 保持向后兼容：旧清单缺失新字段时自动填充 `""`，不中断已有工作流。

### 4. Tide Cache 篡改防御自校验 (PARTIALLY FIXED / P1)
- **代码位置**: `core/tide_cache.py` 第 350-380 行。
- **问题机理**:
  - `inspect_tide_cache_metadata` 目前仅从 NetCDF 中提取 `CACHE_SIGNATURE` 属性值，不校验该签名是否与当前文件中的全局属性一致；
  - 尽管 `validate_tide_cache_compatibility` 包含了签名验证，但若直接通过 `inspect_tide_cache_metadata` 读取元数据，无法获知 Cache 是否曾被手工篡改。
- **改进方案**:
  - 在 `inspect_tide_cache_metadata` 中增加可选 `validate_signature=True` 机制，自重构签名并进行完整性核验，若不一致抛出 `TideCacheIntegrityError` 或记录失败原因。

### 5. GUI 与 CLI From-Cache 交互口径整肃 (STILL BROKEN / P1)
- **代码位置**: `gui/main_window.py` 与 `cli.py`。
- **问题机理**:
  - GUI 切换至 from-cache 模式时，`lbl_batch_samples` 仍旧根据已被禁用的时序控件进行无意义的步数计算并显示（例如显示 `17,568 步`），极易误导用户以为当前界面的时间参数仍然生效；
  - CLI 在执行 `raster batch --mode exposure-from-cache` 时，未向终端输出“时间/基准/分潮/目标模式均由 Tide Cache 决定，Stage 1 命令行选项已被忽略”的透明提示。
- **改进方案**:
  - GUI from-cache 模式下，`lbl_batch_samples` 显示明确提示：“时间采样与科学配置：读取自已存在的 Tide Cache”；
  - CLI from-cache 执行时输出一条清晰的中性通知。

### 6. 文档与代码中的非学术性与过度修辞清理 (DOCUMENTATION ONLY / P2)
- 经过全局文本扫描，确认以下文件存在需要中性化降级的修辞：
  1. `README.md` & `README_EN.md`:
     - 剔除 Snapshot “像元级厘米级水面几何正高” -> 改为中性描述水面高程生成与基准转换；
     - 剔除生态 “耐干旱极限” -> 改为水文暴露指标/环境协变量；
     - 澄清时间语义：明确科学产品默认采用 `[start, end)`，通用单点自定义时序接口保留可配置性；
     - 澄清 Schema 1.1：客观说明 Schema 1.1 可读但缺少终端潮位无法对最后时步进行跨界闭合积分；
     - 降级规则网格批评文风：避免“天然缺陷”、“动辄数十厘米虚假增减水”、“彻底抹平”、“数公里人为断裂”等绝对化词汇；
     - Tide Cache 签名术语：避免使用“防篡改数字签名”等易引起密码学误解的词汇，规范为“兼容性/完整性元数据哈希 (Tamper-evident metadata hash)”。
  2. `gui/manual_dialog.py`:
     - 拓扑阻隔：“彻底阻断” -> 基于目标掩膜降低跨越隔离区域插值风险；
     - From-cache 适用场景：删除“重调容差参数”等错误描述；
     - 终端潮位缺失：删除“末端时步采用截断估算” -> 说明最后区间不计入有效积分并降低有效时间覆盖率；
     - QC=0：“正常高保真解算，无任何降级或近似” -> “未触发当前定义的 Exposure QC 位”；
     - 原子写入：“七大产品整体原子提交” -> “七个 GeoTIFF 分别采用临时文件写入和单文件原子替换”；
     - 硬件推荐：标注为经验建议，非硬性保证。
  3. `data/geoid/README_GEOID.md`:
     - 修正“瞬时平均海平面” -> “模型平均海面参考”；
     - 修正“绝对海拔正高” -> “相对 EGM2008 大地水准面参考的高程”。
  4. `core/raster_engine.py`:
     - 消除 docstring 中的“100% 数学与科学连通域拓扑一致”与“near O(N_blocks)”。

---

## 三、修复规划与门禁验收准则 (Action Plan & Merge Gate Criteria)

1. **核心逻辑修复**:
   - 修复 `core/raster_engine.py` 中的 `LeafCellSpatialIndex`，全面支持经纬度 EPSG:4326、投影坐标系、极小尺度与负经纬度；
   - 完善 `core/tide_cache.py` 与 `core/batch_raster_engine.py` 中的 GeoTIFF tags 与 Manifest provenance；
   - 增加 `inspect_tide_cache_metadata` 篡改检测；
   - 规范 GUI 与 CLI 的 from-cache 状态与提示。
2. **测试强化**:
   - 在 `tests/test_v16_round7_merge_gate.py` 中新增包含随机 Property-style 测试在内的完备测试矩阵；
   - 验证空间桶索引与暴力全扫描在随机各种边界、极端尺度下的精确等价；
   - 验证生产级 Inundation 与 Exposure 产物在使用空间桶索引前后的数值一致性；
   - 验证 from-cache 模式下 tags 严格溯源且不受外部参数污染。
3. **文档与注释整肃**:
   - 完成所有标记文档的去宣传化与科学精准化修改；
   - 最终执行全库单元测试、`compileall` 检查与 GitHub Actions 验证。
