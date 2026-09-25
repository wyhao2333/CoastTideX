# CoastTideX v1.7 — MSL Reference Workflow 架构设计与用户指南

---

## 1. 科学背景与理论依据 (Scientific Foundations)

在传统的海岸带淹没分析、水动力数值模拟与风暴潮致灾评估中，研究人员通常尝试将瞬时动力海面高程（Dynamic Ocean Surface / Astronomical Tide）转换至固定的陆地重力水准面基准（如 EGM2008 或国家高程基准）：

$$\text{Tide}_{\text{EGM2008}}(t) = \text{Tide}_{\text{MSL}}(t) + \text{MDT} + \Delta N$$

根据 **Seeger & Minderhoud (Nature, 2026)** *"Sea level much higher than assumed in most coastal hazard assessments"* 的研究发现，全球绝大多数海岸带灾害评估由于未能正确统一海平面与陆地高程基准，系统性低估了沿岸实际海平面高程。该研究提出利用平均动态地形 (MDT) 作为连接重力大地水准面与局部平均海平面的物理基准，并通过空间外推建立向陆延伸的海平面基准面。

结合 CoastTideX 在高分辨率潮间带遥感地形处理中的工程与科学需求：
1. **统一物理几何基准**：借鉴 Seeger & Minderhoud (2026) 提出的基准统一思想，将陆面 DEM 前置转换为局部平均海平面基准，彻底消除水动力模型与静态地形比较时的基准错位；
2. **计算解耦与消除冗余**：前置基准转换使得在后续自适应四叉树控制网格解算中，控制节点无需反复查询 MDT 与大地水准面，实现纯动力学潮位的零冗余快速评估。

### v1.7 核心范式跃迁 (Paradigm Shift)
**CoastTideX v1.7** 正式实现基准统一框架跃迁：**将陆地 DEM 前置转换为局部平均海平面 (Local Mean Sea Level, MSL) 基准**（Adapted from Seeger & Minderhoud, 2026）：

$$Z_{\text{MSL}} = Z_{\text{EGM2008}} - \text{MDT} - \Delta N$$

转换完成后，FES2022b 预测的原生纯天文动力学潮位 $\text{Tide}_{\text{MSL}}(t)$ 与 $\text{DEM}_{\text{MSL}}$ 直接在同一局部平均海平面几何物理基准下进行无缝比较：

$$\text{Tide}_{\text{MSL}}(t) > \text{DEM}_{\text{MSL}}$$

---

## 2. 核心数学模型与外推算法 (Mathematical Formulation & Extrapolation)

### 2.1 高程前置代数关系
对于空间任意位置 $(\lambda, \varphi)$ 处的陆地 DEM 像元高程 $Z_{\text{EGM2008}}$：
- **MDT**：CNES-CLS22 全球平均动态地形高（米）；
- **$\Delta N$**：GOCO06s/EIGEN-6C4 与 EGM2008 之大地水准面高差改正（米）；
- **$Z_{\text{MSL}}$**：统一到局部平均海平面的陆地地形高程（米）。

代数转换满足绝对可逆恒等关系：
$$(\text{Tide}_{\text{MSL}}(t) + \text{MDT} + \Delta N > Z_{\text{EGM2008}}) \iff (\text{Tide}_{\text{MSL}}(t) > Z_{\text{MSL}})$$

### 2.2 两阶段 MDT 空间重构与 100 km 门禁机制
1. **阶段 1 (Native Ocean MDT)**：
   - 对于开阔大洋与有效海域，采用 CNES-CLS22 原生网格高精度双线性插值（Bilinear Interpolation）；
   - 质量控制编码标记为：`QC = 0 (native_mdt)`。
2. **阶段 2 (Coastal & Land Extrapolation)**：
   - 对于潮滩内陆与岸线缺失区，将经纬度 $(\lambda, \varphi)$ 投影至以地球平均曲率半径 $R=6,371,000\text{ m}$ 的球面三维直角空间坐标系 $(X, Y, Z)$：
     $$X = R \cos\varphi \cos\lambda, \quad Y = R \cos\varphi \sin\lambda, \quad Z = R \sin\varphi$$
   - 基于 `scipy.spatial.cKDTree` 构建大洋有效边界点的高维空间索引，进行反距离加权（IDW，幂次 $p=2.0$，近邻点数 $k=8$）空间外推；
   - 质量控制编码标记为：`QC = 1 (idw_extrapolated)`。
3. **物理距离硬截断门禁 (100 km Hard Cutoff Guard)**：
   - 常量设定：`MAX_MDT_EXTRAPOLATION_DISTANCE_KM = 100.0 km`；
   - **特别说明**：Seeger & Minderhoud (2026) 原研究针对全球宏观尺度采用了 500 km 沿岸范围；CoastTideX 针对高分辨率沿海潮滩与滨海湿地生态模拟，引入了更为保守的 **100 km** 空间门禁上限；
   - 凡至最近有效大洋网格点的测地空间距离 $> 100\text{ km}$ 的深陆区，系统严密阻断外推，强制赋值 `NoData`（`NaN`）；
   - 质量控制编码标记为：`QC = 2 (nodata_or_exceeded_100km)`，坚决杜绝内陆无限外推造成的失真。

---

## 3. 架构优势与性能收益 (Architectural Advantages & Performance)

