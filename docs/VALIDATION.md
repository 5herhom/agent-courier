# 验收记录

环境与时间：2026-11-01，当前主机 `sherhom`；Windows Python 3.12、WSL Ubuntu Python 3.14.4 / Git 2.53.0。仅记录实际观察。

## 已通过

- WSL 真实临时 Git bare 远端：11 项集成用例通过。包括文件原始字节与 WIP 保留、广播固定名单、按端回执、回复完成关联、无效路径、历史改写拒绝、并发 push 拒绝后保留双方消息、离线恢复同 ID 与原文件、跨日恢复、只有回执/登记更新时不调用 Agent、投递后崩溃不再入队、核实后恢复本地待办。
- Windows Python 3.12：同一组 11 项全部通过，用时 248.296 秒；WSL 同组用时 5.214 秒。此为隔离测试总耗时，不是运行中的单次轮询耗时。
- 独立 Skill 安装后，从单独复制的 `scripts/courier.py --version` 可运行（0.1.0），不依赖源目录导入。
- Skill Creator 的 quick_validate 校验通过；PyYAML 6.0.3 仅装入忽略的本地校验目录，未加入程序运行时依赖。
- WSL 用户 systemd 隔离实机检查通过：安装后 enabled/active，受控终止自身 PID 后自动恢复新 PID，手动第二实例被锁拒绝，停用后 inactive/disabled，卸载后 not-found，Git 历史保留。测试仅操作 `agent-courier-startup-smoke-linux.service`，没有向模型投递消息。
- work-tool 同一 origin 的 `agent-remote-controller` 和 `agent-courier-controller` 在初次只读查询中不存在。WSL 使用原生 Windows SSH 及已有 Windows known_hosts 可访问该 origin；没有放宽主机密钥检查。
- 首版源码 `dcba65af98a5f60ecb3a15aad846b90d52eeebdf` 已由 WSL Git push，HEAD、origin/master、远端 SHA 一致，ahead/behind 为 0/0。WSL 的 HTTPS push 使用已有 Windows Credential Manager，不复制或打印凭据。
- 基础分支建立提交 `16686b3376cb07d475eeee6b6a8c19b0b5cdafa8`；旧控制通道只读监听迁移提交 `b12c84c6908ffbe94e36de737804be76a64ba802`。双方已有扫描均已改为每分钟基础通信；对端在旧分支提交 `3360f856418ee981901c8d52cd3d7c0c6a40425c` 确认其真实主机名为 `bronzehuang-pc1`、已有 `automation-2` 已迁移。
- Windows 实机安装发现 Codex MSIX 的 AppData 重定向：表面路径不能被 WSL 和系统任务直接访问。0.1.1 改为使用物理解析目录后，计划任务 `AgentCourier-work-default` 实际为 Running；主通道创建与本机登记提交为 `0b3bc1e43a5d70f7f92eab755fe0dc35ea17d9ce`。这不代表已验证桌面投递或重启。
- Windows 隔离任务完整检查通过：受控终止测试进程后约 48.938 秒恢复新 PID，第二实例被锁拒绝，停用后 Disabled，卸载后任务不存在，Git 历史保留。仅操作 `AgentCourier-startup-smoke-windows`。单独的 RestartOnFailure 未能在 90 秒内恢复受控终止的进程，加入同任务每分钟恢复触发器后通过。
- 实际短消息及 795 字节复杂文件已通过主通道冻结发布；删除发送端临时源文件后，快照 SHA-256 为 `5e223d4e4de141305c2175252d2bd048fe5f038911091a7b99d1e8498d02b5bc`，业务工作区与暂存区前后相同。两条通知均实际进入指定原聊天并触发后续模型轮次；模型通过 Skill 读取、接手并回复完成，发送端读取关联回复，原消息均为 completed。短消息回复提交 `c282f1dd0aa466effc3b26d62624ec8dda5e5087`，文件回复提交 `231adef8f9d7fdcc4d110d19e9b5f3408719bea6`。临时 validation 端点随后停用，历史保留，主监听继续运行。
- 对端基础回执提交 `caf22fd2df29aab25d0b24328e505ad1f74fb65e` 确认已安装独立 Skill 0.1.1，但其 Codex Queue 没有 `--resume`，适配器能力探测阶段终止安装，未注册端点或任务。0.1.2 按本机帮助检测该可选参数，随后恢复安装并完成以下双向实测。
- 0.1.2 补丁后 WSL 全部 12 项通过；Windows 新增的 Queue 可选参数兼容用例通过。已完成消息重复 read --accept 不新增 started 回执、不改变远端 HEAD，已纳入真实 Git 用例。
- 本机独立 Skill 已更新到 0.1.2，校验通过；原 `AgentCourier-work-default` 原位更新后为 Running，登记提交 `dcc69f4375bfec72c063c1d2250c6b345cf201f6`，原数据库与待处理消息保留。Windows CLI 在非 UTF-8 控制台环境下也输出有效 UTF-8 JSON。
- 文件通知实测发现 Windows `LK_LOCK` 仅尝试约十秒，CLI 会在合法长轮询持锁时误报单实例冲突。0.1.3 改为阻塞操作等待，单实例的非阻塞拒绝保持；真实子进程持锁 10.5 秒后，等待方正常取得锁，Windows 回归用时 10.872 秒。WSL 全部 13 项通过。本机已更新到 0.1.3、任务 Running，文件读取、回复与回执往返验证通过。
- 对端在基础分支提交 `7581ebc6a16dc6ddf2db69c0bc6653b67d066473` 确认独立 Skill 0.1.2、WSL Git、计划任务 Running 和十秒轮询，已发送短消息及 553 字节文件，文件摘要 `1bc7ddcb7885730f384b66b0f24d8da1b6e917919a365752937315fea551a5b0`。此阶段先确认发出和 Queue 接受，完整回复与桌面轮次证据随后分别核对。
- 双机实际往返随后通过：bronzehuang-pc1 发给 sherhom 的短消息和 553 字节文件均在原聊天触发模型、接手、回复并 completed；sherhom 发给 bronzehuang-pc1 的短消息及 1061 字节文件也均 completed 并回复，1061 字节 SHA-256 为 `b1ce8a19693a2bcce207c1541b848a3d108ef2507d2dabb231a6261be8b1efc2`，与发布前一致。双方临时源文件删除后快照仍可读取，业务工作区/暂存区保持原状。
- 对端基础诊断回执 `20261101-232339-bronzehuang-pc1-ui-probe-01`（分支提交 `e08b2424aa01f334b1e014e8e52fa5c730af657a`）确认：其桌面自带 CLI 为 0.165.0-alpha.1.2，不支持 --resume，但两条 Queue 通知在前台轮次结束后分别作为独立 userMessage 进入原聊天，模型轮次 `01a197b1-ee4d-7cb3-9205-9d6ecd83debb` 和 `01a197b2-7c46-7330-a540-d876e4454fe9`。未重复执行已 completed 的业务。0.1.3 两端任务均 Running。
- 已通过主通道发布最终切换请求 `msg-20261101-232732-073269-sherhom-default-bc5cb95eb9c2`，提交 `456890380101cbbb4f4b73fd84a447d8f06e457f`。对端回复 `msg-20261101-232939-823168-bronzehuang-pc1-default-84044ea56120`（提交 `3bca88e00955ec749cff4b18c8896fcf1177ece3`），确认应用工具删除旧 `automation-2`、配置目录不存在、主 Windows 任务仍 Running；本机已核对并记录 completed。
- 本机随后用应用工具删除旧 Codex 扫描 `d-data-code-work-tool-doc-knowledge-skills-coding-pod-rebuild-skill-md`，返回 deleted 并复核配置目录不存在；主 `AgentCourier-work-default` 保持 Running。基础分支历史保留，双方旧 Agent 扫描均已注销。
- 0.1.4 修复安装包模板被重复识别为第二个 Skill：模板改名为 guide.md，升级只清理本工具的旧嵌套 SKILL.md，保留用户其他文件。Windows 与 WSL Linux 均实测独立入口可运行、升级后只发现一个 Skill、从独立安装包再次分发仍可运行；补充仅结果回复使用 ack、避免确认循环的指引。
- 0.1.4 wheel 在临时隔离构建环境生成，安装到临时目录后可分发独立 Skill，独立入口输出 0.1.4 且只发现一个 SKILL.md。没有向系统 Python 安装构建依赖。本机同名主监听原位更新并保留原状态，登记提交 `f9eeef53f612debdd99588371ab7ae88983c4eb8`。
- 双方旧扫描关闭后才发布最终探针 `msg-20261101-233632-599789-sherhom-default-9ab18c10cf95`，首次提交 `822b13e5d9a1a6c5d5ae872882a7543d085d9a76`；2384 字节文件快照 SHA-256 为 `3c0c0d637405cfbec262b15b0de7345c6a7fc88435fbbff2e67280220ea2e95a`。对端确认独立 Queue userMessage 进入其原聊天，模型轮次 `01a197c1-dc32-7a43-8707-ccafcc725910`；已将源码及独立 Skill 更新为 0.1.4，主任务 Running、十秒轮询，旧 automation-2 仍不存在。
- 最终回信 `msg-20261101-233923-500923-bronzehuang-pc1-default-dc974a815492`，首次提交 `e16443a84d03a117546402f8a1746e824f85c15c`，也独立进入 sherhom 原聊天，触发模型轮次 `01a197c9-775b-7b40-acd0-48a20b041cd3`。已通过 Skill 核对原消息关联并接手；仅结果确认以 completed 回执收尾，不再生成回复。双方完成关闭旧扫描后的实际主通道往返。

## 验证范围

- 已验证持久任务配置、受控进程退出后恢复、双机短消息与文件、完成回执及真实桌面聊天唤醒；双方主监听最终均为 0.1.4。
- 未执行物理机器重启、注销后重新登录，以及长期 CPU/内存/网络开销基准；进程恢复结果不能代替这些实测。
- 源码和文档均经 WSL Git 正常 push，并核对本地、origin/master 与真实远端 SHA 一致；业务 work-tool 的工作区、暂存区和 master 保持原状。

模拟两端测试不能代替两台物理机器的结果。测试没有访问生产 Pod 页面或执行业务重建。
