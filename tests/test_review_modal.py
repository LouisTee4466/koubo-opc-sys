"""Adversarial review tests for modal_app: webhook handler edge cases, claim races, C.1/C.2 traces."""
import asyncio, os, sys, threading, types
sys.path.insert(0, os.getcwd())
os.environ.setdefault("ANTHROPIC_API_KEY", "test-placeholder")
os.environ["MONTHLY_BUDGET_USD"] = "0"

import modal_app as app

# capture raw bodies BEFORE stubbing any Function attribute
webhook_raw = app.webhook_handler._raw_f_
dispatch_raw = app.dispatch_change._raw_f_
topic_raw = app.handle_topic_done._raw_f_
heygen_raw = app.handle_heygen_ready._raw_f_
poll_raw = app.poll_heygen._raw_f_
SRC = open("modal_app.py", encoding="utf-8").read()


class FakeDict(dict):
    """thread-safe mimic of modal.Dict.put(skip_if_exists) / pop / get / __setitem__"""
    _lock = threading.Lock()

    def put(self, key, value, *, skip_if_exists=False):
        with self._lock:
            if skip_if_exists and key in self:
                return False
            self[key] = value
            return True


state = FakeDict()
app.STATE_DICT = state
app.BUDGET_DICT = FakeDict()

# ============================================================
# (i) webhook handler
# ============================================================
spawned = []
app.handle_new_record = types.SimpleNamespace(spawn=lambda **kw: spawned.append(("new", kw["record_id"])))
app.dispatch_change = types.SimpleNamespace(spawn=lambda **kw: spawned.append(("dispatch", kw["record_id"])))
fetches, responses = [], {}


def fetch(webhook_id, cursor=None):
    fetches.append(cursor)
    return responses["fn"](cursor)


app._load = lambda: (types.SimpleNamespace(fetch_webhook_payloads=fetch), None, None, None, None, None, None)


class Req:
    async def json(self):
        return {"webhook": {"id": "achX"}, "base": {"id": "app1"}, "timestamp": "t"}


KEY = "webhook_cursor:achX"

state.clear(); spawned.clear(); fetches.clear()
responses["fn"] = lambda c: {"payloads": [
    {"changedTablesById": {"tbl": {"createdRecordsById": {"recX": {}}, "changedRecordsById": {"recX": {}, "recY": {}}}}},
    {"changedTablesById": {"tbl": {"changedRecordsById": {"recX": {}}}}}],
    "cursor": 3, "mightHaveMore": False}
asyncio.run(webhook_raw(Req()))
assert spawned.count(("new", "recX")) == 1 and spawned.count(("dispatch", "recX")) == 1, spawned
assert ("dispatch", "recY") in spawned and len(spawned) == 3, spawned
assert state[KEY] == 3
print("(i-a) created+changed same record: new x1, dispatch x1 ->", spawned)

state.clear(); spawned.clear(); fetches.clear()
responses["fn"] = lambda c: {"payloads": [{"changedTablesById": {"tbl": {"changedRecordsById": {"recZ": {}}}}}],
                             "mightHaveMore": False}
asyncio.run(webhook_raw(Req()))
assert KEY not in state, state
assert spawned == [("dispatch", "recZ")]
asyncio.run(webhook_raw(Req()))
assert fetches == [None, None] and spawned == [("dispatch", "recZ")] * 2, (fetches, spawned)
print("(i-b) response lacks cursor: None is never stored (nothing to advance to) ->", spawned)

state.clear(); spawned.clear(); fetches.clear()
state[KEY] = 77
asyncio.run(webhook_raw(Req()))
assert state[KEY] == 77 and fetches == [77]
print("(i-c) stored cursor kept when response lacks cursor")

state.clear(); spawned.clear(); fetches.clear()


def endless(c):
    c = c or 0
    return {"payloads": [{"changedTablesById": {"tbl": {"changedRecordsById": {f"rec{c % 7}": {}}}}}],
            "cursor": c + 1, "mightHaveMore": True}


responses["fn"] = endless
asyncio.run(webhook_raw(Req()))
assert len(fetches) == 40 and state[KEY] == 40 and len(spawned) == 7, (len(fetches), state, len(spawned))
asyncio.run(webhook_raw(Req()))
assert fetches[40] == 40 and state[KEY] == 80
print("(i-d) >40 pages: capped at 40 fetches/ping, cursor", state[KEY], "spawns deduped to 7")

state.clear(); spawned.clear(); fetches.clear()


def fail_on_5(c):
    if (c or 0) == 4:
        raise RuntimeError("429")
    return endless(c)


responses["fn"] = fail_on_5
asyncio.run(webhook_raw(Req()))
assert spawned == [] and KEY not in state, (spawned, state)
print("(i-e) mid-pull failure: no spawns, cursor untouched")

