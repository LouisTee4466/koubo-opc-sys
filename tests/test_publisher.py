import os, sys
from urllib.parse import urlparse

sys.path.insert(0, os.getcwd())
os.environ.setdefault("BLOTATO_API_KEY", "x")

from core import publisher, retry as retry_mod
import requests

retry_mod.time.sleep = lambda s: None  # 不等退避


class FakeResp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code}", response=self)


calls = []
_MISSING = object()  # 路由缺失 = 404;显式 None = JSON null body


def install(routes):
    """routes: {(path, frozenset(params.items()) 或 path): body | int status | callable(params)}"""
    def fake_get(url, headers=None, params=None, timeout=None):
        assert headers["blotato-api-key"] == "x"
        path = urlparse(url).path.removeprefix("/v2")
        calls.append((path, dict(params or {})))
        handler = routes.get(path, _MISSING)
        if handler is _MISSING:
            return FakeResp(404, {"message": "not found"})
        if callable(handler):
            handler = handler(params or {})
        if isinstance(handler, int):
            return FakeResp(handler, {})
        return FakeResp(200, handler)
    publisher.requests.get = fake_get
    calls.clear()


URL = "https://www.instagram.com/reel/ABC123/"

# ---------- 1. 正常路径:published-posts 直接命中 ----------
install({
    "/posts/sub-ig": {"postSubmissionId": "sub-ig", "status": "published", "publicUrl": URL},
    "/published-posts": lambda p: {"items": [
        {"id": "pp-other", "postUrl": "https://www.instagram.com/reel/XYZ/", "platform": "instagram"},
        {"id": "pp-1", "postUrl": URL, "platform": "instagram",
         "latestMetrics": {"fetchedAt": "t", "metrics": {"viewsCount": 1}}},
    ]},
    "/posts/pp-1/analytics": {"metrics": {"viewsCount": 1200, "likesCount": 88, "commentsCount": 7,
                                          "sharesCount": 3, "reachCount": 900}, "history": []},
})
out = publisher.fetch_platform_stats("instagram", "sub-ig")
assert out == {"play": 1200, "like": 88, "comment": 7, "share": 3, "post_url": URL}, out
assert [c[0] for c in calls] == ["/posts/sub-ig", "/published-posts", "/posts/pp-1/analytics"], calls
assert calls[1][1]["platform"] == "instagram" and calls[1][1]["offset"] == 0
print("happy path OK:", out)

# ---------- 2. 回退路径:published-posts 翻 3 页没命中 → posts 列表 state.postUrl 命中;FB reactions 求和;无 sharesCount ----------
FB_URL = "https://www.facebook.com/reel/999"


def pp_pages(p):
    # 第 0/100/200 页都满 100 条且不命中,应在 300 条处停下
    return {"items": [{"id": f"pp-{p['offset']}-{i}", "postUrl": f"https://www.facebook.com/x/{p['offset']}-{i}"}
                      for i in range(100)]}


def posts_list(p):
    # Blotato 数组参数是重复 key:status=published&platform=facebook(不是 status[]=)
    assert p["status"] == ["published"] and p["platform"] == ["facebook"], p
    wire = requests.Request("GET", "https://x/v2/posts", params=p).prepare().url
    assert "status=published" in wire and "platform=facebook" in wire and "%5B%5D" not in wire, wire
    assert p["since"].endswith("Z") and "cursor" not in p, p
    return {"items": [
        {"id": "p-sched", "platform": "facebook", "state": {"type": "scheduled"}},
        {"id": "p-42", "platform": "facebook", "state": {"type": "published", "postUrl": FB_URL + "/"}},
    ]}


