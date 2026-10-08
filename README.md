# serialforge

> 适用版本: 0.0.1  |  当前里程碑: M7

`serialforge` 是面向 Windows 10/11 x64 应用的 PySide6 串口通讯库。库只依赖
QtCore 的信号、槽和线程，提供设备发现、命令调度、自动重连和流量日志。
库不包含任何具体设备业务命令，业务命令由使用方声明。

支持 Python 3.11–3.14、`PySide6-Essentials>=6.11.2`、`pyserial>=3.5` 和
`loguru>=0.7`。较旧 Qt 绑定的信号连接存在与业务 `connect()` 的名称冲突；6.11.2
是本次通过完整套件的保守下限，不表示所有中间版本都已逐一验证。导入
`serialforge` 不会创建 Qt 应用、线程、文件或日志 sink。

## 最小示例

下例在仓库中运行，使用 `tests.support` 的本地测试替身。客户 wheel 和 sdist 均不包含
测试替身、`tests`、`examples` 或 `serialforge.testing`。使用方必须创建
`QCoreApplication`、注册命令、等待连接信号，再发送命令；`result.spec` 保留原始对象：

```python
from dataclasses import dataclass

from PySide6.QtCore import QCoreApplication, QTimer

from serialforge import (
    CommandSpec, ConnectionState, DeviceProfile, SerialHandler,
)
from tests.support import FakeBackend, SimulatedDevice, use_fake_backend


@dataclass(frozen=True)
class VersionInfo:
    version: str


VERSION = CommandSpec(
    "Version",
    r"Software version (?P<version>\d+\.\d+)",
    VersionInfo,
)

app = QCoreApplication.instance() or QCoreApplication([])
profile = DeviceProfile(
    vid_pid=[(0x067B, 0x23A3)],
    baudrates=[115200],
    probe=VERSION,
)

def on_state(state):
    if state is ConnectionState.CONNECTED:
        handler.send(VERSION)


def on_result(result):
    assert result.spec is VERSION
    assert result.ok
    assert result.data == VersionInfo("1.02")
    print(result.data)
    app.exit(0)


backend = FakeBackend([
    SimulatedDevice(0x067B, 0x23A3, port="SIM-README", responses={
        b"Version\r\n": b"Software version 1.02\r\n",
    }),
])
with use_fake_backend(backend):
    handler = SerialHandler(profile)
    handler.register(VERSION)
    handler.connection_state_changed.connect(on_state)
    handler.command_finished.connect(on_result)
    handler.error_occurred.connect(lambda error: app.exit(1))
    deadline = QTimer()
    deadline.setSingleShot(True)
    deadline.timeout.connect(lambda: app.exit(1))
    deadline.start(3000)
    handler.connect()
    try:
        assert app.exec() == 0
    finally:
        deadline.stop()
        handler.disconnect()
```

`connect()` 异步执行，支持无参自动发现、指定端口名、指定已探测的 `DeviceInfo`。
真实设备使用相同的注册和信号流程，删除上述假后端上下文即可。
`DeviceFinder.find()` 阻塞返回发现列表；应用界面应使用 `find_async()`。

串口句柄由 I/O 工作线程管理；Qt 自动连接的 `QObject` 槽在接收对象所属线程运行。
跨线程接收建议使用应用线程创建的 `QObject` 和 `@Slot` 方法，保持 Qt 事件循环运行，
并保留 handler/finder 对象。`send()` 可从任意线程调用，立即返回可取消的 ticket；
`send_and_wait()` 只允许在没有运行 Qt 事件循环的脚本线程调用。退出前调用
`disconnect()`，它会等待 I/O 线程停止，也挂接了 `aboutToQuit`。

## 无硬件 Demo

独立 Demo 在同一连接与发现路径上注入内存后端，并演示无结束符回复的静默定界：

```powershell
uv run python examples/demo.py
```

输出包含版本结果和 `spec_is_VERSION=True`。README 代码和 Demo 都由
`tests/test_demo.py` 实际运行；wheel 冒烟时在源码目录之外复制这两项验收输入。

## 添加命令

1. 在使用方代码、`examples/` 或 `tests/` 中定义一个 `CommandSpec`，包括请求文本、
   响应正则和可选的 dataclass 结果类型。
2. 用同一个对象调用 `handler.register(SPEC)`。库不会隐式注册命令。
3. 使用 `handler.send(SPEC)`，在 `command_finished` 中通过
   `result.spec is SPEC` 分支处理结果。

`request` 可以有 `{value}` 占位符，发送时用 `handler.send(SPEC, value=...)`。
`send("命令文本")` 先匹配已注册声明，匹配不到时默认作为原始文本发送；设置
`allow_raw_text=False` 可拒绝这种发送。参数和文本不能含控制字符或内嵌行终止符。
`pattern` 使用命名捕获组；不带 `pattern` 为 NO_REPLY，设置 `count` 或 `until` 为
MULTI，STREAM 必须显式指定。异常子类在 `serialforge.errors`。

业务命令不能放入 `src/serialforge/`。未匹配的主动上报通过 `event_received` 发送，
其中 `event.spec` 可能是已注册的 `EventSpec`、STREAM 命令或 `None`。

## 配置与日志

日常代码只需要 `DeviceProfile`；串口、定界和运行时细项通过 `serialforge.advanced`
中的 `SerialConfig`、`FramingConfig`、`RuntimeConfig` 配置。发送终止符和接收定界是
两个独立设置；没有结束符的回复使用 `FramingMode.SILENCE_GAP` 与正数
`silence_gap_s`。

库内日志只通过 loguru 以 DEBUG 级别输出，默认关闭。需要按连接保存文件时传入
`LogConfig(enabled=True, save_to_file=True, dir=...)`；日志目录不可写不会使连接崩溃。

## Windows 范围

支持 Windows 10/11 x64 和 64 位 Python。睡眠唤醒、USB 选择性挂起、驱动 DTR/RTS
脉冲、拔插重连和 TX-RX 回环必须按
[.agents/docs/knowledge/software/0003_Windows真机验证矩阵.md](.agents/docs/knowledge/software/0003_Windows真机验证矩阵.md)
中的矩阵由使用方验证；当前仓库没有宣称整张矩阵通过。PyInstaller 使用方还需自行
履行 PySide6 LGPL 合规义务。遇到挂起导致的掉线，可以检查 Windows 的 USB 选择性
暂停和设备电源管理选项；库不会修改系统设置。日志和波特率缓存使用 Qt 应用数据目录，
PyInstaller 打包后也不应改成程序目录。

## 开发与验收

```powershell
uv sync
uv run python scripts/ci.py --fast
uv run python scripts/ci.py
```

完整本地 CI 包含 Python 3.11–3.14 独立环境、格式、ty、pytest、pylint、构建、
twine 和干净 wheel 冒烟。`--lowest` 单独验证最低直接依赖；`--release` 包含全部检查。
`--smoke --wheel <路径> --python 3.11` 可在另一台机器对已构建 wheel 验收。
发布流程见
[.agents/docs/knowledge/software/0002_发布流程.md](.agents/docs/knowledge/software/0002_发布流程.md)。
本项目不生成云端 CI，不执行 PyPI 上传、推送或打 tag。

设计契约见 `串口通讯模块_方案_v10定稿.md`，AI 协作入口见 `AGENTS.md`。
