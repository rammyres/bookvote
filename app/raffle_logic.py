import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from .models import Raffle, RaffleDraw, RaffleEntry

PHASE_SIGNUP = "signup"  # entries open
PHASE_READY = "ready"    # signup_end passed, draw not run yet
PHASE_DONE = "done"      # draw has run


def now() -> datetime:
    return datetime.now(timezone.utc)


def as_aware(dt: datetime) -> datetime:
    # SQLite loses tzinfo on round-trip; treat naive datetimes as UTC.
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def get_phase(raffle: Raffle) -> str:
    if raffle.drawn:
        return PHASE_DONE
    if now() < as_aware(raffle.signup_end):
        return PHASE_SIGNUP
    return PHASE_READY


@dataclass
class RaffleResult:
    entries: list[RaffleEntry]
    winners: list[RaffleEntry]  # empty until the draw has run
    draw: Optional[RaffleDraw]


def get_result(db: Session, raffle: Raffle) -> RaffleResult:
    entries = (
        db.query(RaffleEntry)
        .filter(RaffleEntry.raffle_id == raffle.id)
        .order_by(RaffleEntry.created_at.asc())
        .all()
    )
    draw = (
        db.query(RaffleDraw)
        .filter(RaffleDraw.raffle_id == raffle.id)
        .order_by(RaffleDraw.created_at.desc())
        .first()
    )
    winners: list[RaffleEntry] = []
    if draw:
        by_id = {e.id: e for e in entries}
        winners = [by_id[i] for i in json.loads(draw.winner_entry_ids_json) if i in by_id]
    return RaffleResult(entries=entries, winners=winners, draw=draw)


def run_raffle_draw(db: Session, raffle: Raffle) -> RaffleDraw:
    """Picks exactly min(winners_count, len(entries)) winners with a
    cryptographically secure RNG, auditable via candidates_json + seed —
    same shape as poll_logic's champion/promotion draws. The caller is
    responsible for the idempotency guard (only call this while
    get_phase(raffle) == PHASE_READY, which also implies raffle.drawn is
    still False)."""
    entries = (
        db.query(RaffleEntry)
        .filter(RaffleEntry.raffle_id == raffle.id, RaffleEntry.rejected.is_(False))
        .all()
    )
    if not entries:
        raise ValueError("Nenhum inscrito elegível para sortear.")

    k = min(raffle.winners_count, len(entries))
    candidate_ids = sorted(e.id for e in entries)
    seed = secrets.token_hex(16)
    rng = secrets.SystemRandom()
    winner_ids = sorted(rng.sample(candidate_ids, k))

    draw = RaffleDraw(
        raffle_id=raffle.id,
        candidates_json=json.dumps(candidate_ids),
        seed=seed,
        winner_entry_ids_json=json.dumps(winner_ids),
    )
    db.add(draw)
    raffle.drawn = True
    db.commit()
    db.refresh(draw)
    return draw