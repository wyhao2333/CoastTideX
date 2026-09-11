# CoastTideX: High-Precision Global Coastal Tide Simulation & Vertical Datum System

<p align="center">
  <img src="https://img.shields.io/badge/Release-v1.2-blue.svg" alt="Release v1.2">
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

---

### ✨ Key Features

* 🌊 **FES2022b Native Non-Structured Mesh**: Directly loads the 3.77 GB native triangular mesh, providing ultimate fidelity in coastal zones with 34 diurnal, semi-diurnal, shallow-water non-linear, and long-period constituents.
* ⚡ **Adaptive Spatial Chunking & Local BBox Caching**:
  * **Single Station Mode**: Automatically bounds the region of interest around input coordinates, indexing only local topology in memory for sub-second query speeds and minimal RAM footprint (~1.2 GB);
  * **Global Discrete Batch Mode**: Employs $5^\circ \times 5^\circ$ adaptive spatial mesh chunking, preventing memory blowup when processing scattered worldwide points.
* 📐 **Rigorous Four-Tier Vertical Datum Pipeline**:
  * **MSL Datum**: Instantaneous tidal oscillation relative to local Mean Sea Level;
  * **GOCO06s Datum**: Relative to the raw CNES-CLS22 MDT reference geoid ($H_{\text{GOCO06S}} = \text{Tide} + \text{MDT}$);
  * **EGM2008 Orthometric Datum**: Rigorously calibrated with the geoid difference correction term $\Delta N = N_{\text{GOCO06S}} - N_{\text{EGM2008}}$ ($H_{\text{EGM2008}} = \text{Tide} + \text{MDT} + \Delta N$);
  * **WGS84 Ellipsoidal Datum**: 3D geometric ellipsoidal height ($h_{\text{WGS84}} = H_{\text{EGM2008}} + N_{\text{EGM2008}}$), immediately compatible with GNSS/RTK observations;
  * **Mediterranean & Black Sea Geoid Datum Identification (v1.2)**: Automatically distinguishes the regional EIGEN-6C4 ($d/o=2190$) geoid datum employed by Hybrid MDT in the Mediterranean and Black Sea.
* 🎯 **True Bilinear Spatial Interpolation & Strict NaN Propagation**:
  * Applies true bilinear interpolation (`map_coordinates(order=1)`) to EGM2008 and $\Delta N$ GeoTIFFs;
  * Inland land points or queries outside valid ocean domain strictly propagate `NaN`—never faking `0.0`.
* 🖥️ **Modern Desktop GUI (PyQt6) & Enhancements (v1.2)**:
  * **Adaptive Scrolling & Arbitrary Window Resizing**: The control panel is enclosed in a `QScrollArea`, completely removing vertical resizing limits for 768p/1080p and high-DPI displays;
  * **Decoupled Pure MSL Mode**: Enables instant tidal predictions without requiring external MDT or Geoid rasters;
  * **Dynamic Timezone Switching**: Instantaneously re-indexes chart axes and tabular data when switching timezone without re-running calculations;
  * **DST Boundary Safety**: Handles daylight saving time transitions smoothly without NaT drops;
  * One-click presets for major world estuaries and ports (Yangtze, Pearl River, Hangzhou Bay, Bohai, Rotterdam, New York, San Francisco, Sydney, etc.);
  * Interactive Matplotlib canvas with pan/zoom and **automatic peak & trough detection calibrated to semi-diurnal physical windows (~10-12h)**;
  * Multi-datum dynamic curve overlay, real-time statistical cards, and dual timezone support (UTC / Local Time);
  * **Integrated User Manual Dialog**: Access complete documentation directly via `Help -> 📖 User Manual & Documentation`.
* 📑 **Batch File Processing & Vectorized Export**: High-throughput vectorized resolution for large tabular CSV files with export to CSV or Excel.
* 📦 **Standalone Executable (.exe) Readiness**: Launch via `run_gui.bat` or compile into a standalone Windows `.exe` application via `build_exe.bat`.

---

## 💻 System & Hardware Requirements

CoastTideX is engineered with adaptive spatial indexing to maintain high performance across diverse hardware configurations:

| Usage Scenario | Minimum RAM | Recommended RAM | Compute & Storage | Details |
| :--- | :---: | :---: | :--- | :--- |
| **Single Location Time-Series**<br>*(Single Point Mode)* | **4 GB** | **8 GB** | Dual-core CPU or better<br>Free Disk Space ≥ 10 GB | Local BBox caching loads only topology around the target location; resident memory is only ~1.2 GB. |
| **Local Regional Batch**<br>*(≤ 8° Geographic Span)* | **4 GB** | **8 GB** | Quad-core CPU or better<br>Free Disk Space ≥ 10 GB | Small regional point clusters are solved in a single bounding box with minimal overhead. |
| **Global Discrete Batch**<br>*(Worldwide Scattered Points)* | **8 GB** | **16 GB** | Quad- to Octa-core CPU<br>High-speed NVMe SSD | $5^\circ \times 5^\circ$ adaptive spatial chunking processes points in clusters, bounding peak memory. |
| **Full Unconstrained Global Grid**<br>*(All-Mesh Global Loading)* | **16 GB** | **32 GB** | Octa-core CPU or better<br>High-speed NVMe SSD | Loading all 5.69 million nodes and 34 constituents simultaneously requires ~6–8 GB of contiguous RAM. |

