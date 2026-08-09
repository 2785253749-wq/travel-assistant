from html.parser import HTMLParser


class _JourneyMarkup(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.elements: dict[str, tuple[str, dict[str, str | None]]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.elements[values["id"]] = (tag, values)


def _home(client) -> _JourneyMarkup:
    document = _JourneyMarkup()
    document.feed(client.get("/").text)
    return document


def test_signed_out_user_can_reach_login_and_anonymous_planning_controls(client):
    document = _home(client)

    assert document.elements["auth-panel"][1]["aria-label"] == "账户"
    assert document.elements["email"][0] == "input"
    assert document.elements["password"][1]["type"] == "password"
    assert document.elements["sign-in-button"][0] == "button"
    assert document.elements["chat-form"][0] == "form"
    assert document.elements["message-input"][1]["required"] is None
    assert document.elements["send-button"][0] == "button"


def test_profile_share_and_history_controls_are_labeled_and_private_by_default(client):
    document = _home(client)

    assert document.elements["profile-confirmation"][1]["hidden"] is None
    assert document.elements["confirm-profile-button"][0] == "button"
    assert document.elements["trip-history"][1]["hidden"] is None
    assert document.elements["share-dialog"][0] == "dialog"
    assert document.elements["share-dialog"][1]["aria-labelledby"] == "share-title"
    assert document.elements["status-message"][1]["aria-live"] == "polite"


def test_public_share_route_is_still_available_without_private_credentials(client):
    response = client.post("/api/shared/resolve", json={"token": "not-a-valid-token"})
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "SHARE_NOT_FOUND"
