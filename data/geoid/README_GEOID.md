# CoastTideX 大地水准面与垂直基准说明 (Geoid and Datum Documentation)

本目录包含 CoastTideX 系统用于高精度潮位垂直基准转换的核心空间栅格数据。

---

## 1. 核心数据文件清单

| 文件名 | 数据内容 | 分辨率 / 范围 | 大小 | 来源与模型 |
| :--- | :--- | :--- | :--- | :--- |
| `us_nga_egm08_25.tif` | 全球 EGM2008 大地水准面起伏 $N_{\text{EGM2008}}$ | 2.5' 全球网格 (4321×8640) | ~72.5 MB | 美国国家地理空间情报局 (NGA) EGM2008 (d/o 2190) |
| `delta_n_goco06s_minus_egm2008.tif` | GOCO06s 与 EGM2008 大地水准面差异 $\Delta N$ | 5.0' 全球网格 (2118×4236) | ~11.8 MB | ICGEM (GFZ Potsdam), GOCO06s (d/o 300) - EGM2008 (d/o 2190) |

---

## 2. 大地测量学原理与严密基准转换链条

### 2.1 物理背景与问题
- **瞬时潮位 ($\text{Tide}$)**：由 FES2022b 全球流体潮汐动力学模型给出，基准面为**局部瞬时平均海平面 (MSL)**。
- **平均动态地形 ($\text{MDT}$)**：CNES-CLS22 提供的 MDT 是相对于**卫星重力场模型 GOCO06s 大地水准面**的海面高度。
- **工程与测绘基准 ($\text{EGM2008}$)**：陆海工程通常以高阶大地水准面 EGM2008 为海拔起算面（正常高/正高体系）。
- **空间几何基准 ($\text{WGS84}$)**：GNSS / 卫星测高测量输出的是相对于参考椭球面的几何椭球高 $h$。

由于 GOCO06s (d/o 300) 仅包含卫星重力信息，而 EGM2008 (d/o 2190) 融合了高阶卫星测高与地面重力异常，两者在大地水准面高度上存在系统性差异：
$$\Delta N(\lambda, \varphi) = N_{\text{GOCO06s}}(\lambda, \varphi) - N_{\text{EGM2008}}(\lambda, \varphi)$$

### 2.2 严密级联转换公式

1. **瞬时海面相对于 GOCO06s 大地水准面的高程**：
   $$H_{\text{GOCO06s}}(t, \lambda, \varphi) = \text{Tide}(t, \lambda, \varphi) + \text{MDT}_{\text{CLS22}}(\lambda, \varphi)$$

2. **严密改正至 EGM2008 大地水准面高程 (海拔正高)**：
   $$H_{\text{EGM2008}}(t, \lambda, \varphi) = H_{\text{GOCO06s}}(t, \lambda, \varphi) + \Delta N(\lambda, \varphi) = \text{Tide} + \text{MDT} + (N_{\text{GOCO06s}} - N_{\text{EGM2008}})$$

3. **严密换算至 WGS84 几何空间椭球高**：
   $$h_{\text{WGS84}}(t, \lambda, \varphi) = H_{\text{EGM2008}}(t, \lambda, \varphi) + N_{\text{EGM2008}}(\lambda, \varphi)$$

以上数学闭合公式已在 CoastTideX 单元测试中实现严格闭合验证。

---

## 3. 数据来源与可复现性 (Provenance & Reproducibility)

### 3.1 ICGEM 数据源
- **服务机构**：International Centre for Global Earth Models (ICGEM), GFZ German Research Centre for Geosciences, Potsdam.
- **参考椭球**：GRS80 / WGS84 正常椭球 ($a = 6378137.0\text{ m}, 1/f = 298.257223563$).
- **潮汐系统**：**Tide-free** (无潮系统，消除永久潮汐形变，与 CNES-CLS22、EGM2008 官方基准规范保持绝对一致).

### 3.2 栅格复现生成
用户可运行项目自带脚本自行从 ICGEM 原始网格重新生成 `delta_n_goco06s_minus_egm2008.tif`：
```bash
python scripts/generate_delta_n.py --goco <path_to_goco06s.tiff> --egm <path_to_egm2008.tiff> --out data/geoid/delta_n_goco06s_minus_egm2008.tif
```
内部自动执行浮点差值计算、边界对齐、NaN 掩膜并采用 LZW/Deflate 压缩，保证生成文件在 100MB 限制内可直接受 Git 追踪。
