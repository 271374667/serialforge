# 原始接收与日志

> 原始数据流就像厨房收银机里吐出的每一张流水小票；而精细解析好的菜品报表，请去调用句柄那里领。

做上位机软件，界面右侧或底部往往会预留一个大文本框，像串口调试助手一样实时滚动显示“发了什么、收了什么”。

很多初学者自己写信号时，容易把原始字节、解析后的对象、命令结束通知全部塞进同一个 Signal 里。结果就是日志窗口疯狂复读：下位机回了一句 `OK`，日志框里瞬间把原始数据、解析帧、状态变更打印了三四遍，刷屏刷得让人眼晕。

serialforge 的门面非常克制地仅公开了三个核心信号，分工极度明确。

---

## 门面仅有的三个信号

这三个信号全部由 Qt 主线程负责派发，你可以直接连接到 UI 槽函数：

| 信号名称 | 参数类型 | 派发时机与用途 |
| --- | --- | --- |
| **`received`** | `Message[Any]` | **仅当真正收到了非空的原始字节时**才会触发。做报文监控视窗认准它。 |
| **`raw_sent`** | `bytes` | 底层串口真正把字节写入线路后触发。包含握手、探针、心跳与重试。 |
| **`connection_state_changed`** | `ConnectionState` | 物理连接状态发生变动（断开、探测中、已连接、失败）。 |

---

## 为什么 `received` 只发非空的原始字节？

这是经过深思熟虑的设计抉择。

当设备发来一串 `TEMP: 25.4\r\n` 时，内部系统其实经历了好几层转化：
1. 物理读到了字节 `b"TEMP: 25.4\r\n"`
2. 定界器剥离换行符，识别出一帧 `TEMP: 25.4`
3. 正则解析器将其转换成数值 `25.4`
4. 对应调用的状态变更为 `OK`

如果每个阶段都去触发一次 `received`，你的日志框就会变成复读机。

因此，**`received` 信号严格只为 `raw_data` 非空的消息放行**：
- 在默认的 `LINE` 行模式下：每当拼装好一行完整的原始文本（末尾保留换行符），就派发一次。
- 在二进制或无换行模式下：只要串口单次 `read()` 读到了非空字节，就原样派发一次。

至于解析出来的强类型数据类、字典、命令状态，它们会安静地通过你的异步回调或者 `send_sync()` 的返回值递交，绝不来污染原始报文视窗。

---

## 5 行代码做一个带颜色的收发终端

有了 `received` 和 `raw_sent`，在界面上做一个像模像样的串口监控面板只需要几行：

```python
# 监听发送出去的字节（标记为绿色）
def on_raw_sent(data: bytes):
    text = data.decode("utf-8", "replace")
    self.log_text_edit.appendHtml(f"<span style='color: green;'>&gt;&gt; {text}</span>")

# 监听接收到的原始字节（标记为蓝色）
def on_raw_received(msg: Message[Any]):
    text = msg.raw_data.decode("utf-8", "replace")
    self.log_text_edit.appendHtml(f"<span style='color: blue;'>&lt;&lt; {text}</span>")

# 绑定信号
forge.raw_sent.connect(on_raw_sent)
forge.received.connect(on_raw_received)
```
因为信号直接在 Qt 主线程投递，完全不需要自己写复杂的线程锁或队列桥接。

---

## 文件日志与调试记录

如果你需要在客户现场排查偶发通信故障，光靠界面上的文本框是不够的。

通过 `serialforge.advanced.LogConfig`，你可以开启落盘的文件日志：

```python
from serialforge.advanced import LogConfig

log_cfg = LogConfig(
    enabled=True,
    directory="./logs",      # 日志存放目录
    max_bytes=10 * 1024 * 1024,  # 单个文件 10MB
    backup_count=5,          # 最多保留 5 个归档文件
)

forge = SerialForge(log=log_cfg)
```

库内部使用的是 loguru，默认处于静默状态，不会往控制台胡乱喷大段调试信息。只有当你主动配置或者开启文件日志时，它才会默默记录完整的诊断轨迹。

下一步：[流式与主动上报](stream-events.md)
