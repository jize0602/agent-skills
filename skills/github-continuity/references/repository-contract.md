# GitHub Continuity Repository Contract

本 reference 供通用 `github-continuity` skill 按需读取。它服务三类明确请求及已接入项目的正式任务收尾：

- `同步至 github+仓库名`：把当前项目状态安全同步到指定 GitHub 仓库。
- `从 github+仓库名读取`：从指定仓库恢复可跨对话接手所需的最新事实。
- `备份至 github+仓库名`：将已同步状态冻结为远端可独立恢复的灾备。

完整语义映射由 `.ai/CONTINUITY.json` 保存，schema 和操作约束见 [operations.md](operations.md)。下面的最小结构是已有项目兼容入口，不代表完整初始化已完成。必须同时定位 implementation、history、decisions、incidents、lessons、tests、deployment、safety、project_skill 和 changelog；复用等效文件而不复制事实。

## 最小连续性结构

优先使用以下路径；项目已有等效结构时建立映射并复用，不复制第二份事实源：

```text
.ai/START_HERE.md       # 新 Agent 读取顺序、边界和事实入口
.ai/PROJECT_STATE.json  # 机器可读状态；只保留一个权威版本
.ai/REQUIREMENTS.md     # 当前需求与验收口径
.ai/TASKS.md            # 待办、优先级、阻塞和 Issue/PR 映射
.ai/HANDOFF.md          # 最近一次接手说明和下一安全动作
evidence/               # 可复核测试、CI、部署和同步证据
```

等效文件应在 `.ai/START_HERE.md` 或 `HANDOFF.md` 中记录映射；不要因名称不同而复制需求、状态或待办内容。

## 状态与新鲜度

必须分别记录三种范围，不能互相推断：

| 范围 | 最少事实 | 新鲜度要求 |
|---|---|---|
| local | 工作树是否 dirty、HEAD、代码/需求/待办版本、实际测试结果 | 记录检查时间；dirty 时禁止把它当作已同步版本 |
| remote | owner/repository、目标 branch、commit/tree、push/PR、CI run | 以本次读取或成功写入的时间为准；过期标为 `STALE` |
| server | Test/Production 版本、health、部署/回滚和隔离证据 | 只能由真实服务器验证更新；CI 或 tag 不等于部署验收 |

状态字段缺失、无法验证或网络中断时写 `UNKNOWN`、`NOT_RUN` 或 `STALE`，不得用旧聊天记录补齐。每项状态应带 `observed_at`/`verified_at`（适用时含时区）和 evidence 路径或 URL。

## 同步闭环

每次同步都必须检查并按项目真实内容更新以下五类信息及完整语义映射中的实现、历史、事故、经验和变更记录：

1. 源代码与当前 commit/tree；不把本地 provenance 冒称为远端历史。
2. 需求与验收口径（`REQUIREMENTS.md` 或其等效文件）。
3. 待办、阻塞、Issue/PR 映射（`TASKS.md` 或其等效文件）。
4. 实际测试、CI、部署和回滚证据（`evidence/`）。
5. 新 Agent 读取顺序、已知限制、下一步和停止条件（`HANDOFF.md`）。

完成写入后回读远端 commit/tree，并在最终回复或本地同步回执记录；不能只报告“已 push”。不要为把提交自己的 SHA 写进自己而产生无限递归提交；仓库内记录基线 SHA 与其含义，最新 SHA 从 Git 读取。读取请求则先读入口、状态、需求、待办、handoff 和最新证据，再说明哪些事实仍旧或未知。

## CI 与发布边界

- CI 必须复用项目已有的真实测试、lint、构建或 manifest 检查；不得为了变绿伪造测试或静态 PASS。
- push/PR 可以触发 CI，但 CI workflow 不部署、不修改服务器、不触碰 Production 数据或凭据。
- 测试不存在、未运行、被环境阻塞或结果不完整时分别记录 `NOT_AVAILABLE`、`NOT_RUN`、`BLOCKED` 或 `PARTIAL`；绝不写 `PASS`。
- CI、GitHub Release/tag、Test health 和 Production 验收分别记录；一个通过不能推断另外三个通过。

## 必须停止的情况

- 无网络或 GitHub 读写不可用：保留本地结果，标记 remote 状态未知，不宣称同步完成。
- remote branch/commit 与预期不一致或远端有未处理变更（dirty remote）：停止，不 force-push、不覆盖、不静默合并。
- 发现 secret、token、cookie、私钥、环境文件或运行时数据：停止，不写入 Git、证据、日志或聊天，并报告需要的清理/轮换动作。
- 仅凭仓库名找到多个同名仓库：停止，要求 owner/full URL；不得猜测目标仓库。
- 任何测试、审计、权限或受控发布 gate 失败：保留失败证据，停止当前同步，不把失败改写成 PASS。

`github-continuity` 的目标是让下一次对话能从仓库事实继续工作，而不是复制聊天记录或制造第二套状态。
