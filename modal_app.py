"""
Brand Clone Engine — Modal 主程序

触发器：
  - Cron 每天 17:00 UTC (12:00 EST)：定时发布检查 + 新建每日流水线
  - Cron 每周一 17:00 UTC：周复盘分析
  - Web Endpoint POST /webhook：接收 Airtable 字段变化事件
  - 延迟函数：HeyGen 视频轮询 + 24h/72h 数据回收

状态机（Current Stage）：
  情报采集中 → 等待选题 → 文案生成中 → 等待文案审核
  → 等待视频生成 → 视频生成中 → 等待视频审核 → 发布中 → 已完成

部署：
  modal deploy modal_app.py
"""

import hashlib
import json
import os
import sys
from datetime import datetime, timezone, timedelta

import modal
from fastapi import Request
from fastapi.responses import JSONResponse

# ── App & Image ─────────────────────────────────────────────
app = modal.App("oral-video-director")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "anthropic>=1.13.0",
        "requests>=2.32.0",
        "python-dotenv>=1.0.0",
        "fastapi[standard]>=0.115.0",
    )
    .add_local_dir("core", remote_path="/app/core")
)

SECRET = modal.Secret.from_name("oral-video-director-secrets")

# 成本累计（按月持久化，跨调用共享）
BUDGET_DICT = modal.Dict.from_name("oral-video-director-budget", create_if_missing=True)

# Webhook 游标：记住已处理到哪条 payload，下次从这里接着拉
STATE_DICT = modal.Dict.from_name("oral-video-director-state", create_if_missing=True)
# 单次通知最多翻页数（Airtable 每页 50 条）
_MAX_PAYLOAD_PAGES = 40


def _digest(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:12]


def _claim(record_id: str, step: str) -> bool:
    """一次性锁。Airtable 通知至少送达一次、webhook 容器又可能并发，
    同一 record 的同一步骤会被 spawn 两次；put(skip_if_exists) 是原子的，只有第一次抢到。
    step 里带内容 hash，改了选题/文案再触发就是新 key，合法重跑不受影响。"""
    return STATE_DICT.put(
        f"claim:{record_id}:{step}",
        datetime.now(timezone.utc).isoformat(),
        skip_if_exists=True,
    )


def _release(record_id: str, step: str) -> None:
    """处理失败时放开锁，否则同样内容的人工重试会被当成重复触发。"""
    STATE_DICT.pop(f"claim:{record_id}:{step}", None)


def _release_quiet(record_id: str, step: str) -> None:
    """终态释放锁；Dict 抖动不能挡住后面的 set_failed / 状态更新。"""
    if not step:
        return
    try:
        _release(record_id, step)
    except Exception as e:
        print(f"[claim] 释放 {record_id}:{step} 失败（已忽略）：{e}")


def _notify(message: str, level: str = "info") -> None:
    """推送告警；失败绝不抛出，不影响主流程。"""
    try:
        from core import notifier
        notifier.notify(message, level=level)
    except Exception as e:
        print(f"[notify] {message}（推送失败已忽略：{e}）")


def _budget_limit() -> float:
    try:
        return float(os.getenv("MONTHLY_BUDGET_USD", "0") or 0)
    except ValueError:
        return 0.0


def _budget_spent() -> float:
    return float(BUDGET_DICT.get(datetime.now(timezone.utc).strftime("%Y-%m"), 0.0))


def _budget_add(usd: float):
    """累加本月成本，返回 (累加前, 累加后)。"""
    key = datetime.now(timezone.utc).strftime("%Y-%m")
    before = float(BUDGET_DICT.get(key, 0.0))
    after = before + usd
    BUDGET_DICT[key] = after
    return before, after


# ── 容器内导入（运行时执行）─────────────────────────────────
def _load():
    sys.path.insert(0, "/app")
    from core import airtable_client as at
    from core import intelligence, topic_generator, copywriter, platform_adapter, video_generator, publisher
    return at, intelligence, topic_generator, copywriter, platform_adapter, video_generator, publisher