install({
    "/posts/sub-fb": {"postSubmissionId": "sub-fb", "status": "published", "publicUrl": FB_URL},
    "/published-posts": pp_pages,
    "/posts": posts_list,
    "/posts/p-42/analytics": {"metrics": {"viewsCount": "350", "commentsCount": 2,
                                          "facebookPostReactionsByType": {"like": 10, "love": 4, "haha": 1}}},
})
out = publisher.fetch_platform_stats("facebook", "sub-fb")
assert out == {"play": 350, "like": 15, "comment": 2, "share": 0, "post_url": FB_URL}, out
paths = [c[0] for c in calls]
assert paths == ["/posts/sub-fb", "/published-posts", "/published-posts", "/published-posts", "/posts", "/posts/p-42/analytics"], paths
assert [c[1]["offset"] for c in calls[1:4]] == [0, 100, 200]
print("fallback path OK:", out)

# ---------- 3. 回退分页:posts 响应带 cursor 翻页(末页无 cursor);没有 viewsCount 时 play 退到 reachCount ----------
def posts_cursor(p):
    if "cursor" not in p:
        return {"items": [{"id": "p-1", "state": {"type": "published", "postUrl": "https://fb.com/other"}}], "cursor": "c2"}
    assert p["cursor"] == "c2" and p["status"] == ["published"], p
    return {"items": [{"id": "p-2", "state": {"type": "published", "postUrl": FB_URL}}]}


install({
    "/posts/sub-fb": {"status": "published", "publicUrl": FB_URL},
    "/published-posts": {"items": []},
    "/posts": posts_cursor,
    "/posts/p-2/analytics": {"metrics": {"reachCount": 5, "likesCount": 1, "commentsCount": 0, "sharesCount": 0}},
})
out = publisher.fetch_platform_stats("facebook", "sub-fb")
assert out == {"play": 5, "like": 1, "comment": 0, "share": 0, "post_url": FB_URL}, out
assert [c[0] for c in calls].count("/posts") == 2
print("cursor paging + reachCount fallback OK")

# viewsCount 存在(哪怕是 0)时优先于 reachCount
assert publisher._map_metrics("instagram", {"viewsCount": 0, "reachCount": 9})["play"] == 0
assert publisher._map_metrics("instagram", {"reachCount": 9})["play"] == 9

# ---------- 4. 全部失败:submission 404 → 全 0,post_url 空 ----------
install({})
out = publisher.fetch_platform_stats("instagram", "sub-missing")
assert out == {"play": 0, "like": 0, "comment": 0, "share": 0, "post_url": ""}, out
assert len(calls) == 1, "404 不该重试"
print("submission 404 -> zeros OK")

# ---------- 5. 仍在发布中,没有 publicUrl → 全 0 ----------
install({"/posts/sub-ip": {"postSubmissionId": "sub-ip", "status": "in-progress"}})
out = publisher.fetch_platform_stats("instagram", "sub-ip")
assert out == {"play": 0, "like": 0, "comment": 0, "share": 0, "post_url": ""}, out
print("in-progress -> zeros OK")

# ---------- 6. 两条路都找不到 id → 全 0 但保留 post_url ----------
install({
    "/posts/sub-ig": {"status": "published", "publicUrl": URL},
    "/published-posts": {"items": []},
    "/posts": {"items": []},
})
out = publisher.fetch_platform_stats("instagram", "sub-ig")
assert out == {"play": 0, "like": 0, "comment": 0, "share": 0, "post_url": URL}, out
assert "analytics" not in "".join(c[0] for c in calls)
print("no match -> zeros with post_url OK")

# ---------- 7. published-posts 挂了(500,重试后仍挂)→ 回退 posts 仍能拿到;analytics 挂 → 全 0 ----------
install({
    "/posts/sub-ig": {"status": "published", "publicUrl": URL},
    "/published-posts": 500,
    "/posts": {"items": [{"id": "p-9", "state": {"type": "published", "postUrl": URL}}]},
    "/posts/p-9/analytics": 503,
})
out = publisher.fetch_platform_stats("instagram", "sub-ig")
assert out == {"play": 0, "like": 0, "comment": 0, "share": 0, "post_url": URL}, out
paths = [c[0] for c in calls]
assert paths.count("/published-posts") == 4 and paths.count("/posts/p-9/analytics") == 4, "5xx 应重试 4 次"
assert paths.count("/posts") == 1
print("5xx retry + degrade OK")

