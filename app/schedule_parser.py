"""Lê a imagem de um cronograma de leitura (como as da Taverna dos Leitores)
e devolve as semanas em formato estruturado, usando a API do Gemini.

Só extrai e sugere: o resultado preenche o formulário de /adminlc e o admin
revisa antes de salvar. A validação de verdade continua sendo a do _save.
"""
import json
import logging
import os
from datetime import date
from typing import Optional

import httpx

logger = logging.getLogger("bookvote.schedule_parser")


def _clean_env(raw: str) -> str:
    return raw.strip().strip('"').strip("'").strip()


GEMINI_API_KEY = _clean_env(os.environ.get("GEMINI_API_KEY", ""))
GEMINI_MODEL = _clean_env(os.environ.get("GEMINI_MODEL", "")) or "gemini-2.5-flash"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

CONFIGURED = bool(GEMINI_API_KEY)
MAX_FIELD = 80  # mesmo limite do formulário (stage_from / stage_to)

PROMPT = (
    "A imagem é um cronograma de leitura coletiva de um livro, com uma tabela de semanas. "
    "Extraia UMA entrada por linha da tabela, na ordem em que aparecem. Para cada semana devolva: "
    "start_day, start_month, end_day, end_month (inteiros, lidos da coluna de período, "
    "ex.: '09/11 a 15/11' => 9, 11, 15, 11) e chapter_from / chapter_to com o TEXTO COMPLETO do "
    "primeiro e do último capítulo da semana, incluindo a numeração e o título, exatamente como "
    "escrito (ex.: '1.1 Dia de Santo Elfrico' e '1.12 Imperatriz ou morte'). Não inclua "
    "observações entre parênteses como '(Parte 1 inteira)'. Ignore número de páginas e qualquer "
    "texto fora da tabela. Se um campo estiver ilegível, use null em vez de inventar."
)

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "weeks": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "start_day": {"type": "INTEGER"},
                    "start_month": {"type": "INTEGER"},
                    "end_day": {"type": "INTEGER"},
                    "end_month": {"type": "INTEGER"},
                    "chapter_from": {"type": "STRING", "nullable": True},
                    "chapter_to": {"type": "STRING", "nullable": True},
                },
                "required": ["start_day", "start_month", "end_day", "end_month"],
            },
        }
    },
    "required": ["weeks"],
}


class ScheduleParseError(Exception):
    """Mensagem já pronta para mostrar ao admin."""


def _safe_date(y: int, m: int, d: int) -> Optional[date]:
    try:
        return date(y, m, d)
    except (ValueError, TypeError):
        return None


def resolve_years(weeks: list[dict], reference: Optional[date] = None, today: Optional[date] = None) -> list[dict]:
    """A imagem não traz o ano. Regras: a 1ª semana usa o ano de `reference`
    (início da LC, se já preenchido) ou, sem ele, o ano de hoje — avançando um
    ano se a data já passou há mais de ~6 meses. As demais datas nunca andam
    para trás: se o mês "voltar" (dez → jan), o ano sobe."""
    today = today or date.today()
    out: list[dict] = []
    prev: Optional[date] = None
    for i, w in enumerate(weeks):
        try:
            sd, sm, ed, em = int(w["start_day"]), int(w["start_month"]), int(w["end_day"]), int(w["end_month"])
        except (KeyError, TypeError, ValueError):
            raise ScheduleParseError(f"Não consegui ler as datas da semana {i + 1}.")
        if prev is None:
            base_year = reference.year if reference else today.year
            start = _safe_date(base_year, sm, sd)
            if start and not reference and (today - start).days > 180:
                start = _safe_date(base_year + 1, sm, sd)
        else:
            start = _safe_date(prev.year, sm, sd)
            if start and start < prev:
                start = _safe_date(prev.year + 1, sm, sd)
        if not start:
            raise ScheduleParseError(f"Data inválida na semana {i + 1} ({sd:02d}/{sm:02d}).")
        end = _safe_date(start.year, em, ed)
        if end and end < start:
            end = _safe_date(start.year + 1, em, ed)
        if not end:
            raise ScheduleParseError(f"Data inválida na semana {i + 1} ({ed:02d}/{em:02d}).")
        prev = end
        out.append({
            "start": start.isoformat(),
            "end": end.isoformat(),
            "from": (w.get("chapter_from") or "").strip()[:MAX_FIELD],
            "to": (w.get("chapter_to") or "").strip()[:MAX_FIELD],
        })
    return out


async def parse_schedule_image(data: bytes, mime: str, reference: Optional[date] = None) -> list[dict]:
    if not CONFIGURED:
        raise ScheduleParseError("Leitura automática não configurada (falta GEMINI_API_KEY).")
    import base64

    body = {
        "contents": [{
            "parts": [
                {"text": PROMPT},
                {"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}},
            ]
        }],
        "generationConfig": {
            "temperature": 0,
            # sem "raciocínio" interno: a extração é direta e fica bem mais rápida
            "thinkingConfig": {"thinkingBudget": 0},
            "responseMimeType": "application/json",
            "responseSchema": SCHEMA,
        },
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
        try:
            resp = await client.post(
                GEMINI_URL.format(model=GEMINI_MODEL),
                headers={"x-goog-api-key": GEMINI_API_KEY},
                json=body,
            )
            resp.raise_for_status()
            payload = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.warning("Gemini retornou %s: %s", exc.response.status_code, exc.response.text[:300])
            raise ScheduleParseError("O serviço de leitura da imagem recusou o pedido. Tente de novo ou preencha à mão.")
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Falha ao chamar o Gemini: %s %r", type(exc).__name__, exc)
            if isinstance(exc, httpx.TimeoutException):
                raise ScheduleParseError("A leitura da imagem demorou demais. Tente de novo em instantes.")
            raise ScheduleParseError("Não consegui falar com o serviço de leitura da imagem. Preencha à mão.")

    try:
        text = payload["candidates"][0]["content"]["parts"][0]["text"]
        weeks = json.loads(text).get("weeks") or []
    except (KeyError, IndexError, TypeError, ValueError):
        raise ScheduleParseError("Não consegui interpretar a resposta sobre a imagem. Preencha à mão.")
    if not weeks:
        raise ScheduleParseError("Não encontrei uma tabela de semanas nessa imagem.")
    return resolve_years(weeks, reference)