# ============================================================
# Web Endpoint：接收 Airtable Webhook
# ============================================================

@app.function(image=image, secrets=[SECRET], timeout=300)
@modal.fastapi_endpoint(method="POST", label="oral-video-director-webhook")
async def webhook_handler(request: Request):  # noqa: F811
    """
    Airtable 发来的 Webhook 通知（body 只含 webhook id，不含变更内容）。
    从上次保存的游标接着拉 payload，根据 record 状态分发到对应处理函数。
    立即返回 200，实际处理异步执行。
    """
    body = await request.json()
    print(f"[webhook] 收到通知：{json.dumps(body)}")

    # Airtable body 格式: {"webhook": {"id": "..."}, "base": {...}, "timestamp": "..."}
    webhook_id = (body.get("webhook") or {}).get("id") or os.getenv("AIRTABLE_WEBHOOK_ID", "").strip("'")

    if not webhook_id:
        return JSONResponse({"status": "ok", "note": "no webhookId"})

    at, *_ = _load()

    # Airtable 不记游标：不带 cursor 每次都从最旧的 payload 开始、单次最多 50 条。
    # 所以游标必须自己存，并翻页拉到最新，否则积压超过 50 条后就再也看不到新变更。
    cursor_key = f"webhook_cursor:{webhook_id}"
    cursor = STATE_DICT.get(cursor_key)
    created, changed = {}, {}
    try:
        for _ in range(_MAX_PAYLOAD_PAGES):
            data = at.fetch_webhook_payloads(webhook_id, cursor)
            for payload in data.get("payloads", []):
                for table_data in payload.get("changedTablesById", {}).values():
                    created.update(dict.fromkeys(table_data.get("createdRecordsById", {})))
                    changed.update(dict.fromkeys(table_data.get("changedRecordsById", {})))
            # 响应偶尔不带 cursor；存了 None 下次会从最旧的 payload 整个重放
            if data.get("cursor") is not None:
                cursor = data["cursor"]
            if not data.get("mightHaveMore"):
                break
    except Exception as e:
        # 游标不前移，下次通知会从同一位置重拉
        print(f"[webhook] 拉取 payload 失败：{e}")
        return JSONResponse({"status": "ok"})

    for record_id in created:
        print(f"[webhook] 新建记录 {record_id}")
        handle_new_record.spawn(record_id=record_id)
    # 分发器只看 record 当前状态，同一条 record 多次变更只需分发一次
    for record_id in changed:
        print(f"[webhook] 记录变更 {record_id}，检查状态")
        dispatch_change.spawn(record_id=record_id)

    if cursor is not None:
        STATE_DICT[cursor_key] = cursor
    return JSONResponse({"status": "ok"})


# ============================================================
# 状态分发器：读取 record 当前状态决定下一步
# ============================================================

@app.function(image=image, secrets=[SECRET], timeout=60)
def dispatch_change(record_id: str):
    at, *_ = _load()
    rec = at.get_pipeline_record(record_id)
    f = rec["fields"]

    stage = f.get("Current Stage 当前状态", "")
    topic_status = f.get("Topic Status", "")
    review_status = f.get("Review Status", "")
    heygen_status = f.get("Heygen Status", "")

    print(f"[dispatch] {record_id} | stage={stage} | topic={topic_status} | review={review_status} | heygen={heygen_status}")

    if stage == "等待选题" and topic_status == "done":
        handle_topic_done.spawn(record_id=record_id)

    elif stage == "等待文案审核" and review_status == "approved":
        handle_script_approved.spawn(record_id=record_id)

    elif stage == "等待视频生成" and heygen_status == "ready":
        handle_heygen_ready.spawn(record_id=record_id)

    elif stage == "等待视频审核" and heygen_status == "ready":
        # 视频不满意 → 退回「等待视频生成」。这次写入会再触发一次 webhook，
        # 由上面的分支提交 HeyGen；这里不直接 spawn，否则两条路径会各提交一次、收两次费。
        # 上一次的锁在 poll_heygen 出片时已释放，文案没改也能重渲染
        at.set_stage(record_id, "等待视频生成", notes="🔁 正在重新提交 HeyGen 生成视频…")

    elif stage == "等待视频审核" and heygen_status == "approved":
        # 视频审核通过 → 进入待发布队列，等 Cron 发布
        at.set_stage(record_id, "待发布",
            notes=(
                "✅ 视频已审核通过，等待每日定时任务（17:00 UTC）发布。\n\n"
                "Scheduled Publish Time 留空 = 下一次定时任务发布；填了日期 = 到那天发布。"
            ))

    else:
        print("[dispatch] 无匹配动作，跳过")


