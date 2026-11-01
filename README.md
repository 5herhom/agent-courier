# Agent Courier

通过 Git 在机器与 Agent 之间传递消息。Python 3.11+，运行时只用标准库；Git 和目标 Agent CLI 单独安装。支持 Windows 任务计划程序、Linux 用户 systemd 服务，首个适配器为 Codex Queue。

发送者只写普通短消息，复杂正文写 UTF-8 文件；程序维护 JSONL、文件快照、真实主机名、日期、广播名单和回执。没有新消息时只检查远端分支，不调用模型。

## 安装

```bash
git clone https://github.com/5herhom/agent-courier.git
cd agent-courier
python -m pip install .
agent-courier --version
```

也可直接运行 `python /绝对路径/agent-courier/scripts/courier.py`，无需 pip 安装。Windows 使用实际 Python 路径；Linux 通常为 `python3`。

配套 Skill 可以独立安装，安装器会复制同版本的程序，不依赖开发仓库路径：

```bash
python scripts/courier.py install-skill --target "$HOME/.codex/skills/agent-courier"
```

Windows 的目标通常为 `%USERPROFILE%/.codex/skills/agent-courier`。通过文件工具打开已安装的 `SKILL.md`；独立入口为该目录的 `scripts/courier.py`。

## 配置监听

机器身份每次取真实操作系统主机名并转小写，不生成 UUID 或身份文件。两台机器必须使用不同主机名；同机可用不同 `inbox`。不要凭备注推测目标机器名，先核对对方登记。

安装参数明确指定业务仓库、同一远端、通信分支和本机已有聊天。通信使用独立裸缓存，不切换业务工作区、不修改暂存区、不运行分支里的脚本和 hooks。

```bash
agent-courier install-listener --channel work \
  --repo /absolute/path/work-tool --remote origin \
  --branch agent-courier-controller --protocol-root agent-courier \
  --poll-interval-seconds 10 --inbox default \
  --codex-executable /absolute/path/to/codex --thread-id EXISTING_CHAT_ID \
  --description '本机备注' --dry-run
```

核对预览后去掉 `--dry-run`。首次创建不存在的通信分支还需明确加 `--create-branch`。普通 watch 不创建分支。`--manual` 仅配置端点而不安装任务，用于可控验收。

Windows 可加 `--git-backend wsl --wsl-distribution Ubuntu`：查询、fetch、push 由 WSL Git 执行，本地对象操作使用已解析的 Windows Git，减少 WSL 启动开销。WSL 须能使用同一远端的凭据和已验证的 SSH 主机信任；不自动接受未知主机密钥。特殊 SSH 环境可通过本机可信配置 `--git-ssh-command` 指定，不从消息正文读取。

后台任务固定解释器和程序绝对路径。安装目录移动后需重新安装；Windows 登录后启动、隐藏运行，同一个任务每分钟尝试恢复已退出的监听，存活时由 `IgnoreNew` 跳过；Linux 使用 `systemctl --user enable --now`，异常退出重启。十秒轮询在同一个常驻进程内执行，恢复触发器不调用模型。默认不改变账户、不设置 Linux linger，也不承诺无人登录运行。没有可用用户 systemd 时明确失败。

配置位于 Windows `%LOCALAPPDATA%/AgentCourier` 或 Linux `$XDG_STATE_HOME/agent-courier`（默认 `~/.local/state/agent-courier`）。`AGENT_COURIER_HOME` 可指定隔离目录。每个通道/收件箱分别保存 `config.json`、SQLite 检查点、Git 缓存、安装清单和日志。

可在配置中调整轮询间隔、Git 超时、失败退避上限、发布尝试次数、启动验证超时、任务失败重启策略、日志大小和备份数；不静默截断正文。默认轮询 10 秒、Git 超时 30 秒、退避上限 60 秒、日志每份 5 MiB/3 份备份。

## 发消息与文件

```bash
agent-courier send --channel work --to receiver-machine --message '请检查并回复。'
agent-courier send --channel work --to receiver-a --to receiver-b --message '请分别确认。'
agent-courier send --channel work --to '*' --message '请各端报告版本。'
```

`--to machine/inbox` 指定收件箱；省略时为 `default`。广播在发送时固定已启用的收件人，排除自身；后来登记的机器不补收，已登记的离线机器恢复后仍可收。每个收件端独立回执。

