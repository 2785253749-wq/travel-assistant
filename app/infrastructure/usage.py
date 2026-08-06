from __future__ import annotations

from datetime import date
from uuid import UUID

from app.core.usage import (
    ProviderUnavailable,
    ReserveResult,
    UsageCount,
)


class SupabaseUsageRepository:
    """Server-only service-role adapter for the atomic usage RPCs."""

    def __init__(self, client: object) -> None:
        self._client = client

    @staticmethod
    def _data(response: object) -> object:
        return getattr(response, "data", response)

    def get_daily(self, user_key: str, day: date) -> UsageCount:
        response = self._client.rpc(
            "get_ai_usage",
            {"p_subject_key": user_key, "p_usage_date": day.isoformat()},
        ).execute()
        row = self._data(response) or {}
        if isinstance(row, list):
            row = row[0] if row else {}
        return UsageCount(
            **{key: int(row.get(key, 0)) for key in UsageCount.__dataclass_fields__}
        )

    def get_global_daily(self, day: date) -> UsageCount:
        response = self._client.rpc(
            "get_ai_global_usage", {"p_usage_date": day.isoformat()}
        ).execute()
        row = self._data(response) or {}
        if isinstance(row, list):
            row = row[0] if row else {}
        return UsageCount(
            **{key: int(row.get(key, 0)) for key in UsageCount.__dataclass_fields__}
        )

    def reserve(
        self,
        user_key: str,
        day: date,
        user_limit: int,
        global_limit: int,
    ) -> ReserveResult:
        try:
            result = self._data(
                self._client.rpc(
                    "reserve_ai_usage",
                    {
                        "p_subject_key": user_key,
                        "p_usage_date": day.isoformat(),
                        "p_user_limit": user_limit,
                        "p_global_limit": global_limit,
                    },
                ).execute()
            )
        except Exception:
            raise ProviderUnavailable() from None
        if isinstance(result, list):
            result = result[0] if len(result) == 1 else None
        if not isinstance(result, dict) or set(result) != {
            "allowed",
            "reservation_id",
            "reason",
        }:
            raise ProviderUnavailable()
        allowed = result["allowed"]
        reservation_id = result["reservation_id"]
        reason = result["reason"]
        if allowed is False and reservation_id is None and reason in {
            "user_limit",
            "global_limit",
        }:
            return ReserveResult(None, reason)
        if allowed is True and isinstance(reservation_id, str) and reason is None:
            try:
                if str(UUID(reservation_id)) == reservation_id:
                    return ReserveResult(reservation_id, None)
            except ValueError:
                pass
        raise ProviderUnavailable()

    def commit(
        self,
        reservation_id: str,
        user_key: str,
        day: date,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        self._client.rpc(
            "commit_ai_usage",
            {
                "p_reservation_id": reservation_id,
                "p_subject_key": user_key,
                "p_usage_date": day.isoformat(),
                "p_input_tokens": input_tokens,
                "p_output_tokens": output_tokens,
            },
        ).execute()

    def rollback(self, reservation_id: str, user_key: str, day: date) -> None:
        self._client.rpc(
            "rollback_ai_usage",
            {
                "p_reservation_id": reservation_id,
                "p_subject_key": user_key,
                "p_usage_date": day.isoformat(),
            },
        ).execute()
