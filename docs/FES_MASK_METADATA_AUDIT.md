# FES2022b 规则格网掩膜文件只读元数据审计报告
# Metadata and Provenance Audit: fes2022b/mask_fes2022B.nc

- **审计时间 / Audit Timestamp**: 2026-09-17
- **审计系统 / System**: CoastTideX v1.6 Beta
- **主要开发者 / Lead Developer**: 王宇浩 (Wang Yuhao)
- **目标文件 / Target File**: `fes2022b/mask_fes2022B.nc`

---

## 1. 物理文件属性与存储大小实测 (Physical File Attributes)

对本地工作区中的 `fes2022b/mask_fes2022B.nc` 进行只读检查，实际属性如下：

- **文件绝对路径**: `I:\Test_tide_model\fes2022b\mask_fes2022B.nc`
- **文件精确字节数**: **1,027,081 字节** (1,027,081 bytes)
- **文件体积 (MB)**: **0.98 MB** (约 0.98 MiB / 1.03 MB)
- **底层存储格式**: NetCDF-4 / HDF5 压缩格式 (deflate / chunked)

> [!CAUTION]
> **历史文档表述纠正 (Documentation Correction)**:
> 历史开发与审计文档中曾有将该掩膜文件体积误记为 `55.6 MB` 或 `700 MB` 的错误记录。
> 经本次底层文件系统与 NetCDF 头结构严格审计确认：
> - `55.6 MB` 为未压缩原始网格在内存中的虚拟浮点字节数近似估算值，**并非实际磁盘文件体积**；
> - `700 MB` 实为 `mdt_cls22` 整个动态地形数据集的总体积，属于误植；
> - 本文件的实际磁盘物理体积严格为 **1,027,081 字节 (约 0.98 MB)**。

---

## 2. NetCDF 维度与变量元数据 (Dimensions & Variable Structure)

通过 `netCDF4.Dataset` 只读解析获取的完整数据结构如下：

### 全局属性 (Global Attributes):
- `Conventions`: `"CF-1.6"`
- `title`: `"FES2022b land-sea and shelf-deep ocean mask"`
- `source`: `"AVISO+ / CNES / LEGOS"`

### 格网维度 (Dimensions):
- `lat`: **5401** (纬度范围: -90.0° 至 +90.0°，采样间隔 $\Delta \varphi = 1/30^\circ \approx 0.0333^\circ$)
- `lon`: **10800** (经度范围: 0.0° 至 360.0°，采样间隔 $\Delta \lambda = 1/30^\circ \approx 0.0333^\circ$)
- **总格网点数**: $5401 \times 10800 = 58,330,800$ (约 5833 万格网节点)

### 变量定义 (Variables):
1. `lat(lat)`: `float64`, units: `degrees_north`
2. `lon(lon)`: `float64`, units: `degrees_east`
3. `mask(lat, lon)`: `float32` / `int8` (Deflate 压缩存储)

### `mask` 取值含义说明 (Mask Value Classifications):
经统计，该文件掩膜变量包含以下取值类别：
- `0.0` (**Open Deep Ocean**): 深海大洋开阔水域，全动力学解；
- `1.0` (**Continental Land**): 大陆与大型岛屿陆地像元；
- `2.0` (**Continental Shelf / Shallow Seas**): 大陆架与近岸浅水陆架区；
- `3.0` (**Inland Lakes / Marginal Extrapolations**): 内陆大型湖泊或近岸外推过渡区。

---

## 3. 为什么 CoastTideX LGP2 原生非结构有限元流程不依赖此文件？

CoastTideX 的核心动力学架构在设计之初就明确确立了**原生非结构有限元网格 (Native LGP2 Unstructured Mesh, 3.77 GB)** 路线。

因此，在标准生产与解算流程中，系统**完全不读取、不加载、亦不依赖** `mask_fes2022B.nc`，原因如下：

1. **几何保真度脱节**：
   `mask_fes2022B.nc` 建立在 $1/30^\circ$ (~3.7 km) 矩形规则格网上。对于千万级像元、空间分辨率为 10m 或 30m 的沿岸高精度 DEM 而言，$1/30^\circ$ 格网具有严重的阶梯效应（Staircase Artifacts），无法精确分辨狭窄潮沟、沙嘴与半岛。
2. **LGP2 有限元网格自闭合性**：
   FES2022b 原生非结构网格由数百万个不规则有限元三角形构成。单元边界本身即严丝合缝贴合真实岸线（在近岸分辨率达数百米），`pyfes` 核心库通过点在三角形内的重心坐标（Barycentric Coordinates）直接判定海洋水动力有效性，遇陆地像元自然输出 NaN，无需借助外部粗网格掩膜判定。
3. **消除外推污染**：
   规则 $1/30^\circ$ 网格中的近岸数值本身已经历了二次重采样与边界外推，若使用其掩膜会人为引入数十厘米的近岸截断误差。

---

## 4. 结论与配置指引 (Audit Conclusions & Guidance)

1. **保留作为外部参考 (External Reference Only)**：
   `fes2022b/mask_fes2022B.nc` 在仓库外存储，仅供用户在做大尺度宏观海洋制图或验证对比时参考，系统核心运行零依赖。
2. **文档同步更新**：
   `README.md`、`README_EN.md` 及依赖表中的文件大小描述统一纠正为 **约 0.98 MB (1,027,081 字节)**。