Agent 在调用前遵守默认 **1,000 Unicode 字符**规则：简单短文本不超过阈值时直接传 `--message`；超过阈值或包含代码、SQL、日志、复杂多行时，先用文件写入工具保存普通 UTF-8 文件，再发送固定模板。不要把长正文塞进 shell 的 echo、脚本或 JSON 请求中。生效阈值可通过 `status` 查询，接近阈值时直接用文件。

文件消息一律为：

```text
请阅读文件 @file(<仓库相对路径>)，按文件中的要求执行任务或回答问题。
```

例如正文保存在配置仓库的 `doc/tasks/检查任务.md`：

```bash
agent-courier send --channel work --to receiver-machine \
  --message '请阅读文件 @file(doc/tasks/检查任务.md)，按文件中的要求执行任务或回答问题。'
```

模板只替换路径，支持中文、空格和括号。程序在发布时冻结原始字节；源文件以后修改不影响已发布消息。接收端不需要相同目录，`read` 会返回本机实际快照路径。绝对路径、越界路径、仓库外符号链接在发布前拒绝；引用文件中的其他路径不递归上传。

## 读取、回复和状态

```bash
agent-courier read --channel work --message-id MESSAGE_ID
agent-courier read --channel work --message-id MESSAGE_ID --accept
agent-courier reply --channel work --in-reply-to MESSAGE_ID --message '检查已完成。' --complete
agent-courier ack --channel work --message-id MESSAGE_ID --status failed --detail '实际失败原因'
agent-courier status --channel work
agent-courier status --channel work --sync
```

读取默认不改变处理状态；接手时 `--accept` 记录 `started`。先核对返回的 `receiver_receipt`，已 completed 的消息不重复处理，`--accept` 不会将它退回 started。回复 `--complete` 同时为原消息追加 `completed`。回复文件使用同一模板。只能为本机实际收件端确认；完成、失败等回执需要真实依据。

状态区分 `received`、`delivered`、`started`、`completed`、`failed` 和 `delivery-unknown`。适配器要求 Queue 支持 `--thread` 和 `--message`；仅在本机帮助声明支持时附加 `--resume`，兼容没有该参数的版本。Codex Queue 成功退出仅证明接受入队，resume 的警告不证明开始执行；任务结果须以接收 Agent 的回执和回复判断。当前 Queue CLI 没有可靠的历史查询接口，未知投递保留待核实，不自动再入队。

```bash
agent-courier retry --channel work --message-id MESSAGE_ID
# 只有核实原聊天/队列确实没有接收后：
agent-courier retry --channel work --message-id MESSAGE_ID \
  --verified-not-delivered --evidence '核实来源与结果'
```

网络失败保留固定 ID、收件人和冻结文件；watch 后续重试发布。投递前持久化意图，崩溃恢复先 inspect；不能排除已入队时不会重放。Git 与 Agent 不是同一事务，不承诺端到端恰好一次。数据库丢失时，远端已有 received 但缺少本地关联的消息也保留未知状态。

```bash
agent-courier watch --channel work --once
agent-courier disable-listener --channel work
agent-courier uninstall-listener --channel work
```

停用/卸载先停止本机任务和下次启动，再更新端点登记；远端不可达时报告登记尚未更新，历史通信和检查点保留。不要同时在 Windows 与 WSL 用同一 `(machine,inbox)` 启动监听。

## 分支数据与验证

```text
agent-courier/
  participants/<machine>/<inbox>.json
  messages/YYYY-MM-DD.jsonl
  receipts/YYYY-MM-DD.jsonl
  payloads/<message_id>/<filename>
```

消息和回执由程序序列化。时间为带偏移 ISO 8601（默认 +08:00）；命令与回复按实际日期分文件。消息、回执和快照只追加，不改历史；首次发布提交由 Git 历史推导。检测到历史改写、重复 ID 或不兼容分支时停止新投递。并发被拒绝后重新基于最新远端按固定 ID 追加，不 force push。

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

测试使用真实临时 bare 仓库验证文件、广播、回执、并发、跨日、离线恢复和不确定投递。测试替身不调用真实 Agent。实机安装、桌面聊天可见性和双机回传属于独立验收，见 [实施清单](docs/IMPLEMENTATION.md) 与 [验收记录](docs/VALIDATION.md)。

通信传递内容，不创造业务权限。本项目不恢复已有 Pod 重建任务；持续停止要求保持有效。调试期间保留原基础通道，双向验证完成前不注销旧扫描。