* **Supported Operating Systems**: Windows 10/11 64-bit, Ubuntu 20.04+, macOS (x86_64 / Apple Silicon via Rosetta 2).
* **Python Runtime**: Python 3.11.

---

## 🏛️ System Architecture

```text
                                ┌────────────────────────┐
                                │   CoastTideX (GUI/CLI) │
                                └───────────┬────────────┘
                                            │
             ┌─────────────────────────────┴─────────────────────────────┐
             ▼                                                           ▼
  ┌───────────────────────┐                                   ┌───────────────────────┐
  │   Tide Engine Core    │                                   │   Datum Engine Core   │
  └──────────┬────────────┘                                   └──────────┬────────────┘
             │                                                           │
   ┌─────────┴─────────┐                                ┌────────┬───────┴────────┬────────┐
   ▼                   ▼                                ▼        ▼                ▼        ▼
FES2022b Native Mesh   Spatial BBox Index            CNES-CLS22 MDT    ΔN GeoTIFF     EGM2008 GeoTIFF
(5.69M nodes / 34)     (Sub-sec / Spatial Chunking)   (GOCO06s Geoid)  (GOCO - EGM)    (Undulation N)
             │                                                           │
             └─────────────────────────────┬─────────────────────────────┘
                                           ▼
                       ┌───────────────────────────────────────┐
                       │  Unified Four-Tier Vertical Datums    │
                       │  (MSL / GOCO06s / EGM2008 / WGS84)    │
                       │    Interactive Charts / CSV / XLSX    │
                       └───────────────────────────────────────┘
```

---

## 📐 Scientific Formulation

### 1. Harmonic Tidal Synthesis
The instantaneous sea surface height $\eta(t)$ at any coordinate $(\lambda, \phi)$ is calculated as the superposition of 34 constituents:

$$\eta(t) = \sum_{i=1}^{34} f_i(t) \cdot H_i(\lambda, \phi) \cdot \cos\left( \omega_i t + V_{0,i}(t_0) + u_i(t) - g_i(\lambda, \phi) \right) + h_{\text{LP}}(t)$$

* $H_i, g_i$: Modeled amplitude and Greenwich phase lag from the FES2022b finite-element mesh;
* $f_i(t), u_i(t)$: Nodal modulation factors covering the 18.61-year lunar nodal cycle;
* $h_{\text{LP}}(t)$: Equilibrium long-period tide.

### 2. Rigorous Four-Tier Vertical Datum Pipeline
In simplified workflows, practitioners often equate $\text{Tide} + \text{MDT}$ directly to EGM2008 height. **This is scientifically inaccurate**.
The CNES-CLS22 MDT model is computed with respect to the **GOCO06s satellite gravity geoid**, which differs globally from the **EGM2008 geoid** by $-6.63\text{m} \sim +6.79\text{m}$ (standard deviation $0.34\text{m}$).

CoastTideX resolves this discrepancy through a mathematically rigorous geodetic transformation:

1. **Instantaneous Tide relative to Mean Sea Level (MSL)**:
   $$\text{Tide}_{\text{MSL}}(\lambda, \phi, t) = \eta(t)$$
2. **Sea Surface Height relative to GOCO06s Geoid**:
   $$H_{\text{GOCO06S}}(\lambda, \phi, t) = \text{Tide}_{\text{MSL}}(\lambda, \phi, t) + \text{MDT}_{\text{CLS22}}(\lambda, \phi)$$
3. **Orthometric Height relative to EGM2008 Geoid** (incorporating the geoid difference correction $\Delta N$):
   $$H_{\text{EGM2008}}(\lambda, \phi, t) = H_{\text{GOCO06S}}(\lambda, \phi, t) + \Delta N(\lambda, \phi)$$
   where $\Delta N(\lambda, \phi) = N_{\text{GOCO06S}}(\lambda, \phi) - N_{\text{EGM2008}}(\lambda, \phi)$.
4. **WGS84 3D Geometric Ellipsoidal Height**:
   $$h_{\text{WGS84}}(\lambda, \phi, t) = H_{\text{EGM2008}}(\lambda, \phi, t) + N_{\text{EGM2008}}(\lambda, \phi)$$
   where $N_{\text{EGM2008}}$ is extracted via true bilinear interpolation from the bundled global 2.5' EGM2008 raster (strictly eliminating the 1.25' half-pixel offset).

### 3. Quality Control (QC) Flags
Every prediction point is tagged with an evaluation `quality_flag`:

