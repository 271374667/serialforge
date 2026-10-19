# 完整示例

> 这四个脚本像全家桶套餐，不需要你一次性吃完。挑一个最贴近你项目需求的，跑一遍，再回头看代码。

所有示例都依赖仓库自带的虚拟串口设备引擎（`examples/virtual_device.py`），在任何电脑上拉下代码就能直接跑，完全不需要真实硬件。

在真实硬件项目里，只要把代码里的 `VID_PID`、`BAUD_RATE` 和命令正则换成你自己设备的实际规约即可。

---

## 示例概览

| 文件路径 | 演示的核心特性 | 适合的场景 |
| --- | --- | --- |
| `examples/advanced/async_callbacks.py` | **推荐生产主干**：集中声明 + 异步扫描连接 + 主线程闭包消费 + STREAM 采集与取消 | 绝大多数标准化桌面 GUI 软件的骨架 |
| `examples/advanced/auto_connect.py` | 扫描参数（VID/PID 过滤、`FIRST_MATCH` 先到先得）与三种连接入口 | 需要自动化寻机、即插即用的现场测试工具 |
| `examples/advanced/quick_start.py` | 五种对话模式全家福（单收、多收、流式、主动上报、无回复） | 协议复杂、同时包含采集与控制的设备 |
| `examples/advanced/interactive.py` | 带实时命令提示符的交互式控制台控制终端 | 需要在控制台里手动敲指令排错的场景 |

用 `uv` 可以一键启动它们：

```powershell
uv run python examples/advanced/async_callbacks.py
uv run python examples/advanced/auto_connect.py
uv run python examples/advanced/quick_start.py
uv run python examples/advanced/interactive.py
```

---

## 1. 生产推荐主干：异步扫描、连接与闭包

先看 `async_callbacks.py`。它是官方最推崇的上位机工程结构：
- 在类的 `__init__` 里集中声明所有 `Spec`；
- 连接时走 `scan_async(...)`，在扫描完成的闭包里衔接 `connect_async(devices[0])`；
- 业务发送使用 `send_async(...).add_done_callback(闭包)`；
- 采集使用 `STREAM`，并通过紧急优先级发 `STOP` 与本地 `cancel()` 配合平稳收尾。

运行输出（节选）：

```text
[连接] SIM-DEVICE@115200

=== 1. add_done_callback 消费成功结果 ===
  回调: [VersionInfo(version='1.02')]  context=7
  done=True  callback_expired=False
  闭包 / result() 同一对象: True
  同期原始接收行: b'Software version 1.02\r\n'

=== 2. 超时不回调，失败从 result() 重抛 ===
  done=True  成功闭包执行次数=0
  result() 重抛 CommandTimeoutError

=== 3. STREAM 原始帧从 received 来，cancel() 收尾 ===
  流式原始帧: b'ADC 1\r\n'
  流式原始帧: b'ADC 2\r\n'
  流式原始帧: b'ADC 3\r\n'
  流式命令终态: cancelled
```

完整源码：

```python title="examples/advanced/async_callbacks.py"
--8<-- "examples/advanced/async_callbacks.py"
```

---

## 2. 自动连接与扫描参数实战

`auto_connect.py` 演示了不同扫描策略的真实耗时与行为表现：
- 省略 VID/PID 时，每个串口都会被敲门；
- 提供 VID/PID 时，无关端口在打开前就被精准剔除，扫描极速完成；
- `ScanMode.FIRST_MATCH` 只要捕获首台设备立刻收兵，不等后续端口；
- 拿到扫描返回的 `DeviceInfo` 直接连接，跳过二次探测。

运行输出（节选）：

```text
=== 异步扫描：省略 VID/PID（全部串口都会被探测） ===
  找到 ['SIM-DEVICE']

=== 异步扫描：给 VID/PID（名单外的端口打开前就被跳过，扫描更快） ===
  找到 ['SIM-DEVICE']

=== 异步扫描：ScanMode.FIRST_MATCH（拿到第一个成功者就取消其他探测） ===
  找到 ['SIM-DEVICE']

=== 连接入口：DeviceInfo(SIM-DEVICE@115200)，跳过重新探测 ===
  已连接 SIM-DEVICE，状态=connected
  收到: [{'name': 'serialforge'}]  原始帧: b'HELLO serialforge'
```

完整源码：

```python title="examples/advanced/auto_connect.py"
--8<-- "examples/advanced/auto_connect.py"
```

---

## 3. 五种对话模式全家福

`quick_start.py` 把日常能用到的五种响应模式串烧在一起演示。所有命令结果统一从异步闭包中取，而原始报文则通过 `received` 信号打印。

运行输出（节选）：

```text
=== 1. 单发单收（SINGLE）===
  请求文本: 'Version'
  解析结果: [VersionInfo(version='1.02')]  原始帧: b'Software version 1.02'

=== 2. 单发多收（MULTI）===
  count=3 第 1 帧: {'value': '1'}
  until=END 收到 2 行样本
  最后一帧: b'END'

=== 3. 流式输出（STREAM）===
  原始帧: b'ADC 1\r\n'
  流式命令终态: cancelled

=== 4. 主动上报（设备自己发的事件）===
  上报原始帧: b'TEMP 22\r\n'
  报警原始帧: b'ALARM 42\r\n'

=== 5. 无回复（NO_REPLY）===
  终态: ok  数据: []
  已写出字节: b'Report 0\r\n'
```

完整源码：

```python title="examples/advanced/quick_start.py"
--8<-- "examples/advanced/quick_start.py"
```

---

## 4. 实时交互控制台

`interactive.py` 提供了一个类似终端的交互式命令行：

```powershell
uv run python examples/advanced/interactive.py
```

敲入 `help` 可以看到所有支持的测试指令。你可以像在真实设备现场调试一样，手动输入命令，实时观察终端里带标签的回显。

运行示例：

```text
serialforge> Version
  [原始] Software version 1.02
  [结果] Version 状态=ok 数据=[VersionInfo(version='1.02')]
serialforge> monitor
  已开始采集；发 stop 或 status 结束
  [流] ADC 1
serialforge> stop
  [结果] Stop 状态=ok 数据=[]
  [结果] Monitor 状态=cancelled 错误=cancelled
```

完整源码：

```python title="examples/advanced/interactive.py"
--8<-- "examples/advanced/interactive.py"
```

遇到具体的方法签名或参数默认值，随时翻阅 [API 参考](../api.md)。
