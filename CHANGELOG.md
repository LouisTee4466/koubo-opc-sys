# 改动记录

按时间倒序。

## 2026-10-13 · 模型升级 + 流程修复 + 审计

- **模型**:默认改为 Sonnet 5.5(轻活)+ Opus 5.5(质量活)。这两个模型不接受强制 tool use,结构化输出改走 `output_config.format`;新增 `EFFORT_CHEAP` / `EFFORT_QUALITY` 控制思考深度;遇安全分类器误拒由服务端自动换模型重试。Anthropic SDK 最低版本升到 1.13.0。
- **Webhook 游标**:原版每次都从最旧的 payload 读起且只看一页,积压超过 50 条后就收不到新变更(改了状态没反应)。现在游标存在 Modal Dict,每次从上次位置翻页读到最新。
- **审核提示对齐代码**:出片后的提示改为「把 `Heygen Status` 改 `approved`」;发布日期留空 = 下一次每日定时任务发布(原来留空永远不发),过期日期也会补发。
- **重新出片**:在「等待视频审核」把 `Heygen Status` 改回 `ready` 现在会真的重新生成(原来没反应)。
- **Modal Secret 不再被清空**:原版在安装第 7 步和 `webhook_register.py setup` 里用 `modal secret create ... --force` 只传 1–2 个 key,会把整份 Secret 覆盖、其余 API key 全丢。现在统一用 `--from-dotenv .env --force` 同步完整的 `.env`。
- **去重锁 + cron 拆分**:四个阶段处理函数加了原子去重锁(Airtable 通知至少送达一次、容器可能并发);每日 cron 的发布 / 数据回收 / webhook 续期拆成独立函数并行跑。
- **Blotato 数据回收修复**:原版打的 analytics 端点不存在该用法,Video Stats 一直写 0;现改为 published-posts → posts/{id}/analytics,复盘终于有真实数据。
- **Tavily 认证修复**:改为 Authorization: Bearer,原版把 key 放在 body 会 401。

## 2026-07-02 · 输出质量升级

- **自动重试**:所有外部调用(Airtable / HeyGen / Blotato / Tavily / TikHub / Claude)遇 429/5xx/网络抖动自动指数退避重试,大多自愈,不再一次报错就整条失败。
- **结构化输出**:所有 AI 调用按 JSON Schema 返回已解析结果,消灭手撕 JSON 的解析失败丢件。
- **文案三步走**:起草 → AI 主编挑刺 → 修订,钩子更狠、AI 腔更少,交付的是修订稿。
- **情报升级**:竞品每账号抓 20 条按**互动率**排序取前 5(不再是"最新 5 条"),选题参考的是"最能打"的内容。
- **模型分层**:轻活(选题/审核/平台改写)用 Sonnet 5.5,质量活(口播正文/周复盘)用 Opus 5.5;可用 `MODEL_CHEAP` / `MODEL_QUALITY` 环境变量覆盖。
- **学你怎么改稿**:周复盘会对照「AI 原稿 vs 你的终稿」,提炼你的改稿偏好写回经验库,下次起草直接照做;经验库自动截断防止无限膨胀。

## 2026-07-01 · 交付前修复

**修好两个原版的坑**(团队装完可稳定长期运行):
- **周复盘崩溃**:`modal_app.py` 周复盘因一处 f-string 写法每周必崩,已修复(不影响功能逻辑)。
- **Webhook 续期隐患**:原版在云端容器里用 `modal` 命令行续期(容器内无凭证会失败),已改用 Airtable 官方 `refresh` 接口(不换 ID/密钥,更稳)。

**新增 成本护栏 + 出错告警**:
- **预算护栏**:累计 HeyGen 花费到 `MONTHLY_BUDGET_USD` 上限时**自动暂停出视频**;跨过 50%/80%/100% 会告警。每月自动重置。
- **出错告警**:任何环节失败,主动推 `ALERT_WEBHOOK_URL`(Slack/Discord),不再只躺在 Airtable 的 Notes 字段。留空则只记日志。

**清理**:移除原作者署名(SOP 文档标题、截图里的工作区名)和硬编码的作者服务器地址;拉平目录、删 Mac 垃圾与重复文件。
