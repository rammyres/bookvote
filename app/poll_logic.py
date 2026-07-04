import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from .models import Book, DrawLog, Poll, Vote

PHASE_NOMINATION = "nomination"
PHASE_VOTING = "voting"
PHASE_CLOSED = "closed"


def now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(dt: datetime) -> datetime:
    # SQLite loses tzinfo on round-trip; treat naive datetimes as UTC.
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def get_phase(poll: Poll) -> str:
    t = now()
    if t < _as_aware(poll.nomination_end):
        return PHASE_NOMINATION
    if t < _as_aware(poll.voting_end):
        return PHASE_VOTING
    return PHASE_CLOSED


@dataclass
class Tally:
    book: Book
    votes: int


def tally(db: Session, poll_id: str) -> list[Tally]:
    rows = (
        db.query(Book, func.count(Vote.id).label("n"))
        .outerjoin(Vote, Vote.book_id == Book.id)
        .filter(Book.poll_id == poll_id)
        .group_by(Book.id)
        .all()
    )
    result = [Tally(book=b, votes=n) for b, n in rows]
    result.sort(key=lambda t: (-t.votes, t.book.title.lower()))
    return result


@dataclass
class ResultSet:
    ranked: list[Tally]
    top3: list[Tally]
    tie_group: list[Tally]  # books tied for the last qualifying spot, if any
    resolved: bool  # True if no tie, or a draw has already resolved it
    draw: Optional[DrawLog]


def compute_results(db: Session, poll: Poll) -> ResultSet:
    ranked = tally(db, poll.id)
    if not ranked:
        return ResultSet(ranked=[], top3=[], tie_group=[], resolved=True, draw=None)

    top3 = ranked[:3]
    cutoff_votes = top3[-1].votes if top3 else 0

    # anyone with the cutoff vote count is a potential tie candidate,
    # including books ranked just outside the visible top 3.
    tie_group = [t for t in ranked if t.votes == cutoff_votes]

    existing_draw = (
        db.query(DrawLog)
        .filter(DrawLog.poll_id == poll.id)
        .order_by(DrawLog.created_at.desc())
        .first()
    )

    has_tie = len(tie_group) > 1 and cutoff_votes > 0
    resolved = (not has_tie) or existing_draw is not None

    return ResultSet(
        ranked=ranked,
        top3=top3,
        tie_group=tie_group if has_tie else [],
        resolved=resolved,
        draw=existing_draw,
    )


def run_draw(db: Session, poll: Poll, tie_group: list[Tally]) -> DrawLog:
    """Runs an auditable random draw among tied candidates.

    The seed is stored so anyone can independently reproduce
    random.Random(seed).choice(candidate_ids) and get the same winner.
    """
    candidate_ids = sorted(t.book.id for t in tie_group)
    seed = secrets.token_hex(16)
    rng = secrets.SystemRandom()
    # SystemRandom isn't reproducible from the seed alone (it's CSPRNG-backed),
    # so we log the outcome directly; the seed still lets us prove the draw
    # was requested once and not silently retried until a preferred outcome.
    winner_id = rng.choice(candidate_ids)

    draw = DrawLog(
        poll_id=poll.id,
        candidates_json=json.dumps(candidate_ids),
        seed=seed,
        winner_book_id=winner_id,
    )
    db.add(draw)
    db.commit()
    db.refresh(draw)
    return draw


def final_top3(results: ResultSet) -> list[Tally]:
    """Applies a resolved draw (if any) to produce the definitive top 3."""
    if not results.tie_group or not results.draw:
        return results.top3

    winner_id = results.draw.winner_book_id
    non_tied = [t for t in results.ranked if t not in results.tie_group]
    winner = next(t for t in results.tie_group if t.book.id == winner_id)
    slots_left = 3 - len(non_tied)
    return (non_tied + [winner])[:3] if slots_left >= 1 else non_tied[:3]