# ============================================================
# (ii) claims: concurrency + failure/retry
# ============================================================
stages, failed, notes_writes = [], [], []
rec = {"fields": {}}
calls = {"script": 0, "submit": 0}
barrier = {"b": None}


def _reset():
    stages.clear(); failed.clear(); notes_writes.clear(); state.clear()
    for k in calls:
        calls[k] = 0


def get_brand():
    if barrier["b"] is not None:
        barrier["b"].wait(timeout=5)
    return {"id": "b", "fields": {}}


def set_stage(rid, stage, notes=""):
    stages.append(stage)
    rec["fields"]["Current Stage 当前状态"] = stage
    if notes:
        notes_writes.append(notes)
        rec["fields"]["Notes 备注"] = notes


def set_failed(rid, msg):
    failed.append(msg)
    rec["fields"]["Current Stage 当前状态"] = "失败"


def update_pipeline(rid, fields):
    rec["fields"].update(fields)
    if "Notes 备注" in fields:
        notes_writes.append(fields["Notes 备注"])


fake_at = types.SimpleNamespace(
    get_pipeline_record=lambda rid: {"id": rid, "fields": dict(rec["fields"])},
    get_brand_config=get_brand, set_stage=set_stage, set_failed=set_failed,
    update_pipeline=update_pipeline, generate_run_id=lambda: "RUN-1",
    upload_video_attachment=lambda rid, url, fn: None,
)
boom = {"on": False}


def gen_script(**kw):
    calls["script"] += 1
    if boom["on"]:
        raise RuntimeError("claude down")
    return {"hooks": [], "body": "b", "ctas": []}


def submit_video(s, a, v):
    calls["submit"] += 1
    return f"vid{calls['submit']}"


copy_mod = types.SimpleNamespace(generate_script=gen_script,
                                 review_script=lambda h, b, c: {"risk_level": "低", "summary": "", "issues": []})
vgen = types.SimpleNamespace(submit_video=submit_video, check_video_status=lambda vid: vgen.status)
vgen.status = {"status": "completed", "video_url": "http://v", "duration": 0}
app._load = lambda: (fake_at, None, None, copy_mod, None, vgen, None)
polls, hs = [], []
app.poll_heygen = types.SimpleNamespace(spawn=lambda **kw: polls.append(kw))
app.handle_heygen_ready = types.SimpleNamespace(spawn=lambda **kw: hs.append(kw["record_id"]))

_reset()
rec["fields"] = {"Final Topic 最终选题": "SAME", "Current Stage 当前状态": "等待选题"}
barrier["b"] = threading.Barrier(2)
errs = []


def run():
    try:
        topic_raw("recA")
    except Exception as e:
        errs.append(e)


ts = [threading.Thread(target=run) for _ in range(2)]
[t.start() for t in ts]; [t.join() for t in ts]
barrier["b"] = None
assert not errs, errs
assert calls["script"] == 1 and stages.count("文案生成中") == 1 and stages.count("等待文案审核") == 1, (calls, stages)
print("(ii-a) two concurrent handle_topic_done, same topic: exactly one proceeded")

_reset()
rec["fields"] = {"Final Topic 最终选题": "SAME", "Current Stage 当前状态": "等待选题"}
boom["on"] = True
try:
    topic_raw("recA"); raise SystemExit("expected raise")
except RuntimeError:
    pass
assert rec["fields"]["Current Stage 当前状态"] == "失败"
assert not any(k.startswith("claim:recA:topic") for k in state), state
boom["on"] = False
# user resets Current Stage -> 等待选题 (Topic Status still done) -> dispatch -> handle_topic_done
rec["fields"]["Current Stage 当前状态"] = "等待选题"
topic_raw("recA")
assert calls["script"] == 2 and rec["fields"]["Current Stage 当前状态"] == "等待文案审核"
print("(ii-b) failure released claim; retry with same topic after manual stage reset proceeded")

n, nf, nn = calls["script"], len(failed), len(notes_writes)
rec["fields"]["Current Stage 当前状态"] = "等待选题"
topic_raw("recA")
assert calls["script"] == n and len(failed) == nf and len(notes_writes) == nn
assert rec["fields"]["Current Stage 当前状态"] == "等待选题"
print("(ii-c) success then re-run same topic: silently skipped, record left in 等待选题, no Notes  [UX TRAP]")

