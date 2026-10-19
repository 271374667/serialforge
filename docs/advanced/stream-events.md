# 流式与主动上报

> 普通命令是“问答”，流式采集是“开水龙头”，而主动上报是“设备突然自己按了门铃”。

绝大多数指令是回合制的：你问一嘴，设备答一嘴。但工业现场往往有两种打破回合制的特殊情况：
1. **持续采样（流式，`STREAM`）**：上位机发一句 `START_ADC`，下位机立刻以每秒 50 次的速度源源不断灌波形数据过来。
2. **紧急通知（主动上报，`EVENT`）**：上位机什么都没问，但下位机检测到电机过载，突然自己往串口塞了一句 `ALARM: OVERHEAT`。

SerialForge 为这两种非回合制场景提供了原生的处理模型。

---

## 1. 流式采集（STREAM）：开水龙头容易，关水龙头要有讲究

流式命令必须通过异步接口 `send_async` 发出，传入的 `timeout_s` 代表**你打算采集的最长时间**：

```python
# 声明流式指令
START_STREAM = forge.add("START_STREAM", r"ADC:\s*(?P<val>\d+)", result_type=int, mode=ResponseMode.STREAM)
STOP_STREAM = forge.add("STOP", mode=ResponseMode.NO_REPLY)

# 发起长达 60 秒的流式采集
ticket = forge.send_async(START_STREAM, timeout_s=60.0)

# 在 raw_received 信号里观察实时涌入的数据
def on_data_arrived(msg: Message[Any]):
    if msg.raw_data.startswith(b"ADC:"):
        print("波形数据:", msg.raw_data)

forge.received.connect(on_data_arrived)
```

### 停流时的“双重关停法则”（至关重要！）
想要提前结束采集，代码里必须完成两个动作：

```python
# 第一步：发指令给设备，让硬件停下来（使用 URGENT 优先级插队，且 NO_REPLY 不等回复）
stop_call = forge.send_async(STOP_STREAM, priority=CommandPriority.URGENT)

# 第二步：在停机发出去后，叫停上位机本地的等待任务
stop_call.add_done_callback(lambda _: ticket.cancel())
```

为什么必须两个都做？
- **只发硬件停机命令，不 cancel 本地句柄**：硬件倒是闭嘴了，但上位机这边的 `ticket` 还在傻傻等满 60 秒才算结束。
- **只调本地 `ticket.cancel()`，不给硬件发停机命令**：上位机自己塞住耳朵不听了，但硬件单片机还在狂吐字节。这些无家可归的残余字节会污染你下一条发出的正常指令！

先通知硬件闭嘴，再挂断本地电话，这是写好流式通信的基本礼仪。

---

## 2. 主动上报：没有 request，只有 pattern

很多智能设备具备自我保护逻辑，例如高温警报、限位开关触发、低电量提醒。这些通知随时可能从下位机冒出来。

在 SerialForge 里，声明一个主动上报只需要**提供 `pattern`，省略 `request`**：

```python
# 声明一个由设备自发推送的报警事件
TEMP_ALARM = forge.add(
    pattern=r"ALARM:\s*(?P<sensor>\w+)\s+(?P<temp>[\d\.]+)",
    result_type=dict,
)
```

设备推送上来的原始数据同样会触发 `received` 信号。如果你需要把结构化的字典或数据类拿到手，可以使用事件路由组件进行捕捉。

### 高频数据刷新 UI 的防卡秘诀
如果下位机以每秒 500 次的速度狂发数据，**千万不要在每次收到数据时直接调 `label.setText()` 或重绘曲线**！

Qt 的主线程虽然很强，但如果每秒钟被塞进几百个绘制事件，事件队列会被塞爆，界面会以肉眼可见的速度变慢变卡。

**正确的姿势是“数据油水分离”**：
- 回调收到数据后，只负责往一个 Python `deque`（双端队列）或者数组里压数据，不碰界面；
- 启动一个 30ms 触发一次（约 30 FPS）的 `QTimer`，每次定时器跳动时，从队列里取出这一批数据统一刷新图表。

这样既能保证数据不丢，又能让 UI 保持丝滑顺畅。

下一步：[接收定界与编码](framing.md)
