from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
from uuid import uuid4

from app.travel_notes.media import (
    CommunityMediaGateway,
    CommunityMediaDeletionError,
    CommunityMediaSigningPayloadError,
    CommunityMediaStorageError,
    InMemoryCommunityMediaCleanupQueue,
    run_cleanup_batch,
    sanitize_cleanup_error,
)


class _FakeStorageBucket:
    def __init__(self, signed: dict[str, str]) -> None:
        self._signed = signed
        self.calls: list[tuple[list[str], int]] = []

    def create_signed_urls(self, paths: list[str], expires_in: int):
        self.calls.append((list(paths), expires_in))
        return [
            {
                "path": path,
                "signedURL": self._signed[path],
            }
            for path in paths
        ]


class _FakeStorage:
    def __init__(self, signed: dict[str, str]) -> None:
        self.bucket_names: list[str] = []
        self.bucket = _FakeStorageBucket(signed)

    def from_(self, bucket_name: str) -> _FakeStorageBucket:
        self.bucket_names.append(bucket_name)
        return self.bucket


class _FakeObjectStore:
    def __init__(self, *, failing_paths: set[str] | None = None) -> None:
        self.failing_paths = failing_paths or set()
        self.removed: list[list[str]] = []

    def remove_paths(self, paths: list[str]) -> None:
        self.removed.append(list(paths))
        for path in paths:
            if path in self.failing_paths:
                raise RuntimeError(
                    f"delete failed for https://storage.example.test/object/{path}"
                )


class _FailingSignedPayloadBucket:
    def create_signed_urls(self, paths: list[str], expires_in: int):
        del paths, expires_in
        return {"data": [{"path": "avatar/plain-key.webp"}]}


class _RaisingSignedUrlBucket:
    def create_signed_urls(self, paths: list[str], expires_in: int):
        del paths, expires_in
        raise RuntimeError("storage backend failed for avatar/plain-key.webp")


class _BucketBackedStorage:
    def __init__(self, bucket) -> None:
        self.bucket = bucket

    def from_(self, bucket_name: str):
        del bucket_name
        return self.bucket


def test_sign_paths_preserves_order_and_returns_signed_urls_only():
    storage = _FakeStorage(
        {
            "a.webp": "https://signed.example.test/a.webp",
            "b.webp": "https://signed.example.test/b.webp",
        }
    )
    gateway = CommunityMediaGateway(storage, bucket="community-media")

    signed_urls = gateway.sign_paths(["a.webp", "b.webp"], expires_in=3600)

    assert storage.bucket_names == ["community-media"]
    assert storage.bucket.calls == [(["a.webp", "b.webp"], 3600)]
    assert signed_urls == [
        "https://signed.example.test/a.webp",
        "https://signed.example.test/b.webp",
    ]


def test_sign_paths_raises_typed_payload_error_for_malformed_signed_url_payload():
    gateway = CommunityMediaGateway(
        _BucketBackedStorage(_FailingSignedPayloadBucket()),
        bucket="community-media",
    )

    try:
        gateway.sign_paths(["avatar/plain-key.webp"], expires_in=3600)
    except CommunityMediaSigningPayloadError as error:
        assert error.code == "COMMUNITY_MEDIA_SIGNING_PAYLOAD_INVALID"
    else:  # pragma: no cover - enforced by assertion
        raise AssertionError("expected CommunityMediaSigningPayloadError")


def test_sign_paths_raises_typed_storage_error_when_storage_client_fails():
    gateway = CommunityMediaGateway(
        _BucketBackedStorage(_RaisingSignedUrlBucket()),
        bucket="community-media",
    )

    try:
        gateway.sign_paths(["avatar/plain-key.webp"], expires_in=3600)
    except CommunityMediaStorageError as error:
        assert error.code == "COMMUNITY_MEDIA_SIGNING_FAILED"
    else:  # pragma: no cover - enforced by assertion
        raise AssertionError("expected CommunityMediaStorageError")


