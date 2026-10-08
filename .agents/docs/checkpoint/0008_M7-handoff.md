# 0008_M7-handoff

> 关联任务: todo/0007_M7_集成演示与发布前验收.md | 更新: 2026-10-08 | 进度: M7 开发与验证进行中
> 最近 commit: 01be615（本次迁移提交后以 git log 为准）

## 已完成

- M1–M6 不重做；阶段记录已归档，原始真机记录仅 COM11/Prolific 的 Version 查询及发现。
- 用户修订：全项目绝对导入，测试替身由 src/serialforge/testing 移至 tests/support。
- 已同步 AGENTS、编码/打包规则、设计修订与架构；Ruff TID252 与 AST 守卫防止相对导入回归。
- wheel/sdist 使用明确内容边界，构建和 twine check 通过，产物成员没有 tests/testing/examples。
- API 快照扩展为公开方法签名、信号、属性、枚举值和数据类字段。
- 修复生产线程导入仅属开发依赖的 typing_extensions；保留 Python 3.11 的逐方法 ty 说明。
- 工作区 M7 Demo/README、硬件套件、CI 和 release.py 正在实现验证，尚不表示 M7 总体验收完成。
- 当前全量测试 97 通过、1 硬件跳过；Ruff/ty 通过，Pylint 10.00。

## 进行中（下一步从这里继续）

- 先提交已验证的结构迁移到 dev，明确排除用户既有 main.py 删除。
- scripts/ci.py 干净 wheel 的来源断言误把 uv 的 --with 依赖层要求放在 sys.prefix 下；
  wheel 已能导入，但该断言需要按包元数据位置修正，再继续 Demo、测试、3.11–3.14 与最低依赖。
- 继续补齐 M7 CI/发布文档验证结果，分独立单元及时提交；不 push、tag、上传。

## 待办

- 实际完成干净 wheel Demo/测试、Python 矩阵与 lowest-direct，验证仓库 uv.lock 无改动。
- M7 阶段报告与内部文档检查；Windows 10/第二种芯片/拔插/睡眠/DTR-RTS/回环保留未验证。
- 用户没有授权新的真机命令或接线，不自动打开 COM11 或其他真实端口。

## 关键上下文

- 保留 main.py 的未提交删除；不得自动提交、回滚或 stash。
- 原 src/serialforge/testing 仅剩旧 __pycache__；删除命令被环境策略阻止，已可回退地移至
  temp/0003_testing_bytecode，生产目录已不存在。测试替身和示例不进入 wheel/sdist。
- 测试与 Demo 改用 tests.support；wheel 冒烟仅复制外置验收输入，不复制 src。
- 计时已实测 Windows 11 build 26200 / Python 3.11.12 x64：1 ms sleep 的中位 1.528 ms，
  5 ms 的中位 5.501 ms；仅证明本机名义 5 ms 无 15.6 ms 固定下限，不证明硬件时序。

## 验证方式

- uv run python scripts/ci.py --release（仍在修正 wheel 验收阶段，不宣称全绿）。
- 架构守卫/API 快照；产物 ZIP/TAR 清单；taskmgr.py check / newdoc.py check。
