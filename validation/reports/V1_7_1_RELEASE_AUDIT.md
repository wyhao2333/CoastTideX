# CoastTideX v1.7.1 Clean Re-Release Audit Report

本报告记录 CoastTideX v1.7.1 正式发布主分支清理、旧 Release/Tag 重新发布、自动化验证、Annotated Tag 以及 GitHub Release 溯源信息。

---

## 1. 重新发布概述与时点 (Clean Re-Release Provenance)

- **Re-Release Date**: 2026-09-29
- **Main Release Commit SHA**: `da4f538366e67e00143130b862cb215bbe77598e`
- **Main Cleanup CI Run ID**: `36530770234`
- **Main Cleanup CI Result**: `completed success` (Workflow: CoastTideX CI, Duration: 1m5s)
- **Previous Release / Tag Handling**:
  - 旧 `v1.7.1` Release 及远端/本地 Tag 在正式外部发布安装包之前已完整删除并重新创建；
  - 旧 Release 目标 Commit (`8d7df9b...`) 留存作为 Git 历史，未做破坏性重写。

---

## 2. 自动化测试套件结果

### Local (Windows, Python 3.11 .venv, PyQt6 Full Environment)
- **Total Tests**: 332
- **Ran**: 332
- **Failures**: 0
- **Errors**: 0
- **Skipped**: 0
- **Status**: `OK` (60.269s)

### GitHub Actions (Ubuntu Linux, Headless Environment)
- **Total Tests**: 332
- **Ran**: 332
- **Failures**: 0
- **Errors**: 0
- **Skipped**: 7 (PyQt6/Offscreen & non-Linux C-extensions guarded by `@unittest.skipUnless`)
- **Status**: `OK (skipped=7)` (Run ID: `36530770234`)

---

## 3. 仓库卫生与合规性 (Repository Hygiene)

- **`GEMINI.md` tracked in Git**: NO (移出版本库索引，仅本地物理留存)
- **`AGENT.md` tracked in Git**: NO
- **`AGENTS.md` tracked in Git**: NO
- **`CLAUDE.md` / `CODEX.md` tracked in Git**: NO
- **`.agents/` directory tracked in Git**: NO (从 Git 索引彻底移除，仅本地保留)
- **`.gitignore` 包含 AI/Agent 屏蔽保护**: PASS (包含 `AGENTS.md`, `AGENT.md`, `GEMINI.md`, `.agents/`)
- **Repository Hygiene 自动化测试强化**: PASS (`tests/test_v171_release_text_consistency.py` 增强路径与目录检查)
- **Git 历史重写声明**:
  > `.agents/` is absent from the current main tree and the recreated v1.7.1 release tree. Historical commits were intentionally not rewritten.
- **Force push during clean re-release**: NO (严禁且未使用 force push)
- **Protected files ZERO DIFF**: PASS (`requirements.txt`, `predict_tide.py`, `run_gui.bat`, `setup_env.bat`, `build_exe.bat`, `app.py`)

---

## 4. Git Annotated Tag

- **Tag**: `v1.7.1`
- **Tag Target Commit SHA**: `da4f538366e67e00143130b862cb215bbe77598e`
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

- **Scientific algorithms changed during cleanup**: NO
- **GUI functionality changed**: NO
- **CLI functionality changed**: NO
- **Datum conversion arithmetic preserved**:
  $$Z_{\mathrm{MSL}} = Z_{\mathrm{EGM2008}} - \mathrm{MDT} - \Delta N$$
- **Default MDT extrapolation limit**: 100.0 km (configurable 0.0 - 500.0 km)
- **Exposure calculation and QC bitmask constants**: 100% preserved
