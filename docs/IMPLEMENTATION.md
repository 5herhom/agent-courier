# 实施与验收清单

依据：work-tool master 的 `doc/agent-courier/README.md`（固定 message 模板版本）。
编码遵循 work-tool `doc/开发规范/通用开发规范.md`；Java 专项规范已读取，本项目使用 Python。

当前目标包含以下完整交付，未验证项不能视为完成：

- [x] 协议、message CLI、文件快照、广播、独立 Git 传输、回执。
- [x] SQLite 检查点、并发追加、跨日恢复、未知投递先核实；两平台单实例锁实测通过。
- [x] Windows 计划任务与 Linux systemd 持久监听、异常退出恢复、停用/卸载/状态查询；未执行物理机器重启。
- [ ] Codex Queue 适配、同一桌面聊天的通知与执行结果证据。
- [x] 可单独安装的 Skill，固定模板及调用前转文件规则。
- [x] 真实临时 Git 远端的关键行为验证，Windows/WSL Linux 各 11 项通过，另补 Queue 参数兼容回归。
- [ ] 所有代码通过 WSL push 至 github.com/5herhom/agent-courier，并核对远端。
- [x] 对端真实主机名由其实际回执确认是 bronzehuang-pc1。
- [x] 通过 work-tool 的 agent-remote-controller 建立双方每分钟基础通信；收到对端迁移及安装阶段回执。
- [ ] 通过基础通信通知对端拉取代码、安装 Skill、完成双向 Agent Courier 调试。
- [ ] 调试前保留基础通信作为异常恢复通道，成功后注销双方旧 Agent 定时扫描。

生产 Pod 重建/重启的持续停止要求保持有效；本通信开发不恢复任何历史业务授权。
实施中只记录可复查的实际结果；系统重启、真实双机与桌面消息验证不能由模拟测试替代。
