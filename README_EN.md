# CoastTideX: High-Precision Global Coastal Tide Simulation & Vertical Datum System

<p align="center">
  <img src="https://img.shields.io/badge/Release-v1.5--alpha-blue.svg" alt="Release v1.5-alpha">
  <img src="https://img.shields.io/badge/Python-3.11-blue.svg" alt="Python 3.11">
  <img src="https://img.shields.io/badge/GUI-PyQt6-green.svg" alt="PyQt6">
  <img src="https://img.shields.io/badge/Tide%20Model-FES2022b%20LGP2-0284c7.svg" alt="FES2022b">
  <img src="https://img.shields.io/badge/MDT-CNES--CLS22-8b5cf6.svg" alt="CNES-CLS22 MDT">
  <img src="https://img.shields.io/badge/Datum-MSL%20%7C%20EGM2008-f59e0b.svg" alt="Vertical Datum">
  <img src="https://img.shields.io/badge/License-MIT-emerald.svg" alt="MIT License">
</p>

<p align="center">
  <a href="README.md">中文版本 (Chinese)</a> | <b>[ English Version ]</b>
</p>

---

## 📖 Introduction

**CoastTideX** is an end-to-end desktop software platform engineered for **marine engineering, coastal remote sensing, geodetic datum unification, and underwater hydrodynamic modeling**.

The platform is powered by the CNES/AVISO state-of-the-art **FES2022b global ocean tide model** (featuring native non-structured triangular finite-element meshes with LGP2 polynomial interpolation across all 34 primary tidal constituents). This resolves the jagged boundary and staircase errors traditional regular grid models suffer along intricate coastlines and estuaries. Furthermore, CoastTideX integrates the **CNES-CLS22 Mean Dynamic Topography (MDT)** and the **NGA EGM2008 2.5-arcminute global geoid raster**, enabling seamless and precise vertical datum transformation from **Mean Sea Level (MSL)** to the absolute **EGM2008 geoid datum**.

**v1.4 introduces the Spatial Raster Tide Engine (Release Candidate)**: evaluates 2D spatially varying sea surface heights at specific timestamps for any CRS-referenced GeoTIFF, and features an **Adaptive Tide Control Grid** to stream potential astronomical tidal inundation frequencies (0% ~ 100%) for high-resolution 10m/30m coastal DEMs across full years or arbitrary periods. Current status: code-complete release candidate for real-world validation.

---

### ✨ Key Features

* 🌊 **FES2022b Native Non-Structured Mesh**: Directly loads the 3.77 GB native triangular mesh, providing ultimate fidelity in coastal zones with 34 diurnal, semi-diurnal, shallow-water non-linear, and long-period constituents.
* 🛰️ **Spatial Raster Tide Engine (v1.4, RC)**:
  * **Instantaneous Sea Surface Snapshot**: Evaluates true spatially varying water levels across GeoTIFF rasters with strict pixel-center unprojecting (`offset='center'`), streaming in 512×512 windows with atomic replacement;
  * **Adaptive Tide Control Grid for Coastal DEMs**: Places adaptive nodes (default 4km) across water/tidal flat domains, pre-sorts annual water levels on control nodes, evaluates pixel exceedance quantiles via binary search (`np.searchsorted`), and applies bilinear spatial interpolation within quadtree leaf cells to efficiently map potential astronomical tidal inundation frequency (0% ~ 100%);
  * **Valid-mask Topology-aware Interpolation Guard**: Identifies contiguous water/tidal domains based on the DEM valid-pixel/NoData mask, preventing cross-barrier tidal leakage across NoData barriers. (Note: operates strictly on DEM valid mask topology, not a 2D hydrodynamic simulation; structures like seawalls and dikes with valid DEM elevations are not automatically treated as barriers); preserves NoData across deep inland areas;
  * **TIFF-Level Rigorous Provenance**: Inundation and snapshot GeoTIFFs embed full geodetic metadata, model version, and parameter tags.
* 📅 **Full-Year & Long Time-Series High-Density Prediction (v1.3/v1.4)**:
  * **Custom Period & Year Mode Toggle**: Seamlessly switch between arbitrary date intervals and convenient annual presets (e.g. Year 2024);
  * **Strict Half-Open Interval & Sample Count Fidelity**: Employs $[start, end)$ half-open interval, rigorously generating exactly **17,568** samples for leap year 2024 at 30-min cadence (17,520 for standard years) with zero boundary overlap or drops;
  * **Real-time Sample Budget & Auto-Switch**: Instantly updates expected sample count label; automatically recommends 30min cadence when switching to annual mode while remembering user preferences.
* ⏳ **Adaptive Time-Chunking Stream Engine**:
  * The evaluation engine introduces an automated 5,000-point time-chunking pipeline for continuous multi-year or high-frequency (5min/6min/10min) simulations, reporting progress per slice and completely eliminating GUI freezes and unhandled crash risks;
  * Validated through continuous 2-year simulation stress testing (35,089 timestamps) without interruption.
