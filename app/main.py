import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()  # no-op if vars are already set (e.g. by docker-compose's env_file)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from fastapi import FastAPI, Request, Response, Form, Depends, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from .database import Base, engine, get_db, ensure_column
from .models import Book, Poll, Vote, VoterIdentity, gen_short_id
from . import poll_logic as pl
from .book_search import search_books
from .email_sender import send_email
from .security import get_or_set_voter_id, hash_ip, verify_captcha, TURNSTILE_SITE_KEY, CAPTCHA_ENABLED

Base.metadata.create_all(bind=engine)
ensure_column("polls", "admin_email", "VARCHAR")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="Enquete de Livros")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

MAX_VOTER_IDENTITIES_PER_IP = int(os.environ.get("BOOKVOTE_MAX_VOTERS_PER_IP", "6"))

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_valid_email(value: str) -> bool:
    return bool(_EMAIL_RE.match(value.strip()))


def parse_local_datetime(value: str, tz_offset_minutes: int) -> datetime:
    """value: 'YYYY-MM-DDTHH:MM' from a <input type=datetime-local>.
    tz_offset_minutes: JS Date.prototype.getTimezoneOffset() value, i.e.
    minutes to ADD to local time to get UTC."""
    naive_local = datetime.strptime(value, "%Y-%m-%dT%H:%M")
    utc_dt = naive_local + timedelta(minutes=tz_offset_minutes)
    return utc_dt.replace(tzinfo=timezone.utc)


def register_voter_identity(db: Session, poll_id: str, ip_hash: str, voter_id: str) -> bool:
    """Returns True if this voter_id is allowed to act (already known, or
    room left under the per-IP cap). Returns False if the IP has already
    spawned too many distinct voter identities for this poll."""
    existing = (
        db.query(VoterIdentity)
        .filter_by(poll_id=poll_id, ip_hash=ip_hash, voter_id=voter_id)
        .first()
    )
    if existing:
        return True

    count = db.query(VoterIdentity).filter_by(poll_id=poll_id, ip_hash=ip_hash).count()
    if count >= MAX_VOTER_IDENTITIES_PER_IP:
        return False

    db.add(VoterIdentity(poll_id=poll_id, ip_hash=ip_hash, voter_id=voter_id))
    db.commit()
    return True


def get_poll_or_404(db: Session, poll_id: str) -> Poll:
    poll = db.query(Poll).filter(Poll.id == poll_id).first()
    if not poll:
        raise HTTPException(status_code=404, detail="Enquete não encontrada")
    return poll


def get_poll_by_admin_token_or_404(db: Session, admin_token: str) -> Poll:
    poll = db.query(Poll).filter(Poll.admin_token == admin_token).first()
    if not poll:
        raise HTTPException(status_code=404, detail="Enquete não encontrada")
    return poll


def carry_cookie(source: Response, target):
    if "set-cookie" in source.headers:
        target.headers["set-cookie"] = source.headers["set-cookie"]
    return target


# ---------------------------------------------------------------- home / create

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})


@app.get("/new", response_class=HTMLResponse)
def new_poll_form(request: Request):
    return templates.TemplateResponse("new_poll.html", {"request": request})


@app.get("/polls", response_class=HTMLResponse)
def list_polls(request: Request, db: Session = Depends(get_db)):
    all_polls = db.query(Poll).order_by(Poll.created_at.desc()).all()
    ongoing = [(p, pl.get_phase(p)) for p in all_polls]
    ongoing = [(p, phase) for p, phase in ongoing if phase != pl.PHASE_CLOSED]
    return templates.TemplateResponse(
        "poll_list.html", {"request": request, "ongoing": ongoing}
    )


