# CoastTideX Production Round A: FES ParentBBox 模型复用架构设计
## (FES ParentBBox Model-Reuse Optimization Architecture Design)

> **文档标识**: `validation/reports/PARENT_BBOX_PRODUCTION_DESIGN.md`  
> **设计日期**: 2026-09-24  
> **所属版本**: CoastTideX v1.6+ Production  
> **核心目标**: 消除自适应四叉树 Stage 1 控制节点解算中的 FES 模型重复加载 (Cache Thrashing)，实现生产环境下的 ParentBBox 空间作用域安全复用。

---

## 一、当前生产环境性能瓶颈与物理根因 (Problem Definition)

### 1.1 现状与瓶颈数据
在当前生产实现中，`FESTidePredictor` 维护了单一精确包围框缓存 (`_cached_bbox`, `_cached_constituents`, `_cached_model`)：
```python
if (self._cached_model is not None and
    self._cached_bbox == target_bbox and
    self._cached_constituents == sorted(constituents)):
    return self._cached_model
```
在 `RasterTideEngine.calculate_inundation_raster` 执行 Stage 1 自适应四叉树控制节点解算时：
- 节点按批次（`control_node_batch_size = 500`）流式提交给 `predict_points_period`；
- 每个批次根据当前子节点的经纬度极值计算局部外扩包围框：`build_circular_fes_bboxes(sub_lons, sub_lats, buffer_deg=0.5)`；
- 四叉树细分产生的各批次节点坐标在空间上不断离散变化，导致连续传入 `_get_model` 的 `cur_bbox` 存在微小的浮点差异；
- `_cached_bbox == target_bbox` 判定 **100% 失败 (Cache Miss)**。

### 1.2 实测恶果 (Empirical Evidence)
在崇明岛 24h 仿真（228,799 个解算点）中：
- `pyfes.evaluate_tide` 真正的 C++ 调和常数内插计算仅耗时 **1.77 秒**；
- 但由于 `_cached_bbox` 判定失败，重复触发了 **78 次** 全局非结构网格 NetCDF 模型的磁盘 I/O、内存树构建与反序列化，累计耗时高达 **4,020.13 秒（约 67 分钟）**，占全流程时间的 **96.4%**！
- 频繁的 C++ `TidalModel` 对象创建与析构造成系统内存严重碎片化，WorkingSet RSS 峰值被推高至 **10.36 GB**。

---

## 二、生产架构设计原则 (Production Architecture Principles)

为彻底解决该问题，同时确保严谨的科学正确性与模块解耦，生产实现遵循以下七大核心原则：

### 1. 栅格生命周期绑定 (Raster-Scoped Lifecycle)
- FES 模型复用必须建立在**明确的局部空间生命周期**之内，称为 `spatial_model_scope`。
- 生命周期从当前 DEM Stage 1 启动时建立，到 Stage 1 控制节点与终端潮位 $H(\text{end})$ 解算完成时销毁并清空内存，严禁将上一幅 DEM 的父包围框模型无控泄漏到下一幅完全无关的 DEM。

### 2. 动态足迹推导，严禁区域硬编码 (Zero Geographic Hardcoding)
- 严禁在核心算法中硬编码任何特定海区的经纬度常数（如崇明 `119.5, 29.5, 123.5, 33.5`）。
- 父包围框通过输入 GeoTIFF 栅格的物理元数据（`info.bounds` 与 `info.crs`）动态投影到 WGS84，并叠加 `buffer_deg = 1.0°`（或配置的默认缓冲半径）自动生成。

### 3. 几何严格包含判定 (Strict Geometric Containment Guard)
- 任何时候复用父模型前，必须先在几何上严格验证：**Parent BBox 完全包含 Requested Sub-BBox**。
- 若请求的包围框超出当前激活的 Parent 范围，**绝对禁止盲目复用**，必须安全回退至精确独立加载（Fallback Exact Load）。

### 4. 环形经度与极区纬度兼容 (Antimeridian & Meridian Circular Awareness)
- 充分复用 `core/utils.py` 中的 `build_circular_fes_bboxes()`：
  - 经度在 $[0, 360)$ 体系下连续无缝处理国际日期变更线（Antimeridian, $\pm 180^\circ$）；
  - 跨越格林尼治子午线（$0^\circ / 360^\circ$）时，自适应拆分为紧凑的两个父包围框，而非生成跨越全球的 $360^\circ$ 巨大包围框；
  - 纬度边界严格裁剪在 $[-90.0, +90.0]$ 物理区间内。