* ⚡ **Adaptive Spatial Chunking & Local BBox Caching**:
  * **Single Station Mode**: Automatically bounds the region of interest around input coordinates, indexing only local topology in memory for sub-second query speeds and minimal RAM footprint (~1.2 GB);
  * **Global Discrete Batch Mode**: Employs $5^\circ \times 5^\circ$ adaptive spatial mesh chunking, preventing memory blowup when processing scattered worldwide points.
* 📐 **Dual-Geoid Hybrid MDT Vertical Datum Pipeline (v1.3/v1.4)**:
  * **Two-Tier Detection & Optional External Datasets**: Priority matching against CNES official `hybrid_mdt_source_mask.tif` (optional external dataset, not bundled), with pure-NumPy closed polygon fallback (marked as `QC_DATUM_SOURCE_APPROX`);
  * **Open Oceans Geoid**: GOCO06s reference datum ($H_{\text{EGM2008}} = \text{Tide} + \text{MDT} + \Delta N_{\text{GOCO06s}\rightarrow\text{EGM2008}}$);
  * **Mediterranean & Black Sea Geoid**: EIGEN-6C4 ($d/o=2190$) regional datum ($H_{\text{EGM2008}} = \text{Tide} + \text{MDT} + \Delta N_{\text{EIGEN-6C4}\rightarrow\text{EGM2008}}$, supported via local optional external `data/geoid/delta_n_eigen6c4_minus_egm2008.tif`; if missing, strict mode raises error while non-strict mode falls back to polygon approximation);
  * **Unified Primary Variable & Semantic Truth**: Primary variable `h_mdt_ref_m` denotes height relative to MDT reference geoid; `h_goco06s_m` is strictly `NaN` in Mediterranean/Black Sea (no faking); deep inland points strictly propagate `NaN`.
* 🎯 **True Bilinear Spatial Interpolation & Target-Aware Loading (v1.3/v1.4)**:
  * Applies true bilinear interpolation (`map_coordinates(order=1)`) to EGM2008 and $\Delta N$ GeoTIFFs;
  * Decouples target datums: MSL or EGM2008 workflows do not require loading unneeded WGS84 rasters.
* 🖥️ **Modern Desktop GUI (PyQt6) & Big Data Safety (v1.3/v1.4)**:
  * **Dedicated Raster Tide Tab (Tab 3)**: Interactive GeoTIFF metadata inspector card, Snapshot vs. Inundation mode panels, adaptive grid spacing controls, progress bar, and cancellation support;
  * **Decoupled Compute & Display Datums**: Allows computing in one datum while displaying another;
  * **Safe Preview Truncation**: Capped to first 2,000 rows in GUI table preview while exporting 100% full dataset to CSV/Excel;
  * **Smart Chart Downsampling & Adaptive Peaks**: Automatic decimation and adaptive peak/trough annotation filtering for responsive exploration of 10,000+ points;
  * **Adaptive Scrolling Panel**: Control panel wrapped in `QScrollArea`, completely removing vertical resizing limits for 768p/1080p displays.
* 📑 **Comprehensive CLI Tooling**: Subcommands for `single`, `batch`, `raster snapshot`, and `raster inundation`.
* 📦 **Standalone Executable (.exe) Readiness**: Launch via `run_gui.bat` or compile into a standalone Windows `.exe` application via `build_exe.bat`.

---

## 💻 System & Hardware Requirements

CoastTideX is engineered with adaptive spatial indexing to maintain high performance across diverse hardware configurations:

| Usage Scenario | Minimum RAM | Recommended RAM | Compute & Storage | Details |
| :--- | :---: | :---: | :--- | :--- |
| **Single Location Time-Series**<br>*(Single Point Mode)* | **4 GB** | **8 GB** | Dual-core CPU or better<br>Free Disk Space ≥ 10 GB | Local BBox caching loads only topology around the target location; resident memory is only ~1.2 GB. |
| **Local Regional Batch**<br>*(≤ 8° Geographic Span)* | **4 GB** | **8 GB** | Quad-core CPU or better<br>Free Disk Space ≥ 10 GB | Small regional point clusters are solved in a single bounding box with minimal overhead. |
| **Global Discrete Batch**<br>*(Worldwide Scattered Points)* | **8 GB** | **16 GB** | Quad- to Octa-core CPU<br>High-speed NVMe SSD | $5^\circ \times 5^\circ$ adaptive spatial chunking processes points in clusters, bounding peak memory. |
| **Spatial Raster Engine**<br>*(Snapshot & Inundation, v1.4)* | **8 GB** | **16 GB** | Quad- to Octa-core CPU<br>High-speed NVMe SSD | 512×512 window streaming and adaptive control grid decouple memory from total raster dimensions. |
| **Full Unconstrained Global Grid**<br>*(All-Mesh Global Loading)* | **16 GB** | **32 GB** | Octa-core CPU or better<br>High-speed NVMe SSD | Loading all 5.69 million nodes and 34 constituents simultaneously requires ~6–8 GB of contiguous RAM. |

