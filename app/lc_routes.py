"""Rotas de Leituras Coletivas: páginas públicas (/lc...) e administração
(/adminlc...). Montado no main.py via build_router(templates, limiter)."""
import os
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from . import lc_logic as lc
from .database import get_db
from .models import CollectiveReading, LCAdmin, LCCategory

MSGS = {
    "created": "Leitura cadastrada.",
    "updated": "Leitura atualizada.",
    "deleted": "Leitura apagada.",
    "state": "Estado atualizado.",
    "grouping": "Agrupamento atualizado.",
    "category_created": "Categoria criada.",
    "category_renamed": "Categoria renomeada.",
    "category_deleted": "Categoria apagada. As leituras dela ficaram sem categoria.",
    "cover_fallback": "Leitura salva, mas não consegui baixar a capa do Google — usando o link direto dela por enquanto.",
    "admin_created": "Administrador criado.",
    "admin_toggled": "Administrador atualizado.",
    "password_set": "Senha redefinida.",
    "password_changed": "Sua senha foi alterada.",
    "logged_out": "Você saiu.",
}


def _cookie_secure() -> bool:
    return os.environ.get("BOOKVOTE_COOKIE_SECURE", "true").lower() == "true"


def br_date(value: Optional[date]) -> str:
    return value.strftime("%d/%m/%Y") if value else ""


