# 0001 M1 包骨架与定义层

> 任务: todo/0001_M1-foundation.md | 更新: 2026-09-30 | 进度: 1/1
> 最近 commit: 未提交（feat/m1-foundation）

## 已完成

- 手动建立 AGENTS、架构总览、规则、工具、todo、checkpoint 和 git hook 结构。
- 建立 `src/serialforge` 定义层、advanced/errors/testing 命名空间和 QtCore 门面空壳。
- 写入公共数据类、枚举、构造期校验、对象身份契约、API 快照与架构守卫测试。
- 配置 uv/hatchling/ty/ruff，生成 `uv.lock`，本地 CI 全量通过。

## 进行中（下一步）

M1 已达到设计文档验收，等待用户确认后开始 M2。恢复时先读 `AGENTS.md`、架构必读区、todo 索引和本 checkpoint。

## 待办

- M2：实现 transport、FakeBackend、FrameSplitter、ChecksumCalculator、LatencyTracker、PortRegistry 与计时实测。
- 收尾时补全 M1 任务完成记录并按 taskmgr 归档；用户确认前保留任务激活状态。

## 关键决策与上下文

- 顶层 `__all__` 固定 15 个名字；进阶类型只从 `serialforge.advanced` 导出。
- `CommandSpec`/`EventSpec` 使用 `frozen=True, eq=False`，不复制对象。
- 定义层通过延迟导入 advanced 默认配置，保持依赖单向且顶层导入无副作用。
- 真实业务命令只允许出现在 tests/examples，源码没有任何具体命令实例。

## 验证方式

`uv run python scripts/ci.py` 全量通过：16 个 pytest、ty、ruff、pylint 10.00/10、uv build、twine check。