# ============================================================
# 新建记录 → 情报采集 + 选题生成
# ============================================================

@app.function(image=image, secrets=[SECRET], timeout=600)
def handle_new_record(record_id: str):
    at, intel_mod, topic_mod, *_ = _load()

    rec = at.get_pipeline_record(record_id)
    f = rec["fields"]

    # 若已有 Run ID 说明是 Cron 建的，跳过
    if f.get("Run ID"):
        print(f"[new_record] {record_id} 已有 Run ID，跳过（由 Cron 处理）")
        return

    step = "new_record"
    if not _claim(record_id, step):
        print(f"[new_record] {record_id} 重复触发，已有一次在处理，跳过")
        return

    run_id = at.generate_run_id()
    at.update_pipeline(record_id, {"Run ID": run_id})

    brand = at.get_brand_config()
    bf = brand["fields"]

    try:
        at.set_stage(record_id, "情报采集中")
        intel = intel_mod.gather_intelligence(
            industry=bf.get("Industry 行业", "AI tools automation"),
            competitor_accounts=bf.get("TikTok Competitor Accounts TT对标账号", ""),
        )
        at.update_pipeline(record_id, {
            "Intelligence Data 情报采集结果": json.dumps(intel, ensure_ascii=False),
        })

        topics = topic_mod.generate_topics(
            intelligence_data=intel,
            ai_profile_summary=bf.get("AI Profile Summary 账号定位", ""),
            ai_experience_notes=bf.get("AI Experience Notes 经验结论", ""),
            positioning=bf.get("Positioning 账号定位", ""),
            target_audience=bf.get("Target Audience 目标受众", ""),
            key_selling_points=bf.get("Key Selling Points 核心卖点", ""),
        )

        at.update_pipeline(record_id, {
            "Topics List 选题列表": json.dumps(topics, ensure_ascii=False, indent=2),
            "Topic Status": "pending",
        })
        at.set_stage(
            record_id, "等待选题",
            notes=(
                "✅ 情报采集完成，已生成 10 条选题。\n\n"
                "👉 下一步：\n"
                "1. 查看 Topics List 选题列表，选择你想做的选题\n"
                "2. 将选中的选题标题和角度填入 Final Topic 字段\n"
                "3. 将 Topic Status 改为 done\n\n"
                "系统检测到 done 后自动生成文案。"
            ),
        )
        print(f"✅ {run_id} 情报采集 + 选题生成完成")

    except Exception as e:
        _release(record_id, step)
        at.set_failed(record_id, f"情报采集/选题生成失败：{e}")
        raise


# ============================================================
# Topic Status → done → 生成文案
# ============================================================

