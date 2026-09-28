# CoastTideX v1.7.1 — Text Claim & Scientific Wording Inventory
## 全项目文字声明与科学语义审计清单

**审计日期**: 2026-09-28  
**当前分支**: `docs/v1.7.1-release-text-closure`  
**审计基线 Commit**: `c8851e3906eaf8ec129724df1fc1ea46a644b225` (`main`)  
**审计范围**: 用户可见文档 (`README.md`, `README_EN.md`, `CHANGELOG.md`)、GUI 手册与弹窗 (`gui/manual_dialog.py`, `gui/main_window.py`, `gui/settings_dialog.py`, `gui/__init__.py`)、CLI 接口说明 (`cli.py`)、生产代码注释 (`core/*.py`)、当前数据说明 (`data/geoid/README_GEOID*.md`) 及技术报告 (`docs/V1_7*.md`)。

---

## 1. 声明审计与分类准则

| 分类代码 (Classification) | 准则定义 |
| :--- | :--- |
| **KEEP** | 具备严密数学定义、工程事实或测试直接验证支撑的准确表述（如严格等高边界 $H(t) \le z$、Strict Resume fail-closed、代数等价性等）。 |
| **FIX** | 存在未经验证的绝对化修辞（如“高保真”、“全精度”、“严密海拔正高”、“完全消除”、“防篡改”）、过时版本号（“v1.6 Beta”）、不符实际行为（“t_(N-1) 平推”、“节省 50% 磁盘”）或易引起物理误导的术语（“物理水体连通性”）。 |
| **HISTORICAL_KEEP** | 历史归档报告与历史测试中的原始上下文记录（如 `docs/V1_5_*`, `docs/V1_6_*`, `tests/test_v16_*`），必须保留其历史原貌，严禁机械篡改。 |
| **SUPERSEDED_NOTE** | 具有当前参考价值但包含早期开发阶段暂定参数（如 $k=12$）的历史技术报告（如 `docs/V1_7_GUI_AND_GITHUB_SYNC_AUDIT.md`），在文件顶部附加醒目声明锚定最新事实，不篡改正文。 |

---

## 2. 详细声明清单 (Text Claim Inventory Table)

