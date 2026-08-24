from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
import logging
import re
from typing import Protocol
from uuid import UUID, uuid4

from app.core.logging import database_operation, hashed_log_subject, operational_context


COMMUNITY_MEDIA_BUCKET = "community-media"
DEFAULT_SIGNED_URL_TTL_SECONDS = 3600
DEFAULT_CLEANUP_BATCH_LIMIT = 20
DEFAULT_CLEANUP_MAX_ATTEMPTS = 3
DEFAULT_CLEANUP_RETRY_DELAY = timedelta(minutes=5)

_UUID_PATTERN = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)
_PATH_PATTERN = re.compile(r"(?:[0-9a-f-]{8,}/)+(?:[^\s/]+)", re.IGNORECASE)
_OBJECT_PATH_FRAGMENT_PATTERN = re.compile(
    r"(?:^|[\s:(])(?:[a-z0-9._-]+/)+[a-z0-9._-]+(?:\.[a-z0-9._-]+)?(?:$|[\s),:])",
    re.IGNORECASE,
)


class CommunityMediaError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class CommunityMediaSigningPayloadError(CommunityMediaError):
    pass


class CommunityMediaStorageError(CommunityMediaError):
    pass


class CommunityMediaDeletionError(CommunityMediaError):
    pass


@dataclass(frozen=True, slots=True)
class CommunityMediaCleanupJob:
    id: UUID
    storage_path: str
    attempts: int
    available_at: datetime
    status: str = "pending"
    note_id: UUID | None = None
    image_id: UUID | None = None
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class CleanupRunResult:
    processed: int
    completed: int
    failed: int
    skipped: int


class CommunityMediaObjectStore(Protocol):
    def remove_paths(self, paths: list[str]) -> None: ...


class CommunityMediaCleanupQueue(Protocol):
    def enqueue(
        self,
        paths: list[str],
        *,
        note_id: UUID | None = None,
        image_id: UUID | None = None,
    ) -> int: ...

    def claim_batch(
        self, *, limit: int, now: datetime, max_attempts: int
    ) -> tuple[list[CommunityMediaCleanupJob], int]: ...

    def mark_succeeded(self, job_id: UUID) -> None: ...

    def mark_failed(
        self,
        job_id: UUID,
        *,
        attempts: int,
        available_at: datetime,
        last_error: str,
    ) -> None: ...


class CommunityMediaGateway:
    def __init__(
        self,
        storage,
        *,
        bucket: str = COMMUNITY_MEDIA_BUCKET,
        default_expires_in: int = DEFAULT_SIGNED_URL_TTL_SECONDS,
    ) -> None:
        self._storage = storage
        self._bucket = bucket
        self._default_expires_in = default_expires_in

    def sign_paths(
        self, paths: list[str], expires_in: int | None = None
    ) -> list[str]:
        if not paths:
            return []
        ttl = expires_in or self._default_expires_in
        try:
            raw = self._storage.from_(self._bucket).create_signed_urls(paths, ttl)
        except Exception as exc:
            raise CommunityMediaStorageError(
                "COMMUNITY_MEDIA_SIGNING_FAILED",
                "community media signing failed",
            ) from exc
        rows = raw.get("data") if isinstance(raw, dict) else raw
        if not isinstance(rows, list):
            raise CommunityMediaSigningPayloadError(
                "COMMUNITY_MEDIA_SIGNING_PAYLOAD_INVALID",
                "community media signing returned an invalid payload",
            )
        signed_urls: list[str] = []
        for row in rows:
            if isinstance(row, str):
                signed_urls.append(row)
                continue
            if not isinstance(row, dict):
                raise CommunityMediaSigningPayloadError(
                    "COMMUNITY_MEDIA_SIGNING_PAYLOAD_INVALID",
                    "community media signing returned an invalid row",
                )
            signed_url = (
                row.get("signedURL") or row.get("signedUrl") or row.get("signed_url")
            )
            if not isinstance(signed_url, str) or not signed_url.strip():
                raise CommunityMediaSigningPayloadError(
                    "COMMUNITY_MEDIA_SIGNING_PAYLOAD_INVALID",
                    "community media signing returned no signed URL",
                )
            signed_urls.append(signed_url)
        return signed_urls


