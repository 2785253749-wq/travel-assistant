from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.core.errors import AppError
from app.travel_notes.models import TravelNoteDraftInput
from app.travel_notes.supabase_repositories import (
    COMMUNITY_MEDIA_BUCKET,
    SupabasePublicTravelNoteRepository,
    SupabaseTravelNoteMediaGateway,
    SupabaseTravelNoteRepository,
    create_public_travel_note_repository,
    create_travel_note_media_gateway,
    create_user_scoped_travel_note_repository,
)


USER_A = UUID("11111111-1111-1111-1111-111111111111")
NOTE_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
REVIEWER_A = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def draft_input() -> TravelNoteDraftInput:
    return TravelNoteDraftInput.model_validate(
        {
            "title": "大理四天三夜",
            "body": "苍山脚下散步，傍晚去洱海看日落。",
            "location_name": "云南·大理",
            "category": "城市漫步",
            "source_trip_id": None,
            "images": [
                {
                    "storage_path": f"{USER_A}/{NOTE_A}/cover.webp",
                    "sort_order": 0,
                    "width": 1440,
                    "height": 1920,
                }
            ],
        }
    )


def _response(data):
    return type("Response", (), {"data": data})()


class _FakePostgrest:
    def __init__(self) -> None:
        self.tokens: list[str] = []

    def auth(self, token: str) -> None:
        self.tokens.append(token)


class _FakeStorageBucket:
    def __init__(self, signed_base: str) -> None:
        self.signed_base = signed_base
        self.calls: list[tuple[list[str], int]] = []

    def create_signed_urls(self, paths: list[str], expires_in: int):
        self.calls.append((list(paths), expires_in))
        return [
            {"signedURL": f"{self.signed_base}/{path}"}
            for path in paths
        ]


class _FakeStorage:
    def __init__(self) -> None:
        self.bucket_names: list[str] = []
        self.bucket = _FakeStorageBucket("https://signed.example.test")

    def from_(self, bucket_name: str) -> _FakeStorageBucket:
        self.bucket_names.append(bucket_name)
        return self.bucket


class _FactoryClient:
    def __init__(self) -> None:
        self.postgrest = _FakePostgrest()
        self.storage = _FakeStorage()


def test_user_scoped_repository_applies_the_verified_bearer_token(monkeypatch):
    fake = _FactoryClient()
    monkeypatch.setitem(
        sys.modules,
        "supabase",
        SimpleNamespace(create_client=lambda _url, _key: fake),
    )

    repository = create_user_scoped_travel_note_repository(
        "https://project.test", "anon-key", "token-a"
    )

    assert isinstance(repository, SupabaseTravelNoteRepository)
    assert fake.postgrest.tokens == ["token-a"]


def test_public_repository_and_media_gateway_use_service_key_clients(monkeypatch):
    public_client = _FactoryClient()
    media_client = _FactoryClient()
    clients = [public_client, media_client]
    monkeypatch.setitem(
        sys.modules,
        "supabase",
        SimpleNamespace(create_client=lambda _url, _key: clients.pop(0)),
    )

    repository = create_public_travel_note_repository(
        "https://project.test", "service-key"
    )
    gateway = create_travel_note_media_gateway(
        "https://project.test", "service-key"
    )

    assert isinstance(repository, SupabasePublicTravelNoteRepository)
    assert isinstance(gateway, SupabaseTravelNoteMediaGateway)
    assert public_client.postgrest.tokens == []
    assert media_client.postgrest.tokens == []


