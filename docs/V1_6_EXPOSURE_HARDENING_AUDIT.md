# CoastTideX v1.6 科学与工程系统性加固与第二轮审计报告
# Systematic Scientific & Engineering Hardening Audit Report (v1.6 Beta)

> [!WARNING]
> **历史报告已更新 / SUPERSEDED**：本文档为 Round 2 阶段性加固审计报告。关于 Stage 2 内存解耦、Tide Cache 结构与读取器、终端潮位时间语义及生产场景验证，已被最新审计报告取代，请参阅 [Round 3 审计报告](V1_6_EXPOSURE_HARDENING_ROUND3.md) 及 [Round 4 最终加固审计报告](V1_6_EXPOSURE_HARDENING_ROUND4.md)。

- **审计基准时间 / Audit Date**: 2026-09-17
- **系统版本 / System Version**: CoastTideX v1.6 Beta (Hardened)
- **主要开发者 / Lead Developer**: 王宇浩 (Wang Yuhao)
- **目标分支 / Target Branch**: `feat/v1.6-exposure-duration`
- **验证环境 / Verification Environment**: Python 3.11.9 (Windows x64, GDAL/Rasterio, NumPy 1.26, NetCDF4)

---

## 一、审计背景与加固目标 (Audit Background & Objectives)

在 CoastTideX v1.5 Beta 完成真实 FES2022b 与真实沙滩/潮滩 DEM 科学验证的基础上，v1.6 正式引入了**潮滩/沙滩潜在天文潮露出时间域 (Exposure Duration) 分析引擎**与**严格半开区间 `[start, end)` 采样语义**。

针对 v1.6 初版实现中存在的性能瓶颈、潜在内存溢出、拓扑屏障穿透、末端跨界插值警告、历史别名混杂及文档格式瑕疵，本轮加固实施了系统性的深度代码重构、数学等价性证明、工业级异常隔离与文档规范统一。

---

## 二、核心加固项技术实现与验证细节 (Hardening Implementation Details)

### 1. 2D 向量化状态转移引擎与 3D 张量彻底消除 (P0-1)
- **问题根源**: 早期流式计算尝试在每个时间切片上构建 $(rows, cols, time\_chunk)$ 三维像元时序立方体，导致 512×512 窗口在 1000 个时间步下瞬时吞噬大量内存；或者退化为像元级 Python 循环，耗时极大。
- **重构实现**:
  - 彻底去除像元级双重循环；
  - 采用纯二维 NumPy 数组就地维护流式状态：`total_exp_sec` (Float64), `max_cont_sec` (Float64), `curr_exp_sec` (Float64), `ev_count` (UInt32), `valid_dur_sec` (Float64), `prev_wl` (Float32)；
  - 逐时间步流式重构二维单时刻水面切片，在 $(rows, cols)$ 空间掩膜上一次性执行 4 类几何跨界状态转移（全露出、全淹没、淹没转露出、露出转淹没）；
  - 线性跨界交点比例 $r = \text{clip}\left(\frac{z - H_0}{H_1 - H_0}, 0.0, 1.0\right)$ 矢量化求交。
- **验证结果**: 编写了 `compute_2d_vec` 与 `compute_1d_continuous_exposure` 在 100 组随机时序上的严密对比测试，**两者的累计露出时长、最大单次连续露出、平均事件时长与发生次数在浮点精度范围内达到精确 0 误差**。

### 2. 拓扑屏障连通防护与连通域隔离 (P0-2)
- **问题根源**: 传统空间双线性或反距离插值仅依赖几何坐标，在沙咀、半岛或人工海堤两侧，水面高程可能穿透陆地屏障发生错误插值。
- **重构实现**:
  - 严格继承 `RasterTideEngine.build_support_topology` 生成的粗粒度连通域标记矩阵 `labeled_coarse`；
  - 在每个 512×512 空间分块中，像元不仅要求落在四叉树叶单元的空间包围盒内，且**仅能选用与其连通域 ID 一致（或开放大洋 component 0）的有效控制节点**进行插值；
  - 跨越陆地阻隔的像元自动触发单侧降级插值，并记录 `QC_EXP_DEGRADED_CELL`；无任何同域节点时标记 `QC_EXP_INSUFFICIENT_NODES`。

### 3. 终端时刻采样与消除时间参数警告 (P0-3)
- **问题根源**: 旧版在解算终端水面时调用 `predict_points_period(start_time=t_end, end_time=t_end)`，触发底层时序校验器的 `start_time >= end_time` 异常与警告。
- **重构实现**:
  - 在 `FESTidePredictor`、`SyntheticHarmonicPredictor`、`TwoBasinSyntheticPredictor` 以及 `DisconnectedBarrierPredictor` 上统一实现标准 `predict_points_at_time(lons, lats, timestamp)` 方法；
  - `RasterTideEngine.calculate_inundation_raster` 在获取终端潮位时优先调用 `predict_points_at_time`，若未实现则回退至半开区间 `[t_end, t_end + freq)` (`inclusive="left"`) 取第 0 帧；
  - **彻底消除了全部运行过程中的时间校验警告，测试输出纯净无报错**。