class InMemoryCommunityMediaCleanupQueue:
    def __init__(self) -> None:
        self.jobs: dict[UUID, CommunityMediaCleanupJob] = {}

    def job(
        self,
        job_id: UUID,
        storage_path: str,
        *,
        attempts: int = 0,
        status: str = "pending",
        available_at: datetime | None = None,
        note_id: UUID | None = None,
        image_id: UUID | None = None,
        last_error: str | None = None,
    ) -> CommunityMediaCleanupJob:
        return CommunityMediaCleanupJob(
            id=job_id,
            storage_path=storage_path,
            attempts=attempts,
            available_at=available_at or datetime.now(UTC),
            status=status,
            note_id=note_id,
            image_id=image_id,
            last_error=last_error,
        )

    def enqueue(
        self,
        paths: list[str],
        *,
        note_id: UUID | None = None,
        image_id: UUID | None = None,
    ) -> int:
        normalized = _normalized_paths(paths)
        now = datetime.now(UTC)
        for path in normalized:
            job_id = uuid4()
            self.jobs[job_id] = self.job(
                job_id,
                path,
                available_at=now,
                note_id=note_id,
                image_id=image_id,
            )
        return len(normalized)

    def claim_batch(
        self, *, limit: int, now: datetime, max_attempts: int
    ) -> tuple[list[CommunityMediaCleanupJob], int]:
        available_jobs = sorted(
            (
                job
                for job in self.jobs.values()
                if job.status == "pending" and job.available_at <= now
            ),
            key=lambda job: (job.available_at, str(job.id)),
        )
        skipped = sum(1 for job in available_jobs if job.attempts >= max_attempts)
        claimed: list[CommunityMediaCleanupJob] = []
        for job in available_jobs:
            if len(claimed) >= limit:
                break
            if job.attempts >= max_attempts:
                continue
            self.jobs[job.id] = replace(job, status="processing")
            claimed.append(self.jobs[job.id])
        return claimed, skipped

    def mark_succeeded(self, job_id: UUID) -> None:
        job = self.jobs[job_id]
        self.jobs[job_id] = replace(job, status="completed", last_error=None)

    def mark_failed(
        self,
        job_id: UUID,
        *,
        attempts: int,
        available_at: datetime,
        last_error: str,
    ) -> None:
        job = self.jobs[job_id]
        self.jobs[job_id] = replace(
            job,
            status="pending",
            attempts=attempts,
            available_at=available_at,
            last_error=last_error,
        )


class NoopCommunityMediaCleanupQueue:
    def enqueue(
        self,
        paths: list[str],
        *,
        note_id: UUID | None = None,
        image_id: UUID | None = None,
    ) -> int:
        del note_id, image_id
        return len(_normalized_paths(paths))

    def claim_batch(
        self, *, limit: int, now: datetime, max_attempts: int
    ) -> tuple[list[CommunityMediaCleanupJob], int]:
        del limit, now, max_attempts
        return [], 0

    def mark_succeeded(self, job_id: UUID) -> None:
        del job_id

    def mark_failed(
        self,
        job_id: UUID,
        *,
        attempts: int,
        available_at: datetime,
        last_error: str,
    ) -> None:
        del job_id, attempts, available_at, last_error


class SupabaseCommunityMediaCleanupQueue:
    def __init__(self, client) -> None:
        self._client = client

    def enqueue(
        self,
        paths: list[str],
        *,
        note_id: UUID | None = None,
        image_id: UUID | None = None,
    ) -> int:
        normalized = _normalized_paths(paths)
        if not normalized:
            return 0
        payload = [
            {
                "note_id": str(note_id) if note_id is not None else None,
                "image_id": str(image_id) if image_id is not None else None,
                "storage_path": path,
            }
            for path in normalized
        ]
        with database_operation(
            "community_media.enqueue_cleanup",
            subject=hashed_log_subject("community-media", "|".join(normalized)),
        ):
            self._client.table("community_media_cleanup_jobs").insert(payload).execute()
        return len(normalized)

    def claim_batch(
        self, *, limit: int, now: datetime, max_attempts: int
    ) -> tuple[list[CommunityMediaCleanupJob], int]:
        fetch_limit = max(limit * 4, limit + 10)
        response = (
            self._client.table("community_media_cleanup_jobs")
            .select(
                "id, note_id, image_id, storage_path, status, attempts, available_at, last_error, created_at"
            )
            .eq("status", "pending")
            .lte("available_at", now.isoformat())
            .order("available_at", desc=False)
            .order("created_at", desc=False)
            .limit(fetch_limit)
            .execute()
        )
        rows = _row_list(response.data)
        skipped = sum(1 for row in rows if int(row.get("attempts", 0)) >= max_attempts)
        claimed: list[CommunityMediaCleanupJob] = []
        for row in rows:
            if len(claimed) >= limit:
                break
            if int(row.get("attempts", 0)) >= max_attempts:
                continue
            job_id = UUID(str(row["id"]))
            update = (
                self._client.table("community_media_cleanup_jobs")
                .update({"status": "processing"})
                .eq("id", str(job_id))
                .eq("status", "pending")
                .execute()
            )
            if not _row_list(update.data):
                continue
            claimed.append(_job_from_row(row))
        return claimed, skipped

    def mark_succeeded(self, job_id: UUID) -> None:
        self._client.table("community_media_cleanup_jobs").update(
            {"status": "completed", "last_error": None}
        ).eq("id", str(job_id)).execute()

    def mark_failed(
        self,
        job_id: UUID,
        *,
        attempts: int,
        available_at: datetime,
        last_error: str,
    ) -> None:
        self._client.table("community_media_cleanup_jobs").update(
            {
                "status": "pending",
                "attempts": attempts,
                "available_at": available_at.isoformat(),
                "last_error": last_error,
            }
        ).eq("id", str(job_id)).execute()