@app.post("/polls")
@limiter.limit("5/minute")
async def create_poll(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    nomination_end_local: str = Form(...),
    round1_end_local: str = Form(...),
    round2_end_local: str = Form(...),
    tz_offset: int = Form(0),
    max_noms_per_voter: int = Form(3),
    admin_email: str = Form(""),
    db: Session = Depends(get_db),
):
    nomination_end = parse_local_datetime(nomination_end_local, tz_offset)
    round1_end = parse_local_datetime(round1_end_local, tz_offset)
    round2_end = parse_local_datetime(round2_end_local, tz_offset)

    if nomination_end <= pl.now():
        raise HTTPException(400, "O fim das indicações precisa ser no futuro.")
    if round1_end <= nomination_end:
        raise HTTPException(400, "O fim da 1ª votação precisa ser depois do fim das indicações.")
    if round2_end <= round1_end:
        raise HTTPException(400, "O fim da 2ª votação precisa ser depois do fim da 1ª votação.")

    admin_email_clean = admin_email.strip()
    if admin_email_clean and not is_valid_email(admin_email_clean):
        raise HTTPException(400, "E-mail inválido.")

    poll = None
    for _ in range(5):
        candidate = Poll(
            id=gen_short_id(8),
            admin_token=gen_short_id(16),
            title=title.strip(),
            description=description.strip(),
            nomination_end=nomination_end,
            round1_end=round1_end,
            round2_end=round2_end,
            max_noms_per_voter=max_noms_per_voter,
            admin_email=admin_email_clean or None,
        )
        db.add(candidate)
        try:
            db.commit()
            poll = candidate
            break
        except IntegrityError:
            db.rollback()
    if poll is None:
        raise HTTPException(500, "Não foi possível gerar um link único. Tente novamente.")
    db.refresh(poll)

    if poll.admin_email:
        admin_url = f"{request.url.scheme}://{request.url.netloc}/admin/{poll.admin_token}"
        public_url = f"{request.url.scheme}://{request.url.netloc}/p/{poll.id}"
        await send_email(
            to=poll.admin_email,
            subject=f"Link de administração — {poll.title}",
            html=(
                f"<p>Sua enquete <strong>{poll.title}</strong> foi criada.</p>"
                f"<p><strong>Link de administração</strong> (guarde com cuidado, "
                f"quem o tiver administra a enquete):<br>"
                f'<a href="{admin_url}">{admin_url}</a></p>'
                f"<p><strong>Link público</strong> (compartilhe com os participantes):<br>"
                f'<a href="{public_url}">{public_url}</a></p>'
            ),
        )

    return RedirectResponse(url=f"/admin/{poll.admin_token}", status_code=303)


# ---------------------------------------------------------------------- poll page

@app.get("/p/{poll_id}", response_class=HTMLResponse)
def view_poll(request: Request, poll_id: str, response: Response, db: Session = Depends(get_db)):
    poll = get_poll_or_404(db, poll_id)
    voter_id = get_or_set_voter_id(request, response)
    phase = pl.get_phase(poll)

    ctx = {
        "request": request,
        "poll": poll,
        "phase": phase,
        "captcha_enabled": CAPTCHA_ENABLED,
        "turnstile_site_key": TURNSTILE_SITE_KEY,
        "show_recovery": True,
    }

    if phase == pl.PHASE_NOMINATION:
        books = db.query(Book).filter(Book.poll_id == poll.id).order_by(Book.created_at).all()
        my_noms = [b for b in books if b.voter_id == voter_id]
        ctx.update(books=books, my_nom_count=len(my_noms))
        html = templates.TemplateResponse("poll_nominate.html", ctx)

    elif phase == pl.PHASE_ROUND1:
        books = db.query(Book).filter(Book.poll_id == poll.id).order_by(Book.title).all()
        my_votes = {
            v.book_id
            for v in db.query(Vote)
            .filter(Vote.poll_id == poll.id, Vote.voter_id == voter_id, Vote.round == 1)
            .all()
        }
        ctx.update(books=books, my_votes=my_votes)
        html = templates.TemplateResponse("poll_vote_round1.html", ctx)

    elif phase == pl.PHASE_ROUND2:
        pl.ensure_round1_promotion(db, poll)
        books = (
            db.query(Book)
            .filter(Book.poll_id == poll.id, Book.promoted.is_(True))
            .order_by(Book.title)
            .all()
        )
        my_vote = (
            db.query(Vote)
            .filter(Vote.poll_id == poll.id, Vote.voter_id == voter_id, Vote.round == 2)
            .first()
        )
        ctx.update(books=books, my_book_id=my_vote.book_id if my_vote else None)
        html = templates.TemplateResponse("poll_vote_round2.html", ctx)

    else:
        pl.ensure_round1_promotion(db, poll)
        results = pl.compute_final_results(db, poll)
        ctx.update(results=results)
        html = templates.TemplateResponse("poll_results.html", ctx)

    return carry_cookie(response, html)


