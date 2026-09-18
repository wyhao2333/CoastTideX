# CoastTideX: High-Precision Coastal Spatial Raster Tide Simulation and Multi-Datum Transformation System

<p align="center">
  <a href="https://github.com/wyhao2333/CoastTideX/actions"><img src="https://github.com/wyhao2333/CoastTideX/actions/workflows/ci.yml/badge.svg" alt="GitHub Actions CI"></a>
  <img src="https://img.shields.io/badge/Release-v1.6--beta-0284c7.svg" alt="Release v1.6-beta">
  <img src="https://img.shields.io/badge/Python-3.11-blue.svg" alt="Python 3.11">
  <img src="https://img.shields.io/badge/GUI-PyQt6-green.svg" alt="PyQt6">
  <img src="https://img.shields.io/badge/Tide%20Model-FES2022b%20LGP2-0284c7.svg" alt="FES2022b LGP2">
  <img src="https://img.shields.io/badge/MDT-CNES--CLS22-8b5cf6.svg" alt="CNES-CLS22 MDT">
  <img src="https://img.shields.io/badge/Datum-MSL%20%7C%20EGM2008%20%7C%20WGS84-f59e0b.svg" alt="Vertical Datum">
  <img src="https://img.shields.io/badge/License-MIT-emerald.svg" alt="MIT License">
</p>

<p align="center">
  <a href="README.md">中文版 (Chinese)</a> | <b>[ English Version ]</b>
</p>

---

## 1. Project Overview & Scientific Mission

**CoastTideX** is an advanced open-source scientific software system designed for **coastal remote sensing, marine geodesy, intertidal morphodynamics, and hydrodynamic baseline unification**.

Powered by the French CNES/AVISO **FES2022b global ocean tide hydrodynamic model (utilizing native LGP2 2nd-order discontinuous/continuous polynomial unstructured finite-element mesh with all 34 constituents)**, CoastTideX significantly mitigates the nearshore staircase distortions and land contamination errors intrinsic to conventional regular latitude-longitude grids. Furthermore, it embeds **CNES-CLS22 Mean Dynamic Topography (MDT)** and **NGA EGM2008 2.5' ultra-high-resolution global geoid undulation**, providing a seamless, mathematically rigorous transformation pipeline between local Mean Sea Level (MSL), orthometric geoid height (EGM2008), and 3D geometric ellipsoidal height (WGS84).

In **CoastTideX v1.6**, the system advances into the **time domain**, introducing the **Potential Astronomical Tidal Exposure Duration Engine for Tidal Flats and Beaches**, **strictly unified half-open interval `[start, end)` temporal slicing semantics**, and **Tide Cache Schema 1.2 (with terminal water level sampling)**.

> [!NOTE]
> Current project status: **CoastTideX v1.6 Beta / Feature Branch**. It is supported by comprehensive automated unit test suites and is suitable for controlled research and evaluation.

---

## 2. Core Scientific Foundations & Four Vertical Datums

In coastal geodesy and physical oceanography, vertical datums represent distinct physical and geometric reference surfaces:

```text
               h_WGS84 (Geometric 3D ellipsoidal height, GNSS)
                    ▲
                    │  + N_EGM2008 (Geoid undulation)
                    ▼
               H_EGM2008 (Orthometric height, terrestrial mapping benchmark)
                    ▲
                    │  + ΔN (GOCO06s/EIGEN-6C4 minus EGM2008 geoid difference)
                    ▼
               H_MDT_REF (Sea surface height relative to MDT reference geoid)
                    ▲
                    │  + MDT (Mean Dynamic Topography)
                    ▼
               Tide_MSL (Instantaneous astronomical tide relative to MSL)
```

### Cascaded Geodetic Conversion Equations:
1. **Instantaneous Tide relative to local Mean Sea Level (MSL)**:
   $$\text{Tide}(t, \lambda, \varphi) = \sum_{k=1}^{34} f_k(t) A_k(\lambda, \varphi) \cos\left( \omega_k t + v_k(t) + u_k(t) - G_k(\lambda, \varphi) \right)$$
