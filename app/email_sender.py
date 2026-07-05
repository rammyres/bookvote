import logging
import os

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


async def send_email(to: str, subject: str, html: str) -> bool:
    """Sends an email via Resend. Returns False (and logs) on any failure —
    callers should treat email delivery as best-effort, never blocking the
    action that triggered it (poll creation, link recovery)."""
    if not EMAIL_CONFIGURED:
        logger.warning("E-mail para %s não enviado (Resend não configurado): %s", to, subject)
        return False

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.post(
                RESEND_API_URL,
                headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
                json={"from": RESEND_FROM_EMAIL, "to": [to], "subject": subject, "html": html},
            )
            resp.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            logger.warning("Falha ao enviar e-mail para %s (%s): %s", to, subject, exc)
            return False
