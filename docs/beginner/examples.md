# 简单示例

> 两个极简脚本，一个像直接打电话，一个像点外卖等外卖员敲门。不需要任何真实硬件就能直接跑。

仓库自带的 `examples.qt_app` 是为了同时在 PySide6、PyQt6 和 PyQt5 的持续集成环境里跑通而准备的垫片；在你自己实际的项目里，直接按习惯导入 `PySide6.QtCore` 里的应用类就行，不需要复制这层垫片。

---

## 1. 同步版：打电话（拨通、对话、挂机）

同步代码是一步接一步执行的：调用 `send_sync()` 之后，当前线程会老老实实等设备把回复送回来才继续往下走。

这种写法条理最清晰，最适合写自动化测试、数据采集脚本或者独立的工作者线程（Worker Thread）。不过千万别把它放在 GUI 按钮的回调里，否则你的界面在设备回话前会瞬间“假死”。

```python
from serialforge import SerialForge

# 创建门面并声明命令
forge = SerialForge(application="core")
PING = forge.add(request="PING", pattern=r"PONG")

# 扫描并连接
devices = forge.scan_sync(baudrates=[115200], probe=PING)
if devices:
    forge.connect_sync(devices[0])
    response = forge.send_sync(PING)
    print(response.data[0])  # 'PONG'
    forge.disconnect()
```

完整的可执行脚本（内建虚拟串口支持）：

```python title="examples/simple/first_command.py"
--8<-- "examples/simple/first_command.py"
```

运行输出：

```text
已连接 SIM-DEVICE，版本 1.02
```

---

## 2. 异步版：点外卖（下单、各忙各的、送达通知）

异步写法是桌面 GUI 程序的标准姿势：把“扫描”、“连接”、“发送”后的动作各写成一个闭包回调。

你在主线程下达指令后，函数立刻返回，界面该画按钮画按钮、该弹动画弹动画；后台 I/O 线程收到设备回包并解析完成后，会自动把结果排队送回 Qt 主线程触发你的回调函数。

```python title="examples/simple/async_callback.py"
--8<-- "examples/simple/async_callback.py"
```

运行输出：

```text
已连接 SIM-DEVICE

版本 1.02
```

这就是写上位机软件最舒适的骨架结构。当你需要处理多行列表读取、连续波形图绘制、异常报警拦截或者断线重连时，再翻开 [进阶指南 · 完整示例](../advanced/examples.md)；一口吃不成胖子，按需查阅即可。
