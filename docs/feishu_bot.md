# 飞书定投助手：创建应用、部署与使用

本版采用“趋势优先”卡片，支持本人私聊、群内 @机器人查询配置、请求推送、增删产品及修改参数。指令由确定性规则解析，不调用大模型、不执行聊天中的代码，不连接券商、不下单。不能确定的指令会返回帮助而不修改配置。

## 运行方式与部署建议

机器人通过飞书官方 Python SDK 的 WebSocket 长连接接收消息和卡片按钮事件，不需要公网 HTTP 回调地址或域名。必须有持续运行的云端进程；本机/Codex 可以关闭。GitHub Actions 保留作为旧提醒和 CI，不能充当常驻对话接收服务。

建议先用一台小规格 Linux 云服务器运行一个 Docker 容器。起步可考虑 **2 核 / 2 GB、Ubuntu 24.04 LTS、支持公网出站访问**，这是部署起点建议，不是已做过的容量承诺。阿里云轻量应用服务器等同类服务均可；购买前以控制台当前价格、续费价格及地域网络实测为准。本次代码不购买服务器、不创建收费资源。

参考：[阿里云轻量服务器](https://www.alibabacloud.com/help/zh/simple-application-server/product-overview/what-is-simple-application-server)、[Docker Ubuntu 安装](https://docs.docker.com/engine/install/ubuntu/)、[飞书官方 SDK](https://github.com/larksuite/oapi-sdk-python)。

数据存在容器命名卷 `bot-data` 的 SQLite 中；容器升级和重启不删除数据。仅部署一个实例，暂不支持多副本调度。不要运行 `docker compose down -v`，它会删除配置、历史和去重记录。

## 1. 创建飞书应用

1. 打开 [飞书开发者后台](https://open.feishu.cn/app)，创建**企业自建应用**，名称可填“定投助手”。
2. 在“添加应用能力”中添加**机器人**。
3. 在“凭证与基础信息”查看 App ID 和 App Secret。App Secret 只填到服务器的 `.env.feishu`，不要放到聊天、仓库或截图中。
4. 在“权限管理”按以下功能名查找并申请最小权限；实际名称以当前控制台为准：

| 功能 | 常见权限标识 |
|---|---|
| 接收用户发给机器人的单聊消息 | `im:message.p2p_msg:readonly` |
| 接收群聊中 @机器人的消息 | `im:message.group_at_msg:readonly` |
| 以应用身份发送消息 | `im:message:send_as_bot` |

不需要读取整个群聊历史、通讯录、券商或文件权限。若控制台在添加消息事件时提示关联权限，应选对应的私聊及群聊 @权限。

5. 在“版本管理与发布”创建版本，将可用范围先限定为本人并发布；若企业要求审批，需要管理员批准。
6. 先完成下面的服务器启动。再进入“事件与回调”，选择**使用长连接接收事件**，保存以下订阅。部分控制台需要检测到运行中的 SDK 连接才能保存：
   - 事件：接收消息 `im.message.receive_v1`。
   - 回调：卡片回传交互 `card.action.trigger`。
7. 如果修改订阅/权限后要求重新发布版本，完成发布。将应用机器人加到需要操作的群里。群需要允许该应用加入；若当前外部群不可用，先用应用私聊验收。

官方接口入口：[接收消息](https://open.feishu.cn/document/server-docs/im-v1/message/events/receive)、[SDK 事件处理](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/server-side-sdk/python--sdk/handle-events)、[卡片回调](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/server-side-sdk/python--sdk/handle-callbacks)。

## 2. 在云服务器启动

按 Docker 官方文档安装 Docker Engine 和 Compose 插件，然后在服务器执行：

```bash
git clone --branch codex/dca-live --single-branch https://github.com/Void0011/vnpy.git
cd vnpy
cp deploy/feishu-bot/.env.example .env.feishu
chmod 600 .env.feishu
```

用服务器编辑器填写 `.env.feishu`：

```dotenv
FEISHU_APP_ID=你的应用ID
FEISHU_APP_SECRET=你的应用密钥
FEISHU_ALLOWED_OPEN_IDS=
DCA_BOT_SCHEDULE_ENABLED=false
```

第一次留空操作人并保持定时关闭。此时只开放“身份”指令，其他用户和配置/推送操作全部拒绝。

```bash
docker compose -f deploy/feishu-bot/compose.yaml up -d --build
docker compose -f deploy/feishu-bot/compose.yaml logs --tail=50
```

完成事件订阅后，私聊机器人发送 **身份**。它只返回发送者自己的应用内 open_id 和当前会话 ID。将你自己的 open_id 填到 `FEISHU_ALLOWED_OPEN_IDS`，再次执行 `up -d` 重新创建容器。不要填群成员其他人的 ID。

机器人启动时通过飞书接口获取自身 open_id，群内消息必须真正 @该机器人才能触发；仅手写机器人的名字不会触发。其他成员即使点击确认卡片，也没有权限修改配置。

## 3. 对话指令

私聊直接发送；群聊先 @机器人。多个参数以空格分隔，名称含空格时加英文双引号。

| 指令 | 用途 |
|---|---|
| `帮助` | 显示支持的指令 |
| `身份` | 查看自己的应用内用户 ID，用于首次绑定 |
| `产品列表` | 查看全部产品及启用状态 |
| `查看 159792` | 查看初始参数及按期生效的变更记录 |
| `状态` | 查看部署层定时开关、目标会话和待发送数量 |
| `推送 159792` / `推送全部` | 向当前会话发送标记为测试的即时看板 |
| `修改 159792 基础金额=5000 窗口=120 VA增长=4000` | 预览下一期生效的参数变更 |
| `把159792的基础金额改成5000元，从下期生效` | 支持的自然语言形式；其他模糊表达会要求按明确格式输入 |
| `新增ETF 六位代码 名称=正式名称 基础金额=2000 周期=20 锚点=YYYY-MM-DD` | 新增产品，先检查行情是否可用 |
| `暂停 159792` / `恢复 159792` | 暂停或恢复周期提醒；历史模拟仍按计划计算 |
| `删除 159792` | 确认后删除提醒产品 |
| `重建计划 159792 周期=20 锚点=YYYY-MM-DD` | 重置模拟计划，必须单独确认 |
| `绑定推送` | 将定时提醒发往当前私聊或群聊；先预览再确认 |
| `修改记录` | 查看最近 10 个配置版本的修改摘要 |
| `确认 变更码` / `取消 变更码` | 按卡片显示的变更码确认或取消 |

按钮确认和文字确认等价。预览有效期为 10 分钟，绑定发起人及原会话；配置版本已经变化时，旧预览不能覆盖新配置。

## 4. 参数与模拟持仓的生效方式

- 保留已有 159792 参数、2026-07-01 锚点、20 个交易日周期和模拟持仓。
- 金额/窗口修改默认从下一期生效。用周期索引保存生效时点，不猜测未来交易日历；已经完成的模拟期次继续使用当时参数。
- 例：前三期目标增长为 4000、4000、5000 元时，第 3 期累计目标为 **13000 元**，不是把历史改成 `3×5000`。
- 本期已到期时提交修改，仍从下一期生效。锚点尚未到达时，修改从第一期生效。
- 重建计划将清空待生效变更和模拟起始持仓，按新锚点重新模拟；预览会明确提示该影响。
- 新产品必须显式填写锚点和名称，避免悄悄沿用旧产品的起点。第一版最多 10 个境内场内 ETF，每期金额上限 1000 万元，周期 1–250 个交易日，窗口 1–1000 日。
- “推送”使用最近已收盘且通过交易日历校验的行情，属于强制测试，不消耗正式周期的发送记录。
- 目前不是任意自然语言聊天机器人，不提供选股建议或自动交易。

## 5. 接管正式定时提醒

正式提醒的配置源应只有一个。机器人首次启动从 `.github/dca-reminder.json` 导入配置，此后以数据库为准。GitHub 上的 JSON 不会被聊天修改覆盖，且后续编辑该 JSON 不会自动覆盖数据库。

建议按此顺序验收和切换：

1. 保持 `DCA_BOT_SCHEDULE_ENABLED=false`，完成私聊“帮助”、群内 @、身份限制及一张即时看板的验收。
2. 在希望接收正式提醒的会话发送“绑定推送”，核对预览并确认。
3. 到 [GitHub Actions Variables](https://github.com/Void0011/vnpy/settings/variables/actions) 将旧 `DCA_REMINDER_ENABLED` 改为 `false`，等待已运行任务完成。
4. 在服务器运行 `git pull --ff-only` 拉取最新发送记录，再将 `.env.feishu` 的 `DCA_BOT_SCHEDULE_ENABLED` 改为 `true`，执行 `docker compose -f deploy/feishu-bot/compose.yaml up -d --build`。本次必须重建镜像，才能导入刚停止的旧工作流的最新记录。
5. 向应用机器人发送“状态”，确认新定时已开启且会话正确。

云端进程每 15 分钟核查最近已到达的工作日北京时间 **17:17** 检查点，因此通常在 17:17–17:32 之间开始检查；仍取决于服务器、网络和行情数据可用性。跨午夜延迟按前一计划检查日处理，修复此前次日凌晨要求尚未生成收盘行情的问题。交易日历不可用时停止计算并重试，不将节假日猜成交易日。

每个产品独立计算、独立去重和排队发送；一个产品失败不会阻止其他产品。失败提示同一产品同一检查日只排队一次，计算约 15 分钟后重试。重启后检查最近计划日，不自动追补任意久远的漏发周期。

发送队列先落库，再调用飞书；调用失败使用相同 UUID 重试。平台 UUID 去重存在平台自身的有效期限制，不能承诺跨无限时间的网络故障也绝不重复。管理员应关注长期积压的“待发送消息”计数和容器健康状态。

迁移前，旧 GitHub 提醒继续使用原 Webhook；迁移后只启用应用机器人的定时。启动时会将镜像内 `.github/dca-reminder-state.json` 的已发送标识增量导入数据库，以跳过同一配置、同一日期已发送的产品；已有数据库记录不会被覆盖。当前单产品旧记录和新版按产品记录可直接迁移，早期多产品合并卡片记录不能拆分，应在非到期日切换并核对。之后两套账本不会自动同步，**不要同时开启两套定时**。

## 趋势优先卡片

- 原生 Feishu Card JSON 2.0，包含真实最近 60 根日线的收盘价与配置窗口的移动平均线。
- 历史均线只使用该日及以前的数据；窗口不足时使用已有数据。
- 每个产品单独一张卡片，包含周期进度、三种策略金额和份数、明确的模拟持仓标记以及可展开的计算口径。
- 三策略属于独立方案，金额不相加执行；测试强推与正式周期提醒有不同标题。
- 使用前请在飞书实际客户端验收图表和折叠组件；本地单元测试只能验证数据与卡片结构，不能替代客户端渲染验收。

## 开发验证与运维

```powershell
.venv/Scripts/python.exe -m pip install -r requirements-feishu-bot.txt
./scripts/dev.ps1 test
./scripts/dev.ps1 check
.venv/Scripts/python.exe -m scripts.feishu_bot --check
```

`--check` 只输出是否已配置凭据，不输出密钥，不连接飞书；没有凭据时返回非零退出码。独立 CI 运行提醒、机器人业务和官方 SDK 适配测试，并构建/检查实际 Linux 容器；不使用真实凭据、不发送消息。

升级时先备份命名卷中的 SQLite，再拉取代码并执行 `up -d --build`。使用 SQLite backup 接口或停机备份，不应只复制运行中数据库的主文件而遗漏 WAL。`.env.feishu`、`.bot-data/` 和 Docker 卷均不得提交到公共仓库。不要把多个服务器同时连接到同一个数据库文件。

旧工作流和桌面回测的说明分别见 [飞书提醒](dca_feishu_reminder.md) 和 [桌面开发](dca_development.md)。