* **Supported Operating Systems**: Windows 10/11 64-bit, Ubuntu 20.04+, macOS (x86_64 / Apple Silicon via Rosetta 2).
* **Python Runtime**: Python 3.11.

---

## 🏛️ System Architecture

```text
                                ┌─────────────────────────────────────────┐
                                │          CoastTideX (GUI / CLI)         │
                                └────────────────────┬────────────────────┘
                                                     │
              ┌──────────────────────────────────────┼──────────────────────────────────────┐
              ▼                                      ▼                                      ▼
   ┌───────────────────────┐              ┌───────────────────────┐              ┌───────────────────────┐
   │   Tide Engine Core    │              │   Datum Engine Core   │              │   Raster Engine Core  │
   └──────────┬────────────┘              └──────────┬────────────┘              └──────────┬────────────┘
              │                                      │                                      │
    ┌─────────┴─────────┐                   ┌────────┴────────┬────────┐             ┌──────┴──────┐
    ▼                   ▼                   ▼                 ▼        ▼             ▼             ▼
 FES2022b Native Mesh   Spatial BBox Index CNES-CLS22 MDT  ΔN GeoTIFF EGM2008     Snapshot      Adaptive Grid
 (5.69M nodes / 34)     (Sub-sec Chunking) (Mask / Poly)   (GOCO/EIG) (Undulation)(512x512 Stream)(10m DEM CCDF)
              │                                      │                                      │
              └──────────────────────────────────────┼──────────────────────────────────────┘
                                                     ▼
                        ┌─────────────────────────────────────────────────────────┐
                        │              Unified Four-Tier Vertical Datums          │
                        │             (MSL / MDT_REF / EGM2008 / WGS84)           │
                        │    Interactive Charts / CSV / XLSX / Spatial GeoTIFFs   │
                        └─────────────────────────────────────────────────────────┘
```

---

## 📐 Scientific Formulation

### 1. Harmonic Tidal Synthesis
The instantaneous sea surface height $\eta(t)$ at any coordinate $(\lambda, \phi)$ is calculated as the superposition of 34 constituents:

$$\eta(t) = \sum_{i=1}^{34} f_i(t) \cdot H_i(\lambda, \phi) \cdot \cos\left( \omega_i t + V_{0,i}(t_0) + u_i(t) - g_i(\lambda, \phi) \right) + h_{\text{LP}}(t)$$

* $H_i, g_i$: Modeled amplitude and Greenwich phase lag from the FES2022b finite-element mesh;
* $f_i(t), u_i(t)$: Nodal modulation factors covering the 18.61-year lunar nodal cycle;
* $h_{\text{LP}}(t)$: Equilibrium long-period tide.

#### 2. Dual-Geoid Hybrid MDT Vertical Datum Pipeline (v1.3)
In simplified workflows, practitioners often equate $\text{Tide} + \text{MDT}$ directly to EGM2008 height. **This is scientifically inaccurate**.
The CNES-CLS22 MDT model is a **hybrid geodetic product**:
* **Global Open Oceans**: Referenced to the **GOCO06s satellite-only gravity geoid**, which globally diverges from the **EGM2008 geoid** by $-6.63\text{m} \sim +6.79\text{m}$ (standard deviation $0.34\text{m}$);
* **Mediterranean & Black Sea**: Referenced to the regional ultra-high-degree **EIGEN-6C4 ($d/o=2190$)** geoid model.

CoastTideX v1.3 resolves this discrepancy through a mathematically rigorous geodetic transformation:

1. **Instantaneous Tide relative to Mean Sea Level (MSL)**:
   $$\text{Tide}_{\text{MSL}}(\lambda, \phi, t) = \eta(t)$$
2. **Sea Surface Height relative to MDT Reference Geoid (Primary Variable)**:
   $$H_{\text{MDT-REF}}(\lambda, \phi, t) = \text{Tide}_{\text{MSL}}(\lambda, \phi, t) + \text{MDT}_{\text{CLS22}}(\lambda, \phi)$$
   * In open oceans, this represents height above the GOCO06s geoid ($H_{\text{GOCO06S}}$);
   * In the Mediterranean and Black Sea, `h_goco06s_m` is strictly `NaN` (no faking), with the reference geoid automatically transitioning to EIGEN-6C4.
