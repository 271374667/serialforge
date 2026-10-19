# 快速开始

> 这篇指南带你在 5 分钟内让代码与设备完成第一次握手。

初次接触硬件通信，容易被密密麻麻的专业名词吓退。其实把串口扒光了看，它就跟打网络电话差不多：两边拨通号码，约定好语速，你问一句我答一句。

如果“串口、波特率、上位机”对你来说还很陌生，花两分钟扫一眼 [串口与上位机入门](../appendix/serial-primer.md)，遇到生词再去查 [术语表](../appendix/glossary.md)。

---

## 0. 先搞清三个核心概念

- **[串口](../appendix/glossary.md#serial-port)**：Windows 设备管理器里的 `COM3`、`COM7`，就像一条窄窄的通信单车道。
- **[波特率](../appendix/glossary.md#baudrate)**：双方讲话的语速，常见的是 `115200` 或 `9600`。两边语速没对上，听到的一定是乱码。
- **[帧](../appendix/glossary.md#frame)**：协议里的“完整一句话”。串口线本身只管吐字节，没有标点符号；哪几个字节凑成一句话，全靠换行符或者停顿来切分。

你可以把串口想象成一条传送带，波特率是传送带的速度，一帧数据就是上面的一个快递盒。serialforge 负责在传送带末端帮你拆开盒子、核对条码，再把里面的物件递到你手上。

---

## 1. 安装与环境准备

准备好 64 位的 Windows 10/11，以及 Python 3.11–3.14 环境，通过 `uv` 安装你偏好的 Qt 绑定：

```powershell
uv add "serialforge[pyside6]"   # 推荐 PySide6；也可以选 serialforge[pyqt6] 或 serialforge[pyqt5]
```

serialforge 本身只依赖轻巧的 `QtCore`，绝不会偷偷弹窗或引入笨重的界面库。

### 关于 Qt 应用程序实例
因为 serialforge 依赖 Qt 的事件循环把消息送回主线程，在创建 `SerialForge()` 前，请确保你的进程里已经有了 Qt 应用程序实例：

- **桌面 GUI 程序**：你在界面入口处早就有 `app = QApplication(sys.argv)` 了，直接在主线程创建 `SerialForge()` 即可。
- **命令行脚本或测试**：如果只想写个无界面的轻量脚本，可以用最省事的一行写法：
  ```python
  forge = SerialForge(application="core")  # 库会在背后自动建一个轻量 QCoreApplication，省去导入步骤
  ```
  或者你也可以手动写 `from PySide6.QtCore import QCoreApplication; app = QCoreApplication([])`。

---

## 2. 声明一条命令

在 serialforge 里，你不需要在每个地方拼字符串，而是像做菜单一样，集中声明每条命令长什么样：

```python
# 声明一条发 PING、期待回 PONG 的简单命令
PING = forge.add(
    request="PING",   # 发出去的文本（末尾的 \r\n 库会自动追加，不用手写）
    pattern=r"PONG",  # 设备回复的正则表达式（匹配前换行符已经被剔除了）
)
```

### 想要直接拿数值或对象？
这是 serialforge 最讨喜的地方之一。通过 `result_type` 参数，你可以让它自动完成类型提取：

```python
# 想要浮点数：正则捕获到的第一组数字会自动 float()
GET_VOLTAGE = forge.add("GET_VOLT", r"VOLT:\s*([\d\.]+)", result_type=float)

# 想要键值对字典：正则使用命名捕获组 (?P<name>...)
GET_INFO = forge.add("INFO", r"FW:(?P<firmware>[\w\.]+)\s+SN:(?P<sn>\w+)", result_type=dict)
```

声明对象是不可变的，在内存里只保留这一份。在后续的回调函数中，你可以放心地用 `message.spec is GET_VOLTAGE` 来比对身份，绝不会有二义性。

---

## 3. 同步调用：扫描、连接与发送

对于脚本、命令行工具或者你自己开的应用工作线程，同步阻塞写法最直观：

```python
from serialforge import SerialForge

# 1. 自动启用轻量控制台 Qt 环境
forge = SerialForge(application="core")

# 2. 声明命令
PING = forge.add(request="PING", pattern=r"PONG")

# 3. 扫描并自动敲门
# 串口不像 U 盘那样能报上大名，因此需要拿 PING 当探针去敲敲门，看谁会乖乖回答 PONG
devices = forge.scan_sync(baudrates=[115200], probe=PING)
if not devices:
    raise SystemExit("没有找到能够回应 PONG 的设备")

# 4. 连接设备
forge.connect_sync(devices[0])
print(f"成功连接设备: {devices[0].port_name}")

# 5. 发送并同步获取结果
response = forge.send_sync(PING)
print("收到应答:", response.data[0])  # 'PONG'

# 6. 收工关闭
forge.disconnect()
```

几点贴心细节：
- 如果某个 COM 口被别的程序占用、或者驱动假死，`scan_sync` 会直接优雅地跳过它，绝不会抛出大段诡异异常砸在用户脸上。
- 如果你知道设备的 USB 芯片型号，例如 CH340 或 FT232，可以在扫描时加上 `vid_pid=[(0x1A86, 0x7523)]`。库会在敲门前直接过滤掉无关端口，扫描时间从几秒瞬间缩短到几十毫秒。

---

## 4. 界面开发：换成异步闭包，拒绝界面卡死

在图形界面开发中，直接在主线程调用 `send_sync()` 或 `scan_sync()` 是一大忌讳——只要设备回包慢了半秒，你的按钮就会卡住，整个窗口陷入未响应。

换成异步方法后，一切都变得如丝般顺滑：

```python
# 1. 发起扫描，找到设备后自动触发 on_scanned 回调
def on_scanned(devices: list[DeviceInfo]) -> None:
    if not devices:
        self.status_label.setText("未找到设备")
        return
    # 2. 找到设备后发起异步连接
    forge.connect_async(devices[0]).add_done_callback(on_connected)

def on_connected(device: DeviceInfo) -> None:
    self.status_label.setText(f"已连接: {device.port_name}")
    # 3. 连接成功后发送指令
    call = forge.send_async(PING)
    call.add_done_callback(on_ping_done)

def on_ping_done(message: Message[str]) -> None:
    # 闭包百分之百在 Qt 主线程里执行！直接更新界面毫无压力
    self.reply_label.setText(f"设备在线，回包: {message.data[0]}")

# 触发扫描
forge.scan_async(baudrates=[115200], probe=PING).add_done_callback(on_scanned)
```

### 为什么说“失败不回调”是好设计？
`add_done_callback` 注册的闭包，只有在**命令完全成功**（收到预期格式的回复）时才会执行。

如果设备超时没回话，或者中途被拔掉了，成功闭包根本不会跑。这样你就完全不需要在每个回调开头写一长串让人厌烦的 `if not message.ok: return`。

如果你确实需要处理超时或错误，可以通过句柄的 `call.result()` 来捕获现场抛出的异常，或者用一个 `QTimer` 做超时后的界面提示。

---

## 5. 本地免硬件试跑

仓库里自备了虚拟串口模拟器，哪怕你手头一块板子都没有，也能马上跑起真实的数据流：

```powershell
uv run python examples/simple/first_command.py      # 同步控制台版
uv run python examples/simple/async_callback.py     # 异步主线程闭包版
uv run python examples/advanced/quick_start.py      # 涵盖 5 种响应模式的进阶范例
```

---

## 常见疑问自查

- **提示没有 Qt Application？**
  `SerialForge` 必须在 Qt 应用程序实例化之后才能创建。GUI 窗口先启动 `QApplication`，控制台脚本直接传 `SerialForge(application="core")`。
- **扫描一直返回空列表？**
  检查两处：波特率是否匹配；探针的正则表达式千万不能匹配发出的请求本身。比如你发 `PING`，但正则写了个宽松的 `P.*`，库会以为这是串口物理回显而直接丢弃。
- **界面莫名卡顿？**
  检查有没有不小心在 GUI 按钮槽里直接写了 `scan_sync` 或 `send_sync`。在界面主线程里，永远认准带 `_async` 的方法。

深入探索：
[进阶指南 · 声明与响应模式](../advanced/declarations.md) · [简单示例](examples.md) · [API 参考](../api.md)