@app.get("/api/book-search")
@limiter.limit("30/minute")
async def api_book_search(request: Request, q: str = ""):
    q = q.strip()
    if len(q) < 2:
        return JSONResponse([])
    results = await search_books(q)
    return JSONResponse(results)


@app.post("/p/{poll_id}/nominate")
@limiter.limit("20/minute")
async def nominate(
    request: Request,
    poll_id: str,
    title: str = Form(...),
    author: str = Form(""),
    isbn: str = Form(""),
    thumbnail_url: str = Form(""),
    submitted_by: str = Form(""),
    cf_turnstile_response: str = Form(default="", alias="cf-turnstile-response"),
    db: Session = Depends(get_db),
):
    poll = get_poll_or_404(db, poll_id)
    if pl.get_phase(poll) != pl.PHASE_NOMINATION:
        raise HTTPException(400, "O período de indicações já terminou.")

    if not await verify_captcha(cf_turnstile_response, request):
        raise HTTPException(400, "Falha na verificação anti-robô. Tente novamente.")

    response = Response()
    voter_id = get_or_set_voter_id(request, response)
    ip_h = hash_ip(request, poll.id)

    if not register_voter_identity(db, poll.id, ip_h, voter_id):
        raise HTTPException(429, "Muitos votantes distintos a partir desta rede. Fale com o organizador.")

    existing_count = db.query(Book).filter(Book.poll_id == poll.id, Book.voter_id == voter_id).count()
    if existing_count >= (poll.max_noms_per_voter or 3):
        raise HTTPException(400, "Você já atingiu o limite de indicações.")

    isbn_clean = isbn.strip() or None
    if isbn_clean:
        dup = db.query(Book).filter(Book.poll_id == poll.id, Book.isbn == isbn_clean).first()
        if dup:
            raise HTTPException(400, "Esse ISBN já foi indicado.")

    thumb_clean = thumbnail_url.strip()
    if not (thumb_clean.startswith("http://") or thumb_clean.startswith("https://")):
        thumb_clean = None

    book = Book(
        poll_id=poll.id,
        title=title.strip(),
        author=author.strip() or None,
        isbn=isbn_clean,
        thumbnail_url=thumb_clean,
        submitted_by=submitted_by.strip() or None,
        voter_id=voter_id,
    )
    db.add(book)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, "Esse livro já foi indicado.")

    return carry_cookie(response, RedirectResponse(url=f"/p/{poll_id}", status_code=303))


@app.post("/p/{poll_id}/resend-admin-link")
@limiter.limit("3/hour")
async def resend_admin_link(request: Request, poll_id: str, email: str = Form(...), db: Session = Depends(get_db)):
    poll = get_poll_or_404(db, poll_id)
    email_norm = email.strip().lower()

    if poll.admin_email and poll.admin_email.strip().lower() == email_norm:
        admin_url = f"{request.url.scheme}://{request.url.netloc}/admin/{poll.admin_token}"
        await send_email(
            to=poll.admin_email,
            subject=f"Link de administração — {poll.title}",
            html=(
                f"<p>Você pediu para recuperar o link de administração da enquete "
                f"<strong>{poll.title}</strong>:</p>"
                f'<p><a href="{admin_url}">{admin_url}</a></p>'
                f"<p>Guarde esse link com cuidado — quem o tiver administra a enquete.</p>"
            ),
        )

    # Same response whether or not the email matched, so this endpoint can't
    # be used to probe which address (if any) is registered on a poll.
    return RedirectResponse(url=f"/p/{poll_id}?resend=1", status_code=303)