# ============================================================
# C.1 trace: dispatch re-render branch -> follow-up webhook -> handle_heygen_ready with same script hash
# ============================================================
_reset(); polls.clear(); hs.clear()
rec["fields"] = {"Current Stage 当前状态": "等待视频生成", "Final Script 完整口播文案": "SCRIPT-V1", "Heygen Status": "ready"}
dispatch_raw("recA")                       # 等待视频生成 + ready -> spawn handle_heygen_ready
assert hs == ["recA"]
heygen_raw("recA")
assert calls["submit"] == 1 and polls[0]["video_id"] == "vid1" and rec["fields"]["Current Stage 当前状态"] == "视频生成中"
claim_key = f"claim:recA:heygen:{app._digest('SCRIPT-V1')}"
assert claim_key in state
assert polls[0]["step"] == f"heygen:{app._digest('SCRIPT-V1')}", polls[0]
poll_raw("recA", "vid1", 1, step=polls[0]["step"])   # completed -> 等待视频审核, Heygen Status done
assert rec["fields"]["Current Stage 当前状态"] == "等待视频审核" and rec["fields"]["Heygen Status"] == "done"
assert claim_key not in state, "claim is released on completion"
# user dislikes video, does NOT edit script, flips Heygen Status back to ready
rec["fields"]["Heygen Status"] = "ready"
hs.clear()
dispatch_raw("recA")                       # 等待视频审核 + ready -> set_stage(等待视频生成) only
assert rec["fields"]["Current Stage 当前状态"] == "等待视频生成" and hs == []
assert claim_key not in state
assert rec["fields"]["Notes 备注"].startswith("🔁 正在重新提交 HeyGen"), rec["fields"]["Notes 备注"]
dispatch_raw("recA")                       # follow-up webhook from that write
assert hs == ["recA"]
heygen_raw("recA")
assert calls["submit"] == 2, "unchanged-script re-render must resubmit"
assert rec["fields"]["Current Stage 当前状态"] == "视频生成中" and polls[-1]["video_id"] == "vid2"
assert claim_key in state, "new claim taken for the re-render (still blocks a concurrent double submit)"
heygen_raw("recA")                         # duplicate spawn of the re-render: stage guard + claim
assert calls["submit"] == 2
print("(C.1) unchanged-script re-render: claim released, Notes written, HeyGen resubmitted exactly once")

# edited script path still works
rec["fields"]["Current Stage 当前状态"] = "等待视频生成"
rec["fields"]["Final Script 完整口播文案"] = "SCRIPT-V1 "
heygen_raw("recA")
assert calls["submit"] == 3
print("(C.1) edited script (1 char) -> resubmits: documented path works")

# ============================================================
# C.2 trace: HeyGen timeout / failed -> set_failed; CLAUDE.md §7 retry advice cannot work
# ============================================================
_reset(); polls.clear(); hs.clear()
rec["fields"] = {"Current Stage 当前状态": "等待视频生成", "Final Script 完整口播文案": "SCRIPT-V2", "Heygen Status": "ready"}
heygen_raw("recA")
assert calls["submit"] == 1
poll_raw("recA", "vid1", 25, step=polls[-1]["step"])   # timeout path
assert rec["fields"]["Current Stage 当前状态"] == "失败" and "超时" in failed[-1]
ck = f"claim:recA:heygen:{app._digest('SCRIPT-V2')}"
assert ck not in state, "claim must be released on poll timeout"
# stage 失败 has no dispatch branch (by design): flipping Heygen Status alone does nothing
hs.clear()
dispatch_raw("recA")
assert hs == [] and rec["fields"]["Current Stage 当前状态"] == "失败"
# documented retry: reset Current Stage -> 等待视频生成 (Heygen Status still ready) -> resubmits same script
rec["fields"]["Current Stage 当前状态"] = "等待视频生成"
dispatch_raw("recA")
assert hs == ["recA"]
heygen_raw("recA")
assert calls["submit"] == 2 and rec["fields"]["Current Stage 当前状态"] == "视频生成中"
print("(C.2) HeyGen timeout: claim released; manual stage reset -> same script resubmitted")

# HeyGen 'failed' status path: same recovery
_reset(); polls.clear(); hs.clear()
rec["fields"] = {"Current Stage 当前状态": "等待视频生成", "Final Script 完整口播文案": "SCRIPT-V3", "Heygen Status": "ready"}
heygen_raw("recA")
vgen.status = {"status": "failed", "error": {"msg": "render error"}}
poll_raw("recA", "vid1", 1, step=polls[-1]["step"])
assert rec["fields"]["Current Stage 当前状态"] == "失败"
assert f"claim:recA:heygen:{app._digest('SCRIPT-V3')}" not in state, "claim must be released on HeyGen failed"
rec["fields"]["Current Stage 当前状态"] = "等待视频生成"
heygen_raw("recA")
assert calls["submit"] == 2
print("(C.2) HeyGen failed: claim released, same-script retry after stage reset proceeds")

# stage branches present in dispatch: confirm no recovery branch for 失败
branches = sorted(set(l.strip() for l in SRC.splitlines() if l.strip().startswith(("if stage ==", "elif stage =="))))
assert not any("失败" in b for b in branches), branches
print("(C.2) dispatch branches:", branches)

print("ALL REVIEW MODAL TESTS PASSED")
