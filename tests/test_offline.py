import asyncio, json, os, sys, types
sys.path.insert(0, os.getcwd())
os.environ.setdefault("ANTHROPIC_API_KEY", "test-placeholder")

# ---------- llm ----------
from core import llm, topic_generator, copywriter, platform_adapter


def walk(s, path="$"):
    if isinstance(s, dict):
        if s.get("type") == "object":
            assert s.get("additionalProperties") is False, path
            for r in s.get("required", []):
                assert r in s["properties"], (path, r)
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
    assert json.dumps(s, ensure_ascii=False) == before, "schema mutated"
print("schemas strict OK:", list(schemas))


class Block:
    def __init__(self, type, text=""):
        self.type, self.text = type, text


class Msg:
    def __init__(self, content, stop_reason="end_turn", stop_details=None):
        self.content, self.stop_reason, self.stop_details = content, stop_reason, stop_details


calls = []


class FakeMessages:
    def __init__(self):
        self.next = None

    def create(self, **kw):
        calls.append(kw)
        return self.next


fm = FakeMessages()
llm._client = types.SimpleNamespace(beta=types.SimpleNamespace(messages=fm))

fm.next = Msg([Block("thinking"), Block("text", '{"topics": [{"title": "t", "angle": "a", "why_viral": "w"}]}')])
out = topic_generator.generate_topics({"tavily": [], "tikhub": None}, "p", "", "", "", "")
kw = calls[-1]
assert out == [{"title": "t", "angle": "a", "why_viral": "w"}]
assert kw["model"] == "claude-sonnet-5-5" and kw["output_config"]["effort"] == "low"
assert kw["output_config"]["format"]["type"] == "json_schema"
assert kw["betas"] == ["server-side-fallback-2026-07-01"] and kw["fallbacks"] == "default"
assert "tool_choice" not in kw and "tools" not in kw and "thinking" not in kw and kw["max_tokens"] == 16000

fm.next = Msg([Block("text", "复盘结论")])
assert llm.text("s", "u", model=llm.MODEL_QUALITY) == "复盘结论"
kw = calls[-1]
assert kw["model"] == "claude-opus-5-5" and kw["output_config"] == {"effort": "medium"}

for reason in ("refusal", "max_tokens"):
    fm.next = Msg([], stop_reason=reason, stop_details=types.SimpleNamespace(category="cyber"))
    n = len(calls)
    try:
        llm.text("s", "u", model=llm.MODEL_CHEAP)
        raise SystemExit("should raise")
    except RuntimeError as e:
        assert len(calls) == n + 1, "must not retry"
        print("raises on", reason, "->", e)

# real SDK builds the request (no network: transport stubbed)
import anthropic, httpx2

seen = {}


