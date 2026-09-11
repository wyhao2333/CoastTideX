# CoastTideX 大地水准面与垂直基准说明 (Geoid and Datum Documentation v1.3)

本目录包含 CoastTideX 系统用于高精度潮位垂直基准转换的核心空间栅格数据与大地测量学定义。

---

## 1. 核心数据文件清单

| 文件名 | 数据内容 | 分辨率 / 范围 | 实际大小 | 空间参考 (CRS) | 来源与模型 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `us_nga_egm08_25.tif` | 全球 EGM2008 大地水准面起伏 $N_{\text{EGM2008}}$ | 2.5' 全球网格 (4321×8640) | **76.86 MB** | EPSG:4979 (WGS84 3D) | 美国国家地理空间情报局 (NGA) EGM2008 (d/o 2190) |
| `delta_n_goco06s_minus_egm2008.tif` | 全球大洋 GOCO06s 与 EGM2008 水准面差值 $\Delta N$ | ~5.1' 全球网格 (2118×4236) | **22.66 MB** | EPSG:4326 (WGS84 2D) | ICGEM (GFZ Potsdam), GOCO06s (d/o 300) - EGM2008 (d/o 2190) |
| `delta_n_eigen6c4_minus_egm2008.tif` | 地中海与黑海 EIGEN-6C4 与 EGM2008 水准面差值 $\Delta N$ | ~5.1' 全球网格 (2118×4236) | **22.54 MB** | EPSG:4326 (WGS84 2D) | ICGEM (GFZ Potsdam), EIGEN-6C4 (d/o 2190) - EGM2008 (d/o 2190) |

---

## 2. 大地测量学原理与严密基准转换链条

### 2.1 物理背景与问题
- **瞬时潮位 ($\text{Tide}$)**：由 FES2022b 全球流体潮汐动力学模型给出，基准面为**局部瞬时平均海平面 (MSL)**。
- **平均动态地形 ($\text{MDT}$)**：CNES-CLS22 提供的 MDT 是瞬时平均海面相对于**大地水准面**的海面高度。
- **工程与测绘基准 ($\text{EGM2008}$)**：陆海工程通常以高阶大地水准面 EGM2008 为海拔起算面（正常高/正高体系）。
- **空间几何基准 ($\text{WGS84}$)**：GNSS / 卫星测高测量输出的是相对于参考椭球面的几何椭球高 $h$。

### 2.2 全球开阔大洋级联转换公式 (Global Ocean)
全球大洋区域（除地中海与黑海），CNES-CLS22 的参考水准面为卫星重力场模型 **GOCO06s** ($d/o=300$)。
由于 GOCO06s 仅包含卫星重力信息，而 EGM2008 ($d/o=2190$) 融合了高阶卫星测高与地面重力异常，两者在大地水准面高度上存在系统性差异：
$$\Delta N(\lambda, \varphi) = N_{\text{GOCO06s}}(\lambda, \varphi) - N_{\text{EGM2008}}(\lambda, \varphi)$$

1. **瞬时海面相对于 GOCO06s 大地水准面的高程**：
   $$H_{\text{GOCO06s}}(t, \lambda, \varphi) = \text{Tide}(t, \lambda, \varphi) + \text{MDT}_{\text{CLS22}}(\lambda, \varphi)$$

2. **严密改正至 EGM2008 大地水准面高程 (海拔正高)**：
   $$H_{\text{EGM2008}}(t, \lambda, \varphi) = H_{\text{GOCO06s}}(t, \lambda, \varphi) + \Delta N(\lambda, \varphi) = \text{Tide} + \text{MDT} + (N_{\text{GOCO06s}} - N_{\text{EGM2008}})$$

3. **严密换算至 WGS84 几何空间椭球高**：
   $$h_{\text{WGS84}}(t, \lambda, \varphi) = H_{\text{EGM2008}}(t, \lambda, \varphi) + N_{\text{EGM2008}}(\lambda, \varphi)$$

### 2.3 复合型混合 MDT (Hybrid MDT) 区域基准特性：地中海与黑海
CNES-CLS22 官方产品（`mdt_hybrid_cnes_cls22_cmems2020_global.nc`）是全球海洋与区域模型的融合成果：
- **全球开阔大洋 (Global Ocean)**：参考大地水准面为 **GOCO06s**；
- **地中海 (Mediterranean Sea)**：融合 CMEMS2020-MED，其参考大地水准面为 **EIGEN-6C4** ($d/o=2190$)；
- **黑海 (Black Sea)**：融合 CMEMS2020-BLK，其参考大地水准面为 **EIGEN-6C4** ($d/o=2190$)。

**科学处理与精度保障 (CoastTideX v1.3 创新机制)**：
1. **精细闭合多边形识别**：系统采用 `matplotlib.path.Path` 矢量闭合多边形，精确识别直布罗陀海峡以东的地中海海域与黑海，彻底消除矩形包围框对大西洋加的斯湾、直布罗陀西侧、比斯开湾及红海的误判；
2. **严格真值输出与拒绝伪造**：
   - 在地中海与黑海，标称主变量 `h_mdt_ref_m` 代表相对 EIGEN-6C4 的海面高；
   - `h_goco06s_m` 严格赋值为 `NaN`（坚决不冒充 GOCO06s）；
   - 正高转换通过专属 `delta_n_eigen6c4_minus_egm2008.tif` 进行差值改正：
     $$H_{\text{EGM2008}} = \text{Tide} + \text{MDT} + (N_{\text{EIGEN-6C4}} - N_{\text{EGM2008}})$$
   - 质量控制标识输出为 `QC_MED_BLACK_SEA_EIGEN6C4`，确保科研可溯源。

---

## 3. 数据来源与可复现性 (Provenance & Reproducibility)

### 3.1 ICGEM 数据源
- **服务机构**：International Centre for Global Earth Models (ICGEM), GFZ German Research Centre for Geosciences, Potsdam.
- **参考椭球**：GRS80 / WGS84 正常椭球 ($a = 6378137.0\text{ m}, 1/f = 298.257223563$).
- **潮汐系统**：**Tide-free** (无潮系统，消除永久潮汐形变，与 CNES-CLS22、EGM2008 官方基准规范保持绝对一致).

### 3.2 栅格复现生成
用户可运行项目自带通用脚本 `scripts/generate_delta_n.py` 重新生成差值栅格：

1. **生成 GOCO06s 差值栅格**：
   ```bash
   python scripts/generate_delta_n.py \
       --ref-name "GOCO06s" \
       --ref-tif <path_to_goco06s.tiff> \
       --target-name "EGM2008" \
       --target-tif <path_to_egm2008.tiff> \
       --out data/geoid/delta_n_goco06s_minus_egm2008.tif
   ```

2. **生成 EIGEN-6C4 差值栅格**：
   ```bash
   python scripts/generate_delta_n.py \
       --ref-name "EIGEN-6C4" \
       --ref-tif <path_to_eigen6c4.tiff> \
       --target-name "EGM2008" \
       --target-tif <path_to_egm2008.tiff> \
       --out data/geoid/delta_n_eigen6c4_minus_egm2008.tif
   ```

脚本内含自动空间配准检查（CRS、Transform、Bounds、Resolution）与双线性重采样（Bilinear Resampling）机制，生成 GeoTIFF 采用 Deflate 算法压缩，确保体积低于 100MB 限制可受 Git 直接管理。