@app.function(image=image, secrets=[SECRET], timeout=600)
def handle_topic_done(record_id: str):
    at, _, _, copy_mod, *_ = _load()

    rec = at.get_pipeline_record(record_id)
    f = rec["fields"]
    brand = at.get_brand_config()["fields"]
    final_topic = f.get("Final Topic 最终选题", "")

    if not final_topic:
        at.set_failed(record_id, "Final Topic 为空，请先填写选题标题再将 Topic Status 改为 done")
        return

    step = f"topic:{_digest(final_topic)}"
    if not _claim(record_id, step):
        print(f"[handle_topic_done] {record_id} 重复触发，同一 Final Topic 已在生成文案，跳过")
        return

    try:
        at.set_stage(record_id, "文案生成中")
        script = copy_mod.generate_script(
            final_topic=final_topic,
            ai_profile_summary=brand.get("AI Profile Summary 账号定位", ""),
            ai_experience_notes=brand.get("AI Experience Notes 经验结论", ""),
            positioning=brand.get("Positioning 账号定位", ""),
            target_audience=brand.get("Target Audience 目标受众", ""),
            key_selling_points=brand.get("Key Selling Points 核心卖点", ""),
        )
        review = copy_mod.review_script(script["hooks"], script["body"], script["ctas"])
        risk_note = (
            f"\n\n⚠️ 风险审核：{review['risk_level']} — {review['summary']}"
            if review.get("issues") else ""
        )
        at.update_pipeline(record_id, {
            "Script Hooks 钩子": json.dumps(script["hooks"], ensure_ascii=False),
            "Script Body 口播正文": script["body"],
            "Script CTAs": json.dumps(script["ctas"], ensure_ascii=False),
            "Review Status": "pending",
        })
        at.set_stage(
            record_id, "等待文案审核",
            notes=(
                f"✅ 文案已生成。{risk_note}\n\n"
                "👉 下一步：\n"
                "1. 查看 Script Hooks（3个钩子备选）\n"
                "2. 查看 Script Body（口播正文）\n"
                "3. 查看 Script CTAs（2个收尾备选）\n"
                "4. 选好钩子和CTA，拼接后填入 Final Script\n"
                "5. 将 Review Status 改为 approved"
            ),
        )
    except Exception as e:
        _release(record_id, step)
        at.set_failed(record_id, f"文案生成失败：{e}")
        raise


# ============================================================
# 文案 Review Status → approved → 生成四平台文案
# ============================================================

@app.function(image=image, secrets=[SECRET], timeout=180)
def handle_script_approved(record_id: str):
    at, _, _, _, adapter_mod, *_ = _load()

    rec = at.get_pipeline_record(record_id)
    f = rec["fields"]
    final_script = f.get("Final Script 完整口播文案", "")
    final_topic = f.get("Final Topic 最终选题", "")

    if not final_script:
        at.set_failed(record_id, "Final Script 为空，请先填写最终文案再将 Review Status 改为 approved")
        return

    step = f"script:{_digest(final_script)}"
    if not _claim(record_id, step):
        print(f"[handle_script_approved] {record_id} 重复触发，同一 Final Script 已在生成平台文案，跳过")
        return

    try:
        captions = adapter_mod.adapt_for_platforms(final_script, final_topic)
        at.update_pipeline(record_id, {
            "TikTok Caption": captions["TikTok Caption"],
            "Instagram Caption": captions["Instagram Caption"],
            "Facebook Caption": captions["Facebook Caption"],
            "YouTube Description": captions["YouTube Description"],
            "Review Status": "pending",
        })
        at.set_stage(
            record_id, "等待视频生成",
            notes=(
                "✅ 四平台文案已生成。\n\n"
                "👉 下一步：\n"
                "1. 确认各平台 Caption 字段内容\n"
                "2. 将 Heygen Status 改为 ready\n\n"
                "改为 ready 后系统自动提交视频生成任务。"
            ),
        )
    except Exception as e:
        _release(record_id, step)
        at.set_failed(record_id, f"四平台文案生成失败：{e}")
        raise


# ============================================================
# HeyGen Status → ready → 提交视频生成
# ============================================================