2. **Sea Surface Height relative to MDT Reference Geoid (Global: GOCO06s; Med/Black Sea: EIGEN-6C4)**:
   $$H_{\text{MDT\_REF}}(t, \lambda, \varphi) = \text{Tide}(t, \lambda, \varphi) + \text{MDT}_{\text{CLS22}}(\lambda, \varphi)$$
3. **Rigorous Transformation to EGM2008 Orthometric Height (Elevation)**:
   $$H_{\text{EGM2008}}(t, \lambda, \varphi) = H_{\text{MDT\_REF}}(t, \lambda, \varphi) + \Delta N(\lambda, \varphi)$$
   - Global Ocean: $\Delta N(\lambda, \varphi) = N_{\text{GOCO06s}}(\lambda, \varphi) - N_{\text{EGM2008}}(\lambda, \varphi)$
   - Mediterranean & Black Sea: $\Delta N(\lambda, \varphi) = N_{\text{EIGEN-6C4}}(\lambda, \varphi) - N_{\text{EGM2008}}(\lambda, \varphi)$
4. **Transformation to WGS84 Geometric 3D Ellipsoidal Height**:
   $$h_{\text{WGS84}}(t, \lambda, \varphi) = H_{\text{EGM2008}}(t, \lambda, \varphi) + N_{\text{EGM2008}}(\lambda, \varphi)$$

---

## 3. Why Choose FES2022b Native LGP2 Unstructured Mesh

FES2022b is released in two formats:
- 1/30° regular grid;
- Native unstructured triangular mesh (LGP2 finite element).

CoastTideX operates directly on the **native unstructured mesh (3.77 GB NS-grid)** for fundamental hydrodynamic reasons:
1. **Physical Fidelity**: Uses degree-2 Lagrange Polynomials (LGP2) to capture non-linear shallow-water tidal interactions ($M_4, MS_4$) and resonant coastal amplification.
2. **Adaptive Resolution**: Mesh cell size dynamically varies from tens of kilometers in the abyssal ocean to hundreds of meters in shallow estuaries, following real coastlines without geometric approximation.
3. **Absence of Interpolation Noise**: The regular 1/30° grid is merely a downsampled interpolation of this finite-element mesh and suffers from boundary smoothing errors.

---

## 4. Why Regular Grids Fail in Nearshore Environments

Conventional marine packages rely on fixed lat-lon grids, which fail on tidal flats and beaches:
- **Staircase Artifacts**: Rectangular pixels chop natural curvilinear coastlines into discrete blocks, causing spurious steps in tidal elevation;
- **Land Extrapolation Contamination**: Mathematical extrapolation into coastal land pixels introduces artificial surges or dampening;
- **Resolution Mismatch**: When remote sensing DEMs reach 10m/30m resolution, a 1/30° (~3.7 km) grid cannot resolve tidal phase lags across intertidal creeks.

---

## 5. Spatial Raster Snapshot Engine

In Snapshot Mode, CoastTideX processes user-supplied GeoTIFF DEMs or satellite imagery:
- **Per-Pixel Coordinate Reprojection**: Accurately reprojects raster pixel centers from any projected coordinate system (e.g. UTM, State Plane) to WGS84 coordinates;
- **Instantaneous Water Level Surface**: Generates a GeoTIFF matching the input raster dimensions, affine transform, and CRS;
- **Chunked Memory Safety**: Employs 512×512 pixel streaming block I/O with atomic file swapping (`*.tmp.tif`), effortlessly handling scenes exceeding 10,000×10,000 pixels.

---

## 6. Adaptive Quadtree Control Grid & Inundation Frequency

To evaluate annual potential inundation frequency over tens of millions of pixels without evaluating full FES harmonic timeseries per pixel, CoastTideX implements an **Adaptive Quadtree Control Grid with Empirical CCDF Search**:
1. **Adaptive Refinement**: Begins with a macro grid (default 4000m) and adaptively subdivides down to 500m along steep topography and water-land interfaces;
2. **In-Place Sorted Timeseries**: Nodes compute full annual timeseries and immediately sort them to construct empirical complementary cumulative distribution functions (CCDF);
3. **Spatial CCDF Search & Bilinear Interpolation**: Pixel elevation $z$ is evaluated against quad cell corner CCDFs via vectorized `np.searchsorted`, followed by local bilinear spatial interpolation;
4. **Computational Decoupling**: Decouples computation from pixel scale $\mathcal{O}(W \times H \times K)$ down to sparse control nodes $\mathcal{O}(M \times K) + \mathcal{O}(W \times H)$ ($M \ll W \times H$). The error tolerance threshold (default 1.0%) acts as an adaptive quadtree subdivision convergence criterion rather than a sensor ground-truth metric. Note that while inundation frequency calculates static cumulative probabilities on CCDFs, Exposure analysis strictly reconstructs chronological time-domain trajectories per pixel.

