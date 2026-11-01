# 验收记录

环境与时间：2026-11-01，当前主机 `sherhom`；Windows Python 3.12、WSL Ubuntu Python 3.14.4 / Git 2.53.0。仅记录实际观察。

## 已通过

- WSL 真实临时 Git bare 远端：11 项集成用例通过。包括文件原始字节与 WIP 保留、广播固定名单、按端回执、回复完成关联、无效路径、历史改写拒绝、并发 push 拒绝后保留双方消息、离线恢复同 ID 与原文件、跨日恢复、只有回执/登记更新时不调用 Agent、投递后崩溃不再入队、核实后恢复本地待办。
- Windows Python 3.12：同一组 11 项全部通过，用时 248.296 秒；WSL 同组用时 5.214 秒。此为隔离测试总耗时，不是运行中的单次轮询耗时。
- 独立 Skill 安装后，从单独复制的 `scripts/courier.py --version` 可运行（0.1.0），不依赖源目录导入。
- Skill Creator 的 quick_validate 校验通过；PyYAML 6.0.3 仅装入忽略的本地校验目录，未加入程序运行时依赖。
- WSL 用户 systemd 管理器只读检查为 `running`；这不等于服务已安装或已验证重启。
- work-tool 同一 origin 的 `agent-remote-controller` 和 `agent-courier-controller` 在初次只读查询中不存在。WSL 使用原生 Windows SSH 及已有 Windows known_hosts 可访问该 origin；没有放宽主机密钥检查。
- 首版源码 `dcba65af98a5f60ecb3a15aad846b90d52eeebdf` 已由 WSL Git push，HEAD、origin/master、远端 SHA 一致，ahead/behind 为 0/0。WSL 的 HTTPS push 使用已有 Windows Credential Manager，不复制或打印凭据。
- 基础分支建立提交 `16686b3376cb07d475eeee6b6a8c19b0b5cdafa8`；旧控制通道只读监听迁移提交 `b12c84c6908ffbe94e36de737804be76a64ba802`。本机已有扫描已通过应用工具更新为每分钟基础后备通信，尚未收到对端本次确认。
- Windows 实机安装发现 Codex MSIX 的 AppData 重定向：表面路径不能被 WSL 和系统任务直接访问。0.1.1 改为使用物理解析目录后，计划任务 `AgentCourier-work-default` 实际为 Running；主通道创建与本机登记提交为 `0b3bc1e43a5d70f7f92eab755fe0dc35ea17d9ce`。这不代表已验证桌面投递或重启。

## 尚待实机验收

- Windows 任务与 Linux 用户服务实际安装、启动、故障恢复、停用与卸载。
- Codex Queue 与指定桌面聊天属于同一服务、通知及执行结果可见。
- 对端真实主机名登记、基础通道每分钟扫描、拉取安装、Agent Courier 双向收发。
- 轮询耗时与资源开销、重新登录/重启后的恢复。
- 代码通过 WSL push 并核对远端；基础通道只在双机验证成功后注销。

模拟两端测试不能代替两台物理机器的结果。测试没有访问生产 Pod 页面或执行业务重建。