def test_run_cleanup_batch_marks_successes_requeues_failures_and_skips_retry_exhausted_jobs(
    caplog,
):
    queue = InMemoryCommunityMediaCleanupQueue()
    removable_path = "11111111-1111-1111-1111-111111111111/avatar/ok.webp"
    failing_path = "11111111-1111-1111-1111-111111111111/avatar/fail.webp"
    skipped_path = "11111111-1111-1111-1111-111111111111/avatar/skip.webp"
    removable_id = uuid4()
    failing_id = uuid4()
    skipped_id = uuid4()
    queue.jobs[removable_id] = queue.job(
        removable_id, removable_path, attempts=0, status="pending"
    )
    queue.jobs[failing_id] = queue.job(
        failing_id, failing_path, attempts=0, status="pending"
    )
    queue.jobs[skipped_id] = queue.job(
        skipped_id, skipped_path, attempts=3, status="pending"
    )
    store = _FakeObjectStore(failing_paths={failing_path})
    now = datetime(2026, 8, 21, 15, 0, tzinfo=UTC)

    with caplog.at_level(logging.INFO):
        result = run_cleanup_batch(
            queue,
            store,
            now=now,
            limit=10,
            max_attempts=3,
            retry_delay=timedelta(minutes=5),
        )

    assert result.processed == 2
    assert result.completed == 1
    assert result.failed == 1
    assert result.skipped == 1
    assert store.removed == [[removable_path], [failing_path]]
    assert queue.jobs[removable_id].status == "completed"
    assert queue.jobs[failing_id].status == "pending"
    assert queue.jobs[failing_id].attempts == 1
    assert queue.jobs[failing_id].available_at == now + timedelta(minutes=5)
    assert failing_path not in (queue.jobs[failing_id].last_error or "")
    assert "storage.example.test" not in (queue.jobs[failing_id].last_error or "")
    assert queue.jobs[skipped_id].attempts == 3
    assert queue.jobs[skipped_id].status == "pending"
    assert failing_path not in caplog.text


def test_sanitize_cleanup_error_never_leaks_plain_storage_keys_or_short_paths():
    plain_key_error = RuntimeError("delete failed for avatar/plain-key.webp")
    short_path_error = RuntimeError("delete failed for ok.webp")
    uuid_prefixed_path_error = RuntimeError(
        "delete failed for 11111111-1111-1111-1111-111111111111/avatar/fail.webp"
    )

    plain_key = sanitize_cleanup_error(plain_key_error)
    short_path = sanitize_cleanup_error(short_path_error)
    uuid_prefixed_path = sanitize_cleanup_error(uuid_prefixed_path_error)

    assert plain_key == "RuntimeError: cleanup operation failed"
    assert short_path == "RuntimeError: cleanup operation failed"
    assert uuid_prefixed_path == "RuntimeError: cleanup operation failed"


def test_cleanup_batch_uses_typed_deletion_error_and_generic_last_error():
    class _TypedFailingObjectStore:
        def remove_paths(self, paths: list[str]) -> None:
            raise CommunityMediaDeletionError(
                "COMMUNITY_MEDIA_DELETE_FAILED",
                f"delete failed for {paths[0]}",
            )

    queue = InMemoryCommunityMediaCleanupQueue()
    job_id = uuid4()
    queue.jobs[job_id] = queue.job(
        job_id,
        "avatar/plain-key.webp",
        attempts=0,
        status="pending",
    )

    result = run_cleanup_batch(
        queue,
        _TypedFailingObjectStore(),
        now=datetime(2026, 8, 21, 15, 0, tzinfo=UTC),
        limit=10,
        max_attempts=3,
        retry_delay=timedelta(minutes=5),
    )

    assert result.failed == 1
    assert queue.jobs[job_id].last_error == "CommunityMediaDeletionError: code=COMMUNITY_MEDIA_DELETE_FAILED"