| 评估维度 | v1.6 旧架构 (Tide to EGM2008) | v1.7 新架构 (DEM to MSL) | 科学与工程收益 |
| :--- | :--- | :--- | :--- |
| **比较物理基准** | EGM2008 大地水准面 | 局部平均海平面 (MSL) | 消除近岸水准面阶梯畸变 |
| **Stage 1 控制节点 MDT 查询** | 逐节点频繁查询 (78+ 次) | **零查询 (0 次)** | Stage 1 纯潮位解算零额外依赖 |
| **MDT 沿岸外推边界** | 无明确物理距离截断 | **严格 100 km 球面空间门禁** | 消除深陆区无限外推风险 |
| **决策边界一致性** | 基准一致 | **100.0000% 严密等价** | 240,000 次判定残差 $< 10^{-7}\text{ m}$ |
| **批处理流式吞吐** | 重复解算基准偏移量 | 一次转换 DEM，后续零开销复用 | 显著提升多方案/多时段分析效率 |

---

## 4. 命令行 CLI 使用完全指南 (CLI Usage Guide)

### 4.1 DEM 垂直基准前置转换 (`convert-dem`)
```bash
python cli.py convert-dem \
    --input F:/data/coastal_dem_egm2008.tif \
    --output F:/data/coastal_dem_msl.tif \
    --qc-output F:/data/coastal_dem_msl_qc.tif \
    --max-dist-km 100.0 \
    --block-size 1024 \
    --overwrite
```

**参数说明**：
- `--input`, `-i`: 待转换的原始 DEM GeoTIFF（必须为 EGM2008 基准）；
- `--output`, `-o`: 输出 DEM_MSL GeoTIFF 路径（默认在输入文件名后附加 `_msl.tif`）；
- `--qc-output`: 输出转换质量控制掩膜 GeoTIFF 路径（默认附加 `_conversion_qc.tif`）；
- `--max-dist-km`: MDT 空间外推允许的最大物理距离（公里，默认 100.0）；
- `--block-size`: 2D 空间流式分块边长（默认 1024 像元，内存占用平稳）；
- `--overwrite`: 覆盖已存在同名输出。

### 4.2 基于 MSL DEM 运行淹没频率解算 (`raster inundation`)
```bash
python cli.py raster inundation \
    --dem F:/data/coastal_dem_msl.tif \
    --output F:/data/inundation_freq.tif \
    --year 2024 \
    --step 30min \
    --dem-datum msl
```

### 4.3 潜在露出时间域 7 大产品解算 (`raster exposure`)
```bash
python cli.py raster exposure \
    --dem F:/data/coastal_dem_msl.tif \
    --output-dir F:/data/exposure_products/ \
    --year 2024 \
    --step 30min \
    --dem-datum msl
```

---

## 5. Python API 编程接口 (Python API)

### 5.1 空间栅格整景流式转换
```python
from core.dem_datum_converter import convert_dem_to_msl

summary = convert_dem_to_msl(
    input_dem_path="path/to/dem_egm2008.tif",
    output_msl_path="path/to/dem_msl.tif",
    output_qc_path="path/to/dem_msl_qc.tif",
    max_extrapolation_distance_km=100.0,
    block_size=1024,
    allow_overwrite=True,
    progress_callback=lambda p, m: print(f"[{p}%] {m}")
)

print(f"转换完成: 有效像元 {summary.valid_dem_pixels:,} / {summary.total_pixels:,}")
print(f"原生插值像元: {summary.native_mdt_pixels:,}, IDW 外推像元: {summary.extrapolated_mdt_pixels:,}")
```

### 5.2 点位级向量化转换
```python
import numpy as np
from core.dem_datum_converter import DEMDatumConverter

converter = DEMDatumConverter(max_extrapolation_distance_km=100.0)

lons = np.array([121.5, 122.0, 117.0])
lats = np.array([31.5, 31.0, 31.0])
z_egm = np.array([3.5, 0.0, 50.0])

z_msl, mdt_vals, delta_n_vals, qc_vals = converter.convert_points(lons, lats, z_egm)

for i in range(len(lons)):
    print(f"点 {i}: Lon={lons[i]}, Lat={lats[i]}, Z_EGM={z_egm[i]}m -> Z_MSL={z_msl[i]:.3f}m, QC={qc_vals[i]}")
```

---

## 6. 质量控制掩膜与元数据规范 (QC Specification & Metadata Tags)

### 6.1 转换 QC 掩膜编码 (`*_conversion_qc.tif`)
| QC 数值 | 宏定义常量 | 几何与物理涵义 | 处理机制 |
| :---: | :--- | :--- | :--- |
| **0** | `QC_MDT_NATIVE` | 原始 CNES-CLS22 大洋开阔海域覆盖点 | 双线性插值，高精度保证 |
| **1** | `QC_MDT_EXTRAPOLATED` | 近岸滩涂与陆地缺失点（距离有效海域 $\le 100\text{ km}$） | 球面 3D 空间 IDW 外推 |
| **2** | `QC_MDT_NODATA` | 输入 DEM 原生 NoData 或距离大洋有效海域 $> 100\text{ km}$ | 严密物理阻断，赋 NoData |

### 6.2 输出 GeoTIFF 元数据溯源标签 (Provenance Tags)
转换生成的 `*_msl.tif` 自动注入完备的科学溯源元数据：
- `SOFTWARE`: `CoastTideX v1.7`
- `CONVERTER`: `DEMDatumConverter`
- `DATUM`: `MSL`
- `ANALYSIS_REFERENCE`: `MSL`
- `SOURCE_VERTICAL_DATUM`: `EGM2008`
- `TARGET_VERTICAL_DATUM`: `MSL`
- `MDT_MODEL`: `CNES-CLS22`
- `MDT_METHOD`: `bilinear_native_plus_idw_extrapolated`
- `MAX_EXTRAPOLATION_DISTANCE_KM`: `100.0`
- `EQUATION`: `Z_MSL = Z_EGM2008 - MDT - DeltaN`
- `SCIENTIFIC_CITATION`: `Adapted from Seeger & Minderhoud (Nature, 2026)`