---

## 7. Potential Astronomical Tidal Exposure Duration Engine (v1.6 New)

### Scientific Definition & Terminology Boundary
> [!WARNING]
> **Scientific Rigor Statement**: This product is strictly defined as **"Potential Astronomical Tidal Exposure Duration under a Fixed Representative Terrain"**.<br>
> It calculates geometric continuous exposure events and durations from pure astronomical tide levels $H(t)$ against a static topography $z$.
> **It MUST NOT be described as "Beach Drying Time" or "2D Hydrodynamic Flooding/Drying"**, as actual sediment drainage and drying depend on sediment porosity, wave runup, groundwater percolation, and meteorological storm surges.

### Boundary Conditions & State Definitions:
- **Inundated State**: $H(t) > z$
- **Exposed State**: $H(t) \le z$
- **Strict Equality Boundary**: When $H(t) == z$, it is **strictly assigned to the Exposed state**, preventing ambiguous edge classifications.

### 7 Independent Spatial GeoTIFF Products:
| Product Filename Suffix | Data Type | Units | Scientific Meaning |
| :--- | :---: | :---: | :--- |
| `*_exposure_fraction.tif` | Float32 | % | Cumulative potential exposure time fraction over valid duration |
| `*_exposure_duration_h.tif` | Float32 | hours | Total cumulative potential exposure duration in hours |
| `*_exposure_max_continuous_h.tif` | Float32 | hours | Maximum single continuous potential exposure event duration |
| `*_exposure_mean_event_h.tif` | Float32 | hours | Mean continuous exposure event duration: $\frac{\text{Total Duration}}{\text{Event Count}}$ |
| `*_exposure_event_count.tif` | UInt32 | count | Number of continuous potential tidal exposure event segments identified within the requested time window |
| `*_exposure_valid_time_fraction.tif` | Float32 | % | Temporal valid data coverage ratio over the requested window |
| `*_exposure_qc.tif` | UInt16 | bitmask | Dedicated exposure quality control bitmask (0 = Valid) |

### Linear Crossing Interpolation:
Between consecutive discrete timesteps $[t_k, t_{k+1}]$ (e.g. 30min), whenever water level crosses $z$, the exact crossing timestamp $t^*$ is solved by linear interpolation, preventing 30-minute discretization steps from creating quantized staircase errors.

---

## 8. Strict Temporal Semantics & Half-Open Interval `[start, end)`

In CoastTideX v1.6, all scientific temporal products strictly adopt the **half-open interval `[start, end)` (`inclusive="left"`)**:
- **Uniform Time Weighting**: For the year 2024 (leap year) at 30min intervals, `[2024-01-01 00:00:00, 2025-01-01 00:00:00)` produces exactly **17,568** sample points, each representing a 30-minute duration;
- **Elimination of Year-End Double Counting**: A closed interval `[start, end]` would erroneously double-count `00:00:00` across adjacent annual cycles;
- **Continuous Terminal Crossing**: The terminal water level $H(t_{\text{end}})$ is stored as a dedicated array (`tide_msl_terminal_m`) in Tide Cache Schema 1.2 to enable closed crossing interpolation in the final interval without altering discrete sample counts.

---

## 9. Tide Cache Two-Stage Architecture & Schema 1.2

For large-scale workflows, CoastTideX employs a two-stage decoupled architecture:
1. **Stage 1 (Tide Cache Generation)**: Builds the adaptive control grid, evaluates FES timeseries at control nodes, and atomically serializes to a NetCDF file (`*_tide.nc`);
2. **Stage 2 (Zero-FES Downstream Products)**: Reconstructs quadtree topology and evaluates Inundation Frequency and Exposure Duration directly from cache with **zero FES calls**.

