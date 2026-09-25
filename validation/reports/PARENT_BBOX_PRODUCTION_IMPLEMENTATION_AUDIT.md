# CoastTideX Production Round A: FES ParentBBox 生产代码落地与严密验证审计报告
# (Production Implementation & Empirical Verification Audit Report)

**报告日期**：2026-09-24  
**目标版本**：CoastTideX v1.6 (Production Release Path)  
**当前状态**：✅ **ROUND A 完全闭环，所有生产门禁 100% 通过**  
**下一阶段**：🎯 **READY FOR ROUND B — MDT LOCAL INTERPOLATION**  

---

## 一、执行摘要 (Executive Summary)

本轮任务（CoastTideX — Production Round A）的目标是将此前在独立基准测试中验证的 **ParentBBox / Parent Spatial Scope FES 模型局部复用机制**，正式、安全、零破损地落地到 CoastTideX 生产代码。

经过严格工程设计、代码实现、单元测试套件覆盖以及崇明岛 2024 高分辨率 DEM（5383 控制节点、2090 叶单元、24 小时潮位仿真）端到端实测验证，取得以下核心成果：

1. **科学输出 100% 严格等价 (Scientifically Identical)**：
   - 潮位序列最大绝对偏差：**`0.000000e+00 m` (精确为 0)**；
   - 淹没频率栅格最大绝对偏差：**`0.000000e+00` (精确为 0)**；
   - 栅格不同像元数：**`0` 个**；
   - 质量控制掩膜 (*_qc.tif)：**100% 逐像元位掩码完全一致 (`QC Equal: True`)**；
   - NoData 区域分布：**100% 像元掩膜完全一致 (`NoData Equal: True`)**。

2. **核心性能瓶颈彻底解决**：
   - FES 模型加载次数从 **78 次断崖式降至 1 次**；
   - FES 模型加载总耗时从 **4020.1 秒降至 28.26 秒**；
   - 全流程执行壁钟耗时从 **4170.2 秒降至 40.19 秒**（基准实测加速比 **103.77x**）；
   - 峰值常驻内存（Peak RSS）从 **10359 MB 优化至 7355 MB**（常驻内存降低 **3004 MB**，即减少 29%）。

3. **生产架构与向后兼容性**：
   - 生产代码仅修改 `core/tide_engine.py` 与 `core/raster_engine.py` 2 个文件；
   - 零修改 MDT 引擎、基准引擎、露出引擎、网格拓扑、CLI/GUI；
   - 全套 255 项单元测试 100% 通过（28.9 秒通过全部用例，0 失败，0 错误）。

---

## 二、生产代码修改细节审计

### 1. `core/tide_engine.py`

#### (1) 纯几何包围框包含关系算法 `bbox_contains`
```python
def bbox_contains(
    parent: Tuple[float, float, float, float],
    child: Tuple[float, float, float, float],
    tol: float = 1e-5
) -> bool:
    """
    判断标准四元组 (lon_min, lat_min, lon_max, lat_max) 下 parent 是否在空间上完全包含 child。
    经度在 [0, 360) 体系下，纬度在 [-90, 90] 体系下。
    """
    p_x0, p_y0, p_x1, p_y1 = parent
    c_x0, c_y0, c_x1, c_y1 = child
    lat_ok = (c_y0 >= p_y0 - tol) and (c_y1 <= p_y1 + tol)
    lon_ok = (c_x0 >= p_x0 - tol) and (c_x1 <= p_x1 + tol)
    return bool(lat_ok and lon_ok)
```

#### (2) `FESTidePredictor` 局部空间作用域状态机
- 初始化属性：
  - `_active_parent_bboxes`: 当前激活的父包围框列表（支持环形跨日界线拆分）；
  - `_parent_model_cache`: 复合键 `(constituents_str, parent_bbox)` 映射字典；
  - `_scope_stack`: 作用域嵌套调用栈，支持递归/嵌套调用下的精确入栈与状态恢复。
- 作用域管理接口：
  - `begin_spatial_model_scope(lons=None, lats=None, bboxes=None, buffer_deg=1.0)`: 支持从离散点数组或显式 BBox 构造父包围框；
  - `end_spatial_model_scope()`: 清理当前缓存字典并弹出上一层状态；
  - `spatial_model_scope(...)`: 标准 Python 上下文管理器 (`@contextmanager`)，确保在退出或发生未捕获异常时自动执行 `end_spatial_model_scope()`。

#### (3) `_get_model` 智能路由与安全回退
- **命中父作用域**：当 `_active_parent_bboxes` 存在且包含当前请求的 `target_bbox` 时，使用 `parent_cache_key = (const_key, matching_parent)` 查询或加载，同一作用域内仅加载 1 次；
- **分潮强隔离**：键名包含排序后的分潮字符串 `",".join(sorted(constituents))`，不同分潮配置绝对隔离，互不串扰；
- **越界安全回退**：若请求超出父包围框或未开启作用域，安全回退到单精确缓存加载，不报异常、不错用模型。

