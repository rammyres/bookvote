import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from .models import Book, DrawLog, Poll, Vote

PHASE_NOMINATION = "nomination"
PHASE_ROUND1 = "round1"   # multi-vote, every nominated book
PHASE_ROUND2 = "round2"   # single-vote, only promoted books
PHASE_CLOSED = "closed"


def now() -> datetime:
    return datetime.now(timezone.utc)


def as_aware(dt: datetime) -> datetime:
    # SQLite loses tzinfo on round-trip; treat naive datetimes as UTC.
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def get_phase(poll: Poll) -> str:
    t = now()
    if t < as_aware(poll.nomination_end):
        return PHASE_NOMINATION
    if t < as_aware(poll.round1_end):
        return PHASE_ROUND1
    if t < as_aware(poll.round2_end):
        return PHASE_ROUND2
    return PHASE_CLOSED


@dataclass
class Tally:
    book: Book
    votes: int


def tally(db: Session, poll_id: str, round: int, promoted_only: bool = False) -> list[Tally]:
    """Vote counts for a single round (1 or 2), so a book's round-1 tally
    never bleeds into its round-2 tally, even though it's the same row.
    Rejected books never appear here — excluded from voting entirely."""
    q = (
        db.query(Book, func.count(Vote.id).label("n"))
        .outerjoin(Vote, and_(Vote.book_id == Book.id, Vote.round == round))
        .filter(Book.poll_id == poll_id, Book.rejected.is_(False))
    )
    if promoted_only:
        q = q.filter(Book.promoted.is_(True))
    rows = q.group_by(Book.id).all()
    result = [Tally(book=b, votes=n) for b, n in rows]
    result.sort(key=lambda t: (-t.votes, t.book.title.lower()))
    return result


# ------------------------------------------------------------- round 1 -> 2

def ensure_round1_promotion(db: Session, poll: Poll) -> None:
    """Idempotently marks which books advance to round 2: the top 3 of
    round 1 by vote count, with ALL ties for the last spot advancing too
    (so a tie can mean more than 3 finalists — no draw at this boundary)."""
    if poll.round1_promoted:
        return

    ranked = tally(db, poll.id, round=1)
    if ranked:
        threshold = ranked[min(2, len(ranked) - 1)].votes
        for t in ranked:
            if t.votes >= threshold:
                t.book.promoted = True

    poll.round1_promoted = True
    db.commit()


# ------------------------------------------------------------------ final

@dataclass
class FinalResult:
    ranked: list[Tally]        # round-2 tally, promoted books only
    tie_group: list[Tally]     # books tied for 1st place, if any
    resolved: bool
    draw: Optional[DrawLog]
    champion: Optional[Tally]  # None only while a tie for 1st is unresolved


def compute_final_results(db: Session, poll: Poll) -> FinalResult:
    ranked = tally(db, poll.id, round=2, promoted_only=True)
    if not ranked:
        return FinalResult(ranked=[], tie_group=[], resolved=True, draw=None, champion=None)

    top_votes = ranked[0].votes
    leaders = [t for t in ranked if t.votes == top_votes]

    if len(leaders) == 1:
        return FinalResult(ranked=ranked, tie_group=[], resolved=True, draw=None, champion=leaders[0])

    existing_draw = (
        db.query(DrawLog)
        .filter(DrawLog.poll_id == poll.id)
        .order_by(DrawLog.created_at.desc())
        .first()
    )
    champion = None
    if existing_draw:
        winner_ids = json.loads(existing_draw.winner_book_ids_json)
        champion = next((t for t in leaders if t.book.id in winner_ids), None)

    return FinalResult(
        ranked=ranked,
        tie_group=leaders,
        resolved=existing_draw is not None,
        draw=existing_draw,
        champion=champion,
    )


def run_champion_draw(db: Session, poll: Poll, tie_group: list[Tally]) -> DrawLog:
    """Auditable draw restricted to the books tied for 1st place. Always
    picks exactly one champion out of the tied candidates."""
    candidate_ids = sorted(t.book.id for t in tie_group)
    seed = secrets.token_hex(16)
    rng = secrets.SystemRandom()
    winner = rng.choice(candidate_ids)

    draw = DrawLog(
        poll_id=poll.id,
        candidates_json=json.dumps(candidate_ids),
        seed=seed,
        winner_book_ids_json=json.dumps([winner]),
    )
    db.add(draw)
    db.commit()
    db.refresh(draw)
    return draw
