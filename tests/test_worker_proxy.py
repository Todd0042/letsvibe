from fastapi.testclient import TestClient

from letsvibe.web import server as srv
from letsvibe.web import ui as ui_mod
from letsvibe.web import worker_daemon as wd


def test_worker_daemon_endpoints(monkeypatch):
    class _FakeWorker:
        current_task_info = {"id": 7, "target": "owner/repo", "status": "running"}

        def request_stop(self, task_id: int) -> bool:
            return task_id == 7

        def process_task_now(self, task_id: int) -> bool:
            return task_id == 9

    monkeypatch.setattr(wd, "_worker", _FakeWorker())
    client = TestClient(wd.worker_app)

    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/status").json() == {"worker_running": {"id": 7, "target": "owner/repo", "status": "running"}}
    assert client.post("/stop/7").json() == {"ok": True}
    assert client.post("/stop/8").json() == {"ok": False}
    assert client.post("/run-now/9").json() == {"ok": True}


def test_remote_worker_proxy(monkeypatch):
    class _Resp:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    monkeypatch.setattr(srv.httpx, "get", lambda url, timeout=1.0: _Resp(200, {"worker_running": {"id": 3}}))
    monkeypatch.setattr(srv.httpx, "post", lambda url, timeout=2.0: _Resp(200, {"ok": url.endswith("/3")}))

    proxy = srv._RemoteWorkerProxy("http://127.0.0.1:8099")
    assert proxy.current_task_info == {"id": 3}
    assert proxy.request_stop(3) is True
    assert proxy.process_task_now(4) is False

    def _boom(*_a, **_kw):
        raise RuntimeError("offline")

    monkeypatch.setattr(srv.httpx, "get", _boom)
    assert proxy.current_task_info is None


def test_templates_dir_hot_reads_changes(tmp_path, monkeypatch):
    tpl_dir = tmp_path / "templates"
    (tpl_dir / "partials").mkdir(parents=True)
    (tpl_dir / "partials" / "footer.html").write_text("<footer>{{YEAR}}-{{RUBRIC_VERSION}}</footer>", encoding="utf-8")
    dash = tpl_dir / "dashboard.html"
    dash.write_text("<html><head></head><body>v1<!--SITE_FOOTER--></body></html>", encoding="utf-8")

    monkeypatch.setattr(ui_mod, "TEMPLATES_DIR", tpl_dir)
    assert "v1" in ui_mod.render_dashboard_html()

    dash.write_text("<html><head></head><body>v2<!--SITE_FOOTER--></body></html>", encoding="utf-8")
    assert "v2" in ui_mod.render_dashboard_html()
