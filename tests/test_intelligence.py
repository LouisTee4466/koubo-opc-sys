import os, sys
sys.path.insert(0, os.getcwd())
os.environ["TAVILY_API_KEY"] = "tvly-test"

import requests
from core import intelligence

captured = []


class FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def fake_post(url, headers=None, json=None, timeout=None, **kw):
    captured.append({"url": url, "headers": headers or {}, "json": json or {}, "timeout": timeout})
    return FakeResp({
        "answer": "A" * 500,
        "results": [
            {"title": "T1", "url": "https://x/1", "content": "C" * 400, "score": 0.9},
            {"title": "T2", "url": "https://x/2", "content": "short"},
        ],
    })


requests.post = fake_post

out = intelligence.fetch_tavily_intel("ai tools")

assert len(captured) == 3, captured
for c in captured:
    assert c["url"] == "https://api.tavily.com/search", c["url"]
    assert c["headers"].get("Authorization") == "Bearer tvly-test", c["headers"]
    assert "api_key" not in c["json"], c["json"]
    assert set(c["json"]) == {"query", "search_depth", "max_results", "include_answer"}, sorted(c["json"])
    assert c["json"]["search_depth"] == "basic" and c["json"]["max_results"] == 5 and c["json"]["include_answer"] is True
    assert c["timeout"] == 30
assert [c["json"]["query"] for c in captured] == [
    "ai tools trending content this week", "ai tools viral videos", "ai tools latest trends 2026"]
print("request: header auth OK, no api_key in body; queries:", [c["json"]["query"] for c in captured])

assert len(out) == 6, out
for item in out:
    assert set(item) == {"query", "title", "url", "content", "answer_summary"}, sorted(item)
    assert "error" not in item
assert out[0] == {"query": "ai tools trending content this week", "title": "T1", "url": "https://x/1",
                  "content": "C" * 300, "answer_summary": "A" * 200}
assert out[1]["title"] == "T2" and out[1]["content"] == "short"
print("parsed shape OK:", sorted(out[0]))

# _format_tavily 消费同一份数据
from core import topic_generator
txt = topic_generator._format_tavily(out)
assert "[ai tools viral videos] T1" in txt and txt.count("\n") == 5, txt

# HTTP 错误 -> 单条 error 记录,不中断其余 query
def post_401(url, **kw):
    r = requests.Response()
    r.status_code = 401
    r.reason = "Unauthorized"
    r.url = url
    raise requests.exceptions.HTTPError("401", response=r)


requests.post = post_401
captured.clear()
out = intelligence.fetch_tavily_intel("ai tools")
assert len(out) == 3 and all("error" in o and o["query"] for o in out), out
assert topic_generator._format_tavily(out) == "(无数据)"
print("http error -> per-query error entries, no retry on 4xx")

# key 缺失 -> 不发请求,返回单条 error,_format_tavily 可消费
calls = []
requests.post = lambda *a, **kw: calls.append((a, kw)) or FakeResp({"results": []})
intelligence.TAVILY_API_KEY = ""
out = intelligence.fetch_tavily_intel("ai tools")
assert calls == [], calls
assert out == [{"query": "ai tools", "error": "TAVILY_API_KEY 未设置"}], out
assert topic_generator._format_tavily(out) == "(无数据)"
intelligence.TAVILY_API_KEY = None
assert intelligence.fetch_tavily_intel("ai tools") == out and calls == []
print("missing key -> skipped, no HTTP call:", out)

print("ALL INTELLIGENCE TESTS PASSED")
