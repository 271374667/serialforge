# serialforge

> 本文件是本项目 AI 协作的唯一入口。修改项目之前先读本文件、`.agents/ARCHITECTURE.md` 的第 1、2、3、6 节，再按规则索引加载需要的细则。

## 1. 项目速览

| 项目 | 内容 |
| --- | --- |
| 名称 | serialforge |
| 简介 | 面向 Windows 10/11 x64 的 PySide6 QtCore 串口通讯库 |
| 技术栈 | Python 3.11+、uv、hatchling、PySide6-Essentials、pyserial、loguru |
| 入口 | `src/serialforge/__init__.py`；开发脚本在 `scripts/` |
| 构建 | `uv build` |
| 测试 | `uv run pytest`；阶段 CI 为 `uv run python scripts/ci.py` |
| 当前阶段 | M7 软件验收通过、真机矩阵排队；公共 API 改造 R1 契约就绪，R2 待阶段确认 |
| 架构总览 | `.agents/ARCHITECTURE.md`（第 1、2、3、6 节为每次必读区，≤150 行） |

目录重点：`src/serialforge/` 是源码，`tests/` 是测试，`examples/` 放使用方命令示例，`.agents/` 放项目规范与交接文档；本 skill 不复制进项目技能目录。

## 2. 规则索引

| 文件 | 什么时候必须读 |
| --- | --- |
| `.agents/rules/01_session_workflow.md` | 每次会话开始、交接与恢复 |
| `.agents/rules/02_code_style.md` | 修改 Python、类型注解、docstring |
| `.agents/rules/03_architecture.md` | 新增模块、类、目录或依赖方向 |
| `.agents/rules/04_docs_system.md` | 新建、编号、归档 `.agents/docs` 文档 |
| `.agents/rules/05_git_and_commits.md` | 分支、提交、钩子、历史操作 |
| `.agents/rules/06_python_toolchain.md` | uv、依赖、测试、ruff、ty |
| `.agents/rules/07_task_tracking.md` | todo、checkpoint、长任务恢复 |
| `.agents/rules/08_layout_and_temp.md` | 新建目录、配置、运行数据、临时产物 |
| `.agents/rules/09_dependencies_and_platform.md` | 第三方依赖与 Windows 兼容性 |
| `.agents/rules/13_packaging_and_outputs.md` | 构建、发布、输出目录 |
| `.agents/rules/15_architecture_doc.md` | 更新架构总览 |
| `.agents/docs/knowledge/software/0005_公共API改造_契约索引与迁移.md` | 修改公共 API、声明/结果、回调/信号、Qt 绑定或对应快照；按索引加载细则 |

## 3. 不可协商的硬约束

1. 既有 M1→M7 仍按 v10 交付，M7 未完成真机项不得提前归档；用户 2026-10-09 授权公共 API 改造按 R1→R5 进行，目标契约见知识索引 0005，冲突的 v10 接口条款由它覆盖。每阶段实现、测试、汇报后停下等待确认。
2. 所有依赖与命令经 `uv` 管理；使用 `src` 布局与 hatchling；不使用 pip。
3. 仅使用 QtCore；禁止 `QtWidgets`、`QtGui`、`asyncio`、`multiprocessing`；导入 `serialforge` 不得产生副作用。
4. 顶层导出按公共 API 契约索引 0005：当前运行代码仍为旧 15 名字，过渡期 19、最终 11；各阶段实现与快照同步，不提前改快照伪装交付。进阶 API 在 `advanced`，异常子类在 `errors`。
5. 新 `Spec` 与兼容 `CommandSpec` / `EventSpec` 必须 `frozen=True, eq=False`，全链路保留身份，库内不得复制或重建；字段构造形式只创建一次。业务判断仍使用原声明对象。
6. serialforge 源码不包含具体业务命令；业务命令只能在 `examples/` 与 `tests/` 声明。
7. 库内日志只用 loguru DEBUG，默认静默；不得执行 PyPI 上传，不生成云端 CI。
8. `master` 是主分支，必须始终包含完整可用的代码（可构建、可导入、已通过阶段验收），只由用户在明确要求时接受来自 `dev` 的合并来更新，AI 不主动向 `master` 提交或合并；`dev` 是开发分支，不保证稳定可用，日常开发、提交与实验性改动都在 `dev` 进行，绝不在 `master` 上直接动手。未经用户明确许可不 `git push`；当前快速迭代阶段在 `dev` 上不新建 `feat/*` / `fix/*` 分支，直到用户明确宣布稳定后恢复特性分支规则；提交信息使用中文 Conventional Commits 和 AI trailers。
9. 项目源码统一使用绝对导入（`serialforge...`）；禁止包内相对导入。FakeBackend、FakeTransport、SimulatedDevice 等测试替身只放 `tests/support/`，不得放进 `src/serialforge/` 或发布 wheel。

## 4. 工作约定

- 发现文档与代码不符时立即更新 `.agents/ARCHITECTURE.md`。
- 长任务使用 `.agents/docs/todo/README.md` 唯一调度索引；同一时间最多一个激活任务。
- 每个里程碑实现、测试、汇报后停下等待确认；收尾只对本次修改文件运行格式化与类型检查，并如实记录 ty、Windows 真机和硬件未验证项。
- 公共 API 目标：`SerialForge`、`Spec`、`Message`；主线程 `add_done_callback`，异步默认 3 秒且超时不回调；`received`/`raw_sent`，公开移除 `traffic_logged`；窄 Qt6 适配不引入 QtPy。实际交付状态以 checkpoint 为准。
- `project-ai-normalize` 脚手架当前含冲突标记，不能执行；本次规范目录按其模板手动生成，工具修复留作项目外问题。
