# CoastTideX 更新日志 / Changelog

本项目严格遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 规范与 [语义化版本 2.0.0](https://semver.org/lang/zh-CN/)。

---

## [1.6.0] - 2026-09-16 (Feature / Beta Release)

### 新增 (Added)
- **潮滩/沙滩潜在天文潮露出时间域分析引擎 (`core/exposure_engine.py`)**：
  - 提供单点高精度基准算法 `compute_1d_continuous_exposure`，支持时间跨界线性插值与连续事件统计。
  - 提供二维分块流式累加调度器 `stream_exposure_metrics_interpolation`，空间按 512x512 窗口分块、时间按 1000 步流式分块累加，彻底解耦空间像元与时间采样点，严禁在内存中创建 pixel x time 全时空 3D 矩阵。
  - 输出 7 大独立 GeoTIFF 空间栅格科学产物：
    1. `*_exposure_fraction.tif` (Float32, %): 累计有效潜在露出时间比例；
    2. `*_exposure_duration_h.tif` (Float32, hours): 累计潜在露出时长；
    3. `*_exposure_max_continuous_h.tif` (Float32, hours): 最长单次连续潜在露出时长；
    4. `*_exposure_mean_event_h.tif` (Float32, hours): 平均单次连续潜在露出事件时长；
    5. `*_exposure_event_count.tif` (UInt32, count): 请求时间窗口内识别到的连续潜在露出事件段数量；
    6. `*_exposure_valid_time_fraction.tif` (Float32, %): 有效时间数据覆盖比例；
    7. `*_exposure_qc.tif` (UInt16, bitmask): 露出分析专用质量控制位掩膜。
- **时间域跨界线性插值 (Linear Crossing Interpolation)**：
  - 在相邻时间步间检测水面高程跨越地形高程时刻，精确线性求解交点时刻 t*，避免整采样步长离散截断量化误差。
- **严格边界判定准则**：
  - 严格定义 $H(t) \le z$ 为露出 (Exposed)，$H(t) > z$ 为淹没 (Inundated)；$H(t) == z$ 严格归属于露出状态。
- **Tide Cache Schema 1.2 升级**：
  - 引入终端时刻潮位采样变量 `tide_msl_terminal_m(node)`，闭合时序末端半开区间跨界线性插值。
  - 保持完全向后兼容读取 Schema 1.1 缓存。
  - 新增 `calculate_exposure_from_tide_cache` 实现基于缓存的零 FES 重复调用露出反演。
- **批处理引擎扩展 (`core/batch_raster_engine.py`)**：
  - 支持全部 6 大任务运行模式：`JOB_MODE_TIDE_ONLY` (`tide`), `JOB_MODE_TIDE_AND_INUNDATION` (`tide-inundation`), `JOB_MODE_INUNDATION_FROM_CACHE` (`inundation-from-cache`), `JOB_MODE_TIDE_AND_EXPOSURE` (`tide-exposure`), `JOB_MODE_EXPOSURE_FROM_CACHE` (`exposure-from-cache`), `JOB_MODE_ALL` (`all`)。
  - `BatchManifest` 增加 `elapsed_exposure_seconds`、`exposure_output_dir` 与 `exposure_products_complete` 及 7 大产品路径映射，具备向前向后字段兼容性。
- **命令行 CLI 与图形界面 GUI 全面支持露出分析**：
  - CLI 新增 `python cli.py raster exposure` 子命令，并在 `raster batch` 中全面支持 6 种模式。
  - GUI Tab 3 空间任务类型支持“潜在天文潮露出时间域分析”，动态提议产品文件夹并弹窗展示 7 大产品摘要；Tab 4 增加对应 6 种批处理流程与 9 列状态表格。
- **单元测试套件 (`tests/test_exposure_v16.py`)**：
  - 包含常时淹没、常时露出、等高严格边界、线性交点解析解、对称三角波事件统计以及端到端合成 DEM 零 FES 缓存反演验证。

### 修复与加固 (Fixed & Hardened in v1.6 Beta)
- **GUI 与文档最终一致性对齐收尾 (GUI & Documentation Final Alignment)**：
  - **Tab 3 露出工作流交互全链路贯通**：`RasterTideWorker` 支持 `mode == 'exposure'`，输出控件自适应切换为产品文件夹选择器，隐藏单独 QC 编辑框，执行完毕弹出专属 7 大产品路径及耗时摘要卡片。
  - **设置面板 NetCDF 坐标多形态容错**：`_deep_validate_file` 支持 `latitude`/`lat` 与 `longitude`/`lon` 灵活匹配，严格限制掩膜类别为 0..3 并保持只读。
  - **批量调度 6 模式与 ERROR_IF_EXISTS 统一预检**：基于 `need_tide`、`need_freq`、`need_exp` 早期判定，若当前策略为 `error_if_exists`，对全部 7 个露出产物进行完备冲突检测。
  - **BatchManifest 完整性与向后兼容性**：清单规范扩充露出目录、产物映射与完成度标记，向后兼容读取旧清单。
  - **用户操作手册重写**：重构 `gui/manual_dialog.py` 为 15 章节高保真规范文档，详细说明科学定义、边界条件、7 大产品、6 大模式与基准体系。
  - **消除 Affine 乘法弃用警告**：将 `rasterio` 的 `*` 替换为 `@` 矩阵乘法运算符。
  - **文风整肃与徽章对齐**：移除静态测试数量徽章，统一以 GitHub Actions 动态 CI 状态为准；清退非学术夸大修辞。
- **彻底去除 2D 像元级 Python 循环与 3D 像元-时序立方体内存开销 (P0-1)**：
  - 采用纯二维 NumPy 数组就地维护流式状态转移，单步重构水面切片，经 100 组独立随机时序对比测试，与 1D 参考算法达到精确 0 误差等价。
- **拓扑屏障连通防护深度集成 (P0-2)**：
  - 露出分析全面集成 Target-Mask-Derived Topology Guard，像元仅能在同连通域内选用有效控制节点，跨越陆地阻隔自动回退并标记 `QC_EXP_DEGRADED_CELL`。
- **消除终端水面解算警告 (P0-3)**：
  - 各类预测器统一实现 `predict_points_at_time`，彻底消除 `validate_time_params` 的起止时间警告。
- **控制角点权重重归一化 (P0-4)**：
  - 动态重归一化 1、2、3 个可用节点的权重，绝不以 0m 稀释水面高程。
- **Tide Cache Schema 1.1 与 1.2 兼容性 (P1-1)**：
  - 签名验证自适应识别 schema version，向下无损兼容读取 Schema 1.1 缓存。
- **原子 GeoTIFF 写入安全防护 (P1-2)**：
  - 实现 `_AtomicExposureWriter`，7 大产物基于 `*.tmp.tif` 写入并原子替换，异常或取消时零临时文件残留。
- **NoData 与 QC Sentinel 规范 (P1-3, P1-4)**：
  - `event_count` NoData 规范设为 `4294967295`，与全淹没区域合法的 0 次事件严格解耦。
- **批处理引擎深层产物校验与别名归一 (P1-5, P1-6, P1-7)**：
  - 升级 `_verify_exposure_artifacts` 深度校验全部 7 大产物及其规范签名；清理 `discover_rasters` 重复 `stat()` 调用。
- **FES 掩膜元数据权威审计 (Docs)**：
  - 查证 `fes2022b/mask_fes2022B.nc` 物理尺寸严格为 1,027,081 字节 (0.98 MB)，澄清历史文档中 55.6 MB 与 700 MB 的误植，并产出 `docs/FES_MASK_METADATA_AUDIT.md`。
- **第四轮最终 Hardening 闭环修复 (Round 4 Final Pre-Merge Hardening)**：
  - **P0-1 非 UTC 时区 Stage 1 -> Tide Cache 时间轴对齐**：在 Tide Cache NetCDF 全局属性中规范化写入标准 UTC 锚定字段 (`TIME_START_UTC`, `TIME_END_UTC`, `TIME_START_UTC_EPOCH`, `TIME_END_UTC_EPOCH`)，并在 `core/tide_cache.py` 中强化支持数字秒时间戳与本地时区安全解析，彻底消除非 UTC 时区时间轴平移风险。
  - **P0-2 淹没与露出双引擎拓扑语义科学统一**：抽象共享核心函数 `compute_cell_membership` 与 `resolve_topology_compatible_corners`，严格隔离不同连通域水体，保守处理 component 0 (UNKNOWN) 节点，杜绝未知节点跨盆地渗透污染。
  - **P1 内存预算模型修正**：修正 `RasterTideEngine` 导出 Tide Cache 时的内存预算估算 (`dtype_bytes = 8`，覆盖 raw MSL 与 sorted 数组)，防止内存溢出。
  - **P1 叶单元半开区间单一片区归属**：空间插值叶单元统一执行内部 `[x_min, x_max)` / `[y_min, y_max)` 半开区间归属，仅外边界闭合，彻底消除内部边界像元多单元重复累加。
  - **P1 终端时刻 QC 逐像元精细化**：终端时刻有效性判定由全局变量提升至像元级 `val_term_step`，精准标记局部终端失效像元的 `QC_EXP_TERMINAL_UNAVAILABLE` 并扣减对应 `valid_time_fraction`。
  - **受控真实 FES2022b 经验 Oracle 评测**：基于真实 FES2022b 模型与长江口代表性潮间带地形完成 30 点位对照解算，输出规范误差指标 (Fraction MAE: 0.0395 pp, Duration MAE: 0.0190 h, Event Count error: 0)。
  - **生产场景严密自动化测试套件**：单元测试套件扩充至 143 个并通过完整回归，覆盖端到端非 UTC 转换、混合拓扑隔离、半开边界唯一归属、逐像元终端 QC 与 1D Oracle 多波形等价性。

### 变更 (Changed)
- **统一全系统采样时间语义为严格半开区间 `[start, end)`**：
  - 全年连续与自定义时段默认统一采用 `inclusive="left"`，彻底杜绝端点重复计算与跨年重叠统计。
- **规范术语界定**：
  - 全面规范定义为“固定代表性地形条件下的潜在天文潮露出时长 (Potential Astronomical Tidal Exposure Duration under a Fixed Representative Terrain)”，严禁混淆为“沙滩干燥时长 (Drying Time)”或“二维水动力退水滞后过程”。
- **文档与数据依赖架构梳理**：
  - 抽离历史版本日志至独立 `CHANGELOG.md`。
  - 彻底澄清仓储自带 (Bundled)、外部必须 (External Mandatory)、外部可选 (External Optional) 与本地生成 (Preprocessed Reproduction) 科学数据依赖边界。

---

## [1.5.0-beta] - 2026-09-14 (Real-FES Validation Release)

### 新增 (Added)
- **真实 FES2022b 与真实沙滩/潮滩 DEM 科学验证套件 (`docs/V1_5_BETA_REAL_FES_VALIDATION.md`, `tests/test_v15_beta_real_fes.py`)**：
  - 真实崇明东滩与长兴岛高精度 DEM 实战验证。
  - 真实 FES2022b 原生非结构有限元网格驱动下的全尺度验证。

---

## [1.5.0-alpha] - 2026-09-13 (Batch & Cache Architecture Release)

### 新增 (Added)
- **批量潮间带栅格引擎 (`core/batch_raster_engine.py`)**：
  - 支持文件夹级轻量扫描与顺序安全解算 (`max_parallel_tiles = 1`)。
  - 二阶段架构：Stage 1 控制网格生成与 NetCDF Tide Cache 序列化；Stage 2 零 FES 快速淹没频率反演。
  - 引入防篡改兼容性签名 (Compatibility Signature, SHA-256) 与断点恢复策略 (`ExistingOutputPolicy: RESUME / ERROR_IF_EXISTS / OVERWRITE`)。
  - 任务清单管理 (`batch_manifest.json` 与 `batch_manifest.csv`)。

---

## [1.4.0] - 2026-09-08 (Spatial Raster Tide Engine Release)

### 新增 (Added)
- **空间栅格潮位解算引擎 (`core/raster_engine.py`)**：
  - GeoTIFF 空间单时刻潮位与水面高程解算 (Snapshot Raster)。
  - 自适应四叉树控制网格 (Adaptive Quadtree Control Grid) 与经验互补分布 (CCDF) 潜在天文潮淹没频率计算。
  - 拓扑屏障保护 (Physical Scale Topology Guard)，基于粗粒度物理尺度掩膜与保守连通域分析阻止跨越陆地非法插值。

---

## [1.3.0] - 2026-08-25 (Datum Engine & Multi-Datum Release)

### 新增 (Added)
- **四大多元垂直基准严密转换系统 (`core/datum_engine.py`)**：
  - 支持 MSL、MDT 参考面、EGM2008 正常高与 WGS84 几何椭球高。
  - 引入 $\Delta N$ 全球大洋重力场水准面差值改正栅格 (`delta_n_goco06s_minus_egm2008.tif`) 与地中海/黑海 EIGEN-6C4 区域支持。
