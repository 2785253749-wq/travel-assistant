from dataclasses import dataclass
from typing import Annotated, Any, Protocol
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status

from app.infrastructure.supabase import SupabaseAuthGateway


@dataclass(frozen=True)
class AuthenticatedUser:
    id: UUID
    email: str | None


class AuthGateway(Protocol):
    def get_user(self, access_token: str) -> Any: ...


def get_supabase_auth_gateway() -> AuthGateway:
    return SupabaseAuthGateway()


def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
    gateway: Annotated[AuthGateway, Depends(get_supabase_auth_gateway)] = None,
) -> AuthenticatedUser:
    """Authenticate only a Bearer token; never accept a caller-supplied user id."""
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "AUTH_REQUIRED", "message": "Bearer token required"},
        )

    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "AUTH_REQUIRED", "message": "Bearer token required"},
        )

    try:
        response = gateway.get_user(token)
        user = getattr(response, "user", response)
        user_id = getattr(user, "id", None)
        if user_id is None:
            raise ValueError("Supabase did not return a user")
        return AuthenticatedUser(id=UUID(str(user_id)), email=getattr(user, "email", None))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "AUTH_INVALID", "message": "Invalid or expired token"},
        ) from exc


CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