def test_public_repository_uses_only_internal_public_read_rpcs():
    published_at = datetime(2026, 8, 21, 9, 30, tzinfo=UTC).isoformat()
    detail_note_id = str(NOTE_A)
    list_row = {
        "id": detail_note_id,
        "creator_slug": "creator-dali",
        "author_display_name": "Voyage Alice",
        "author_avatar_path": f"{USER_A}/avatars/avatar.webp",
        "title": "大理清晨",
        "location_name": "云南·大理",
        "category": "城市漫步",
        "cover_storage_path": f"{USER_A}/{NOTE_A}/cover.webp",
        "published_at": published_at,
        "like_count": 12,
        "comment_count": 3,
    }
    detail_row = {
        "id": detail_note_id,
        "creator_slug": "creator-dali",
        "author_display_name": "Voyage Alice",
        "author_avatar_path": f"{USER_A}/avatars/avatar.webp",
        "title": "大理清晨",
        "body": "苍山脚下散步，傍晚去洱海看日落。",
        "location_name": "云南·大理",
        "category": "城市漫步",
        "itinerary_snapshot": {"days": 4},
        "published_at": published_at,
        "like_count": 12,
        "comment_count": 3,
        "image_manifest": [
            {
                "storage_path": f"{USER_A}/{NOTE_A}/cover.webp",
                "width": 1440,
                "height": 1920,
                "sort_order": 0,
            },
            {
                "storage_path": f"{USER_A}/{NOTE_A}/detail.webp",
                "width": 1440,
                "height": 1920,
                "sort_order": 1,
            },
        ],
    }

    class RpcCall:
        def __init__(self, data):
            self._data = data

        def execute(self):
            return _response(self._data)

    class RpcOnlyClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        def rpc(self, name: str, params: dict[str, object]):
            self.calls.append((name, params))
            if name == "list_public_travel_notes_internal":
                return RpcCall([list_row])
            if name == "get_public_travel_note_internal":
                return RpcCall([detail_row])
            raise AssertionError(f"unexpected RPC {name}")

        def table(self, _name: str):
            raise AssertionError("public travel note reads must stay on the RPC allow-list")

    repository = SupabasePublicTravelNoteRepository(RpcOnlyClient())

    listed = repository.list_public(None, 20, category=None, search_query=None)
    detail = repository.get_public(NOTE_A)

    assert [note.id for note in listed] == [NOTE_A]
    assert listed[0].images[0].storage_path == f"{USER_A}/{NOTE_A}/cover.webp"
    assert detail is not None
    assert [image.sort_order for image in detail.images] == [0, 1]
    assert repository._client.calls == [  # type: ignore[attr-defined]
        (
            "list_public_travel_notes_internal",
            {
                "cursor_published_at": None,
                "cursor_id": None,
                "page_size": 20,
                "category_filter": None,
                "search_query": None,
            },
        ),
        ("get_public_travel_note_internal", {"p_note_id": detail_note_id}),
    ]


@pytest.mark.parametrize(
    ("code", "message", "expected"),
    [
        ("P0002", "travel note not found", "TRAVEL_NOTE_NOT_FOUND"),
        ("P0001", "travel note is not submittable", "TRAVEL_NOTE_INVALID_STATE"),
        (
            "P0001",
            "travel note requires one to nine ordered images",
            "TRAVEL_NOTE_VALIDATION_FAILED",
        ),
    ],
)
def test_private_repository_maps_database_errors_to_stable_domain_codes(
    code: str, message: str, expected: str
):
    class FakeDatabaseError(Exception):
        def __init__(self) -> None:
            super().__init__(message)
            self.code = code

    class FailingRpc:
        def execute(self):
            raise FakeDatabaseError()

    class Client:
        def rpc(self, name: str, params: dict[str, object]):
            assert name == "submit_travel_note"
            assert params == {"p_note_id": str(NOTE_A)}
            return FailingRpc()

        def table(self, _name: str):
            raise AssertionError("submit should use the RPC path")

    repository = SupabaseTravelNoteRepository(Client())

    with pytest.raises(AppError) as error:
        repository.submit(USER_A, NOTE_A, now=datetime(2026, 8, 21, 10, 0, tzinfo=UTC))

    assert error.value.code == expected


