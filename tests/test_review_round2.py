"""Round-2 regression hunt on the fixes: duplicate-event interleavings for the re-render branch,
stale-hash release in poll_heygen, mid-pull cursor absence, collect_stats back-fill isolation."""
import asyncio, json, os, sys, threading, types
sys.path.insert(0, os.getcwd())
os.environ.setdefault("ANTHROPIC_API_KEY", "test-placeholder")
os.environ["MONTHLY_BUDGET_USD"] = "0"

import modal_app as app

webhook_raw = app.webhook_handler._raw_f_
dispatch_raw = app.dispatch_change._raw_f_
heygen_raw = app.handle_heygen_ready._raw_f_
poll_raw = app.poll_heygen._raw_f_
collect_raw = app.collect_stats._raw_f_


class FakeDict(dict):
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

rec = {"fields": {}}
stages, failed, notes_log = [], [], []
calls = {"submit": 0}
snapshot_hook = {"fn": None}   # lets a thread read a stale snapshot


def get_record(rid):
    if snapshot_hook["fn"] is not None and threading.current_thread().name == "D1prime":
        return snapshot_hook["fn"]()
    return {"id": rid, "fields": dict(rec["fields"])}


def set_stage(rid, stage, notes=""):
    stages.append(stage)
    rec["fields"]["Current Stage 当前状态"] = stage
    if notes:
        notes_log.append(notes); rec["fields"]["Notes 备注"] = notes


def set_failed(rid, msg):
    failed.append(msg); rec["fields"]["Current Stage 当前状态"] = "失败"


def update_pipeline(rid, fields):
    rec["fields"].update(fields)


fake_at = types.SimpleNamespace(
    get_pipeline_record=get_record, get_brand_config=lambda: {"id": "b", "fields": {}},
    set_stage=set_stage, set_failed=set_failed, update_pipeline=update_pipeline,
    upload_video_attachment=lambda rid, url, fn: None,
)
vgen = types.SimpleNamespace(submit_video=lambda s, a, v: (calls.__setitem__("submit", calls["submit"] + 1), f"vid{calls['submit']}")[1],
                             check_video_status=lambda vid: vgen.status)
vgen.status = {"status": "completed", "video_url": "http://v", "duration": 0}
app._load = lambda: (fake_at, None, None, None, None, vgen, None)
hs, polls = [], []
app.handle_heygen_ready = types.SimpleNamespace(spawn=lambda **kw: hs.append(kw["record_id"]))
app.poll_heygen = types.SimpleNamespace(spawn=lambda **kw: polls.append(kw))

SCRIPT = "SCRIPT-SAME"
KEY = f"claim:recA:heygen:{app._digest(SCRIPT)}"


def fresh_after_first_render():
    """record state right after a completed first render; user has just flipped Heygen Status -> ready"""
    state.clear(); stages.clear(); failed.clear(); notes_log.clear(); hs.clear(); polls.clear(); calls["submit"] = 0
    rec["fields"] = {"Current Stage 当前状态": "等待视频生成", "Final Script 完整口播文案": SCRIPT, "Heygen Status": "ready"}
    heygen_raw("recA"); poll_raw("recA", "vid1", 1, step=polls[-1]["step"])
    assert calls["submit"] == 1 and KEY not in state and rec["fields"]["Current Stage 当前状态"] == "等待视频审核"
    rec["fields"]["Heygen Status"] = "ready"
    hs.clear()


def drain_followups():
    """emulate: every set_stage write produced a webhook -> dispatch; every dispatch spawn -> handle_heygen_ready"""
    for _ in range(4):
        n_before = len(hs)
        dispatch_raw("recA")
        while hs:
            hs.pop(0); heygen_raw("recA")
        if len(hs) == n_before == 0:
            break


# ---------- (a) interleaving 1: E1' arrives before D1's set_stage (both read 等待视频审核+ready) ----------
fresh_after_first_render()
stale1 = {"id": "recA", "fields": dict(rec["fields"])}
snapshot_hook["fn"] = lambda: stale1             # D1' read the record before D1 wrote
dispatch_raw("recA")                             # D1: set_stage only (claim was released at completion)
t = threading.Thread(target=dispatch_raw, args=("recA",), name="D1prime"); t.start(); t.join()
snapshot_hook["fn"] = None
assert stages[-2:] == ["等待视频生成", "等待视频生成"], stages
drain_followups()
assert calls["submit"] == 2, calls               # 1 original + exactly 1 re-render
print("(a-1) duplicate before set_stage: HeyGen submitted exactly once for the re-render")

