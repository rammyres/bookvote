import os
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request, Response, Form, Depends, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from .database import Base, engine, get_db
from .models import Book, Poll, Vote, VoterIdentity
from . import poll_logic as pl
from .security import get_or_set_voter_id, hash_ip, verify_captcha, TURNSTILE_SITE_KEY, CAPTCHA_ENABLED

Base.metadata.create_all(bind=engine)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="Enquete de Livros")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

MAX_VOTER_IDENTITIES_PER_IP = int(os.environ.get("BOOKVOTE_MAX_VOTERS_PER_IP", "6"))


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


# ---------------------------------------------------------------- home / create

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})


@app.post("/polls")
@limiter.limit("5/minute")
def create_poll(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    nomination_end_local: str = Form(...),
    voting_end_local: str = Form(...),
    tz_offset: int = Form(0),
    max_noms_per_voter: int = Form(3),
    db: Session = Depends(get_db),
):
    nomination_end = parse_local_datetime(nomination_end_local, tz_offset)
    voting_end = parse_local_datetime(voting_end_local, tz_offset)

    if voting_end <= nomination_end:
        raise HTTPException(400, "O fim da votação precisa ser depois do fim das indicações.")
    if nomination_end <= pl.now():
        raise HTTPException(400, "O fim das indicações precisa ser no futuro.")

    poll = Poll(
        title=title.strip(),
        description=description.strip(),
        nomination_end=nomination_end,
        voting_end=voting_end,
        max_noms_per_voter=max_noms_per_voter,
    )
    db.add(poll)
    db.commit()
    db.refresh(poll)
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
    }

    if phase == pl.PHASE_NOMINATION:
        books = db.query(Book).filter(Book.poll_id == poll.id).order_by(Book.created_at).all()
        my_noms = [b for b in books if b.voter_id == voter_id]
        ctx.update(books=books, my_nom_count=len(my_noms))
        html = templates.TemplateResponse("poll_nominate.html", ctx)
    elif phase == pl.PHASE_VOTING:
        books = db.query(Book).filter(Book.poll_id == poll.id).order_by(Book.title).all()
        my_votes = {
            v.book_id
            for v in db.query(Vote).filter(Vote.poll_id == poll.id, Vote.voter_id == voter_id).all()
        }
        ctx.update(books=books, my_votes=my_votes)
        html = templates.TemplateResponse("poll_vote.html", ctx)
    else:
        results = pl.compute_results(db, poll)
        top3 = pl.final_top3(results)
        ctx.update(results=results, top3=top3)
        html = templates.TemplateResponse("poll_results.html", ctx)

    # carry over the Set-Cookie header set by get_or_set_voter_id, if any
    if "set-cookie" in response.headers:
        html.headers["set-cookie"] = response.headers["set-cookie"]
    return html


@app.post("/p/{poll_id}/nominate")
@limiter.limit("20/minute")
async def nominate(
    request: Request,
    poll_id: str,
    title: str = Form(...),
    author: str = Form(""),
    isbn: str = Form(""),
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

    book = Book(
        poll_id=poll.id,
        title=title.strip(),
        author=author.strip() or None,
        isbn=isbn_clean,
        submitted_by=submitted_by.strip() or None,
        voter_id=voter_id,
    )
    db.add(book)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, "Esse livro já foi indicado.")

    redirect = RedirectResponse(url=f"/p/{poll_id}", status_code=303)
    if "set-cookie" in response.headers:
        redirect.headers["set-cookie"] = response.headers["set-cookie"]
    return redirect


@app.post("/p/{poll_id}/vote")
@limiter.limit("20/minute")
async def vote(
    request: Request,
    poll_id: str,
    book_ids: list[str] = Form(default=[]),
    cf_turnstile_response: str = Form(default="", alias="cf-turnstile-response"),
    db: Session = Depends(get_db),
):
    poll = get_poll_or_404(db, poll_id)
    if pl.get_phase(poll) != pl.PHASE_VOTING:
        raise HTTPException(400, "O período de votação não está ativo.")

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

    # Replace this voter's ballot with the newly submitted selection, so
    # people can change their mind up until the deadline.
    db.query(Vote).filter(Vote.poll_id == poll.id, Vote.voter_id == voter_id).delete()
    for book_id in valid_ids:
        db.add(Vote(poll_id=poll.id, book_id=book_id, voter_id=voter_id, ip_hash=ip_h))
    db.commit()

    redirect = RedirectResponse(url=f"/p/{poll_id}", status_code=303)
    if "set-cookie" in response.headers:
        redirect.headers["set-cookie"] = response.headers["set-cookie"]
    return redirect


# --------------------------------------------------------------------------- admin

@app.get("/admin/{admin_token}", response_class=HTMLResponse)
def admin_dashboard(request: Request, admin_token: str, db: Session = Depends(get_db)):
    poll = get_poll_by_admin_token_or_404(db, admin_token)
    phase = pl.get_phase(poll)
    books = db.query(Book).filter(Book.poll_id == poll.id).order_by(Book.created_at).all()
    results = pl.compute_results(db, poll) if phase == pl.PHASE_CLOSED else None
    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "poll": poll,
            "phase": phase,
            "books": books,
            "results": results,
            "top3": pl.final_top3(results) if results else None,
        },
    )


@app.post("/admin/{admin_token}/end-nomination")
def end_nomination(admin_token: str, db: Session = Depends(get_db)):
    poll = get_poll_by_admin_token_or_404(db, admin_token)
    if pl.get_phase(poll) == pl.PHASE_NOMINATION:
        poll.nomination_end = pl.now()
        db.commit()
    return RedirectResponse(url=f"/admin/{admin_token}", status_code=303)


@app.post("/admin/{admin_token}/end-voting")
def end_voting(admin_token: str, db: Session = Depends(get_db)):
    poll = get_poll_by_admin_token_or_404(db, admin_token)
    if pl.get_phase(poll) in (pl.PHASE_NOMINATION, pl.PHASE_VOTING):
        poll.nomination_end = min(poll.nomination_end, pl.now())
        poll.voting_end = pl.now()
        db.commit()
    return RedirectResponse(url=f"/admin/{admin_token}", status_code=303)


@app.post("/admin/{admin_token}/draw")
def trigger_draw(admin_token: str, db: Session = Depends(get_db)):
    poll = get_poll_by_admin_token_or_404(db, admin_token)
    if pl.get_phase(poll) != pl.PHASE_CLOSED:
        raise HTTPException(400, "A votação ainda não terminou.")
    results = pl.compute_results(db, poll)
    if results.tie_group and not results.draw:
        pl.run_draw(db, poll, results.tie_group, results.slots_needed)
    return RedirectResponse(url=f"/admin/{admin_token}", status_code=303)
