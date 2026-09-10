# CoastTideX: High-Precision Global Coastal Tide Simulation & Vertical Datum System

<p align="center">
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

## ✨ Key Features

* 🌊 **FES2022b Native Non-Structured Mesh**: Directly loads the 3.77 GB native triangular mesh, providing ultimate fidelity in coastal zones with 34 diurnal, semi-diurnal, shallow-water non-linear, and long-period constituents.
* ⚡ **Adaptive Spatial Bounding-Box Caching**: Automatically bounds the region of interest around input coordinates, indexing only local topology in memory for sub-second query speeds and minimal RAM overhead.
* 📐 **Rigorous Dual Vertical Datum Pipeline**:
  * **MSL Datum**: Instantaneous tidal oscillation relative to local Mean Sea Level.
  * **EGM2008 Datum**: Fused with the CNES-CLS22 MDT model to output geodetic orthometric heights, immediately compatible with terrestrial LiDAR, drone surveys, and Copernicus DEMs for coastal inundation analysis.
* 🖥️ **Modern Desktop GUI (PyQt6)**:
  * One-click presets for major world estuaries and ports (Yangtze, Pearl River, Hangzhou Bay, Bohai, Rotterdam, New York, San Francisco, Sydney, etc.);
  * Interactive Matplotlib canvas with pan/zoom and **automatic peak (high tide) and trough (low tide) detection & annotations**;
  * Asynchronous multithreaded calculation (`QThread`) keeping the interface responsive at all times.
* 📑 **Batch File Processing & Export**: Effortlessly processes tabular CSV files containing thousands of discrete coordinate and timestamp records, with one-click export to standard CSV or Excel format.
* 📦 **Standalone Executable (.exe) Readiness**: Launch via `run_gui.bat` or compile into a standalone Windows `.exe` application via `build_exe.bat`.

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
   ┌─────────┴─────────┐                                       ┌─────────┴─────────┐
   ▼                   ▼                                       ▼                   ▼
FES2022b Native Mesh   Spatial BBox Index                 CNES-CLS22 MDT     EGM2008 GeoTIFF
(5.69M nodes / 34)     (Sub-sec / Low RAM)                (Dynamic Setup)    (Geoid Undulation N)
             │                                                           │
             └─────────────────────────────┬─────────────────────────────┘
                                           ▼
                       ┌───────────────────────────────────────┐
                       │  Multi-Datum Tide Heights (MSL / EGM) │
                       │    Interactive Charts / CSV / XLSX    │
                       └───────────────────────────────────────┘
```

---

## 📐 Scientific Formulation

### 1. Harmonic Tidal Synthesis
The instantaneous sea surface height $\eta(t)$ at any coordinate $(\lambda, \phi)$ is calculated as the superposition of 34 constituents:

$$\eta(t) = \sum_{i=1}^{34} f_i(t) \cdot H_i(\lambda, \phi) \cdot \cos\left( \omega_i t + V_{0,i}(t_0) + u_i(t) - g_i(\lambda, \phi) \right) + h_{\text{LP}}(t)$$

* $H_i, g_i$: Modeled amplitude and Greenwich phase lag from the FES2022b mesh;
* $f_i(t), u_i(t)$: Nodal modulation factors covering the 18.61-year lunar nodal cycle;
* $h_{\text{LP}}(t)$: Equilibrium long-period tide.

### 2. Vertical Datum Transformation to EGM2008
Since FES tide anomalies are referenced to the local Mean Sea Level (MSL), converting to the EGM2008 geoid requires adding the Mean Dynamic Topography (MDT):

$$H_{\text{EGM2008}}(\lambda, \phi, t) = \text{MDT}(\lambda, \phi) + \eta_{\text{tide}}(\lambda, \phi, t)$$

* Conversion to WGS84 geometric ellipsoidal height $h_{\text{WGS84}}$:
  $$h_{\text{WGS84}} = H_{\text{EGM2008}} + N_{\text{EGM2008}}(\lambda, \phi)$$
  (where $N_{\text{EGM2008}}$ is sampled directly from the bundled `us_nga_egm08_25.tif` raster).

---

## 🚀 Quick Start

### 1. Environment Setup
Run `setup_env.bat` in the project root to automatically create the dedicated Python 3.11 `.venv` and install all prerequisites.

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
Automate predictions using `cli.py`:
* **Single Location Time-Series**:
  ```bash
  python cli.py single --lon 122.0 --lat 31.0 --start "2026-09-10 00:00:00" --end "2026-09-11 00:00:00" --step 1h --output output.csv
  ```
* **Batch Tabular Processing**:
  ```bash
  python cli.py batch --input points.csv --lon-col longitude --lat-col latitude --time-col datetime --output batch_out.csv
  ```

### 4. Python API Usage
```python
from core.tide_engine import FESTidePredictor
from core.datum_engine import DatumTransformer

predictor = FESTidePredictor()
transformer = DatumTransformer()

# 24-hour tidal simulation
df = predictor.predict_series(
    lon=122.0, lat=31.0,
    start_time="2026-09-10 00:00:00",
    end_time="2026-09-11 00:00:00",
    freq="1h",
    constituents="all"
)

# Convert to EGM2008
egm_tide, mdt = transformer.convert_msl_to_egm2008(df['tide_total_m'].values, 122.0, 31.0)
df['h_egm2008_m'] = egm_tide
print(df[['datetime', 'tide_total_m', 'h_egm2008_m']].head())
```

---

## 📦 Packaging to Executable (.exe)

A ready-to-use PyInstaller configuration is provided in `build_exe.bat`:
1. Run `build_exe.bat`;
2. Find the standalone application in `dist/CoastTideX/CoastTideX.exe`.

---

## 📚 Citations & Acknowledgements

1. **FES2022b Tide Model**:
   > *"The FES2022 Tide product was funded by CNES, produced by LEGOS, NOVELTIS and CLS and made freely available by AVISO."* (DOI: `10.24400/527896/a01-2024.004`)
2. **CNES-CLS22 MDT**:
   > *"The Mean Dynamic Topography CNES-CLS22 was produced by CLS and CNES."* (DOI: `10.24400/527896/a01-2023.003`)
3. **EGM2008 Geoid**:
   > Pavlis, N. K., Holmes, S. A., Kenyon, S. C., & Factor, J. K. (2012). The development and evaluation of the Earth Gravitational Model 2008 (EGM2008). *Journal of Geophysical Research: Solid Earth*, 117(B4).
