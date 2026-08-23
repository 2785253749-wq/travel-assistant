from html.parser import HTMLParser
from uuid import uuid4


class _Regions(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.by_id: dict[str, list[tuple[str, dict[str, str | None]]]] = {}
        self.scripts: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.by_id.setdefault(values["id"], []).append((tag, values))
        if tag == "script":
            self.scripts.append(values)


def test_home_has_core_regions(client):
    html = client.get("/").text
    document = _Regions()
    document.feed(html)

    assert 'id="auth-panel"' in html
    assert 'id="chat-panel"' in html
    assert 'id="trip-history"' in html
    assert 'aria-live="polite"' in html
    assert document.by_id["auth-panel"][0][0] == "section"
    assert document.by_id["chat-panel"][0][0] == "section"
    assert document.by_id["trip-history"][0][0] == "aside"


def test_javascript_is_external(client):
    html = client.get("/").text
    assert '<script src="/static/app.js"' in html
    assert "DEEPSEEK_API_KEY" not in client.get("/static/app.js").text


def test_home_loads_a_responsive_external_stylesheet(client):
    html = client.get("/").text
    document = _Regions()
    document.feed(html)

    assert 'name="viewport"' in html
    assert 'href="/static/styles.css"' in html
    assert any(script.get("src") == "/static/app.js" for script in document.scripts)


def test_returned_page_bootstraps_only_public_supabase_runtime_config(client, monkeypatch):
    """Dropping the runtime script must leave the production login client unconfigured."""
    from app.core.config import get_settings

    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "public-anon-placeholder")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "server-only-placeholder")
    get_settings.cache_clear()

    page = client.get("/")
    runtime = client.get("/runtime-config.js")

    assert page.status_code == 200
    assert runtime.status_code == 200
    assert runtime.headers["content-type"].startswith("application/javascript")
    assert runtime.headers["cache-control"] == "no-store"
    assert '<script src="/runtime-config.js"></script>' in page.text
    assert page.text.index('/runtime-config.js') < page.text.index('/static/app.js')
    assert '"supabaseUrl":"https://project.supabase.co/"' in runtime.text
    assert '"supabaseAnonKey":"public-anon-placeholder"' in runtime.text
    assert "server-only-placeholder" not in runtime.text
    assert "DEEPSEEK" not in runtime.text
    get_settings.cache_clear()


def test_runtime_config_exposes_configured_amap_direct_mode_credentials(client, monkeypatch):
    """The map loader receives both browser-facing direct-mode credentials."""
    from app.core.config import get_settings

    monkeypatch.setenv("AMAP_JS_KEY", "amap-browser-test-key")
    monkeypatch.setenv("AMAP_SECURITY_JS_CODE", "amap-browser-test-security-code")
    get_settings.cache_clear()

    response = client.get("/runtime-config.js")

    assert '"amapJsKey":"amap-browser-test-key"' in response.text
    assert '"amapSecurityJsCode":"amap-browser-test-security-code"' in response.text
    assert "service" not in response.text.lower()

    get_settings.cache_clear()


def test_runtime_config_uses_null_when_amap_browser_key_is_not_configured(
    client, monkeypatch, tmp_path
):
    from app.core.config import get_settings

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AMAP_JS_KEY", raising=False)
    monkeypatch.delenv("AMAP_SECURITY_JS_CODE", raising=False)
    get_settings.cache_clear()

    response = client.get("/runtime-config.js")

    assert '"amapJsKey":null' in response.text
    assert '"amapSecurityJsCode":null' in response.text

    get_settings.cache_clear()


def test_travel_community_routes_serve_dedicated_page_shells(client):
    """Task 11 replaced the temporary index shell with dedicated community pages."""
    community_note_id = uuid4()
    routes = {
        "/community": "community-main",
        "/community/notes/new": "community-editor-main",
        f"/community/notes/{community_note_id}": "community-note-main",
        "/community/mine": "community-mine-main",
        "/community/creators/voyage-alice": "community-creator-main",
        f"/community/notes/{community_note_id}/edit": "community-editor-main",
    }

    for route, main_id in routes.items():
        response = client.get(route)
        assert response.status_code == 200
        assert f'id="{main_id}"' in response.text
        assert "/static/community-client.js" in response.text
