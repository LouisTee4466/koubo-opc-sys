"""Adversarial review tests: llm._strict wire body, Airtable formula, publisher URL matching."""
import json, os, re, sys, types
from urllib.parse import urlparse
sys.path.insert(0, os.getcwd())
os.environ.setdefault("ANTHROPIC_API_KEY", "test-placeholder")
os.environ.setdefault("BLOTATO_API_KEY", "x")
os.environ.setdefault("AIRTABLE_PIPELINE_TABLE", "tblP")

from core import llm, airtable_client, publisher, retry as retry_mod, copywriter, topic_generator
import requests

retry_mod.time.sleep = lambda s: None

# ============================================================
# (iii) _strict: property literally named "type", arrays of objects, nested object inside array
# ============================================================
SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": "string"},                      # property named `type`
        "properties": {"type": "integer"},               # property named `properties`
        "items": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "type": {"type": "object", "properties": {"deep": {"type": "string"}}},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["tags"],
        }},
        "maybe": {"anyOf": [{"type": "object", "properties": {"a": {"type": "string"}}}, {"type": "null"}]},
    },
    "required": ["type"],
}
st = llm._strict(SCHEMA)
assert st["required"] == ["type", "properties", "items", "maybe"], st["required"]
assert st["additionalProperties"] is False
assert st["properties"]["type"] == {"type": "string"}, st["properties"]["type"]
assert st["properties"]["properties"] == {"type": "integer"}
inner = st["properties"]["items"]["items"]
assert inner["required"] == ["type", "tags"] and inner["additionalProperties"] is False
assert inner["properties"]["type"]["required"] == ["deep"] and inner["properties"]["type"]["additionalProperties"] is False
assert st["properties"]["maybe"]["anyOf"][0]["required"] == ["a"]
assert "required" not in st["properties"]["items"], "array node must not get required"
assert SCHEMA["required"] == ["type"], "input mutated"
# the properties-map itself must never be treated as an object schema
assert "additionalProperties" not in st["properties"] and "required" not in st["properties"]
print("(iii-a) _strict: type-named property, nested arrays of objects, anyOf all handled; input untouched")

# wire body check: capture what hits the SDK
captured = {}


class Block:
    def __init__(self, text):
        self.type, self.text = "text", text


class Msg:
    stop_reason = "end_turn"; stop_details = None
    content = [Block(json.dumps({"ok": 1}))]


def fake_create(**kw):
    captured.update(kw)
    return Msg()


llm._client = types.SimpleNamespace(beta=types.SimpleNamespace(messages=types.SimpleNamespace(create=fake_create)))
for name, schema in {"review": copywriter.REVIEW_SCHEMA, "topics": topic_generator.TOPICS_SCHEMA, "adv": SCHEMA}.items():
    captured.clear()
    llm.structured("s", "u", schema, model="claude-sonnet-5-5")
    fmt = captured["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    wire = fmt["schema"]

    def walk(s, path):
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
    walk(wire, name)
    assert captured["betas"] == ["server-side-fallback-2026-07-01"] and captured["fallbacks"] == "default"
    assert captured["output_config"]["effort"] == "low"
    assert "tool_choice" not in captured and "tools" not in captured
    json.dumps(wire)
print("(iii-b) wire body: additionalProperties:false + required==properties on every object; beta/fallbacks set; no tool_choice")

# ============================================================
# (iv) list_pending_publish formula
# ============================================================
seen = {}


def fake_get(url, headers=None, params=None, timeout=None):
    seen["params"] = params
    return types.SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"records": []})


airtable_client.requests.get = fake_get
airtable_client.list_pending_publish()
f = seen["params"]["filterByFormula"]
print("     formula:", f)
assert f.count("(") == f.count(")"), (f.count("("), f.count(")"))
assert f.count("{") == f.count("}")
assert "BLANK()" in f and "IS_BEFORE(" in f and "IS_SAME(" in f
assert re.search(r"IS_SAME\(\{Scheduled Publish Time 发布日期\}, '\d{4}-\d{2}-\d{2}', 'day'\)", f)
assert re.search(r"IS_BEFORE\(\{Scheduled Publish Time 发布日期\}, '\d{4}-\d{2}-\d{2}'\)", f)
assert f.startswith("AND(") and f.endswith(")")
# OR(...) group fully closed before the Publish Links predicate
or_start = f.index("OR(") + 2
depth = 0
for i, ch in enumerate(f[or_start:]):
    depth += ch == "("; depth -= ch == ")"
    if depth == 0:
        or_end = or_start + i; break
assert f[or_end + 1:].startswith(", {Publish Links 发布链接}=''"), f[or_end + 1:]
# quotes balanced
assert f.count("'") % 2 == 0
print("(iv) formula parens/braces/quotes balanced, BLANK()/IS_SAME/IS_BEFORE present, OR group closed")

# ============================================================
# (v) fetch_platform_stats: publicUrl with trailing query string vs postUrl without
# ============================================================
calls = []
_MISSING = object()  # missing route = 404; explicit None = JSON null body


class FakeResp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code}", response=self)


def install(routes):
    def fake_get(url, headers=None, params=None, timeout=None):
        path = urlparse(url).path.removeprefix("/v2")
        calls.append((path, dict(params or {})))
        h = routes.get(path, _MISSING)
        if h is _MISSING:
            return FakeResp(404, {})
        if callable(h):
            h = h(params or {})
        return FakeResp(200, h)
    publisher.requests.get = fake_get
    calls.clear()


