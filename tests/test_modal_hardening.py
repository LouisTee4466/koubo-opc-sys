import json, os, sys, types
sys.path.insert(0, os.getcwd())
os.environ.setdefault("ANTHROPIC_API_KEY", "test-placeholder")

# ---------- llm._strict: required = all properties, recursively, non-mutating ----------
from core import llm, topic_generator, copywriter, platform_adapter


def walk(s, path="$"):
    if isinstance(s, dict):
        if s.get("type") == "object":
            assert s.get("additionalProperties") is False, path
            if "properties" in s:
                assert s.get("required") == list(s["properties"]), (path, s.get("required"))
        for k, v in s.items():
            walk(v, f"{path}.{k}")
    elif isinstance(s, list):
        for i, v in enumerate(s):
            walk(v, f"{path}[{i}]")


schemas = {"topics": topic_generator.TOPICS_SCHEMA, "script": copywriter.SCRIPT_SCHEMA,
           "critique": copywriter.CRITIQUE_SCHEMA, "review": copywriter.REVIEW_SCHEMA,
           "captions": platform_adapter.CAPTIONS_SCHEMA}
for name, s in schemas.items():
    before = json.dumps(s, ensure_ascii=False)
    walk(llm._strict(s))
    assert json.dumps(s, ensure_ascii=False) == before, f"{name} schema mutated"

st = llm._strict(platform_adapter.CAPTIONS_SCHEMA)
assert st["required"] == ["tiktok", "instagram", "facebook", "youtube"]
assert st["properties"]["youtube"]["required"] == ["title", "description"]
st = llm._strict(topic_generator.TOPICS_SCHEMA)
assert st["properties"]["topics"]["items"]["required"] == ["title", "angle", "reference", "why_viral"]
assert topic_generator.TOPICS_SCHEMA["properties"]["topics"]["items"]["required"] == ["title", "angle", "why_viral"]
st = llm._strict(copywriter.REVIEW_SCHEMA)
assert st["properties"]["issues"]["items"]["required"] == ["type", "original", "reason", "suggestion"]
assert copywriter.REVIEW_SCHEMA["properties"]["issues"]["items"]["required"] == ["type", "reason"]
print("_strict: required == properties on every nested object, originals untouched")

# ---------- modal app ----------
import modal_app as app


class FakeDict(dict):
    """mimics modal.Dict.put(skip_if_exists) / pop"""

    def put(self, key, value, *, skip_if_exists=False):
        if skip_if_exists and key in self:
            return False
        self[key] = value
        return True


state = FakeDict()
app.STATE_DICT = state

stages, failed, updates = [], [], []
rec = {"id": "recA", "fields": {}}
calls = {"intel": 0, "topics": 0, "script": 0, "review": 0, "adapt": 0, "submit": 0}


def _reset():
    stages.clear(); failed.clear(); updates.clear(); state.clear()
    for k in calls:
        calls[k] = 0


brand = {"id": "brand1", "fields": {"AI Profile Summary 账号定位": "p"}}
fake_at = types.SimpleNamespace(
    get_pipeline_record=lambda rid: rec,
    get_brand_config=lambda: brand,
    set_stage=lambda rid, stage, notes="": stages.append(stage),
    set_failed=lambda rid, msg: failed.append(msg),
    update_pipeline=lambda rid, fields: updates.append(fields),
    generate_run_id=lambda: "RUN-1",
)


def gen_intel(**kw):
    calls["intel"] += 1
    return {"x": 1}


def gen_topics(**kw):
    calls["topics"] += 1
    return [{"title": "t"}]


script_boom = {"on": False}


def gen_script(**kw):
    calls["script"] += 1
    if script_boom["on"]:
        raise RuntimeError("claude down")
    return {"hooks": [{"type": "a", "text": "h"}], "body": "b", "ctas": [{"type": "c", "text": "cta"}]}


def review_script(h, b, c):
    calls["review"] += 1
    return {"risk_level": "低风险", "summary": "ok", "approved": True, "issues": []}


def adapt(script, topic):
    calls["adapt"] += 1
    return {"TikTok Caption": "t", "Instagram Caption": "i", "Facebook Caption": "f", "YouTube Description": "y"}


