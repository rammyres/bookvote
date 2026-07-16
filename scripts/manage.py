#!/usr/bin/env python3
"""Ferramenta de administração de sistema (sysadmin) do bookvote.

NÃO fica exposta na web — só funciona com acesso direto ao servidor/
container. Serve pra listar e apagar enquetes ou sorteios criados por
engano ou só de teste. Apagar é IRREVERSÍVEL: some a enquete/sorteio e
tudo que pertence a ela (livros, votos, inscritos, sorteios de
desempate).

Uso (dentro do container, via docker compose):
  docker compose exec bookvote python scripts/manage.py list-polls
  docker compose exec bookvote python scripts/manage.py list-raffles
  docker compose exec bookvote python scripts/manage.py delete-poll <id>
  docker compose exec bookvote python scripts/manage.py delete-raffle <id>

Fora do Docker (rodando local com o mesmo BOOKVOTE_DATA_DIR do servidor),
funciona igual, direto com `python scripts/manage.py ...`.
"""
import argparse
import os
import sys

# Permite rodar como `python scripts/manage.py` de qualquer diretório,
# sem precisar instalar o pacote — insere a raiz do projeto (pai de
# scripts/) no sys.path antes de importar `app`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal  # noqa: E402
from app.models import Book, DrawLog, Poll, Raffle, RaffleDraw, RaffleEntry, Vote, VoterIdentity  # noqa: E402
from app import poll_logic as pl  # noqa: E402
from app import raffle_logic as rl  # noqa: E402


def _fmt_dt(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else "—"


def list_polls(args: argparse.Namespace) -> None:
    db = SessionLocal()
    try:
        polls = db.query(Poll).order_by(Poll.created_at.desc()).all()
        if not polls:
            print("Nenhuma enquete cadastrada.")
            return
        print(f"{'ID':<10} {'FASE':<10} {'LIVROS':>6} {'VOTOS':>6} {'CRIADA EM':<17} TÍTULO")
        for p in polls:
            phase = pl.get_phase(p)
            n_books = db.query(Book).filter_by(poll_id=p.id).count()
            n_votes = db.query(Vote).filter_by(poll_id=p.id, nullified=False).count()
            print(f"{p.id:<10} {phase:<10} {n_books:>6} {n_votes:>6} {_fmt_dt(p.created_at):<17} {p.title}")
    finally:
        db.close()


def list_raffles(args: argparse.Namespace) -> None:
    db = SessionLocal()
    try:
        raffles = db.query(Raffle).order_by(Raffle.created_at.desc()).all()
        if not raffles:
            print("Nenhum sorteio cadastrado.")
            return
        print(f"{'ID':<10} {'FASE':<8} {'INSCRITOS':>9} {'CRIADO EM':<17} TÍTULO")
        for r in raffles:
            phase = rl.get_phase(r)
            n_entries = db.query(RaffleEntry).filter_by(raffle_id=r.id).count()
            print(f"{r.id:<10} {phase:<8} {n_entries:>9} {_fmt_dt(r.created_at):<17} {r.title}")
    finally:
        db.close()


def _confirm(expected_id: str, skip: bool) -> bool:
    if skip:
        return True
    typed = input(f"Digite o ID ({expected_id}) de novo para confirmar, ou Enter para cancelar: ")
    return typed.strip() == expected_id


def delete_poll(args: argparse.Namespace) -> None:
    db = SessionLocal()
    try:
        poll = db.query(Poll).filter(Poll.id == args.poll_id).first()
        if not poll:
            print(f"Enquete '{args.poll_id}' não encontrada.")
            sys.exit(1)

        n_books = db.query(Book).filter_by(poll_id=poll.id).count()
        n_votes = db.query(Vote).filter_by(poll_id=poll.id).count()
        n_draws = db.query(DrawLog).filter_by(poll_id=poll.id).count()
        n_identities = db.query(VoterIdentity).filter_by(poll_id=poll.id).count()

        print(f"Enquete: {poll.title!r} (id={poll.id}, fase={pl.get_phase(poll)})")
        print(f"Vai apagar junto: {n_books} livro(s), {n_votes} voto(s), {n_draws} sorteio(s) de desempate,")
        print(f"{n_identities} identidade(s) de votante. ISSO NÃO TEM VOLTA.")

        if not _confirm(poll.id, args.yes):
            print("Cancelado.")
            sys.exit(1)

        # VoterIdentity não tem relationship/cascade configurado no model —
        # precisa apagar manualmente antes, ou fica órfão referenciando um
        # poll_id que não existe mais.
        db.query(VoterIdentity).filter_by(poll_id=poll.id).delete()
        db.delete(poll)  # cascade="all, delete-orphan" cuida de books/votes/draws
        db.commit()
        print("Enquete apagada.")
    finally:
        db.close()


def delete_raffle(args: argparse.Namespace) -> None:
    db = SessionLocal()
    try:
        raffle = db.query(Raffle).filter(Raffle.id == args.raffle_id).first()
        if not raffle:
            print(f"Sorteio '{args.raffle_id}' não encontrado.")
            sys.exit(1)

        n_entries = db.query(RaffleEntry).filter_by(raffle_id=raffle.id).count()
        n_draws = db.query(RaffleDraw).filter_by(raffle_id=raffle.id).count()

        print(f"Sorteio: {raffle.title!r} (id={raffle.id}, fase={rl.get_phase(raffle)})")
        print(f"Vai apagar junto: {n_entries} inscrito(s), {n_draws} sorteio(s) já realizado(s). ISSO NÃO TEM VOLTA.")

        if not _confirm(raffle.id, args.yes):
            print("Cancelado.")
            sys.exit(1)

        db.delete(raffle)  # cascade="all, delete-orphan" cuida de entries/draws
        db.commit()
        print("Sorteio apagado.")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-polls", help="Lista todas as enquetes").set_defaults(func=list_polls)
    sub.add_parser("list-raffles", help="Lista todos os sorteios").set_defaults(func=list_raffles)

    p_del_poll = sub.add_parser("delete-poll", help="Apaga uma enquete e tudo que pertence a ela")
    p_del_poll.add_argument("poll_id")
    p_del_poll.add_argument("--yes", action="store_true", help="Pula a confirmação interativa")
    p_del_poll.set_defaults(func=delete_poll)

    p_del_raffle = sub.add_parser("delete-raffle", help="Apaga um sorteio e tudo que pertence a ele")
    p_del_raffle.add_argument("raffle_id")
    p_del_raffle.add_argument("--yes", action="store_true", help="Pula a confirmação interativa")
    p_del_raffle.set_defaults(func=delete_raffle)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()