3. **Orthometric Height relative to EGM2008 Geoid** (incorporating geoid difference correction $\Delta N$):
   $$H_{\text{EGM2008}}(\lambda, \phi, t) = H_{\text{MDT-REF}}(\lambda, \phi, t) + \Delta N(\lambda, \phi)$$
   * Global Ocean: $\Delta N(\lambda, \phi) = N_{\text{GOCO06S}}(\lambda, \phi) - N_{\text{EGM2008}}(\lambda, \phi)$;
   * Mediterranean & Black Sea: $\Delta N(\lambda, \phi) = N_{\text{EIGEN-6C4}}(\lambda, \phi) - N_{\text{EGM2008}}(\lambda, \phi)$.
4. **WGS84 3D Geometric Ellipsoidal Height**:
   $$h_{\text{WGS84}}(\lambda, \phi, t) = H_{\text{EGM2008}}(\lambda, \phi, t) + N_{\text{EGM2008}}(\lambda, \phi)$$
   where $N_{\text{EGM2008}}$ is extracted via true bilinear interpolation from the bundled global 2.5' EGM2008 raster (strictly eliminating the 1.25' half-pixel offset).

### 3. Quality Control (QC) Flags
Every prediction point is tagged with an evaluation `quality_flag` and `qc_warning`:

| Quality Flag | Definition | Scientific Meaning & Processing |
| :---: | :---: | :--- |
| **Flag 1 ~ 6** | Valid Interpolation | The target point falls inside a high-resolution triangular finite element. Full polynomial accuracy. |
| **Flag < 0** | Extrapolated | The target point is near complex coastal shorelines or shallow flats. Extrapolated by tide dynamics. Highlighted in amber. |
| **Flag = 0** | Missing / Inland | Inland point or no tidal solution available. Tide and datums are set to `NaN` (no silent zeros). Highlighted in red. |

### 4. International Standard Tidal Prediction Intervals (Literature Benchmarks)
CoastTideX supports 5min, 6min, 10min, 15min, 30min, 1h, and 2h sampling steps based on authoritative international standards:

| Recommended Step | Standard & Operational Scenario | Scientific Basis & Literature Citations |
| :--- | :--- | :--- |
| **6 minutes (0.1 h)** | **NOAA Operational Tide Gauges & Real-time Predictions** | **NOAA CO-OPS Operational Specification**: The gold standard across US real-time tide gauge networks. High frequency is essential for capturing shallow-water non-linear overtides ($M_4, MS_4, M_6$) and peak turning points. |
| **10 ~ 15 minutes** | **IOC / GLOSS Global Tide Stations** | **UNESCO IOC / GLOSS Specifications**: Standard operational cadence for global sea-level monitoring stations, balancing wave peak fidelity with data volume. |
| **30 minutes (0.5 h)** | **Annual High-Density Coastal Simulations (v1.3)** | **Coastal Hydrodynamic Modeling**: Optimal trade-off between computational efficiency and waveform fidelity. Rigorously yields 17,568 samples for leap year 2024. |
| **1 hour (60 minutes)** | **Classical Harmonic Analysis & Long-term Sea Level** | **Foreman (1977) & Pawlowicz et al. (2002, T_TIDE)**: The standard input interval for classic harmonic tidal analysis and multi-decadal sea level variation research. |

### 5. Potential Astronomical Tidal Inundation Frequency Analysis
For coastal wetland conservation, mangrove zonation, and flood defense planning, evaluating the hydroperiod and inundation frequency under astronomical tidal forcing is critical.
CoastTideX provides a vectorized complementary empirical cumulative distribution function (CCDF / 1 - ECDF):

$$P_{\text{inundation}}(z) = P(\eta_{\text{tide}} > z) = 1 - F(z) = \frac{1}{N}\sum_{i=1}^N \mathbb{I}(\eta_i > z)$$

Leveraging `np.searchsorted`, this $O(M \log N)$ algorithm calculates inundation frequency over annual time-series for single point elevations or massive Digital Elevation Model (DEM) arrays in sub-second time.

### 6. Spatial Raster Tide Engine & Adaptive Control Grid (v1.4)
For coastal remote sensing interpretation and tidal wetland dynamics, CoastTideX v1.4 integrates an industrial-grade spatial raster engine (`core.raster_engine.RasterTideEngine`):

1. **Instantaneous Sea Surface Snapshot**:
   * Rigorously converts GeoTIFF pixel grid coordinates to geographic coordinates $(\lambda, \phi)$ using true pixel centers (`offset='center'`);
   * Streams raster blocks in 512×512 windows, computing 2D tidal elevation and target vertical datums with atomic file replacement (`os.replace`) to avoid incomplete corrupted outputs;
   * Preserves exact input CRS, affine transform, dimensions, and NoData masks while injecting comprehensive geodetic provenance tags.