# ---------- (a) interleaving 2: E1' after set_stage, before H1 claims -> D1' sees 等待视频生成+ready, spawns H directly ----------
fresh_after_first_render()
dispatch_raw("recA")                             # D1: release + set_stage
dispatch_raw("recA")                             # W1 -> dispatch: spawns H1
dispatch_raw("recA")                             # D1' (late read): also spawns H1'
assert hs == ["recA", "recA"]
heygen_raw("recA"); heygen_raw("recA")
assert calls["submit"] == 2 and rec["fields"]["Current Stage 当前状态"] == "视频生成中"
print("(a-2) duplicate after set_stage / before claim: stage guard + claim -> exactly once")

# ---------- (a) interleaving 3: E1' after H1 claimed and stage=视频生成中 ----------
fresh_after_first_render()
dispatch_raw("recA"); dispatch_raw("recA"); heygen_raw("recA")
assert calls["submit"] == 2
hs.clear(); dispatch_raw("recA")                 # D1' reads 视频生成中 + ready -> no branch
assert hs == [] and calls["submit"] == 2
print("(a-3) duplicate after claim/视频生成中: no branch matches -> exactly once")

# ---------- (a) interleaving 4: D1' read the record BEFORE D1's set_stage, but executes its write AFTER H1 claimed ----------
# (the branch no longer releases anything, so the stale thread is gated on its set_stage write instead)
fresh_after_first_render()
stale = {"id": "recA", "fields": dict(rec["fields"])}     # 等待视频审核 + ready
snapshot_hook["fn"] = lambda: stale
gate = threading.Event()
orig_set_stage = fake_at.set_stage


def gated_set_stage(rid, stage, notes=""):
    if threading.current_thread().name == "D1prime":
        gate.wait(timeout=5)
    orig_set_stage(rid, stage, notes)


fake_at.set_stage = gated_set_stage
d1p = threading.Thread(target=dispatch_raw, args=("recA",), name="D1prime"); d1p.start()
# main thread = D1 full path + follow-up webhook + H1
dispatch_raw("recA")                             # D1: set_stage(等待视频生成)
dispatch_raw("recA")                             # W1 -> H1 spawned
hs.clear(); heygen_raw("recA")                   # H1 claims, 视频生成中, submit #2
assert calls["submit"] == 2 and KEY in state
gate.set(); d1p.join()                           # D1' re-parks the stage but no longer touches the claim
assert KEY in state and rec["fields"]["Current Stage 当前状态"] == "等待视频生成"
dispatch_raw("recA")                             # W2 from D1's write -> H2
heygen_raw("recA")                               # stage guard passes, claim blocks
fake_at.set_stage = orig_set_stage; snapshot_hook["fn"] = None
assert calls["submit"] == 2, calls
assert KEY in state, "H1's live claim must survive the stale duplicate dispatch"
print("(a-4) stale-read D1' after H1 claimed: claim untouched, H2 blocked -> exactly once")

# ---------- (b) poll_heygen release with edited script during flight ----------
state.clear(); stages.clear(); failed.clear(); calls["submit"] = 0; polls.clear()
rec["fields"] = {"Current Stage 当前状态": "等待视频生成", "Final Script 完整口播文案": "OLD", "Heygen Status": "ready"}
heygen_raw("recA")
old_key = f"claim:recA:heygen:{app._digest('OLD')}"
assert polls[-1]["step"] == f"heygen:{app._digest('OLD')}", polls[-1]
rec["fields"]["Final Script 完整口播文案"] = "NEW"        # user edits while rendering
poll_raw("recA", "vid1", 25, step=polls[-1]["step"])     # timeout -> releases the key it was spawned with
assert old_key not in state, "release must target the claim actually taken (OLD), not the current record text"
assert f"claim:recA:heygen:{app._digest('NEW')}" not in state
# retry with NEW script after stage reset -> proceeds
rec["fields"]["Current Stage 当前状态"] = "等待视频生成"
heygen_raw("recA"); assert calls["submit"] == 2
# reverting to OLD text and retrying -> no stale claim, proceeds too
state.pop(f"claim:recA:heygen:{app._digest('NEW')}", None)
rec["fields"].update({"Current Stage 当前状态": "等待视频生成", "Final Script 完整口播文案": "OLD"})
heygen_raw("recA")
assert calls["submit"] == 3, calls
# failed path releases the same way; a Dict hiccup on release must not block set_failed
vgen.status = {"status": "failed", "error": {"msg": "render error"}}
orig_pop = FakeDict.pop
FakeDict.pop = lambda self, *a, **k: (_ for _ in ()).throw(RuntimeError("dict down"))
failed.clear()
poll_raw("recA", "vid3", 1, step=polls[-1]["step"])
FakeDict.pop = orig_pop
assert failed and "HeyGen 生成失败" in failed[-1] and rec["fields"]["Current Stage 当前状态"] == "失败"
vgen.status = {"status": "completed", "video_url": "http://v", "duration": 0}
print("(b) script edited in flight: exact claim key released on timeout; revert-to-OLD retry unblocked; release hiccup does not block set_failed")

