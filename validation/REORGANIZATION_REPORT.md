# CoastTideX 本地项目目录安全分区整理报告
## (CoastTideX Repository Reorganization — SAFE MODE Final Report)

> **整理执行时间**: 2026-09-24  
> **整理工作区**: `I:\Test_tide_model`  
> **执行准则**: MOVE, DO NOT DELETE | ZERO DELETION | ZERO OVERWRITE | ZERO DESTRUCTIVE ACTION

---

## 一、核心原则与十六大合规指标核验

| 序号 | 审查项目 | 判定结果 | 说明与证据 |
| :---: | :--- | :---: | :--- |
| **1** | **production root 是否保持不变** | **YES (是)** | 项目根目录、运行环境及核心锚点绝对未移动，仍然位于 `I:\Test_tide_model`。 |
| **2** | **tests/ 是否保持原位** | **YES (是)** | `I:\Test_tide_model\tests` 保持根目录绝对原位，与 GitHub Actions CI 保持 100% 对齐。 |
| **3** | **哪些 scripts 被移动** | **已列出** | 共 5 个验证/基准/历史脚本安全迁入 `validation/scripts/`（见下节清单）。 |
| **4** | **哪些 scripts 因引用关系没有移动** | **已列出** | 6 个脚本因生产工具链、CLI、文档或回归测试套件直接 import 依赖而保留在 `scripts/` 原位。 |
| **5** | **哪些 results/artifacts 被移动** | **已列出** | `tmp/chongming_verify/`、`outputs/chongming_diagnosis/`、`tmp/v1_5_beta_real_fes/`、`tmp_debug/` 及 `empirical_fes_exposure_oracle_results.json` 完整迁入 `validation/artifacts/` 与 `validation/scratch/`。 |
| **6** | **哪些 docs 被移动** | **已列出** | 4 篇崇明岛专项验证技术报告安全迁入 `validation/reports/chongming/`。 |
| **7** | **哪些 docs 因引用复杂保持原位** | **已列出** | `DEVELOPMENT_GUIDE.md` 及全部 v1.5/v1.6 审计文档因被 `README.md`、`CHANGELOG.md` 及多个对齐测试用例引用，采取保守策略保留在 `docs/` 原位。 |
| **8** | **所有大文件 SHA256 是否一致** | **YES (是)** | 所有 `.nc`、`.tif`、`.tiff` 等二进制与文本大文件在移动前后完成逐文件校验，`before SHA256 == after SHA256` 100% 吻合。 |
| **9** | **production diff 是否为 0** | **YES (是)** | 执行 `git diff -- core gui app.py cli.py config.yaml` 严格为 0，零改动。 |
| **10** | **unit tests 是否通过** | **YES (是)** | `python -m unittest discover -s tests -p "test_*.py"` 完整运行，**246 tests 全部通过 (OK)**。 |
| **11** | **app import 是否成功** | **YES (是)** | `python -c "import app; print('APP_IMPORT_OK')"` 运行无报错，打印 `APP_IMPORT_OK`。 |
| **12** | **CLI 是否成功** | **YES (是)** | `python cli.py --help` 成功打印标准帮助文档，入口完全正常。 |
| **13** | **AGENTS.md 是否仍是本地未跟踪状态** | **YES (是)** | `Test-Path AGENTS.md` 为 `True`，且 `git ls-files` 严格无输出，本地保留且不被 Git 跟踪。 |
| **14** | **是否执行过删除** | **NO (否)** | **绝对没有执行过任何删除操作**（无 `rm`、无 `Remove-Item`、无 `del`、无 `git clean`）。 |
| **15** | **是否执行过 git push** | **NO (否)** | **绝对没有执行过任何 `git push`**。 |
| **16** | **是否执行过 commit** | **NO (否)** | **绝对没有执行过任何 `git commit`**，全部变动保持在工作区/暂存区。 |

---

## 二、脚本与文档迁移及保留清单

