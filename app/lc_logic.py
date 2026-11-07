"""Leituras Coletivas (LC): estados, autenticação dos admins e capas.

Mantido separado do main.py (que já é grande) e sem depender dele — as
rotas ficam em lc_routes.py.
"""
import base64
import hashlib
import hmac
import logging
import os
import re
import secrets
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

import httpx
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy.orm import Session

from .database import DATA_DIR
from .models import CollectiveReading, LCAdmin, SiteSetting
from .security import SECRET_KEY

logger = logging.getLogger("bookvote.lc")

# ------------------------------------------------------------------ estados

STATE_PLANNED = "planned"
STATE_ONGOING = "ongoing"
STATE_DONE = "done"
STATES = (STATE_PLANNED, STATE_ONGOING, STATE_DONE)
STATE_LABELS = {
    STATE_PLANNED: "Prevista",
    STATE_ONGOING: "Em andamento",
    STATE_DONE: "Concluída",
}


def local_today() -> date:
    """'Hoje' no fuso do clube — a virada prevista→em andamento acontece à
    meia-noite daqui, não à meia-noite UTC (que seria 21h no Brasil)."""
    tzname = os.environ.get("BOOKVOTE_TZ", "America/Sao_Paulo")
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(tzname)
    except Exception:  # sem tzdata no sistema: Brasil não tem horário de verão desde 2019
        tz = timezone(timedelta(hours=-3))
    return datetime.now(tz).date()


def sync_reading_states(db: Session) -> int:
    """Prevista cuja data de início chegou vira 'em andamento'. Idempotente
    e barato (um UPDATE) — chamado em toda página que lista LCs, então não
    depende de cron nem de ninguém abrir o admin."""
    n = (
        db.query(CollectiveReading)
        .filter(
            CollectiveReading.state == STATE_PLANNED,
            CollectiveReading.start_date.isnot(None),
            CollectiveReading.start_date <= local_today(),
        )
        .update({CollectiveReading.state: STATE_ONGOING}, synchronize_session=False)
    )
    if n:
        db.commit()
        logger.info("%d leitura(s) coletiva(s) prevista(s) passaram para em andamento.", n)
    return n


def _far_future() -> date:
    return date.max


def grouped_readings(db: Session) -> dict[str, list[CollectiveReading]]:
    """Já sincroniza os estados e devolve {estado: [leituras]} ordenado:
    em andamento e previstas pelo início (mais próxima primeiro), concluídas
    da mais recente para a mais antiga."""
    sync_reading_states(db)
    rows = db.query(CollectiveReading).all()
    out = {s: [] for s in STATES}
    for r in rows:
        out.get(r.state, out[STATE_PLANNED]).append(r)
    out[STATE_ONGOING].sort(key=lambda r: (r.start_date or date.min, r.created_at or datetime.min))
    out[STATE_PLANNED].sort(key=lambda r: (r.start_date or _far_future(), r.created_at or datetime.min))
    out[STATE_DONE].sort(
        key=lambda r: (r.end_date or r.start_date or date.min, r.created_at or datetime.min),
        reverse=True,
    )
    return out


# ---------------------------------------------------------------- agrupamento

GROUP_NONE = "none"
GROUP_CATEGORY = "category"
GROUP_AUTHOR = "author"
GROUP_MODES = (GROUP_NONE, GROUP_CATEGORY, GROUP_AUTHOR)
GROUP_LABELS = {
    GROUP_NONE: "Sem agrupamento",
    GROUP_CATEGORY: "Por categoria",
    GROUP_AUTHOR: "Por autor",
}
_GROUP_SETTING_KEY = "lc_group_by"
_NO_CATEGORY_LABEL = "Sem categoria"
_NO_AUTHOR_LABEL = "Sem autor"
MAX_CATEGORY_LEN = 80


def get_group_mode(db: Session) -> str:
    row = db.query(SiteSetting).filter(SiteSetting.key == _GROUP_SETTING_KEY).first()
    return row.value if row and row.value in GROUP_MODES else GROUP_NONE


def set_group_mode(db: Session, mode: str) -> None:
    if mode not in GROUP_MODES:
        raise ValueError(mode)
    row = db.query(SiteSetting).filter(SiteSetting.key == _GROUP_SETTING_KEY).first()
    if row:
        row.value = mode
    else:
        db.add(SiteSetting(key=_GROUP_SETTING_KEY, value=mode))
    db.commit()


