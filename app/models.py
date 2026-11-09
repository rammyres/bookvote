import secrets
import string
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    String,
    Date,
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
    # set once the closure e-mail has been sent (only sent once results
    # are resolved — i.e. not while a 1st-place tie is still pending a draw)
    close_email_sent = Column(Boolean, default=False, nullable=False)
    # set once the "there's a tie, come run the draw" e-mail has been sent
    tie_email_sent = Column(Boolean, default=False, nullable=False)
    # same, but for a round-1 -> round-2 promotion tie
    promotion_tie_email_sent = Column(Boolean, default=False, nullable=False)
    # set once the "nominations are frozen, come review them" e-mail has been sent
    review_email_sent = Column(Boolean, default=False, nullable=False)

    nomination_end = Column(DateTime, nullable=False)
    round1_end = Column(DateTime, nullable=False)  # multi-vote, all nominated books
    round2_end = Column(DateTime, nullable=False)  # single-vote, only promoted books

    # set once, the first time anyone loads the poll after round1_end passes
    round1_promoted = Column(Boolean, default=False, nullable=False)

    # organizer can call off a deserted/mistaken poll at any point before
    # it closes — see poll_logic.get_phase, checked before every other
    # phase condition
    cancelled = Column(Boolean, default=False, nullable=False)

    # optional per-voter limits (anti-flood, not anti-bot per se)
    max_noms_per_voter = Column(Integer, default=3)

    # round 1 stays frozen (PHASE_REVIEW) after nomination_end until the
    # admin explicitly releases it — see poll_logic.get_phase
    round1_released = Column(Boolean, default=False, nullable=False)

    # what happens when a tie lands exactly on the round-1 -> round-2 cutoff
    # (top 3): "draw" runs a lottery restricted to the tied books, keeping
    # round 2 capped at exactly 3 finalists (the default). "all_advance"
    # waves every tied book through instead, so round 2 can end up with
    # more than 3 finalists. Chosen once, at poll creation.
    promotion_tie_policy = Column(String, default="draw", nullable=False)

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
    """Append-only vote log: every ballot ever cast stays in this table,
    nothing is deleted. When someone (re)votes, their previous non-nullified
    votes for that round — matched by voter_id OR ip_hash — get flagged
    nullified instead of removed, and the new ballot's rows are inserted
    fresh. Tallies only count nullified=False rows, giving "latest ballot
    wins" semantics while preserving a full audit trail."""

    __tablename__ = "votes"

    id = Column(String, primary_key=True, default=gen_id)
    poll_id = Column(String, ForeignKey("polls.id"), nullable=False, index=True)
    book_id = Column(String, ForeignKey("books.id"), nullable=False, index=True)
    voter_id = Column(String, nullable=False, index=True)
    ip_hash = Column(String, nullable=False, index=True)
    round = Column(Integer, nullable=False)  # 1 = multi-vote phase, 2 = single-vote phase

    # groups every row inserted together as one ballot submission
    ballot_id = Column(String, nullable=True, index=True)
    # True once superseded by a later ballot from the same voter_id/ip_hash
    nullified = Column(Boolean, default=False, nullable=False, index=True)

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
    # "promotion" (round 1 -> round 2 tie) or "champion" (round 2 -> winner tie)
    kind = Column(String, nullable=False, default="champion")
    candidates_json = Column(Text, nullable=False)
    seed = Column(String, nullable=False)
    winner_book_ids_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utcnow)

    poll = relationship("Poll", back_populates="draws")


