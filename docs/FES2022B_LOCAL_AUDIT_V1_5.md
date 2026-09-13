# CoastTideX v1.5 FES2022b 本地数据包只读审查报告
# FES2022b Local Dataset Audit & Mask Semantics Assessment

- **审查时间**：2026-09-14
- **审查目标**：`I:\Test_tide_model\fes2022b` 完整本地模型数据
- **访问模式**：严格只读 (READ-ONLY)
- **审查工具**：Python 3.11 (`netCDF4`, `numpy`) & `pyfes 2026.5.2`

---

## 1. 目录结构与数据资产概览

`I:\Test_tide_model\fes2022b` 根目录下包含 5 项核心资产，总数据量约 **20.56 GB**：

| 资产名称 | 类型 | 文件数量 | 磁盘总大小 | 存储格式 | 核心内容与说明 |
| :--- | :---: | :---: | :---: | :---: | :--- |
| `ocean_tide_non_structured` | 目录 | 1 | 3.682 GB | NetCDF-4 (非压缩) | **原生非结构有限元网格 (LGP2)**，CoastTideX 当前核心潮汐解算模型 |
| `mask_fes2022B.nc` | 文件 | 1 | 0.98 MB | NetCDF-4 (非压缩) | 官方 1/30° 全球规则网格掩膜文件 |
| `ocean_tide_extrapolated` | 目录 | 35 | 5.264 GB | NetCDF-4 XZ 压缩 (`.nc.xz`) | 官方 1/30° 沿岸外推规则网格分潮库 (含 34 分潮与附属文件) |
| `ocean_tide_20241025` | 目录 | 34 | 4.944 GB | NetCDF-4 XZ 压缩 (`.nc.xz`) | 官方 1/30° 非外推规则网格分潮库 (34 分潮) |
| `load_tide` | 目录 | 34 | 6.673 GB | NetCDF-4 XZ 压缩 (`.nc.xz`) | 官方 1/30° 负荷潮规则网格分潮库 (34 分潮) |

---

## 2. 原生非结构网格模型 (Native FES2022b LGP2 Mesh)

CoastTideX 当前生产引擎运行于该原生网格上：

- **文件路径**：`I:\Test_tide_model\fes2022b\ocean_tide_non_structured\FES2022b_OceanTide_NSgrid.nc`
- **文件大小**：3,953,520,380 字节 (~3.682 GB)
- **全局标题**：`FES2022 ocean tide elevations - Non-Structured grids`
- **模型版本**：FES2022b (`product_version = "b"`)
- **网格拓扑维度**：
  - `coordinates`（节点数）：**5,691,517**
  - `triangles`（三角形单元数）：**11,056,490**
  - `lgp2_nodes`（LGP2 高阶节点数）：**22,446,034**
  - `three`：3，`six`：6
- **分潮覆盖**：**34 个完整天文分潮**（均包含 `*_amplitude` 与 `*_phase`）：
  `2N2`, `Eps2`, `J1`, `K1`, `K2`, `L2`, `Lambda2`, `M2`, `M3`, `M4`, `M6`, `M8`, `MKS2`, `MN4`, `MS4`, `MSf`, `Mf`, `Mm`, `Msqm`, `Mtm`, `Mu2`, `N2`, `N4`, `Nu2`, `O1`, `P1`, `Q1`, `R2`, `S1`, `S2`, `S4`, `Sa`, `Ssa`, `T2`。
- **坐标定义**：`lon`, `lat` 经纬度浮点数组，非结构有限元空间拓扑。

---

## 3. 官方掩膜文件深度审查 (`mask_fes2022B.nc`)

- **文件路径**：`I:\Test_tide_model\fes2022b\mask_fes2022B.nc`
- **文件大小**：1,029,918 字节 (~0.98 MB)
- **NetCDF 标题**：`FES2022 mask for extrapolated tide elavations`
- **摘要说明**：`Mask to differentiate native data and extrapolated data for FES2022 tide elevation grids`
- **网格维度**：
  - `lat`: 5,401 (纬度范围: [-90.0°, +90.0°], 空间步长: 0.033333333° = 1/30° = 2 角分)
  - `lon`: 10,800 (经度范围: [0.0°, 359.96667°], 空间步长: 0.033333333° = 1/30° = 2 角分)