def submit_video(script, avatar, voice):
    calls["submit"] += 1
    return f"vid-{calls['submit']}"


intel_mod = types.SimpleNamespace(gather_intelligence=gen_intel)
topic_mod = types.SimpleNamespace(generate_topics=gen_topics)
copy_mod = types.SimpleNamespace(generate_script=gen_script, review_script=review_script)
adapter_mod = types.SimpleNamespace(adapt_for_platforms=adapt)
vgen_mod = types.SimpleNamespace(submit_video=submit_video)
app._load = lambda: (fake_at, intel_mod, topic_mod, copy_mod, adapter_mod, vgen_mod, None)
polls = []
app.poll_heygen = types.SimpleNamespace(spawn=lambda **kw: polls.append(kw["video_id"]))

# --- handle_new_record: same record twice -> one run ---
_reset()
rec["fields"] = {}
new = app.handle_new_record._raw_f_
new("recA"); new("recA")
assert calls["intel"] == 1 and calls["topics"] == 1, calls
assert stages == ["情报采集中", "等待选题"], stages
assert "claim:recA:new_record" in state
print("new_record: duplicate spawn is a no-op")

# --- handle_topic_done: dedup on same topic, re-run on changed topic, failure releases claim ---
_reset()
rec["fields"] = {"Final Topic 最终选题": "T1"}
td = app.handle_topic_done._raw_f_
td("recA"); td("recA")
assert calls["script"] == 1 and calls["review"] == 1 and stages.count("文案生成中") == 1
rec["fields"]["Final Topic 最终选题"] = "T2"
td("recA")
assert calls["script"] == 2, "edited Final Topic must be allowed to re-run"
script_boom["on"] = True
rec["fields"]["Final Topic 最终选题"] = "T3"
try:
    td("recA")
    raise SystemExit("should raise")
except RuntimeError:
    pass
assert failed and "文案生成失败" in failed[-1]
script_boom["on"] = False
td("recA")
assert calls["script"] == 4, "retry with the same topic after a failure must run again"
rec["fields"]["Final Topic 最终选题"] = ""
n = calls["script"]
td("recA")
assert calls["script"] == n and "Final Topic 为空" in failed[-1]
print("topic_done: dedup / edited-topic rerun / failure releases claim / empty topic unchanged")

# --- handle_script_approved ---
_reset()
rec["fields"] = {"Final Script 完整口播文案": "S1", "Final Topic 最终选题": "T"}
sa = app.handle_script_approved._raw_f_
sa("recA"); sa("recA")
assert calls["adapt"] == 1 and stages == ["等待视频生成"], (calls, stages)
rec["fields"]["Final Script 完整口播文案"] = "S2"
sa("recA")
assert calls["adapt"] == 2
print("script_approved: dedup / edited-script rerun")

# --- handle_heygen_ready: stage guard kept + claim; re-render after script edit allowed ---
_reset()
polls.clear()
rec["fields"] = {"Current Stage 当前状态": "等待视频生成", "Final Script 完整口播文案": "S1"}
hr = app.handle_heygen_ready._raw_f_
hr("recA"); hr("recA")  # fake set_stage never mutates rec, so only the claim can stop the 2nd call
assert calls["submit"] == 1 and polls == ["vid-1"], (calls, polls)
rec["fields"]["Final Script 完整口播文案"] = "S1 edited"
hr("recA")
assert calls["submit"] == 2 and polls == ["vid-1", "vid-2"], "re-render with edited script must submit again"
rec["fields"]["Current Stage 当前状态"] = "视频生成中"
rec["fields"]["Final Script 完整口播文案"] = "S9"
hr("recA")
assert calls["submit"] == 2, "stage guard still in place"
assert not any(k.endswith(app._digest("S9")) for k in state), "stage guard must not consume a claim"
print("heygen_ready: claim dedup + stage guard + re-render on edited script")

# --- daily_pipeline spawns the three split jobs and still runs ① ---
_reset()
spawned = []
orig_jobs = {n: getattr(app, n) for n in ("publish_pending", "collect_stats", "refresh_webhook")}
for name in orig_jobs:
    setattr(app, name, types.SimpleNamespace(spawn=lambda _n=name, **kw: spawned.append(_n)))
