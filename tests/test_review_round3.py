"""Round 3: independent re-run of the re-render interleavings against release-on-completion,
the release->stage-write window in poll_heygen, the a-4 re-park through to completion, and
poll_heygen backward compat (no `step`)."""
import os, sys, threading, types
sys.path.insert(0, os.getcwd())
os.environ.setdefault("ANTHROPIC_API_KEY", "test-placeholder")
os.environ["MONTHLY_BUDGET_USD"] = "0"

import modal_app as app

dispatch_raw = app.dispatch_change._raw_f_
heygen_raw = app.handle_heygen_ready._raw_f_
poll_raw = app.poll_heygen._raw_f_
SRC = open("modal_app.py", encoding="utf-8").read()


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
stages, failed = [], []
calls = {"submit": 0}
stale = {"snap": None}
hooks = {"on_update": None}


def get_record(rid):
    if stale["snap"] is not None and threading.current_thread().name == "D1prime":
        return stale["snap"]
    return {"id": rid, "fields": dict(rec["fields"])}


def set_stage(rid, stage, notes=""):
    stages.append(stage); rec["fields"]["Current Stage 当前状态"] = stage
    if notes:
        rec["fields"]["Notes 备注"] = notes


def set_failed(rid, msg):
    failed.append(msg); rec["fields"]["Current Stage 当前状态"] = "失败"


def update_pipeline(rid, fields):
    rec["fields"].update(fields)
    if hooks["on_update"] and fields.get("Heygen Status") == "done":
        hooks["on_update"]()


fake_at = types.SimpleNamespace(get_pipeline_record=get_record, get_brand_config=lambda: {"id": "b", "fields": {}},
                                set_stage=set_stage, set_failed=set_failed, update_pipeline=update_pipeline,
                                upload_video_attachment=lambda rid, url, fn: None)
vgen = types.SimpleNamespace(submit_video=lambda s, a, v: (calls.__setitem__("submit", calls["submit"] + 1), f"vid{calls['submit']}")[1],
                             check_video_status=lambda vid: vgen.status)
vgen.status = {"status": "completed", "video_url": "http://v", "duration": 0}
app._load = lambda: (fake_at, None, None, None, None, vgen, None)
hs, polls = [], []
app.handle_heygen_ready = types.SimpleNamespace(spawn=lambda **kw: hs.append(kw["record_id"]))
app.poll_heygen = types.SimpleNamespace(spawn=lambda **kw: polls.append(kw))

SCRIPT = "S"
KEY = f"claim:recA:heygen:{app._digest(SCRIPT)}"


def complete_last_render():
    p = polls[-1]
    poll_raw(p["record_id"], p["video_id"], 1, step=p["step"])


def after_first_render():
    state.clear(); stages.clear(); failed.clear(); hs.clear(); polls.clear(); calls["submit"] = 0; stale["snap"] = None
    rec["fields"] = {"Current Stage 当前状态": "等待视频生成", "Final Script 完整口播文案": SCRIPT, "Heygen Status": "ready"}
    heygen_raw("recA"); complete_last_render()
    assert calls["submit"] == 1 and KEY not in state and rec["fields"]["Current Stage 当前状态"] == "等待视频审核"
    rec["fields"]["Heygen Status"] = "ready"; hs.clear()


def run_all_spawned():
    while hs:
        hs.pop(0); heygen_raw("recA")


# static: dispatch re-render branch no longer releases; poll releases in all three terminal branches
branch = SRC[SRC.index('stage == "等待视频审核" and heygen_status == "ready"'):SRC.index('stage == "等待视频审核" and heygen_status == "approved"')]
assert "_release" not in branch, branch
poll_src = SRC[SRC.index("def poll_heygen"):SRC.index("def _refresh_webhook")]
assert poll_src.count("_release_quiet(record_id, step)") == 3
assert poll_src.index("_release_quiet(record_id, step)") < poll_src.index('at.set_failed(record_id, "HeyGen 视频生成超时')
comp = poll_src[poll_src.index('if status == "completed"'):poll_src.index('elif status == "failed"')]
assert comp.index('"Heygen Status": "done"') < comp.index('"等待视频审核"') < comp.index("_release_quiet")
print("(static) dispatch branch has no release; poll_heygen releases in timeout/completed/failed; completed releases AFTER the stage write (Q1)")

# a-1: both dispatches read 等待视频审核+ready
after_first_render()
stale["snap"] = {"id": "recA", "fields": dict(rec["fields"])}
dispatch_raw("recA")
t = threading.Thread(target=dispatch_raw, args=("recA",), name="D1prime"); t.start(); t.join(); stale["snap"] = None
assert stages[-2:] == ["等待视频生成"] * 2 and hs == []
dispatch_raw("recA"); dispatch_raw("recA")       # W1, W2
assert hs == ["recA", "recA"]; run_all_spawned()
assert calls["submit"] == 2; complete_last_render()
assert rec["fields"]["Current Stage 当前状态"] == "等待视频审核" and KEY not in state
print("(a-1) exactly once; completion releases; record ends 等待视频审核")

