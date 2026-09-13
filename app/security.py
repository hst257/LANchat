import time
from collections import defaultdict, deque
from urllib.parse import urlsplit

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, RedirectResponse

from . import settings


class SecurityMiddleware(BaseHTTPMiddleware):
    _attempts: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next):
        host = request.url.hostname or ""
        if request.url.path != "/healthz" and not _host_allowed(host):
            return JSONResponse({"detail": "Invalid host header"}, status_code=400)
        forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
        is_https = request.url.scheme == "https" or forwarded_proto == "https"
        if settings.FORCE_HTTPS and not is_https and request.url.path != "/healthz":
            url = request.url.replace(scheme="https")
            return RedirectResponse(str(url), status_code=308)

        if request.method not in {"GET", "HEAD", "OPTIONS"} and request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            if settings.IS_PRODUCTION and (not origin or origin not in settings.ALLOWED_ORIGINS):
                return JSONResponse({"detail": "Request origin is not allowed"}, status_code=403)

        if request.url.path in {"/api/login", "/api/register"} and request.method == "POST":
            client = request.client.host if request.client else "unknown"
            key = (client, request.url.path)
            now = time.monotonic()
            window = 300 if request.url.path == "/api/login" else 3600
            limit = 10 if request.url.path == "/api/login" else 5
            attempts = self._attempts[key]
            while attempts and attempts[0] <= now - window:
                attempts.popleft()
            if len(attempts) >= limit:
                return JSONResponse(
                    {"detail": "Too many attempts. Try again later."},
                    status_code=429,
                    headers={"Retry-After": str(window)},
                )
            attempts.append(now)

        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = _content_security_policy()
        if is_https:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response


def _content_security_policy() -> str:
    media_hosts = ""
    if settings.CLOUDFRONT_DOMAIN:
        media_hosts = f" https://{settings.CLOUDFRONT_DOMAIN}"
    return "; ".join(
        [
            "default-src 'self'",
            "base-uri 'self'",
            "object-src 'none'",
            "frame-ancestors 'none'",
            "form-action 'self'",
            "script-src 'self'",
            "style-src 'self'",
            "font-src 'self'",
            f"img-src 'self' data: blob:{media_hosts}",
            f"media-src 'self' blob:{media_hosts}",
            "connect-src 'self' ws: wss:",
        ]
    )


def _host_allowed(host: str) -> bool:
    if "*" in settings.ALLOWED_HOSTS:
        return True
    return any(
        host == allowed
        or (allowed.startswith("*.") and host.endswith(allowed[1:]))
        for allowed in settings.ALLOWED_HOSTS
    )


def websocket_origin_allowed(origin: str | None) -> bool:
    if not settings.IS_PRODUCTION:
        return True
    if not origin or origin not in settings.ALLOWED_ORIGINS:
        return False
    return urlsplit(origin).scheme == "https"
