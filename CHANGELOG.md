# Changelog

## 1.0.0 - 2026-10-19

- 待填写发布说明。

## 0.0.1 - 2026-10-08

- 公共 API 改造 R1–R5：新增 `SerialForge` 门面、统一 `Spec` 声明与 `Message` 结果，日常配置、扫描、连接、发送和观察都走门面。
- 同步 `scan_sync()` / `connect_sync()` / `send_sync()` 阻塞；异步扫描/连接返回 `ScanCall`/`ConnectCall`，`add_done_callback` 在主线程交付结果，失败不回调。
- 异步发送返回 `CommandCall`，成功回调在主线程投递，默认 3 秒期限且超时不回调。
- 收发观察收敛为 `connection_state_changed` / `received` / `raw_sent` 三个信号，公开移除 `traffic_logged`；兼容入口 `SerialHandler` 保留旧方法名桥接但不再暴露旧信号。
- 新增 PyQt6 绑定支持（与 PySide6 二选一，extra `pyqt6` / `pyside6`）与 mkdocs-material 使用文档站点。
- 完成 M1–M6：QtCore 串口传输、帧定界、调度、日志、连接生命周期、发现与波特率探测。
- 增加 M7 无硬件集成 Demo、API 快照、发布前检查脚本和 Windows 验证矩阵模板。
- 增加默认跳过的 `hardware` 回环测试；未验证的系统、芯片和电源管理组合保持明确标记。
- 统一绝对导入；测试替身移至 `tests/support/`，移除 `serialforge.testing`，wheel 和 sdist 不包含测试组件。
- 修复生产线程对开发专用 `typing_extensions` 的依赖，支持只有声明运行依赖的干净 wheel 环境。
- 最低依赖实测发现旧版 Qt 信号连接与 `SerialHandler.connect()` 冲突；将 PySide6-Essentials 下限收紧至完整套件通过的 6.11.2，pyserial 3.5 与 loguru 0.7.0 保持通过。