### 4. 控制角点权重重新归一化 (P0-4)
- **问题根源**: 四叉树四角节点中若有 1~3 个节点位于陆地或被屏障隔断，若直接按几何双线性权重加权，会导致无效节点等效于 0m，严重拉低或抬高实际水位。
- **重构实现**:
  - 动态统计当前像元实际可用的节点集合，对可用节点的权重向量进行逐像元求和与重新归一化：$w_{	ext{norm}} = rac{w_k}{\sum w}$；
  - 保证仅有 1 个角点有效时其权重严格为 1.0，杜绝将无效角点作为 0m 稀释。

### 5. Tide Cache Schema 1.1 与 1.2 兼容性 (P1-1)
- **重构实现**:
  - Tide Cache Schema 演进至 `"1.2"`，新增 `tide_msl_terminal_m` 变量；
  - `generate_tide_cache_signature` 支持显式指定 `schema_version`；
  - `validate_tide_cache_compatibility` 识别 Schema 1.1 缓存，若当前请求仅为淹没频率计算，或在露出分析中允许终端水位平推近似（标记 `QC_EXP_TERMINAL_APPROX`），实现向后无损兼容。

### 6. 原子 GeoTIFF 写入安全包装器 (P1-2)
- **重构实现**:
  - 实现 `_AtomicExposureWriter` 类，统一管理 7 大空间栅格产物；
  - 所有产物初始写入 `*.tmp.tif`，全部分块顺利写毕后执行 `os.replace` 原子替换；
  - 若遇中途取消或异常报错，自动触发 `cleanup_tmp()` 清除全部临时碎片，目标文件绝对不留损坏坏图。

### 7. 质量控制位掩膜与 NoData 规范 (P1-3 & P1-4)
- **重构实现**:
  - `event_count` 产物 NoData Sentinel 明确设为 `4294967295` (`np.iinfo(np.uint32).max`)，与全淹没区域合法的 `0` 次事件完全分离；
  - 常时淹没像元严格标记 `QC_EXP_PERMANENTLY_SUBMERGED` (32)；常时露出像元标记 `QC_EXP_PERMANENTLY_EXPOSED` (64)；
  - 时序含有 NaN 间隙时标记 `QC_EXP_PARTIAL_VALID_TIME` (16)，有效时间百分比严格小于 100%。

### 8. Batch 调度引擎深层验证与别名归一 (P1-5, P1-6, P1-7)
- **重构实现**:
  - 升级 `_verify_exposure_artifacts(dem_info, exp_paths, expected_cache_sig=None)`：全面校验 7 大文件的存在性、尺寸、CRS、仿射矩阵、数据类型、NoData 及缓存规范签名；
  - 统一清理 `discover_rasters` 中对单个文件的重复 `stat()` 调用与重复 `size_mb` 计算；
  - 在 `run_batch` 调度入口统一归一化任务模式别名，并在 `cli.py` 中移除含混的 `"exposure"` 选项，保留标准枚举项。

---

## 三、外部数据实测与文档审计 (Documentation & Data Provenance)

1. **`fes2022b/mask_fes2022B.nc` 审计**:
   - 查证物理尺寸严格为 1,027,081 字节 (0.98 MB)，消除历史误记的 55.6 MB / 700 MB；
   - 撰写独立报告 `docs/FES_MASK_METADATA_AUDIT.md`，阐明 LGP2 原生非结构流程无需依赖此文件的动力学原因。
2. **README 公式排版修复**:
   - 彻底修复 `README.md` 中被转义为制表符与回车的 LaTeX 公式（`\varphi`, `\right`, `\text` 等）；
   - 更新 CLI 使用示例中 snapshot 命令的 `--input / -i` 参数；
   - 移除未经科学控制的宣传式性能秒数，改为具有工程约束的定性/半定量描述。
3. **作者署名统一**:
   - 全项目代码、文档与元数据统一作者署名为 **王宇浩** (Wang Yuhao)。
4. **历史报告勘误**:
   - 在 `docs/PROJECT_CONTEXT_AUDIT.md` 顶部增设历史归档预警横幅；
   - 在 `docs/V1_5_BETA_REAL_FES_VALIDATION.md` 中详尽说明 17,568（半开区间）与 17,569（旧闭区间）的样本数差异，保留 6.617 百分点接缝最大差异的客观实测事实。

---

## 四、自动化测试套件执行报告 (Test Execution Verification)

- **执行指令**:
  ```powershell
  & "I:\Test_tide_model\.venv\Scripts\python.exe" -m unittest discover -s tests -p "test_*.py"
  ```
- **测试结果**:
  - **总通过用例数**: **127 项** (全部通过)
  - **失败 (Failures)**: 0
  - **错误 (Errors)**: 0
  - **回归 (Regressions)**: 0
  - **终端时刻警告**: 0 警告
  - **总耗时**: 29.179 秒