### 5. 缓存标识完整性 (Complete Cache Identity)
- 缓存键值必须由 `(constituents_key, parent_bbox)` 复合构成。
- 同一 Parent 空间范围内，若请求的分潮组合不同（如 `all34` vs `major8`），必须作为独立模型实例加载，杜绝跨分潮方案的错误复用。

### 6. 单点预测与通用 API 零破坏 (100% Backward Compatibility)
- 未显式开启 `spatial_model_scope` 时，`FESTidePredictor` 维持原版单精确包围框缓存行为，单点预测、时序预测、CLI 及 GUI 单点功能完全不受影响。

### 7. 异常防御与无全局可变状态 (Exception Safety & No Global State)
- 采用 Python 上下文管理器（`@contextmanager`）配合 `try...finally`，任何意外异常均能确保父缓存安全清空并恢复调用前状态。

---

## 三、核心技术实现方案 (Technical Specifications)

### 3.1 空间包围框几何包含算法
在 `core/tide_engine.py` 中引入无依赖的几何包含判别函数：
```python
def bbox_contains(parent: tuple, child: tuple, tol: float = 1e-5) -> bool:
    """
    判断标准四元组 (lon_min, lat_min, lon_max, lat_max) 下 parent 是否在空间上完全包含 child。
    经度在 [0, 360) 体系下，纬度在 [-90, 90] 体系下。
    """
    p_x0, p_y0, p_x1, p_y1 = parent
    c_x0, c_y0, c_x1, c_y1 = child
    lat_ok = (c_y0 >= p_y0 - tol) and (c_y1 <= p_y1 + tol)
    lon_ok = (c_x0 >= p_x0 - tol) and (c_x1 <= p_x1 + tol)
    return lat_ok and lon_ok
```

### 3.2 FESTidePredictor 上下文管理器与 _get_model 改动
在 `FESTidePredictor` 内部扩展：
```python
class FESTidePredictor:
    def __init__(self, ns_grid_path: str = None):
        ...
        # 现有单精确包围框缓存 (向后兼容)
        self._cached_model = None
        self._cached_bbox = None
        self._cached_constituents = None

        # 作用域父包围框缓存 (Parent Scope)
        self._active_parent_bboxes: Optional[List[Tuple[float, float, float, float]]] = None
        self._parent_model_cache: Dict[Tuple[str, Tuple[float, float, float, float]], Any] = {}

    @contextmanager
    def spatial_model_scope(
        self,
        lons: Optional[Any] = None,
        lats: Optional[Any] = None,
        bboxes: Optional[Sequence[Tuple[float, float, float, float]]] = None,
        buffer_deg: float = 1.0
    ):
        prev_parents = self._active_parent_bboxes
        prev_cache = self._parent_model_cache

        # 动态推导激活的父包围框列表
        if bboxes is not None:
            if isinstance(bboxes, tuple) and len(bboxes) == 4 and all(isinstance(x, (int, float)) for x in bboxes):
                new_bboxes = [bboxes]
            else:
                new_bboxes = list(bboxes)
        elif lons is not None and lats is not None:
            lons_arr = np.atleast_1d(np.asarray(lons, dtype=float))
            lats_arr = np.atleast_1d(np.asarray(lats, dtype=float))
            lons_norm = np.array([normalize_longitude(x, to_360=True) for x in lons_arr], dtype=float)
            new_bboxes = build_circular_fes_bboxes(lons_norm, lats_arr, buffer_deg=buffer_deg)
        else:
            new_bboxes = []

        self._active_parent_bboxes = new_bboxes
        self._parent_model_cache = {}

        try:
            yield self
        finally:
            self._parent_model_cache.clear()
            self._active_parent_bboxes = prev_parents
            self._parent_model_cache = prev_cache

    def _get_model(self, bbox: tuple[float, float, float, float], constituents: list):
        target_bbox = (
            float(bbox[0]),
            max(-90.0, float(bbox[1])),
            float(bbox[2]),
            min(90.0, float(bbox[3]))
        )
        const_key = ",".join(sorted(constituents))

        # 1. 处于激活的父包围框作用域中时，优先匹配包含该子区域的父模型
        if self._active_parent_bboxes:
            matching_parent = None
            for pb in self._active_parent_bboxes:
                if bbox_contains(pb, target_bbox):
                    matching_parent = pb
                    break

            if matching_parent is not None:
                parent_key = (const_key, matching_parent)
                if parent_key in self._parent_model_cache:
                    return self._parent_model_cache[parent_key]

                # 首次加载父模型
                lgp_config = cfg.LGP(
                    path=self.ns_grid_path,
                    type='lgp2',
                    codes='lgp2',
                    constituents=constituents,
                    bbox=matching_parent
                )
                model = lgp_config.load()
                self._parent_model_cache[parent_key] = model
                return model

        # 2. 未开启作用域或子区域超出父包围框时，安全回退到精确单缓存加载
        if (self._cached_model is not None and
            self._cached_bbox == target_bbox and
            self._cached_constituents == sorted(constituents)):
            return self._cached_model

        lgp_config = cfg.LGP(
            path=self.ns_grid_path,
            type='lgp2',
            codes='lgp2',
            constituents=constituents,
            bbox=target_bbox
        )
        model = lgp_config.load()
        self._cached_model = model
        self._cached_bbox = target_bbox
        self._cached_constituents = sorted(constituents)
        return model
```

