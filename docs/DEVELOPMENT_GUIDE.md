# CoastTideX 代码规范与双语开发指南 / Development & Bilingual Documentation Guide

版本: v1.6 Beta | 维护者: 王宇浩 (Yuhao Wang) | 机构领域: 沿海海洋动力学与大地测量学 (Coastal Ocean Dynamics & Geodesy)

---

## 一、 双语注释与代码规范 / Bilingual Code & Comment Standards

CoastTideX 作为一个具备严谨学术水准的开源科研工程系统，要求所有核心算法、数据结构、公开接口与模块文档必须遵循**中英双语 (Chinese & English)** 规范。

### 1. 务实双语推进原则 (Pragmatic Bilingual Policy)
- **强制要求范围 (Mandatory)**：所有新功能开发、新增模块、新暴露 API、修改代码、核心科学定义与数学公式注释，必须完整配备中英双语。
- **渐进迭代范围 (Gradual)**：对于既有稳定历史模块（如底层驱动适配），采取渐进式补充维护策略，严禁为了纯双语形式化而做破坏性的大规模重构。

### 2. 模块级文档字符串 (Module Docstring)
- 每个 `.py` 核心文件顶部必须包含中英双语模块说明、核心科学定义、理论公式与内存管理不变量。
- 必须明确注明模块的数学解析基础与质量控制 (QC) 位掩膜定义。

### 3. 公共函数与类说明 (Function & Class Docstrings)
- 参数名、返回值、数据类型与异常说明必须包含中英双语注释。
- 关键算法步骤应在函数内部使用简明双语单行注释标记流水线阶段。

```python
def compute_1d_continuous_exposure(
    water_levels: np.ndarray,
    timestamps_seconds: np.ndarray,
    elevation: float,
    terminal_water_level: Optional[float] = None,
    terminal_timestamp_seconds: Optional[float] = None
) -> Dict[str, Any]:
    """
    单点一维连续潜在天文潮露出时间域基准算法 (包含跨界线性插值与事件统计)。
    1D reference algorithm for continuous potential tidal exposure with crossing interpolation.

    参数 / Parameters:
        water_levels: 各采样时刻的水位序列 [米] / Water level series at sample timestamps [m]
        timestamps_seconds: 各采样时刻的纪元秒时间戳 / Epoch timestamps in seconds
        elevation: 地形高程 z [米] / Terrain surface elevation z [m]
        terminal_water_level: 终端时刻 H(t_end) [米] / Terminal water level H(t_end) [m]
        terminal_timestamp_seconds: 终端时刻纪元秒 / Terminal epoch timestamp in seconds

    返回 / Returns:
        包含累计露出时长、露出比例、最长连续露出、平均事件时长与事件次数的字典。
        Dictionary containing cumulative exposure duration, fraction, max continuous, mean event and count.
    """
```

---

## 二、 核心科学术语对齐词典 / Core Scientific Terminology Dictionary

为保持跨模块、CLI、GUI、测试套件与学术论文的一致性，全系统严密遵从以下规范对齐词典：

| 中文规范术语 (Mandatory Chinese) | 英文规范术语 (Mandatory English) | 数学/物理定义 (Mathematical / Physical Definition) | 严禁使用之误导性词汇 (PROHIBITED Terms) |
| :--- | :--- | :--- | :--- |
| **固定代表性地形条件下的潜在天文潮露出时长** | **Potential Astronomical Tidal Exposure Duration under a Fixed Representative Terrain** | 固定高程 $z$ 条件下，连续天文潮位 $H(t) \le z$ 的时间积分与连续段统计 | ❌ 沙滩干燥时长 (Beach Drying Time)<br>❌ 干燥时间 (Drying Duration)<br>❌ 真实沙滩水动力退水时间 |
| **潜在天文潮淹没频率** | **Potential Astronomical Tidal Inundation Frequency** | 固定高程 $z$ 下，水面高程高于地形的经验概率：$P(H(t) > z) \times 100\%$ | ❌ 水动力洪水频率<br>❌ 瞬时淹水范围 |
| **等高严格边界归属** | **Strict Elevation Boundary Equality** | 瞬时水面等于地形高程：$H(t) == z$ 严格归属于露出 (Exposed) | ❌ 边界状态不确定<br>❌ 任意归入淹没 |
| **时间跨界线性插值** | **Linear Crossing Interpolation** | 在相邻离散采样步 $[t_k, t_{k+1}]$ 间求解 $H(t^*) = z$ 的精确连续交点时刻 $t^*$ | ❌ 整步长粗暴量化截断<br>❌ 阶梯常数平推 |
| **严格半开采样区间** | **Strict Half-Open Sampling Interval `[start, end)`** | 时间采样序列覆盖起点但不包含终点；全系统统一步长加权 | ❌ 双闭区间导致跨年点双重累加 |
| **自适应四叉树控制网格** | **Adaptive Quadtree Control Grid** | 根据高程梯度、空间曲率与容错阈值自适应局部加密的控制网格 | ❌ 粗糙固定网格<br>❌ 盲目全像元 FES 爆算 |
| **拓扑连通防护** | **Valid-Mask Topology Guard** | 结合物理尺度分辨率阻止跨越陆地或 NoData 屏障的错误空间插值 | ❌ 跨陆地欧氏距离盲插 |

---

## 三、 内存安全与工程不变量 / Memory Safety & Engineering Invariants

1. **避免在内存中分配像元全时空 3D 矩阵 (No Full 3D Array)**：
   - 栅格影像像元数常达 $10^7 \sim 10^8$，年时步常达 $17,568$。创建全量 $P \times T$ 矩阵将直接引发巨大内存消耗。
   - 必须采用**空间二维分块 (Window Blocks, 默认 512x512)** 与**时间维流式批次 (Time Chunks, 默认 1000步)** 双重流式累加。
2. **控制节点数量预算控制**：
   - 必须严格受控于 `max_in_memory_control_nodes` (默认 50,000)，超出时主动拦截。
3. **原子替换保护 (Atomic File Swapping)**：
   - 无论 GeoTIFF 还是 NetCDF Tide Cache，写入阶段必须先输出至 `*.tmp.tif` 或 `*.tmp.nc`，在完整校验无误后通过 `os.replace` 原子替换，杜绝生成半途损坏文件。
