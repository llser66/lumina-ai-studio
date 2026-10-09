import hashlib
import secrets
import time

from fastapi import HTTPException, Request, Response
from pwdlib import PasswordHash

PASSWORDS = PasswordHash.recommended()
# Equal-cost verification when an account is missing limits timing disclosure.
DUMMY_HASH = PASSWORDS.hash(secrets.token_urlsafe(32))


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def issue_session(response: Response, db, settings, user_id):
    token, csrf = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
    ttl = settings.session_days * 86400
    db.execute(
        "INSERT INTO sessions(token_hash,user_id,csrf_hash,expires_at) VALUES(?,?,?,?)",
        (digest(token), user_id, digest(csrf), time.time() + ttl),
    )
    response.set_cookie(
        "lumina_session",
        token,
        max_age=ttl,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )
    response.set_cookie(
        "lumina_csrf",
        csrf,
        max_age=ttl,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )
    return csrf


def current_user(request: Request):
    token = request.cookies.get("lumina_session", "")
    if not token:
        raise HTTPException(401, "请先登录")
    row = request.app.state.db.one(
        "SELECT u.id,u.name,u.email,s.csrf_hash FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?",
        (digest(token), time.time()),
    )
    if not row:
        raise HTTPException(401, "登录已过期，请重新登录")
    return row


def mutation_user(request: Request):
    user = current_user(request)
    token = request.headers.get("X-CSRF-Token", "")
    if not token or not secrets.compare_digest(digest(token), user["csrf_hash"]):
        raise HTTPException(403, "请求校验失败，请刷新页面后重试")
    return user


def rate_limit(request, scope, *, limit=15, seconds=60, user_id=None):
    # Forwarded IP headers are deliberately ignored unless a trusted proxy is configured.
    identity = user_id or (request.client.host if request.client else "unknown")
    key = f"{scope}:{digest(identity)}"
    if not request.app.state.db.rate_allowed(key, limit, seconds):
        raise HTTPException(429, "操作过于频繁，请稍后再试", headers={"Retry-After": str(seconds)})