2. **Adaptive Tide Control Grid for High-Resolution Coastal DEMs**:
   * **Computational Barrier**: Evaluating $10000 \times 10000$ (100M) 10m DEM pixels across 17,568 timestamps demands $1.75 \times 10^{12}$ tidal queries, which is computationally intractable and physically unwarranted given long-wave hydrodynamic continuity;
   * **Adaptive Control Nodes**: Dynamically places control points at user-defined spatial spacing (default 4,000 meters) across water and tidal flat regions;
   * **Batch Annual Hydrograph Evaluation**: Generates complete time series (e.g. 17,568 points) across the sparse control grid;
   * **Pre-sorted CCDF & Bilinear Spatial Interpolation**: Pre-sorts water levels at control nodes ($O(T \log T)$), rapidly queries exceedance probabilities per pixel via binary search ($O(\log T)$ via `np.searchsorted`), and bilinearly interpolates CCDF values within quadtree leaf cells;
   * **Valid-mask Topology-aware Guard**: Prevents tidal interpolation across NoData land barriers based on physical-scale connected components. (Note: relies on DEM valid/NoData mask topology, not a 2D hydrodynamic numerical model; seawalls with valid elevations are not automatically identified as barriers). Deep inland or isolated regions without valid ocean control nodes propagate NoData and receive UInt16 QC flags.

---

## 🚀 Quick Start

### 1. Environment Setup
Run `setup_env.bat` in the project root to automatically configure the dedicated Python 3.11 `.venv`.

Manual setup:
```bash
# Create virtual environment
python -m venv .venv
# Activate environment (Windows)
.venv\Scripts\activate
# Install requirements
pip install -r requirements.txt
```

### 2. Launch GUI
Double-click `run_gui.bat` or run:
```bash
.venv\Scripts\python.exe app.py
```

### 3. Command-Line Interface (CLI)
Automate predictions using `cli.py`, with full support for timezones, annual presets, datums, and spatial rasters:
* **Single Location Time-Series (Custom Period)**:
  ```bash
  python cli.py single --lon 122.0 --lat 31.0 --start "2026-09-10 00:00:00" --end "2026-09-11 00:00:00" --step 1h --tz UTC --output output.csv
  ```
* **Single Location Annual High-Density Prediction (Strictly 17,568 samples for 2024)**:
  ```bash
  python cli.py single --lon 122.0 --lat 31.0 --year 2024 --step 30min --output tide_2024.csv
  ```
* **Batch Tabular Processing**:
  ```bash
  python cli.py batch --input points.csv --lon-col longitude --lat-col latitude --time-col datetime --tz UTC --output batch_out.csv
  ```
* **Spatial Sea Surface Snapshot Raster (v1.4 Snapshot)**:
  ```bash
  python cli.py raster snapshot --input dem_or_scene.tif --output snapshot_water_level.tif --time "2024-06-18 10:30:00" --datum egm2008
  ```
* **Coastal DEM Potential Inundation Frequency Raster (v1.4 Inundation)**:
  ```bash
  python cli.py raster inundation --dem coastal_flat_dem.tif --output inundation_pct_2024.tif --year 2024 --freq 30min --datum egm2008 --spacing-m 4000
  ```
  *(Note: `scripts/calculate_inundation_raster.py` remains supported as a backwards-compatible CLI wrapper)*

### 4. Python API Usage

#### (1) Annual 30-min High-Density Prediction (Strictly 17,568 samples for leap year 2024)
```python
from core.tide_engine import FESTidePredictor
from core.datum_engine import DatumTransformer
from core.utils import compute_inundation_frequency

predictor = FESTidePredictor()
transformer = DatumTransformer()

# Predict full-year 2024 at 30-min resolution ([2024-01-01, 2025-01-01) half-open interval)
df_year = predictor.predict_year(
    lon=122.0, lat=31.0,
    year=2024,
    freq="30min",
    source_tz="UTC"
)
print(f"2024 Sample Count: {len(df_year)}")  # Outputs exactly: 17568

# Rigorous vertical datum conversion
datum_res = transformer.convert_tide_datums(
    tide_msl_m=df_year['tide_total_m'].values,
    lons=122.0,
    lats=31.0
)

df_year['tide_msl_m'] = datum_res['tide_msl_m']
df_year['h_mdt_ref_m'] = datum_res['h_mdt_ref_m']
df_year['h_goco06s_m'] = datum_res['h_goco06s_m']
df_year['h_egm2008_m'] = datum_res['h_egm2008_m']
df_year['h_wgs84_m'] = datum_res['h_wgs84_m']

# Inundation frequency evaluation across coastal elevations (+1.5m, +2.0m, +2.5m)
elevations = [1.5, 2.0, 2.5]
freq_pct = compute_inundation_frequency(
    water_levels_m=df_year['tide_msl_m'].values,
    terrain_elevations_m=elevations,
    as_percentage=True
)
for elev, pct in zip(elevations, freq_pct):
    print(f"Inundation probability at elevation {elev:+.1f} m: {pct:.2f}%")
```

