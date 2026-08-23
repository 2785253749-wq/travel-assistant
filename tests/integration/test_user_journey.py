from html.parser import HTMLParser

import pytest

from app.main import app


@pytest.fixture
def isolated_development_state():
    """Task 14: this route-contract test must not inherit another test's post."""
    from app import composition

    dependencies = (
        composition.get_development_repository,
        composition.get_development_profile_repository,
        composition.get_development_profile_module,
        composition.get_development_community_repository,
        composition.get_development_community_module,
        composition.get_public_community_repository,
    )
    for dependency in dependencies:
        dependency.cache_clear()
    try:
        yield
    finally:
        for dependency in dependencies:
            dependency.cache_clear()


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


def test_profile_and_community_routes_are_registered_without_losing_existing_routes(
    client, isolated_development_state
):
    del isolated_development_state
    assert str(app.url_path_for("home")) == "/"
    assert str(app.url_path_for("auth_page")) == "/auth"
    assert str(app.url_path_for("profile_page")) == "/profile"
    assert str(app.url_path_for("get_profile")) == "/api/profile"
    assert str(app.url_path_for("list_community_posts")) == "/api/community/posts"
    assert (
        str(
            app.url_path_for(
                "get_community_post",
                post_id="00000000-0000-0000-0000-000000000000",
            )
        )
        == "/api/community/posts/00000000-0000-0000-0000-000000000000"
    )

    profile = client.get("/api/profile")
    listing = client.get("/api/community/posts")
    detail = client.get("/api/community/posts/00000000-0000-0000-0000-000000000000")

    assert profile.status_code == 401
    assert profile.json()["detail"]["code"] == "AUTH_REQUIRED"
    assert listing.status_code == 200
    assert listing.json() == {"items": [], "next_cursor": None}
    assert detail.status_code == 404
    assert detail.json()["detail"]["code"] == "COMMUNITY_POST_NOT_FOUND"
