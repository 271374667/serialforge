> 归档时间: 2026-09-30  |  归档原因: 任务已完成  |  原路径: .agents/docs/todo/0002_M2 transport and no-hardware backend.md

# 0002_M2 transport and no-hardware backend

> 状态: 已完成  |  创建: 2026-09-30  |  完成: 2026-09-30
> 优先级: 高  |  关联 checkpoint: checkpoint/0002_m2-transport.md

## 背景

设计文档的 M2 负责建立可替换的串口传输基础设施，使后续 connection/discovery 能在真实后端与无硬件假后端之间复用同一协议。当前 `transport` 只有空壳，`testing` 只有数据占位，M1 的定义层已冻结。

## 目标（验收标准）
- [ ] `BackendSwitch`、`SerialTransport`、`FrameSplitter`、`ChecksumCalculator`、`LatencyTracker`、`PortRegistry` 按 L1 单向依赖实现并有完整类型注解。
- [ ] `FakeBackend`、`FakeTransport`、`SimulatedDevice`、`use_fake_backend` 可注入后端，支持延迟、粘包/半包、乱码和断线故障脚本。
- [ ] 帧定界覆盖 LINE、DELIMITED、LENGTH_PREFIX、SILENCE_GAP，处理超长缓冲并保留原始 bytes；校验覆盖 NONE、SUM8、XOR、CRC16-MODBUS、CRC16-CCITT、CRC32 及自定义函数。
- [ ] 延迟跟踪实现 RFC 6298 风格 EWMA、冷启动和轮询间隔上下限；端口注册表实现进程内独占租约。
- [ ] 加入 Windows 计时粒度实测脚本；真实 Windows/硬件结果如实标为未验证。
- [ ] 新增 M2 单元测试，更新架构总览、checkpoint 和任务完成记录；`uv run pytest`、ruff、ty 通过。

## 阻塞 / 依赖

## 完成记录

M2 传输层、可替换假后端、计时脚本和定向测试已完成；`uv run python scripts/ci.py --fast`、ty、ruff、pylint 和规范校验通过。真实 Windows 10/11、多种 USB 芯片及串口硬件仍待 M7 验证。
