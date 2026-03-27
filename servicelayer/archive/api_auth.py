"""
https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/

Authorization expects an encrypted bearer token with the content_hash. This
keeps the auth logic completely external to OpenAleph to control creating jwt
tokens. The api only checks if the request is allowed to access the given
content_hash.

This allows a very customizable auth implementation via auth requests (e.g.
nginx, see docstring in _auth/validate endpoint).

Tokens should have a short expiration (via `exp` property in payload).
"""

import jwt
from anystore.api import create_app
from anystore.logging import get_logger
from anystore.store import get_store
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from fastapi.security import OAuth2PasswordBearer

from servicelayer import settings
from servicelayer.archive.util import path_prefix


UNAUTHORIZED = HTTPException(401, headers={"WWW-Authenticate": "Bearer"})
FORBIDDEN = HTTPException(403)

log = get_logger(__name__)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/", auto_error=True)


def validate_token(token: str | None, path: str) -> str:
    """
    Decode a JWT token and verify it grants access to the given content hash.
    Returns TokenData on success, raises UNAUTHORIZED (401) or FORBIDDEN (403).
    """
    if not token:
        log.error("Auth: no token")
        raise UNAUTHORIZED
    try:
        payload = jwt.decode(
            token,
            settings.ARCHIVE_SECRET_KEY,
            algorithms=["HS256"],
            verify=True,
        )
    except Exception as e:
        log.error(f"Auth: invalid token: `{e}`")
        raise UNAUTHORIZED
    content_hash = payload.get("c")
    if content_hash is None:
        raise UNAUTHORIZED
    prefix = path_prefix(content_hash)
    if prefix is None:
        raise UNAUTHORIZED
    if not path.startswith(prefix):
        log.error(
            "Auth: invalid path for content_hash prefix", prefix=prefix, path=path
        )
        raise FORBIDDEN
    return content_hash


auth_router = APIRouter(prefix="/_auth", tags=["auth"])


@auth_router.get("/validate")
def validate_token_endpoint(
    token: str = Depends(oauth2_scheme),
    x_original_uri: str = Header("/"),  # noqa: B008
) -> Response:
    """Validate a token for nginx auth_request subrequests.

    nginx sends the original request's Authorization header along with
    X-Original-URI so we can check permissions against the actual request being
    proxied.

    Returns 200 on success, 401 for missing/invalid token, 403 if the
    token doesn't grant access to the requested path.

    Example nginx configuration:

        location / {
            auth_request /_auth/validate;
            proxy_pass http://api:8000;
        }

        location = /_auth/validate {
            internal;
            proxy_pass http://api:8000/_auth/validate;
            proxy_pass_request_body off;
            proxy_set_header Content-Length "";
            proxy_set_header X-Original-URI $request_uri;
        }
    """
    validate_token(token, x_original_uri)
    return Response(status_code=200)


def create_archive_app():
    store = get_store(settings.ARCHIVE_URI)
    app = create_app(store=store)
    app.include_router(auth_router)
    return app
