# 🎬 Oral Video Director — 系统文档 & 安装指南

---

## 🤖 CLAUDE CODE 自动指令（用户无需阅读此部分）

**如果用户输入了 "start the agent"，你必须立即执行以下操作，不要等待，不要让用户自己看文档：**

1. 用中文向用户打招呼，介绍这是「Oral Video Director — 全自动口播视频生产系统」
2. 告诉用户整个安装过程约 30-45 分钟，你会一步一步引导
3. 立刻开始执行下方「第 5 章：逐步安装流程」，**每完成一步才进入下一步，每步都要等用户确认或提供信息**
4. 所有步骤中需要用户提供的信息，**主动用中文提问**，不要让用户自己去看文档
5. 用户提供信息后，**立刻帮用户写入 `.env` 文件**，不要让用户自己编辑

**重要：用户只会说 "start the agent"，其他什么都不会做。你要全程主导。**

---

---

## 目录

1. [系统简介](#1-系统简介)
2. [完整状态机流程](#2-完整状态机流程)
3. [Airtable 数据库结构](#3-airtable-数据库结构)
4. [API 账号清单](#4-api-账号清单)
5. [逐步安装流程](#5-逐步安装流程)
6. [日常使用流程](#6-日常使用流程)
7. [常见问题排查](#7-常见问题排查)

---

## 1. 系统简介

**Oral Video Director** 是一套全自动口播视频生产系统，专为个人品牌和社媒运营者设计。

### 🔄 系统做什么

每天自动完成以下流程：

1. **情报采集** — 抓取 TikTok 竞品账号最新视频数据，结合 AI 分析行业趋势
2. **选题生成** — 基于情报输出 10 条潜力选题，等待人工选择
3. **文案生成** — 根据确认选题自动生成完整口播脚本（Hook + 正文 + CTA）
4. **视频生成** — 调用 HeyGen 数字人接口，自动生成口播视频
5. **社媒发布** — 通过 Blotato 自动发布到 Instagram 和 Facebook
6. **数据回收** — 24h / 72h 后自动拉取播放量、点赞、评论、分享数据
7. **周复盘** — 每周一自动分析数据，更新账号经验库，指导下周选题

### 🏗 技术栈

| 组件 | 用途 |
|------|------|
| Modal | 无服务器云端运行，Cron 调度，Webhook 端点 |
| Airtable | 数据库 + 人工审核界面 |
| Anthropic Claude | 情报分析、选题、文案生成、周复盘 |
| HeyGen | 数字人视频生成 |
| Blotato | 多平台社媒自动发布 + 数据分析 |
| Tavily | 网络搜索情报采集 |
| TikHub | TikTok 竞品数据抓取 |

---

## 2. 完整状态机流程

### 📋 状态总览

```
情报采集中 → 等待选题 → 文案生成中 → 等待文案审核
     → 等待视频生成 → 视频生成中 → 等待视频审核 → 待发布 → 发布中 → 已完成
```

失败状态：任意阶段出错 → `失败`（查看 Notes 备注字段了解原因）

---

### 🔁 详细阶段说明

#### 阶段 1：`情报采集中`
- **触发**：每天 EST 12:00（UTC 17:00）Cron 自动触发，或手动新建 Pipeline 记录
- **系统做什么**：
  - 调用 TikHub 抓取竞品 TikTok 账号最新视频
  - 调用 Tavily 搜索行业关键词相关内容
  - 用 Claude 分析情报，提炼趋势洞察
- **输出**：情报数据写入 `Intelligence Data 情报采集结果` 字段

#### 阶段 2：`等待选题`
- **触发**：情报采集完成后自动进入
- **系统做什么**：基于情报生成 10 条选题，写入 `Topics List 选题列表`
- **👤 用户需要做什么**：
  1. 打开 Airtable，查看 `Topics List 选题列表`
  2. 选择最想做的那条，把选题标题 + 角度填入 `Final Topic 最终选题` 字段
  3. 将 `Topic Status` 改为 `done`
- **下一步触发**：Airtable Webhook 检测到 `Topic Status = done` → 自动进入文案生成

#### 阶段 3：`文案生成中`
- **触发**：`Topic Status` 改为 `done` 后自动触发
- **系统做什么**：
  - 根据选题 + Brand Config 生成 3 个 Hook 备选
  - 生成口播正文
  - 生成 2 个 CTA 备选
  - 自动做内容风险审核
- **输出**：写入 `Script Hooks 钩子`、`Script Body 口播正文`、`Script CTAs`

#### 阶段 4：`等待文案审核`
- **触发**：文案生成完成后自动进入
- **👤 用户需要做什么**：
  1. 查看 `Script Hooks 钩子`（选一个你喜欢的开头）
  2. 查看 `Script Body 口播正文`（可以手动修改）
  3. 查看 `Script CTAs`（选一个结尾）
  4. 把选好的内容拼接后填入 `Final Script 完整口播文案`
  5. 将 `Review Status` 改为 `approved`
- **下一步触发**：Webhook 检测到 `Review Status = approved` → 自动生成四平台文案

#### 阶段 5：`等待视频生成`
- **触发**：文案审核通过后自动进入
- **系统做什么**：生成 TikTok、Instagram、Facebook、YouTube 各平台 Caption
- **👤 用户需要做什么**：
  1. 确认各平台 Caption 字段内容（可手动微调）
  2. 将 `Heygen Status` 改为 `ready`
- **下一步触发**：Webhook 检测到 `Heygen Status = ready` → 自动提交 HeyGen

#### 阶段 6：`视频生成中`
- **触发**：`Heygen Status = ready` 后自动触发
- **系统做什么**：
  - 调用 HeyGen API 提交视频生成任务
  - 每 5 分钟轮询一次状态（最多等 2 小时）
  - 完成后自动下载视频，上传到 Airtable `Video File 视频` 字段
- **输出**：视频文件写入 `Video File 视频` 附件字段

#### 阶段 7：`等待视频审核`
- **触发**：HeyGen 视频生成完成后自动进入
- **👤 用户需要做什么**：
  1. 点击 `Video File 视频` 预览视频
  2. 满意 →
     - （可选）填写 `Scheduled Publish Time 发布日期`（不填 = 下一次每日 Cron 发布；填了 = 到那天发布）
     - 将 `Heygen Status` 改为 `approved`
  3. 不满意 → 修改 `Final Script`，再将 `Heygen Status` 改为 `ready` 重新生成（会再收一次 HeyGen 渲染费）

#### 阶段 8：`待发布`
- **触发**：`Heygen Status = approved` 后自动进入
- **等待**：Cron 每天 EST 12:00（UTC 17:00）发布「发布日期留空，或发布日期已到」的记录
- **提醒**：日期按 UTC 计算；想延后发布就填未来日期，留空则下一次 Cron 就发

#### 阶段 9：`发布中`
- **触发**：Cron 检测到待发布记录
- **系统做什么**：
  - 把视频转存到 Blotato CDN
  - 分别发布到 Instagram（Reel）和 Facebook（Reel）
  - 写入 `Publish Links 发布链接` 字段

#### 阶段 10：`已完成`
- **触发**：发布成功后自动进入
- **后续**：
  - 24h 后：自动拉取播放、点赞、评论、分享数据
  - 72h 后：再次拉取数据
  - 每周一：纳入周复盘分析

---

## 3. Airtable 数据库结构

需要在 Airtable 创建 **1 个 Base**，**3 张表**：

---

### 📋 表 1：Brand Config（品牌配置表）

> 只有 **1 行数据**，代表你的账号配置。

| 字段名 | 字段类型 | 说明 |
|--------|----------|------|
| Account Name 账号名称 | Single line text | 你的账号名称，如 "AI工具侦探" |
| Industry 行业 | Single line text | 所在行业，如 "AI tools automation" |
| Positioning 账号定位 | Long text | 账号定位描述，1-3 句话 |
| Target Audience 目标受众 | Long text | 目标受众画像，如 "25-40岁的中小企业主" |
| Key Selling Points 核心卖点 | Long text | 你的产品/服务核心价值，可列多条 |
| TikTok Competitor Accounts TT对标账号 | Single line text | 竞品 TikTok 用户名，英文逗号分隔，如 "user1,user2,user3" |
| HeyGen Avatar ID 数字人ID | Single line text | 你的 HeyGen 数字人 ID（见下方说明） |
| Heygen Voice ID 音色ID | Single line text | 你的 HeyGen 音色 ID（见下方说明） |
| AI Profile Summary 账号定位 | Long text | **系统自动生成**，首次运行后自动填入，无需手动填 |
| AI Experience Notes 经验结论 | Long text | **系统自动更新**，每周复盘后追加，无需手动填 |

**⚠️ 关于 HeyGen Avatar ID 和 Voice ID 怎么找：**

1. 登录 [HeyGen 后台](https://app.heygen.com)
2. **Avatar ID**：左侧菜单 → Avatars → 点击你要用的数字人 → URL 里的 ID，或者通过 API `GET /v2/avatars` 获取 avatar_id 字段
3. **Voice ID**：左侧菜单 → Voices → 点击你要用的声音 → 同样从 URL 或 API `GET /v2/voices` 获取 voice_id 字段

系统优先读取 Brand Config 表里的 ID，如果为空则 fallback 到 `.env` 里的 `HEYGEN_AVATAR_ID` / `HEYGEN_VOICE_ID`。

---

### 📋 表 2：Content Pipeline（内容流水线表）

> 每天自动新建 1 行，代表一个内容制作周期。

| 字段名 | 字段类型 | 说明 |
|--------|----------|------|
| Run ID | Single line text | 系统自动生成，格式 `RUN-20240614-ABCDEF` |
| Created At 创建时间 | Date | 记录创建日期 |
| Current Stage 当前状态 | Single select | 状态机当前阶段（见第 2 章） |
| Topic Status | Single select | `pending` / `done` / `failed` |
| Review Status | Single select | `pending` / `approved` |
| Heygen Status | Single select | `ready` / `processing` / `done` / `approved` |
| Intelligence Data 情报采集结果 | Long text | AI 情报采集原始数据（JSON） |
| Topics List 选题列表 | Long text | 10 条 AI 生成选题（JSON 数组） |
| Final Topic 最终选题 | Long text | 你选定的选题标题 + 角度 |
| Script Hooks 钩子 | Long text | 3 个 Hook 备选（JSON 数组） |
| Script Body 口播正文 | Long text | 口播正文内容 |
| Script CTAs | Long text | 2 个 CTA 备选（JSON 数组） |
| Final Script 完整口播文案 | Long text | 你最终确认的完整文案（Hook + 正文 + CTA） |
| TikTok Caption | Long text | TikTok 专用文案 |
| Instagram Caption | Long text | Instagram 专用文案 |
| Facebook Caption | Long text | Facebook 专用文案 |
| YouTube Description | Long text | YouTube 专用文案 |
| Video File 视频 | Attachment | HeyGen 生成的视频文件 |
| Scheduled Publish Time 发布日期 | Date | 计划发布日期（不填 = 下一次每日 Cron 发布） |
| Published At 发布时间 | Date | 实际发布日期（系统自动填写） |
| Publish Links 发布链接 | Long text | 各平台发布结果（JSON） |
| Notes 备注 | Long text | 系统操作日志和用户提示 |

**⚠️ Single select 字段的选项值必须完全匹配：**

`Current Stage 当前状态` 的选项：
- 情报采集中
- 等待选题
- 文案生成中
- 等待文案审核
- 等待视频生成
- 视频生成中
- 等待视频审核
- 待发布
- 发布中
- 已完成
- 失败

---

### 📋 表 3：Video Stats（视频数据表）

> 系统自动写入，每条发布记录在 24h 和 72h 各产生 2 行（Instagram + Facebook）。

| 字段名 | 字段类型 | 说明 |
|--------|----------|------|
| Run ID | Single line text | 对应 Pipeline 表的 Run ID |
| Platform 平台 | Single select | `Instagram` / `Facebook` / `TikTok` / `Youtube` |
| Platform Video ID | Single line text | Blotato 内的 postSubmissionId |
| Collected At | Date | 数据采集日期 |
| Play Count 播放量 | Number | 播放量 |
| Like Count 点赞量 | Number | 点赞数 |
| Comment Count 评论数 | Number | 评论数 |
| Share Count 分享数 | Number | 分享数 |

---

## 4. API 账号清单

| 服务 | 获取地址 | 对应环境变量 |
|------|----------|------------|
| Anthropic Claude | https://console.anthropic.com | `ANTHROPIC_API_KEY` |
| Tavily Search | https://tavily.com | `TAVILY_API_KEY` |
| TikHub | https://tikhub.io | `TIKHUB_API_KEY` |
| HeyGen | https://app.heygen.com/settings?nav=API | `HEYGEN_API_KEY` |
| Blotato | https://app.blotato.com → 右上角 Settings | `BLOTATO_API_KEY` |
| Airtable | https://airtable.com/create/tokens | `AIRTABLE_PAT` |

**⚠️ 关于 Blotato Account ID 怎么找：**

1. 登录 [Blotato 后台](https://app.blotato.com)
2. 左侧菜单 → **Accounts**
3. 找到你连接的 Instagram 账号 → 点进去 → URL 里有数字 ID，或者在账号详情页看 Account ID
4. 同样方式找 Facebook 账号 ID
5. Facebook Page ID：在 Blotato 账号详情页的 Page 信息里找，或者登录 Facebook Business Suite 查看

---

## 5. 逐步安装流程

> **⚡ 当用户输入 "start the agent" 时，Claude Code 按以下步骤逐一引导用户完成配置。**

---

### 👋 第 0 步：自我介绍并说明流程

**直接对用户说（中文）：**

> 你好！我是你的安装助手。我将帮你把「Oral Video Director — 全自动口播视频生产系统」安装到你的账号上。
>
> 整个过程约 30-45 分钟，你只需要按我的提示操作，提供信息给我，我来帮你完成所有配置。
>
> 我们现在开始，先准备好以下账号（没有的可以边安装边注册）：
> - Anthropic（Claude AI）
> - Tavily（搜索情报）
> - TikHub（竞品监控，可选）
> - HeyGen（数字人视频）
> - Blotato（社媒发布）
> - Airtable（数据库）
> - Modal（云端运行）
>
> 准备好了告诉我，我们开始第一步！

---

### 📄 第 1 步：初始化配置文件

用户说准备好后，立刻执行：

```bash
cp .env.template .env
```

然后告诉用户：「好的，配置文件已创建。接下来我们逐一填入每个 API Key，我问一个你答一个。」

---

### 🔑 第 2 步：逐一收集 API Keys

**每收到一个 Key 就立刻用 Edit 工具写入 `.env`，不要攒在一起写。按顺序问：**

**问题 1：**「请提供你的 **Anthropic API Key**。获取地址：https://console.anthropic.com/keys」
→ 写入 `ANTHROPIC_API_KEY=<用户提供的值>`

**问题 2：**「请提供你的 **Tavily API Key**。获取地址：https://tavily.com → Dashboard → API Keys」
→ 写入 `TAVILY_API_KEY=<值>`

**问题 3：**「请提供你的 **TikHub API Key**（用于监控竞品账号，没有可跳过）。获取地址：https://tikhub.io」
→ 写入 `TIKHUB_API_KEY=<值>`（跳过则留空）

**问题 4：**「请提供你的 **HeyGen API Key**。获取地址：https://app.heygen.com/settings?nav=API」
→ 写入 `HEYGEN_API_KEY=<值>`

**问题 5：**「请提供你的 **HeyGen 数字人 Avatar ID**。获取方式：HeyGen 后台 → Avatars → 点击你的数字人 → 复制 ID」
→ 写入 `HEYGEN_AVATAR_ID=<值>`

**问题 6：**「请提供你的 **HeyGen 音色 Voice ID**。获取方式：HeyGen 后台 → Voices → 找到你要用的声音 → 复制 ID」
→ 写入 `HEYGEN_VOICE_ID=<值>`

**问题 7：**「请提供你的 **Blotato API Key**。获取地址：https://app.blotato.com → Settings → API」
→ 写入 `BLOTATO_API_KEY=<值>`

**问题 8：**「请提供你在 Blotato 绑定的 **Instagram 账号 ID**。获取方式：Blotato 后台 → Accounts → 找到 Instagram → 复制 ID（纯数字）」
→ 写入 `BLOTATO_IG_ACCOUNT_ID=<值>`

**问题 9：**「请提供你在 Blotato 绑定的 **Facebook 账号 ID**。获取方式：同上，找 Facebook 账号 → 复制 ID」
→ 写入 `BLOTATO_FB_ACCOUNT_ID=<值>`

**问题 10：**「请提供你的 **Facebook Page ID**。获取方式：Blotato → Accounts → 点击 Facebook 账号详情 → 找 Page ID」
→ 写入 `BLOTATO_FB_PAGE_ID=<值>`

**问题 11：**「请提供你的 **Airtable Personal Access Token**。获取地址：https://airtable.com/create/tokens → 创建 Token，勾选 data.records 和 schema.bases 权限」
→ 写入 `AIRTABLE_PAT=<值>`

**问题 12（可选，强烈建议开启【成本护栏 + 出错告警】）：**
「① 每月预算上限（美元，默认 200，累计到达后自动暂停出视频，避免失控烧钱）？
② HeyGen 每秒单价（默认 0.05，按你的数字人引擎调：Avatar III 孪生 0.0167 / Avatar IV 照片 0.05 等）？
③ 要不要出错/花超时收到通知？给我一个 Slack 或 Discord 的 Incoming Webhook URL（不想要可跳过）」
→ 分别写入 `MONTHLY_BUDGET_USD=<值>`、`HEYGEN_COST_PER_SEC=<值>`、`ALERT_WEBHOOK_URL=<值>`（跳过则用默认/留空）

---

### 🗃 第 3 步：配置 Airtable 数据库

**直接对用户说：**

> 现在需要配置 Airtable 数据库。我已经为你准备好了完整模板，你只需要点击链接复制即可，不需要手动建表。
>
> 👉 **请点击此链接复制 Airtable 模板：**
> https://airtable.com/appUoqCja58gtfjBE/shreXZQC6NBQjC6x8
>
> 步骤：
> 1. 点击链接，登录你的 Airtable 账号
> 2. 点击右上角 **「Copy base」** 按钮
> 3. 选择你的 Workspace，确认复制
>
> 复制完成后告诉我，我来帮你获取 Base ID 和 Table ID。

**用户说复制完成后，引导用户获取 ID：**

> 很好！现在请打开你复制的 Base，然后：
> 1. 看浏览器地址栏，URL 里 `app` 开头的字符串就是 **Base ID**（如 `appXXXXXXXXXX`），请复制给我
> 2. 点击 **Brand Config** 表名旁边的 `...` → 选择 **「Get table id」**，复制 `tbl` 开头的 ID 给我
> 3. 同样方式获取 **Content Pipeline** 表的 Table ID
> 4. 同样方式获取 **Video Stats** 表的 Table ID

**用户提供后，立刻写入 .env：**
- `AIRTABLE_BASE_ID=<值>`
- `AIRTABLE_BRAND_CONFIG_TABLE=<值>`
- `AIRTABLE_PIPELINE_TABLE=<值>`
- `AIRTABLE_STATS_TABLE=<值>`

---

### 📝 第 4 步：填写 Brand Config 第一行数据

打开 Airtable，在 **Brand Config** 表里填写第一行：

- **Account Name 账号名称**：你的账号名
- **Industry 行业**：你的行业（英文，如 "AI tools automation"）
- **Positioning 账号定位**：1-3 句描述账号定位
- **Target Audience 目标受众**：目标受众画像
- **Key Selling Points 核心卖点**：产品/服务核心价值
- **TikTok Competitor Accounts TT对标账号**：竞品账号用户名，英文逗号分隔
- **HeyGen Avatar ID 数字人ID**：你的 HeyGen 数字人 ID
- **Heygen Voice ID 音色ID**：你的 HeyGen 音色 ID

（AI Profile Summary 和 AI Experience Notes 留空，首次运行时系统自动生成）

---

### ⚙️ 第 5 步：安装依赖 + Modal

```bash
pip install -r requirements.txt
modal setup
```

第一条装齐全部本地依赖（modal / fastapi / anthropic / requests 等，部署和注册 webhook 都要用）。
`modal setup` 会打开浏览器让你登录 Modal 账号（没有就注册一个，免费额度够用）。

---

### 🔐 第 6 步：创建 Modal Secret

把 `.env` 里的全部环境变量上传到 Modal Secret：

```bash
modal secret create oral-video-director-secrets --from-dotenv .env --force
```

> ⚠️ `--force` 是**整份覆盖**：Secret 里只会剩下这次传进去的变量。所以永远用 `--from-dotenv .env` 同步完整的 `.env`，不要只传单个 key。
> 💡 之后改了 `.env` 里任何一项（换 API Key、调预算），重新跑一次上面这条命令即可。

---

### 🚀 第 7 步：部署到 Modal

```bash
modal deploy modal_app.py
```

部署完成后会输出 Webhook URL，格式类似：
```
https://xxxxxxxxxx--oral-video-director-webhook-handler.modal.run
```

**把这个 URL 写入 `.env` 的 `MODAL_WEBHOOK_URL=`，再把完整 `.env` 重新同步到 Modal Secret：**

```bash
modal secret create oral-video-director-secrets --from-dotenv .env --force
```

---

### 🔗 第 8 步：注册 Airtable Webhook

```bash
python webhook_register.py setup
```

这个命令会：
1. 在 Airtable 注册一个 Webhook，监听 Content Pipeline 表的变化
2. 把 Webhook ID 和 Secret 写入 `.env`，并把完整 `.env` 同步到 Modal Secret
3. 输出注册成功信息和 Webhook 到期时间（7 天，系统会自动续期）

---

### ✅ 第 9 步：验证安装

```bash
python webhook_register.py list
```

确认输出里显示有 1 个 Webhook 已注册。

---

### 🎉 第 10 步：安装完成！

告诉用户：

> ✅ **系统已成功上线！**
>
> 系统将在每天 **北京时间 01:00（EST 12:00）** 自动执行：
> - 情报采集 + 10 条选题生成
> - 定时发布待发布视频
> - 24h / 72h 数据回收
>
> 每周一同一时间还会执行周复盘分析。
>
> **你现在需要做的：**
> 1. 等待明天的 Cron 自动触发，或者手动在 Airtable Content Pipeline 表新建一行触发情报采集
> 2. 收到系统通知（查看 Notes 字段）后，在 Airtable 完成人工审核步骤

---

## 6. 日常使用流程

### 📅 每日流程

| 时间 | 谁做 | 做什么 |
|------|------|--------|
| EST 12:00 | 🤖 系统 | 自动情报采集 + 生成 10 条选题 |
| 任意时间 | 👤 你 | 在 Airtable 查看选题，选一条填入 Final Topic，改 Topic Status = done |
| 5 分钟后 | 🤖 系统 | 自动生成文案（Hook + 正文 + CTA） |
| 任意时间 | 👤 你 | 审核文案，拼接 Final Script，改 Review Status = approved |
| 5 分钟后 | 🤖 系统 | 生成四平台 Caption |
| 任意时间 | 👤 你 | 确认 Caption，改 Heygen Status = ready |
| 1-3 分钟后 | 🤖 系统 | 提交 HeyGen，等待视频生成（约 1-3 分钟） |
| 任意时间 | 👤 你 | 预览视频，满意则改 Heygen Status = approved |
| 次日 EST 12:00 | 🤖 系统 | 自动发布到 Instagram + Facebook |

### 📊 查看数据

在 Airtable **Video Stats** 表查看每条视频的播放、点赞、评论、分享数据。

### 📝 查看周复盘

每周一 EST 12:00 后，在 Brand Config 表的 `AI Experience Notes 经验结论` 字段查看最新周复盘分析。

---

## 7. 常见问题排查

### ❓ 情报采集失败
- 检查 `TAVILY_API_KEY` 和 `TIKHUB_API_KEY` 是否正确
- 检查 Brand Config 表 `TikTok Competitor Accounts` 字段格式是否正确（英文逗号分隔，不要加 @）

### ❓ 文案生成失败
- 检查 `ANTHROPIC_API_KEY` 是否有余额
- 检查 `Final Topic 最终选题` 字段是否已填写

### ❓ HeyGen 视频一直 processing
- HeyGen 有时服务器繁忙，系统会每 5 分钟轮询一次，最多等 2 小时
- 超时后查看 Notes 备注，手动将 `Heygen Status` 改回 `ready` 重试

### ❓ 发布失败
- 检查 Blotato 账号是否还连接着 Instagram / Facebook
- 检查 `BLOTATO_IG_ACCOUNT_ID` / `BLOTATO_FB_ACCOUNT_ID` / `BLOTATO_FB_PAGE_ID` 是否正确
- 查看 Notes 备注字段里的错误信息

### ❓ Webhook 收不到通知
- 运行 `python webhook_register.py list` 检查 Webhook 是否注册
- 检查 Modal Secret 里的 `MODAL_WEBHOOK_URL` 是否是最新的部署 URL
- Webhook 每 7 天到期，系统每天自动续期，如果续期失败重新运行 `python webhook_register.py setup`

### ❓ 需要重新部署
```bash
modal deploy modal_app.py
```
注意：重新部署后 Webhook URL 不会变，无需重新注册 Webhook。

### ❓ 更新 API Key
先改 `.env` 里对应的值，再整份同步（不要只传单个 key，`--force` 会把其余 key 清掉）：
```bash
modal secret create oral-video-director-secrets --from-dotenv .env --force
```

---

*文档最后更新：2026-10-13*
