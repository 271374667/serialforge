# serialforge

> 适用版本: 0.0.1 | 公共 API 改造 R1–R5

面向 Windows 10/11 x64、Python 3.11–3.14 的串口通讯库。仅使用 QtCore，
支持 PySide6 和 PyQt6；导入不会创建应用、线程、串口、文件或日志 sink。
设备指令由使用方声明，库负责发现、调度、解析、重连和回调。

```powershell
uv add "serialforge[pyside6]"  # 或 serialforge[pyqt6]
```

同时安装两种绑定时，启动前设置 `QT_API=pyside6` 或 `QT_API=pyqt6`；已加载的
绑定与设置必须一致。本轮保持 Python 3.11 下限，未加入 Qt5 支持。

## 最小示例

仅通过 `SerialForge` 完成配置、声明、连接和同步取结果。以下可在仓库中直接运行；
`tests.support` 只提供无硬件演示后端，不进入客户 wheel/sdist。

```python
from serialforge import SerialForge
from tests.support import FakeBackend, SimulatedDevice, use_fake_backend

backend = FakeBackend([
    SimulatedDevice(None, None, port="SIM-README", responses={
        b"Version\r\n": b"VERSION:1.02\r\n",
    }),
])
with use_fake_backend(backend):
    device = SerialForge(application="core")
    device.configure(baudrates=[115200], probe="Version",
                     probe_pattern=r"VERSION:.+")
    version = device.add("Version", r"VERSION:(?P<version>.+)", dict)
    device.connect("SIM-README")
    try:
        result = device.send_sync(version, context={"row": 1})
        assert result.spec is version
        assert result.data == {"version": "1.02"}
        print(result.data, result.context)
    finally:
        device.disconnect()
```

真机移除假后端上下文，端口改为真实端口或无参自动发现。GUI 中先创建自己的
Qt 应用，再在主线程创建 `SerialForge()`。`connect()` 和 `scan()` 阻塞返回；
要保持界面响应，由应用自己的工作线程调用，扫描没有完成或进度信号。
`scan(baudrates=[115200], probe="Version", probe_pattern=r"VERSION:.+")`
直接配置并返回 `list[DeviceInfo]`；也支持已有 `DeviceProfile`，扫描不会自动连接。

## 声明与发送

`device.add(spec)` 返回传入的原对象；`device.add("Read", r"VALUE:.+")`
则内部构建统一 `Spec`。响应命令的 request/pattern 必填，默认 mode=SINGLE；
默认 result_type=str 返回整段匹配帧，命名组字典须显式传 dict。事件写成
`device.add(pattern=r"ALARM:.+")`；只发送命令必须显式 mode=ResponseMode.NO_REPLY。
MULTI 必须显式选择模式，并在 count>0 与非空 until 中选一个。可选值缺省为
count=-1、until/error_pattern/parser=""、total_timeout_s=-1.0，不接受 None。

`send_sync(target, wait_timeout_s=3.0, params=..., context=...) -> Message`
和 `send_async(target, timeout_s=3.0, params=..., context=...) -> CommandCall`
处于同级。target 可以是已注册声明或完整命令文本，文本不接受额外 params。
占位符参数通过 params 字典传递；context 是每次调用独立的只读浅快照，
保留在返回消息和调用句柄中，不发送到设备，也不参与协议匹配。

异步消费推荐 `device.send_async(spec, context={"row": row}).add_done_callback(consume)`。
consume 是只接收一个 Message 的函数，闭包可捕获控件或其他状态。回调始终排队到
Qt 应用主线程，必须保持事件循环。默认 3 秒从提交开始，包含排队、响应与 GUI
回调投递；超时/失败不调用成功回调。结果已成功但 GUI 堵塞超过期限时，result()
仍可取成功结果，闭包不再运行。关闭界面时 cancel()，释放其待执行闭包。

同步失败直接抛出 `serialforge.errors` 的执行异常；异步失败通过 call.result()
重抛，未完成查询抛 CommandNotReadyError。STREAM 只能异步发送，帧从 received
取得，直到 cancel() 或调用时限结束。未归属命令的后台错误由 check_errors() 抛出。

## 收发观察与日志

门面仅公开三个信号：`connection_state_changed(ConnectionState)`、
`received(Message)` 和 `raw_sent(bytes)`。received 的 category 区分 RAW_RECEIVE、
RESPONSE_FRAME、COMMAND_RESULT、DEVICE_EVENT、STREAM_FRAME、UNKNOWN_FRAME。
结果信号与同步返回/异步 result()/回调共享同一个最终 Message 对象。

滚动日志只选 RAW_RECEIVE 的 raw_data 和 raw_sent 的 bytes：这是完整实际读写字节，
包含结束符、回显、探测、初始化、心跳和重试。raw_sent 只含底层确认接收的字节，
部分写入只记录接收的前缀。解析帧和终态来自同一物理回复，混入日志会重复。
并发扫描的裸 TX bytes 没有端口字段。两个观察信号在主线程投递，独立于日志开关。
traffic_logged 不再作为公开信号；库内 loguru DEBUG/文件日志保持默认静默。

## 示例与兼容迁移

```powershell
uv run python examples/quick_start.py
uv run python examples/auto_connect.py
uv run python examples/interactive.py
```

过渡期顶层保留原 15 个名字，再加 SerialForge/Spec/Message/MessageCategory，共 19 个。
日常使用统一 API；进阶配置在 `serialforge.advanced`，异常在 `serialforge.errors`。
SerialHandler 保留 register/unregister/send/send_and_wait 方法桥接，但不再提供旧结果、
事件、错误和流量信号。旧 CommandSpec/EventSpec 构造及身份语义仍可用；新返回值
统一为 Message，不保证旧结果类型的 isinstance。下一轮移除兼容入口的版本由用户决定。

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