@app.function(image=image, secrets=[SECRET], timeout=180)
def handle_heygen_ready(record_id: str):
    at, _, _, _, _, vgen_mod, _ = _load()

    rec = at.get_pipeline_record(record_id)
    f = rec["fields"]

    # 防止重复提交：只有 stage 还是「等待视频生成」才继续
    if f.get("Current Stage 当前状态") != "等待视频生成":
        print(f"[handle_heygen_ready] {record_id} stage 已变更，跳过")
        return

    brand = at.get_brand_config()["fields"]
    final_script = f.get("Final Script 完整口播文案", "")
    avatar_id = brand.get("HeyGen Avatar ID 数字人ID") or os.getenv("HEYGEN_AVATAR_ID", "")
    voice_id = brand.get("Heygen Voice ID 音色ID") or os.getenv("HEYGEN_VOICE_ID", "")

    if not final_script:
        at.set_failed(record_id, "Final Script 为空，无法生成视频")
        return

    # 成本护栏：本月预算用尽则不再提交 HeyGen（视频渲染是主要成本）
    limit = _budget_limit()
    if limit > 0 and _budget_spent() >= limit:
        at.set_failed(record_id, (
            f"⚠️ 本月预算已达上限（已花 ${_budget_spent():.2f} / 上限 ${limit:.2f}），"
            f"为控制成本已暂停出视频。下月自动重置，或调高 MONTHLY_BUDGET_USD 后新建记录重试。"
        ))
        return

    # stage 守卫挡不住两个容器同时读到「等待视频生成」的窗口，再加原子锁
    step = f"heygen:{_digest(final_script)}"
    if not _claim(record_id, step):
        print(f"[handle_heygen_ready] {record_id} 重复触发，同一 Final Script 已提交 HeyGen，跳过")
        return

    try:
        at.set_stage(record_id, "视频生成中")
        video_id = vgen_mod.submit_video(final_script, avatar_id, voice_id)
        at.update_pipeline(record_id, {
            "Notes 备注": f"🎬 HeyGen 任务已提交（video_id={video_id}），正在生成中，请耐心等待 1-3 分钟..."
        })
        poll_heygen.spawn(record_id=record_id, video_id=video_id, attempt=1, step=step)
    except Exception as e:
        _release(record_id, step)
        at.set_failed(record_id, f"HeyGen 任务提交失败：{e}")
        raise


# ============================================================
# 延迟任务：HeyGen 轮询（每 5 分钟，最多 24 次 = 2 小时）
# ============================================================

@app.function(image=image, secrets=[SECRET], timeout=360)
def poll_heygen(record_id: str, video_id: str, attempt: int, step: str = ""):
    import time
    at, _, _, _, _, vgen_mod, _ = _load()

    # step 是提交时抢到的锁 key；三个终态都释放，用户改回「等待视频生成」即可用同一文案重试
    if attempt > 24:
        _release_quiet(record_id, step)
        at.set_failed(record_id, "HeyGen 视频生成超时（超过2小时），请检查 HeyGen 后台或重新提交")
        return

    result = vgen_mod.check_video_status(video_id)
    status = result["status"]
    print(f"[第{attempt}次] HeyGen {video_id} 状态：{status}")

    if status == "completed":
        at.upload_video_attachment(record_id, result["video_url"], f"{video_id}.mp4")
        # 记录 HeyGen 渲染成本，并在跨过 50% / 80% / 100% 预算时告警
        try:
            dur = float(result.get("duration", 0) or 0)
            rate = float(os.getenv("HEYGEN_COST_PER_SEC", "0.05") or 0.05)
            if dur > 0 and rate > 0:
                before, after = _budget_add(dur * rate)
                limit = _budget_limit()
                if limit > 0:
                    for th, lvl in [(1.0, "error"), (0.8, "warn"), (0.5, "money")]:
                        if before < limit * th <= after:
                            _notify(f"本月成本 ${after:.2f} 已达预算 ${limit:.2f} 的 {int(th*100)}%", level=lvl)
                            break
        except Exception as e:
            print(f"[budget] 记录成本失败（已忽略）：{e}")
        at.update_pipeline(record_id, {
            "Heygen Status": "done",
            "Review Status": "pending",
        })
        at.set_stage(
            record_id, "等待视频审核",
            notes=(
                "✅ 视频已生成，已上传到 Video File 字段。\n\n"
                "👉 下一步：\n"
                "1. 点击 Video File 字段预览视频\n"
                "2. 满意 → 将 Heygen Status 改为 approved（Scheduled Publish Time 留空 = 下一次每日定时任务发布，填了日期 = 到那天发布）\n"
                "3. 不满意 → 将 Heygen Status 改回 ready 重新生成（可先修改 Final Script）"
            ),
        )
        # 先写完「等待视频审核」再放锁：此时任何迟到的「等待视频生成+ready」处理函数都会被 stage 守卫挡住
        _release_quiet(record_id, step)
    elif status == "failed":
        _release_quiet(record_id, step)
        at.set_failed(record_id, f"HeyGen 生成失败：{result.get('error', {})}")
    else:
        time.sleep(300)
        poll_heygen.spawn(record_id=record_id, video_id=video_id, attempt=attempt + 1, step=step)




