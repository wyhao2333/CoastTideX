# CoastTideX 项目开发规范与上下文准则 / Project Rules & Context

本项目为 **CoastTideX**（全球潮汐与高程基准空间模拟系统，工作区：`I:\Test_tide_model`）。在本项目的所有会话、任务与日常开发中，必须严格遵循以下行为准则：

---

## 一、核心铁律：一定要等 GitHub 完全推送成功再决定

> **【核心铁律】在所有涉及向 GitHub 远程仓库推送代码的任务中，严禁在执行 `git push` 后立即判定任务完成或向用户汇报。必须确认 GitHub 远程完全推送成功且 CI 流水线完全执行成功（绿标）后，方可做下一步决定或向用户汇报！**

### 1. 验证远程 Commit 同步
- 执行 `git push` 之后，必须运行 `git ls-remote origin <branch>` 或 `git log -1 origin/<branch>`，确保远端 HEAD 指针已与本地最新 Commit Hash 保持完全一致。

### 2. 强制等待 GitHub Actions CI 流水线执行完毕并变绿
- 推送后，必须使用 GitHub CLI（`gh run list --limit 1` 获取最新工作流 ID，并使用 `gh run watch <run-id>` 持续监听）等待远端 CI 自动化构建与测试完成。
- **只有在 GitHub Actions CI 显示状态为 `completed success` 时**，才代表本次推送真正成功。

### 3. CI 失败即时闭环修复
- 若 GitHub Actions CI 出现任何异常、报错或非 `success` 状态，必须立即调用 `gh run view <run-id> --log-failed` 调取失败日志进行排查。
- 必须立刻修复问题并在本地验证通过后再次推送，并继续等待新的 CI 运行完毕直至 `success`。
- **在 CI 处于运行中（in_progress / queued）或失败（failure）状态下，严禁向用户声称任务已完成！**

---

## 二、项目专属 Python 虚拟环境准则

- **专属虚拟环境路径**：
  - Python 解释器：`I:\Test_tide_model\.venv\Scripts\python.exe`
  - Pip 管理器：`I:\Test_tide_model\.venv\Scripts\pip.exe`
- **最高优先级覆盖**：
  - 在本仓库（`I:\Test_tide_model`）内执行任何 Python 脚本、测试套件、CLI 命令、GUI 启动或包管理时，**必须优先且唯一使用本项目的 `.venv` 虚拟环境**，显式覆盖全局默认环境。
  - 严禁随意切换到未授权的全局环境或外部环境。

---

## 三、功能开发与修改后的测试验证准则

1. **修改后必测**：所有代码修改、Bug 修复、算法迭代或依赖变动，在提交前必须在本地 `.venv` 环境下完整运行单元测试套件：
   ```powershell
   & "I:\Test_tide_model\.venv\Scripts\python.exe" -m unittest discover -s tests -p "test_*.py"
   ```
2. **端到端核心功能简测**：对新增或修改的业务逻辑、GUI 响应插槽、CLI 命令，必须执行实际运行测试，杜绝 `TypeError`、`NoneType`、坐标系转换错误、除以零等潜在崩溃隐患。
3. **CI 环境防御机制**：GitHub Actions 运行环境为无图形界面且缺少 `pyfes` 编译环境的纯净 Linux，编写涉及底层 C 扩展或 GUI 显示的测试用例时，必须做好 `@unittest.skipUnless` 守卫与 Mock 兼容。
