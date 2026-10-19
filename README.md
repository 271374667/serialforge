# SerialForge

> 写给那些只想在 Qt 界面上点个按钮读写串口，却被多线程、分包断句、乱码和界面卡死折磨到怀疑人生的开发者。

开发上位机串口程序，大概是每个写 GUI 的人迟早都要趟一遍的浑水。

如果用原生方案写，你的代码通常会长成这样：开一个 `QThread`，在死循环里用 `pyserial.read()` 啃字节；因为串口本质上是流水，一口气读出来的 50 个字节可能前半截是上一条回复的尾巴，中间混着设备被踢掉线时的乱码，后半截才是刚刚发出的回包；你得手写状态机拼接 `\r\n`，再把文本拆开转成浮点数；如果设备还喜欢复读你的命令（回显），或者冷不丁主动报个警，你还得区分哪个是回复、哪个是上报；最后还要通过自定义的 Qt Signal 辛辛苦苦把数据丢回主线程更新界面。

一旦有人拔掉 USB 线，或者遇上一个打不开的虚拟蓝牙串口，整个程序直接在 `open()` 里面假死，Windows 标题栏挂上灰色的“未响应”。

**SerialForge 就是为了消灭这几百行脏代码而生的。**

你只管声明业务逻辑：“发什么文本、按什么正则匹配、帮我转成什么类型”。至于底层的后台 I/O 线程、断句切帧、排队限流、超时兜底、断线重连，以及把解析好的数据直接送到 Qt 主线程闭包，全部由库包办。

```powershell
uv add serialforge              # 项目里已经有 Qt（PySide6 / PyQt6 / PyQt5），装本体就够
uv add "serialforge[pyside6]"   # 环境里一个 Qt 都没有？让它顺手带一份 PySide6 或者 serialforge[pyqt6] / serialforge[pyqt5]
```

SerialForge 本体**不带 Qt**，只借用你环境里已有的 `QtCore`。所以你原本用哪套绑定，装完 SerialForge 就继续用哪套，不会被悄悄换掉或升级；只有当你手上连 Qt 都没有时，才需要 extra 代劳。

---

## 10 秒钟看懂它是怎么工作的

假设你的设备协议很简单：发 `PING`，回 `PONG`。

```python
from serialforge import SerialForge

def read_ping(forge: SerialForge) -> str:
    """扫描、连接、发一条命令，返回设备回复。"""
    PING = forge.add(request="PING", pattern=r"PONG")
    devices = forge.scan_sync(baudrates=[115200], probe=PING)
    if not devices:
        return ""
    forge.connect_sync(devices[0])
    try:
        return forge.send_sync(PING).data[0]
    finally:
        forge.disconnect()
```

纯控制台脚本传 `SerialForge(application="core")` 即可自动托管事件循环；GUI 程序在已有 `QApplication` 的主线程直接创建 `forge = SerialForge()`。

如果是在 Qt GUI 界面里，不要在主线程用 `_sync` 堵路，换成对应的 `_async` 方法加闭包回调即可。你的回调函数**百分之百在 Qt 主线程里执行**，拿到了数据直接调 `label.setText()`，绝不卡界面。

---

## 几个让你省下大把时间的设计

### 1. 正则匹配 + 自动转类型，告别手撕字符串
很多协议长成 `TEMP: 24.8` 这样。以前你得拿正则匹配，取出 group(1)，再用 `float()` 强转，还要套 try-except 防炸。

在 SerialForge 里，支持直接绑定数值或 Python `@dataclass`：

```python
from dataclasses import dataclass
from serialforge import SerialForge

@dataclass
class SensorData:
    channel: int
    voltage: float

def declare_commands(forge: SerialForge):
    # 想要浮点数：正则捕获的第一组数字自动转 float
    GET_TEMP = forge.add("GET_TEMP", r"TEMP:\s*(?P<val>[\d\.]+)", result_type=float)

    # 想要强类型结构体：命名捕获组自动映射到 dataclass 字段
    READ_ADC = forge.add(
        "READ_ADC",
        r"CH(?P<channel>\d+):(?P<voltage>[\d\.]+)V",
        result_type=SensorData,
    )
```

### 2. 带着上下文发命令（Context）
在表格界面里，用户点了第 15 行的“刷新”按钮。300 毫秒后设备回包了，你的回调怎么知道该更新表格的第几行？

以前你可能得在窗体类里搞个全局字典记住 request_id，生怕并发一高把行号搞窜。现在直接把你的私货塞进 `context`：

