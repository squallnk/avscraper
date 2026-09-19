r"""前端是 history 模式路由，服务端必须给子路径回 index.html。

**起因**：用户打开 ``http://host:9300/records`` 看到空白页。
原来只挂了一个 ``StaticFiles(directory=dist, html=True)`` —— 它只在**目录根**
返回 index.html，``/records`` 直接 404（``{"detail":"Not Found"}``）。
点菜单进去没事（客户端路由不发请求），一旦刷新或直接开链接就白屏。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.main import mount_frontend


def _make_dist(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True, exist_ok=True)
    (dist / "index.html").write_text("<!doctype html><div id=app></div>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("不该被读到", encoding="utf-8")
    return dist


def _client(tmp_path) -> TestClient:
    app = FastAPI()

    @app.get("/api/ping")
    async def ping() -> dict[str, str]:
        return {"pong": "ok"}

    mount_frontend(app, _make_dist(tmp_path))
    return TestClient(app)


def test_root_serves_index(tmp_path):
    response = _client(tmp_path).get("/")
    assert response.status_code == 200
    assert "id=app" in response.text


def test_deep_link_falls_back_to_index(tmp_path):
    r"""这条就是那次白屏。刷新 ``/records`` 必须拿到应用本身，而不是 404。"""
    response = _client(tmp_path).get("/records")
    assert response.status_code == 200
    assert "id=app" in response.text
    assert "text/html" in response.headers["content-type"]


def test_assets_are_served_for_real(tmp_path):
    response = _client(tmp_path).get("/assets/app.js")
    assert response.status_code == 200
    assert "console.log" in response.text


def test_api_routes_still_win(tmp_path):
    assert _client(tmp_path).get("/api/ping").json() == {"pong": "ok"}


def test_unknown_api_path_stays_json(tmp_path):
    """未知的 API 路径不能被兜底吞成 index.html —— 前端要把响应当 JSON 读。"""
    response = _client(tmp_path).get("/api/nope")
    assert response.status_code == 404
    assert response.json()["detail"] == "Not Found"


def test_path_traversal_is_refused(tmp_path):
    """兜底会把 URL 拼成文件路径，不校验就是目录穿越。"""
    client = _client(tmp_path)
    for path in ("/../secret.txt", "/..%2Fsecret.txt", "/assets/../../secret.txt"):
        response = client.get(path)
        assert "不该被读到" not in response.text, path