# ---------- 8. 真的抛异常也吞掉(网络错误) ----------
def boom(*a, **k):
    raise requests.exceptions.ConnectionError("down")


publisher.requests.get = boom
out = publisher.fetch_platform_stats("facebook", "sub-x")
assert out == {"play": 0, "like": 0, "comment": 0, "share": 0, "post_url": ""}, out
print("network error -> zeros OK")

# ---------- 9. URL 归一化:?utm / #fragment / host 大小写 / 尾斜杠 都应命中,path 不同不命中 ----------
assert publisher._same_url(URL + "?utm_source=ig_web_copy_link&igsh=xyz", URL)
assert publisher._same_url("https://WWW.Instagram.com/reel/ABC123#x", "https://www.instagram.com/reel/ABC123/")
assert publisher._same_url("  " + URL + "  ", URL.rstrip("/"))
assert not publisher._same_url("http://www.instagram.com/reel/ABC123", URL), "scheme 不同不算同一条"
assert not publisher._same_url("https://www.instagram.com/reel/abc123", URL), "path 大小写敏感"
assert not publisher._same_url("", URL) and not publisher._same_url(None, URL) and not publisher._same_url(123, URL)

install({
    "/posts/sub-ig": {"status": "published", "publicUrl": URL + "?utm_source=ig_web_copy_link"},
    "/published-posts": {"items": [{"id": "pp-q", "postUrl": "https://WWW.instagram.com/reel/ABC123"}]},
    "/posts/pp-q/analytics": {"metrics": {"viewsCount": 7, "likesCount": 1, "commentsCount": 0, "sharesCount": 0}},
})
out = publisher.fetch_platform_stats("instagram", "sub-ig")
assert out == {"play": 7, "like": 1, "comment": 0, "share": 0, "post_url": URL + "?utm_source=ig_web_copy_link"}, out
print("url normalisation OK")

# ---------- 10. 200 但 body 形状不对,一律不抛,全 0 ----------
ZERO = {"play": 0, "like": 0, "comment": 0, "share": 0, "post_url": ""}
for bad in (None, "str", [1], 3):
    install({"/posts/sub-bad": bad})
    assert publisher.fetch_platform_stats("instagram", "sub-bad") == ZERO, bad
    assert len(calls) == 1, "非 dict 响应不是瞬时错误,不该重试"
install({"/posts/sub-ig": {"status": "published", "publicUrl": 42}})
assert publisher.fetch_platform_stats("instagram", "sub-ig") == ZERO

for bad in ("not-a-dict", None, [], {"metrics": "x"}, {"metrics": None}, {}):
    install({
        "/posts/sub-ig": {"status": "published", "publicUrl": URL},
        "/published-posts": {"items": [{"id": "pp-1", "postUrl": URL}]},
        "/posts/pp-1/analytics": bad,
    })
    assert publisher.fetch_platform_stats("instagram", "sub-ig") == {**ZERO, "post_url": URL}, bad

# 列表端点 body/items/item/state 畸形 → 跳过或回退,不抛
install({
    "/posts/sub-ig": {"status": "published", "publicUrl": URL},
    "/published-posts": [1, 2],
    "/posts": {"items": [None, "s", {"id": "x", "state": "bad"}, {"id": "p-ok", "state": {"postUrl": URL}}]},
    "/posts/p-ok/analytics": {"metrics": {"viewsCount": 3}},
})
out = publisher.fetch_platform_stats("instagram", "sub-ig")
assert out == {"play": 3, "like": 0, "comment": 0, "share": 0, "post_url": URL}, out
install({
    "/posts/sub-ig": {"status": "published", "publicUrl": URL},
    "/published-posts": {"items": "nope"},
    "/posts": {"items": {"a": 1}},
})
assert publisher.fetch_platform_stats("instagram", "sub-ig") == {**ZERO, "post_url": URL}
print("garbage bodies -> zeros, never raise OK")

print("ALL PUBLISHER TESTS PASSED")