# ---------- (c) cursor absent in the middle of a multi-page pull ----------
spawned, fetches = [], []
app.handle_new_record = types.SimpleNamespace(spawn=lambda **kw: spawned.append(("new", kw["record_id"])))
app.dispatch_change = types.SimpleNamespace(spawn=lambda **kw: spawned.append(("dispatch", kw["record_id"])))


def fetch(webhook_id, cursor=None):
    fetches.append(cursor)
    n = len(fetches)
    if n == 1:
        return {"payloads": [{"changedTablesById": {"t": {"changedRecordsById": {"r1": {}}}}}], "cursor": 10, "mightHaveMore": True}
    if n == 2:
        return {"payloads": [{"changedTablesById": {"t": {"changedRecordsById": {"r2": {}}}}}], "mightHaveMore": True}  # no cursor
    return {"payloads": [{"changedTablesById": {"t": {"changedRecordsById": {"r3": {}}}}}], "cursor": 30, "mightHaveMore": False}


app._load = lambda: (types.SimpleNamespace(fetch_webhook_payloads=fetch), None, None, None, None, None, None)


class Req:
    async def json(self):
        return {"webhook": {"id": "achC"}}


state.clear()
asyncio.run(webhook_raw(Req()))
assert fetches == [None, 10, 10], fetches          # page 3 re-requested with the last known cursor
assert state["webhook_cursor:achC"] == 30
assert sorted(r for _, r in spawned) == ["r1", "r2", "r3"]
print("(c) mid-pull missing cursor: re-requests with last known cursor, final cursor stored, all records dispatched")
# mightHaveMore=False with no cursor on the last page -> previous cursor kept
state.clear(); fetches.clear(); spawned.clear()


def fetch2(webhook_id, cursor=None):
    fetches.append(cursor)
    if len(fetches) == 1:
        return {"payloads": [], "cursor": 5, "mightHaveMore": True}
    return {"payloads": [{"changedTablesById": {"t": {"changedRecordsById": {"rX": {}}}}}], "mightHaveMore": False}


app._load = lambda: (types.SimpleNamespace(fetch_webhook_payloads=fetch2), None, None, None, None, None, None)
asyncio.run(webhook_raw(Req()))
assert state["webhook_cursor:achC"] == 5 and spawned == [("dispatch", "rX")]
print("(c) last page without cursor: cursor=5 kept (rX will be re-dispatched next ping; state-based no-op)")

# ---------- (d) collect_stats: failure on one platform does not swallow the other platform's back-fill ----------
rows, updates = [], []
links = {"instagram": {"success": True, "post_id": "ig1"}, "facebook": {"success": True, "post_id": "fb1"}}
stat_at = types.SimpleNamespace(
    list_for_stats=lambda d: [{"id": "recS", "fields": {"Run ID": "R", "Publish Links 发布链接": json.dumps(links)}}] if d == 1 else [],
    create_stat_record=lambda **kw: (rows.append(kw), (_ for _ in ()).throw(RuntimeError("airtable 500")) if kw["platform"] == "facebook" else None),
    update_pipeline=lambda rid, fields: updates.append(fields),
)


def fps(platform, sid):
    if platform == "instagram":
        raise RuntimeError("blotato down")       # fetch_platform_stats itself raising (contract breach) is contained
    return {"play": 1, "like": 1, "comment": 1, "share": 1, "post_url": "https://fb/1"}


app._load = lambda: (stat_at, None, None, None, None, None, types.SimpleNamespace(fetch_platform_stats=fps))
collect_raw()
assert [r["platform"] for r in rows] == ["facebook"]
assert updates == [], "facebook create_stat_record raised -> its back-fill is skipped (continue before back-fill)"
print("(d) ig fetch raised, fb create_stat raised: run continued; no back-fill because fb failed AFTER stats fetched (post_url lost for this run)")
rows.clear(); updates.clear()
stat_at.create_stat_record = lambda **kw: rows.append(kw)
collect_raw()
assert json.loads(updates[0]["Publish Links 发布链接"])["facebook"]["post_url"] == "https://fb/1"
assert "post_url" not in json.loads(updates[0]["Publish Links 发布链接"])["instagram"]
print("(d) ig raised, fb ok: fb back-filled, ig untouched, loop survived")

print("ALL ROUND2 TESTS PASSED")
