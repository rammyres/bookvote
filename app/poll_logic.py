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
    secured: list[Tally]     # books that make the top 3 outright, no draw involved
    tie_group: list[Tally]   # ONLY the books contesting the remaining slot(s); empty if no tie
    slots_needed: int        # how many winners must be drawn out of tie_group
    resolved: bool           # True if there was no tie, or a draw already resolved it
    draw: Optional[DrawLog]


def compute_results(db: Session, poll: Poll) -> ResultSet:
    ranked = tally(db, poll.id)
    if not ranked:
        return ResultSet(ranked=[], secured=[], tie_group=[], slots_needed=0, resolved=True, draw=None)

    # group consecutive books by identical vote count (ranked is already sorted desc)
    groups: list[tuple[int, list[Tally]]] = []
    for t in ranked:
        if groups and groups[-1][0] == t.votes:
            groups[-1][1].append(t)
        else:
            groups.append((t.votes, [t]))

    secured: list[Tally] = []
    tie_group: list[Tally] = []
    slots_needed = 0
    remaining = 3
    for _votes, members in groups:
        if remaining <= 0:
            break
        if len(members) <= remaining:
            # this whole vote-count group fits within the remaining slots outright
            secured.extend(members)
            remaining -= len(members)
        else:
            # more books tied at this vote count than slots left: THIS is the
            # only group a draw should ever touch, and only for `remaining` winners
            tie_group = members
            slots_needed = remaining
            remaining = 0
            break

    existing_draw = (
        db.query(DrawLog)
        .filter(DrawLog.poll_id == poll.id)
        .order_by(DrawLog.created_at.desc())
        .first()
    )

    resolved = (not tie_group) or existing_draw is not None

    return ResultSet(
        ranked=ranked,
        secured=secured,
        tie_group=tie_group,
        slots_needed=slots_needed,
        resolved=resolved,
        draw=existing_draw,
    )


def run_draw(db: Session, poll: Poll, tie_group: list[Tally], slots_needed: int) -> DrawLog:
    """Runs an auditable random draw restricted to the tied candidates,
    picking exactly `slots_needed` winners (not the whole top 3).

    The candidate pool and seed are stored so the draw can be explained;
    winners are sampled without replacement from that pool only.
    """
    candidate_ids = sorted(t.book.id for t in tie_group)
    seed = secrets.token_hex(16)
    rng = secrets.SystemRandom()
    winners = sorted(rng.sample(candidate_ids, slots_needed))

    draw = DrawLog(
        poll_id=poll.id,
        candidates_json=json.dumps(candidate_ids),
        seed=seed,
        winner_book_ids_json=json.dumps(winners),
    )
    db.add(draw)
    db.commit()
    db.refresh(draw)
    return draw


def final_top3(results: ResultSet) -> list[Tally]:
    """Produces the definitive top 3: books secured outright, plus draw
    winners (only) if a tie among the contested group has been resolved."""
    if not results.tie_group:
        return results.secured[:3]
    if not results.draw:
        return results.secured  # incomplete on purpose: draw still pending

    winner_ids = set(json.loads(results.draw.winner_book_ids_json))
    winners = [t for t in results.tie_group if t.book.id in winner_ids]
    return (results.secured + winners)[:3]
