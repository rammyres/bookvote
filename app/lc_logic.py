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
from .models import CollectiveReading, LCAdmin, LCCategory, SiteSetting
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
    """Segue o cronograma:
      - prevista cuja data de início chegou (start_date <= hoje) vira 'em andamento';
      - em andamento cuja data de fim já passou (end_date < hoje, ou seja, a
        partir do dia seguinte ao último dia) vira 'concluída'.
    Os dois passos rodam em sequência: prevista com início e fim já passados
    chega a 'concluída' de uma vez. Sem data não há transição automática (o
    admin muda no painel). Idempotente e barato (dois UPDATEs) — chamado em
    toda página que lista LCs, então não depende de cron nem de admin."""
    today = local_today()
    started = (
        db.query(CollectiveReading)
        .filter(
            CollectiveReading.state == STATE_PLANNED,
            CollectiveReading.start_date.isnot(None),
            CollectiveReading.start_date <= today,
        )
        .update({CollectiveReading.state: STATE_ONGOING}, synchronize_session=False)
    )
    finished = (
        db.query(CollectiveReading)
        .filter(
            CollectiveReading.state == STATE_ONGOING,
            CollectiveReading.end_date.isnot(None),
            CollectiveReading.end_date < today,
        )
        .update({CollectiveReading.state: STATE_DONE}, synchronize_session=False)
    )
    if started or finished:
        db.commit()
        logger.info(
            "Leituras coletivas: %d passaram para em andamento, %d para concluída.", started, finished
        )
    return started + finished


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
# Páginas onde o agrupamento pode valer: "main" = página principal (home e
# /lc); "done" = leituras concluídas (/lc/concluidas).
PAGE_MAIN = "main"
PAGE_DONE = "done"
GROUP_PAGES = (PAGE_MAIN, PAGE_DONE)
GROUP_PAGE_LABELS = {
    PAGE_MAIN: "Página principal (home e lista de leituras)",
    PAGE_DONE: "Leituras concluídas",
}
_GROUP_SETTING_KEY = "lc_group_by"
_GROUP_PAGES_KEY = "lc_group_pages"
_NO_CATEGORY_LABEL = "Sem categoria"
_NO_AUTHOR_LABEL = "Sem autor"
MAX_CATEGORY_LEN = 60


def _get_setting(db: Session, key: str) -> Optional[str]:
    row = db.query(SiteSetting).filter(SiteSetting.key == key).first()
    return row.value if row else None


def _put_setting(db: Session, key: str, value: str) -> None:
    row = db.query(SiteSetting).filter(SiteSetting.key == key).first()
    if row:
        row.value = value
    else:
        db.add(SiteSetting(key=key, value=value))


def get_group_mode(db: Session) -> str:
    value = _get_setting(db, _GROUP_SETTING_KEY)
    return value if value in GROUP_MODES else GROUP_NONE


def get_group_pages(db: Session) -> list[str]:
    """Páginas com agrupamento. Sem configuração salva = todas; valor vazio
    salvo = nenhuma (o admin desmarcou tudo)."""
    value = _get_setting(db, _GROUP_PAGES_KEY)
    if value is None:
        return list(GROUP_PAGES)
    return [p for p in value.split(",") if p in GROUP_PAGES]


def save_group_settings(db: Session, mode: str, pages) -> None:
    if mode not in GROUP_MODES:
        raise ValueError(mode)
    chosen = [p for p in GROUP_PAGES if p in set(pages)]
    _put_setting(db, _GROUP_SETTING_KEY, mode)
    _put_setting(db, _GROUP_PAGES_KEY, ",".join(chosen))
    db.commit()


def group_mode_for(db: Session, page: str) -> str:
    """Modo de agrupamento efetivo de uma página ('none' se ela não foi
    marcada pelo admin principal)."""
    return get_group_mode(db) if page in get_group_pages(db) else GROUP_NONE