class Raffle(Base):
    """A standalone giveaway: open signup (name + phone) for a period,
    then the admin runs a single draw for a chosen number of winners.
    Independent of Poll — no nomination/voting involved."""

    __tablename__ = "raffles"

    id = Column(String, primary_key=True, default=lambda: gen_short_id(8))
    admin_token = Column(
        String, unique=True, default=lambda: gen_short_id(16), nullable=False, index=True
    )
    title = Column(String, nullable=False)
    description = Column(Text, default="")
    admin_email = Column(String, nullable=True)

    signup_end = Column(DateTime, nullable=False)
    # chosen once, at creation — the draw always picks exactly this many
    # winners (or every entrant, if fewer signed up) in a single event
    winners_count = Column(Integer, default=1, nullable=False)

    # set once the draw has run — see raffle_logic.get_phase
    drawn = Column(Boolean, default=False, nullable=False)

    # organizer can call off a deserted/mistaken raffle any time before the
    # draw runs — see raffle_logic.get_phase, checked before drawn/signup_end
    cancelled = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime, default=utcnow)

    entries = relationship("RaffleEntry", back_populates="raffle", cascade="all, delete-orphan")
    draws = relationship("RaffleDraw", back_populates="raffle", cascade="all, delete-orphan")


class RaffleEntry(Base):
    """One signup = one person entered for the prize(s). `phone` is
    normalized to digits only and is the de-duplication key — for a
    real-world giveaway, a phone number identifies a person more
    reliably than a browser cookie."""

    __tablename__ = "raffle_entries"
    __table_args__ = (
        UniqueConstraint("raffle_id", "phone", name="uq_raffle_entry_phone"),
    )

    id = Column(String, primary_key=True, default=gen_id)
    raffle_id = Column(String, ForeignKey("raffles.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    phone = Column(String, nullable=False)
    ip_hash = Column(String, nullable=False, index=True)
    # True when the organizer added this entry manually after signup
    # closed (e.g. someone who confirmed by phone/WhatsApp) — kept only
    # so the admin panel can flag it, avoiding "who added this?" later.
    added_by_admin = Column(Boolean, default=False, nullable=False)
    # Organizer can disqualify an entry (duplicate person under a
    # different number, broke a stated rule, etc.) without deleting it —
    # excluded from the draw pool but kept for the record, same pattern
    # as Book.rejected for poll nominations.
    rejected = Column(Boolean, default=False, nullable=False)
    rejection_reason = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    raffle = relationship("Raffle", back_populates="entries")


class RaffleDraw(Base):
    """Audit trail for the raffle's draw — same shape/purpose as DrawLog,
    kept separate since it references raffle_entries, not books."""

    __tablename__ = "raffle_draws"

    id = Column(String, primary_key=True, default=gen_id)
    raffle_id = Column(String, ForeignKey("raffles.id"), nullable=False, index=True)
    candidates_json = Column(Text, nullable=False)
    seed = Column(String, nullable=False)
    winner_entry_ids_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utcnow)

    raffle = relationship("Raffle", back_populates="draws")


class LCAdmin(Base):
    """Administrador da seção de Leituras Coletivas (/adminlc). Tem login
    com usuário + senha, separado dos links secretos de enquete/sorteio.
    `is_main` marca o administrador principal — o único que cria, desativa
    e redefine a senha dos demais. Só existe um (criado via
    scripts/manage.py)."""

    __tablename__ = "lc_admins"

    id = Column(String, primary_key=True, default=gen_id)
    username = Column(String, nullable=False, unique=True, index=True)
    display_name = Column(String, nullable=True)
    password_hash = Column(String, nullable=False)
    is_main = Column(Boolean, default=False, nullable=False)
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=utcnow)


class LCCategory(Base):
    """Categoria de leitura coletiva (ex.: "Fantasia", "Cosmere"). Criada
    antes pelo administrador principal; o formulário da LC só escolhe entre
    as existentes."""

    __tablename__ = "lc_categories"

    id = Column(String, primary_key=True, default=lambda: gen_short_id(8))
    name = Column(String, nullable=False, unique=True)
    created_at = Column(DateTime, default=utcnow)


