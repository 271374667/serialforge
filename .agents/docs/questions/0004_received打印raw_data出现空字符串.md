# 0004_received打印raw_data出现空字符串

> 分类: questions  |  发现: 2026-10-10  |  状态: 已修复  |  严重级别: 低

## 现象

- 用户运行 temp/0005真机测试/U987demo1.py，点击按钮后仍打印 Received: b''。
- 原始读分片已经按完整行合并，但打印函数会把合法响应帧和命令完成消息也当作原始 RX。

## 复现步骤

- 提取 demo 实际 receive 函数，依次传入 RAW_RECEIVE、RESPONSE_FRAME、COMMAND_RESULT。
- 原打印结果是一条完整行和两条 b''；不需要启动 GUI 或访问真实串口即可复现。

## 排查过程

- demo 直接打印每个 Message.raw_data，未判定是否存在原始字节。
- 空行还可进入调度器事件队列，稍后以 UNKNOWN_FRAME 发出；回归测试显式 flush_events 后复现。

## 根本原因

- received 是原始接收、解析帧和命令终态的统一入口，后两者的有效内容在 raw_frames/data/status，raw_data 默认为空。
- 首次修复仅覆盖原始读取路径，既没有调整 demo 的原始日志消费，也未拦截空解析帧。

## 修复方案

- demo 的 receive 先判断 raw_data 非空再打印，改用公开 serialforge 导入。
- serial_forge.py 的 _publish 过滤没有原始字节和非空帧的非终态消息，保留命令结果身份和回调。
- 假设备运行 demo 实际声明、接收槽及异步按钮路径，只打印两条完整原始行，回调得到 921600。
- 相关测试 93 项通过；真机和 GUI 点击未运行。

## 如何避免再犯

- 原始日志先筛选 raw_data/category，业务消费者使用 raw_frames/data；见知识 0007。
- 验证空行时等事件队列完成投递，不能只等待命令完成，否则可能漏掉稍后的空帧观察。
