# 错误与诊断

> 报错不是库在乱发脾气，而是一份清晰的“案发现场调查勘验单”。

在工业现场排查上位机通信问题，最怕的不是报错，而是“死得不明不白”——界面没有任何动静，控制台也静悄悄的，只留你一个人对着无响应的按钮陷入哲学沉思。

serialforge 的异常体系设计遵循一条铁律：**带着充足的现场物证报错**。

---

## 异常族谱

所有库内异常均继承自顶层的基类 `SerialForgeError`：

```text
SerialForgeError
├── ConfigError                 # 配置参数非法（例如：探针正则匹配了发出的文本，或没建 Qt 实例）
├── CommandError                # 命令声明不合法（例如：自定义解析器抛出类型异常）
└── CommandExecutionError       # 运行时指令执行失败（最常见的案发现场）
    ├── CommandTimeoutError     # 超时：在规定期限内没有等到预期回包
    ├── CommandCancelledError   # 取消：被显式调用 call.cancel() 或窗口关闭打断
    ├── CommandBusyError        # 线路正忙：上一条独占命令还没走完
    ├── CommandDisconnectedError# 掉线：发送过程中物理串口意外断开
    ├── CommandParseError       # 解析失败：收到了回复，但数据无法转成目标类型
    └── DeviceCommandError      # 设备报错：匹配到了声明中的 error_pattern 错误帧
```

---

## 案发现场的物证提取

当你使用同步 `send_sync()`，或者对异步句柄调用 `call.result()` 时，如果命令失败，就会抛出 `CommandExecutionError` 的子类。

这个异常对象可不是只有一句简单的错误文字，它随身打包了所有的**第一现场证物**：

```python
from serialforge.errors import CommandExecutionError, CommandTimeoutError

try:
    forge.send_sync(SET_VOLTAGE, params={"val": 5.0}, context={"channel": 3})
except CommandTimeoutError as err:
    print(f"指令超时！状态码: {err.status}")
    print(f"当时尝试发出的完整字节: {err.sent}")
    print(f"超时前已经收到的半截帧: {err.raw_frames}")
    print(f"附带的业务上下文便签: {err.context}")  # {'channel': 3}
except CommandExecutionError as err:
    print(f"其他执行失败: {err.error_message}")
```

看看这些字段：
- **`err.sent`**：真真切切从串口写出去的字节（含结束符）。可以一眼看出占位符有没有替换错、大小写对不对。
- **`err.raw_frames`**：硬件在超时前到底吐出了什么东西。很多时候你会发现：“原来硬件根本不是没回，而是回了一句看不懂的乱码或者报文格式变了”。
- **`err.context`**：你随指令传进去的业务标签。在复杂界面里，靠它就能精准锁定是哪个表格单元格或者哪个通道触发了故障。

---

## 后台失物招领处：`check_errors()`

有些错误与某一条具体的命令无关，比如后台读取线程突然检测到校验和错乱、或者半行数据缓存溢出。

这些无家可归的后台异常不会大张旗鼓地直接把整个 Qt 事件循环炸停，而是被收进了一个最多容纳 128 条记录的内部环形队列。

你可以在合适的时候主动检查并清仓：

```python
try:
    # 取出并抛出积攒的最老的一条未被归属的后台异常
    forge.check_errors()
except Exception as err:
    print("后台逮到一只野生异常:", err)
```
如果长时间没去检查导致队列溢出，被顶掉的异常数量可以通过 `forge.stats().dropped_background_errors` 查看。

---

## 现场翻车排查四步法

当你在现场发现“设备怎么没反应”时，请按顺序排查，绝大多数问题在第 2 步就能找到真相：

1. **看日志视窗中的 `received` 信号**：
   设备到底有没有把字节发过来？如果连一个字节都没有，去查硬件供电、TX/RX 接反了没有、波特率是不是设错了。
2. **看 `raw_sent` 信号**：
   上位机发出去的字节到底长什么样？末尾有没有带下位机要求的换行符（`\r\n` 还是单纯的 `\n`）？
3. **打印 `call.result()` 抛出的异常**：
   查看 `err.raw_frames`。设备可能回话了，但是格式是 `VAL:42`，而你的正则写成了 `VAL=\d+`，正则对不上号。
4. **开启文件日志（`LogConfig`）**：
   如果问题偶发（比如跑一晚上出现一次），配置好文件日志，让库把每一次握手、状态迁移与字节记录完整落盘，第二天早上喝着咖啡翻日志即可。

回到 [进阶指南总览](index.md)
