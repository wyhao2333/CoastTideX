# CoastTideX Validation Workspace

本目录专用于 CoastTideX 项目的科学验证、性能基准评测与科研审计材料归档，只包含：
- benchmark（性能基准评测）
- diagnostic（科学缺陷诊断与证据核验）
- legacy（历史开发期验证预言机与测试套件）
- scientific audit（科研审计与技术验证报告）
- local validation artifacts（本地验证产物）
- scratch outputs（临时调试工作区）

## 项目代码库边界与分区说明
- **正式生产源码**: 严格位于项目根目录下的 `core/`、`gui/`、`app.py`、`cli.py`、`predict_tide.py`。
- **正式自动化回归测试 (CI Tests)**: 严格位于 `tests/`，直接对接 GitHub Actions 自动化测试流。
- **正式生产工具链脚本**: 严格位于 `scripts/`（如 `calculate_inundation_raster.py`、`generate_delta_n.py` 等）。
- **验证与科研材料**: 集中归档于本 `validation/` 目录。

## 目录结构
```text
validation/
├── README.md                           # 本说明文档
├── reorganization_reference_audit.csv  # 迁移前全仓库引用依赖审计清单
├── reorganization_manifest_before.csv  # 迁移前物料清册 (含初始 SHA256 与文件大小)
├── reorganization_manifest_after.csv   # 迁移后物料校验清册 (100% SHA256 吻合验证)
├── scripts/                            # 验证与测试脚本
│   ├── benchmarks/                     # 性能基准测试脚本 (如 ParentBBox 性能评测)
│   ├── diagnostics/                    # 科学诊断与证据闭环脚本
│   └── legacy/                         # 历史版本验证与预言机脚本
├── reports/                            # 专项科研验证与技术审计报告
│   ├── chongming/                      # 崇明岛专项验证与基准支撑诊断报告
│   ├── v1_5/                           # v1.5 阶段归档报告预留区
│   └── v1_6/                           # v1.6 阶段归档报告预留区
├── artifacts/                          # 本地验证产物 (默认不入 Git 仓库，由 .gitignore 忽略)
│   ├── chongming_verify/               # 崇明岛验证产物 (包含基准测试结果、对照栅格、NC 缓存)
│   ├── chongming_diagnosis/            # 崇明岛控制节点归因与空间分布图
│   └── v1_5_beta_real_fes/             # 历史 Beta 阶段验证数据
└── scratch/                            # 临时调试脚本与小样数据 (由 .gitignore 忽略)
    └── tmp_debug/                      # 临时调试 DEM 与潮位缓存
```

## 注意事项
- `validation/artifacts/` 与 `validation/scratch/` 属于本地运行与科研试验产物，默认不属于软件正式发行包。
- 严禁将 `validation/` 下的非生产模块直接引入 `core/`、`gui/`、`app.py` 或 `cli.py`。
