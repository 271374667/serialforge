# 0008_M7-handoff

> 关联任务: todo/0007_M7_集成演示与发布前验收.md  |  更新: 2026-10-08  |  进度: M1–M6 已交付，M7 未开始
> 最近 commit: 4d253a0（交接前 HEAD；交接文档提交见 `git log -1`）

## 背景与目标

用户要求确认全部分支合入 `dev`、只保留 `dev`，保存各阶段进度并转到新对话开发。本轮仅执行分支清理和文档交接。

## 已完成

| 阶段 | 已交付范围 | 关键 commit |
| --- | --- | --- |
| M1 | 包骨架、定义层、API/架构守卫、工具链与规范 | `98ba269` |
| M2 | 传输、定界、校验、延迟、租约与假后端 | `40ab7e7` |
| M3 | 流量记录、日志文件管理、默认静默 | `1716749` |
| M4 | 注册表、响应解析、调度、背压、静默定界 | `222044c`、`393c7b9`、`526ac6f` |
| M5 | 连接工作线程、读循环、初始化、心跳与重连 | `e8c73ca` |
| M6 | 扫描、探测、缓存、异步取消、COM11 发现 | `e8c73ca` |
| M7 | 尚未实施；任务已登记用于新会话恢复 | - |

- `8bcb650` 合并既有分支历史；`d13b7e0` 固化快速迭代时直接在 `dev` 开发；`4d253a0` 归档 M5/M6 并同步架构。
- 删除前逐一执行 `git merge-base --is-ancestor <分支> dev`，4 个分支均通过；随后用 `git branch -d` 删除，只剩 `dev`。
- 已删分支及末端：`feat/m1-foundation` → `a24e8ab`；`feat/m3-diagnostics` → `a48a66e`；`feat/m4-dispatcher` 和 `feat/m5-m6-lifecycle-discovery` → `9b80efb`。提交均仍可从 `dev` 到达，必要时可据哈希重新创建分支。
- 上轮 `uv run python scripts/ci.py` 通过：全仓 88 项测试、Ruff 格式检查、ty、Pylint 10.00、wheel/sdist 构建和 twine check；本轮没有修改代码或重新跑全量 CI。
- M6 真机：COM11，VID/PID `067B:23A3`，115200；发送 `Version\r\n`，静默间隔 0.08 秒后匹配无结束符回复 `Software version 1.02`，耗时约 0.313 秒；版本号不写死。
- M1–M6 的历史任务与 checkpoint 已归档；当前 checkpoint 目录只有本交接快照。

## 进行中（下一步从这里继续）

- 当前分支为 `dev`；本轮停止开发，M7 在新对话开始。先读取关联任务并确认最新用户指令，再按设计第 14 节推进。
- 先检查 `git status --short --branch`；唯一既有未提交状态应为 ` D main.py`。不回滚、不自动提交该删除，也不自动 stash。
- 对照设计文档第 3、12.4、13、14、15 节核对 M7 契约；从 `scripts/ci.py`、`README.md`、`tests/test_api_snapshot.py` 和现有测试确定缺失项，随后补充 `examples/` 与 `docs/` 交付内容。

## 待办

- M7 的 Demo、使用文档、docstring、CHANGELOG、API 快照、发布前流程及默认跳过的硬件套件。
- Python 3.11–3.14 矩阵、最低直接依赖、干净环境安装 wheel 与运行 Demo。
- Windows 10/11 多机器、至少两种芯片、DTR/RTS、拔插重连、睡眠唤醒和 TX-RX 回环矩阵。

## 关键决策与上下文（恢复任务必读）

- 直接在 `dev` 开发和提交，直到用户宣布稳定；其他规范以 `AGENTS.md` 与规则索引为准。
- 只使用 QtCore、uv；禁止 PyPI 上传、未授权 push、tag、强制改写历史和云端 CI。
- 无结束符接收与发送 CRLF 独立；周期性 `flush()`、动态版本匹配和原始 spec 对象身份必须保留。
- 默认无硬件测试；COM11 的历史授权仅涉及 `Version` 查询，不代表拔插、复位或其他命令已获授权。
- 用户级规范脚手架含冲突标记，不执行；当前本地规范工具可用。

## 未验证事项与风险

- 88 项测试是全仓总数，不是 M5/M6 定向测试数；阶段完成记录不代表设计所有组合已经验证。
- `scripts/ci.py --matrix` 只打印提示；`--smoke` 仅在项目环境导入；`--release` 未实现完整发布前流程。现有 CI 通过不证明矩阵、最低依赖或干净 wheel 安装通过。
- 真机验证限于当前 COM11 的查询与扫描；其余硬件矩阵保持未验证。

## 验证方式

- 本轮：分支可达性检查、`git diff --check`、`uv run python .agents/tools/taskmgr.py check` 与 `uv run python .agents/tools/newdoc.py check`。
- 后续开发：按变更范围跑定向测试；M7 收尾完成实际矩阵、安装包验证和阶段 CI，如实记录不可验证项。
