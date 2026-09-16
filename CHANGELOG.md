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
    4. `*_exposure_mean_event_h.tif` (Float32, hours): 平均单次露出事件时长；
    5. `*_exposure_event_count.tif` (UInt32, count): 露出事件完整发生频次；
    6. `*_exposure_valid_time_fraction.tif` (Float32, %): 有效时间数据覆盖比例；
    7. `*_exposure_qc.tif` (UInt16, bitmask): 露出分析专用质量控制位掩膜。
- **时间域跨界线性插值 (Linear Crossing Interpolation)**：
  - 在相邻时间步间检测水面高程跨越地形高程时刻，精确线性求解交点时刻 t*，杜绝整采样步长离散截断量化误差。
- **严格边界判定准则**：
  - 严格定义 $H(t) \le z$ 为露出 (Exposed)，$H(t) > z$ 为淹没 (Inundated)；$H(t) == z$ 严格归属于露出状态。
- **Tide Cache Schema 1.2 升级**：
  - 引入终端时刻潮位采样变量 `tide_msl_terminal_m(node)`，完美闭合时序末端半开区间跨界线性插值。
  - 保持完全向后兼容读取 Schema 1.1 缓存。
  - 新增 `calculate_exposure_from_tide_cache` 实现基于缓存的零 FES 重复调用露出反演。
- **批处理引擎扩展 (`core/batch_raster_engine.py`)**：
  - 新增任务运行模式：`JOB_MODE_EXPOSURE_FROM_CACHE` (`exposure-from-cache`), `JOB_MODE_TIDE_AND_EXPOSURE` (`tide-exposure`), `JOB_MODE_ALL` (`all`)。
  - `BatchManifest` 增加 `elapsed_exposure_seconds` 耗时记录。
- **命令行 CLI 与图形界面 GUI 全面支持露出分析**：
  - CLI 新增 `python cli.py raster exposure` 子命令与 `--mode tide-exposure / exposure / all` 选项。
  - GUI Tab 3 空间任务类型新增“⏳ 潜在天文潮露出时间域分析”，Tab 4 增加对应批处理流程选项。
- **单元测试套件 (`tests/test_exposure_v16.py`)**：
  - 包含常时淹没、常时露出、等高严格边界、线性交点解析解、对称三角波事件统计以及端到端合成 DEM 零 FES 缓存反演验证。

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
