# Changelog

## 0.0.1 - 2026-10-08

- 完成 M1–M6：QtCore 串口传输、帧定界、调度、日志、连接生命周期、发现与波特率探测。
- 增加 M7 无硬件集成 Demo、API 快照、发布前检查脚本和 Windows 验证矩阵模板。
- 增加默认跳过的 `hardware` 回环测试；未验证的系统、芯片和电源管理组合保持明确标记。
- 统一绝对导入；测试替身移至 `tests/support/`，移除 `serialforge.testing`，wheel 和 sdist 不包含测试组件。
- 修复生产线程对开发专用 `typing_extensions` 的依赖，支持只有声明运行依赖的干净 wheel 环境。
