# Git 与提交规则

## 分支

- 快速迭代阶段所有开发和提交直接在 `dev`；用户宣布稳定后恢复使用 `feat/*` 或 `fix/*`，完成后只合并到 `dev`；`master`/`main` 只由用户管理。
- 未经用户明确许可不执行 `git push`、tag、发布或强制历史改写。
- 当前项目已设置 `core.hooksPath=.githooks`，只有 `commit-msg` 钩子。

## 提交

- 每个提交是可独立回退的功能或文档单元；提交前检查 `git status --short`、`git diff --check` 和 `git diff --stat`。
- 长任务完成一个可验证单元并通过相关检查后立即提交到 dev，不积累多个已验证单元等待整个里程碑结束；未完成部分留在工作区并写 checkpoint，用户原有改动不混入提交。
- 标题使用中文 Conventional Commits：`feat(scope): 描述`。正文之后可加入 `AI-Used-For` 与 `AI-Prompt` trailer；不写 AI 签名或 `Co-authored-by`。
- 钩子不运行测试、ruff 或 ty；大任务收尾由 AI 主动运行检查，中间 checkpoint 提交不自动触发检查。

## 安全

- 不提交密钥、token、`data/` 运行时数据、构建产物或大文件；真实本地配置使用 `*.local.*`。
- 不使用 `git reset --hard`、`git checkout --` 或 `--no-verify` 清理用户改动，除非用户明确要求。
