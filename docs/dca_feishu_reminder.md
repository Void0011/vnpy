# ETF 定投策略飞书提醒

该提醒任务在 GitHub Actions 上以无界面方式运行，不加载 PySide6、TA-Lib，也不会启动或修改本地交易界面。它只计算并推送以下三个周期策略：

- 动态回撤阶梯
- 长均线偏离度
- 价值平均

默认工作流在北京时间每个工作日 17:17 检查一次。脚本会继续验证交易日、最新行情日期、周期是否到期和消息是否已经发送，因此 GitHub cron 并不直接等同于交易周期。

## 上线前配置

1. 修改 `.github/dca-reminder.json` 中的 ETF、金额和价值平均参数。第一版最多支持 10 个 ETF。
2. 在 GitHub 仓库的 `Settings → Secrets and variables → Actions` 中创建：
   - Secret `FEISHU_WEBHOOK_URL`：单人私有群的机器人 Webhook。
   - Secret `FEISHU_WEBHOOK_SECRET`：可选，机器人启用“签名校验”时填写。
   - Secret `DCA_REMINDER_CONFIG_JSON`：可选。填写完整配置 JSON 后会覆盖仓库中的配置文件，适合隐藏持仓和目标金额。
   - Variable `DCA_CYCLE_ANCHOR`：可选，全局覆盖配置中的周期锚点。
   - Variable `DCA_CYCLE_DAYS`：可选，全局覆盖周期天数，正式环境建议为 `20`。
   - Variable `DCA_REMINDER_ENABLED`：确认配置无误后设置为 `true`，定时任务才会实际启用。
3. 从 Actions 页面手工运行 `ETF DCA reminder`，第一次保留默认的 `force_send=true`、`dry_run=true`，检查日志中的计算和卡片 JSON。
4. 再手工运行一次，将 `dry_run` 改为 `false`，验证飞书私有群能收到交互式策略看板。
5. 最后设置 `DCA_REMINDER_ENABLED=true`，开启工作日定时检查。

幂等状态需要由 Action 提交回默认分支。请确认仓库 `Settings → Actions → General → Workflow permissions` 允许工作流获得写权限；如果默认分支有保护规则，还需要允许 GitHub Actions Bot 更新 `.github/dca-reminder-state.json`，否则消息虽然可能已经送达，状态提交仍会失败并在下次运行时存在重复推送风险。

周期锚点表示“首个计划执行交易日”。如果锚点本身不是交易日，脚本会把锚点之后有行情的第一个交易日作为周期索引 0，之后每隔配置的交易日数触发一次。

## ETF 配置

```json
{
  "cycle_anchor_date": "2026-07-01",
  "cycle_days": 20,
  "etfs": [
    {
      "symbol": "159792",
      "name": "港股通互联网ETF富国",
      "price_adjustment": "qfq",
      "base_amount": 4000,
      "drawdown_lookback_days": 120,
      "bias_lookback_days": 120,
      "target_growth": 4000,
      "value_averaging_mode": "simulated",
      "position_volume": 0,
      "completed_periods": 0
    }
  ]
}
```

159792 的基金全称为“富国中证港股通互联网交易型开放式指数证券投资基金”，场内简称“港股通互联网ETF富国”，参见[基金管理人产品页](https://wap.fullgoal.com.cn/fundDetail/159792/index.html)。正式配置保留每期基础金额 4000 元、120 日参考窗口、价值平均每期增长 4000 元、20 个交易日周期和 2026-07-01 锚点。

`price_adjustment` 默认使用 `qfq`（前复权），避免分红或份额调整被误判为大幅回撤。若需要和当前桌面回测的未复权行情严格一致，可以设置为 `none`；此时应自行核查除权事件。

`simulated` 模式会从周期锚点开始，假设每次历史建议都按当日收盘价完全成交。看板会明确标注“模拟持仓结果”。

如需按真实持仓计算，将模式改成：

```json
{
  "value_averaging_mode": "actual",
  "position_volume": 12000,
  "completed_periods": 8
}
```

其中 `position_volume` 是当前实际份数，`completed_periods` 是已经完成的价值平均期数。真实成交后需要人工更新这两个值；提醒任务不会连接券商或推定提醒已经成交。

## 测试和回放

本地只生成看板、不发送消息：

```powershell
$env:DCA_REQUIRE_CURRENT_TRADE_DATE="false"
python scripts/dca_reminder.py --force-send --dry-run
```

手工 Action 的 `force_send=true` 可以绕过 20 日周期限制，但不会绕过行情日期校验。`as_of_date` 可用于历史回放；历史回放建议同时保持 `dry_run=true`。

## 重复保护与失败告警

成功发送后，任务会更新 `.github/dca-reminder-state.json` 并由 GitHub Actions Bot 提交到当前默认分支。状态保存已发送消息键的历史列表，并兼容旧的单条消息键；相同 ETF、信号日期、周期和配置摘要不会再次发送，即使中间已发过另一条消息。试运行和发送失败均不会写入成功状态。工作流还配置了并发锁，避免定时与手工任务同时运行。

普通定时运行在交易所休市日直接跳过；强制验收可绕过休市日和周期门槛，但仍检查行情不得晚于运行日期，并在运行日为交易日时要求当日行情。建议在交易日收盘数据更新后进行正式验收。

如果行情下载、日期校验、飞书发送或状态提交失败，Action 会失败，并尝试向同一机器人发送红色失败卡片。GitHub 定时任务仍可能延迟或漏跑，必要时可从 Actions 页面手工补跑。

## 安全注意事项

- Webhook 只能保存为 GitHub Secret，不得写入配置、源码、提交记录或日志。
- 仓库公开时，建议将带有真实持仓和目标金额的完整配置保存到 `DCA_REMINDER_CONFIG_JSON` Secret。
- Webhook 一旦曾经出现在聊天、日志或提交中，应在飞书中重新生成后再上线。
- 看板金额使用日线收盘价估算，不代表下一交易日一定可以按该价格成交。默认技术指标使用前复权历史价格，最新价格仍对应当前交易单位。
