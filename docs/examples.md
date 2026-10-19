# 示例

本页是仓库内**无硬件**可运行脚本的说明。写自己的程序时请以[快速开始](getting-started.md)里的
推荐用法为主线：命令集中声明一次、`send_async(...).add_done_callback(闭包)` 在主线程改界面、
`scan_sync(baudrates=..., probe=命令)` 后用返回的 `DeviceInfo` 连接。

`examples/` 下的脚本共用 `examples/virtual_device.py` 里的**虚拟串口设备**：假后台与指令集
只写一份，示例里就只剩 `SerialForge` 门面的调用（声明、连接、发送）。
真实项目里把无参 `device.connect_sync()` 换成 `device.connect_sync("COM11")`，或用
`device.scan_sync()` 拿到 `DeviceInfo` 再连接即可，流程完全一致。

| 文件 | 演示内容 |
| --- | --- |
| `examples/virtual_device.py` | 共用的虚拟设备：指令集、声明与内存后端（其他示例都引用它） |
| `examples/quick_start.py` | 五种取信息方式：单发单收、单发多收、流式输出、主动上报、无回复 |
| `examples/auto_connect.py` | 阻塞扫描的参数与模式（`ALL` / `FIRST_MATCH`、VID/PID 可选），三种 `connect_sync()` 入口与 `send_sync()` |
| `examples/async_callbacks.py` | 推荐的异步消费方式：`add_done_callback()` 主线程闭包、失败不回调、STREAM + `cancel()` |
| `examples/interactive.py` | `while` + `input()` 实时交互控制台（流式、主动上报、原始文本） |

四个示例都是面向对象的：逻辑收在一个类里，模块顶层除了 `if __name__ == "__main__":`
没有可执行代码。测试替身只在源码仓库里，不随 wheel 或 sdist 分发。

## 快速启动：五种取信息方式

`connect_sync()` 阻塞到设备就绪，返回后即可发送。示例里手动泵事件只用来接收信号——
结果与事件由 I/O 工作线程发出、槽在主线程，需要转一次事件循环才会被调用。

```powershell
uv run python examples/quick_start.py
```

输出（节选）：

```text
[连接] connected

=== 1. 单发单收（SINGLE）===
  请求文本: 'Version'
  解析结果: VersionInfo(version='1.02')  原始帧: b'Software version 1.02'

=== 2. 单发多收（MULTI，count=3）===
  第 1 帧: {'value': '1'}
  帧数: 3

=== 3. 流式输出（STREAM）===
  帧: {'value': '1'}
  流式命令终态: cancelled

=== 4. 主动上报（Spec）===
  事件: Alarm(code='42')  原始帧: b'ALARM 42'

=== 5. 无回复（NO_REPLY）===
  终态: ok  数据: None
```

```python title="examples/quick_start.py"
--8<-- "examples/quick_start.py"
```

## 自动连接设备

示例里的 `scan_sync()` 与 `connect_sync()` 都是阻塞调用，返回就意味着扫描结束或设备已就绪。扫描结果只由
返回值给出——门面没有扫描进度信号；界面应用可改用 `scan_async()` 的 `add_done_callback()`。
示例对照了“省略 VID/PID / 给 VID/PID / 只等第一个”三种扫描方式，以及三种 `connect_sync()` 入口，
并用阻塞的 `send_sync()` 直接取结果。

```powershell
uv run python examples/auto_connect.py
```

输出（节选）：

```text
=== 阻塞扫描：省略 VID/PID（全部串口都会被探测） ===
  找到 ['SIM-DEVICE']

=== 阻塞扫描：给 VID/PID（名单外的端口直接跳过） ===
  找到 ['SIM-DEVICE']

=== 阻塞扫描：ScanMode.FIRST_MATCH（拿到第一个成功者就取消其他探测） ===
  找到 ['SIM-DEVICE']

=== 连接入口：DeviceInfo(SIM-DEVICE@115200)，跳过重新探测 ===
  已连接，状态=connected
  收到: {'name': 'serialforge'}  原始帧: b'HELLO serialforge'

=== 连接入口：端口名 SIM-DEVICE，绕过 VID/PID 过滤 ===
  已连接，状态=connected
  收到: {'name': 'serialforge'}  原始帧: b'HELLO serialforge'

=== 连接入口：无参，自动筛选并按探测指令确认设备 ===
  已连接，状态=connected
  收到: {'name': 'serialforge'}  原始帧: b'HELLO serialforge'
```

```python title="examples/auto_connect.py"
--8<-- "examples/auto_connect.py"
```

## 推荐的异步消费

文档推荐的异步消费方式是 `send_async(...).add_done_callback(consume)`：闭包统一排队到 Qt
应用主线程，可以直接更新界面，不必像脚本示例那样手动泵事件。示例覆盖成功、失败与流式三条
路径，并演示超时不回调、失败从 `result()` 重抛。

```powershell
uv run python examples/async_callbacks.py
```

输出（节选）：

```text
=== 1. add_done_callback 消费成功结果 ===
  回调: VersionInfo(version='1.02')  context=7
  done=True  callback_expired=False
  闭包 / received / result() 同一对象: True

=== 2. 超时不回调，失败从 result() 重抛 ===
  done=True  成功闭包执行次数=0
  result() 重抛 CommandTimeoutError

=== 3. STREAM 帧从 received 来，cancel() 收尾 ===
  流式帧: {'value': '1'}
  流式命令终态: cancelled
```

```python title="examples/async_callbacks.py"
--8<-- "examples/async_callbacks.py"
```

## 实时交互

```powershell
uv run python examples/interactive.py
```

输入 `help` 可以看到命令表：`Version`、`ReadAll`、`monitor` / `stop`、`alarm`、
`raw Ping`、`quit`。

```text
serialforge> Version
  [结果] Version 状态=ok 数据=VersionInfo(version='1.02')
serialforge> monitor
  已开始流式输出，输入 stop 结束
  [流] {'value': '1'}
serialforge> stop
  已请求停止流式输出
  [结果] Monitor 状态=cancelled 数据=None
```

`input()` 会阻塞 Qt 事件循环，所以该示例在每次输入后手动泵一小段时间事件；真实 GUI 程序
应该把发送与接收都做成信号槽，不要在事件线程里读标准输入。

```python title="examples/interactive.py"
--8<-- "examples/interactive.py"
```

## 共用的虚拟设备与指令集

模块顶部就是完整的指令集表（请求、应答、响应形态、对应声明）；示例引用的正是这里的
同一份声明和应答表。

```python title="examples/virtual_device.py"
--8<-- "examples/virtual_device.py"
```