def _fold(text: str) -> str:
    """Chave de agrupamento/ordenação sem acento nem caixa ('Ágata' = 'agata')."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c)
    )


def group_readings(readings, mode: str) -> list[tuple[Optional[str], list]]:
    """Divide uma lista (já ordenada) em [(rótulo, leituras)]. Sem
    agrupamento devolve um único grupo de rótulo None. Grupos em ordem
    alfabética (sem acento/caixa); 'Sem categoria'/'Sem autor' por último.
    Dentro de cada grupo mantém a ordem original da lista."""
    readings = list(readings)
    if mode not in (GROUP_CATEGORY, GROUP_AUTHOR):
        return [(None, readings)] if readings else []
    attr, empty_label = (
        ("category", _NO_CATEGORY_LABEL) if mode == GROUP_CATEGORY else ("author", _NO_AUTHOR_LABEL)
    )
    buckets: dict[str, tuple[str, list]] = {}
    for r in readings:
        label = (getattr(r, attr) or "").strip()
        key = _fold(label)
        buckets.setdefault(key, (label, []))[1].append(r)
    empty = buckets.pop("", None)
    out = [(label or empty_label, items) for _, (label, items) in sorted(buckets.items())]
    if empty:
        out.append((empty_label, empty[1]))
    return out


def known_categories(db: Session) -> list[str]:
    rows = db.query(CollectiveReading.category).filter(CollectiveReading.category.isnot(None)).distinct().all()
    return sorted({c.strip() for (c,) in rows if c and c.strip()}, key=_fold)


# ------------------------------------------------------------------- senhas

MIN_PASSWORD_LEN = 10
_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


def valid_username(value: str) -> bool:
    return bool(_USERNAME_RE.match(value or ""))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N, _SCRYPT_R, _SCRYPT_P,
        base64.b64encode(salt).decode(), base64.b64encode(dk).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, dk_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(dk_b64)
        dk = hashlib.scrypt(
            password.encode(), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected)
        )
    except Exception:
        return False
    return hmac.compare_digest(dk, expected)


# um hash válido de uma senha aleatória: usado para gastar o mesmo tempo de
# CPU quando o usuário não existe (não revela quais usernames existem)
_DUMMY_HASH = hash_password(secrets.token_hex(8))


def authenticate(db: Session, username: str, password: str) -> Optional[LCAdmin]:
    admin = db.query(LCAdmin).filter(LCAdmin.username == (username or "").strip().lower()).first()
    if not admin:
        verify_password(password or "", _DUMMY_HASH)
        return None
    ok = verify_password(password or "", admin.password_hash)
    return admin if (ok and admin.active) else None


def create_admin(db: Session, username: str, password: str, *, display_name: str = "", is_main: bool = False) -> LCAdmin:
    admin = LCAdmin(
        username=username.strip().lower(),
        display_name=(display_name or "").strip() or None,
        password_hash=hash_password(password),
        is_main=is_main,
        active=True,
    )
    db.add(admin)
    db.commit()
    return admin


# ------------------------------------------------------------------- sessão

SESSION_COOKIE = "bv_lc_admin"
SESSION_MAX_AGE = 60 * 60 * 24 * 7
_session_serializer = URLSafeSerializer(SECRET_KEY, salt="bookvote-lc-admin-session")


def _pw_fingerprint(admin: LCAdmin) -> str:
    # Trocar a senha muda o hash, o que invalida as sessões antigas.
    return hashlib.sha256(admin.password_hash.encode()).hexdigest()[:16]


def make_session_token(admin: LCAdmin) -> str:
    return _session_serializer.dumps({"i": admin.id, "p": _pw_fingerprint(admin)})


def admin_from_token(db: Session, token: Optional[str]) -> Optional[LCAdmin]:
    if not token:
        return None
    try:
        data = _session_serializer.loads(token)
    except BadSignature:
        return None
    if not isinstance(data, dict):
        return None
    admin = db.query(LCAdmin).filter(LCAdmin.id == data.get("i")).first()
    if not admin or not admin.active:
        return None
    if not hmac.compare_digest(str(data.get("p", "")), _pw_fingerprint(admin)):
        return None
    return admin


# -------------------------------------------------------------------- capas

COVERS_DIR = os.path.join(DATA_DIR, "lc_covers")
os.makedirs(COVERS_DIR, exist_ok=True)

MAX_COVER_BYTES = 5 * 1024 * 1024
_GOOGLE_HOST_SUFFIXES = (".google.com", ".googleusercontent.com", ".gstatic.com")


def sniff_image_ext(data: bytes) -> Optional[str]:
    """Extensão pela assinatura (magic bytes), não pelo nome/Content-Type
    enviados — e SVG fica de fora de propósito (pode carregar script)."""
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def save_cover_bytes(data: bytes) -> Optional[str]:
    """Grava a imagem com nome aleatório e devolve o nome do arquivo, ou
    None se não for uma imagem aceita."""
    ext = sniff_image_ext(data)
    if not ext or len(data) > MAX_COVER_BYTES:
        return None
    name = f"{secrets.token_hex(12)}.{ext}"
    with open(os.path.join(COVERS_DIR, name), "wb") as fh:
        fh.write(data)
    return name


def delete_cover_file(filename: Optional[str]) -> None:
    if not filename or "/" in filename or "\\" in filename or filename.startswith("."):
        return
    try:
        os.remove(os.path.join(COVERS_DIR, filename))
    except FileNotFoundError:
        pass
    except OSError:
        logger.warning("Não consegui apagar a capa %s", filename, exc_info=True)


def is_google_image_url(url: str) -> bool:
    try:
        u = urlparse(url)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return u.scheme == "https" and any(host == s[1:] or host.endswith(s) for s in _GOOGLE_HOST_SUFFIXES)


def _with_zoom(url: str, zoom: int) -> str:
    return re.sub(r"([?&])zoom=\d+", rf"\g<1>zoom={zoom}", url) if "zoom=" in url else url


async def download_google_cover(url: str) -> Optional[str]:
    """Baixa a capa escolhida na busca do Google Books para o disco do
    servidor (a página não fica dependendo do Google, e não vira hotlink).
    Tenta a versão maior (zoom=2) e a original e fica com a de mais bytes
    (às vezes o zoom maior devolve só um 'sem capa' pequeno). Só aceita
    hosts do Google. Devolve o nome do arquivo ou None."""
    if not is_google_image_url(url):
        return None
    candidates = [_with_zoom(url, 2), url] if "zoom=" in url else [url]
    best: Optional[bytes] = None
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        for cand in candidates:
            try:
                resp = await client.get(cand)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                logger.warning("Falha ao baixar capa %s: %s", cand, exc)
                continue
            if not is_google_image_url(str(resp.url)):  # redirecionou para fora do Google
                continue
            data = resp.content
            if sniff_image_ext(data) and len(data) <= MAX_COVER_BYTES and (best is None or len(data) > len(best)):
                best = data
    return save_cover_bytes(best) if best else None
