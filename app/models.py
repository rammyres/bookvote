import secrets
import string
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    String,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    Boolean,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from .database import Base

_SHORT_ID_ALPHABET = string.ascii_letters + string.digits  # base62


def gen_id() -> str:
    return uuid.uuid4().hex


def gen_short_id(length: int) -> str:
    """Random base62 id for URL-facing identifiers. Uses secrets (CSPRNG),
    which matters for admin_token since that's a bearer credential."""
    return "".join(secrets.choice(_SHORT_ID_ALPHABET) for _ in range(length))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Poll(Base):
    __tablename__ = "polls"

    # 8 chars base62 ≈ 2.2e14 combinations — plenty for a small tool's poll
    # link, and short enough to read out loud or type by hand.
    id = Column(String, primary_key=True, default=lambda: gen_short_id(8))
    title = Column(String, nullable=False)
    description = Column(Text, default="")
    # 16 chars base62 ≈ 95 bits of entropy — this is a bearer credential
    # (whoever has it administers the poll), so it stays much longer than
    # the poll id despite also being shortened from the original UUID.
    admin_token = Column(
        String, unique=True, default=lambda: gen_short_id(16), nullable=False, index=True
    )
    # optional: lets the creator recover their admin link later by email
    admin_email = Column(String, nullable=True)

    nomination_end = Column(DateTime, nullable=False)
    round1_end = Column(DateTime, nullable=False)  # multi-vote, all nominated books
    round2_end = Column(DateTime, nullable=False)  # single-vote, only promoted books

    # set once, the first time anyone loads the poll after round1_end passes
    round1_promoted = Column(Boolean, default=False, nullable=False)

    # optional per-voter limits (anti-flood, not anti-bot per se)
    max_noms_per_voter = Column(Integer, default=3)

    created_at = Column(DateTime, default=utcnow)

    books = relationship("Book", back_populates="poll", cascade="all, delete-orphan")
    votes = relationship("Vote", back_populates="poll", cascade="all, delete-orphan")
    draws = relationship("DrawLog", back_populates="poll", cascade="all, delete-orphan")


class Book(Base):
    __tablename__ = "books"

    id = Column(String, primary_key=True, default=gen_id)
    poll_id = Column(String, ForeignKey("polls.id"), nullable=False, index=True)

    isbn = Column(String, nullable=True)
    title = Column(String, nullable=False)
    author = Column(String, nullable=True)
    thumbnail_url = Column(String, nullable=True)
    submitted_by = Column(String, nullable=True)  # display name, optional
    voter_id = Column(String, nullable=True, index=True)  # who nominated it

    # True for books that advanced to round 2 (top 3 of round 1, ties included)
    promoted = Column(Boolean, default=False, nullable=False)

    # admin can reject a nomination during the nomination phase (e-book
    # only, not released in Brazil, only found used, etc.) — rejected
    # books are excluded from voting/tallies but kept for the record.
    rejected = Column(Boolean, default=False, nullable=False)
    rejection_reason = Column(String, nullable=True)

    created_at = Column(DateTime, default=utcnow)

    poll = relationship("Poll", back_populates="books")
    votes = relationship("Vote", back_populates="book", cascade="all, delete-orphan")


class Vote(Base):
    __tablename__ = "votes"
    __table_args__ = (
        UniqueConstraint("book_id", "voter_id", "round", name="uq_vote_book_voter_round"),
    )

    id = Column(String, primary_key=True, default=gen_id)
    poll_id = Column(String, ForeignKey("polls.id"), nullable=False, index=True)
    book_id = Column(String, ForeignKey("books.id"), nullable=False, index=True)
    voter_id = Column(String, nullable=False, index=True)
    ip_hash = Column(String, nullable=False, index=True)
    round = Column(Integer, nullable=False)  # 1 = multi-vote phase, 2 = single-vote phase

    created_at = Column(DateTime, default=utcnow)

    poll = relationship("Poll", back_populates="votes")
    book = relationship("Book", back_populates="votes")


class VoterIdentity(Base):
    """Records each distinct voter_id seen from a given ip_hash within a poll.

    Used to cap how many separate 'voters' a single IP can spawn, which
    blunts the simplest bot pattern (clear cookies, vote again).
    """

    __tablename__ = "voter_identities"
    __table_args__ = (
        UniqueConstraint("poll_id", "ip_hash", "voter_id", name="uq_voter_identity"),
    )

    id = Column(String, primary_key=True, default=gen_id)
    poll_id = Column(String, ForeignKey("polls.id"), nullable=False, index=True)
    ip_hash = Column(String, nullable=False, index=True)
    voter_id = Column(String, nullable=False, index=True)
    created_at = Column(DateTime, default=utcnow)


class DrawLog(Base):
    """Audit trail for a tie-break lottery. Anyone can recompute the draw
    from candidates_json + seed to confirm it was fair."""

    __tablename__ = "draws"

    id = Column(String, primary_key=True, default=gen_id)
    poll_id = Column(String, ForeignKey("polls.id"), nullable=False, index=True)
    candidates_json = Column(Text, nullable=False)
    seed = Column(String, nullable=False)
    winner_book_ids_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utcnow)

    poll = relationship("Poll", back_populates="draws")
