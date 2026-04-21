from pathlib import Path

import pytest


class TestTrustedProxyDashboard:
    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch, tmp_path):
        try:
            from fastapi import FastAPI
            from starlette.testclient import TestClient
        except ImportError:
            pytest.skip("fastapi/starlette not installed")

        import hermes_cli.web_server as web_server

        web_dist = tmp_path / "web_dist"
        assets_dir = web_dist / "assets"
        assets_dir.mkdir(parents=True)
        (web_dist / "index.html").write_text(
            "<html><head><title>Hermes</title></head><body>dashboard</body></html>",
            encoding="utf-8",
        )
        (assets_dir / "app.js").write_text("console.log('ok');", encoding="utf-8")

        monkeypatch.setattr(web_server, "WEB_DIST", web_dist)
        monkeypatch.setenv("HERMES_TRUSTED_PROXY_HEADER", "X-Hermes-Proxy-Auth")
        monkeypatch.setenv("HERMES_TRUSTED_PROXY_VALUE", "proxy-secret")

        app = FastAPI()
        web_server.mount_spa(app)
        self.client = TestClient(app)

    def test_untrusted_dashboard_request_does_not_receive_session_token(self):
        response = self.client.get("/", follow_redirects=False)

        assert response.status_code == 401
        assert "__HERMES_SESSION_TOKEN__" not in response.text

    def test_trusted_dashboard_request_receives_session_token(self):
        response = self.client.get(
            "/",
            headers={"X-Hermes-Proxy-Auth": "proxy-secret"},
            follow_redirects=False,
        )

        assert response.status_code == 200
        assert "__HERMES_SESSION_TOKEN__" in response.text

    def test_untrusted_spa_fallback_route_does_not_receive_session_token(self):
        response = self.client.get("/sessions/123", follow_redirects=False)

        assert response.status_code == 401
        assert "__HERMES_SESSION_TOKEN__" not in response.text

    def test_static_assets_still_serve_without_proxy_header(self):
        response = self.client.get("/assets/app.js", follow_redirects=False)

        assert response.status_code == 200
        assert response.text == "console.log('ok');"