### 1. 成功迁移的脚本清单 (Moved Scripts)
- `scripts/benchmark_clean_performance.py` $\rightarrow$ `validation/scripts/benchmarks/benchmark_clean_performance.py`
- `scripts/benchmark_mdt_edge_holdout.py` $\rightarrow$ `validation/scripts/benchmarks/benchmark_mdt_edge_holdout.py`
- `scripts/verify_evidence_closure.py` $\rightarrow$ `validation/scripts/diagnostics/verify_evidence_closure.py`
- `scripts/run_empirical_fes_oracle.py` $\rightarrow$ `validation/scripts/legacy/run_empirical_fes_oracle.py`
- `scripts/validate_raster_engine.py` $\rightarrow$ `validation/scripts/legacy/validate_raster_engine.py`

*(上述脚本已配置动态工程根目录查找逻辑 `find_project_root()`，并完成 `python -m py_compile` 语法校验，可独立在任意目录下正常调用运行)*

### 2. 因引用依赖保留在原位的脚本清单 (Scripts Left In Place)
- `scripts/calculate_inundation_raster.py`: 生产工具链核心 CLI 辅助工具，被 `cli.py`、`core/raster_engine.py`、`gui/main_window.py` 及回归测试直接调用。
- `scripts/generate_delta_n.py`: 生产工具链大地水准面生成脚本，被 `config.yaml`、`README.md`、`gui/settings_dialog.py` 及数据文档多处引用。
- `scripts/diagnose_chongming_verification.py`: 被回归测试 `tests/test_chongming_verification_final.py` 直接模块级 import。
- `scripts/diagnose_coastal_datum_support.py`: 被测试套件 `tests/test_chongming_datum_support_diagnostics.py` 与 `tests/test_performance_instrumentation.py` 直接 import。
- `scripts/validate_real_fes_raster.py`: 被核心自动化回归测试 `tests/test_engines.py` 直接 import。
- `scripts/validate_v15_beta_real_fes.py`: 被自动化回归测试 `tests/test_v15_beta_validation_harness.py` 直接 import 并执行单元测试 Mock Patch。

### 3. 文档迁移与保留清单 (Docs Reorganization)
- **迁移至 `validation/reports/chongming/` 的崇明岛专项验证技术报告**（外部 0 引用）：
  - `docs/CHONGMING_DATUM_PERFORMANCE_VERIFICATION_FINAL.md`
  - `docs/CHONGMING_DATUM_SUPPORT_AND_GLOBAL_SCALING_AUDIT.md`
  - `docs/CHONGMING_FINAL_EVIDENCE_CLOSURE.md`
  - `docs/CHONGMING_FINAL_TWO_GATE_VERIFICATION.md`
- **保留在 `docs/` 原位的正式文档与审计报告**：
  - `docs/DEVELOPMENT_GUIDE.md`: 正式开发者指南。
  - `docs/FES_MASK_METADATA_AUDIT.md`、`docs/V1_5_BETA_REAL_FES_VALIDATION.md`、`docs/V1_6_EXPOSURE_HARDENING_AUDIT.md` 等：由于被 `README.md`、`CHANGELOG.md` 及 `tests/test_v16_round8_release_candidate.py` 等用例作为文档一致性对齐检查的目标路径，依保守原则保留原位，确保 CI 与测试 100% 兼容。

---

## 三、验证产物与调试工作区安全迁移 (Artifacts & Scratch)

- `tmp/chongming_verify/*` $\rightarrow$ `validation/artifacts/chongming_verify/`（包含基准测试数据表、栅格与缓存）
- `outputs/chongming_diagnosis/*` $\rightarrow$ `validation/artifacts/chongming_diagnosis/`
- `tmp/v1_5_beta_real_fes/*` $\rightarrow$ `validation/artifacts/v1_5_beta_real_fes/`
- `tmp_debug/*` $\rightarrow$ `validation/scratch/tmp_debug/`
- `outputs/empirical_fes_exposure_oracle_results.json` $\rightarrow$ `validation/artifacts/empirical_fes_exposure_oracle_results.json`
- **原目录保护**: `tmp/`、`outputs/`、`tmp_debug/` 目录结构仍然完好保留，未做任何删除。
- **.gitignore 优化**: 追加了 `validation/artifacts/` 与 `validation/scratch/`，确保大体积与本地实验产物不侵入 Git 跟踪，同时 `validation/scripts/` 与 `validation/reports/` 保持受控。

---

## 四、重构后项目目录树 (3 层概览)

