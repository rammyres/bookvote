import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    String,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from .database import Base


def gen_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Poll(Base):
    __tablename__ = "polls"

    id = Column(String, primary_key=True, default=gen_id)
    title = Column(String, nullable=False)
    description = Column(Text, default="")
    admin_token = Column(String, unique=True, default=gen_id, nullable=False, index=True)

    nomination_end = Column(DateTime, nullable=False)
    voting_end = Column(DateTime, nullable=False)

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
    submitted_by = Column(String, nullable=True)  # display name, optional
    voter_id = Column(String, nullable=True, index=True)  # who nominated it

    created_at = Column(DateTime, default=utcnow)

    poll = relationship("Poll", back_populates="books")
    votes = relationship("Vote", back_populates="book", cascade="all, delete-orphan")


class Vote(Base):
    __tablename__ = "votes"
    __table_args__ = (
        UniqueConstraint("book_id", "voter_id", name="uq_vote_book_voter"),
    )

    id = Column(String, primary_key=True, default=gen_id)
    poll_id = Column(String, ForeignKey("polls.id"), nullable=False, index=True)
    book_id = Column(String, ForeignKey("books.id"), nullable=False, index=True)
    voter_id = Column(String, nullable=False, index=True)
    ip_hash = Column(String, nullable=False, index=True)

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
    winner_book_id = Column(String, nullable=False)
    created_at = Column(DateTime, default=utcnow)

    poll = relationship("Poll", back_populates="draws")
