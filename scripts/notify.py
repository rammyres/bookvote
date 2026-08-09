#!/usr/bin/env python3
"""Dispara os e-mails de mudança de fase pendentes (indicações encerradas,
empates, resultado final) e sai. Feito para ser chamado por cron, não para
ficar rodando — assim o envio não depende da vida útil do processo web
(restart, deploy, múltiplos workers) e o agendamento fica visível e
inspecionável (`crontab -l`), em vez de escondido num loop assíncrono.

Uso (dentro do container, via docker compose):
  docker compose exec web python scripts/notify.py

No crontab do host (não dentro do container — cron roda no host, chamando
o container por fora):
  */2 * * * * cd /caminho/do/bookvote && docker compose exec -T web python scripts/notify.py >> /var/log/bookvote-notify.log 2>&1

`-T` desliga a alocação de pseudo-TTY — sem isso, `docker compose exec`
falha rodando via cron (só funciona interativo no terminal).

Precisa de BOOKVOTE_BASE_URL configurada no .env — sem ela, cada execução
só avisa no log e não envia nada (mesmo aviso que antes aparecia no log
do processo web).
"""
import asyncio
import os
import sys

# Permite rodar como `python scripts/notify.py` de qualquer diretório,
# sem precisar instalar o pacote — insere a raiz do projeto (pai de
# scripts/) no sys.path antes de importar `app`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Importar app.main já configura o logging (mesmo formato usado pelo
# processo web) como efeito colateral do import.
from app.main import run_pending_notifications  # noqa: E402


def main() -> None:
    asyncio.run(run_pending_notifications())


if __name__ == "__main__":
    main()