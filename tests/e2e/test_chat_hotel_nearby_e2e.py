from __future__ import annotations

from collections.abc import Iterator
import os

import httpx
import pytest
from fastapi.testclient import TestClient

from app.composition import get_hotel_nearby_application
from app.core.config import get_settings
from app.main import app
from app.schemas import ChatResponse


_BAIDU_HOST = "api.map.baidu.com"


def _require_live_e2e() -> None:
    if os.getenv("RUN_CHAT_HOTEL_NEARBY_E2E") != "1":
        pytest.skip(
            "真实 Chat Hotel Nearby E2E 未启用：请设置 RUN_CHAT_HOTEL_NEARBY_E2E=1"
        )

    get_settings.cache_clear()
    settings = get_settings()
    if settings.baidu_map_ak is None or not settings.baidu_map_ak.get_secret_value().strip():
        pytest.skip("真实 Chat Hotel Nearby E2E 未执行，因为 BAIDU_MAP_AK 未配置")


def _ensure_baidu_direct_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    inherited_values = [
        os.getenv("NO_PROXY", ""),
        os.getenv("no_proxy", ""),
    ]
    entries = [
        entry.strip()
        for value in inherited_values
        for entry in value.split(",")
        if entry.strip()
    ]
    normalized_entries = {
        entry.lower().lstrip(".").split(":", 1)[0] for entry in entries
    }
    if _BAIDU_HOST not in normalized_entries:
        entries.append(_BAIDU_HOST)
    updated_no_proxy = ",".join(dict.fromkeys(entries))
    monkeypatch.setenv("NO_PROXY", updated_no_proxy)
    monkeypatch.setenv("no_proxy", updated_no_proxy)


@pytest.fixture(scope="module")
def live_chat_client() -> Iterator[tuple[TestClient, list[tuple[str, str]]]]:
    _require_live_e2e()
    monkeypatch = pytest.MonkeyPatch()
    _ensure_baidu_direct_connection(monkeypatch)
    get_settings.cache_clear()
    get_hotel_nearby_application.cache_clear()
    app.dependency_overrides.clear()
    request_paths: list[tuple[str, str]] = []
    original_get = httpx.Client.get

    def recording_get(
        client: httpx.Client,
        url: httpx.URL | str,
        *args: object,
        **kwargs: object,
    ) -> httpx.Response:
        parsed = httpx.URL(url)
        request_paths.append((parsed.host or "", parsed.path))
        return original_get(client, url, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "get", recording_get)
    application = get_hotel_nearby_application()
    try:
        with TestClient(app) as client:
            yield client, request_paths
    finally:
        app.dependency_overrides.clear()
        application._location_service._provider._client.close()
        application._hotel_service._provider._client.close()
        get_hotel_nearby_application.cache_clear()
        get_settings.cache_clear()
        monkeypatch.undo()


def test_chat_hotel_nearby_uses_real_http_chain(
    live_chat_client: tuple[TestClient, list[tuple[str, str]]],
) -> None:
    client, request_paths = live_chat_client

    response = client.post(
        "/api/chat",
        json={
            "message": "帮我找厦门大学附近的酒店",
            "thread_id": "chat-hotel-nearby-e2e",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    ChatResponse.model_validate(payload)
    assert payload["stage"] == "collecting"
    assert payload.get("error_code") is None
    assert "厦门大学" in payload["reply"]
    assert "暂不可用" not in payload["reply"]
    assert request_paths == [
        (_BAIDU_HOST, "/place/v3/region"),
        (_BAIDU_HOST, "/place/v3/around"),
    ]

    print(
        "chat_e2e "
        f"status={response.status_code} "
        f"stage={payload['stage']} "
        f"error_code={payload.get('error_code')} "
        f"reply_summary={payload['reply'].splitlines()[0]} "
        f"baidu_request_count={len(request_paths)}"
    )
