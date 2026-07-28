from typing import Any

from app.core.config import get_settings


class SupabaseAuthGateway:
    """Verify access tokens with Supabase using the public anon key."""

    def __init__(self) -> None:
        settings = get_settings()
        if settings.supabase_url is None or settings.supabase_anon_key is None:
            raise RuntimeError("Supabase authentication is not configured")

        # Import lazily so local development and tests do not need a network client.
        from supabase import create_client

        self._client = create_client(
            str(settings.supabase_url), settings.supabase_anon_key.get_secret_value()
        )

    def get_user(self, access_token: str) -> Any:
        """Return Supabase's verified user response for an access token."""
        return self._client.auth.get_user(access_token)
