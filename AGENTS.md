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
| 当前阶段 | M4 实现与阶段验证完成；等待确认进入 M5 连接门面 |
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

## 3. 不可协商的硬约束

1. 按设计文档 `串口通讯模块_方案_v10定稿.md` 的 M1→M7 顺序交付；每个里程碑实现、测试、汇报后停下等待确认。
2. 所有依赖与命令经 `uv` 管理；使用 `src` 布局与 hatchling；不使用 pip。
3. 仅使用 QtCore；禁止 `QtWidgets`、`QtGui`、`asyncio`、`multiprocessing`；导入 `serialforge` 不得产生副作用。
4. 顶层 `serialforge.__all__` 固定为设计文档规定的 15 个名字；进阶 API 在 `advanced`，异常子类在 `errors`。
5. `CommandSpec` / `EventSpec` 必须是 `frozen=True, eq=False`，库内不得复制或重建它们；结果通过对象身份判断。
6. serialforge 源码不包含具体业务命令；业务命令只能在 `examples/` 与 `tests/` 声明。
7. 库内日志只用 loguru DEBUG，默认静默；不得执行 PyPI 上传，不生成云端 CI。
8. 未经用户明确许可不 `git push`；当前快速迭代阶段直接在 `dev` 开发和提交，不新建 `feat/*` / `fix/*` 分支，直到用户明确宣布稳定后恢复特性分支规则；提交信息使用中文 Conventional Commits 和 AI trailers。

## 4. 工作约定

- 发现文档与代码不符时立即更新 `.agents/ARCHITECTURE.md`。
- 长任务使用 `.agents/docs/todo/README.md` 唯一调度索引；同一时间最多一个激活任务。
- 每个里程碑实现、测试、汇报后停下等待确认；收尾只对本次修改文件运行格式化与类型检查，并如实记录 ty、Windows 真机和硬件未验证项。
- `project-ai-normalize` 脚手架当前含冲突标记，不能执行；本次规范目录按其模板手动生成，工具修复留作项目外问题。