@app.post("/p/{poll_id}/vote-round1")
@limiter.limit("20/minute")
async def vote_round1(
    request: Request,
    poll_id: str,
    book_ids: list[str] = Form(default=[]),
    cf_turnstile_response: str = Form(default="", alias="cf-turnstile-response"),
    db: Session = Depends(get_db),
):
    poll = get_poll_or_404(db, poll_id)
    if pl.get_phase(poll) != pl.PHASE_ROUND1:
        raise HTTPException(400, "A 1ª fase de votação não está ativa.")

    if not await verify_captcha(cf_turnstile_response, request):
        raise HTTPException(400, "Falha na verificação anti-robô. Tente novamente.")

    response = Response()
    voter_id = get_or_set_voter_id(request, response)
    ip_h = hash_ip(request, poll.id)

    if not register_voter_identity(db, poll.id, ip_h, voter_id):
        raise HTTPException(429, "Muitos votantes distintos a partir desta rede. Fale com o organizador.")

    valid_ids = {
        b.id for b in db.query(Book.id).filter(Book.poll_id == poll.id, Book.id.in_(book_ids)).all()
    }

    # Replace this voter's round-1 ballot so people can change their mind
    # up until the deadline; round-2 votes (a different `round`) are untouched.
    db.query(Vote).filter(
        Vote.poll_id == poll.id, Vote.voter_id == voter_id, Vote.round == 1
    ).delete()
    for book_id in valid_ids:
        db.add(Vote(poll_id=poll.id, book_id=book_id, voter_id=voter_id, ip_hash=ip_h, round=1))
    db.commit()

    return carry_cookie(response, RedirectResponse(url=f"/p/{poll_id}", status_code=303))


@app.post("/p/{poll_id}/vote-round2")
@limiter.limit("20/minute")
async def vote_round2(
    request: Request,
    poll_id: str,
    book_id: str = Form(...),
    cf_turnstile_response: str = Form(default="", alias="cf-turnstile-response"),
    db: Session = Depends(get_db),
):
    poll = get_poll_or_404(db, poll_id)
    if pl.get_phase(poll) != pl.PHASE_ROUND2:
        raise HTTPException(400, "A 2ª fase de votação não está ativa.")

    pl.ensure_round1_promotion(db, poll)

    if not await verify_captcha(cf_turnstile_response, request):
        raise HTTPException(400, "Falha na verificação anti-robô. Tente novamente.")

    response = Response()
    voter_id = get_or_set_voter_id(request, response)
    ip_h = hash_ip(request, poll.id)

    if not register_voter_identity(db, poll.id, ip_h, voter_id):
        raise HTTPException(429, "Muitos votantes distintos a partir desta rede. Fale com o organizador.")

    book = (
        db.query(Book)
        .filter(Book.id == book_id, Book.poll_id == poll.id, Book.promoted.is_(True))
        .first()
    )
    if not book:
        raise HTTPException(400, "Livro inválido para a 2ª fase.")

    # single choice: replace any previous round-2 vote from this voter
    db.query(Vote).filter(
        Vote.poll_id == poll.id, Vote.voter_id == voter_id, Vote.round == 2
    ).delete()
    db.add(Vote(poll_id=poll.id, book_id=book.id, voter_id=voter_id, ip_hash=ip_h, round=2))
    db.commit()

    return carry_cookie(response, RedirectResponse(url=f"/p/{poll_id}", status_code=303))


# --------------------------------------------------------------------------- admin

