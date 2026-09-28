# CoastTideX v1.7.1 Release Audit Report

本报告记录 CoastTideX v1.7.1 正式发布主分支合并、自动化验证、Annotated Tag 以及 GitHub Release 溯源信息。

---

## 1. Main 分支状态

- **Release Merge Target Commit**: `8d7df9b5608531de16e4106f7f0bae408992af9e`
- **Main CI Run ID**: `36454966168`
- **Main CI Result**: `completed success` (Workflow: CoastTideX CI, Duration: 59s)

---

## 2. 自动化测试套件结果

### Local (Windows, Python 3.11 .venv, PyQt6 Full Environment)
- **Total Tests**: 332
- **Ran**: 332
- **Failures**: 0
- **Errors**: 0
- **Skipped**: 0
- **Status**: `OK` (36.500s / 60.422s)

### GitHub Actions (Ubuntu Linux, Headless Environment)
- **Total Tests**: 332
- **Ran**: 332
- **Failures**: 0
- **Errors**: 0
- **Skipped**: 7 (PyQt6/Offscreen & non-Linux C-extensions guarded by `@unittest.skipUnless`)
- **Status**: `OK (skipped=7)` (15.895s)

---

## 3. 仓库卫生与合规性 (Repository Hygiene)

- **`GEMINI.md` tracked in Git**: NO (移出版本库索引，仅本地物理留存)
- **`AGENT.md` tracked in Git**: NO
- **`AGENTS.md` tracked in Git**: NO
- **`CLAUDE.md` / `CODEX.md` tracked in Git**: NO
- **`.gitignore` 包含 AI/Agent 屏蔽保护**: PASS
- **Force push during final integration**: NO (严格采用 fast-forward / standard merge push)
- **Rebase during final integration**: NO (保持历史追溯完整)
- **Protected files ZERO DIFF**: PASS (`requirements.txt`, `predict_tide.py`, `run_gui.bat`, `setup_env.bat`, `build_exe.bat`, `app.py`)

---

## 4. Git Annotated Tag

- **Tag**: `v1.7.1`
- **Tag Target Commit SHA**: `8d7df9b5608531de16e4106f7f0bae408992af9e`
- **Annotated Message**: `CoastTideX v1.7.1`
- **Remote Tag Push Verification**: `refs/tags/v1.7.1` verified on `origin`

---

## 5. GitHub Release

- **Title**: `CoastTideX v1.7.1`
- **Tag**: `v1.7.1`
- **Draft**: NO (`false`)
- **Prerelease**: NO (`false`)
- **URL**: [https://github.com/wyhao2333/CoastTideX/releases/tag/v1.7.1](https://github.com/wyhao2333/CoastTideX/releases/tag/v1.7.1)
- **Release Assets**: Source code (zip, tar.gz)

---

## 6. 科学计算与功能冻结声明 (Scientific Freeze)

- **Scientific algorithms changed during final docs merge**: NO
- **Datum conversion arithmetic preserved**:
  $$Z_{\mathrm{MSL}} = Z_{\mathrm{EGM2008}} - \mathrm{MDT} - \Delta N$$
- **Default MDT extrapolation limit**: 100.0 km (configurable 0.0 - 500.0 km)
- **Exposure calculation and QC bitmask constants**: 100% preserved
