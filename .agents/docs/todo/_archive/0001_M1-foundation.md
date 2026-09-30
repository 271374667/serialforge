> 归档时间: 2026-09-30  |  归档原因: 任务已完成  |  原路径: .agents/docs/todo/0001_M1-foundation.md

# 0001 M1 foundation

> 状态: 已完成  |  创建: 2026-09-30  |  完成: 2026-09-30
> 优先级: 高  |  关联 checkpoint: checkpoint/0001_m1-foundation.md

## 背景

设计文档要求先交付 M1：建立 `src` 布局、定义层数据类与枚举、分层命名空间、架构守卫/API 快照骨架、ty 验证夹具和本地 CI。项目当前只有占位 `main.py`，没有可复用的库实现。

## 目标（验收标准）
- [x] `uv sync` 成功，`uv.lock` 入库，包可从 `src/serialforge` 导入。
- [x] 顶层 `__all__` 恰为 15 个名字；`advanced`、`errors`、`testing` 命名空间边界稳定。
- [x] `CommandSpec`、`EventSpec`、`DeviceProfile`、`LogConfig`、结果类和 M1 进阶配置具备构造期校验及不可变契约。
- [x] `CommandSpec`/`EventSpec` 模式推断、默认值、对象身份语义和 `DeviceProfile` 默认 8N1 有测试。
- [x] `test_architecture.py`、API 快照骨架、`tests/typing/qt_smoke.py`、`scripts/ci.py` 可运行。
- [x] `.agents/docs/checkpoint/0001_m1-foundation.md` 记录三项 ty 未验证点、命令结果、假设与未在真机验证项。

## 阻塞 / 依赖

设计文档指定的第三方依赖已由用户需求明确授权：`PySide6-Essentials`、`pyserial`、`loguru`；工具链使用 uv。

## 完成记录

M1 验收已在 2026-09-30 完成；全量本地 CI 通过，未进行真实串口或 Windows 10 验证。进度记录已迁入 `.agents/docs/checkpoint/`，旧的 `docs/progress/M1.md` 已归档。
