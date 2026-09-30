# 0001 M1 包骨架与定义层

> 任务: todo/0001_M1-foundation.md | 更新: 2026-09-30 | 进度: 1/1
> 最近 commit: 98ba269（feat/m1-foundation）

## 已完成

- 手动建立 AGENTS、架构总览、规则、工具、todo、checkpoint 和 git hook 结构。
- 建立 `src/serialforge` 定义层、advanced/errors/testing 命名空间和 QtCore 门面空壳。
- 写入公共数据类、枚举、构造期校验、对象身份契约、API 快照与架构守卫测试。
- 配置 uv/hatchling/ty/ruff，生成 `uv.lock`，本地 CI 全量通过。
- 原 `.agents/docs/checkpoint/0001_m1-foundation.md` 的验证记录已合并到本 checkpoint；阶段进度不再在公开 docs 下重复维护。

## 历史验证记录

- T-09、T-13、T-19、T-23、T-24、T-33、T-36、T-37、T-40、T-45、T-50、T-52、T-53、T-54、T-55 已完成。
- `uv sync`、`uv run ty check`、`uv run ruff check src tests scripts`、`uv run ruff format --check src tests scripts`、`uv run pytest`（16 passed）、`uv run pylint src`（10.00/10）、`uv build`、`uv run twine check dist/*` 和 `uv run python scripts/ci.py` 均通过。
- ty 参数、QtCore `Signal`/`Slot`/`QThread.run` smoke 与 `all = "error"` 均无诊断。
- 未在真实串口硬件、Windows 10、多种 USB 芯片验证；未运行 Python 3.12–3.14 矩阵或最低依赖解析。
- 用户级 `project-ai-normalize` 的脚手架存在冲突标记，因此项目规范目录按模板手动落地。

## 关键决策与上下文

- 顶层 `__all__` 固定 15 个名字；进阶类型只从 `serialforge.advanced` 导出。
- `CommandSpec`/`EventSpec` 使用 `frozen=True, eq=False`，不复制对象。
- 定义层通过延迟导入 advanced 默认配置，保持依赖单向且顶层导入无副作用。
- 真实业务命令只允许出现在 tests/examples，源码没有任何具体命令实例。

## 验证方式

`uv run python scripts/ci.py` 全量通过：16 个 pytest、ty、ruff、pylint 10.00/10、uv build、twine check。

旧的 `docs/progress/M1.md` 已迁移到本归档 checkpoint；后续阶段以 `.agents/docs/checkpoint/` 为唯一进度位置。