#### (2) Spatial Raster Snapshot & DEM Inundation Frequency (v1.4)
```python
from core.raster_engine import RasterTideEngine

raster_engine = RasterTideEngine()

# 1. Compute instantaneous spatial sea surface elevation snapshot
summary_snap = raster_engine.calculate_snapshot_raster(
    input_raster_path="coastal_flat_dem.tif",
    output_raster_path="water_level_snapshot_egm2008.tif",
    timestamp="2024-06-18 10:30:00",
    datum="egm2008"
)
print(f"Snapshot done: {summary_snap.valid_pixels} valid pixels in {summary_snap.elapsed_seconds:.2f}s")

# 2. Compute annual potential astronomical tidal inundation frequency (0~100%)
summary_inund = raster_engine.calculate_inundation_raster(
    dem_path="coastal_flat_dem.tif",
    output_path="inundation_frequency_2024.tif",
    year=2024,
    freq="30min",
    datum="egm2008",
    control_spacing_m=4000
)
print(f"Inundation analysis done: mean inundation = {summary_inund.mean_val:.2f}%")
```

---

## 📁 Directory Structure

```text
CoastTideX/
├── .github/workflows/ci.yml        # Automated GitHub Actions test pipeline
├── .gitignore                      # Excludes large netCDF meshes, .venv, build caches
├── LICENSE                         # MIT License
├── README.md                       # Chinese Documentation
├── README_EN.md                    # English Documentation
├── requirements.txt                # Python package dependencies
├── setup_env.bat                   # Portable .venv initializer
├── run_gui.bat                     # GUI launcher script
├── build_exe.bat                   # PyInstaller standalone packager
├── config.yaml                     # Application & data path configuration
├── app.py                          # Desktop GUI entrypoint
├── cli.py                          # Headless CLI entrypoint
├── scripts/
│   ├── generate_delta_n.py         # Generalized ΔN raster generator (GOCO06s, EIGEN-6C4, etc.)
│   └── calculate_inundation_raster.py # Coastal tidal flat 10m/30m DEM inundation frequency raster tool
├── data/
│   └── geoid/
│       ├── README_GEOID.md         # Geodetic provenance & ICGEM specifications
│       ├── hybrid_mdt_source_mask.tif # CNES-CLS22 MDT official reference geoid mask (v1.4 priority)
│       ├── us_nga_egm08_25.tif     # NGA EGM2008 2.5' global geoid raster (~76.9MB)
│       ├── delta_n_goco06s_minus_egm2008.tif # GOCO06s - EGM2008 correction raster (~22.7MB)
│       └── delta_n_eigen6c4_minus_egm2008.tif # EIGEN-6C4 - EGM2008 correction raster (Med & Black Sea, local generation)
├── core/                           # Core computation modules
│   ├── tide_engine.py              # FES2022b evaluator, time-chunking, spatial chunking & annual prediction
│   ├── datum_engine.py             # Dual-geoid Hybrid MDT transformation, official mask & polygon checks
│   ├── raster_engine.py            # (v1.4) Spatial raster tide engine, pixel-center alignment, snapshot & adaptive DEM inundation
│   └── utils.py                    # Presets, coordinates, timezones (DST), inundation & safe metadata
├── gui/                            # PyQt6 desktop application
│   ├── main_window.py              # Main window (single point, period, annual mode, table preview & Tab 3 Raster Panel)
│   ├── chart_widget.py             # Matplotlib waveform component (smart decimation & adaptive peaks)
│   ├── manual_dialog.py            # Built-in User Manual & Documentation dialog (v1.4)
│   ├── settings_dialog.py          # Data source path configuration dialog & deep file validation
│   └── styles.py                   # High-contrast dark QSS stylesheet
└── tests/                          # Automated unit and integration test suite
    └── test_engines.py             # Comprehensive 47-test suite (full FES chain, adaptive quadtree, barrier isolation, leap year, raster engine, closures)
```

---

## 📦 Packaging to Executable (.exe)

A ready-to-use PyInstaller configuration is provided in `build_exe.bat`:
1. Run `build_exe.bat`;
2. Find the standalone application in `dist/CoastTideX/CoastTideX.exe`.

> [!NOTE]
> `build_exe.bat` serves as a release reference script for Windows environments containing essential hidden import definitions. Validation on a target clean release environment is recommended before formal distribution.

## 📝 Changelog

