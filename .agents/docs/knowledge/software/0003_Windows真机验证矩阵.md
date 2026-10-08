# 0003_Windows真机验证矩阵

> 分类: knowledge/software  |  创建: 2026-10-08  |  适用范围: M7 之后的 Windows 10/11 真机硬件验证与计时测量
> 关键词: 真机 hardware loopback PL2303 CH340 CP210x 计时粒度 poll_min_s M7

## 结论（先给做法，再讲原理）

- 库只支持 Windows 10/11 x64；没有设备、第二台系统或第二种 USB 转串口芯片时，
  单元测试和本地 CI **不能**替代真机结论；矩阵里的 `未验证` 不代表通过。
- 回环测试必须显式选择 `-m hardware` 才执行，并先设好启用变量与端口。
- `ReadLoopConfig.poll_min_s` 要先用 `scripts/measure_windows_timer.py` 实测本机定时器粒度，
  不能把 5 ms 默认值当作真机测量结果。
- 每次新增实测按文末「现场记录模板」记录，失败项保留错误信息与重现步骤，不能只写"通过"。

## 背景

本页记录 M7 的人工验证边界与已有实测结果。原始真机记录只有历史 Windows 11 开发机的
COM11/Prolific：115200 波特率，发送 `Version\r\n`，收到无结束符回复 `Software version 1.02`，
探测和查询均完成。该记录不覆盖拔插、睡眠唤醒、回环、DTR/RTS 或其他芯片。

## 细节

### 测试准备

- 只在独立的 USB 串口适配器上将 TX 与 RX 短接；断开适配器与目标设备的连接，
  不连接 VCC。区分 TTL、RS-232、RS-485 的电气接口；RS-485 A/B 不适用此 TX/RX 测试。
- 关闭终端程序对目标 COM 口的占用；记录驱动版本、适配器 VID/PID、COM 号和 Python 位数。
- 使用 `SERIALFORGE_HARDWARE_PORT` 和 `SERIALFORGE_HARDWARE_BAUDRATE` 配置回环测试：

```powershell
$env:SERIALFORGE_RUN_HARDWARE = "1"
$env:SERIALFORGE_HARDWARE_PORT = "COM11"
$env:SERIALFORGE_HARDWARE_BAUDRATE = "115200"
uv run pytest -m hardware tests/hardware/test_loopback.py
Remove-Item Env:SERIALFORGE_RUN_HARDWARE
```

回环测试直连显式的 DeviceInfo，不做探测；发送 `SERIALFORGE_LOOPBACK\r\n` 并要求完整
回显。默认 pytest 即使设置了启用变量，也只有选择 `-m hardware` 才能执行硬件测试。
M7 本次未打开任何真实端口。

### 矩阵

`通过` 只表示该格已在对应环境实际执行；`未验证` 不代表通过。

| 系统 | 芯片/适配器 | 正常收发 | 拔插重连 | 睡眠唤醒 | TX-RX 回环 | DTR/RTS | 计时粒度 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Windows 11 x64 | Prolific PL2303 (`067B:23A3`) | 已验证：COM11 `Version` 查询/发现 | 未验证 | 未验证 | 未验证 | 未验证 `dtr=False` 脉冲 | 未验证 |
| Windows 11 x64 | CH340 | 未验证 | 未验证 | 未验证 | 未验证 | 未验证 | 未验证 |
| Windows 11 x64 | CP210x 或 FTDI | 未验证 | 未验证 | 未验证 | 未验证 | 未验证 | 未验证 |
| Windows 10 x64 | Prolific PL2303 | 未验证 | 未验证 | 未验证 | 未验证 | 未验证 | 未验证 |
| Windows 10 x64 | CH340 | 未验证 | 未验证 | 未验证 | 未验证 | 未验证 | 未验证 |
| Windows 10 x64 | CP210x 或 FTDI | 未验证 | 未验证 | 未验证 | 未验证 | 未验证 | 未验证 |

### 当前主机计时

2026-10-08，Windows 内部版本 10.0.26200（Windows 11），Python 3.11.12、64 位。
通过 `uv run python scripts/measure_windows_timer.py --samples 100` 实测：

| 请求 sleep | 最短 | 中位 | 最长 |
| --- | --- | --- | --- |
| 1 ms | 1.023 ms | 1.528 ms | 2.585 ms |
| 5 ms | 5.024 ms | 5.501 ms | 6.334 ms |

1 ms 请求的样本说明本主机当前调度没有 15.6 ms 的固定下限，可保留 5 ms 名义
`poll_min_s`；实际等待含调度开销，不承诺精确 5 ms。结果不代表串口驱动定时器、
遮挡/省电场景或 Windows 10；矩阵中的硬件相关计时仍未验证。

### 本机软件验收

2026-10-08，Windows 11 x64 build 26200，`uv run python scripts/ci.py --release`
完整执行退出码 0。以下结果不替代上面的真机矩阵：

| 环境 | 结果 |
| --- | --- |
| Python 3.11.12 / 3.12.10 / 3.13.3 / 3.14.3 独立环境 | 每个环境 99 通过、1 硬件跳过 |
| 源码外的干净 wheel 安装 | 导入、Demo、对象身份和外置测试通过；99 通过、1 硬件跳过 |
| Python 3.11 最低组合 | PySide6-Essentials 6.11.2、pyserial 3.5、loguru 0.7.0；99 通过、1 硬件跳过 |
| 构建与静态检查 | wheel/sdist、twine、Ruff、ty 通过；Pylint 10.00 |

wheel 和 sdist 成员检查确认没有 tests、testing、examples 或字节码。最低解析在临时
副本固定开发工具，完整 CI 未修改仓库锁文件。6.11.2 是本次已验证的保守 Qt 下限；
测试过的部分旧版本存在 signal.connect 与业务 connect 名称冲突，中间版本未逐一验证。

### 现场记录模板

每次新增实测请记录：日期、Windows 内部版本、Python 版本和位数、芯片及驱动版本、
COM 号、波特率、DTR/RTS 设置、计时粒度、正常收发、拔插、睡眠唤醒、USB 选择性挂起、
TX-RX 回环结果和日志文件。失败项保留错误信息与重现步骤，不能只写"通过"。

## 常见改动点

- 新增一次真机实测 → 只更新对应格子与「现场记录」，不要把未测格改成通过。
- 想改回环步骤或超时 → 改 `tests/hardware/test_loopback.py`，并同步本页的 PowerShell 片段。
- 想改计时测量 → 改 `scripts/measure_windows_timer.py`，并在「当前主机计时」补新数据。

## 参考

- 脚本：`scripts/measure_windows_timer.py`、`scripts/ci.py`
- 测试：`tests/hardware/test_loopback.py`
- 关联任务：`.agents/docs/todo/0007_M7_集成演示与发布前验收.md`