def _fold(text: str) -> str:
    """Chave de agrupamento/ordenação sem acento nem caixa ('Ágata' = 'agata')."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c)
    )


def _group_label(reading, mode: str) -> str:
    if mode == GROUP_CATEGORY:
        cat = getattr(reading, "category", None)
        return (cat.name if cat else "").strip()
    return (getattr(reading, "author", None) or "").strip()


def group_readings(readings, mode: str) -> list[tuple[Optional[str], list]]:
    """Divide uma lista (já ordenada) em [(rótulo, leituras)]. Sem
    agrupamento devolve um único grupo de rótulo None. Grupos em ordem
    alfabética (sem acento/caixa); 'Sem categoria'/'Sem autor' por último.
    Dentro de cada grupo mantém a ordem original da lista."""
    readings = list(readings)
    if mode not in (GROUP_CATEGORY, GROUP_AUTHOR):
        return [(None, readings)] if readings else []
    empty_label = _NO_CATEGORY_LABEL if mode == GROUP_CATEGORY else _NO_AUTHOR_LABEL
    buckets: dict[str, tuple[str, list]] = {}
    for r in readings:
        label = _group_label(r, mode)
        buckets.setdefault(_fold(label), (label, []))[1].append(r)
    empty = buckets.pop("", None)
    out = [(label, items) for _, (label, items) in sorted(buckets.items())]
    if empty:
        out.append((empty_label, empty[1]))
    return out


# ------------------------------------------------------------------ progresso

MAX_STAGES = 60


def _dm(d: date) -> str:
    return d.strftime("%d/%m")


SHORT_STAGE_DAYS = 5


def stage_unit(stages) -> dict:
    """Como chamar cada etapa do cronograma: "semana" se as etapas passam de
    5 dias, "período" se todas são curtas (até 5 dias) e "etapa" se forem
    misturadas. `art`/`first` ajudam a concordar o gênero nas frases."""
    spans = [(s.end_date - s.start_date).days + 1 for s in stages]
    if not spans or all(d > SHORT_STAGE_DAYS for d in spans):
        return {"one": "semana", "many": "semanas", "cap": "Semana", "first": "Primeira", "next": "Próxima", "last": "última", "done_f": "concluídas"}
    if all(d <= SHORT_STAGE_DAYS for d in spans):
        return {"one": "período", "many": "períodos", "cap": "Período", "first": "Primeiro", "next": "Próximo", "last": "último", "done_f": "concluídos"}
    return {"one": "etapa", "many": "etapas", "cap": "Etapa", "first": "Primeira", "next": "Próxima", "last": "última", "done_f": "concluídas"}


def reading_progress(reading, today: Optional[date] = None) -> Optional[dict]:
    """Dados para a barra de progresso da página da leitura. Usa o
    cronograma detalhado (etapas) quando existe; senão, o tempo decorrido
    entre início e fim ("semana X de Y"); só com início, apenas "semana X".
    Devolve None para concluída ou sem dados suficientes.

    Chaves: mode ('stages'|'time'|'weeks'), percent (int|None — None = sem
    barra), title, detail, stage_status (por etapa: done|current|upcoming),
    ticks (nº de etapas, p/ marcar a divisão na barra)."""
    if getattr(reading, "state", None) == STATE_DONE:
        return None
    today = today or local_today()
    stages = list(getattr(reading, "stages", None) or [])

    if stages:
        n = len(stages)
        u = stage_unit(stages)
        status = []
        for s in stages:
            if s.end_date < today:
                status.append("done")
            elif s.start_date <= today:
                status.append("current")
            else:
                status.append("upcoming")
        done = status.count("done")
        if "current" in status:
            i = status.index("current")
            s = stages[i]
            span = (s.end_date - s.start_date).days + 1
            frac = min(max(((today - s.start_date).days + 1) / span, 0), 1)
            percent = round((done + frac) / n * 100)
            title = f"{u['cap']} {i + 1} de {n}"
            detail = f"{_dm(s.start_date)} a {_dm(s.end_date)}"
            if s.chapters:
                detail += f" · trecho: {s.chapters}"
        elif done == n:
            percent, title = 100, "Cronograma concluído"
            detail = f"{'O' if u['one'] == 'período' else 'A'} {u['last']} {u['one']} terminou em {_dm(stages[-1].end_date)}."
        elif done == 0:
            percent, title = 0, f"A leitura começa em {_dm(stages[0].start_date)}"
            detail = f"{u['first']} {u['one']}: {_dm(stages[0].start_date)} a {_dm(stages[0].end_date)}"
            if stages[0].chapters:
                detail += f" · trecho: {stages[0].chapters}"
        else:  # intervalo entre duas semanas
            nxt = stages[done]
            percent = round(done / n * 100)
            title = f"{done} de {n} {u['many']} {u['done_f']}"
            detail = f"{u['next']} {u['one']} começa em {_dm(nxt.start_date)}"
            if nxt.chapters:
                detail += f" · trecho: {nxt.chapters}"
        return {"mode": "stages", "percent": percent, "title": title, "detail": detail,
                "stage_status": status, "ticks": n}

    start, end = getattr(reading, "start_date", None), getattr(reading, "end_date", None)
    if not start:
        return None
    if end and end >= start:
        total = (end - start).days + 1
        weeks_total = -(-total // 7)
        if today < start:
            return {"mode": "time", "percent": 0, "title": f"A leitura começa em {_dm(start)}",
                    "detail": f"Termina em {_dm(end)}", "stage_status": [], "ticks": 0}
        if today > end:
            return {"mode": "time", "percent": 100, "title": "Prazo da leitura encerrado",
                    "detail": f"Terminou em {_dm(end)}", "stage_status": [], "ticks": 0}
        elapsed = (today - start).days + 1
        week = (today - start).days // 7 + 1
        return {"mode": "time", "percent": round(elapsed / total * 100),
                "title": f"Semana {week} de {weeks_total}",
                "detail": f"Dia {elapsed} de {total} · termina em {_dm(end)}",
                "stage_status": [], "ticks": 0}
    if today < start:
        return {"mode": "weeks", "percent": None, "title": f"A leitura começa em {_dm(start)}",
                "detail": "", "stage_status": [], "ticks": 0}
    week = (today - start).days // 7 + 1
    return {"mode": "weeks", "percent": None, "title": f"Semana {week} da leitura",
            "detail": f"Desde {_dm(start)}", "stage_status": [], "ticks": 0}


# ------------------------------------------------------------------ destaque

# Leituras em andamento cuja categoria tem este nome ganham destaque na
# página inicial. Comparação sem acento/caixa; se a categoria for renomeada,
# o destaque deixa de valer (é pelo nome, de propósito: não exige config).
FEATURED_CATEGORY_NAME = "Leitura principal"


def is_featured(reading) -> bool:
    cat = getattr(reading, "category", None)
    return (
        getattr(reading, "state", None) == STATE_ONGOING
        and cat is not None
        and _fold(cat.name.strip()) == _fold(FEATURED_CATEGORY_NAME)
    )


def split_featured(readings) -> tuple[list, list]:
    """(destaques, demais), mantendo a ordem da lista."""
    featured, rest = [], []
    for r in readings:
        (featured if is_featured(r) else rest).append(r)
    return featured, rest


# ---------------------------------------------------------------- categorias

def list_categories(db: Session) -> list[LCCategory]:
    return sorted(db.query(LCCategory).all(), key=lambda c: _fold(c.name))


def category_counts(db: Session) -> dict[str, int]:
    counts: dict[str, int] = {}
    for (cid,) in db.query(CollectiveReading.category_id).filter(CollectiveReading.category_id.isnot(None)).all():
        counts[cid] = counts.get(cid, 0) + 1
    return counts


def category_name_taken(db: Session, name: str, exclude_id: Optional[str] = None) -> bool:
    key = _fold(name.strip())
    return any(_fold(c.name) == key and c.id != exclude_id for c in db.query(LCCategory).all())


def delete_category(db: Session, category: LCCategory) -> int:
    """Apaga a categoria e solta as leituras dela (ficam sem categoria)."""
    n = (
        db.query(CollectiveReading)
        .filter(CollectiveReading.category_id == category.id)
        .update({CollectiveReading.category_id: None}, synchronize_session=False)
    )
    db.delete(category)
    db.commit()
    return n


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
MAX_SCHEDULE_IMAGE_BYTES = 10 * 1024 * 1024  # cronograma costuma ser um print/arte grande
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


def save_cover_bytes(data: bytes, max_bytes: int = MAX_COVER_BYTES) -> Optional[str]:
    """Grava a imagem com nome aleatório e devolve o nome do arquivo, ou
    None se não for uma imagem aceita. Serve para capas e para a imagem do
    cronograma (mesmo diretório, limite de tamanho diferente)."""
    ext = sniff_image_ext(data)
    if not ext or len(data) > max_bytes:
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
