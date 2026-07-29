from html.parser import HTMLParser


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