def handler(request):
    seen["headers"], seen["body"] = dict(request.headers), json.loads(request.content)
    return httpx2.Response(200, json={
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": [{"type": "text", "text": "{\"weaknesses\": [], \"revision_guidance\": \"g\"}"}],
        "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1}})


llm._client = anthropic.Anthropic(api_key="x", http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
res = llm.structured("s", "u", copywriter.CRITIQUE_SCHEMA, model=llm.MODEL_QUALITY)
assert res == {"weaknesses": [], "revision_guidance": "g"}
b = seen["body"]
assert seen["headers"]["anthropic-beta"] == "server-side-fallback-2026-07-01", seen["headers"].get("anthropic-beta")
assert b["fallbacks"] == "default" and b["output_config"]["format"]["schema"]["additionalProperties"] is False
print("wire body keys:", sorted(b))

# ---------- airtable formula ----------
from core import airtable_client as at

captured = {}


def fake_list(table, filter_formula="", max_records=100):
    captured["f"] = filter_formula
    return []


at._list = fake_list
at.list_pending_publish()
print("formula:", captured["f"])
assert "=BLANK()" in captured["f"] and "IS_BEFORE(" in captured["f"]
assert captured["f"].count("(") == captured["f"].count(")")

# ---------- modal app: webhook cursor ----------
import modal_app as app

state = {}
app.STATE_DICT = state
spawned = []


class Spawner:
    def __init__(self, name):
        self.name = name

    def spawn(self, **kw):
        spawned.append((self.name, kw["record_id"]))


app.handle_new_record, app.dispatch_change_orig = Spawner("new"), app.dispatch_change
app.dispatch_change = Spawner("dispatch")

# 120 payloads on the Airtable side, 50 per page
PAYLOADS = [{"changedTablesById": {"tbl": {"changedRecordsById": {f"rec{i % 7}": {}}}}} for i in range(1, 121)]
PAYLOADS[4]["changedTablesById"]["tbl"]["createdRecordsById"] = {"recNEW": {}}
fetches = []


def fake_fetch(webhook_id, cursor=None):
    start = cursor or 1
    fetches.append(start)
    page = PAYLOADS[start - 1:start - 1 + 50]
    nxt = start + len(page)
    return {"payloads": page, "cursor": nxt, "mightHaveMore": nxt <= len(PAYLOADS)}


fake_at = types.SimpleNamespace(fetch_webhook_payloads=fake_fetch)
app._load = lambda: (fake_at, None, None, None, None, None, None)


class Req:
    async def json(self):
        return {"webhook": {"id": "ach1"}, "base": {"id": "app1"}, "timestamp": "t"}


raw = app.webhook_handler._raw_f_
asyncio.run(raw(Req()))
assert fetches == [1, 51, 101], fetches
assert state == {"webhook_cursor:ach1": 121}, state
assert spawned.count(("new", "recNEW")) == 1
assert sorted(r for n, r in spawned if n == "dispatch") == [f"rec{i}" for i in range(7)], spawned
print("first ping: paged", fetches, "cursor ->", state)

# next ping: only the new payload is read
PAYLOADS.append({"changedTablesById": {"tbl": {"changedRecordsById": {"recLATEST": {}}}}})
fetches.clear()
spawned.clear()
asyncio.run(raw(Req()))
assert fetches == [121] and spawned == [("dispatch", "recLATEST")] and state["webhook_cursor:ach1"] == 122
print("second ping: only new change dispatched", spawned)


# fetch failure: cursor untouched, nothing dispatched
def boom(webhook_id, cursor=None):
    raise RuntimeError("airtable down")


fake_at.fetch_webhook_payloads = boom
spawned.clear()
asyncio.run(raw(Req()))
assert spawned == [] and state["webhook_cursor:ach1"] == 122
print("fetch failure: cursor kept at", state["webhook_cursor:ach1"])

# ---------- dispatch: regenerate + approve ----------
stages = []
rec = {"fields": {"Current Stage 当前状态": "等待视频审核", "Heygen Status": "ready"}}
disp_at = types.SimpleNamespace(get_pipeline_record=lambda rid: rec,
                                set_stage=lambda rid, stage, notes="": stages.append((stage, notes)))
app._load = lambda: (disp_at, None, None, None, None, None, None)
hs = []
app.handle_heygen_ready = types.SimpleNamespace(spawn=lambda **kw: hs.append(kw["record_id"]))
d = app.dispatch_change_orig._raw_f_
d("recA")
assert stages[-1][0] == "等待视频生成" and hs == [], "re-render must not spawn directly (double HeyGen submit)"
rec["fields"]["Current Stage 当前状态"] = "等待视频生成"
d("recA")
assert hs == ["recA"], "the follow-up webhook event submits HeyGen exactly once"
rec["fields"]["Current Stage 当前状态"] = "等待视频审核"
rec["fields"]["Heygen Status"] = "approved"
d("recA")
assert stages[-1][0] == "待发布" and "留空" in stages[-1][1] and hs == ["recA"]
rec["fields"]["Heygen Status"] = "done"
n = len(stages)
d("recA")
assert len(stages) == n
print("dispatch: ready -> regenerate, approved -> 待发布, done -> no-op")
print("ALL OFFLINE TESTS PASSED")
