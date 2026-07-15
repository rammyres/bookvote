import html as html_module
import logging
import os
import re

import httpx

logger = logging.getLogger("bookvote.email")

RESEND_API_URL = "https://api.resend.com/emails"
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "").strip().strip('"').strip("'")
RESEND_FROM_EMAIL = os.environ.get(
    "RESEND_FROM_EMAIL", "Enquete de Livros <onboarding@resend.dev>"
).strip()

EMAIL_CONFIGURED = bool(RESEND_API_KEY)

if EMAIL_CONFIGURED:
    logger.info("Resend configurado — remetente: %s", RESEND_FROM_EMAIL)
else:
    logger.warning(
        "RESEND_API_KEY não configurada — e-mails de administração não serão enviados "
        "(a enquete continua funcionando normalmente, só sem esse envio)."
    )

_TAG_RE = re.compile(r"<[^>]+>")
_LINK_RE = re.compile(r'<a\s+href="([^"]*)"[^>]*>.*?</a>', re.IGNORECASE | re.DOTALL)
_BLOCK_END_RE = re.compile(r"</(p|div|li)>", re.IGNORECASE)
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)


def _html_to_text(body_html: str) -> str:
    """Best-effort plain-text alternative for our (deliberately simple —
    just <p>/<strong>/<a href="X">X</a>) email templates. Every link in
    this codebase is written as <a href="url">url</a>, so replacing the
    whole tag with just the URL loses nothing."""
    text = _LINK_RE.sub(r"\1", body_html)
    text = _BR_RE.sub("\n", text)
    text = _BLOCK_END_RE.sub("\n\n", text)
    text = _TAG_RE.sub("", text)
    text = html_module.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


async def send_email(to: str, subject: str, html: str) -> bool:
    """Sends an email via Resend. Returns False (and logs) on any failure —
    callers should treat email delivery as best-effort, never blocking the
    action that triggered it (poll creation, link recovery).

    Sends both html and a derived plain-text part: HTML-only transactional
    mail is a real (if secondary) spam-scoring signal for some filters —
    this doesn't fix delivery delays caused by DNS/reputation/greylisting,
    but it's a legitimate, free improvement to stack on top of those."""
    if not EMAIL_CONFIGURED:
        logger.warning("E-mail para %s não enviado (Resend não configurado): %s", to, subject)
        return False

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.post(
                RESEND_API_URL,
                headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
                json={
                    "from": RESEND_FROM_EMAIL,
                    "to": [to],
                    "subject": subject,
                    "html": html,
                    "text": _html_to_text(html),
                },
            )
            resp.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            logger.warning("Falha ao enviar e-mail para %s (%s): %s", to, subject, exc)
            return False