created = []
fake_at.create_pipeline_run = lambda run_id: created.append(run_id) or {"id": "recNEW"}
fake_at.list_pending_publish = lambda: (_ for _ in ()).throw(AssertionError("② must not run inline"))
fake_at.list_for_stats = lambda d: (_ for _ in ()).throw(AssertionError("③ must not run inline"))
app._refresh_webhook = lambda: (_ for _ in ()).throw(AssertionError("④ must not run inline"))
app.daily_pipeline._raw_f_()
assert sorted(spawned) == ["collect_stats", "publish_pending", "refresh_webhook"], spawned
assert created == ["RUN-1"] and calls["intel"] == 1 and calls["topics"] == 1 and stages[-1] == "等待选题"
print("daily_pipeline: spawned", spawned, "and ran ① inline")

# split jobs behave like the old inline blocks
for name, fn in orig_jobs.items():
    setattr(app, name, fn)
app2 = app
pub_calls, stat_calls, stat_rows = [], [], []
pending = [{"id": "recP", "fields": {"Run ID": "RUN-P", "Video File 视频": [{"url": "http://v"}],
                                     "Instagram Caption": "ig", "Facebook Caption": "fb"}}]
stats_recs = [{"id": "recS", "fields": {"Run ID": "RUN-S", "Publish Links 发布链接": json.dumps(
    {"instagram": {"success": True, "post_id": "p1"}, "facebook": {"success": False}})}}]
pub_at = types.SimpleNamespace(
    list_pending_publish=lambda: pending,
    list_for_stats=lambda d: stats_recs if d == 1 else [],
    set_stage=lambda rid, stage, notes="": stages.append(stage),
    set_failed=lambda rid, msg: failed.append(msg),
    update_pipeline=lambda rid, fields: updates.append((rid, fields)),
    create_stat_record=lambda **kw: stat_rows.append(kw),
)
publisher = types.SimpleNamespace(
    publish_all_platforms=lambda **kw: pub_calls.append(kw) or {"instagram": {"success": True, "post_id": "p1"}},
    fetch_platform_stats=lambda platform, sid: stat_calls.append((platform, sid)) or
    {"play": 1, "like": 2, "comment": 3, "share": 4, "post_url": "http://post"},
)
app2._load = lambda: (pub_at, None, None, None, None, None, publisher)
_reset()
app2.publish_pending._raw_f_()
assert pub_calls == [{"video_url": "http://v", "instagram_caption": "ig", "facebook_caption": "fb"}]
assert stages == ["发布中", "已完成"] and updates[0][0] == "recP" and "Published At 发布时间" in updates[0][1]
_reset()
app2.collect_stats._raw_f_()
assert stat_calls == [("instagram", "p1")] and stat_rows[0]["play"] == 1 and stat_rows[0]["run_id"] == "RUN-S"
assert json.loads(updates[-1][1]["Publish Links 发布链接"])["instagram"]["post_url"] == "http://post"
# one bad Blotato response must not abort the rest of the day's stats
stats_recs.insert(0, {"id": "recBAD", "fields": {"Run ID": "RUN-BAD", "Publish Links 发布链接": json.dumps(
    {"instagram": {"success": True, "post_id": "boom"}, "facebook": {"success": True, "post_id": "p2"}})}})
good = publisher.fetch_platform_stats
publisher.fetch_platform_stats = lambda platform, sid: (_ for _ in ()).throw(RuntimeError("blotato 500")) if sid == "boom" else good(platform, sid)
_reset(); stat_calls.clear(); stat_rows.clear()
app2.collect_stats._raw_f_()
assert [r["platform_video_id"] for r in stat_rows] == ["p2", "p1"], stat_rows
assert sum(1 for rid, f in updates if rid == "recBAD") == 1, "good platform of the bad record still backfills post_url"
refreshed = []
app2._refresh_webhook = lambda: refreshed.append(1)
app2.refresh_webhook._raw_f_()
assert refreshed == [1]
print("split jobs: publish / stats / refresh bodies behave as before")
print("ALL HARDENING TESTS PASSED")
