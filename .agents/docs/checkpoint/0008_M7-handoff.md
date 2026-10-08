# 0008_M7-handoff

> 关联任务: todo/0007_M7_集成演示与发布前验收.md | 更新: 2026-10-08 | 进度: M7 软件验收通过，真机矩阵未完成
> 最近代码 commit: 38d30d0（pytest-xdist 加速与完整发布前 CI 已通过并提交 dev）

## 已完成

- M1–M6 不重做；阶段记录已归档，原始真机记录仅 COM11/Prolific 的 Version 查询及发现。
- `84fa007`：全项目绝对导入，测试替身由 src/serialforge/testing 移至 tests/support。
- 已同步 AGENTS、编码/打包规则、设计修订与架构；Ruff TID252 与 AST 守卫防止相对导入回归。
- wheel/sdist 使用明确内容边界，构建和 twine check 通过，产物成员没有 tests/testing/examples。
- API 快照扩展为公开方法签名、信号、属性、枚举值和数据类字段。
- 修复生产线程导入仅属开发依赖的 typing_extensions；保留 Python 3.11 的逐方法 ty 说明。
- `7d16a4c`：M7 Demo/README、硬件套件、CI 和 release.py；默认 dry-run 和非交互上传安全回归通过。
- `3d7286a`、`650b8a8`：最低运行依赖解析固定开发工具，实际 uv 编辑与临时项目隔离回归通过。
- `112c954`：旧 Qt 版本 connect 名称冲突导致失败，下限收紧至实测 6.11.2；未逐一验证中间版本。
- `38d30d0`：pytest-xdist 3.8.0 仅作开发依赖，默认最多 4 个 worker、worksteal 调度；无真实端口的回归测试确认硬件选择在调度前强制串行。
- 最终 ci.py --release 退出码 0：Ruff/格式/ty 通过，Pylint 10.00，构建和 twine 通过。
- 源码与独立 Python 3.11.12、3.12.10、3.13.3、3.14.3 环境均 100 通过、1 硬件跳过。
- 干净 wheel 导入、Demo（spec identity 保持）及外置测试通过：100 通过、1 硬件跳过。
- 最低组合 PySide6-Essentials 6.11.2、pyserial 3.5、loguru 0.7.0：100 通过、1 硬件跳过。
- pytest-xdist/execnet 只进入开发依赖，wheel 安装仍只使用运行依赖；最终 CI 锁文件哈希检查通过，验收未改动锁文件。
- 同一套 100 项测试的本机整条命令耗时：串行 6.889 秒，默认并行 4.730 秒，约缩短 31%。

## 进行中（下一步从这里继续）

- 软件交付已提交到 dev；接下来补齐 `.agents/docs/knowledge/software/0003_Windows真机验证矩阵.md`
  的 Windows 10/11 与至少两种芯片真机矩阵。
- 当前环境缺少完整矩阵所需机器、芯片和接线条件；任务仍激活，不以模拟测试代替真机验收。

## 待办

- Windows 10/第二种芯片/拔插/睡眠/DTR-RTS/回环保留未验证；完成实测后再做 M7 最终报告及任务归档。
- 用户没有授权新的真机命令或接线，不自动打开 COM11 或其他真实端口。

## 关键上下文

- 保留 main.py 的未提交删除；不得自动提交、回滚或 stash。
- release.py 实际入口因该删除按规则拒绝运行；默认 dry-run 逻辑与上传拦截通过回归测试，未执行上传。
- 原 src/serialforge/testing 仅剩旧 __pycache__；删除命令被环境策略阻止，已可回退地移至
  temp/0003_testing_bytecode，生产目录已不存在。测试替身和示例不进入 wheel/sdist。
- 测试与 Demo 改用 tests.support；wheel 冒烟仅复制外置验收输入，不复制 src。
- `uv run pytest` 默认 `-n auto --maxprocesses=4 --dist worksteal`；`-n 0` 串行调试，精确 `-m hardware` 选择强制串行且仍须显式启用与配置端口。
- 计时已实测 Windows 11 build 26200 / Python 3.11.12 x64：1 ms sleep 的中位 1.528 ms，
  5 ms 的中位 5.501 ms；仅证明本机名义 5 ms 无 15.6 ms 固定下限，不证明硬件时序。

## 验证方式

- uv run python scripts/ci.py --release（最终完整执行通过；不上传、不 push、不创建 tag）。
- 架构守卫/API 快照；产物 ZIP/TAR 清单；taskmgr.py check / newdoc.py check。