@app.get("/admin/{admin_token}", response_class=HTMLResponse)
def admin_dashboard(request: Request, admin_token: str, db: Session = Depends(get_db)):
    poll = get_poll_by_admin_token_or_404(db, admin_token)
    phase = pl.get_phase(poll)

    round1_tally = pl.tally(db, poll.id, round=1) if phase != pl.PHASE_NOMINATION else []
    round2_tally = None
    results = None

    if phase in (pl.PHASE_ROUND2, pl.PHASE_CLOSED):
        pl.ensure_round1_promotion(db, poll)
        round2_tally = pl.tally(db, poll.id, round=2, promoted_only=True)
    if phase == pl.PHASE_CLOSED:
        results = pl.compute_final_results(db, poll)

    tie_group_json = None
    if results and results.tie_group:
        tie_group_json = json.dumps(
            [{"id": t.book.id, "title": t.book.title} for t in results.tie_group]
        )

    books = db.query(Book).filter(Book.poll_id == poll.id).order_by(Book.created_at).all()

    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "poll": poll,
            "phase": phase,
            "books": books,
            "round1_tally": round1_tally,
            "round2_tally": round2_tally,
            "results": results,
            "tie_group_json": tie_group_json,
        },
    )


@app.post("/admin/{admin_token}/end-nomination")
def end_nomination(admin_token: str, db: Session = Depends(get_db)):
    poll = get_poll_by_admin_token_or_404(db, admin_token)
    if pl.get_phase(poll) == pl.PHASE_NOMINATION:
        poll.nomination_end = pl.now()
        db.commit()
    return RedirectResponse(url=f"/admin/{admin_token}", status_code=303)


@app.post("/admin/{admin_token}/end-round1")
def end_round1(admin_token: str, db: Session = Depends(get_db)):
    poll = get_poll_by_admin_token_or_404(db, admin_token)
    if pl.get_phase(poll) in (pl.PHASE_NOMINATION, pl.PHASE_ROUND1):
        t = pl.now()
        poll.nomination_end = min(pl.as_aware(poll.nomination_end), t)
        poll.round1_end = t
        db.commit()
    return RedirectResponse(url=f"/admin/{admin_token}", status_code=303)


@app.post("/admin/{admin_token}/end-round2")
def end_round2(admin_token: str, db: Session = Depends(get_db)):
    poll = get_poll_by_admin_token_or_404(db, admin_token)
    if pl.get_phase(poll) in (pl.PHASE_NOMINATION, pl.PHASE_ROUND1, pl.PHASE_ROUND2):
        t = pl.now()
        poll.nomination_end = min(pl.as_aware(poll.nomination_end), t)
        poll.round1_end = min(pl.as_aware(poll.round1_end), t)
        poll.round2_end = t
        db.commit()
    return RedirectResponse(url=f"/admin/{admin_token}", status_code=303)


@app.post("/admin/{admin_token}/draw")
def trigger_draw(admin_token: str, db: Session = Depends(get_db)):
    poll = get_poll_by_admin_token_or_404(db, admin_token)
    if pl.get_phase(poll) != pl.PHASE_CLOSED:
        raise HTTPException(400, "A 2ª votação ainda não terminou.")
    pl.ensure_round1_promotion(db, poll)
    results = pl.compute_final_results(db, poll)
    if results.tie_group and not results.draw:
        pl.run_champion_draw(db, poll, results.tie_group)
    return RedirectResponse(url=f"/admin/{admin_token}", status_code=303)


@app.post("/admin/{admin_token}/draw-json")
def trigger_draw_json(admin_token: str, db: Session = Depends(get_db)):
    """Same draw as /draw, but returns the outcome as JSON so the admin
    dashboard can drive a suspense animation before reloading. Running the
    draw twice is safe: compute_final_results/run_champion_draw only ever
    draws once per poll and reuses the stored DrawLog afterwards."""
    poll = get_poll_by_admin_token_or_404(db, admin_token)
    if pl.get_phase(poll) != pl.PHASE_CLOSED:
        raise HTTPException(400, "A 2ª votação ainda não terminou.")
    pl.ensure_round1_promotion(db, poll)
    results = pl.compute_final_results(db, poll)
    if not results.tie_group:
        raise HTTPException(400, "Não há empate para sortear.")
    if not results.draw:
        pl.run_champion_draw(db, poll, results.tie_group)
        results = pl.compute_final_results(db, poll)

    return JSONResponse(
        {
            "candidates": [{"id": t.book.id, "title": t.book.title} for t in results.tie_group],
            "winner_id": results.champion.book.id if results.champion else None,
            "winner_title": results.champion.book.title if results.champion else None,
        }
    )
