# 0011_迁移随库示例到received非空原始数据契约

> 状态: 排队  |  创建: 2026-10-10  |  完成: -
> 优先级: 中  |  关联 checkpoint: -

## 背景

用户 2026-10-10 明确 received 的非空判断属于库行为：raw_data 为空的消息不能触发信号。库已统一过滤并保留 send_sync/result/add_done_callback；使用方 temp/0005真机测试/U987demo1.py 已恢复原样。

代码检查发现随库 examples/quick_start.py、interactive.py、async_callbacks.py 仍依赖 received 的 COMMAND_RESULT/STREAM_FRAME/DEVICE_EVENT 元信息。它们需要按新契约迁移；本次不扩展为重写示例，不修改用户 demo。

## 目标（验收标准）
- [ ] 命令结果通过调用句柄/回调/同步接口获取，失败通过 result() 的异常获取。
- [ ] 流数据与主动上报按非空 raw_data 消费，取消终态从调用句柄取得。
- [ ] 三份随库示例和 tests/test_examples.py 全部通过，消费端无需为空 received 添加防护。
- [ ] 用户明确要求时再同步 README/公开 docs；不得改动用户 temp demo。

## 阻塞 / 依赖

- 等用户安排示例迁移；库行为以知识 0005/0007 的最新覆盖契约为准。

## 完成记录