# ============================================================
# Webhook 续期（内部调用）
# ============================================================

def _refresh_webhook():
    """检查 Airtable Webhook 有效期，剩余不足 2 天时用官方 refresh 接口续期。
    refresh 不改变 webhook ID / secret，因此无需回写 Modal Secret，
    也就不需要在容器内调用 modal CLI（容器内没有 modal 登录凭证，原做法会失败）。"""
    import requests as req
    pat = os.getenv("AIRTABLE_PAT")
    base_id = os.getenv("AIRTABLE_BASE_ID")
    headers = {"Authorization": f"Bearer {pat}", "Content-Type": "application/json"}
    webhooks_url = f"https://api.airtable.com/v0/bases/{base_id}/webhooks"

    resp = req.get(webhooks_url, headers=headers, timeout=15)
    resp.raise_for_status()
    webhooks = resp.json().get("webhooks", [])

    now = datetime.now(timezone.utc)
    threshold = now + timedelta(days=2)

    for wh in webhooks:
        expiry_str = wh.get("expirationTime", "")
        if not expiry_str:
            continue
        expiry = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
        remaining = (expiry - now).days
        if expiry <= threshold:
            print(f"🔄 Webhook 剩余 {remaining} 天，调用 refresh 接口续期...")
            r = req.post(f"{webhooks_url}/{wh['id']}/refresh", headers=headers, timeout=15)
            if r.ok:
                print(f"✅ Webhook 续期成功，新到期：{r.json().get('expirationTime')}")
            else:
                print(f"⚠️  Webhook 续期失败：{r.status_code} {r.text[:200]}")
        else:
            print(f"✅ Webhook 有效，剩余 {remaining} 天，无需续期")


@app.function(image=image, secrets=[SECRET], timeout=60)
def refresh_webhook():
    _refresh_webhook()


# ============================================================
# 每日任务 ②：定时发布（待发布 且 发布日期留空或已到）
# ============================================================

@app.function(image=image, secrets=[SECRET], timeout=600)
def publish_pending():
    at, _, _, _, _, _, publisher_mod = _load()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    to_publish = at.list_pending_publish()
    print(f"📅 今日待发布：{len(to_publish)} 条")
    for rec in to_publish:
        r_id = rec["id"]
        f = rec["fields"]
        r_run_id = f.get("Run ID", r_id)
        try:
            at.set_stage(r_id, "发布中")
            attachments = f.get("Video File 视频", [])
            video_url = attachments[0].get("url", "") if attachments else ""
            result = publisher_mod.publish_all_platforms(
                video_url=video_url,
                instagram_caption=f.get("Instagram Caption", ""),
                facebook_caption=f.get("Facebook Caption", ""),
            )
            at.update_pipeline(r_id, {
                "Publish Links 发布链接": json.dumps(result, ensure_ascii=False),
                "Published At 发布时间": today,
            })
            at.set_stage(r_id, "已完成", notes="✅ 已发布到 Instagram 和 Facebook。")
            print(f"✅ [{r_run_id}] 发布完成")
        except Exception as e:
            at.set_failed(r_id, f"发布失败：{e}")
            print(f"❌ [{r_run_id}] 发布失败：{e}")


# ============================================================
# 每日任务 ③：数据回收（昨天发布的 24h 数据 + 3天前发布的 72h 数据）
# ============================================================

