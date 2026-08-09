from dataclasses import dataclass
from collections.abc import Callable
from typing import Annotated, Any, Protocol
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status

from app.infrastructure.supabase import InvalidAuthToken, SupabaseAuthGateway


@dataclass(frozen=True)
class AuthenticatedUser:
    id: UUID
    email: str | None
    access_token: str | None = None


class AuthGateway(Protocol):
    def get_user(self, access_token: str) -> Any: ...


def get_supabase_auth_gateway() -> AuthGateway:
    return SupabaseAuthGateway()


def get_supabase_auth_gateway_factory() -> Callable[[], AuthGateway]:
    """Provide a replaceable factory without constructing the external client."""
    return get_supabase_auth_gateway


def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
    gateway_factory: Annotated[
        Callable[[], AuthGateway], Depends(get_supabase_auth_gateway_factory)
    ] = get_supabase_auth_gateway,
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
        gateway = gateway_factory()
        response = gateway.get_user(token)
        user = getattr(response, "user", response)
        user_id = getattr(user, "id", None)
        if user_id is None:
            raise ValueError("Supabase did not return a user")
        return AuthenticatedUser(
            id=UUID(str(user_id)),
            email=getattr(user, "email", None),
            access_token=token,
        )
    except InvalidAuthToken as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "AUTH_INVALID", "message": "Invalid or expired token"},
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "AUTH_UNAVAILABLE",
                "message": "Authentication service unavailable",
            },
        ) from exc


CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]


def get_optional_current_user(
    authorization: Annotated[str | None, Header()] = None,
    gateway_factory: Annotated[
        Callable[[], AuthGateway], Depends(get_supabase_auth_gateway_factory)
    ] = get_supabase_auth_gateway,
) -> AuthenticatedUser | None:
    """Allow no credential, but fully validate every credential that is presented."""
    if authorization is None:
        return None
    return get_current_user(authorization, gateway_factory)


OptionalCurrentUser = Annotated[
    AuthenticatedUser | None, Depends(get_optional_current_user)
]
