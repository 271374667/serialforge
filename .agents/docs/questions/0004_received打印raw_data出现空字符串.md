# 0004_received打印raw_data出现空字符串

> 分类: questions  |  发现: 2026-10-10  |  状态: 已修复  |  严重级别: 低

## 现象

- 用户运行 temp/0005真机测试/U987demo1.py，点击按钮后仍打印 Received: b''。
- 原始读分片已经按完整行合并，但库仍将 raw_data 默认空的响应帧、命令完成消息发给 received，违反用户要求的非空信号行为。

## 复现步骤

- 提取 demo 实际 receive 函数，依次传入 RAW_RECEIVE、RESPONSE_FRAME、COMMAND_RESULT。
- 原打印结果是一条完整行和两条 b''；不需要启动 GUI 或访问真实串口即可复现。

## 排查过程

- demo 原样直接打印每个 Message.raw_data；用户明确非空保证必须由库提供，不能通过修改 demo 隐藏空信号。
- 空行还可进入调度器事件队列，稍后以 UNKNOWN_FRAME 发出；回归测试显式 flush_events 后复现。

## 根本原因

- received 是原始接收、解析帧和命令终态的统一入口，后两者的有效内容在 raw_frames/data/status，raw_data 默认为空。
- 初次修复只过滤原始读取；后续补丁仍为 COMMAND_RESULT/非空 raw_frames 设置例外，并错误修改 demo。用户进一步要求统一出口对 raw_data 非空做无例外判断。

## 修复方案

- 撤回 demo 的全部修改，恢复原打印函数、导入和格式。
- serial_forge.py 的 _publish 一律执行 `if not message.raw_data: return`，空载荷不排队、不触发 received，没有类别或终态例外。
- 命令结果独立由调用句柄保存，send_sync/result/add_done_callback 仍可获取结果与原声明对象。
- 假设备运行恢复原样的 demo 实际声明和无过滤打印函数，只打印两条完整原始行，回调得到 921600。
- 回归覆盖正常响应、MULTI、空行、NO_REPLY 和超时；真机和 GUI 点击未运行。

## 如何避免再犯

- 用无过滤的原打印函数直接连接 received 验收，断言每条消息 raw_data 非空；不能在消费端加过滤来代替库行为。最新契约见知识 0007。
- 验证空行时等事件队列完成投递，不能只等待命令完成，否则可能漏掉稍后的空帧观察。