@app.function(image=image, secrets=[SECRET], timeout=600)
def collect_stats():
    at, _, _, _, _, _, publisher_mod = _load()
    for days_ago, label in [(1, "24h"), (3, "72h")]:
        records = at.list_for_stats(days_ago)
        print(f"📊 {label} 数据回收：{len(records)} 条")
        for rec in records:
            f = rec["fields"]
            r_run_id = f.get("Run ID", rec["id"])
            links_raw = f.get("Publish Links 发布链接", "")
            if not links_raw:
                continue
            try:
                publish_links = json.loads(links_raw)
            except Exception:
                continue
            links_updated = False
            for platform, info in publish_links.items():
                if not info.get("success"):
                    continue
                sub_id = info.get("post_id", "")
                if not sub_id:
                    continue
                # 单条/单平台出错只记一行日志，不拖垮当天其余数据回收
                try:
                    stats = publisher_mod.fetch_platform_stats(platform, sub_id)
                    at.create_stat_record(
                        run_id=r_run_id,
                        platform=platform,
                        platform_video_id=sub_id,
                        play=stats["play"],
                        like=stats["like"],
                        comment=stats["comment"],
                        share=stats["share"],
                    )
                    print(f"✅ [{r_run_id}] {platform} {label} 数据写入")
                except Exception as e:
                    print(f"❌ [{r_run_id}] {platform} {label} 数据回收失败：{e}")
                    continue
                # 回填 post_url（发布时 Blotato 异步还没有，现在补上）
                if not info.get("post_url") and stats.get("post_url"):
                    publish_links[platform]["post_url"] = stats["post_url"]
                    links_updated = True
            if links_updated:
                at.update_pipeline(rec["id"], {
                    "Publish Links 发布链接": json.dumps(publish_links, ensure_ascii=False)
                })
                print(f"🔗 [{r_run_id}] post_url 已回填")


# ============================================================
# Cron：每天 17:00 UTC（EST 12:00 / 北京时间次日 01:00）
# ① 情报采集 + 选题生成 在本函数内跑；② 定时发布 ③ 数据回收 ④ Webhook 续期
# 各自 spawn 成独立函数，Blotato 上传慢或重试不会拖死其他任务
# ============================================================

@app.function(image=image, secrets=[SECRET], schedule=modal.Cron("0 17 * * *"), timeout=600)
def daily_pipeline():
    at, intel_mod, topic_mod, *_ = _load()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"[{today}] 每日流水线启动（EST 12:00）")

    # 先派发，① 里 Brand Config / 生成定位摘要若抛错也不影响发布和回收
    publish_pending.spawn()
    collect_stats.spawn()
    refresh_webhook.spawn()

    # ── ① 情报采集 + 选题生成 ──────────────────────────────
    brand = at.get_brand_config()
    bf = brand["fields"]

    # 首次运行：AI Profile Summary 为空时自动生成
    if not bf.get("AI Profile Summary 账号定位", "").strip():
        print("📝 AI Profile Summary 为空，开始自动生成...")
        from core import llm
        profile_summary = llm.text(
            system="你是账号定位提炼助手，输出简洁、结构化的中文描述。",
            user=f"""根据以下品牌信息，生成一段结构化的账号定位描述（200字以内，中文），
供AI每次生成选题和文案时作为核心参考。要清晰体现：账号是做什么的、目标受众是谁、内容风格和核心价值主张。

账号名称：{bf.get('Account Name 账号名称', '')}
行业：{bf.get('Industry 行业', '')}
账号定位：{bf.get('Positioning 账号定位', '')}
目标受众：{bf.get('Target Audience 目标受众', '')}
核心卖点：{bf.get('Key Selling Points 核心卖点', '')}

直接输出描述文本，不要标题和前缀。""",
            model=llm.MODEL_CHEAP,
        )
        at.update_brand_config(brand["id"], {"AI Profile Summary 账号定位": profile_summary})
        bf["AI Profile Summary 账号定位"] = profile_summary
        print("✅ AI Profile Summary 已生成并写入 Brand Config")

    run_id = at.generate_run_id()
    rec_id = at.create_pipeline_run(run_id)["id"]

    try:
        at.set_stage(rec_id, "情报采集中")
        intel = intel_mod.gather_intelligence(
            industry=bf.get("Industry 行业", "AI tools automation"),
            competitor_accounts=bf.get("TikTok Competitor Accounts TT对标账号", ""),
        )
        at.update_pipeline(rec_id, {
            "Intelligence Data 情报采集结果": json.dumps(intel, ensure_ascii=False),
        })
        topics = topic_mod.generate_topics(
            intelligence_data=intel,
            ai_profile_summary=bf.get("AI Profile Summary 账号定位", ""),
            ai_experience_notes=bf.get("AI Experience Notes 经验结论", ""),
            positioning=bf.get("Positioning 账号定位", ""),
            target_audience=bf.get("Target Audience 目标受众", ""),
            key_selling_points=bf.get("Key Selling Points 核心卖点", ""),
        )
        at.update_pipeline(rec_id, {
            "Topics List 选题列表": json.dumps(topics, ensure_ascii=False, indent=2),
            "Topic Status": "pending",
        })
        at.set_stage(rec_id, "等待选题", notes=(
            "✅ 今日情报采集完成，已生成 10 条选题。\n\n"
            "👉 下一步：\n"
            "1. 查看 Topics List，选择选题\n"
            "2. 填入 Final Topic 字段\n"
            "3. 将 Topic Status 改为 done"
        ))
        print(f"✅ [{run_id}] 情报采集 + 选题生成完成")
    except Exception as e:
        at.set_failed(rec_id, f"情报采集失败：{e}")
        print(f"❌ 情报采集失败：{e}")