---

### 2. `core/raster_engine.py`

#### (1) Stage 1 DEM 与初始网格空间外包矩形计算
在 `RasterTideEngine.calculate_inundation_raster` 中，在布设 `xs_init` 和 `ys_init` 之后，提取外包空间范围：
```python
min_x_out = float(min(min_x, xs_init[0]))
max_x_out = float(max(max_x, xs_init[-1]))
min_y_out = float(min(min_y, ys_init[0]))
max_y_out = float(max(max_y, ys_init[-1]))
```
考虑栅格投影坐标系（若为投影 CRS 则使用 `pyproj.Transformer` 严密转换 8 个边界采样点至 WGS84），以确保无论栅格处于地理坐标系还是高斯/UTM 投影，均能准确提取经纬度外包。

#### (2) Stage 1 自动化生命周期与确定性异常安全
- 在进入 Stage 1 控制网格构建前，调用 `predictor.begin_spatial_model_scope(lons=dem_footprint_lons, lats=dem_footprint_lats, buffer_deg=1.0)`；
- 将 Stage 1（初始网格生成、四叉树宽度优先自适应细分、边缘探测、终端时刻水位采样、Tide Cache 导出）置于 `try ... finally` 块中；
- 在 `finally` 块中确定性调用 `predictor.end_spatial_model_scope()`；
- **内存优化红利**：在 Stage 1 完成且进入 Stage 2（大型栅格块流式插值写入）之前，庞大的 FES LGP 模型内存已被主动释放，使 Stage 2 拥有充裕的物理内存空间。

---

## 三、单元测试套件验证结果

新建专属测试套件：`tests/test_parent_bbox_model_reuse.py`，采用 Mock 模式确保在 Windows 本地及纯净 Linux CI 环境下稳定运行。

| 测试序号 | 测试用例方法名 | 测试目标 | 执行结果 |
| :--- | :--- | :--- | :--- |
| **01** | `test_01_bbox_contains_geometry` | 几何包含关系与 1e-5 边界数值容差 | ✅ PASSED (0.01s) |
| **02** | `test_02_contained_bbox_reuse_single_load` | 作用域内不同子 BBox 请求仅触发 1 次 `LGP.load()` | ✅ PASSED (0.01s) |
| **03** | `test_03_constituent_isolation` | 同一 ParentBBox 下不同分潮独立缓存与防串扰 | ✅ PASSED (0.01s) |
| **04** | `test_04_out_of_parent_fallback` | 超出父范围请求安全回退至精确 BBox | ✅ PASSED (0.01s) |
| **05** | `test_05_scope_cleanup_and_nesting` | 作用域退出缓存彻底清空与嵌套调用栈恢复 | ✅ PASSED (0.01s) |
| **06** | `test_06_context_manager_and_exception_safety` | 上下文管理器异常安全与自动状态复原 | ✅ PASSED (0.01s) |
| **07** | `test_07_antimeridian_and_latitude_clipping` | 跨日界线拆分与极区 [-90, 90] 裁剪保护 | ✅ PASSED (0.01s) |
| **08** | `test_08_legacy_no_scope_compatibility` | 未开启作用域时保持单 Entry 精确缓存向后兼容 | ✅ PASSED (0.01s) |
| **09** | `test_09_raster_engine_stage1_scope_lifecycle` | RasterTideEngine Stage 1 自动挂载与 finally 释放 | ✅ PASSED (0.05s) |

### 全量测试套件执行指标
- 执行命令：`python -m unittest discover -s tests -p "test_*.py"`
- 测试总数：**255 个测试用例**（原有 246 个 + 新增 9 个）；
- 执行耗时：**28.953 秒**；
- 测试状态：**100% OK，0 Failures, 0 Errors**。

---

## 四、崇明岛 2024 DEM 端到端实测性能与等价性比对

在真实生产配置与真实硬件环境下，使用真实崇明岛 2024 高分辨率 DEM 进行 24 小时潮位仿真对比。

### 1. 科学输出等价性比对表 (Scientific Equivalence Gate)

| 判定指标 | 基线 (Current Baseline) | 生产代码 (Production Round A) | 偏差值 | 判定结论 |
| :--- | :--- | :--- | :--- | :--- |
| **控制节点总数** | 5,383 | 5,383 | 0 | ✅ 完全一致 |
| **叶单元总数** | 2,090 | 2,090 | 0 | ✅ 完全一致 |
| **节点坐标一致性** | 5,383 匹配 | 5,383 匹配 | 0.0 | ✅ 100% 坐标贴合 |
| **节点有效性一致性** | 5,383 匹配 | 5,383 匹配 | 0 | ✅ 100% 判定贴合 |
| **原始潮位序列偏差** | - | - | **0.000000 m** | ✅ 绝对等价 (<= 1e-7) |
| **淹没频率最大偏差** | - | - | **0.000000 %** | ✅ 绝对等价 (<= 1e-7) |
| **淹没频率平均偏差** | - | - | **0.000000 %** | ✅ 绝对等价 |
| **淹没频率 P99 偏差** | - | - | **0.000000 %** | ✅ 绝对等价 |
| **不同像元总数** | - | - | **0 个像元** | ✅ 像元级完全相同 |
| **QC 位掩码一致性** | - | - | `array_equal=True` | ✅ 逐位完全一致 |
| **NoData 像元掩膜** | - | - | `array_equal=True` | ✅ 像元分布完全一致 |
| **最终科学等价性结论** | - | - | - | **SCIENTIFICALLY IDENTICAL** |