### 3.3 RasterTideEngine Stage 1 接入
在 `core/raster_engine.py` 的 `calculate_inundation_raster()` 中：
1. 通过 `info.bounds` 与 `info.crs` 提取 GeoTIFF 四个角点并转换为 WGS84 坐标序列 `(dem_lons, dem_lats)`；
2. 获取当前 `predictor`，通过 `hasattr(predictor, "spatial_model_scope")` 创建上下文；
3. 将作用域覆盖整个 Stage 1 核心计算流程：
   - 初始粗网格控制节点（Initial Nodes）批次计算；
   - 自适应四叉树细分（Adaptive Refinement）所有后续批次计算；
   - 终端时刻潮位采样（Terminal Tide at `end_time`）的解算。
4. Stage 1 完成且 Tide Cache 写入后，上下文自动退出，销毁父模型，释放底层内存，无缝进入纯基于缓存插值的 Stage 2。

---

## 四、验证与回归测试策略 (Verification Plan)

### 4.1 专用单元测试套件 (`tests/test_parent_bbox_model_reuse.py`)
覆盖 8 大边界用例：
1. **Contained bbox reuse**: 父包围框内多次子请求，LGP 仅加载 1 次；
2. **Different constituents**: 相同父包围框，不同分潮组合（`all34` vs `major8`）正确产生不同模型；
3. **Out-of-parent request**: 超出父包围框请求安全回退精确加载，不发生错误串用；
4. **Scope cleanup**: 上下文退出后，父缓存完全清空，不遗留陈旧状态；
5. **Exception cleanup**: 上下文内部抛出异常时，`finally` 保证状态恢复；
6. **Antimeridian handling**: 国际日期变更线附近（$179^\circ \text{E}$ 与 $-179^\circ \text{W}$）正确按经度圆弧处理，不生成 $359.6^\circ$ 伪全球包围框；
7. **Latitude clipping**: 接近 $\pm 90^\circ$ 极区时，纬度严格截断在 $[-90, 90]$；
8. **Generic no-scope compatibility**: 未开启上下文时的单点或时序调用，行为与基线 100% 一致。

### 4.2 崇明岛真实数据端到端科学等价性回归 (Chongming End-to-End Regression)
- **输入数据**: `ChongMing_2024_Elevation.tif` (2024-01-01 00:00:00 至 2024-01-02 00:00:00, 1h 步长, 24 个 regular 采样点 + 1 个 terminal 采样点, EGM2008 基准, 4000m/500m 自适应控制网格)。
- **比对基线**: `validation/artifacts/chongming_verify/` 下已有的 `smoke_cache_current_clean.nc`、`smoke_freq_current_clean.tif` 与 `smoke_qc_current_clean.tif`。
- **通过准则 (Gate Criteria)**:
  - 控制节点总数、坐标、有效性标志 100% 相同；
  - 节点潮位数组最大绝对差 $\le 10^{-7}\text{ m}$；
  - 输出淹没频率栅格像元最大绝对差 $\le 10^{-7}$；
  - QC 矩阵与 NoData 掩膜严格完全相等（`exact array equality`）；
  - 模型加载次数显著下降（由 78 次降至 1 次或极少数次）。

---
*(设计审查完毕，即将进入正式代码实施阶段)*
