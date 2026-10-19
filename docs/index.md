# serialforge

> 适用版本: 0.0.1  |  公共 API 改造 R1–R5（真机验证矩阵仍未完成）

`serialforge` 是面向 Windows 10/11 x64 应用的 QtCore 串口通讯库。库只依赖 QtCore 的信号、槽和线程，
提供设备发现、命令调度、自动重连和流量日志；库内不含任何具体设备业务命令，业务命令由使用方声明。
日常操作统一通过 `SerialForge` 门面完成：声明、扫描、连接、发送与观察信号都在同一个对象上。

## 推荐用法

三步固定下来，协议里的命令表与代码骨架一一对应：

1. **声明集中一处**：建好 `SerialForge` 后立刻把命令全部 `add(...)` 存成属性，每个命令只创建一次。
2. **发送与消费写在同一个函数里**：`send_async(命令).add_done_callback(闭包)`，闭包在主线程直接改控件。
3. **扫描即配置，连接用扫描结果**：`scan_sync(baudrates=[...], probe=命令)` → `connect_sync(devices[0])`。

```python
self.forge = SerialForge()

# ① 声明集中一处：加命令只改这一段
self.VERSION = self.forge.add(
    request=">SN_INFO_GET=?",                    # request 不用自己写 CRLF
    pattern=r">SN_INFO_GET=(?!\?)(?P<sn>.+)",    # 负向预查排除回显
)

# ② 发送 + 闭包在同一函数内，闭包捕获控件
def on_query(self):
    def on_done(message: Message[str]) -> None:
        self.output.setText(str(message.data))

    self.forge.send_async(self.VERSION).add_done_callback(on_done)

# ③ 波特率与探针一次给全，用扫描返回值连接
def on_connect(self):
    devices = self.forge.scan_sync(baudrates=[921600], probe=self.VERSION)
    if devices:
        self.forge.connect_sync(devices[0])
```

回调统一排队到 Qt 应用主线程，因此可以放心在闭包里改界面；默认期限 3 秒，超时或失败**不调用**成功回调。
`scan_sync()` 与 `connect_sync()` 会阻塞；界面应用可用 `scan_async()` / `connect_async()` 的闭包，
或把它们放到自己的工作线程。
完整说明见[快速开始](getting-started.md) 与[使用指南](usage.md)。

## 特性

- **门面 API**：`SerialForge` 覆盖声明、同步/异步扫描与连接、同步与异步发送，一个对象走完流程。
- **声明集中且身份稳定**：统一 `Spec` 冻结、`eq=False`，`add()` 返回原对象，回调里用 `message.spec is SPEC` 分发。
- **主线程闭包**：`send_async(...).add_done_callback(consume)` 的回调排队到 Qt 应用主线程，直接更新界面。
- **设备发现**：给波特率与探测命令即可 `scan_sync()`，逐个尝试候选波特率并缓存已成功的组合。
- **命令调度**：队列、优先级、超时、多行与流式响应的收齐规则由库处理。
- **自动重连与心跳**：掉线后按退避策略重连，可配置心跳探测与重连后的重新识别。
- **流量日志**：库内日志只走 loguru DEBUG，默认静默；可按连接落盘并限制文件数量与体积。
- **导入无副作用**：`import serialforge` 不创建 Qt 应用、线程、文件或日志 sink。

## 环境要求

| 项目 | 要求 |
| --- | --- |
| 操作系统 | Windows 10/11 x64 |
| Python | 3.11–3.14（64 位） |
| 运行时依赖 | `pyserial>=3.5`、`loguru>=0.7`；Qt extra 选 PySide6-Essentials>=6.11.2、PyQt6>=6.11 或 PyQt5>=5.15.11 |

Qt 下限 6.11.2 是当前通过完整测试套件的保守值：更早的 Qt 绑定存在信号 `connect` 与兼容
`SerialHandler.connect` 的名称冲突（新门面已改用 `connect_sync`/`connect_async`），中间版本未逐一验证。

## 安装

项目目前的验收方式是从构建产物安装；发布到包索引后可直接按名称安装：

```powershell
# 从本地构建的 wheel 安装（在解释器或项目环境中执行）
uv add ./serialforge-0.0.1-py3-none-any.whl --extra pyside6

# 发布到索引之后
uv add "serialforge[pyside6]"  # 或 serialforge[pyqt6] / serialforge[pyqt5]
```

wheel 与 sdist 只包含 `src/serialforge`，不含测试替身、`tests`、`examples` 或字节码。

## 下一步

- [快速开始](getting-started.md)：把推荐用法跑成一段完整代码
- [使用指南](usage.md)：声明、发送、闭包消费、扫描连接、定界与日志
- [示例](examples.md)：`examples/` 下的可运行脚本（本页代码即取自它们）
- [API 参考](api.md)：统一日常接口、19 名字过渡期与进阶 API
- [平台与限制](platform.md)：Windows 范围与真机验证边界
