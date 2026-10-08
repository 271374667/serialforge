# Git 与提交规则

## 分支

- `master` 是主分支，必须始终包含完整可用的代码：可构建、可导入、已通过对应阶段验收的提交。它只接受来自 `dev` 的合并，且该合并仅在用户明确要求时执行；AI 不向 `master` 直接提交、推送或合并。
- `dev` 是开发分支，不保证稳定可用，允许存在未完成、未验收或实验性改动；所有日常开发、提交与 checkpoint 都在 `dev` 进行。
- 快速迭代阶段所有开发和提交直接在 `dev`；用户宣布稳定后恢复使用 `feat/*` 或 `fix/*`，完成后只合并到 `dev`。
- 绝不在 `master` 上直接动手，不自动切换主干、不自动 stash。
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