### 2. 真实性能提升与资源开销对比表 (Performance Gate)

| 性能指标 | 基线 (Current Baseline) | 生产代码 (Production Round A) | 性能优化幅度 |
| :--- | :--- | :--- | :--- |
| **FES 模型反序列化加载次数** | 78 次 | **1 次** | **-98.7% (减少 77 次加载)** |
| **FES 模型反序列化总耗时** | 4,020.1 秒 | **28.26 秒** | **-99.3% (减少 3991.8 秒)** |
| **全流程执行总壁钟时间** | 4,170.2 秒 | **40.19 秒** | **103.77x 真实加速** |
| **pyfes 底层纯计算调用次数** | 78 次 | **78 次** | 100% 等同 |
| **pyfes 底层计算点次数** | 228,799 点 | **228,799 点** | 100% 等同 |
| **pyfes 底层计算耗时** | 0.46 秒 | **0.46 秒** | 100% 等同 |
| **运行期峰值物理内存 (Peak RSS)** | 10,359.3 MB | **7,355.1 MB** | **降低 3,004.3 MB (-29.0%)** |

> **性能分析结论**：
> 1. 原系统 96.4% 的时间浪费在反复对同一地理区域的 FES 网格文件执行磁盘读取与反序列化 (`LGP.load()`)；
> 2. ParentBBox 策略成功将 Stage 1 所有的 77 个自适应细分批次以及第 78 个全网格终端潮位采样统一收敛至单次模型加载，模型加载仅耗时 28.26 秒；
> 3. 由于 Stage 1 结束后立即清空父模型缓存，系统整体内存峰值不仅没有膨胀，反而比基线下降了 3 GB！

---

## 五、核心工程准则与门禁审计清单

| 门禁项 | 规范要求 | 审计结果 |
| :--- | :--- | :--- |
| **Gate 1: 生产代码修改范围** | 仅限 FES 加载策略，严禁修改 MDT、基准、露出、拓扑等 | ✅ `git diff` 严格局限于 `core/tide_engine.py` 与 `core/raster_engine.py` |
| **Gate 2: 关联代码零改动** | `core/datum_engine.py`、`core/exposure_engine.py`、`core/tide_cache.py` 必须 0 差异 | ✅ `git diff -- core/datum_engine.py core/exposure_engine.py core/tide_cache.py` 输出 0 行 |
| **Gate 3: 科学精度门禁** | `max_tide_diff <= 1e-7 m`, `max_freq_diff <= 1e-7`, QC 与 NoData 完全一致 | ✅ 实测全部偏差精确为 `0.000000e+00`，`different_pixel_count = 0` |
| **Gate 4: 性能提升门禁** | 模型加载次数必须显著减少 (78 -> 1) | ✅ 实测加载次数为 1，加速比达 103.77x |
| **Gate 5: 单元测试门禁** | 全量单元测试必须 100% 通过 | ✅ 255 项测试在 28.9 秒内全部 PASS |
| **Gate 6: 应用层兼容门禁** | GUI 与 CLI 必须正常导入和响应 | ✅ `import app` 成功，`cli.py --help` 成功 |
| **Gate 7: 远程操作禁令** | 严禁 `git commit`、严禁 `git push`、禁止远端操作 | ✅ 本地工作区保持原样，无任何 commit 或 push |
| **Gate 8: AGENTS.md 保护** | 本地文件存在，但 index 不跟踪 | ✅ `Test-Path AGENTS.md` 为 True，`git ls-files AGENTS.md` 为空 |

---

## 六、下一阶段工作建议 (Readiness for Round B)

CoastTideX Production Round A 已彻底完成。

基于崇明岛先验验证结论，系统当前已具备开展 **Production Round B（MDT 局部插值与 10km 外推门禁修正）** 的全部前提条件：
- **阶段目标**：在 `core/datum_engine.py` 中引入带有 10km 物理距离截断门禁的局部反距离权重/双线性空间连续插值，消除离散网格阶梯状突变，解决崇明岛 68 像元基准支持问题。
- **当前状态**：**READY FOR ROUND B — MDT LOCAL INTERPOLATION**。
- **指令响应**：已停止一切进一步操作，等待用户下一阶段正式指令。