```text
I:\Test_tide_model
├── .agents
│   └── rules
│       └── project_rules.md
├── .github
│   └── workflows
│       └── ci.yml
├── .gitignore
├── AGENTS.md                                        # 本地保留 (未被 Git 跟踪)
├── CHANGELOG.md
├── GEMINI.md
├── LICENSE
├── README.md
├── README_EN.md
├── app.py                                           # GUI 主入口
├── build_exe.bat
├── cli.py                                           # CLI 主入口
├── config.yaml                                      # 正式配置文件
├── core                                             # 【核心生产算法引擎】
│   ├── __init__.py
│   ├── batch_raster_engine.py
│   ├── datum_engine.py
│   ├── exposure_engine.py
│   ├── raster_engine.py
│   ├── tide_cache.py
│   ├── tide_engine.py
│   └── utils.py
├── data                                             # 【大地水准面高程异常数据】
│   └── geoid
│       ├── README_GEOID.md
│       ├── README_GEOID_EN.md
│       ├── delta_n_eigen6c4_minus_egm2008.tif
│       ├── delta_n_goco06s_minus_egm2008.tif
│       ├── height_anomaly_ell_*.tiff
│       └── us_nga_egm08_25.tif
├── docs                                             # 【开发指南与版本审计】
│   ├── DEVELOPMENT_GUIDE.md
│   └── ... (12 篇版本发布与测试对齐审计报告)
├── fes2022b                                         # 【FES2022b 模型库】
│   ├── load_tide (34+ NetCDF)
│   ├── mask_fes2022B.nc
│   ├── ocean_tide_20241025 (34+ NetCDF)
│   ├── ocean_tide_extrapolated (35+ NetCDF)
│   └── ocean_tide_non_structured
│       └── FES2022b_OceanTide_NSgrid.nc
├── gui                                              # 【桌面 GUI 模块】
│   ├── __init__.py
│   ├── chart_widget.py
│   ├── main_window.py
│   ├── manual_dialog.py
│   ├── settings_dialog.py
│   └── styles.py
├── mdt_cls22                                        # 【全球 MDT 数据库】
│   └── mdt_hybrid_cnes_cls22_cmems2020_global.nc
├── outputs                                          # 【输出目录 (保留原位)】
│   └── chongming_diagnosis
├── predict_tide.py
├── predicted_tide_example.csv
├── requirements.txt
├── run_gui.bat
├── scripts                                          # 【生产工具链与测试依赖脚本】
│   ├── calculate_inundation_raster.py
│   ├── diagnose_chongming_verification.py
│   ├── diagnose_coastal_datum_support.py
│   ├── generate_delta_n.py
│   ├── validate_real_fes_raster.py
│   └── validate_v15_beta_real_fes.py
├── setup_env.bat
├── tests                                            # 【正式自动化 CI 回归测试套件】
│   ├── __init__.py
│   └── ... (16 个自动化测试文件，246 个用例全部 PASS)
├── tmp                                              # 【临时目录 (保留原位)】
│   ├── chongming_verify
│   └── v1_5_beta_real_fes
├── tmp_debug                                        # 【调试目录 (保留原位)】
└── validation                                       # 【新建：科研验证与基准评测专区】
    ├── README.md                                    # 验证专区规范说明
    ├── reorganization_reference_audit.csv           # 迁移前全仓库引用依赖审计
    ├── reorganization_manifest_before.csv           # 迁移前物料清单与哈希
    ├── reorganization_manifest_after.csv            # 迁移后哈希完全吻合清册
    ├── artifacts                                    # 验证成果物 (由 .gitignore 忽略)
    │   ├── chongming_diagnosis
    │   ├── chongming_verify
    │   ├── empirical_fes_exposure_oracle_results.json
    │   └── v1_5_beta_real_fes
    ├── reports                                      # 专项技术验证报告
    │   ├── chongming                                # 4 篇崇明岛专项验证技术报告
    │   ├── v1_5
    │   └── v1_6
    ├── scratch                                      # 临时调试工作区 (由 .gitignore 忽略)
    │   └── tmp_debug
    └── scripts                                      # 验证与评测专用脚本
        ├── benchmarks                               # ParentBBox 与 MDT 外推基准评测
        ├── diagnostics                              # 证据闭环脚本
        └── legacy                                   # 历史解析预言机与验证脚本
```

---
*(整理完毕，未执行任何删除或覆盖，未执行任何 git push 或 commit)*
