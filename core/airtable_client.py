"""
Airtable REST API 封装
所有表的读写操作集中在这里，其他模块只调用这个文件
"""

import os
import uuid
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

from core.retry import retry

load_dotenv()

PAT         = os.getenv("AIRTABLE_PAT")
BASE_ID     = os.getenv("AIRTABLE_BASE_ID")
TBL_BRAND   = os.getenv("AIRTABLE_BRAND_CONFIG_TABLE")
TBL_PIPE    = os.getenv("AIRTABLE_PIPELINE_TABLE")
TBL_STATS   = os.getenv("AIRTABLE_STATS_TABLE")

HEADERS = {
    "Authorization": f"Bearer {PAT}",
    "Content-Type": "application/json",
}

API = f"https://api.airtable.com/v0/{BASE_ID}"


@retry()
def _get(table_id: str, record_id: str) -> dict:
    resp = requests.get(f"{API}/{table_id}/{record_id}", headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()


@retry()
def _patch(table_id: str, record_id: str, fields: dict) -> dict:
    resp = requests.patch(
        f"{API}/{table_id}/{record_id}",
        headers=HEADERS,
        json={"fields": fields},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


@retry()
def _create(table_id: str, fields: dict) -> dict:
    resp = requests.post(
        f"{API}/{table_id}",
        headers=HEADERS,
        json={"fields": fields},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


@retry()
def _list(table_id: str, filter_formula: str = "", max_records: int = 100) -> list[dict]:
    params = {"maxRecords": max_records}
    if filter_formula:
        params["filterByFormula"] = filter_formula
    resp = requests.get(f"{API}/{table_id}", headers=HEADERS, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json().get("records", [])


# ============================================================
# Brand Config 表
# ============================================================

def get_brand_config() -> dict:
    """读取 Brand Config 表第一行（唯一的配置行）"""
    records = _list(TBL_BRAND, max_records=1)
    if not records:
        raise RuntimeError("Brand Config 表没有数据，请先填写配置")
    return records[0]


def update_brand_config(record_id: str, fields: dict) -> dict:
    return _patch(TBL_BRAND, record_id, fields)


# ============================================================
# Content Pipeline 表
# ============================================================

def create_pipeline_run(run_id: str) -> dict:
    """新建一行流水线记录，返回创建的 record"""
    fields = {
        "Run ID": run_id,
        "Created At 创建时间": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "Current Stage 当前状态": "情报采集中",
    }
    return _create(TBL_PIPE, fields)


def get_pipeline_record(record_id: str) -> dict:
    return _get(TBL_PIPE, record_id)


def update_pipeline(record_id: str, fields: dict) -> dict:
    return _patch(TBL_PIPE, record_id, fields)


def set_stage(record_id: str, stage: str, notes: str = "") -> None:
    fields = {"Current Stage 当前状态": stage}
    if notes:
        fields["Notes 备注"] = notes
    _patch(TBL_PIPE, record_id, fields)


def set_failed(record_id: str, error_msg: str) -> None:
    _patch(TBL_PIPE, record_id, {
        "Current Stage 当前状态": "失败",
        "Notes 备注": f"[错误] {error_msg}",
    })
    # 主动告警（若配置了 ALERT_WEBHOOK_URL），失败不再只躺在 Notes 字段里
    try:
        from core import notifier
        notifier.notify(f"记录 {record_id} → 失败：{error_msg}", level="error")
    except Exception:
        pass


def list_pending_publish() -> list[dict]:
    """列出「待发布」且尚未发布、发布日期留空或已到（今天及更早）的记录"""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    scheduled = "{Scheduled Publish Time 发布日期}"
    formula = (
        f"AND("
        f"{{Current Stage 当前状态}}='待发布', "
        f"OR({scheduled}=BLANK(), IS_SAME({scheduled}, '{today}', 'day'), IS_BEFORE({scheduled}, '{today}')), "
        f"{{Publish Links 发布链接}}=''"
        f")"
    )
    return _list(TBL_PIPE, filter_formula=formula)


def list_for_stats(days_ago: int) -> list[dict]:
    """列出 N 天前发布的记录，用于数据回收"""
    from datetime import timedelta
    target = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    formula = f"IS_SAME({{Published At 发布时间}}, '{target}', 'day')"
    return _list(TBL_PIPE, filter_formula=formula)


def list_stats_for_review(days: int = 30) -> list[dict]:
    """列出过去 N 天内已发布的记录，用于周复盘"""
    formula = f"AND({{Publish Links 发布链接}}!='', {{Current Stage 当前状态}}='已完成')"
    return _list(TBL_PIPE, filter_formula=formula, max_records=200)


# ============================================================
# Video File 附件操作
# ============================================================

def upload_video_attachment(record_id: str, video_url: str, filename: str = "video.mp4") -> dict:
    """
    把视频 URL 写入 Video File 附件字段（Airtable 会自动下载）
    """
    return _patch(TBL_PIPE, record_id, {
        "Video File 视频": [{"url": video_url, "filename": filename}]
    })


def delete_video_attachment(record_id: str) -> dict:
    """发布完成后清空 Video File 附件字段"""
    return _patch(TBL_PIPE, record_id, {"Video File 视频": []})


# ============================================================
# Video Stats 表
# ============================================================

def create_stat_record(
    run_id: str,
    platform: str,
    platform_video_id: str,
    play: int, like: int, comment: int, share: int,
) -> dict:
    fields = {
        "Run ID": run_id,
        "Platform 平台": platform.capitalize(),
        "Platform Video ID": platform_video_id,
        "Collected At": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "Play Count 播放量": play,
        "Like Count 点赞量": like,
        "Comment Count 评论数": comment,
        "Share Count 分享数": share,
    }
    return _create(TBL_STATS, fields)


def list_stats(days: int = 30) -> list[dict]:
    return _list(TBL_STATS, max_records=500)


# ============================================================
# 工具函数
# ============================================================

def generate_run_id() -> str:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    short = str(uuid.uuid4())[:6].upper()
    return f"RUN-{today}-{short}"


# ============================================================
# Webhook Payload 拉取
# ============================================================

@retry()
def fetch_webhook_payloads(webhook_id: str, cursor: int = None) -> dict:
    """从 Airtable 拉取 cursor 之后的 Webhook 变更数据；返回里的 cursor 是下次的起点"""
    url = f"https://api.airtable.com/v0/bases/{BASE_ID}/webhooks/{webhook_id}/payloads"
    params = {}
    if cursor is not None:
        params["cursor"] = cursor
    resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()
