# 离线测试

不打任何真实 API（Claude / Airtable / HeyGen / Blotato 全部 mock），覆盖状态机分发、去重锁时序、webhook 游标、结构化输出请求格式、Blotato 数据回收、发布筛选公式。

```bash
pip install -r requirements.txt
PYTHONDONTWRITEBYTECODE=1 ANTHROPIC_API_KEY=x TAVILY_API_KEY=x BLOTATO_API_KEY=x \
  for t in tests/test_*.py; do python -X utf8 "$t" | tail -1; done
```

每个文件独立运行，最后一行打印 `ALL ... TESTS PASSED` 即通过。