### Tide Cache Schema 1.2 Highlights:
- Backward-compatible with Schema 1.1;
- Includes optional variable `tide_msl_terminal_m(node)` for terminal continuous exposure calculations;
- Encodes deterministic SHA-256 compatibility signatures (`CACHE_SIGNATURE`).

---

## 10. Batch Intertidal Raster Engine & ExistingOutputPolicy

`BatchRasterEngine` enables robust automated processing of DEM directories:
- **Sequential Tile Execution (`max_parallel_tiles = 1`)**: Prevents out-of-memory errors by ensuring each tile has dedicated memory access;
- **Per-Tile Failure Isolation**: Corrupt GeoTIFF files are flagged as `FAILED` in the manifest without terminating the batch;
- **Dual Manifest Output**: Generates both `batch_manifest.json` and `batch_manifest.csv`;
- **Existing Output Policies**:
  - `RESUME` (Default): Skips completed tiles and resumes interrupted jobs;
  - `ERROR_IF_EXISTS`: Aborts if destination files exist;
  - `OVERWRITE`: Atomically recomputes and overwrites.

---

## 11. External Scientific Data Dependencies & Download Guide

| Data Category | Relative Path | Storage Property | Description & Requirements |
| :--- | :--- | :---: | :--- |
| **Bundled Data** | `data/geoid/us_nga_egm08_25.tif` | In Git Repo (76.86 MB) | Global EGM2008 2.5' geoid undulation grid |
| **Bundled Data** | `data/geoid/delta_n_goco06s_minus_egm2008.tif` | In Git Repo (22.66 MB) | Global ocean GOCO06s minus EGM2008 geoid difference $\Delta N$ |
| **External Mandatory** | `fes2022b/ocean_tide_non_structured/...` | External (~3.77 GB) | FES2022b native unstructured triangular mesh NetCDF |
| **External Mandatory** | `mdt_cls22/...` | External (~700 MB) | CNES-CLS22 Mean Dynamic Topography NetCDF |
| **External Optional** | `config.yaml -> paths.hybrid_mdt_source_mask` | User-defined | Authoritative Hybrid MDT classification mask (fallback to polygon ray-casting if omitted) |
| **External Optional** | `fes2022b/mask_fes2022B.nc` | External Reference (~0.98 MB, 1,027,081 bytes) | FES2022b 1/30° regular grid extrapolation mask (not used in native LGP2 workflow; see [docs/FES_MASK_METADATA_AUDIT.md](docs/FES_MASK_METADATA_AUDIT.md)) |
| **Preprocessed Reproduction** | `data/geoid/delta_n_eigen6c4_minus_egm2008.tif` | Local (Git Ignored) | Mediterranean/Black Sea geoid difference; generated via `scripts/generate_delta_n.py` |

---

## 12. Valid-Mask Topology Guard

In complex archipelagoes, narrow sandbars, and bifurcated estuaries, Euclidean distance interpolation can mistakenly leak tidal signals across land barriers.
CoastTideX applies **Valid-Mask Topology Guarding**:
- Derives a coarse physical binary mask (`topology_max_resolution_m`, default 100m);
- Computes connected components via `scipy.ndimage.label`;
- Prevents cross-barrier interpolation, falling back to one-sided available nodes and flagging `QC_BIT_CONNECTIVITY_FALLBACK`.

---

## 13. Quality Control System & UInt16 Bitmasks

Every pixel in CoastTideX products carries a bitmask for quality assurance:

### Inundation & Snapshot QC Bits:
- `bit 0 (1)`: FES extrapolated point (`QC_BIT_FES_EXTRAPOLATED`)
- `bit 1 (2)`: Degraded spatial interpolation (`QC_BIT_SPATIAL_FALLBACK`)
- `bit 2 (4)`: Insufficient control nodes (`QC_BIT_INSUFFICIENT_NODES`)
- `bit 3 (8)`: Invalid vertical datum (`QC_BIT_DATUM_INVALID`)
- `bit 4 (16)`: Geographic polygon datum approximation (`QC_BIT_DATUM_SOURCE_APPROX`)
- `bit 5 (32)`: Minimum spacing reached without meeting tolerance (`QC_BIT_MIN_SPACING_REACHED`)
- `bit 6 (64)`: Connectivity fallback across topological barrier (`QC_BIT_CONNECTIVITY_FALLBACK`)
- `bit 7 (128)`: FES validity boundary discontinuity (`QC_BIT_FES_VALIDITY_BOUNDARY`)
- `bit 8 (256)`: Maximum refinement depth reached (`QC_BIT_MAX_REFINEMENT_REACHED`)
- `65535`: NoData / Land mask

