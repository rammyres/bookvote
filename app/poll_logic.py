import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from .models import Book, DrawLog, Poll, Vote

PHASE_NOMINATION = "nomination"
PHASE_REVIEW = "review"   # nomination_end passed; frozen until the admin releases round 1
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
    if not poll.round1_released:
        return PHASE_REVIEW
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
    Rejected books never appear here — excluded from voting entirely.
    Nullified votes (superseded by a later ballot) never count either."""
    q = (
        db.query(Book, func.count(Vote.id).label("n"))
        .outerjoin(
            Vote,
            and_(Vote.book_id == Book.id, Vote.round == round, Vote.nullified.is_(False)),
        )
        .filter(Book.poll_id == poll_id, Book.rejected.is_(False))
    )
    if promoted_only:
        q = q.filter(Book.promoted.is_(True))
    rows = q.group_by(Book.id).all()
    result = [Tally(book=b, votes=n) for b, n in rows]
    result.sort(key=lambda t: (-t.votes, t.book.title.lower()))
    return result


def record_ballot(
    db: Session, poll_id: str, round: int, voter_id: str, ip_hash: str, book_ids: list[str]
) -> None:
    """Records a vote submission as new rows, never deleting anything.

    Any earlier non-nullified vote from this round matching this voter_id
    OR this ip_hash gets marked nullified — covering both "the same person
    changed their mind" (voter_id match) and "a new browser/cookie on the
    same network tried to vote again" (ip_hash match) — so only the latest
    ballot per voter *and* per IP ever counts, without losing history.
    """
    db.query(Vote).filter(
        Vote.poll_id == poll_id,
        Vote.round == round,
        Vote.nullified.is_(False),
        or_(Vote.voter_id == voter_id, Vote.ip_hash == ip_hash),
    ).update({"nullified": True}, synchronize_session=False)

    ballot_id = uuid.uuid4().hex
    for book_id in book_ids:
        db.add(
            Vote(
                poll_id=poll_id,
                book_id=book_id,
                voter_id=voter_id,
                ip_hash=ip_hash,
                round=round,
                ballot_id=ballot_id,
                nullified=False,
            )
        )
    db.commit()


# ------------------------------------------------------------- round 1 -> 2

@dataclass
class PromotionResult:
    ranked: list[Tally]        # round-1 tally, every (non-rejected) book
    secured: list[Tally]       # books that make round 2 outright, no draw
    tie_group: list[Tally]     # contested for the remaining slot(s), if any
    slots_needed: int          # winners still needed out of tie_group
    resolved: bool             # True if no tie, or a draw already resolved it
    draw: Optional[DrawLog]


def compute_round1_promotion(db: Session, poll: Poll) -> PromotionResult:
    """Figures out who advances to round 2. By default (poll.promotion_tie_policy
    == "draw"), capped at exactly 3: if more books are tied for the last
    spot(s) than there is room for, that group — and ONLY that group —
    needs a draw to narrow down to the remaining slots, mirroring
    compute_final_results's tie-break shape. If the poll opted into
    "all_advance" instead, every book tied at the cutoff is waved through
    directly, so round 2 can end up with more than 3 finalists and no
    draw is ever needed here."""
    ranked = tally(db, poll.id, round=1)
    if not ranked:
        return PromotionResult(ranked=[], secured=[], tie_group=[], slots_needed=0, resolved=True, draw=None)

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
    all_advance = poll.promotion_tie_policy == "all_advance"
    for _votes, members in groups:
        if remaining <= 0:
            break
        if len(members) <= remaining or all_advance:
            secured.extend(members)
            remaining -= len(members)
        else:
            tie_group = members
            slots_needed = remaining
            remaining = 0
            break

    existing_draw = (
        db.query(DrawLog)
        .filter(DrawLog.poll_id == poll.id, DrawLog.kind == "promotion")
        .order_by(DrawLog.created_at.desc())
        .first()
    )
    resolved = (not tie_group) or existing_draw is not None

    return PromotionResult(
        ranked=ranked,
        secured=secured,
        tie_group=tie_group,
        slots_needed=slots_needed,
        resolved=resolved,
        draw=existing_draw,
    )


def run_promotion_draw(db: Session, poll: Poll, tie_group: list[Tally], slots_needed: int) -> DrawLog:
    """Auditable draw restricted to the books tied at the round-2 cutoff,
    picking exactly the number of remaining slots (not the whole top 3)."""
    candidate_ids = sorted(t.book.id for t in tie_group)
    seed = secrets.token_hex(16)
    rng = secrets.SystemRandom()
    winners = sorted(rng.sample(candidate_ids, slots_needed))

    draw = DrawLog(
        poll_id=poll.id,
        candidates_json=json.dumps(candidate_ids),
        seed=seed,
        winner_book_ids_json=json.dumps(winners),
        kind="promotion",
    )
    db.add(draw)
    db.commit()
    db.refresh(draw)
    return draw


def finalize_round1_promotion(db: Session, poll: Poll) -> PromotionResult:
    """Idempotently applies the promotion result to Book.promoted once it's
    resolved (no tie, or a promotion draw already ran). While a tie is
    pending, this makes no changes — round 2 stays empty until the admin
    runs the draw. Safe to call on every page load."""
    if poll.round1_promoted:
        return compute_round1_promotion(db, poll)

    result = compute_round1_promotion(db, poll)
    if not result.resolved:
        return result

    winner_ids: set[str] = set()
    if result.draw:
        winner_ids = set(json.loads(result.draw.winner_book_ids_json))

    for t in result.secured:
        t.book.promoted = True
    for t in result.tie_group:
        if t.book.id in winner_ids:
            t.book.promoted = True

    poll.round1_promoted = True
    db.commit()
    return result


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
        .filter(DrawLog.poll_id == poll.id, DrawLog.kind == "champion")
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
        kind="champion",
    )
    db.add(draw)
    db.commit()
    db.refresh(draw)
    return draw