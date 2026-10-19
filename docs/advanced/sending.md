# 发送、回调与取消

> 发送一条指令就像在餐厅下单：你可以站在柜台死等，也可以拿个叫号牌回座位喝茶。

在上位机代码里，发送只是故事的开头。更关键的是：指令怎么带动态参数？结果回包时怎么更新对应的 UI 控件？如果网络或者线路卡住了，程序该怎么优雅地退场？

---

## 两种发送姿势：同步 vs 异步

```python
# 姿势 A：同步阻塞（适合脚本、单元测试或独立的后台 Worker 线程）
msg = forge.send_sync(GET_TEMP)
print("同步拿到数据:", msg.data[0])

# 姿势 B：异步非阻塞（适合 Qt GUI 主线程，必须认准这个！）
call = forge.send_async(GET_TEMP)
call.add_done_callback(lambda msg: self.label.setText(str(msg.data[0])))
```

- **`send_sync`**：当前线程会老老实实挂起，直到硬件回包或超时抛异常。如果在界面的按钮槽函数里调它，设备稍微迟疑一下，你的整个窗口就会卡死。
- **`send_async`**：立刻返回一个调用句柄 `CommandCall`。你把要干的事情写在回调闭包里，**这个闭包百分之百由 Qt 主线程驱动**，你在里面操作任何 QLabel、QTableWidget 都绝对线程安全。

---

## 动态传参：`params` 占位符

很多控制命令需要带参数，比如设定某个通道的电压。你不需要为了通道 1、通道 2 分别声明两个对象，用 `{占位符}` 即可：

```python
# 声明时在 request 里写上 {channel} 和 {voltage}
SET_VOLT = forge.add("SET_CH {channel} {voltage:.2f}", r"OK")

# 发送时用 params 传入具体的数值
forge.send_async(
    SET_VOLT,
    params={"channel": 2, "voltage": 3.3},
)
```
底层库会自动按照 Python 格式化规则完成替换，安全又规整。

---

## 救命神器：`context` 上下文穿透

在图形界面里，你经常会遇到这种尴尬场面：
界面上有一个 50 行的表格，每一行代表一台从机。用户点了第 18 行的“读取状态”按钮。500 毫秒后，设备回包了。

此时你的回调函数触发了，但它怎么知道刚才那个回包对应表格里的哪一行？
以前大家往往要写各种恶心的全局映射字典、或者拼复杂的闭包包装器，一不小心还容易内存泄漏或行号错乱。

在 SerialForge 里，你只需要随手塞一个 `context`：

```python
def on_click_row(self, row_idx: int):
    # context 是一份只读的浅拷贝便签
    call = self.forge.send_async(QUERY_STATUS, context={"target_row": row_idx})
    call.add_done_callback(self.on_status_returned)

def on_status_returned(self, msg: Message[dict]):
    # context 原封不动地跟着结果回来了！
    row = msg.context["target_row"]
    self.table.setItem(row, 2, QTableWidgetItem(msg.data[0]["status"]))
```

**`context` 里的内容绝对不会被发到串口线路上**，下位机对此一无所知。它纯粹是陪伴这次调用的专属便签，极大减轻了界面状态绑定的痛苦。

---

## 急事插队：`priority=CommandPriority.URGENT`

假如下位机正在按顺序执行一个耗时 10 秒的自动校准脚本，你的队列里还排着另外 5 条轮询命令。此时现场突然冒烟，操作员猛拍红色的“紧急停机”按钮。

普通的先进先出（FIFO）队列会让你绝望：停机命令得乖乖排在 5 条轮询后面。

使用紧急优先级，指令立刻插队到队列最前端执行：

```python
from serialforge.advanced import CommandPriority

# 停机指令直接加塞，并且 mode=NO_REPLY 表示发完就走，不等设备回包
STOP = forge.add("EMERGENCY_STOP", mode=ResponseMode.NO_REPLY)

forge.send_async(STOP, priority=CommandPriority.URGENT)
```

---

## 超时与 3 秒期限

`send_async` 的 `timeout_s` 默认是 **3.0 秒**。这个时间究竟在等什么？

它计量的是**从你点击发送、进入队列排队、物理发出、设备思考回包，一直到数据送回 Qt 主线程**的全过程总耐心。

这里有一个关键区别：
- **`Spec.timeout_s`（协议预算）**：声明命令时写的超时（默认 1 秒），表示从发出去到设备吐出第一帧的最大允许时间。
- **调用期限（`timeout_s`）**：调用 `send_async` 时给的期限（默认 3 秒），代表整场任务的总预算。

如果你有一条多行命令（`MULTI`），声明里允许它读 10 行，并且估计需要耗时 4 秒（设置了 `total_timeout_s=4.0`），那么你在调用时务必把总期限放宽：

```python
# 总期限一定要大于协议预算，给排队和主线程调度留出余量
forge.send_async(DUMP_DATA, timeout_s=5.0)
```
如果总期限设成 3 秒，设备还在慢吞吞吐第 6 行时，你的调用期限就已经掀桌了。

---

## 优雅的设计：为什么“失败不执行回调”？

很多初学者会疑惑：为什么设备超时没回话的时候，`add_done_callback` 挂的回调函数压根没动静？库是不是假死了？

这是刻意遵循的**职责分离**设计。

如果成功和失败都挤在同一个回调里，你每写一个函数都得这么提心吊胆：
```python
def on_done(msg):
    if not msg.ok:       # 每个人都得写一遍这种样板代码
        show_error(...)
        return
    update_ui(...)       # 真正想干的事情被挤在最底下
```

在 SerialForge 里，**`add_done_callback` 承诺只为成功的结果上菜**。只要闭包跑起来了，你就可以确定数据是新鲜有效的，直接写业务逻辑。

### 那么失败去哪里看？
如果你需要感知失败，有两种常见模式：

1. **非阻塞查看结果或异常**：
   调用句柄的 `call.result()` 会如实重抛当时的异常：
   ```python
   try:
       # 如果还没完成，会抛 CommandNotReadyError
       # 如果已失败，会把当时的 CommandTimeoutError / DeviceCommandError 原封不动抛出
       res = call.result()
   except CommandExecutionError as err:
       print("原来失败是因为:", err.status, err)
   ```

2. **配合界面的定时器做超时提示**：
   ```python
   def send_with_feedback(self):
       self.btn.setEnabled(False)
       self.status.setText("正在执行...")
       
       call = self.forge.send_async(RESET_DEV, timeout_s=4.0)
       
       # 成功回调
       def on_success(msg):
           self.status.setText("执行成功！")
           self.btn.setEnabled(True)
       call.add_done_callback(on_success)
       
       # 利用 QTimer 挂一个稍大一点的界面兜底检查
       QTimer.singleShot(4200, lambda: self.btn.setEnabled(True) if not call.done else None)
   ```

3. **关闭窗口时的收尾**：
   如果用户关掉了某个对话框，而之前发出的指令还在等待中，直接调用 `call.cancel()` 即可。库会取消这次等待并抑制后续可能迟到的回调，绝不会出现“窗口都关了，突然蹦出个弹窗报错”的滑稽局面。

下一步：[扫描与连接](scan-connect.md)
