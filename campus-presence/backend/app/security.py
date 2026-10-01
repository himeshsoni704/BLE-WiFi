"""Credentials, JWT, hashed identifiers and a small rate limiter."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

ROLES = ("student", "faculty", "admin")


# ---- passwords (PBKDF2-HMAC-SHA256, stdlib only) ---------------------------------

def hash_password(password: str, iterations: int = 200_000) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(
        iterations, base64.b64encode(salt).decode(), base64.b64encode(dk).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iters, salt_b64, dk_b64 = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt_b64), int(iters))
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except Exception:
        return False


def hash_secret_key(key: str) -> str:
    """Fast salted hash for high-entropy API keys (node keys); not for passwords."""
    salt = secrets.token_bytes(8)
    return "sha256$" + base64.b64encode(salt).decode() + "$" + hashlib.sha256(salt + key.encode()).hexdigest()


def verify_secret_key(key: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        _, salt_b64, digest = stored.split("$")
        calc = hashlib.sha256(base64.b64decode(salt_b64) + key.encode()).hexdigest()
        return hmac.compare_digest(calc, digest)
    except Exception:
        return False


# ---- hashed identifiers ----------------------------------------------------------

def student_key_for(student_id: str, pepper: str) -> str:
    """Pseudonymous, stable, non-reversible without the pepper. 16 hex chars."""
    return hmac.new(pepper.encode(), b"student|" + student_id.strip().upper().encode(),
                    hashlib.sha256).hexdigest()[:16]


def new_token_secret() -> str:
    return secrets.token_hex(16)


# ---- JWT --------------------------------------------------------------------------

@dataclass
class Principal:
    sub: str
    role: str                      # student | faculty | admin | node
    student_key: str | None = None
    marker_id: str | None = None

    def is_staff(self) -> bool:
        return self.role in ("faculty", "admin")


def create_jwt(settings, *, sub: str, role: str, student_key: str | None = None) -> str:
    now = int(time.time())
    payload = {"sub": sub, "role": role, "sk": student_key, "iat": now, "exp": now + settings.jwt_ttl_s}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_jwt(settings, token: str) -> Principal:
    try:
        p = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"], options={"require": ["exp", "sub"]})
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "token expired")
    except jwt.PyJWTError:
        raise HTTPException(401, "invalid token")
    if p.get("role") not in ROLES:
        raise HTTPException(401, "invalid token role")
    return Principal(sub=p["sub"], role=p["role"], student_key=p.get("sk"))


_bearer = HTTPBearer(auto_error=False)


def current_principal(request: Request,
                      creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> Principal:
    if creds is None:
        raise HTTPException(401, "missing bearer token")
    principal = decode_jwt(request.app.state.settings, creds.credentials)
    request.state.principal = principal
    return principal


def require_roles(*roles: str):
    def dep(p: Principal = Depends(current_principal)) -> Principal:
        if p.role not in roles:
            raise HTTPException(403, f"requires role: {', '.join(roles)}")
        return p
    return dep


# ---- rate limiting -----------------------------------------------------------------

class RateLimiter:
    """In-memory sliding window. Single-process only; use a shared store when scaling out."""

    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, identity: str, bucket: str, limit: int, window_s: float = 60.0) -> None:
        now = time.monotonic()
        with self._lock:
            q = self._hits[(identity, bucket)]
            while q and now - q[0] > window_s:
                q.popleft()
            if len(q) >= limit:
                retry = int(window_s - (now - q[0])) + 1
                raise HTTPException(429, "rate limit exceeded", headers={"Retry-After": str(retry)})
            q.append(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


def rate_limit(bucket: str, limit: int, window_s: float = 60.0):
    """Dependency factory: keyed by bearer subject when present, else client IP."""
    def dep(request: Request) -> None:
        settings = request.app.state.settings
        if not settings.rate_limit_enabled:
            return
        identity = request.client.host if request.client else "unknown"
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            try:
                identity = "u:" + decode_jwt(settings, auth[7:]).sub
            except HTTPException:
                pass
        node_key = request.headers.get("x-node-key")
        if node_key:
            identity = "n:" + hashlib.sha256(node_key.encode()).hexdigest()[:12]
        request.app.state.limiter.check(identity, bucket, limit, window_s)
    return dep