### Exposure QC Bits:
- `0`: High-fidelity valid computation (`QC_EXP_VALID`)
- `bit 0 (1)`: Degraded cell interpolation (`QC_EXP_DEGRADED_CELL`)
- `bit 1 (2)`: Insufficient valid nodes (`QC_EXP_INSUFFICIENT_NODES`)
- `bit 2 (4)`: Datum polygon approximation (`QC_EXP_DATUM_APPROX`)
- `bit 3 (8)`: Terminal sample approximated (`QC_EXP_TERMINAL_APPROX`)
- `bit 4 (16)`: Invalid time gaps present (`QC_EXP_PARTIAL_VALID_TIME`)
- `bit 5 (32)`: Permanently submerged pixel (`QC_EXP_PERMANENTLY_SUBMERGED`)
- `bit 6 (64)`: Permanently exposed pixel (`QC_EXP_PERMANENTLY_EXPOSED`)
- `65535`: NoData / Land mask (`QC_EXP_NODATA`)

---

## 14. Installation & Environment Configuration

### Prerequisites:
- Python 3.11 64-bit;
- Dedicated virtual environment recommended.

```bash
git clone https://github.com/wyhao2333/CoastTideX.git
cd CoastTideX

python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
```

---

## 15. Quick Start: CLI Guide

```bash
# 1. Potential Tidal Exposure Duration Analysis (New in v1.6)
python cli.py raster exposure \
    --dem path/to/beach_dem.tif \
    --cache path/to/beach_dem_tide.nc \
    --output-dir path/to/output_dir

# 2. Spatial Raster Snapshot
python cli.py raster snapshot \
    --input path/to/dem.tif \
    -o path/to/snapshot.tif \
    --time "2024-06-15 12:00:00" \
    --datum egm2008

# 3. Adaptive Inundation Frequency
python cli.py raster inundation \
    --dem path/to/dem.tif \
    -o path/to/inundation.tif \
    --year 2024 --step 30min \
    --dem-datum egm2008 \
    --export-cache path/to/cache_tide.nc

# 4. Batch Intertidal Raster Processing
python cli.py raster batch \
    -i path/to/dem_folder \
    -o path/to/output_folder \
    --mode all \
    --year 2024 --step 30min \
    --existing-policy resume
```

---

## 16. Quick Start: GUI Desktop Guide

Launch the desktop interface via `run_gui.bat` or `python app.py` (CLI batch mode is available via `python cli.py --help`):
- **Tab 1: Single Point / Timeseries**: Predicts timeseries, identifies HW/LW, and performs multi-datum conversion;
- **Tab 2: Batch Station Predictions**: Ingests CSV coordinate lists and evaluates water levels across multiple epochs;
- **Tab 3: Spatial Raster Simulation**: Supports Snapshot, Inundation Frequency, and Exposure Duration analysis for single GeoTIFF files;
- **Tab 4: Batch Intertidal Raster Processing**: Configures batch folders, job modes, and policies with live progress tracking.

---

## 17. Typical Scientific & Engineering Applications

1. **Satellite-Derived Bathymetry (SDB) & Intertidal Shoreline Inversion**: Corrects satellite overpass water levels with pixel-accurate vertical datum alignments;
2. **Coastal Wetland & Mangrove Morphodynamics**: Quantifies inundation frequencies and continuous exposure windows for habitat suitability modeling;
3. **Marine Infrastructure & Coastal Engineering**: Harmonizes offshore wind foundations and sea bridges with national terrestrial vertical geoids.

---

## 18. Computational Efficiency & Memory Safety