BASE = "https://www.instagram.com/reel/ABC123/"
QS = BASE + "?utm_source=ig_web_copy_link&igsh=xyz"
ANALYTICS = {"metrics": {"viewsCount": 10, "likesCount": 2, "commentsCount": 1, "sharesCount": 0}}

install({
    "/posts/sub-1": {"status": "published", "publicUrl": QS},
    "/published-posts": lambda p: {"items": [{"id": "pp-1", "postUrl": BASE}]},
    "/posts": lambda p: {"items": [{"id": "legacy-1", "state": {"postUrl": BASE}}]},
    "/posts/pp-1/analytics": ANALYTICS,
    "/posts/legacy-1/analytics": ANALYTICS,
})
out = publisher.fetch_platform_stats("instagram", "sub-1")
assert set(out) == {"play", "like", "comment", "share", "post_url"}
print("(v-a) publicUrl has ?query, postUrl without ->", out)
assert out == {"play": 10, "like": 2, "comment": 1, "share": 0, "post_url": QS}, out
assert [c[0] for c in calls] == ["/posts/sub-1", "/published-posts", "/posts/pp-1/analytics"], calls

# reverse: publicUrl without, postUrl with query
install({
    "/posts/sub-2": {"status": "published", "publicUrl": BASE},
    "/published-posts": lambda p: {"items": [{"id": "pp-2", "postUrl": QS}]},
    "/posts": lambda p: {"items": []},
    "/posts/pp-2/analytics": ANALYTICS,
})
out2 = publisher.fetch_platform_stats("instagram", "sub-2")
print("(v-b) postUrl has ?query, publicUrl without ->", out2)
assert out2 == {"play": 10, "like": 2, "comment": 1, "share": 0, "post_url": BASE}, out2

# scheme/host case & http vs https also not normalised
install({
    "/posts/sub-3": {"status": "published", "publicUrl": "https://WWW.instagram.com/reel/ABC123"},
    "/published-posts": lambda p: {"items": [{"id": "pp-3", "postUrl": BASE}]},
    "/posts": lambda p: {"items": []},
    "/posts/pp-3/analytics": ANALYTICS,
})
out3 = publisher.fetch_platform_stats("instagram", "sub-3")
print("(v-c) host case differs ->", out3)
assert out3 == {"play": 10, "like": 2, "comment": 1, "share": 0, "post_url": "https://WWW.instagram.com/reel/ABC123"}, out3

# contract: never raises, always exact key set, even on garbage bodies
install({"/posts/sub-4": {"publicUrl": BASE}, "/published-posts": lambda p: {"items": [{"id": "pp-4", "postUrl": BASE}]},
         "/posts/pp-4/analytics": {"metrics": {"viewsCount": "12", "likesCount": None, "commentsCount": "x", "sharesCount": [1]}}})
out4 = publisher.fetch_platform_stats("instagram", "sub-4")
assert out4 == {"play": 12, "like": 0, "comment": 0, "share": 0, "post_url": BASE}, out4
install({"/posts/sub-5": {"publicUrl": BASE}, "/published-posts": lambda p: {"items": [{"id": "pp-5", "postUrl": BASE}]},
         "/posts/pp-5/analytics": "not-a-dict"})
out5 = publisher.fetch_platform_stats("instagram", "sub-5")
print("(v-d) garbage analytics body ->", out5)
assert out5 == {"play": 0, "like": 0, "comment": 0, "share": 0, "post_url": BASE}, out5
# non-dict /posts/{id} body (e.g. JSON null) -> zeros, no retry (not a transient error)
install({"/posts/sub-5b": None})
out5b = publisher.fetch_platform_stats("instagram", "sub-5b")
print("(v-d2) null post body ->", out5b)
assert out5b == {"play": 0, "like": 0, "comment": 0, "share": 0, "post_url": ""}, out5b
assert len(calls) == 1, calls

# published-posts page exactly _PAGE long with no match -> continues to offset 100, 200, then stops at 300
pages = []
install({"/posts/sub-6": {"publicUrl": BASE},
         "/published-posts": lambda p: pages.append(p["offset"]) or {"items": [{"id": "n", "postUrl": "https://x/"}] * 100},
         "/posts": lambda p: {"items": []}})
publisher.fetch_platform_stats("instagram", "sub-6")
assert pages == [0, 100, 200], pages
# fallback /posts: repeated-key arrays + cursor paging stops when cursor absent
install({"/posts/sub-7": {"publicUrl": BASE}, "/published-posts": lambda p: {"items": []},
         "/posts": lambda p: {"items": [], "cursor": "c2"} if "cursor" not in p else {"items": []}})
publisher.fetch_platform_stats("instagram", "sub-7")
posts_calls = [c for c in calls if c[0] == "/posts"]
assert posts_calls[0][1]["status"] == ["published"] and posts_calls[0][1]["platform"] == ["instagram"]
assert "cursor" not in posts_calls[0][1] and posts_calls[1][1]["cursor"] == "c2" and len(posts_calls) == 2
enc = requests.Request("GET", "https://x/posts", params=posts_calls[0][1]).prepare().url
assert "status=published&platform=instagram" in enc, enc
print("(v-e) paging caps / repeated-key encoding OK:", enc.split("?")[1])

print("ALL REVIEW CORE TESTS PASSED")