| Quality Flag | Definition | Scientific Meaning & Processing |
| :---: | :---: | :--- |
| **Flag 1 ~ 6** | Valid Interpolation | The target point falls inside a high-resolution triangular finite element. Full polynomial accuracy. |
| **Flag < 0** | Extrapolated | The target point is near complex coastal shorelines or shallow flats. Extrapolated by tide dynamics. Highlighted in amber. |
| **Flag = 0** | Missing / Inland | Inland point or no tidal solution available. Tide and datums are set to `NaN` (no silent zeros). Highlighted in red. |

### 4. International Standard Tidal Prediction Intervals (Literature Benchmarks)
CoastTideX supports 1min, 5min, 6min, 10min, 15min, 30min, and 1h sampling steps based on authoritative international standards:

| Recommended Step | Standard & Operational Scenario | Scientific Basis & Literature Citations |
| :--- | :--- | :--- |
| **6 minutes (0.1 h)** | **NOAA Operational Tide Gauges & Real-time Predictions** | **NOAA CO-OPS Operational Specification**: The gold standard across US real-time tide gauge networks. High frequency is essential for capturing shallow-water non-linear overtides ($M_4, MS_4, M_6$) and peak turning points. |
| **10 ~ 15 minutes** | **IOC / GLOSS Global Tide Stations** | **UNESCO IOC / GLOSS Specifications**: Standard operational cadence for global sea-level monitoring stations, balancing wave peak fidelity with data volume. |
| **1 hour (60 minutes)** | **Classical Harmonic Analysis & Long-term Sea Level** | **Foreman (1977) & Pawlowicz et al. (2002, T_TIDE)**: The standard input interval for classic harmonic tidal analysis and multi-decadal sea level variation research. |

---


## 🚀 Quick Start

### 1. Environment Setup
Run `setup_env.bat` in the project root to automatically configure the dedicated Python 3.11 `.venv`.

Manual setup:
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Launch GUI
Double-click `run_gui.bat` or run:
```bash
.venv\Scripts\python.exe app.py
```

### 3. Command-Line Interface (CLI)
Automate predictions using `cli.py`, with full support for timezones and all 4 datums:
* **Single Location Time-Series**:
  ```bash
  python cli.py single --lon 122.0 --lat 31.0 --start "2026-09-10 00:00:00" --end "2026-09-11 00:00:00" --step 1h --tz UTC --output output.csv
  ```
* **Batch Tabular Processing**:
  ```bash
  python cli.py batch --input points.csv --lon-col longitude --lat-col latitude --time-col datetime --tz UTC --output batch_out.csv
  ```

### 4. Python API Usage
```python
from core.tide_engine import FESTidePredictor
from core.datum_engine import DatumTransformer

predictor = FESTidePredictor()
transformer = DatumTransformer()

# 1. 24-hour tidal simulation (UTC timezone)
df = predictor.predict_series(
    lon=122.0, lat=31.0,
    start_time="2026-09-10 00:00:00",
    end_time="2026-09-11 00:00:00",
    freq="1h",
    constituents="all",
    source_tz="UTC"
)

# 2. Rigorous vertical datum conversion
datum_res = transformer.convert_tide_datums(
    tide_msl_m=df['tide_total_m'].values,
    lons=122.0,
    lats=31.0
)

df['tide_msl_m'] = datum_res['tide_msl_m']
df['h_goco06s_m'] = datum_res['h_goco06s_m']
df['h_egm2008_m'] = datum_res['h_egm2008_m']
df['h_wgs84_m'] = datum_res['h_wgs84_m']

print(df[['datetime_utc', 'tide_msl_m', 'h_egm2008_m', 'h_wgs84_m']].head())
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
│   └── generate_delta_n.py         # ΔN geoid difference raster generation script
├── data/
│   └── geoid/
│       ├── README_GEOID.md         # Geodetic provenance & ICGEM specifications
│       ├── us_nga_egm08_25.tif     # NGA EGM2008 2.5' global geoid raster (~72.5MB)
│       └── delta_n_goco06s_minus_egm2008.tif # GOCO06s - EGM2008 correction raster (~11.8MB)
├── core/                           # Core computation modules
│   ├── tide_engine.py              # FES2022b tide evaluator & spatial chunking
│   ├── datum_engine.py             # 4-tier vertical datum transformation & bilinear interpolation
│   └── utils.py                    # Presets, coordinates, timezones (DST) & relative paths
├── gui/                            # PyQt6 desktop application
│   ├── main_window.py              # Main window implementation (with QC badges)
│   ├── chart_widget.py             # Matplotlib multi-datum waveform component
│   ├── manual_dialog.py            # Built-in User Manual & Documentation dialog
│   ├── settings_dialog.py          # Data source path configuration dialog
│   └── styles.py                   # High-contrast dark QSS stylesheet
└── tests/                          # Automated unit and integration test suite
    └── test_engines.py             # Decoupled ground truth, closure, DST & portability tests
```

---

## 📦 Packaging to Executable (.exe)

A ready-to-use PyInstaller configuration is provided in `build_exe.bat`:
1. Run `build_exe.bat`;
2. Find the standalone application in `dist/CoastTideX/CoastTideX.exe`.

## 📝 Changelog

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
