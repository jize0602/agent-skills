---
name: github-continuity
description: 从github读取、同步至github、备份至github，以及已接入项目的正式任务收尾。保存和恢复代码、需求、实现、历史、事故、经验、测试与部署事实；支持 Private 初始化、CI 回读、独立恢复和最近三份灾备维护，不代表部署授权。
---

# GitHub 项目接续

让仓库成为项目的长期事实来源，聊天只是操作入口。无需依赖旧聊天或反复上传 ZIP。先说明本次使用本技能及同步/读取模式。

## 识别请求和仓库

- `同步至github <项目名>`：同步当前项目至已确认的目标私有仓库。`从github <项目名>读取`：只读接手该仓库，不提交、部署或自动开始开发。
- `$github-continuity 同步至github owner/repo` / `$github-continuity 从github owner/repo读取` 是明确调用形式。
- 优先用户提供的 owner/repo，其次当前项目已验证 remote，最后通过现有 GitHub 连接查找当前账号的唯一同名仓库。多个匹配、身份冲突、项目与仓库不符时先询问，绝不猜测。
- 仓库确实不存在时只询问是否创建该名称的 Private 仓库；403/404 不能单独证明不存在，先核实账号与授权。新项目无名称时只询问名称与 Private 建库同意。用户已明确要求建立测试仓库时不重复询问。Public 必须单独明确确认。只让用户完成必要登录/OAuth，不索要聊天中的 token。
- 优先现有 GitHub connector 或已认证 Git CLI。浏览器登录不等于 CLI 已授权。遇到权限拒绝不得绕过；不要读取浏览器凭证、私钥或环境密钥。

## 读取模式

1. 读取默认分支当前 commit 和根目录 AGENTS.md、README、已有接手入口，按其引用阅读。用 [repository-contract.md](references/repository-contract.md) 检查信息完整性，不因文件名不同另建重复体系。
2. 在同一 commit 读取项目级 Skill、总体/当前需求、实现与验收进度、历史、决策、事故、经验、安全边界、任务、测试、部署和 handoff；检查关联 Issues/PR、最近 commits、Release/tag 和该 commit 的 CI。必须阅读实际源码核对关键实现；大型历史索引后按相关条目展开，事故和经验摘要必读。
3. 需要源码时在独立路径获取精确 commit，不覆盖用户脏工作树。明确 GitHub commit、源代码版本、部署版本之间区别；仓库记载的服务器状态不是本次实时检查。
4. 汇报当前版本、已完成、未完成、最近测试、阻塞、允许做什么和下一步。读仓库本身不授权云端写入、付费调用或恢复旧的 Production 批准。若用户同时说继续，再在明确任务范围内推进。
5. 缺少仓库权限时报告具体缺口；本地 ZIP 只能作为注明时间/版本的离线快照，不冒充最新仓库。

## 同步模式