class LCStage(Base):
    """Uma etapa (semana) do cronograma detalhado de uma leitura coletiva:
    período + trecho a ler, que pode ser número ou nome ("1" a "5",
    "Prólogo" a "Parte II"). `position` é a ordem (por data de início)."""

    __tablename__ = "lc_stages"

    id = Column(String, primary_key=True, default=lambda: gen_short_id(8))
    reading_id = Column(String, ForeignKey("collective_readings.id"), nullable=False, index=True)
    position = Column(Integer, nullable=False, default=0)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    chapter_from = Column(String, nullable=True)
    chapter_to = Column(String, nullable=True)

    @staticmethod
    def _clean(v):
        """Trata "-", "–", "—" (marcadores de vazio) como campo vazio."""
        v = (v or "").strip()
        return "" if v in ("-", "\u2013", "\u2014") else v

    @property
    def chapters(self):
        """Trecho para exibir: "1 a 5", "Prólogo" (só início/trecho único) ou
        "até Parte II". Um fim vazio ou "-" é omitido (sem o "a -")."""
        a, b = self._clean(self.chapter_from), self._clean(self.chapter_to)
        if a and b and a != b:
            return f"{a} a {b}"
        if a or b:
            return a or f"até {b}"
        return ""

    @property
    def days(self):
        return (self.end_date - self.start_date).days + 1

    @property
    def duration_label(self):
        """Duração para o leitor: até 5 dias em dias; acima disso, arredondada
        para semanas (6 a 10 dias = 1 semana, 11 a 17 = 2, ...)."""
        d = self.days
        if d <= 5:
            return "1 dia" if d == 1 else f"{d} dias"
        w = max(1, int(d / 7 + 0.5))
        return "1 semana" if w == 1 else f"{w} semanas"


class CollectiveReading(Base):
    """Uma Leitura Coletiva (LC). `state` é 'planned' (prevista), 'ongoing'
    (em andamento) ou 'done' (concluída). Prevista vira em andamento
    sozinha quando `start_date` chega e em andamento vira concluída quando
    `end_date` passa (lc_logic.sync_reading_states); sem data, o admin muda
    o estado manualmente. Concluídas não precisam de cronograma."""

    __tablename__ = "collective_readings"

    id = Column(String, primary_key=True, default=lambda: gen_short_id(8))
    title = Column(String, nullable=False)
    author = Column(String, nullable=True)
    isbn = Column(String, nullable=True)
    description = Column(Text, nullable=True)

    # categoria escolhida entre as previamente cadastradas (LCCategory);
    # opcional. Serve para agrupar as leituras quando o admin principal
    # escolhe isso. (Em bancos antigos a coluna é adicionada sem a FK; o
    # ORM funciona igual, e apagar categoria zera category_id na mão.)
    category_id = Column(String, ForeignKey("lc_categories.id"), nullable=True, index=True)
    category = relationship("LCCategory", lazy="joined")

    # cronograma detalhado (opcional): semanas com datas e trecho a ler
    stages = relationship(
        "LCStage",
        cascade="all, delete-orphan",
        order_by="LCStage.position",
        lazy="selectin",
    )

    state = Column(String, nullable=False, default="planned", index=True)

    # cronograma: período + texto livre com as etapas (ex.: uma por linha).
    # Prevista pode ficar sem start_date ("data a definir"): nesse caso ela
    # não vira "em andamento" sozinha — o admin inicia manualmente.
    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)
    schedule_text = Column(Text, nullable=True)
    # cronograma em imagem (o mais comum): arquivo em disco, mesmo diretório das capas
    schedule_image_filename = Column(String, nullable=True)

    # capa: arquivo salvo em disco (upload do admin ou baixado do Google
    # Books) tem prioridade; cover_url é só o fallback quando o download
    # do Google falhou.
    cover_filename = Column(String, nullable=True)
    cover_url = Column(String, nullable=True)

    created_by = Column(String, nullable=True)  # LCAdmin.id (sem FK de propósito)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    @property
    def schedule_image_src(self):
        return f"/media/lc/{self.schedule_image_filename}" if self.schedule_image_filename else None

    @property
    def cover_src(self):
        if self.cover_filename:
            return f"/media/lc/{self.cover_filename}"
        return self.cover_url or None



class SiteSetting(Base):
    """Configuração simples chave/valor do site. Hoje só guarda
    `lc_group_by` (como a lista de leituras coletivas é agrupada)."""

    __tablename__ = "site_settings"

    key = Column(String, primary_key=True)
    value = Column(String, nullable=False, default="")
