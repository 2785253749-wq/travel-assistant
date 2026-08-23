from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime

from supabase import create_client

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.travel_notes.media import (
    DEFAULT_CLEANUP_BATCH_LIMIT,
    DEFAULT_CLEANUP_MAX_ATTEMPTS,
    CleanupRunResult,
    SupabaseCommunityMediaCleanupQueue,
    SupabaseCommunityMediaObjectStore,
    run_cleanup_batch,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Process pending private community media cleanup jobs."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_CLEANUP_BATCH_LIMIT,
        help="Maximum cleanup jobs to process in one batch.",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=DEFAULT_CLEANUP_MAX_ATTEMPTS,
        help="Maximum automatic attempts before a job is left pending for manual review.",
    )
    return parser.parse_args()


def main() -> int:
    configure_logging()
    settings = get_settings()
    if settings.supabase_url is None or settings.supabase_service_key is None:
        raise RuntimeError("Supabase service-role configuration is required")

    args = parse_args()
    client = create_client(
        str(settings.supabase_url),
        settings.supabase_service_key.get_secret_value(),
    )
    result = run_cleanup_batch(
        SupabaseCommunityMediaCleanupQueue(client),
        SupabaseCommunityMediaObjectStore(client),
        now=datetime.now(UTC),
        limit=max(1, args.limit),
        max_attempts=max(1, args.max_attempts),
    )
    _log_summary(result)
    return 0


def _log_summary(result: CleanupRunResult) -> None:
    logging.getLogger("app.community_media").info(
        "cleanup_batch_complete",
        extra={
            "stage": "cleanup",
            "processed": result.processed,
            "completed": result.completed,
            "failed": result.failed,
            "skipped": result.skipped,
        },
    )


if __name__ == "__main__":
    raise SystemExit(main())
