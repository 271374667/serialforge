# 0001_loguru 默认静默与导入边界

> 分类: knowledge/software  |  创建: 2026-10-01  |  适用范围: diagnostics 日志实现
> 关键词: loguru、默认静默、导入副作用、enqueue

## 结论（先给做法，再讲原理）
不要在包导入时调用 `logger.disable("serialforge")` 或 `logger.enable(...)`。`TrafficLogger` 根据实例的 `LogConfig.enabled` 决定是否发出 DEBUG 记录；启用文件记录时，`LogFileManager` 只接收绑定了本连接 `conn_id` 的记录。

## 背景
设计文档提议在导入时禁用 `serialforge` namespace，再由日志开关启用它。loguru 的这些 API 修改进程级 logger 状态；导入 `serialforge` 则必须无副作用，也不能覆盖宿主程序自己的 loguru 配置。

## 细节
日志调用只从 `TrafficLogger` 的显式记录方法发出。默认 `enabled=False` 时不会发出记录，因此不需要改动 loguru 的全局 namespace 开关。启用后，记录通过宿主已配置的 loguru sink 输出；`save_to_file=True` 时另挂 `enqueue=True` 的连接专属 sink，filter 按 `conn_id` 隔离多个连接。

关闭连接或文件开关时先移除该连接的 sink，再调用 `logger.complete()` 排空队列。该 `complete()` 会同步等待队列 sink；它返回的 awaitable 用于宿主配置的异步 sink，本项目不创建或运行异步 sink。

## 常见改动点
- 想调整默认静默策略 → 改 `src/serialforge/diagnostics/traffic_logger.py` 的 `_record()` / `record_event()`，不要在 `__init__.py` 增加 logger 全局设置。
- 想调整连接文件隔离 → 改 `src/serialforge/diagnostics/log_file_manager.py` 的 `connection_filter`，确保它仍只匹配 `conn_id`。

## 参考
- 关联代码：`src/serialforge/diagnostics/traffic_logger.py`、`src/serialforge/diagnostics/log_file_manager.py`
- 设计依据：`串口通讯模块_方案_v10定稿.md` 第 9.2、9.3 节；项目约束要求导入 `serialforge` 无副作用。