class SupabaseCommunityMediaObjectStore:
    def __init__(self, client, *, bucket: str = COMMUNITY_MEDIA_BUCKET) -> None:
        self._client = client
        self._bucket = bucket

    def remove_paths(self, paths: list[str]) -> None:
        if not paths:
            return
        try:
            raw = self._client.storage.from_(self._bucket).remove(paths)
        except Exception as exc:
            raise CommunityMediaDeletionError(
                "COMMUNITY_MEDIA_DELETE_FAILED",
                "community media deletion failed",
            ) from exc
        error = raw.get("error") if isinstance(raw, dict) else None
        if error:
            raise CommunityMediaDeletionError(
                "COMMUNITY_MEDIA_DELETE_FAILED",
                "community media deletion failed",
            )


def run_cleanup_batch(
    queue: CommunityMediaCleanupQueue,
    object_store: CommunityMediaObjectStore,
    *,
    now: datetime | None = None,
    limit: int = DEFAULT_CLEANUP_BATCH_LIMIT,
    max_attempts: int = DEFAULT_CLEANUP_MAX_ATTEMPTS,
    retry_delay: timedelta = DEFAULT_CLEANUP_RETRY_DELAY,
    logger: logging.Logger | None = None,
) -> CleanupRunResult:
    timestamp = now or datetime.now(UTC)
    claimed, skipped = queue.claim_batch(
        limit=limit,
        now=timestamp,
        max_attempts=max_attempts,
    )
    completed = 0
    failed = 0
    logger = logger or logging.getLogger("app.community_media")
    for job in claimed:
        subject = hashed_log_subject("community-media", job.id)
        try:
            object_store.remove_paths([job.storage_path])
            queue.mark_succeeded(job.id)
            completed += 1
            logger.info(
                "cleanup_job_succeeded",
                extra=operational_context(subject=subject, stage="cleanup"),
            )
        except Exception as exc:
            failed += 1
            queue.mark_failed(
                job.id,
                attempts=min(job.attempts + 1, max_attempts),
                available_at=timestamp + retry_delay,
                last_error=sanitize_cleanup_error(exc),
            )
            logger.warning(
                "cleanup_job_failed",
                extra=operational_context(
                    subject=subject,
                    stage="cleanup",
                    error_code="COMMUNITY_MEDIA_CLEANUP_FAILED",
                    exception_type=type(exc).__name__,
                ),
            )
    return CleanupRunResult(
        processed=len(claimed),
        completed=completed,
        failed=failed,
        skipped=skipped,
    )


def sanitize_cleanup_error(error: Exception) -> str:
    parts = [type(error).__name__]
    code = getattr(error, "code", None)
    if code:
        parts.append(f"code={code}")
    else:
        message = str(error).strip()
        if message:
            if _contains_sensitive_cleanup_fragment(message):
                parts.append("cleanup operation failed")
                return ": ".join(parts)
            sanitized = _URL_PATTERN.sub("[redacted-url]", message)
            sanitized = _UUID_PATTERN.sub("[redacted-id]", sanitized)
            sanitized = _PATH_PATTERN.sub("[redacted-path]", sanitized)
            sanitized = re.sub(r"\s+", " ", sanitized).strip()
            if sanitized != message:
                parts.append(sanitized[:160])
            else:
                parts.append("cleanup operation failed")
    return ": ".join(parts)


def _job_from_row(row: dict[str, object]) -> CommunityMediaCleanupJob:
    available_at = row.get("available_at")
    return CommunityMediaCleanupJob(
        id=UUID(str(row["id"])),
        note_id=_optional_uuid(row.get("note_id")),
        image_id=_optional_uuid(row.get("image_id")),
        storage_path=str(row.get("storage_path", "")).strip(),
        status=str(row.get("status", "pending")).strip() or "pending",
        attempts=int(row.get("attempts", 0)),
        available_at=_parse_datetime(available_at) or datetime.now(UTC),
        last_error=_optional_text(row.get("last_error")),
    )


def _normalized_paths(paths: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for path in paths:
        if not isinstance(path, str):
            continue
        candidate = path.strip()
        if not candidate or candidate in seen:
            continue
        normalized.append(candidate)
        seen.add(candidate)
    return normalized


def _row_list(data: object) -> list[dict[str, object]]:
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


def _parse_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    return None


def _optional_uuid(value: object) -> UUID | None:
    if value is None:
        return None
    return UUID(str(value))


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _contains_sensitive_cleanup_fragment(message: str) -> bool:
    if _URL_PATTERN.search(message):
        return True
    if _PATH_PATTERN.search(message):
        return True
    return bool(_OBJECT_PATH_FRAGMENT_PATTERN.search(message))