# ============================================================
# Cron 2：每周复盘（每周一 12:00 EST）
# ============================================================

@app.function(image=image, secrets=[SECRET], schedule=modal.Cron("0 17 * * 1"), timeout=300)
def weekly_review():
    at, *_ = _load()
    from core import llm
    print(f"[{datetime.now(timezone.utc)}] 周复盘启动")

    stats = at.list_stats()
    pipeline = at.list_stats_for_review()
    if not stats:
        print("⚠️  无数据，跳过")
        return

    topics_summary = [
        {"run_id": r["fields"].get("Run ID"), "topic": r["fields"].get("Final Topic 最终选题")}
        for r in pipeline[:20]
    ]

    # 学习信号:AI 原稿 vs 人工终稿的差异,是最强的偏好信号,喂进复盘
    edit_pairs = []
    for r in pipeline[:5]:
        f = r["fields"]
        ai_draft = (f.get("Script Body 口播正文") or "").strip()
        human_final = (f.get("Final Script 完整口播文案") or "").strip()
        if ai_draft and human_final:
            edit_pairs.append({
                "topic": f.get("Final Topic 最终选题", ""),
                "ai_draft": ai_draft[:400],
                "human_final": human_final[:400],
            })

    review_text = llm.text(
        system="你是短视频运营数据分析师，结论务实、可执行，不写空话。",
        user=f"""分析以下过去一周的视频数据与人工改稿记录，用中文输出300字以内的结论：

## 数据
{json.dumps(stats[:50], ensure_ascii=False)}

## 选题
{json.dumps(topics_summary, ensure_ascii=False)}

## 人工改稿对照（AI原稿 vs 人工终稿，反映用户偏好）
{json.dumps(edit_pairs, ensure_ascii=False) if edit_pairs else "（本周无改稿记录）"}

格式：
【本周爆款规律】...
【下周建议选题方向】...
【人工改稿偏好】用户倾向怎么改AI的稿子，下次起草直接照做...
【需要避免】...""",
        model=llm.MODEL_QUALITY,
    )

    conclusion = f"\n\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d')} 周复盘]\n{review_text}"
    brand = at.get_brand_config()
    existing = brand["fields"].get("AI Experience Notes 经验结论", "")
    # 只保留最近约 6000 字的经验,防止无限追加污染后续 prompt(新结论在末尾,截头不截尾)
    combined = (existing + conclusion)[-6000:]
    at.update_brand_config(brand["id"], {"AI Experience Notes 经验结论": combined})
    print("✅ 周复盘完成")