```python
from serialforge import SerialForge, Spec

def send_with_context(forge: SerialForge, query: Spec[dict]):
    # context 是一份只读便签，不会发到线路上，但会随结果原样返回
    call = forge.send_async(query, context={"table_row": 15})
    call.add_done_callback(
        lambda msg: print(f"更新第 {msg.context['table_row']} 行: {msg.data[0]}")
    )
```

### 3. 急事可以插队（URGENT 优先级）
队列里排了 10 条轮询传感器数据的指令，操作员突然按了红色的“紧急停机”按钮。如果按传统的 FIFO 队列慢慢排，等轮到停机命令时黄花菜都凉了。

给紧急命令加个高优先级，它立刻插到队列最前头：

```python
from serialforge import ResponseMode, SerialForge
from serialforge.advanced import CommandPriority

def emergency_stop(forge: SerialForge):
    # 停机指令直接加塞到队列最前头，且不等回复
    STOP = forge.add("STOP", mode=ResponseMode.NO_REPLY)
    forge.send_async(STOP, priority=CommandPriority.URGENT)
```

### 4. 知道 USB 身份时，扫描快到飞起
很多人扫描串口很慢，是因为电脑上插了各种蓝牙转串口、虚拟调制解调器，或者驱动稀烂的设备，打开一次就要等一两秒。

如果你知道自己设备的 USB 芯片型号（VID/PID），直接告诉扫描器：

```python
from serialforge import SerialForge, Spec

def fast_scan(forge: SerialForge, ping_probe: Spec[str]):
    # 传入芯片 VID/PID，跳过无关端口，扫描极速完成
    call = forge.scan_async(
        baudrates=[115200],
        probe=ping_probe,
        vid_pid=[(0x1A86, 0x7523)],  # 比如只敲 CH340 的门
    )
    call.add_done_callback(lambda devices: print(f"找到 {len(devices)} 台设备"))
```
扫描时间直接从 5 秒跌到几十毫秒。

### 5. 失败不调成功回调
许多库喜欢不管成功失败都调同一个回调，让用户在回调里写一堆 `if result.is_error(): return`。

SerialForge 的策略很干脆：`add_done_callback` 注册的闭包**只在真正成功收到预期回复时触发**。如果超时、设备掉线或者通信出错，成功回调压根不会执行。想看异常细节，去调 `call.result()`，它会把现场原原本本地重抛给你。

---

## 示例代码目录

仓库 `examples/` 目录下带了一台内建的虚拟串口设备，不需要任何硬件，敲命令就能跑：

```powershell
# 简单入门
uv run python examples/simple/first_command.py      # 同步阻塞最小脚本
uv run python examples/simple/async_callback.py     # 异步回调最小脚本

# 进阶特性
uv run python examples/advanced/async_callbacks.py  # 生产推荐写法：异步发现 + 闭包处理 + STREAM
uv run python examples/advanced/auto_connect.py     # 扫描模式与三种连接姿势
uv run python examples/advanced/quick_start.py      # 单发单收、多收、流式、上报、无回复五种模式
uv run python examples/advanced/interactive.py      # 带命令行界面的交互控制台
```

---

## 纯洁与边界

- **只依赖 QtCore**：绝不导入 `QtWidgets` 或 `QtGui`。你用它写无界面的后台服务、控制台小工具，还是搭配庞大的桌面 UI，全凭你心意。
- **导入无副作用**：`import serialforge` 不会擅自起线程、不会开串口、不会动文件系统，更不会弹窗。
- **支持三套绑定**：PySide6、PyQt6、PyQt5 都可以。如果你同时装了多个，设置环境变量 `$env:QT_API="pyside6"` 指定即可。
- **环境要求**：Windows 10/11 x64，Python 3.11–3.14（64位）。

---

## 完整文档

更详细的指南在在线文档站点：<https://271374667.github.io/serialforge/>

- [新手入门 · 快速开始](docs/beginner/getting-started.md)：手把手搭建第一个工程
- [进阶指南 · 声明与响应模式](docs/advanced/declarations.md)：单发多收、流式持续采集与主动上报
- [进阶指南 · 定界与编码](docs/advanced/framing.md)：遇到不说“句号”的 Modbus 或二进制设备怎么办
- [附录 · 串口与上位机入门](docs/appendix/serial-primer.md)：刚接手硬件项目的纯软件开发者先看这个
- [附录 · 术语表](docs/appendix/glossary.md)：波特率、CRLF、探针、8N1 的人话速查