### v1.5 Alpha (2026-09)
* **[Feature] Batch Intertidal Raster Engine (BatchRasterEngine)**: Automated folder scanning, filtering, and deterministic sorting with sequential tile processing (`max_parallel_tiles = 1`);
* **[Feature] Persistent NetCDF4 Tide Cache (*_tide.nc)**: Decouples Stage 1 (FES control grid solving) and Stage 2 (Cache-driven inundation frequency), guaranteeing zero FES calls during Stage 2;
* **[Feature] Single-Tile Failure Isolation & Manifest-Driven Resume**: `batch_manifest.json` and `.csv` track tile state machine; isolates single-file failures and resumes `TIDE_READY` tiles without recalculating tides;
* **[Algorithm] Target-Aware Intertidal Refinement**: `target_mode="intertidal"` optimizes quadtree refinement based on DEM target elevation errors rather than purely land/ocean discontinuity;
* **[Audit] Local FES2022b Package Read-Only Audit**: Fully audited local package (`docs/FES2022B_LOCAL_AUDIT_V1_5.md`), explicitly disabling compressed XZ extrapolated fallback in Phase 1;
* **[GUI/CLI] Batch Tab and CLI Commands**: Added dedicated Batch Intertidal tab in GUI and `raster batch` / `raster batch-intertidal` CLI subcommands;
* **[Testing] Test Suite Expanded to 61 Tests**: Added 11 tests covering batch discovery, time sampling fidelity, Tide Cache round-trip, Stage 2 zero FES verification, spatial attribute inheritance, narrow strip refinement, failure isolation, resume state machine, and adjacent tile seam continuity (61/61 OK).

### v1.4 (2026-09)
* **[Major Upgrade] Spatial Raster Tide Engine (RasterTideEngine)**: Integrated `core/raster_engine.py` for any CRS-enabled GeoTIFF, evaluating 2D spatially varying sea surface heights with strict pixel-center alignment (`offset='center'`) and 512×512 atomic window streaming;
* **[Algorithmic Breakthrough] Adaptive Quadtree Control Grid**: Solved the intractable computational barrier of multi-billion pixel evaluations on 10m/30m coastal DEMs by dynamically refining adaptive quadtree control nodes with valid-mask topological connectivity guards, evaluating batch time series, and applying localized pre-sorted CCDF binary search with bilinear spatial interpolation to stream potential astronomical inundation frequencies (0% ~ 100%) and UInt16 quality masks;
* **[Geodetic Rigor & Mask Priority]**: Priority matching against CNES official `hybrid_mdt_source_mask.tif` (canonical 0/1/2/3/255 categories) with projected CRS reprojection and polygon fallback; strict array dimension broadcast enforcement; decoupled EGM2008 and WGS84 raster dependencies;
* **[GUI Dedicated Raster Tide Panel (Tab 3)]**: Added dedicated Raster Tide & Inundation tab featuring interactive GeoTIFF metadata cards, Snapshot vs. Inundation mode panels, adaptive grid spacing controls, progress bar, and cancellation support;
* **[Decoupled Compute vs. Display Datums]**: GUI supports computing in one vertical datum and displaying another; auto-recommends 30-min interval upon toggling Year Mode with user memory;
* **[Deep Settings Validation]**: Settings dialog incorporates deep format validation for NetCDF and GeoTIFFs, with unified reset keys;
* **[Comprehensive Test Suite Expansion to 47 Tests]**: Added adaptive quadtree dynamic subdivision growth, minimum spacing termination & QC bit 32, topological connectivity barrier isolation, canonical mask 5-category handling, projected CRS source mask reprojection, circular longitude wrapping across 0°/180°, unit factor scaling (meters/feet), and end-to-end synthetic oracle validation, circular split BBoxes, two-basin cross-barrier isolation oracle, validity-boundary refinement & edge probing, physical-scale topology downsampling barrier preservation, level-wise batching efficiency, and max_fes_evaluate_points passing (47/47 passing). (Note: GitHub Actions CI executes synthetic and logic tests without requiring large FES model files; local development environment runs the full suite including skipped tests);
* **[v1.4 RC Final Hardening]**:
  * **Resident Memory Budget Guard**: Implemented explicit `max_in_memory_control_nodes` hard limit raising structured `RasterMemoryLimitError` upon budget breach, replacing unfulfilled memmap promises;
  * **CLI Year Mode Datum Decoupling**: Fixed duplicate datum conversion in `cli.py` single year mode; MSL mode strictly bypasses `DatumTransformer`;
  * **Direct Sampled-Pixel FES Oracle**: Enhanced `scripts/validate_real_fes_raster.py` with direct pixel-center FES prediction oracle (`Direct Sampled-Pixel FES Oracle`), and renamed dense grid comparison to `Dense Regular Control-Grid Reference`;
  * **CI Headless GUI Assertion**: In CI environments with PyQt6, GUI import failures are asserted as strict test failures rather than silently skipped;
  * **Scientific Nomenclature & Boundary Alignment**: Purged legacy IDW and 2D hydrodynamic claims from documentation; strictly framed topology protection as valid-mask based and clarified optional external datasets.

