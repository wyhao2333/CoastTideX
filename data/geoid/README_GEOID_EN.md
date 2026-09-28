# CoastTideX Geoid and Vertical Datum Documentation (v1.6 Beta)

This directory contains the essential spatial rasters and geodetic definitions used by CoastTideX for high-precision vertical datum transformations across coastal and marine domains.

---

## 1. Core Data Manifest

| File Name | Data Content | Resolution / Domain | Storage Type | Spatial Reference (CRS) | Source & Model |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `us_nga_egm08_25.tif` | Global EGM2008 geoid undulation $N_{\text{EGM2008}}$ | 2.5' global grid (4321×8640) | **Bundled in Git** (76.86 MB) | EPSG:4979 (WGS84 3D) | NGA EGM2008 (d/o 2190) |
| `delta_n_goco06s_minus_egm2008.tif` | Global ocean GOCO06s minus EGM2008 geoid difference $\Delta N$ | ~5.1' global grid (2118×4236) | **Bundled in Git** (22.66 MB) | EPSG:4326 (WGS84 2D) | ICGEM (GFZ Potsdam), GOCO06s (d/o 300) - EGM2008 (d/o 2190) |
| `delta_n_eigen6c4_minus_egm2008.tif` | Mediterranean & Black Sea EIGEN-6C4 minus EGM2008 difference $\Delta N$ | ~5.1' global grid (2118×4236) | **Preprocessed Reproduction** (Ignored in Git) | EPSG:4326 (WGS84 2D) | ICGEM EIGEN-6C4 (d/o 2190) - EGM2008; reproducible via `scripts/generate_delta_n.py` |
| `hybrid_mdt_source_mask.tif` | Hybrid MDT source classification mask (1=GOCO06s, 2=Med EIGEN-6C4, 3=Black Sea EIGEN-6C4, 0=Unknown, 255=NoData) | User-defined | **External Optional** (Not bundled) | EPSG:4326 or projected CRS | Used for authoritative MDT reference classification; system falls back to geographic polygon ray-casting and flags `QC_DATUM_SOURCE_APPROX` when missing. |
| `fes2022b/mask_fes2022B.nc` | FES2022b 1/30° regular grid tide source/extrapolation mask (0=Ocean native data, 1=Extrapolated data, 2=Land, 3=Lake) | 1/30° (5401×10800) | **External Optional Reference** (~0.98 MB on disk, 1,027,081 bytes zlib; 55.6 MB uncompressed memory array) | EPSG:4326 | Indicates extrapolation origin in FES regular grid; not used in native LGP2 finite element processing or MDT classification. |

---

## 2. Geodetic Principles & Cascaded Datum Chain

### 2.1 Physical Formulation
- **Instantaneous Tide ($\text{Tide}$)**: Pure astronomical tide elevation relative to local Mean Sea Level (MSL) predicted by FES2022b.
- **Mean Dynamic Topography ($\text{MDT}$)**: Sea surface height above the geoid provided by CNES-CLS22 MDT.
- **Engineering / Orthometric Height ($\text{EGM2008}$)**: Orthometric height $H$ referenced to the global gravitational equipotential surface (EGM2008).
- **Geometric Ellipsoidal Height ($\text{WGS84}$)**: Pure geometric 3D distance $h$ normal to the reference ellipsoid.

### 2.2 Global Ocean Cascaded Conversion Formulas
In the global ocean (excluding the Mediterranean and Black Seas), CNES-CLS22 references the satellite gravity geoid **GOCO06s** ($d/o=300$). The systematic difference with EGM2008 ($d/o=2190$) is:
$$\Delta N(\lambda, \varphi) = N_{\text{GOCO06s}}(\lambda, \varphi) - N_{\text{EGM2008}}(\lambda, \varphi)$$

1. **Height relative to GOCO06s Geoid**:
   $$H_{\text{GOCO06s}}(t, \lambda, \varphi) = \text{Tide}(t, \lambda, \varphi) + \text{MDT}_{\text{CLS22}}(\lambda, \varphi)$$

2. **Orthometric Height relative to EGM2008 Geoid**:
   $$H_{\text{EGM2008}}(t, \lambda, \varphi) = H_{\text{GOCO06s}}(t, \lambda, \varphi) + \Delta N(\lambda, \varphi) = \text{Tide} + \text{MDT} + (N_{\text{GOCO06s}} - N_{\text{EGM2008}})$$

3. **Geometric Ellipsoidal Height relative to WGS84**:
   $$h_{\text{WGS84}}(t, \lambda, \varphi) = H_{\text{EGM2008}}(t, \lambda, \varphi) + N_{\text{EGM2008}}(\lambda, \varphi)$$

---

## 3. Data Provenance & Reproducibility

User can regenerate the difference rasters using the bundled script:
```bash
python scripts/generate_delta_n.py \
    --ref-name "GOCO06s" \
    --ref-tif <path_to_goco06s.tiff> \
    --target-name "EGM2008" \
    --target-tif <path_to_egm2008.tiff> \
    --out data/geoid/delta_n_goco06s_minus_egm2008.tif
```
