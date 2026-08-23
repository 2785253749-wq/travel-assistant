from __future__ import annotations

import logging
import sys
from copy import deepcopy
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


def test_public_repository_uses_migration_rpc_argument_names_and_only_internal_reads():
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


def test_review_rpcs_use_the_migration_note_id_and_decision_arguments(monkeypatch):
    class RpcCall:
        def execute(self):
            return _response({"id": str(NOTE_A)})

    class RpcClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        def rpc(self, name: str, params: dict[str, object]):
            self.calls.append((name, params))
            return RpcCall()

    client = RpcClient()
    repository = SupabaseTravelNoteRepository(client)
    monkeypatch.setattr(repository, "get_note", lambda _note_id: object())

    assert repository.approve(
        REVIEWER_A, NOTE_A, now=datetime(2026, 8, 21, 10, 0, tzinfo=UTC)
    ) is not None
    assert repository.reject(
        REVIEWER_A,
        NOTE_A,
        reason="需要补充图片说明",
        now=datetime(2026, 8, 21, 10, 0, tzinfo=UTC),
    ) is not None

    assert client.calls == [
        (
            "review_travel_note",
            {"p_note_id": str(NOTE_A), "decision": "approved", "reason": None},
        ),
        (
            "review_travel_note",
            {
                "p_note_id": str(NOTE_A),
                "decision": "rejected",
                "reason": "需要补充图片说明",
            },
        ),
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


class _StatefulQuery:
    def __init__(self, client, table_name: str) -> None:
        self._client = client
        self._table_name = table_name
        self._operation = "select"
        self._payload = None
        self._filters: list[tuple[str, object, str]] = []
        self._orders: list[tuple[str, bool]] = []

    def select(self, _columns: str):
        self._operation = "select"
        return self

    def insert(self, payload):
        self._operation = "insert"
        self._payload = payload
        return self

    def update(self, payload):
        self._operation = "update"
        self._payload = payload
        return self

    def delete(self):
        self._operation = "delete"
        return self

    def eq(self, field: str, value: object):
        self._filters.append((field, value, "eq"))
        return self

    def in_(self, field: str, values: list[object]):
        self._filters.append((field, values, "in"))
        return self

    def order(self, field: str, *, desc: bool = False):
        self._orders.append((field, desc))
        return self

    def execute(self):
        if self._operation == "insert":
            return self._client._insert(self._table_name, self._payload)
        rows = self._matching_rows()
        if self._operation == "update":
            self._client.update_payloads.append(
                (self._table_name, deepcopy(self._payload))
            )
            for row in rows:
                row.update(deepcopy(self._payload))
                if self._table_name == "travel_notes" and "updated_at" not in self._payload:
                    row["updated_at"] = "2026-08-21T10:00:00+00:00"
            return _response(deepcopy(rows))
        if self._operation == "delete":
            deleted = []
            table = self._client._table(self._table_name)
            for row in list(table):
                if self._matches(row):
                    deleted.append(table.pop(table.index(row)))
            return _response(deepcopy(deleted))
        for field, desc in reversed(self._orders):
            rows.sort(key=lambda row: row.get(field), reverse=desc)
        return _response(deepcopy(rows))

    def _matching_rows(self):
        return [row for row in self._client._table(self._table_name) if self._matches(row)]

    def _matches(self, row: dict[str, object]) -> bool:
        for field, expected, operator in self._filters:
            if operator == "eq" and row.get(field) != expected:
                return False
            if operator == "in" and row.get(field) not in expected:
                return False
        return True


class _StatefulClient:
    def __init__(self) -> None:
        self.notes: list[dict[str, object]] = []
        self.images: list[dict[str, object]] = []
        self.profiles: list[dict[str, object]] = []
        self.table_calls: list[str] = []
        self.insert_payloads: list[tuple[str, object]] = []
        self.update_payloads: list[tuple[str, object]] = []
        self.fail_image_insert = False

    def table(self, table_name: str):
        self.table_calls.append(table_name)
        return _StatefulQuery(self, table_name)

    def _table(self, table_name: str) -> list[dict[str, object]]:
        return {
            "travel_notes": self.notes,
            "travel_note_images": self.images,
            "profiles": self.profiles,
        }[table_name]

    def _insert(self, table_name: str, payload):
        self.insert_payloads.append((table_name, deepcopy(payload)))
        rows = payload if isinstance(payload, list) else [payload]
        inserted: list[dict[str, object]] = []
        for index, source in enumerate(rows):
            row = deepcopy(source)
            if table_name == "travel_notes":
                row.setdefault("id", str(uuid4()))
                row.setdefault("status", "draft")
                row.setdefault("review_reason", None)
                row.setdefault("submitted_at", None)
                row.setdefault("published_at", None)
                row.setdefault("deleted_at", None)
                row.setdefault("like_count", 0)
                row.setdefault("comment_count", 0)
                row.setdefault("created_at", datetime(2026, 8, 21, tzinfo=UTC).isoformat())
                row.setdefault("updated_at", row["created_at"])
            elif table_name == "travel_note_images":
                row.setdefault("id", str(uuid4()))
            self._table(table_name).append(row)
            inserted.append(row)
            if table_name == "travel_note_images" and self.fail_image_insert and index == 0:
                self.fail_image_insert = False
                raise RuntimeError("image insert failed after one row")
        return _response(deepcopy(inserted))


def _stateful_note(*, note_id: UUID = NOTE_A, title: str = "旧标题") -> dict[str, object]:
    timestamp = datetime(2026, 8, 21, 9, 0, tzinfo=UTC).isoformat()
    return {
        "id": str(note_id),
        "author_id": str(USER_A),
        "title": title,
        "body": "旧正文",
        "location_name": "旧地点",
        "category": "城市漫步",
        "source_trip_id": None,
        "itinerary_snapshot": {"days": 2},
        "status": "draft",
        "review_reason": None,
        "submitted_at": None,
        "published_at": None,
        "deleted_at": None,
        "like_count": 0,
        "comment_count": 0,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def _stateful_image(
    *, image_id: UUID, note_id: UUID, path: str, sort_order: int
) -> dict[str, object]:
    return {
        "id": str(image_id),
        "note_id": str(note_id),
        "owner_id": str(USER_A),
        "storage_path": path,
        "sort_order": sort_order,
        "width": 1200,
        "height": 800,
    }


def test_create_draft_persists_snapshot_and_compensates_after_partial_image_write():
    client = _StatefulClient()
    client.fail_image_insert = True
    snapshot = {"days": [{"day": 1, "places": ["大理古城"]}]}
    repository = SupabaseTravelNoteRepository(client)

    with pytest.raises(AppError) as error:
        repository.create_draft(
            USER_A,
            draft_input(),
            now=datetime(2026, 8, 21, 10, 0, tzinfo=UTC),
            itinerary_snapshot=snapshot,
        )

    assert error.value.code == "TRAVEL_NOTE_UNAVAILABLE"
    assert client.notes == []
    assert client.images == []

    client.fail_image_insert = False
    created = repository.create_draft(
        USER_A,
        draft_input(),
        now=datetime(2026, 8, 21, 10, 0, tzinfo=UTC),
        itinerary_snapshot=snapshot,
    )

    assert created.itinerary_snapshot is None
    assert "itinerary_snapshot" not in client.insert_payloads[-2][1]
    assert client.notes[0].get("itinerary_snapshot") is None


def test_replace_draft_restores_old_note_and_images_after_failed_image_write():
    client = _StatefulClient()
    client.fail_image_insert = True
    client.notes.append(_stateful_note())
    old_image = _stateful_image(
        image_id=UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
        note_id=NOTE_A,
        path=f"{USER_A}/{NOTE_A}/old.webp",
        sort_order=0,
    )
    old_image_2 = _stateful_image(
        image_id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"),
        note_id=NOTE_A,
        path=f"{USER_A}/{NOTE_A}/old-2.webp",
        sort_order=1,
    )
    client.images.extend([old_image, old_image_2])
    replacement = TravelNoteDraftInput.model_validate(
        {
            "title": "新标题",
            "body": "新正文",
            "location_name": "新地点",
            "category": "自然风光",
            "source_trip_id": None,
            "images": [
                {
                    "storage_path": f"{USER_A}/{NOTE_A}/new.webp",
                    "sort_order": 0,
                    "width": 1600,
                    "height": 1000,
                },
                {
                    "storage_path": f"{USER_A}/{NOTE_A}/new-2.webp",
                    "sort_order": 1,
                    "width": 1600,
                    "height": 1000,
                },
            ],
        }
    )

    with pytest.raises(AppError) as error:
        SupabaseTravelNoteRepository(client).replace_draft(
            USER_A,
            NOTE_A,
            replacement,
            now=datetime(2026, 8, 21, 10, 0, tzinfo=UTC),
            itinerary_snapshot={"days": 4},
        )

    assert error.value.code == "TRAVEL_NOTE_UNAVAILABLE"
    assert client.notes == [_stateful_note()]
    assert client.images == [old_image, old_image_2]
    assert all(
        "itinerary_snapshot" not in payload
        for table, payload in client.update_payloads
        if table == "travel_notes"
    )


def test_replace_draft_persists_the_new_itinerary_snapshot():
    client = _StatefulClient()
    client.notes.append(_stateful_note())
    client.images.append(
        _stateful_image(
            image_id=UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
            note_id=NOTE_A,
            path=f"{USER_A}/{NOTE_A}/old.webp",
            sort_order=0,
        )
    )
    replacement = TravelNoteDraftInput.model_validate(
        {
            "title": "新标题",
            "body": "新正文",
            "location_name": "新地点",
            "category": "自然风光",
            "source_trip_id": None,
            "images": [
                {
                    "storage_path": f"{USER_A}/{NOTE_A}/new.webp",
                    "sort_order": 0,
                    "width": 1600,
                    "height": 1000,
                }
            ],
        }
    )
    snapshot = {"days": [{"day": 1, "places": ["洱海"]}]}

    stored = SupabaseTravelNoteRepository(client).replace_draft(
        USER_A,
        NOTE_A,
        replacement,
        now=datetime(2026, 8, 21, 10, 0, tzinfo=UTC),
        itinerary_snapshot=snapshot,
    )

    assert stored is not None
    assert stored.itinerary_snapshot == {"days": 2}
    assert client.notes[0]["itinerary_snapshot"] == {"days": 2}
    assert client.update_payloads[0] == (
        "travel_notes",
        {
            "title": "新标题",
            "body": "新正文",
            "location_name": "新地点",
            "category": "自然风光",
            "source_trip_id": None,
        },
    )


def test_list_owned_batch_loads_images_and_profiles_once():
    client = _StatefulClient()
    client.notes.extend(
        [
            _stateful_note(note_id=NOTE_A, title="第一篇"),
            _stateful_note(
                note_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"), title="第二篇"
            ),
        ]
    )
    client.images.extend(
        [
            _stateful_image(
                image_id=UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
                note_id=NOTE_A,
                path=f"{USER_A}/{NOTE_A}/one.webp",
                sort_order=0,
            ),
            _stateful_image(
                image_id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"),
                note_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"),
                path=f"{USER_A}/dddddddd-dddd-dddd-dddd-dddddddddddd/two.webp",
                sort_order=0,
            ),
        ]
    )
    client.profiles.append(
        {
            "user_id": str(USER_A),
            "display_name": "Voyage Alice",
            "avatar_path": None,
            "creator_slug": "creator-alice",
        }
    )

    notes = SupabaseTravelNoteRepository(client).list_owned(USER_A)

    assert [note.title for note in notes] == ["第二篇", "第一篇"]
    assert all(len(note.images) == 1 for note in notes)
    assert all(note.author_display_name == "Voyage Alice" for note in notes)
    assert client.table_calls == ["travel_notes", "travel_note_images", "profiles"]
