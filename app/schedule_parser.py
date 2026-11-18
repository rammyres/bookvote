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


def _fallback_env(name: str, default: str) -> str:
    """Modelo de reserva: vazio = padrão; "none" desliga essa posição."""
    raw = _clean_env(os.environ.get(name, ""))
    return "" if raw.lower() == "none" else (raw or default)


GEMINI_API_KEY = _clean_env(os.environ.get("GEMINI_API_KEY", ""))
GEMINI_MODEL = _clean_env(os.environ.get("GEMINI_MODEL", "")) or "gemini-3.8-flash"
# Cadeia de reserva, tentada em ordem se o modelo anterior falhar (alta
# demanda, timeout, erro do servidor, modelo aposentado/inexistente ou
# parâmetro não aceito). Os nomes de modelo do Gemini giram rápido — o
# padrão antigo (gemini-2.5-flash) já saiu do ar —, então tudo é configurável
# pelo .env, sem mexer no código.
GEMINI_FALLBACK_MODEL = _fallback_env("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash")        # reserva 1
GEMINI_FALLBACK_MODEL_2 = _fallback_env("GEMINI_FALLBACK_MODEL_2", "gemini-3.5-flash-lite")  # reserva 2
THINKING_LEVEL = _clean_env(os.environ.get("GEMINI_THINKING_LEVEL", "")).lower() or "low"
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
    "texto fora da tabela. Se a semana tiver um único capítulo ou conto, preencha só chapter_from e deixe chapter_to como null (nunca use '-'). Se um campo estiver ilegível, use null em vez de inventar."
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


def _field(v) -> str:
    v = (v or "").strip()
    return "" if v in ("-", "\u2013", "\u2014") else v[:MAX_FIELD]


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
            "from": _field(w.get("chapter_from")),
            "to": _field(w.get("chapter_to")),
        })
    return out


def _generation_config(model: str) -> dict:
    cfg = {"responseMimeType": "application/json", "responseSchema": SCHEMA}
    if model.startswith("gemini-2"):
        # família 2.x: orçamento de raciocínio numérico (0 = desligado)
        cfg["temperature"] = 0
        cfg["thinkingConfig"] = {"thinkingBudget": 0}
    else:
        # família 3.x+: nível de raciocínio; sem temperature (descontinuado)
        cfg["thinkingConfig"] = {"thinkingLevel": THINKING_LEVEL}
    return cfg


def model_chain() -> list[str]:
    """Principal + reservas, sem vazios nem repetidos (ordem preservada)."""
    chain: list[str] = []
    for m in (GEMINI_MODEL, GEMINI_FALLBACK_MODEL, GEMINI_FALLBACK_MODEL_2):
        if m and m not in chain:
            chain.append(m)
    return chain


class _Retryable(Exception):
    """Falha que justifica tentar o próximo modelo da cadeia. `transient` é
    True para instabilidade (demanda, timeout, 5xx, resposta ilegível) e False
    para recusa do modelo (400/404: aposentado, inexistente, parâmetro não
    aceito) — só serve para escolher a mensagem final."""

    def __init__(self, transient: bool = True):
        super().__init__()
        self.transient = transient


# Troca de transporte só para testes (httpx.MockTransport).
_TRANSPORT: Optional[httpx.AsyncBaseTransport] = None


async def _call_model(client: httpx.AsyncClient, model: str, parts: list) -> list[dict]:
    cfg = _generation_config(model)
    payload = None
    for attempt in (1, 2):
        body = {"contents": [{"parts": parts}], "generationConfig": cfg}
        try:
            resp = await client.post(
                GEMINI_URL.format(model=model), headers={"x-goog-api-key": GEMINI_API_KEY}, json=body
            )
            resp.raise_for_status()
            payload = resp.json()
            break
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            text = exc.response.text
            logger.warning("Gemini (%s) retornou %s: %s", model, code, text[:300])
            low = text.lower()
            if code == 400 and ("api_key_invalid" in low or "api key not valid" in low):
                # a chave vale para todos os modelos: não adianta tentar os outros
                raise ScheduleParseError("A chave da API de leitura da imagem (GEMINI_API_KEY) foi recusada. Avise quem administra o servidor.")
            if code == 400 and attempt == 1 and "thinking" in low and "thinkingConfig" in cfg:
                # cada modelo aceita um conjunto diferente de níveis/orçamentos de
                # raciocínio: tenta o mesmo modelo de novo, sem esse parâmetro
                logger.warning("Gemini (%s) não aceitou thinkingConfig; repetindo sem ele.", model)
                cfg = {k: v for k, v in cfg.items() if k != "thinkingConfig"}
                continue
            if code in (408, 429, 500, 502, 503, 504):
                raise _Retryable(transient=True)
            if code in (400, 404):  # modelo recusou o pedido ou não existe mais: vale o próximo
                raise _Retryable(transient=False)
            raise ScheduleParseError("O serviço de leitura da imagem recusou o pedido. Tente de novo ou preencha à mão.")
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Falha ao chamar o Gemini (%s): %s %r", model, type(exc).__name__, exc)
            if isinstance(exc, httpx.HTTPError):  # timeout, conexão: vale tentar o próximo
                raise _Retryable(transient=True)
            raise ScheduleParseError("Resposta inválida do serviço de leitura da imagem. Preencha à mão.")
    if payload is None:
        raise _Retryable(transient=True)

    try:
        text = payload["candidates"][0]["content"]["parts"][0]["text"]
        weeks = json.loads(text).get("weeks") or []
    except (KeyError, IndexError, TypeError, ValueError):
        logger.warning("Gemini (%s) devolveu resposta não interpretável: %s", model, str(payload)[:300])
        raise _Retryable(transient=True)
    if not weeks:
        raise ScheduleParseError("Não encontrei uma tabela de semanas nessa imagem.")
    return weeks


async def parse_schedule_image(data: bytes, mime: str, reference: Optional[date] = None) -> list[dict]:
    if not CONFIGURED:
        raise ScheduleParseError("Leitura automática não configurada (falta GEMINI_API_KEY).")
    import base64

    parts = [
        {"text": PROMPT},
        {"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}},
    ]
    models = model_chain()

    weeks = None
    saw_transient = False
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0), transport=_TRANSPORT) as client:
        for n, model in enumerate(models):
            try:
                weeks = await _call_model(client, model, parts)
                if n:
                    logger.info("Cronograma lido com o modelo de reserva %d (%s).", n, model)
                break
            except _Retryable as exc:
                saw_transient = saw_transient or exc.transient
                if n + 1 < len(models):
                    logger.warning("Falha com %s; tentando a reserva %d (%s).", model, n + 1, models[n + 1])
    if weeks is None:
        if saw_transient:
            raise ScheduleParseError("O serviço de leitura da imagem está indisponível ou sobrecarregado. Tente em instantes ou preencha à mão.")
        logger.error("Todos os modelos recusaram o pedido (%s) — confira os nomes em GEMINI_MODEL e GEMINI_FALLBACK_MODEL*.", ", ".join(models))
        raise ScheduleParseError("O serviço de leitura da imagem recusou o pedido. Tente de novo ou preencha à mão.")
    return resolve_years(weeks, reference)
