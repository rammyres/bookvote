"""Sugestões de novas Leituras Coletivas a partir do resultado das enquetes.

Quando uma enquete encerra com um livro escolhido (campeão), nasce uma
`LCSuggestion` pendente. Um administrador de LC só precisa confirmar: a
confirmação cria uma leitura **prevista** ("data a definir") com título,
autor, ISBN e capa do livro vencedor; as datas, a categoria etc. são
ajustadas depois, pelo formulário de edição de sempre.

Como o encerramento de uma enquete é por tempo (não há evento), a criação é
preguiçosa — mesmo padrão de lc_logic.sync_reading_states: `sync_suggestions`
é barata, idempotente e roda quando o painel de LC é aberto (e logo após um
sorteio de desempate resolver o campeão).
"""
import logging
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import lc_logic as lcl
from . import poll_logic as pl
from .media import KIND_BOOK, aread_image
from .models import CollectiveReading, LCSuggestion, Poll, utcnow

logger = logging.getLogger("bookvote.lc.suggestions")

PENDING = "pending"
CONFIRMED = "confirmed"
DISMISSED = "dismissed"


# ------------------------------------------------------------------ criação

def sync_suggestions(db: Session) -> int:
    """Cria a sugestão de toda enquete já encerrada, com campeão definido e
    ainda sem sugestão. Enquete cancelada, sem votos na votação final ou com
    empate em 1º ainda sem sorteio não gera nada (e volta a ser avaliada na
    próxima chamada). Devolve quantas sugestões nasceram."""
    already = db.query(LCSuggestion.poll_id)
    candidates = (
        db.query(Poll)
        .filter(Poll.cancelled.is_(False), Poll.round1_released.is_(True), ~Poll.id.in_(already))
        .all()
    )
    created = 0
    for poll in candidates:
        if pl.get_phase(poll) != pl.PHASE_CLOSED:
            continue
        pl.finalize_round1_promotion(db, poll)
        result = pl.compute_final_results(db, poll)
        champ = result.champion
        if not result.resolved or champ is None or champ.votes <= 0:
            continue
        db.add(
            LCSuggestion(
                poll_id=poll.id,
                book_id=champ.book.id,
                votes=champ.votes,
                by_draw=result.draw is not None,
            )
        )
        try:
            db.commit()
            created += 1
        except IntegrityError:  # outra requisição criou no mesmo instante
            db.rollback()
    if created:
        logger.info("Sugestões de LC: %d nova(s) a partir de enquetes encerradas.", created)
    return created


# ------------------------------------------------------------------ listagem

def list_suggestions(db: Session) -> dict[str, list[dict]]:
    """{'pending': [...], 'dismissed': [...]} — cada item traz a sugestão, o
    livro, a enquete, a capa a exibir e `dup` (já existe LC com o mesmo ISBN
    ou título: útil para dispensar sem pensar, principalmente nas enquetes
    antigas que aparecem na primeira vez)."""
    rows = (
        db.query(LCSuggestion)
        .filter(LCSuggestion.status.in_((PENDING, DISMISSED)))
        .order_by(LCSuggestion.created_at.desc())
        .all()
    )
    readings = db.query(CollectiveReading.title, CollectiveReading.isbn).all()
    titles = {lcl._fold(t.strip()) for t, _ in readings if t}
    isbns = {i.strip() for _, i in readings if i}
    out: dict[str, list[dict]] = {PENDING: [], DISMISSED: []}
    for s in rows:
        b = s.book
        dup = bool((b.isbn and b.isbn.strip() in isbns) or lcl._fold(b.title.strip()) in titles)
        out[s.status].append({"s": s, "book": b, "poll": s.poll, "cover_src": b.cover_src, "dup": dup})
    return out


# ------------------------------------------------------------------- decisão

async def _cover_for(book) -> tuple[Optional[str], Optional[str]]:
    """(cover_filename, cover_url) da nova LC. Upload da indicação → vira
    cópia própria em lc_covers (a LC não depende da enquete); senão baixa a
    miniatura do Google; se o download falhar, usa o link dela como reserva.
    Links que não são do Google não viram capa (mesma regra do cadastro)."""
    if book.image_filename:
        data = await aread_image(KIND_BOOK, book.image_filename)
        name = await lcl.asave_cover_bytes(data) if data else None
        if name:
            return name, None
    thumb = book.thumbnail_url
    if thumb and lcl.is_google_image_url(thumb):
        name = await lcl.download_google_cover(thumb)
        return (name, None) if name else (None, thumb)
    return None, None


async def confirm_suggestion(db: Session, suggestion_id: str, admin_id: str) -> Optional[CollectiveReading]:
    """Cria a LC prevista. Devolve None se a sugestão não existe ou já foi
    tratada (por exemplo, por outro administrador no mesmo instante)."""
    s = db.query(LCSuggestion).filter(LCSuggestion.id == suggestion_id).first()
    if not s or s.status != PENDING:
        return None
    book = s.book
    title, author, isbn = book.title.strip()[:200], (book.author or "").strip()[:200], (book.isbn or "").strip()[:20]

    # download/cópia da capa antes de tocar no banco: se falhar, nada fica pela metade
    cover_file, cover_url = await _cover_for(book)
    try:
        claimed = (
            db.query(LCSuggestion)
            .filter(LCSuggestion.id == suggestion_id, LCSuggestion.status == PENDING)
            .update(
                {LCSuggestion.status: CONFIRMED, LCSuggestion.resolved_by: admin_id, LCSuggestion.resolved_at: utcnow()},
                synchronize_session=False,
            )
        )
        if not claimed:  # perdeu a corrida para outro administrador
            db.rollback()
            await lcl.adelete_cover_file(cover_file)
            return None
        reading = CollectiveReading(
            title=title,
            author=author or None,
            isbn=isbn or None,
            state=lcl.STATE_PLANNED,  # prevista, sem datas: "data a definir"
            cover_filename=cover_file,
            cover_url=cover_url,
            created_by=admin_id,
        )
        db.add(reading)
        db.flush()
        db.query(LCSuggestion).filter(LCSuggestion.id == suggestion_id).update(
            {LCSuggestion.reading_id: reading.id}, synchronize_session=False
        )
        db.commit()
    except Exception:
        db.rollback()
        await lcl.adelete_cover_file(cover_file)
        raise
    return reading


def dismiss_suggestion(db: Session, suggestion_id: str, admin_id: str) -> bool:
    n = (
        db.query(LCSuggestion)
        .filter(LCSuggestion.id == suggestion_id, LCSuggestion.status == PENDING)
        .update(
            {LCSuggestion.status: DISMISSED, LCSuggestion.resolved_by: admin_id, LCSuggestion.resolved_at: utcnow()},
            synchronize_session=False,
        )
    )
    db.commit()
    return bool(n)


def restore_suggestion(db: Session, suggestion_id: str) -> bool:
    n = (
        db.query(LCSuggestion)
        .filter(LCSuggestion.id == suggestion_id, LCSuggestion.status == DISMISSED)
        .update(
            {LCSuggestion.status: PENDING, LCSuggestion.resolved_by: None, LCSuggestion.resolved_at: None},
            synchronize_session=False,
        )
    )
    db.commit()
    return bool(n)
