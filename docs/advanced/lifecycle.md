# 生命周期与重连

> 正常的通信像开店：早晨开门营业、白天招待客人、晚上打烊锁门；意外断线就像遭遇断电，关键在于电来了之后能不能自己爬起来。

编写稳健的工业上位机，最考验功力的往往不是正常工作时的流程，而是异常与退出的善后处理：
- 用户点了右上角的红叉关闭窗口，后台线程会不会变成僵尸进程赖在任务管理器里？
- 工人踢了一脚工控机的机箱，USB 串口松了一下又弹回去，软件能不能自己重新连上？
- 软件刚连上板子，需要先发 3 条初始化配置指令，怎么确保每次重连后这 3 条命令都能自动补发？

---

## 优雅退场：`disconnect()` 的讲究

在传统的 `pyserial` 代码里，关闭窗口时如果不小心，很容易因为后台读写线程没退出，导致端口被占用锁死，下次再打开软件就会提示“拒绝访问（Access Denied）”。

SerialForge 在这方面做了严格的双重防护：

1. **自动挂载 Qt 退出事件**：
   在创建门面时，库会自动把内部清理挂在 `app.aboutToQuit` 信号上。即使用户暴力退出程序，底层工作线程也会收到撤退信号并安全闭合。
2. **在窗口 `closeEvent` 中主动关闭**：
   ```python
   def closeEvent(self, event):
       # disconnect() 是线程安全的，且可重复安全调用
       # 它会通知所有后台工作线程撤退，并安全等待串口物理句柄释放
       self.forge.disconnect()
       super().closeEvent(event)
   ```

`disconnect()` 会实实在在地等待底层的物理串口句柄彻底释放干净后才返回，绝不会把半开不开的悬空资源留在操作系统里。

---

## 一套标准的生命周期流程

一个成熟的上位机界面，通常遵循这样的生命周期节奏：

```text
1. 软件启动 ───▶ 创建 QApplication ───▶ 创建 SerialForge ───▶ 集中声明 Spec 命令
                                                                      │
2. 用户点击连接 ───▶ forge.scan_async() ───▶ 闭包中 forge.connect_async()
                                                       │
3. 业务通讯 ◀──────────────────────────────────────────┘
      ├── 发送控制指令: forge.send_async().add_done_callback(...)
      ├── 观察收发数据: forge.received.connect(...) / forge.raw_sent.connect(...)
      └── 监控连接状态: forge.connection_state_changed.connect(...)
                               │
4. 退出或断线 ───▶ forge.disconnect() ───▶ 物理资源完全归还系统
```

---

## 掉线重连与健康心跳

### 1. 监听连接状态变化
不要在界面上开个定时器去猜设备还在不在。直接监听 `connection_state_changed` 信号：

```python
from serialforge.enums import ConnectionState

def on_state_changed(state: ConnectionState):
    if state == ConnectionState.CONNECTED:
        self.status_icon.setStyleSheet("background-color: green;")
        self.status_text.setText("设备在线")
    elif state == ConnectionState.DISCONNECTED:
        self.status_icon.setStyleSheet("background-color: gray;")
        self.status_text.setText("已断开")
    elif state == ConnectionState.PROBING:
        self.status_text.setText("正在尝试重连...")
    elif state == ConnectionState.FAILED:
        self.status_icon.setStyleSheet("background-color: red;")
        self.status_text.setText("连接失败")

forge.connection_state_changed.connect(on_state_changed)
```

### 2. 健康心跳（Heartbeat）
如果设备长时间不说话，你怎么知道线有没有被拔掉？
心跳机制会在静默时周期性发一条微小的探针命令去确认设备存活。

```python
# 每隔 2 秒发一次心跳
forge.configure(
    baudrates=[115200],
    probe=PING,
    heartbeat_s=2.0,
)
```

> **注意**：有些结构脆弱的小型单片机一旦被频繁问话，内部状态机容易紊乱。如果你的设备“经不起多问”，把心跳显式关掉即可：`heartbeat_s=-1.0`（库的默认值就是禁用心跳）。

### 3. 初始化序列（Init Sequence）
有些设备在刚连上（或者断线重连恢复）时，必须先发几条握手指令进行初始化，比如 `RESET`、`SET_MODE_AUTO`。

使用 `set_init_sequence` 注册后，无论是第一次连接成功，还是半夜掉线后自动重连成功，库都会**自动按序发完这组初始化命令**，确认设备完全就绪后才把状态置为 `CONNECTED`：

```python
# 设置开机/重连后必走的初始化仪式
forge.set_init_sequence([INIT_CFG, CALIBRATE])
```

你再也不需要在每次重连的回调里手忙脚乱地重复写初始化代码了。

下一步：[错误与诊断](errors.md)