def build_router(templates, limiter) -> APIRouter:
    router = APIRouter()
    templates.env.filters["br_date"] = br_date
    templates.env.globals["LC_STATE_LABELS"] = lc.STATE_LABELS
    templates.env.globals["LC_GROUP_LABELS"] = lc.GROUP_LABELS
    templates.env.globals["LC_GROUP_PAGES"] = lc.GROUP_PAGES
    templates.env.globals["LC_GROUP_PAGE_LABELS"] = lc.GROUP_PAGE_LABELS
    templates.env.globals["group_readings"] = lc.group_readings

    # ------------------------------------------------------------ dependências

    def current_admin(request: Request, db: Session = Depends(get_db)) -> Optional[LCAdmin]:
        return lc.admin_from_token(db, request.cookies.get(lc.SESSION_COOKIE))

    def require_admin(admin: Optional[LCAdmin] = Depends(current_admin)) -> LCAdmin:
        if not admin:
            raise HTTPException(status_code=303, headers={"Location": "/adminlc"})
        return admin

    def require_main(admin: LCAdmin = Depends(require_admin)) -> LCAdmin:
        if not admin.is_main:
            raise HTTPException(status_code=403, detail="Só o administrador principal pode fazer isso.")
        return admin

    def go(path: str, msg: Optional[str] = None) -> RedirectResponse:
        return RedirectResponse(f"{path}?msg={msg}" if msg else path, status_code=303)

    def render(request: Request, name: str, status_code: int = 200, **ctx):
        ctx["request"] = request
        ctx.setdefault("msg", MSGS.get(request.query_params.get("msg", "")))
        return templates.TemplateResponse(name, ctx, status_code=status_code)

    # ---------------------------------------------------------------- públicas

    @router.get("/lc", response_class=HTMLResponse)
    def public_list(request: Request, db: Session = Depends(get_db)):
        groups = lc.grouped_readings(db)
        return render(request, "lc_public.html", groups=groups, mode="all", group_mode=lc.group_mode_for(db, lc.PAGE_MAIN))

    @router.get("/lc/concluidas", response_class=HTMLResponse)
    def public_done(request: Request, db: Session = Depends(get_db)):
        groups = lc.grouped_readings(db)
        return render(request, "lc_public.html", groups=groups, mode="done", group_mode=lc.group_mode_for(db, lc.PAGE_DONE))

    @router.get("/lc/{reading_id}", response_class=HTMLResponse)
    def public_detail(request: Request, reading_id: str, db: Session = Depends(get_db)):
        lc.sync_reading_states(db)
        reading = db.query(CollectiveReading).filter(CollectiveReading.id == reading_id).first()
        if not reading:
            raise HTTPException(status_code=404, detail="Leitura coletiva não encontrada.")
        return render(request, "lc_detail.html", lc=reading)

    # ------------------------------------------------------------ login/logout

    @router.get("/adminlc", response_class=HTMLResponse)
    def adminlc_home(
        request: Request,
        admin: Optional[LCAdmin] = Depends(current_admin),
        db: Session = Depends(get_db),
    ):
        if not admin:
            has_admin = db.query(LCAdmin).count() > 0
            return render(request, "adminlc_login.html", has_admin=has_admin, error=None, username="")
        groups = lc.grouped_readings(db)
        return render(
            request, "adminlc_dashboard.html",
            admin=admin, groups=groups, today=lc.local_today(),
            group_mode=lc.get_group_mode(db), group_pages=lc.get_group_pages(db),
        )

    @router.post("/adminlc/login")
    @limiter.limit("10/minute")
    async def adminlc_login(request: Request, db: Session = Depends(get_db)):
        form = await request.form()
        username = str(form.get("username", "")).strip().lower()
        password = str(form.get("password", ""))
        admin = lc.authenticate(db, username, password)
        if not admin:
            return render(
                request, "adminlc_login.html", status_code=401,
                has_admin=True, error="Usuário ou senha incorretos.", username=username,
            )
        resp = go("/adminlc")
        resp.set_cookie(
            lc.SESSION_COOKIE, lc.make_session_token(admin),
            max_age=lc.SESSION_MAX_AGE, httponly=True, samesite="lax", secure=_cookie_secure(),
        )
        return resp

    @router.post("/adminlc/logout")
    def adminlc_logout():
        resp = go("/adminlc", "logged_out")
        resp.delete_cookie(lc.SESSION_COOKIE)
        return resp

    # ---------------------------------------------------- cadastro de leituras

    FIELDS = ("title", "author", "isbn", "description", "category_id", "state", "start_date", "end_date",
              "schedule_text", "google_cover_url")

    def _form_values(form) -> dict:
        v = {f: str(form.get(f, "") or "").strip() for f in FIELDS}
        v["state"] = v["state"] or lc.STATE_PLANNED
        v["remove_cover"] = bool(form.get("remove_cover"))
        return v

    def _parse_date(raw: str) -> Optional[date]:
        return date.fromisoformat(raw) if raw else None

    def _validate(v: dict) -> tuple[list[str], dict]:
        errors: list[str] = []
        parsed: dict = {}
        if not v["title"]:
            errors.append("Informe o título.")
        for field, label, limit in (
            ("title", "Título", 200), ("author", "Autor", 200), ("isbn", "ISBN", 20),
            ("description", "Descrição", 4000), ("schedule_text", "Cronograma", 4000),
        ):
            if len(v[field]) > limit:
                errors.append(f"{label} passa de {limit} caracteres.")
        if v["state"] not in lc.STATES:
            errors.append("Estado inválido.")
        for key, label in (("start_date", "início"), ("end_date", "fim")):
            try:
                parsed[key] = _parse_date(v[key])
            except ValueError:
                parsed[key] = None
                errors.append(f"Data de {label} inválida.")
        # Prevista pode ficar sem data ("data a definir"); em andamento precisa
        # de início (é o que a home mostra como "Desde ...").
        if v["state"] == lc.STATE_ONGOING and not parsed.get("start_date") and not errors:
            errors.append("Informe a data de início — ou deixe a leitura como Prevista, sem data.")
        if parsed.get("start_date") and parsed.get("end_date") and parsed["end_date"] < parsed["start_date"]:
            errors.append("A data de fim não pode ser anterior à de início.")
        return errors, parsed

    async def _apply_cover(form, v: dict, reading: Optional[CollectiveReading]):
        """Decide a capa final. Devolve (cover_filename, cover_url, arquivo_antigo_a_apagar,
        erro, aviso). Prioridade: upload > escolha do Google > remover > manter."""
        old_file = reading.cover_filename if reading else None
        cur_file = old_file
        cur_url = reading.cover_url if reading else None

        upload = form.get("cover_file")
        if isinstance(upload, UploadFile) and upload.filename:
            data = await upload.read(lc.MAX_COVER_BYTES + 1)
            name = lc.save_cover_bytes(data) if data else None
            if not name:
                return cur_file, cur_url, None, "Capa inválida: envie JPG, PNG, WEBP ou GIF de até 5 MB.", None
            return name, None, old_file, None, None

        if v["google_cover_url"]:
            name = await lc.download_google_cover(v["google_cover_url"])
            if name:
                return name, None, old_file, None, None
            if lc.is_google_image_url(v["google_cover_url"]):
                return None, v["google_cover_url"], old_file, None, "cover_fallback"
            return cur_file, cur_url, None, None, None

        if v["remove_cover"]:
            return None, None, old_file, None, None

        return cur_file, cur_url, None, None, None

    async def _save(request: Request, db: Session, admin: LCAdmin, reading: Optional[CollectiveReading]):
        form = await request.form()
        v = _form_values(form)
        errors, parsed = _validate(v)
        if v["category_id"] and not db.query(LCCategory).filter(LCCategory.id == v["category_id"]).first():
            errors.append("Categoria inválida — escolha uma da lista.")
        cover_file, cover_url, old_file, cover_err, notice = (None, None, None, None, None)
        if not errors:
            cover_file, cover_url, old_file, cover_err, notice = await _apply_cover(form, v, reading)
            if cover_err:
                errors.append(cover_err)
        if errors:
            return render(
                request, "adminlc_form.html", status_code=400,
                admin=admin, lc=reading, values=v, errors=errors,
                categories=lc.list_categories(db),
            )

        creating = reading is None
        if creating:
            reading = CollectiveReading(created_by=admin.id)
            db.add(reading)
        reading.title = v["title"]
        reading.author = v["author"] or None
        reading.isbn = v["isbn"] or None
        reading.category_id = v["category_id"] or None
        reading.description = v["description"] or None
        reading.state = v["state"]
        reading.start_date = parsed["start_date"]
        reading.end_date = parsed["end_date"]
        reading.schedule_text = v["schedule_text"] or None
        reading.cover_filename = cover_file
        reading.cover_url = cover_url
        db.commit()
        lc.delete_cover_file(old_file if old_file != cover_file else None)
        lc.sync_reading_states(db)  # data de início já passada → em andamento
        return go("/adminlc", notice or ("created" if creating else "updated"))

    @router.get("/adminlc/lc/new", response_class=HTMLResponse)
    def new_reading_form(
        request: Request, admin: LCAdmin = Depends(require_admin), db: Session = Depends(get_db)
    ):
        values = {f: "" for f in FIELDS}
        values["state"] = lc.STATE_PLANNED
        values["remove_cover"] = False
        return render(
            request, "adminlc_form.html", admin=admin, lc=None, values=values, errors=[],
            categories=lc.list_categories(db),
        )

    @router.post("/adminlc/lc/new")
    @limiter.limit("30/minute")
    async def create_reading(
        request: Request, admin: LCAdmin = Depends(require_admin), db: Session = Depends(get_db)
    ):
        return await _save(request, db, admin, None)

    def _get_reading(db: Session, reading_id: str) -> CollectiveReading:
        r = db.query(CollectiveReading).filter(CollectiveReading.id == reading_id).first()
        if not r:
            raise HTTPException(status_code=404, detail="Leitura coletiva não encontrada.")
        return r

    @router.get("/adminlc/lc/{reading_id}/edit", response_class=HTMLResponse)
    def edit_reading_form(
        request: Request, reading_id: str,
        admin: LCAdmin = Depends(require_admin), db: Session = Depends(get_db),
    ):
        lc.sync_reading_states(db)
        r = _get_reading(db, reading_id)
        values = {
            "title": r.title, "author": r.author or "", "isbn": r.isbn or "",
            "description": r.description or "", "category_id": r.category_id or "", "state": r.state,
            "start_date": r.start_date.isoformat() if r.start_date else "",
            "end_date": r.end_date.isoformat() if r.end_date else "",
            "schedule_text": r.schedule_text or "", "google_cover_url": "", "remove_cover": False,
        }
        return render(
            request, "adminlc_form.html", admin=admin, lc=r, values=values, errors=[],
            categories=lc.list_categories(db),
        )

    @router.post("/adminlc/lc/{reading_id}/edit")
    @limiter.limit("30/minute")
    async def edit_reading(
        request: Request, reading_id: str,
        admin: LCAdmin = Depends(require_admin), db: Session = Depends(get_db),
    ):
        return await _save(request, db, admin, _get_reading(db, reading_id))

    @router.post("/adminlc/lc/{reading_id}/state")
    async def set_reading_state(
        request: Request, reading_id: str,
        admin: LCAdmin = Depends(require_admin), db: Session = Depends(get_db),
    ):
        form = await request.form()
        new_state = str(form.get("state", ""))
        # atalhos do painel: iniciar (planned→ongoing), concluir (ongoing→done)
        # e reabrir (done→ongoing)
        if new_state not in (lc.STATE_DONE, lc.STATE_ONGOING):
            raise HTTPException(status_code=400, detail="Estado inválido.")
        r = _get_reading(db, reading_id)
        if new_state == lc.STATE_ONGOING and not r.start_date:
            r.start_date = lc.local_today()  # prevista sem data: o início é hoje
        r.state = new_state
        db.commit()
        return go("/adminlc", "state")

    @router.post("/adminlc/grouping")
    async def set_grouping(
        request: Request, admin: LCAdmin = Depends(require_main), db: Session = Depends(get_db)
    ):
        form = await request.form()
        mode = str(form.get("group_by", ""))
        if mode not in lc.GROUP_MODES:
            raise HTTPException(status_code=400, detail="Agrupamento inválido.")
        pages = [str(p) for p in form.getlist("group_pages")]
        if any(p not in lc.GROUP_PAGES for p in pages):
            raise HTTPException(status_code=400, detail="Página inválida.")
        lc.save_group_settings(db, mode, pages)
        return go("/adminlc", "grouping")

    # ------------------------------------------------------------- categorias
    # Só o administrador principal cria/renomeia/apaga categorias; todos os
    # administradores escolhem entre elas ao cadastrar/editar uma leitura.

    def _categories_page(request: Request, db: Session, admin: LCAdmin, error=None, error_for=None, status_code=200):
        return render(
            request, "adminlc_categories.html", status_code=status_code,
            admin=admin, categories=lc.list_categories(db), counts=lc.category_counts(db),
            error=error, error_for=error_for,
        )

    def _clean_category_name(raw) -> str:
        return " ".join(str(raw or "").split())

    @router.get("/adminlc/categorias", response_class=HTMLResponse)
    def categories_page(
        request: Request, admin: LCAdmin = Depends(require_main), db: Session = Depends(get_db)
    ):
        return _categories_page(request, db, admin)

    @router.post("/adminlc/categorias/new")
    @limiter.limit("30/minute")
    async def create_category(
        request: Request, admin: LCAdmin = Depends(require_main), db: Session = Depends(get_db)
    ):
        form = await request.form()
        name = _clean_category_name(form.get("name"))
        if not name:
            return _categories_page(request, db, admin, "Informe o nome da categoria.", "new", 400)
        if len(name) > lc.MAX_CATEGORY_LEN:
            return _categories_page(request, db, admin, f"O nome passa de {lc.MAX_CATEGORY_LEN} caracteres.", "new", 400)
        if lc.category_name_taken(db, name):
            return _categories_page(request, db, admin, "Já existe uma categoria com esse nome.", "new", 400)
        db.add(LCCategory(name=name))
        db.commit()
        return go("/adminlc/categorias", "category_created")

    def _get_category(db: Session, category_id: str) -> LCCategory:
        c = db.query(LCCategory).filter(LCCategory.id == category_id).first()
        if not c:
            raise HTTPException(status_code=404, detail="Categoria não encontrada.")
        return c

    @router.post("/adminlc/categorias/{category_id}/rename")
    @limiter.limit("30/minute")
    async def rename_category(
        request: Request, category_id: str,
        admin: LCAdmin = Depends(require_main), db: Session = Depends(get_db),
    ):
        cat = _get_category(db, category_id)
        form = await request.form()
        name = _clean_category_name(form.get("name"))
        if not name:
            return _categories_page(request, db, admin, "Informe o nome da categoria.", cat.id, 400)
        if len(name) > lc.MAX_CATEGORY_LEN:
            return _categories_page(request, db, admin, f"O nome passa de {lc.MAX_CATEGORY_LEN} caracteres.", cat.id, 400)
        if lc.category_name_taken(db, name, exclude_id=cat.id):
            return _categories_page(request, db, admin, "Já existe uma categoria com esse nome.", cat.id, 400)
        cat.name = name
        db.commit()
        return go("/adminlc/categorias", "category_renamed")

    @router.post("/adminlc/categorias/{category_id}/delete")
    def delete_category(
        category_id: str, admin: LCAdmin = Depends(require_main), db: Session = Depends(get_db)
    ):
        lc.delete_category(db, _get_category(db, category_id))
        return go("/adminlc/categorias", "category_deleted")

    @router.post("/adminlc/lc/{reading_id}/delete")
    def delete_reading(
        reading_id: str, admin: LCAdmin = Depends(require_admin), db: Session = Depends(get_db)
    ):
        r = _get_reading(db, reading_id)
        old_file = r.cover_filename
        db.delete(r)
        db.commit()
        lc.delete_cover_file(old_file)
        return go("/adminlc", "deleted")

    # ------------------------------------------------------- administradores

    @router.get("/adminlc/admins", response_class=HTMLResponse)
    def admins_page(
        request: Request, admin: LCAdmin = Depends(require_admin), db: Session = Depends(get_db)
    ):
        admins = db.query(LCAdmin).order_by(LCAdmin.is_main.desc(), LCAdmin.created_at.asc()).all()
        return render(request, "adminlc_admins.html", admin=admin, admins=admins, error=None, error_for=None)

    def _admins_error(request: Request, db: Session, admin: LCAdmin, where: str, error: str):
        admins = db.query(LCAdmin).order_by(LCAdmin.is_main.desc(), LCAdmin.created_at.asc()).all()
        return render(
            request, "adminlc_admins.html", status_code=400,
            admin=admin, admins=admins, error=error, error_for=where,
        )

    @router.post("/adminlc/admins/new")
    @limiter.limit("10/minute")
    async def create_lc_admin(
        request: Request, admin: LCAdmin = Depends(require_main), db: Session = Depends(get_db)
    ):
        form = await request.form()
        username = str(form.get("username", "")).strip().lower()
        display_name = str(form.get("display_name", "")).strip()[:80]
        password = str(form.get("password", ""))
        if not lc.valid_username(username):
            return _admins_error(
                request, db, admin, "new",
                "Usuário: 3 a 32 caracteres, só letras minúsculas, números, ponto, hífen e sublinhado.",
            )
        if db.query(LCAdmin).filter(LCAdmin.username == username).first():
            return _admins_error(request, db, admin, "new", "Esse usuário já existe.")
        if len(password) < lc.MIN_PASSWORD_LEN:
            return _admins_error(
                request, db, admin, "new", f"A senha precisa ter pelo menos {lc.MIN_PASSWORD_LEN} caracteres."
            )
        lc.create_admin(db, username, password, display_name=display_name)
        return go("/adminlc/admins", "admin_created")

    @router.post("/adminlc/admins/{admin_id}/toggle")
    def toggle_lc_admin(
        admin_id: str, admin: LCAdmin = Depends(require_main), db: Session = Depends(get_db)
    ):
        target = db.query(LCAdmin).filter(LCAdmin.id == admin_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="Administrador não encontrado.")
        if target.is_main:
            raise HTTPException(status_code=400, detail="O administrador principal não pode ser desativado.")
        target.active = not target.active
        db.commit()
        return go("/adminlc/admins", "admin_toggled")

    @router.post("/adminlc/admins/{admin_id}/password")
    @limiter.limit("10/minute")
    async def reset_lc_admin_password(
        request: Request, admin_id: str,
        admin: LCAdmin = Depends(require_main), db: Session = Depends(get_db),
    ):
        target = db.query(LCAdmin).filter(LCAdmin.id == admin_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="Administrador não encontrado.")
        if target.is_main:
            raise HTTPException(status_code=400, detail="Use \"Alterar minha senha\" para a sua própria conta.")
        form = await request.form()
        password = str(form.get("password", ""))
        if len(password) < lc.MIN_PASSWORD_LEN:
            return _admins_error(
                request, db, admin, target.id,
                f"A senha precisa ter pelo menos {lc.MIN_PASSWORD_LEN} caracteres.",
            )
        target.password_hash = lc.hash_password(password)  # derruba as sessões dele
        db.commit()
        return go("/adminlc/admins", "password_set")

    @router.post("/adminlc/account/password")
    @limiter.limit("10/minute")
    async def change_own_password(
        request: Request, admin: LCAdmin = Depends(require_admin), db: Session = Depends(get_db)
    ):
        form = await request.form()
        current = str(form.get("current_password", ""))
        new = str(form.get("new_password", ""))
        confirm = str(form.get("confirm_password", ""))
        if not lc.verify_password(current, admin.password_hash):
            return _admins_error(request, db, admin, "me", "Senha atual incorreta.")
        if len(new) < lc.MIN_PASSWORD_LEN:
            return _admins_error(
                request, db, admin, "me", f"A nova senha precisa ter pelo menos {lc.MIN_PASSWORD_LEN} caracteres."
            )
        if new != confirm:
            return _admins_error(request, db, admin, "me", "A confirmação não confere com a nova senha.")
        admin.password_hash = lc.hash_password(new)
        db.commit()
        # o fingerprint da senha mudou: reemite o cookie desta sessão
        resp = go("/adminlc/admins", "password_changed")
        resp.set_cookie(
            lc.SESSION_COOKIE, lc.make_session_token(admin),
            max_age=lc.SESSION_MAX_AGE, httponly=True, samesite="lax", secure=_cookie_secure(),
        )
        return resp

    return router