| File | Line / Section | Original wording | Classification | Action | Reason |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `README.md` | Line 132 | `进行高保真连续几何跨界求交与事件积分` | **FIX** | 改为 `基于相邻采样点的一阶线性跨界插值与连续事件积分` | 跨界求交为离散时步间线性插值，非全物理连续高保真。 |
| `README.md` | Line 149 | `(0 表示高保真解算)` | **FIX** | 改为 `(0 表示未触发当前定义的 Exposure QC / degradation bit; 不代表对真实物理环境的绝对精度保证)` | 澄清 QC=0 仅为质量控制状态码，非绝对精度背书。 |
| `README.md` | Line 190 | `完全向下兼容读取 Schema 1.1...会自动采用 t_(N-1) 终端潮位平推降级并标记 QC_EXP_TERMINAL_UNAVAILABLE` | **FIX** | 改为 `支持符合当前兼容边界的 Schema 1.1 历史缓存读取...未闭合末段区间不纳入有效积分并扣减 valid_time_fraction，置位 QC_EXP_TERMINAL_UNAVAILABLE = 8` | 反映真实代码实现（安全扣减积分，而非平推）。 |
| `README.md` | Line 254 | `- 0: 正常高保真解算 (QC_EXP_VALID)` | **FIX** | 改为 `- 0: 未触发当前定义的 Exposure QC/degradation bit (QC_EXP_VALID)` | 消除“高保真”夸大修辞。 |
| `README.md` | Math formulas | `\text{MDT_REF}`, `\text{..._...}` | **FIX** | 统一替换为 `\mathrm{MDT\,REF}`，关键公式迁移为 fenced ```` ```math ```` 块 | 彻底修复 GitHub Markdown Math 中下划线报错问题。 |
| `README.md` | Section 0 / Intro | `全面通过真实海岸带千万级像元 DEM 端到端科学闭环验证` | **FIX** | 改为客观事实陈述，限定崇明 DEM 与自动化测试范围，声明不构成对全球所有海岸环境的统一物理精度保证 | 避免未经全球广泛实测的泛化推广。 |
| `README.md` | Section 0 | `零 MDT 重复查询与 100% 决策等价性` | **FIX** | 补充限定前提：在相同 MDT/ΔN 与静态偏移条件下，代数判定等价 | 澄清代数等价与空间支撑域边界的关系。 |
| `README_EN.md` | Entire file | Corresponding English claims and formulas | **FIX** | 全面同义同步中文修改，公式、QC=0、Schema 1.1、Topology Heuristic 保持一致 | 保持中英文档科学与技术细节严格等价。 |
| `gui/manual_dialog.py` | Title & Header | `v1.6 Beta` (HTML comments & body) | **FIX** | 更新为 `CoastTideX v1.7.1`，清除无意义 v1.6 Beta 注释与正文 | 手册版本与系统实际发布版本保持一致。 |
| `gui/manual_dialog.py` | Line 45 | `高精度桌面与命令行解算系统` | **FIX** | 改为 `桌面与命令行空间天文潮模拟与垂直基准转换系统` | 剔除未经量化的精度修饰词。 |
| `gui/manual_dialog.py` | Line 49 | `全链路、可追溯、高保真的潮汐动力学模拟...` | **FIX** | 改为 `全链路、可追溯的潮汐动力学模拟...` | 剔除绝对化修辞。 |
| `gui/manual_dialog.py` | Line 83 | `7 大高保真空间栅格产品` | **FIX** | 改为 `7 类 Exposure 空间栅格产品` | 规范科学产品名称。 |
| `gui/manual_dialog.py` | Line 159 | `实现无截断的高保真线性插值，杜绝最后一个时步被虚假丢弃` | **FIX** | 改为说明 Schema 1.2 显式保存 H(t_end) 用于区间闭合，历史缓存缺失则安全扣减有效积分并置位 bit 8 | 澄清 Schema 1.2 真实机制与 Schema 1.1 兼容边界。 |
| `gui/manual_dialog.py` | Section 16 | `传统方式容易因为多重重力场差值产生系统偏差；新方式才能保证物理正确` | **FIX** | 改写为中性科学解释：旧 Tide->EGM 与新 DEM->MSL 在相同条件下代数等价，MSL-first 优势在于前置解耦与复用 | 消除对旧等价公式的不当贬低。 |
| `gui/manual_dialog.py` | Line 331 | `结合潮间带工程的高精度要求` | **FIX** | 改为 `根据 CoastTideX 当前应用范围与工程设计采用默认 100 km (允许配置 0~500 km)` | 明确 100 km 为系统应用默认值，非 Nature 证明的最佳值。 |
| `gui/manual_dialog.py` | Line 348 | `减少 50% 磁盘占用与大量 I/O 开销` | **FIX** | 改为 `默认不落盘额外 QC GeoTIFF，从而减少额外磁盘占用与写入 I/O；实际节省比例取决于栅格内容与压缩率` | 消除未经全局实测的“50%”定量宣传。 |
| `gui/manual_dialog.py` | Line 355 | `即可直接展开高精度潜在淹没与露出分析` | **FIX** | 改为 `即可执行潜在天文潮淹没频率或 Exposure 分析` | 规范科学功能表述。 |
| `gui/manual_dialog.py` | Section 9 | `Target-Mask-Derived Topology Guard` 相关 | **FIX** | 明确注明该防护由目标 DEM 的 valid/NoData mask 派生，为插值安全启发式，不等价于实际物理水力连通性 | 避免读者误认为具备水动力阻隔与连通模型。 |
| `gui/main_window.py` | Line 744, 1967 | `"全部 34 个主分潮 (全精度)"` | **FIX** | 改为 `"全部 34 个分潮 (默认科学配置)"`，保留 `currentData == "all"` | 消除“全精度”修饰，保持内部枚举数据不变。 |
| `gui/main_window.py` | Line 2016 | `"15分钟 (15min - 高精度)"` | **FIX** | 改为 `"15分钟 (15min - 较高时间分辨率)"`，保留 `currentData == "15min"` | 区分时间采样密度与模型物理精度。 |
| `gui/main_window.py` | Line 1959 | `"EGM2008 (大地水准面严密海拔正高)"` | **FIX** | 改为 `"EGM2008 (相对 EGM2008 大地水准面的高程 / 正高近似)"`，保留 `currentData == "egm2008"` | 遵循现代大地测量学规范术语。 |
| `gui/main_window.py` | Line 2504 | `✅ 全程高保真有效 (Flag 1~6)` | **FIX** | 改为 `✅ FES 结果有效，未触发 NoData / 外推警告 (Flag 1~6)` | 准确反映 FES Flag 1~6 的质量控制含义。 |
| `gui/main_window.py` | Line 3086 | `高保真度的空间潮汐预测与严密基准转换工具` | **FIX** | 改为 `空间天文潮模拟与垂直基准转换工具` | 中性化 About 弹窗文字。 |
| `gui/main_window.py` | Line 3100 | `EGM2008 (经 ΔN 改正的严密海拔正高)` | **FIX** | 改为 `EGM2008 (相对 EGM2008 大地水准面的高程 / 正高近似)` | 统一大地测量术语。 |
| `gui/main_window.py` | Line 3104 | `高精度水准面栅格` | **FIX** | 改为 `NGA EGM2008 2.5' 大地水准面起伏栅格` | 准确标注数据源名称。 |
| `gui/main_window.py` | Line 3108 | `高精度时间跨界线性插值` | **FIX** | 改为 `亚时间步线性跨界插值` | 准确表征一阶插值数学本质。 |
| `gui/main_window.py` | Line 3109 | `全系统严格遵循半开区间 [start, end) 时间采样语义` | **FIX** | 改为 `栅格与 Tide Cache 工作流采用 [start, end)；通用点位时间序列接口支持可配置 inclusivity` | 真实反映点位接口支持 both/left/right/neither。 |
| `gui/main_window.py` | Line 3115 | `防篡改断点恢复` | **FIX** | 改为 `SHA-256 参数/兼容性签名与断点恢复` | 避免误解为密码学防对抗篡改系统。 |
| `gui/settings_dialog.py` | Line 2, 90, 94 | `Settings Dialog v1.6 Beta` | **FIX** | 更新为 `CoastTideX v1.7.1` | 同步弹窗标题与文档头版本。 |
| `gui/__init__.py` | Line 3 | `基于 PyQt6 构建的现代化高精度潮位预测与基准转换桌面系统` | **FIX** | 改为 `PyQt6 潮位模拟与垂直基准转换桌面界面` | 简化并中性化包说明。 |
| `cli.py` | Line 125, 145, 146 | `(v1.6 Beta)` in comments and batch help | **FIX** | 更新或剔除过时 `v1.6 Beta` 字样，保留全部 CLI 命令、参数 choices 与 defaults 不变 | 统一命令行帮助信息版本一致性。 |
| `core/__init__.py` | Line 2 | `v1.6 Beta` | **FIX** | 更新为 `v1.7.1` (仅改 docstring) | 核心包 docstring 版本对齐。 |
| `core/exposure_engine.py` | Line 4 | `Version: CoastTideX v1.6 Beta` | **FIX** | 更新为 `Version: CoastTideX v1.7.1` (仅改 docstring) | 模块 docstring 版本对齐。 |
| `core/exposure_engine.py` | Line 64 | `QC_EXP_VALID = 0  # 0: 正常高保真解算` | **FIX** | 改为 `QC_EXP_VALID = 0  # 0: 未触发当前定义的 Exposure QC/degradation bit` (常数 0 绝对不变) | 消除“高保真”注释修辞。 |
| `core/raster_engine.py` | Line 74 | `QC_BIT_VALID = 0  # 0: 无异常 / 完全有效高保真解算` | **FIX** | 改为 `QC_BIT_VALID = 0  # 0: 未触发当前定义的 Inundation QC/degradation bit` (常数 0 绝对不变) | 消除“高保真”注释修辞。 |
| `core/tide_cache.py` | Line 608, 847 | `防篡改校验 / 篡改防御 (Tamper-evidence)` | **FIX** | 改为 `签名一致性与兼容完整性核查 (Signature Consistency & Integrity Validation)` (算法与签名完全不变) | 准确表征其科学兼容性与结构校验属性。 |
| `core/batch_raster_engine.py` | Line 2 | `v1.6 Beta Hardened` | **FIX** | 更新为 `v1.7.1` (仅改 docstring) | 模块 docstring 版本对齐。 |
| `core/batch_datum_converter.py` | Line 13, 14, 445 | `彻底杜绝竞态污染 / workers=1, 2, 4 解算结果保证严密科学等价 (Bitwise / Identical)` | **FIX** | 改为 `采用 thread-local 实例与线程锁，避免多线程共享状态竞争；workers=1/2/4 在当前自动化 integration regression 中满足既定数值一致性门槛` | 不做超出测试范围的绝对化承诺。 |
| `core/dem_datum_converter.py` | Line 173 | `实现 DEM_EGM2008 到 DEM_MSL 的高精度流式转换` | **FIX** | 改为 `实现 DEM_EGM2008 到 DEM_MSL 的分块流式转换` | 消除未经量化的精度修饰词。 |
| `data/geoid/README_GEOID.md` | Line 1, 3, 55 | `v1.6 Beta` / `高保真度` | **FIX** | 更新为 `v1.7.1`，将 `质量评定为高保真度` 改为 `使用配置的来源分类栅格确定参考 geoid source` | 规范数据源分类说明。 |
| `data/geoid/README_GEOID_EN.md` | Line 1, 3, 55 | `v1.6 Beta` / `high-fidelity` | **FIX** | 同步英文版数据说明，更新为 `v1.7.1` 并中性化表述 | 保持双语一致。 |
| `docs/V1_7_MSL_REFERENCE_WORKFLOW.md` | Line 93 | `节省 50% 磁盘 I/O` | **FIX** | 改为 `减少额外 QC GeoTIFF 的存储与写入 I/O` | 消除未经全局实测的“50%”定量宣传。 |
| `docs/V1_7_MSL_REFERENCE_WORKFLOW.md` | Line 41, 190 | `高精度保证 / 高精度双线性插值` | **FIX** | 中性化为标准插值术语，澄清 QC 编码为插值路径标识符 | 规范科学技术文档。 |
| `docs/V1_7_GUI_AND_GITHUB_SYNC_AUDIT.md` | Top banner | 历史初版报告包含 $k=12$ 与 $0\sim 100\text{ km}$ 描述 | **SUPERSEDED_NOTE** | 在文件顶部增加 `SUPERSEDED / HISTORICAL SNAPSHOT` 横幅并指引最新权威参数 ($k=8, p=2, 0\sim 500\text{ km}$) | 保护历史审计报告完整性同时消除潜在误导。 |
| `docs/V1_6_*` | All files | 历史 v1.6 开发与加固报告中的 `v1.6 Beta` | **HISTORICAL_KEEP** | 完整保留历史原貌，不修改正文 | 历史事实存档，不得篡改。 |
| `tests/test_v16_*` | All files | 历史 v1.6 测试套件文件名与内部逻辑 | **HISTORICAL_KEEP** | 完整保留历史原貌，仅在文档断言必要时调整匹配字面值 | 保持历史回归基准稳定性。 |

---

## 3. 结论

全项目共计审计 46 处核心声明点，分类判定为：
- **FIX**: 38 项（涉及文档、公式、GUI 手册、UI 标签、docstrings 及当前技术报告）；
- **KEEP**: 核心代数公式、物理不变量、0-500 km 参数范围、Strict Resume fail-closed 语义等全面保留；
- **SUPERSEDED_NOTE**: 1 项（`docs/V1_7_GUI_AND_GITHUB_SYNC_AUDIT.md` 增加顶部说明）；
- **HISTORICAL_KEEP**: 7 项历史文件集完全保留。

所有 FIX 均在 PART C 至 PART I 中严格落地，严禁修改任何底层科学计算逻辑。
