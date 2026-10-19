# 快速开始

> 适用版本: 0.0.1 | SerialForge 公共 API 改造

## 环境准备

使用 Windows 10/11 x64、64 位 Python 3.11–3.14，安装 `serialforge[pyside6]`
或 `serialforge[pyqt6]`。只加载一个绑定；同时安装时设置 QT_API 明确选择。
已有 GUI 应用先创建自己的 Qt 应用，再在主线程创建 SerialForge。

## 推荐用法（三步）

serialforge 的推荐形态是：**命令集中声明一次 → `send_async` 后在同函数内用闭包改界面 →
用 `scan_sync()` 的返回值 `connect_sync()`**。下面是可以直接对照改造的真实结构：

```python
from serialforge import Message, SerialForge
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("设备面板")

        # 控件（真实项目里自行布局）
        self.output = QLabel("结果显示区")
        self.query_button = QPushButton("获取波特率")
        self.connect_button = QPushButton("连接设备")
        self.status_label = QLabel("连接状态: 未连接")
        layout = QVBoxLayout(self)
        for widget in (self.output, self.query_button, self.connect_button,
                       self.status_label):
            layout.addWidget(widget)

        # ① 声明集中一处：先建门面，再把命令全部注册进来
        self.forge = SerialForge()
        self.VERSION = self.forge.add(
            request=">SN_INFO_GET=?",                       # request 不用自己写 CRLF
            pattern=r">SN_INFO_GET=(?!\?)(?P<sn>.+)",       # 负向预查排除请求回显
        )
        self.BAUD_RATE = self.forge.add(
            request=">BAUD_RATE_GET=?",
            pattern=r">BAUD_RATE_GET=(?!\?)(?P<baud>\d+)",
            result_type=int,                                # data 是 [921600]
        )

        self.query_button.clicked.connect(self.on_get_baud_rate)
        self.connect_button.clicked.connect(self.on_connect_device)

    # ② 发送与消费写在同一个函数里
    def on_get_baud_rate(self):
        def on_done(message: Message[int]) -> None:
            """成功回调：data 是只含一个值的列表。"""
            self.output.setText(str(message.data[0]))

        self.forge.send_async(self.BAUD_RATE).add_done_callback(on_done)

    # ③ 波特率 + 探针一次给全，再用扫描结果连接
    def on_connect_device(self):
        devices = self.forge.scan_sync(baudrates=[921600], probe=self.VERSION)
        if not devices:
            self.status_label.setText("连接状态: 未连接")
            return
        self.forge.connect_sync(devices[0])
        self.status_label.setText("连接状态: 已连接")


if __name__ == "__main__":
    app = QApplication([])
    window = MainWindow()
    window.show()
    app.exec()
```

三个要点：

- **闭包在主线程**：`add_done_callback` 的回调统一排队到 Qt 应用主线程，所以闭包可以直接读写控件，
  不用自己拆线程、加定时器或跨线程投递。
- **默认 3 秒且失败不回调**：期限从提交开始算，包含排队、设备等待与主线程投递；超时或失败**不会**
  执行成功闭包。需要失败分支时用 `call.result()`（重抛 `serialforge.errors` 里的异常）或自行兜底。
- **scan_sync/connect_sync 阻塞**：上面为了结构清晰写在槽函数里。真实项目可直接用 `scan_async()` /
  `connect_async()` 的 `add_done_callback()`，或把 `scan_sync()`/`connect_sync()` 放到应用自己的工作
  线程。门面不提供扫描进度信号，结果由返回值或调用句柄交付。

命令表与代码骨架能一一对应，就是这套声明方式最大的好处：加命令只改声明区一段，
发送处只引用对象；回调里还可用 `message.spec is self.BAUD_RATE` 判断是哪条命令的结果。

## 三种连接入口

`scan_sync()` 返回 `list[DeviceInfo]`（无匹配是 `[]`），不自动连接；`connect_sync()` 决定用哪种方式打开设备：

```python
devices = forge.scan_sync(baudrates=[115200, 921600], probe=PROBE)  # 阻塞，返回到此结束

forge.connect_sync(devices[0])   # 用扫描确认过的波特率直接打开，跳过重新探测
forge.connect_sync("COM11")      # 指定端口，绕过 USB 身份过滤但仍要探测波特率
forge.connect_sync()             # 按保存策略发现首个设备
```

`scan_sync(baudrates=..., probe=...)` 自带完整配置，不必先 `configure()`；也接受 `configure` 的字段形式、
字符串探针（须补 `probe_pattern`）或现成的 `DeviceProfile`。`ScanMode.FIRST_MATCH` 拿到第一个成功者
就取消其余探测；`cancel_scan()` 可在线程安全地取消。详见[使用指南](usage.md)。

## 同步等待

`send_sync(target, wait_timeout_s=3.0)` 返回 `Message`，失败直接抛类型化异常。它是正式交互接口，
适合工作线程或脚本；在主线程直接调用会阻塞界面。

```python
message = forge.send_sync(forge.add(">PING=?", r">PONG=(?P<rtt>\d+)", int))
print(message.data)
```

STREAM 只能异步发送，帧从 `received` 取得，详见[使用指南](usage.md)。

## 无硬件示例

```powershell
uv run python examples/quick_start.py
uv run python examples/auto_connect.py
uv run python examples/async_callbacks.py
uv run python examples/interactive.py
```

测试替身只在源码仓库的 `tests/support/`；客户 wheel 和 sdist 不包含它们。真实项目里把示例的连接
入口换成 `connect_sync("COM11")` 或扫描结果即可，流程完全一致。

## 常见问题

- **构造失败**：GUI 先建立 Qt 应用，再在它的主线程创建门面；纯脚本可显式 `application="core"`。
- **扫描找不到设备**：检查 `baudrates` 与探针正则；探针 pattern 不能匹配自己的请求回显（否则直接 `ConfigError`），
  已声明对象做探针还必须是 `SINGLE` 且无占位符。
- **收不到回调**：保持事件循环；超时/失败不执行成功闭包；用 `call.result()` 取失败原因，
  用 `call.callback_expired` 判断是否只是投递过期。
- **`add` 报冲突**：同一条 request（或事件 pattern）重复注册会抛 `CommandError`，声明集中在声明区即可避免。
- **界面卡住**：`connect_sync()`、`send_sync()` 会阻塞调用线程，请放在工作线程里执行；扫描与
  连接可分别用 `scan_async()` / `connect_async()` 避免阻塞。
- **无结束符设备**：发送终止符与接收定界独立，配置 `SILENCE_GAP`，见[使用指南](usage.md)。