### v1.3 (2026-09)
* **[Annual Mode & Long Time-Series]** Added Year Mode toggle with strict $[start, end)$ half-open interval, generating exactly **17,568** samples for leap year 2024 at 30-min cadence (17,520 for standard years), with dynamic sample budget estimation and coordinate validation;
* **[Adaptive Time-Chunking Engine]** Implemented 5,000-point dynamic chunking with real-time per-slice progress callback in the tide engine, validated across 2-year continuous stress testing (35,089 timestamps) with zero memory/UI stalls;
* **[Dual-Geoid Hybrid MDT]** Rigorously separated open oceans (GOCO06s) and Mediterranean/Black Sea (EIGEN-6C4) geodetic reference datums; introduced unified primary variable `h_mdt_ref_m`; strictly enforces `NaN` for `h_goco06s_m` in European hybrid zones to prevent datum spoofing; supports configuring external `data/geoid/delta_n_eigen6c4_minus_egm2008.tif` (strict=True raises DatumDataError when missing, strict=False assigns NaN);
* **[High-Precision Closed Polygon Masks]** Implemented `matplotlib.path.Path` closed boundary polygons for the Mediterranean and Black Sea, eliminating rectangular BBox misclassification in the Gulf of Cadiz, Portugal, Bay of Biscay, and Red Sea;
* **[Potential Inundation Frequency Analysis & 10m DEM Tool]** Added `compute_inundation_frequency` vectorized complementary empirical cumulative distribution function (CCDF) and released `scripts/calculate_inundation_raster.py` for block-streaming 10m/30m coastal DEMs into annual potential astronomical inundation GeoTIFFs;
* **[GUI Performance & Big Data Safety]** Capped table preview to 2,000 rows while preserving 100% full dataset export for CSV/Excel; added chart decimation and adaptive peak/trough text labeling thresholds for responsive navigation across 10,000+ points;
* **[Generalized ΔN Generation Script]** Upgraded `scripts/generate_delta_n.py` to support arbitrary reference models (GOCO06s / EIGEN-6C4) with strict spatial alignment checks and GeoTIFF tag metadata;
* **[Comprehensive Test Suite Upgrade]** Expanded automated testing to **22/22 passing tests**, validating full FES2022b mesh operations, 2024 leap year 17,568-point fidelity, target-aware datum resolution, and DST boundary safety.

### v1.2 (2026-09)
* **[UI Adaptive Resizing]** Enclosed the left control panel in a `QScrollArea` to remove vertical resizing limits on 768p/1080p and high-DPI displays;
* **[MSL Mode Decoupling]** Removed mandatory MDT/Geoid dependencies when running pure tide MSL predictions;
* **[Regional Geoid Recognition]** Automatically identifies the EIGEN-6C4 ($d/o=2190$) reference geoid used by Hybrid MDT in Mediterranean and Black Sea domains;
* **[Dynamic Timezone Switching]** Instantly synchronizes chart time axes and result tables upon changing timezone dropdown without re-running calculations;
* **[DST Transition Safety]** Resolved potential ambiguous time issues during daylight saving time transitions;
* **[Sampling Interval Guidelines]** Integrated international operational benchmarks (NOAA 6-min, IOC/GLOSS 10-15min, Foreman 1h) directly in GUI and docs;
* **[Dependency Compatibility]** Resolved Affine matrix multiplication deprecation warning and expanded automated tests to 14/14 passing.

### v1.1 (2026-09)
* Corrected vertical datum conversion to EGM2008 by introducing $\Delta N = N_{\text{GOCO06S}} - N_{\text{EGM2008}}$ geoid difference correction;
* Optimized single-point BBox caching and global batch adaptive spatial chunking;
* Introduced true bilinear interpolation and strict inland `NaN` propagation;
* Added automatic astronomical high/low tide peak detection and statistics cards.

---

## 📚 Citations & Acknowledgements

1. **FES2022b Tide Model**:
   > *"The FES2022 Tide product was funded by CNES, produced by LEGOS, NOVELTIS and CLS and made freely available by AVISO."* (DOI: `10.24400/527896/a01-2024.004`)
2. **CNES-CLS22 MDT**:
   > *"The Mean Dynamic Topography CNES-CLS22 was produced by CLS and CNES."* (DOI: `10.24400/527896/a01-2023.003`)
3. **GOCO06s Satellite Gravity Field**:
   > Kvas, A., et al. (2021). GOCO06s - a satellite-only global gravity field model. *International Centre for Global Earth Models (ICGEM)*, GFZ Potsdam. (DOI: `10.5880/ICGEM.2021.002`)
4. **EGM2008 Geoid**:
   > Pavlis, N. K., Holmes, S. A., Kenyon, S. C., & Factor, J. K. (2012). The development and evaluation of the Earth Gravitational Model 2008 (EGM2008). *Journal of Geophysical Research: Solid Earth*, 117(B4).