- **经度规约**：`[0, 360)` 体系。
- **掩膜变量属性**：
  - 变量名：`mask`
  - 数据类型：`float32`
  - 形状：`(5401, 10800)`
  - 属性 `long_name`: **`Mask : 0=Ocean native data, 1=Extrapolated data, 2=Land, 3=Lake`**
  - 实测唯一值集合：`[0.0, 1.0, 2.0, 3.0]`

### 掩膜各编码值科学语义：
1. **`0` = Ocean native data**：属于 FES2022 原生水动力模型直接解算的开阔大洋/近岸海域像元；
2. **`1` = Extrapolated data**：由 AVISO/LEGOS 官方在 1/30° 规则网格产品中采用特定距离向岸外推填充的沿岸潮位像元；
3. **`2` = Land**：陆地像元，无潮位解；
4. **`3` = Lake**：内陆湖泊像元，无海洋潮位解。

---

## 4. 规则网格产品审查 (`ocean_tide_extrapolated` 等)

- **文件集合**：
  - `ocean_tide_extrapolated`: 35 个文件（34 分潮 + 描述）
  - `ocean_tide_20241025`: 34 个文件
  - `load_tide`: 34 个文件
- **关键技术事实**：
  - 所有这三个目录下的 NetCDF 规则网格文件均采用了 **`.nc.xz` (XZ/LZMA 压缩)** 封装；
  - 直接调用 C 语言 NetCDF4 库打开报错：`OSError: [Errno -51] NetCDF: Unknown file format`；
  - Python `pyfes` 库及其底层 C 扩展无法直接对 `.nc.xz` 压缩包建立内存网格索引；
  - 若要使用，必须在外部进行解压缩得到几十 GB 的原始 `.nc` 文件。

---

## 5. 科学评估与 Fallback 决策 (Mask & Fallback Decision)

### 核心结论：**Coastal Extrapolated Fallback 在 v1.5 Phase 1 中暂不集成 (NOT INTEGRATED / DISABLED)**

#### 决策依据：
1. **数据物理封装限制**：
   `ocean_tide_extrapolated` 目录下的全部 35 个分潮文件均是 `.nc.xz` 归档。根据本项目的铁律原则（0.1 条），`fes2022b` 目录是严格只读的，禁止原地解压、修改或重命名。在未经解压前，`pyfes` 无法直接读取。
2. **网格体制不一致性**：
   - CoastTideX 核心使用的是基于三角形有限元单元的 **非结构高阶网格 (Native LGP2 Mesh)**；
   - `mask_fes2022B.nc` 是专门为 **1/30° 笛卡尔规则网格 (`ocean_tide_extrapolated`)** 设计的判别掩膜，并非非结构网格的节点属性；
   - 如果将 1/30° 笛卡尔掩膜强行套用到非结构有限元三角形单元上，存在空间基准与空间采样混叠偏差。
3. **GUI 规范落实**：
   在 v1.5 GUI 中，对于“允许官方沿岸外推 FES 回退 (Allow official coastal extrapolated FES fallback)”选项，必须设置为 **禁用 (Disabled / Grayed-out)**，并附带明确 Tooltip 说明：“本地外推网格为 .nc.xz 压缩包且掩膜为规则网格，Phase 1 维持原生 LGP2 高精度非结构网格优先解算，外推回退机制暂未激活”。

---

## 6. PyFES 与运行环境现状

- **已安装 pyfes 版本**：`2026.5.2` (位于 `I:\Test_tide_model\.venv\Lib\site-packages\pyfes`)
- **配置文件对应**：
  - `config.yaml` 中 `paths.fes_ns_grid` 指向 `fes2022b/ocean_tide_non_structured/FES2022b_OceanTide_NSgrid.nc`。
  - 核心引擎 `core/tide_engine.py` 调用 `cfg.LGP(..., type='lgp2', codes='lgp2')` 正常稳定运行，已通过全部 50 项单元测试。