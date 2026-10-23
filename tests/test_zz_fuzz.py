"""刁鑽用戶：每個 API 丟亂七八糟的輸入，不能 500。"""
import re

SKIP = re.compile(r"restart|shutdown|restore|trash|archive|/open$|reveal|lan/|pick-folder|browse|userscript|upload|/m/")
BODIES = [{}, {"ids": []}, {"ids": [999999]}, {"ids": ["x"]}, {"ids": [1, 1, 1], "tag": "", "value": True},
          {"tag": "   ", "ids": [1], "action": "bogus"}, {"name": "", "query": ""}, {"url": "not a url"},
          {"ids": [-1], "tag": "%", "category": "bogus", "kind": "bogus"}, [], "junk", {"bucket": "p_x", "action": "x"}]


def test_zz_writes_hold_the_lock_after_all_tests(client):
    """所有測試跑完後再檢查一次：沒包在 write_lock 裡的寫入會一直占著 SQLite（別的請求卡住，像這個 fuzz 曾經卡死）。"""
    from app import db
    bad = {sql: where for sql, where in db.UNLOCKED_WRITES}
    assert not bad, "\n\n".join(f"{k}\n{v}" for k, v in list(bad.items())[:5])


def test_no_500_on_junk(client):
    from app.server import app

    def walk(rs):
        for r in rs:
            sub = getattr(r, "routes", None) or getattr(getattr(r, "original_router", None), "routes", None)
            if sub and not getattr(r, "path", None):
                yield from walk(sub)
            else:
                yield r
    bad = []; n = 0
    for r in walk(app.routes):
        methods = getattr(r, "methods", set()) or set()
        path = getattr(r, "path", "")
        if not path.startswith("/api") or SKIP.search(path):
            continue
        p = re.sub(r"\{mid\}", "999999", path)
        p = re.sub(r"\{[^}]+\}", "doesnotexist", p)
        for m in methods & {"POST", "PUT", "DELETE", "PATCH"}:
            for b in BODIES:
                n += 1
                try:
                    res = client.request(m, p, json=b)
                    code = res.status_code
                except Exception as e:   # noqa
                    code = f"EXC {type(e).__name__}: {e}"
                if not isinstance(code, int) or code >= 500:
                    bad.append((m, p, str(b)[:40], code))
    print("CHECKED", n); assert n > 50
    assert not bad, "\n".join(map(str, bad))
