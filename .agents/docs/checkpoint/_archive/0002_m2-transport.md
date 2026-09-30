> 归档时间: 2026-09-30  |  归档原因: 任务已完成  |  原路径: .agents/docs/checkpoint/0002_m2-transport.md

# 0002 M2 传输层与无硬件测试后端

> 关联任务: todo/0002_M2 transport and no-hardware backend.md  |  更新: 2026-09-30  |  进度: 5/5
> 最近 commit: 待提交（feat/m2-transport）

## 已完成

- 完成 `BackendSwitch`、`SerialTransport`、`FrameSplitter`、`ChecksumCalculator`、`LatencyTracker`、`PortRegistry`。
- 完成 `FakeBackend`、`FakeTransport`、`SimulatedDevice`、`use_fake_backend`，支持脚本响应、延迟、回显、分块、主动注入和断线故障。
- 新增 24 项定向测试，覆盖半包/粘包、四种定界、缓冲上限、六种校验、EWMA、租约和假后端读写。
- 新增 `scripts/measure_windows_timer.py`；本机 20 次 1 ms 请求观测为 min 1.348 ms / median 1.539 ms / max 1.618 ms。
- 规范化旧 M1 进度页：`docs/progress/M1.md` 已移入 `.agents/docs/checkpoint/_archive/`，M1 与 M2 todo/checkpoint 均已归档。

## 进行中（下一步从这里继续）

M2 实现与本地验证完成，等待用户确认后进入 M3。后续不得在本阶段继续扩展 connection/discovery。

## 待办

- Windows 10/11 多机器、USB 转串口芯片、拔插/DTR/RTS/真实计时粒度仍未在硬件真机验证。
- M3：实现 TrafficLogger、LogFileManager 与 loguru 接入。

## 关键决策与上下文（恢复任务必读）

- `FrameSplitter` 内部始终处理 bytes；LINE/DELIMITED 返回去定界符或含边界的原始 bytes，LENGTH_PREFIX 的长度字段表示 payload 长度，SILENCE_GAP 由 `flush()` 发帧。
- `ChecksumCalculator` 的 CRC16-MODBUS 返回 little-endian，CRC16-CCITT 返回 big-endian，CRC32 返回 little-endian；自定义函数优先。
- `FakeBackend` 通过 `BackendSwitch` 注入，未改变 `SerialHandler`/`DeviceFinder` 构造签名；`PortRegistry` 是进程内租约表。

## 验证方式

- `uv run python scripts/ci.py --fast`：sync、ruff format、ty、pytest 全部通过，24 passed。
- `python .agents/tools/newdoc.py --root . check`：编号、引用、任务索引通过。
- `uv run pylint src/serialforge/transport src/serialforge/testing`：10.00/10。
- 未执行 PyPI 上传；未在 Windows 10 或真实串口硬件验证。
