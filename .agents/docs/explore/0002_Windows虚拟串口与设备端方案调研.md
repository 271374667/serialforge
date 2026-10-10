# 0002_Windows虚拟串口与设备端方案调研

> 分类: explore  |  日期: 2026-10-09  |  结论: 待定（等用户选路线）

## 问题

`examples/virtual_device.py` 目前把 `tests/support` 的内存后端当"串口"用，存在三个不满点：

1. 不是真正的 COM 口：设备管理器里没有，外部工具（串口助手、别的语言）看不到，`list_ports` 也只看到内存描述符。
2. 指令集缺能力：只有一次性注入的 `ALARM`，没有"周期性主动上报"，也没有"发一条后持续返回、直到发下一条命令才停"的流式行为（现在 `MONITOR` 只是固定 4 帧）。
3. 不够透明：使用方要写 `with device.use()`、`device.inject_alarm()`、`device.silent` 等设备侧细节，而不是"只有指令和返回值"。

要回答：Windows 上能否造真 COM 口、有没有第三方库、不行的话能否自研。

## 调研范围与方法

- PyPI 候选库：`PyVirtualSerialPorts`、`pyvirtualserial`（查元数据与 README）。
- 驱动/工具：`com0com`（开源）、Eltima VSPD / HHD / FabulaTech（商业）。
- pyserial 内置 URL handler（`serial_for_url`）在本项目是否可用。
- 本机实测：`Get-CimInstance Win32_PnPEntity` 无任何 COM 口；未安装 com0com；pyserial 3.5。
- 读源码确认约束：`transport/serial_transport.py` 用 `serial.Serial(**values)` 打开端口（不是 `serial_for_url`）；
  `connection/command_dispatcher.py:246-252` 在流活跃时把新的 `Correlation.EXCLUSIVE` 命令判为 BUSY。

## 候选方案对比

| 方案 | 优点 | 缺点 | 结论 |
| --- | --- | --- | --- |
| `PyVirtualSerialPorts` 2.0.0（MIT, 2025-10） | 纯 Python、上下文管理器 API | 只支持 \*nix 的 pty，README 明说 Windows 尚在开发中 | 不可用 |
| `pyvirtualserial` 1.1.0（MIT, 2024-04 后停更） | Windows 上开箱即用 | 本质是 com0com 封装，运行时自行提权并自动下载安装内核驱动（来源无哈希校验）；API 文档缺失 | 不引入；只作参考实现 |
| `com0com` 3.0.0.0（GPLv2，文件最后更新 2012） | 免费、开源、真内核驱动、`setupc.exe` 可脚本化建端口对 | 需管理员安装驱动；Win11 上 3.0 签名包有 Error Code 52 的失败报告，旧版驱动+管理员可成功；装的是系统级驱动 | 真 COM 口路线唯一免费选择 |
| 商业驱动（Eltima VSPD / HHD / FabulaTech） | 签名稳定、有 GUI/CLI、支持好 | 收费、许可商用需采购 | 备选 |
| 自研内核驱动 | 完全可控 | 需 WDK 写串口功能驱动并做 EV 签名 | 不可行 |
| 自研设备端逻辑（指令集/状态机） | 业务本属本项目，理应自研；与驱动解耦 | 无 | 必做 |
| pyserial `serial_for_url` + `socket://` / `rfc2217://` | 无需驱动、跨进程、对任何 pyserial 客户端透明、CI 友好 | 需改 `src`（1 行）；设备管理器看不到、`list_ports` 扫不到 | 备选（无驱动路线） |

补充事实：

- Windows 10/11 没有内置的虚拟 COM 端口对；真 COM 口必须由内核驱动提供，因此"纯 Python 造真 COM 口"不存在。
- `serial_for_url("COM5")` 会退化为普通 `Serial`，所以把打开方式换成 `serial_for_url` 对现有真实串口无行为变化，
  只是额外解锁 URL scheme；当前源码未使用它。

## 结论与建议

1. **真 COM 口只能靠驱动**：免费路线 = com0com 端口对（`COMx <-> COMy`），商业路线 = VSPD 类。
   没有任何第三方 Python 库能免驱动实现，两个候选库一个不支持 Windows、一个就是 com0com 的提权封装。
2. **设备端必须自研**，并且应该与传输解耦，做成一个"指令集 + 行为状态机"的引擎，
   再由两种挂载方式复用：
   - memory：沿用 `tests/support` 内存后端（CI 无驱动可用，测试保持绿色）；
   - real：com0com 端口对 + 后台线程用 pyserial 驱动另一端，示例侧代码与 memory 完全一致。
3. 使用方视图收敛为"指令声明 + 端口名 + 收发信号"：不再需要 `inject_alarm()`、`silent`、`use()` 这类设备侧细节
   （主动上报由引擎自己的定时器产生，流式由"收到下一条请求即停止"实现）。

## 风险

- 装 com0com 是系统级改动（管理员 + 内核驱动），且 Win11 上可能遇到签名问题，必须先征得用户同意；
  CI 不能依赖它，只能作为本地演示通道。
- 新指令集的"持续流"语义与库的 BUSY 规则冲突：流声明若保持 `EXCLUSIVE`，流期间只有 `NO_REPLY` 命令能发出；
  要让"发下一条就停"自然成立，需把流声明改成 `Correlation.TAGGED`，或让停止命令用 `NO_REPLY`（现有做法）。
- `tests/test_examples.py` 会校验 `virtual_device` 文档串覆盖所有 COMMAND 声明，指令集扩展后必须同步更新文档表。

## 后续（确认可行 → 建 todo）

- [x] 用户选路线：**B) 只做 memory 引擎，不装任何驱动**（真 COM 口挂载留作后续挂载方式）。
- [x] 指令集定稿：11 条声明（Version / Hello / ReadAll / Dump / Monitor / Stop /
      Report{period_ms} / SetGain{value} / Status / TEMP / ALARM）+ 故意未声明的
      `Ping`；**去掉 Reboot**（1 秒沉默不值得）。
- [x] 流的打断权限按库语义：`Stop`（NO_REPLY）与 `Status`（`Correlation.TAGGED`）
      能打断采集；其余 EXCLUSIVE 命令在流期间仍是 BUSY。
- [x] 重写 `examples/virtual_device.py` 与 4 个示例、`tests/test_examples.py`（见 todo 0009）。
- [ ] `docs/examples.md` 的指令表与示例输出需要同步，按 `rules/02` §11 等用户明确要求后再动笔。
- [ ] 真 COM 口挂载（com0com 端口对 + 后台线程用 pyserial 打开另一端）尚未实现。
