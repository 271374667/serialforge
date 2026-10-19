# 平台与限制

> 适用版本: 0.0.1 | SerialForge 公共 API 改造

## 支持范围

| 项目 | 要求 |
| --- | --- |
| 操作系统 | Windows 10/11 x64 |
| Python | 3.11–3.14（64 位） |
| 运行时依赖 | `pyserial>=3.5`、`loguru>=0.7` |
| Qt | 三选一：PySide6-Essentials>=6.11.2、PyQt6>=6.11、PyQt5>=5.15.11 |

不承诺 Linux、macOS、32 位 Windows 或与硬件无关的真机行为。库只使用 QtCore，
不导入 QtWidgets/QtGui，不使用 asyncio 或多进程。

Qt 下限 6.11.2 是当前通过完整测试套件的保守值：更早的 Qt 绑定存在信号 `connect`
与兼容 `SerialHandler.connect` 的名称冲突（新门面已改用 `connect_sync`/`connect_async`），
中间版本未逐一验证。

## Qt 绑定选择

同一次运行只能加载一个绑定。只安装一个时无需配置；同时安装多个时，启动前设置环境变量：

```powershell
$env:QT_API = "pyside6"   # 或 pyqt6 / pyqt5
```

已加载的绑定与设置必须一致，不一致直接报错。PyQt5/PyQt6 为 Riverbank GPL v3 / 商业双授权，
使用方需按其条款履行义务。

## 线程边界

- 在 Qt 应用主线程创建 `SerialForge`；`add`、`send_async` 支持应用线程调用。
- `scan_sync()`、`connect_sync()`、`send_sync()` 都**阻塞调用者**；`scan_async()` / `connect_async()`
  是异步入口，后台线程执行、闭包在主线程拿到 `list[DeviceInfo]` / `DeviceInfo`。
  `connect_sync()`/`send_sync()` 仍要放到应用自己的工作线程，否则事件循环被卡住，闭包也投递不进来。
- 异步结果经主线程排队投递：`add_done_callback` 的闭包、`received`、`raw_sent`、
  `connection_state_changed` 都在主线程被调用，保持事件循环运行即可安全更新界面。
- `cancel_scan()`、`disconnect()` 可被其他线程调用以取消进行中的工作；
  `disconnect()` 会等待 I/O 与扫描结束，可重复调用。

## 真机验证边界

仓库当前完成的是软件验收：Python 3.11–3.14 × 三种 Qt 绑定的组合测试、静态检查、
构建与干净 wheel 冒烟。**整张 Windows 真机验证矩阵尚未宣称通过**，以下行为需要由使用方
按自己的硬件与适配器复验：

- 睡眠/唤醒后恢复通讯，USB 选择性挂起导致的掉线；
- 驱动或适配器在打开串口时的 DTR/RTS 脉冲；
- 拔插重连与自动重连的退避行为；
- TX-RX 回环（须把独立适配器的 TX 与 RX 物理短接后再测；接到目标设备时它不会回显
  `SERIALFORGE_LOOPBACK`）；
- 驱动信息与 USB 身份过滤（VID/PID）在目标机器上的表现。

完整矩阵与验收口径见仓库内 `.agents/docs/knowledge/software/0003_Windows真机验证矩阵.md`，
`.agents/docs/knowledge/software/0004_阻塞连接与可选USB身份契约.md` 记录了连接与身份的最新契约。

遇到挂起导致的掉线，可以检查 Windows 的 USB 选择性暂停和设备电源管理选项；库不会修改系统设置。

## 运行数据与打包

- 日志与波特率缓存写入 Qt 应用数据目录；PyInstaller 打包后也不要改到程序目录。
- wheel 与 sdist 只包含 `src/serialforge`，不含 `tests`、`examples`、测试替身或字节码；
  测试替身在源码仓库的 `tests/support/`，仅供本地验证。
- 用 PyInstaller 打包的使用方需自行履行 PySide6 LGPL 合规义务。

## 下一步

- [快速开始](getting-started.md)：推荐用法的最小程序
- [使用指南](usage.md)：声明、发送、闭包消费、扫描连接与定界
- [API 参考](api.md)：门面、声明、调用句柄、消息与异常签名
