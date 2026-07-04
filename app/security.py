import hashlib
import os
import uuid

import httpx
from fastapi import Request, Response
from itsdangerous import BadSignature, URLSafeSerializer

SECRET_KEY = os.environ.get("BOOKVOTE_SECRET_KEY", "change-me-in-.env")
TURNSTILE_SECRET_KEY = os.environ.get("TURNSTILE_SECRET_KEY", "")
TURNSTILE_SITE_KEY = os.environ.get("TURNSTILE_SITE_KEY", "")
# When no Turnstile keys are configured, captcha checks are skipped (dev mode).
CAPTCHA_ENABLED = bool(TURNSTILE_SECRET_KEY and TURNSTILE_SITE_KEY)

_serializer = URLSafeSerializer(SECRET_KEY, salt="bookvote-voter")
VOTER_COOKIE = "bv_voter"


def get_or_set_voter_id(request: Request, response: Response) -> str:
    """Returns a stable per-browser voter id, setting a signed cookie if absent.

    This is one layer of the anti-bot stack: it does not stop a determined
    attacker (cookies can be cleared), but combined with the IP-based cap
    in `register_voter_identity` and the captcha check, it raises the cost
    of casual multi-voting substantially.
    """
    raw = request.cookies.get(VOTER_COOKIE)
    if raw:
        try:
            voter_id = _serializer.loads(raw)
            if isinstance(voter_id, str) and voter_id:
                return voter_id
        except BadSignature:
            pass

    voter_id = uuid.uuid4().hex
    signed = _serializer.dumps(voter_id)
    response.set_cookie(
        VOTER_COOKIE,
        signed,
        max_age=60 * 60 * 24 * 365,
        httponly=True,
        samesite="lax",
        secure=os.environ.get("BOOKVOTE_COOKIE_SECURE", "true").lower() == "true",
    )
    return voter_id


def hash_ip(request: Request, poll_id: str) -> str:
    """Hashes the caller's IP together with the poll id and a server secret,
    so raw IPs are never stored and hashes aren't reusable across polls."""
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "unknown")
    ip = ip.split(",")[0].strip()
    payload = f"{SECRET_KEY}:{poll_id}:{ip}".encode()
    return hashlib.sha256(payload).hexdigest()


async def verify_captcha(token: str, request: Request) -> bool:
    """Verifies a Cloudflare Turnstile token. Returns True (allow) if
    Turnstile is not configured, so the app still runs out of the box in
    dev; set TURNSTILE_SITE_KEY/TURNSTILE_SECRET_KEY in .env for production.
    """
    if not CAPTCHA_ENABLED:
        return True
    if not token:
        return False

    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "")
    ip = ip.split(",")[0].strip()

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                data={"secret": TURNSTILE_SECRET_KEY, "response": token, "remoteip": ip},
            )
            data = resp.json()
        except httpx.HTTPError:
            return False
    return bool(data.get("success"))
