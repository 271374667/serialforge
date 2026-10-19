# SerialForge

> 写给那些懂 Qt、却第一次接手串口通信，或者已经被串口多线程虐过千百遍的你。

你在 Qt 里画好了界面，按钮摆得整整齐齐，信号槽也接得赏心悦目。老板突然抱来一块电路板插进电脑：“来，点这个按钮，让板子亮个灯，顺便把温度读上来。”

你满口答应，翻出 `pyserial` 开始写代码。半小时后，你很可能会遭遇以下“串口下马威”：

1. **界面卡成PPT**：你直接在按钮点击事件里调了 `serial.read()`，然而设备慢悠悠过了 500 毫秒才回包，这半秒钟里你的窗口整个发白，标题栏写着刺眼的“未响应”。
2. **字节是一盆乱炖**：串口没有“行”的概念，它是一根流式水管。你以为发一条命令就能整整齐齐读回一行文本，结果第一次读出来半截，第二次读出来后半截连带着下一次的开头。
3. **复读机与插嘴怪**：有些设备喜欢回显——你发 `PING`，它先冲你学一句 `PING`，再给你回 `PONG`；还有些设备会在你发命令的当口，冷不丁自己上报一条 `ALARM: HIGH TEMP`，把你的正常回包顶到了九霄云外。
4. **找不到设备在哪个口**：电脑上一口气挂了 4 个 COM 口，你不知道板子到底插在 COM3 还是 COM8，只能让用户在下拉框里一个个手选。

**SerialForge 就是为了替你摆平这些脏活而写的。**

你只需要像写菜单一样声明业务：“我发这条命令，它应该回长成这样的正则，请帮我解析好”。接下来的事情——后台独立线程读写、按行或静默间隔分帧、正则匹配提取、数据类型转换、超时监控、故障隔离，以及**把结果完好无损地送回 Qt 主线程**——统统交给库来处理。

---

## 30 秒尝鲜

假设你的板子很简单：发一句 `PING`，回一句 `PONG`。

```python
from PySide6.QtCore import QCoreApplication
from serialforge import SerialForge

# 1. 创建 Qt 核心应用与门面（GUI 界面直接复用现有的 QApplication 即可）
app = QCoreApplication([])
forge = SerialForge()

# 2. 声明一条命令（不用操心 \r\n 换行符，库会自动补齐与剔除）
PING = forge.add(request="PING", pattern=r"PONG")

# 3. 扫描附近的端口，挨个拿 PING 去敲门，看谁会礼貌回 PONG
devices = forge.scan_sync(baudrates=[115200], probe=PING)
if not devices:
    raise SystemExit("敲遍了所有 COM 口，没有设备理我")

# 4. 连接找到的第一个设备，发送指令并拿到结果
forge.connect_sync(devices[0])
response = forge.send_sync(PING)

print("设备回复:", response.data[0])  # 'PONG'

forge.disconnect()
```

上面这段是同步写法，非常适合在控制台脚本或单独的工作线程里跑。

如果是在带界面的 GUI 程序里，只要把 `_sync` 换成对应的 `_async`，再挂一个 `add_done_callback` 闭包就行。回调函数百分之百在 Qt 主线程里触发，拿到数据直接调用 `label.setText()`，主线程一毫秒都不会卡。

---

## 它帮你搞定了哪些糟心事

- **智能敲门寻机**：给定一组候选[波特率](appendix/glossary.md#baudrate)，用预设的[探针](appendix/glossary.md#probe)自动探测。知道芯片的 USB [VID/PID](appendix/glossary.md#vid-pid) 还能先过滤无关端口，避免把时间浪费在无辜的蓝牙串口上。
- **自动分包断句**：串口数据流水一般涌来，库负责根据换行符或静默停顿切出完整的[帧](appendix/glossary.md#frame)，拼装好再交给你。
- **正则与数据绑定**：支持用命名正则捕获组提取数据。你可以直接指定 `result_type=int`、`float`、`dict`，甚至是自定义的 `@dataclass`，免去手工切字符串的繁琐。
- **绝对的主线程安全**：无论底层的串口读取线程多忙碌，交付给你的回调与信号全部排队投递到 Qt 主线程，更新界面不需要自己琢磨线程锁。
- **干净的错误与超时**：命令发出后默认 3 秒期限，超时自动清理现场，绝不让迟到的幽灵数据窜到下一条指令里。

---

## 接下来去哪

如果你刚拿到硬件，很多概念还摸不着头脑：
- 先花几分钟翻翻 [串口与上位机入门](appendix/serial-primer.md)，把上位机、波特率、CRLF 这些概念建立起清晰的图景。
- 看代码遇到不懂的词，随时查阅 [术语表](appendix/glossary.md)。

如果你准备开始动手：
- [快速开始](beginner/getting-started.md)：5 分钟完整跑通第一个工程。
- [简单示例](beginner/examples.md)：同步与异步两个极简短脚本。
- [进阶指南](advanced/index.md)：按功能深入了解多行响应、流式采集、主动上报与错误诊断。
- [完整示例](advanced/examples.md)：直接运行免硬件的五种通信模式与交互控制台 Demo。
- [API 参考](api.md)：所有类、方法、参数签名与默认值速查。