# a-2: D1' reads after D1's write -> spawns H directly
after_first_render()
dispatch_raw("recA"); dispatch_raw("recA"); dispatch_raw("recA")
assert hs == ["recA"] * 2; run_all_spawned()
assert calls["submit"] == 2 and rec["fields"]["Current Stage 当前状态"] == "视频生成中"
print("(a-2) exactly once")

# a-3: D1' after claim / 视频生成中
after_first_render()
dispatch_raw("recA"); dispatch_raw("recA"); run_all_spawned()
assert calls["submit"] == 2; dispatch_raw("recA")
assert hs == [] and calls["submit"] == 2
print("(a-3) exactly once")

# a-4: D1' stale read, its set_stage lands AFTER H1 claimed -> re-park; then W2 -> H2; then completion
after_first_render()
stale["snap"] = {"id": "recA", "fields": dict(rec["fields"])}
gate = threading.Event()
orig = fake_at.set_stage


def gated(rid, stage, notes=""):
    if threading.current_thread().name == "D1prime":
        gate.wait(timeout=5)
    orig(rid, stage, notes)


fake_at.set_stage = gated
d1p = threading.Thread(target=dispatch_raw, args=("recA",), name="D1prime"); d1p.start()
dispatch_raw("recA"); dispatch_raw("recA"); hs.clear(); heygen_raw("recA")   # D1, W1, H1 claims + submit #2
assert calls["submit"] == 2 and KEY in state and rec["fields"]["Current Stage 当前状态"] == "视频生成中"
gate.set(); d1p.join(); fake_at.set_stage = orig; stale["snap"] = None
assert rec["fields"]["Current Stage 当前状态"] == "等待视频生成" and KEY in state, "re-park happened, claim intact"
dispatch_raw("recA"); run_all_spawned()         # W2 -> H2: guard passes, claim blocks
assert calls["submit"] == 2 and KEY in state and rec["fields"]["Current Stage 当前状态"] == "等待视频生成"
complete_last_render()                           # H1's render finishes
assert rec["fields"]["Current Stage 当前状态"] == "等待视频审核" and rec["fields"]["Heygen Status"] == "done"
assert KEY not in state and not failed
dispatch_raw("recA")                             # any late event now sees 等待视频审核+done -> no-op
assert hs == [] and calls["submit"] == 2
print("(a-4) re-park left 等待视频生成 while rendering; H2 blocked; completion overwrote to 等待视频审核; not stuck; exactly once")

# window: release happens before the stage write in `completed`. A stale 等待视频生成+ready handler landing there:
# (w-1) normal path: stage is 视频生成中 during the window -> stage guard blocks
after_first_render()
dispatch_raw("recA"); dispatch_raw("recA"); run_all_spawned()
assert calls["submit"] == 2 and rec["fields"]["Current Stage 当前状态"] == "视频生成中"
hooks["on_update"] = lambda: heygen_raw("recA")  # fires right after _release_quiet, before set_stage(等待视频审核)
complete_last_render(); hooks["on_update"] = None
assert calls["submit"] == 2 and rec["fields"]["Current Stage 当前状态"] == "等待视频审核"
print("(w-1) stale handler inside the release->stage-write window, normal path: stage guard (视频生成中) blocks")

# (w-2) only if the stage had been re-parked to 等待视频生成 by an a-4 duplicate: guard passes, claim is free -> extra submit
after_first_render()
dispatch_raw("recA"); dispatch_raw("recA"); run_all_spawned()
assert calls["submit"] == 2
rec["fields"]["Current Stage 当前状态"] = "等待视频生成"   # emulate the a-4 re-park
hooks["on_update"] = lambda: heygen_raw("recA")
complete_last_render(); hooks["on_update"] = None
extra = calls["submit"] - 2
print(f"(w-2) re-parked + stale handler inside the window: extra submits = {extra} "
      f"(needs an a-4 stall AND a stale ready-event in a ~1s window after completion; record ends {rec['fields']['Current Stage 当前状态']})")
assert rec["fields"]["Current Stage 当前状态"] == "等待视频审核"   # poll's own set_stage still lands; not stuck
assert extra == 0, "Q1: release now happens after the stage write, so the claim is still held inside the window"

# backward compat: in-flight poll from the old deploy has no `step`
after_first_render()
state.clear()
for status in ({"status": "completed", "video_url": "http://v", "duration": 0},
               {"status": "failed", "error": {"m": "x"}}):
    vgen.status = status
    rec["fields"]["Current Stage 当前状态"] = "视频生成中"
    poll_raw("recA", "vidOLD", 1)               # old signature
vgen.status = {"status": "completed", "video_url": "http://v", "duration": 0}
poll_raw("recA", "vidOLD", 25)                  # timeout path, old signature
assert state == {} and failed[-1].startswith("HeyGen 视频生成超时")
# and `_release_quiet("")` is a no-op even when the Dict is broken
app.STATE_DICT = types.SimpleNamespace(pop=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("dict down")))
app._release_quiet("recA", "")
app._release_quiet("recA", "heygen:abc")        # swallowed
app.STATE_DICT = state
print("(compat) poll_heygen without step: completed/failed/timeout all run, no Dict access, nothing raised")

print("ALL ROUND3 TESTS PASSED")