CoastTideX is architected for large-scale coastal remote sensing scenes and long timeseries simulations:

1. **Decoupled Quadtree Control Grid vs. Brute-Force Pixel Inversion**:
   - Rather than evaluating full FES harmonic expansions across tens of millions of DEM pixels, CoastTideX adaptively concentrates tidal evaluations on sparse quadtree control nodes (hundreds to thousands of nodes per scene);
   - Inundation frequency is rapidly inverted via empirical CCDF search at each pixel, bypassing over 99% of redundant FES calculations while bounding the spatial error to the configured tolerance (default < 1.0%).

2. **2D Vectorized Streaming State Machine with Bounded Memory**:
   - Completely eliminates 3D `(rows, cols, time_chunk)` pixel tensor allocations in memory;
   - Streaming updates occur within a 512×512 spatial block window along the time dimension;
   - Memory usage depends predictably on block_size, local control node density, and streaming buffer chunks, providing scalable execution during full-year 17,568-step evaluations.

---

## 19. Unit Testing & Quality Verification

CoastTideX is supported by a comprehensive tiered automated test architecture, consisting of a lightweight portable CI test suite and a local full validation harness with real FES2022b:
```bash
python -m unittest discover -s tests -p "test_*.py"
```

### Testing Strategy & Isolation:
1. **GitHub Actions Remote CI**: Runs in a headless Linux runner without graphical display or C/C++ compiled `pyfes` extensions, leveraging mock predictors and analytical solvers to verify datum closures, quadtree topology guards, Tide Cache NetCDF chunked streaming, and 2D state machine physics (live status reflected by the GitHub Actions CI badge above);
2. **Local Full Validation Harness**: Located at `tests/test_v15_beta_validation_harness.py`, executing physical end-to-end evaluations with real FES2022b native mesh (3.77 GB) and real coastal DEMs;
3. **v1.6 Production Hardening Suite**: Production-grade scenarios verifying slice reader bounds, barrier topology isolation, weight re-normalization, timezone parsing, atomic temp file cleanup, stale DEM protection, zero FES call invariance in Stage 2, and corrupt node index integrity errors.

---

## 20. Changelog & Version Evolution

See the full history in [CHANGELOG.md](CHANGELOG.md):
- **v1.6 (Beta / Feature Release)**: Exposure Duration Engine (7 GeoTIFF products), Linear Crossing Interpolation, Strict `[start, end)` Semantics, Tide Cache Schema 1.2, Bilingual Development Guide;
- **v1.5 Beta**: Real FES2022b and Real Coastal DEM Validation Suite;
- **v1.5 Alpha**: Batch Intertidal Raster Engine, Tide Cache Architecture, and Compatibility Signatures;
- **v1.4**: Spatial Raster Engine (Snapshot & Adaptive Inundation Frequency);
- **v1.3**: Multi-Datum Geodetic Transformation System (MSL, MDT, EGM2008, WGS84).

---

## 21. Citation & Acknowledgements

If you use CoastTideX in your research or engineering projects, please cite:

```bibtex
@software{CoastTideX_2026,
  author = {Wang, Yuhao},
  title = {CoastTideX: A High-Precision Coastal Spatial Raster Tide Simulation and Multi-Datum Transformation System},
  year = {2026},
  version = {v1.6},
  url = {https://github.com/wyhao2333/CoastTideX}
}
```

### Models & Data Acknowledgements:
- **FES2022b**: LEGOS, NOVELTIS, CLS, CNES (DOI: [10.24400/527896/a01-2024.004](https://doi.org/10.24400/527896/a01-2024.004));
- **CNES-CLS22 MDT**: CLS Space Oceanography Division and CNES (DOI: [10.24400/527896/a01-2023.003](https://doi.org/10.24400/527896/a01-2023.003));
- **GOCO06s Gravity Field**: ICGEM, GFZ German Research Centre for Geosciences, Potsdam;
- **EGM2008 Geoid**: National Geospatial-Intelligence Agency (NGA), Pavlis et al. (2012).

---

## 22. Author & License

- **Author / Developer**: **王宇浩** (Yuhao Wang)
- **Discipline**: Coastal Ocean Dynamics & Geodesy
- **License**: [MIT License](LICENSE)