1. 读取当前项目指令、git status/diff/remote、远端 HEAD 和已有 CI。确认源码根目录及提交范围；保留无关改动。读取上述 reference，只补缺少的接续信息。
2. 按 contract 映射检查并更新 Code、Requirements、Implementation、State、History、Decisions、Incidents、Lessons、Tasks、Tests、Deployment、Handoff、Changelog。没发生的事故不可虚构，未变化的信息不制造无意义更新。状态区分 PASS/FAIL/BLOCKED/NOT_RUN。部署状态标注验证时间、证据和权限，绝不把推送成功等同上线。
3. 密钥/私人数据检查：不提交 .env、真实 key/token/密码/私钥、运行数据库、session、uploads、logs、客户资料或认证浏览器状态。允许经扫描的不含真实值的 .env.example、合成 fixture。检查拟提交文件及将推送的历史；有泄漏则停止推送，只报告文件/类别不输出值。ZIP 等嵌套附件也要审计，不因仓库 private 放宽。正常图片/字体的逐文件人工复核凭据及 `PASS_WITH_REVIEW` 规则见 [operations.md](references/operations.md)；未知二进制不得自动放行。
4. 运行现有适用检查。配置或复用最小 GitHub Actions，让 push/PR 自动跑真实项目测试和安全检查，默认只读权限，不引入部署步骤、云端凭证、真实付费 AI 调用或 pull_request_target 执行不可信代码。普通本地 commit 不触发云端 CI，必须 push；缺少测试明确标注，不用空检查假装通过。
5. 仅提交已审查文件，禁止盲目 git add .、force push、删除远端历史。远端有新提交先检查差异；有语义冲突立即停止，不自动决定保留哪方，不覆盖他人工作。已有分支规则走普通分支/PR，不绕过保护。Issues 复用稳定任务ID，避免重复创建；不把本次范围扩为无关 issue 批量修改。推送前必须检查现有 workflow/webhook 的已知发布触发条件；若此次推送会触发未获授权的部署，则停止，不以“只是同步”为由触发发布，不私自关闭部署规则。
6. 用 Git CLI 正常推送；只有 connector 可写时可创建 Git tree/commit/ref，但先核对 parent HEAD、保留未改文件，验证远端最终 tree 与目标一致；不得声称未推送的本地历史也已同步。
7. 回读远端 commit 和关键状态文件，等待本次 commit 的 CI 结果。失败可修复本次引入的普通问题；权限/重大冲突停止，不弱化校验。CI尚在运行就标注 PENDING，不能报全部完成。
8. 做新对话接手检查：仅凭仓库入口能定位源码版本、需求、安全边界、待办、证据和下一步。如用户允许代理，仅用 Luna 做独立接手验证；不可用则等待，不换模型。报告仓库URL、commit、CI、剩余项。非用户要求不重复生成 ZIP。

## 持续维护与边界

初次接入时在项目 AGENTS.md 合并任务收尾规则：正式开发任务完成必须自动更新接续文件、检查、同步、等待 CI、远端回读，才算交付；以 `.ai/CONTINUITY.json` 的 `auto_sync` 记录项目授权。用户明确要求只读/不 push 时遵从，并标记交付未同步。只有完整闭环成功才写 `SYNC=PASS`。技能依靠支持本 Skill 的 Agent 执行，不是后台常驻程序；不能保证其他工具自动遵守。

## 初始化、备份和恢复

执行这些模式前阅读 [operations.md](references/operations.md)。确定性工具位于 `scripts/`：`context.py` 初始化/校验语义映射，`security.py` 检查工作树和历史，`backup.py` 打包/校验/独立恢复/生成精确轮换计划。命令行参数以各脚本 `--help` 为准。

`备份至github <项目名>` 先完成同步闭环，再将完整、已扫描快照上传 GitHub Release Asset，使用独立 continuity-backup 命名。必须下载远端资产重新检查 SHA、manifest、commit、关键文件并恢复到新目录；只生成本地 ZIP 不算备份成功。只保留最新三份已验证普通灾备，正式/生产/里程碑/用户锁定 Release 和全部 Git 历史永久保留。删除前主 Agent 复核精确 Release ID、metadata、当前数量，上传验证失败不删除旧备份。

初始化或重大 contract 变化后，执行仅访问远端仓库的独立 Resume Test，回答 operations.md 的二十项；文件齐全不等于接手测试通过。若子 Agent 受用户模型限制，严格遵守，不用其他模型替代。

技能是执行规范，不是常驻服务：不会后台自动监控，不保证未同步的聊天内容可恢复。只有实际 push 后 GitHub Actions 才自动检查。新设备/新对话需安装本技能或读取仓库入口，并具备私有仓库访问权限。可把本技能的副本放入项目既有技能目录供下一位 Agent 使用，但不要重复放置冲突版本。

不自动发布 Production、不修改共享服务、不重放旧部署确认、不把个人项目安全规则推广成所有项目部署权限。同步和读取都不得改变运行环境。记录通道的命令格式/能力及最后验证时间，绝不保存凭证。