def test_private_repository_hides_raw_identifiers_from_database_logs(caplog):
    row = {
        "id": str(NOTE_A),
        "author_id": str(USER_A),
        "title": "大理四天三夜",
        "body": "苍山脚下散步，傍晚去洱海看日落。",
        "location_name": "云南·大理",
        "category": "城市漫步",
        "status": "draft",
        "review_reason": None,
        "source_trip_id": None,
        "itinerary_snapshot": None,
        "created_at": datetime(2026, 8, 21, 9, 0, tzinfo=UTC).isoformat(),
        "updated_at": datetime(2026, 8, 21, 9, 0, tzinfo=UTC).isoformat(),
        "submitted_at": None,
        "published_at": None,
        "deleted_at": None,
        "like_count": 0,
        "comment_count": 0,
    }

    class NoteQuery:
        def select(self, columns: str):
            assert columns == "*"
            return self

        def eq(self, field: str, value: str):
            assert (field, value) in {
                ("id", str(NOTE_A)),
                ("author_id", str(USER_A)),
            }
            return self

        def execute(self):
            return _response([row])

    class ImageQuery:
        def select(self, columns: str):
            assert columns == "id, note_id, owner_id, storage_path, sort_order, width, height"
            return self

        def eq(self, field: str, value: str):
            assert (field, value) == ("note_id", str(NOTE_A))
            return self

        def order(self, field: str, *, desc: bool = False):
            assert field == "sort_order"
            assert desc is False
            return self

        def execute(self):
            return _response(
                [
                    {
                        "id": str(uuid4()),
                        "note_id": str(NOTE_A),
                        "owner_id": str(USER_A),
                        "storage_path": f"{USER_A}/{NOTE_A}/cover.webp",
                        "sort_order": 0,
                        "width": 1440,
                        "height": 1920,
                    }
                ]
            )

    class ProfileQuery:
        def select(self, columns: str):
            assert columns == "display_name, avatar_path, creator_slug"
            return self

        def eq(self, field: str, value: str):
            assert field == "user_id"
            assert value == str(USER_A)
            return self

        def execute(self):
            return _response(
                [
                    {
                        "display_name": " Voyage Alice ",
                        "avatar_path": f"{USER_A}/avatars/avatar.webp",
                        "creator_slug": "creator-dali",
                    }
                ]
            )

    class Client:
        def table(self, name: str):
            if name == "travel_notes":
                return NoteQuery()
            if name == "travel_note_images":
                return ImageQuery()
            if name == "profiles":
                return ProfileQuery()
            raise AssertionError(f"unexpected table {name}")

        def rpc(self, _name: str, _params: dict[str, object]):
            raise AssertionError("owned travel note reads should not use RPCs")

    with caplog.at_level(logging.INFO, logger="app.database"):
        stored = SupabaseTravelNoteRepository(Client()).get_owned(USER_A, NOTE_A)

    assert stored is not None
    record = next(record for record in caplog.records if record.message == "database_result")
    assert record.subject.startswith("user-digest:")
    assert str(USER_A) not in caplog.text
    assert str(NOTE_A) not in caplog.text


def test_media_gateway_uses_the_private_community_bucket():
    client = _FactoryClient()
    gateway = SupabaseTravelNoteMediaGateway(client)

    signed_urls = gateway.sign_paths(
        [f"{USER_A}/{NOTE_A}/cover.webp", f"{USER_A}/{NOTE_A}/detail.webp"]
    )

    assert client.storage.bucket_names == [COMMUNITY_MEDIA_BUCKET]
    assert client.storage.bucket.calls == [
        (
            [f"{USER_A}/{NOTE_A}/cover.webp", f"{USER_A}/{NOTE_A}/detail.webp"],
            3600,
        )
    ]
    assert signed_urls == [
        f"https://signed.example.test/{USER_A}/{NOTE_A}/cover.webp",
        f"https://signed.example.test/{USER_A}/{NOTE_A}/detail.webp",
    ]
