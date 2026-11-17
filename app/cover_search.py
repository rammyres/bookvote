"""Sugestão de capas por fontes públicas, a partir do ISBN (e do título/autor
quando não há ISBN): Open Library, BrasilAPI (que consulta CBL, Mercado
Editorial etc.) e Google Books.

Princípios:
  - O navegador NUNCA manda URL de imagem. Cada candidata vira um *token
    assinado* com só "fonte + chave" (ISBN ou id de capa da Open Library); ao
    salvar, o servidor remonta o endereço sozinho. Assim não dá para usar o
    formulário para fazer o servidor baixar um endereço escolhido por quem
    digita (SSRF). A única URL que não é fixa vem do campo `cover_url` da
    BrasilAPI — por isso todo download exige https e host público, a cada
    redirecionamento.
  - Cada candidata é baixada e validada aqui (formato pela assinatura do
    arquivo, tamanho, largura mínima): placeholders em branco e "sem capa" de
    1 pixel não chegam à interface.
  - Todas as consultas saem do IP do servidor, então os limites das APIs
    valem para o clube inteiro; por isso há cache curto em memória.
"""
import asyncio
import hashlib
import ipaddress
import logging
import re
import socket
import struct
import time
import unicodedata
from collections import OrderedDict
from typing import Optional
from urllib.parse import quote, urljoin, urlparse

import httpx
from itsdangerous import BadSignature, URLSafeSerializer

from . import lc_logic as lcl
from .book_search import search_books
from .media import sniff_image_ext
from .security import SECRET_KEY

logger = logging.getLogger("bookvote.covers")

_serializer = URLSafeSerializer(SECRET_KEY, salt="bookvote-cover-candidate")

SOURCE_LABELS = {
    "ol": "Open Library",
    "olid": "Open Library",
    "br": "BrasilAPI",
    "gb": "Google Books",
}

MAX_IMAGE_BYTES = 3 * 1024 * 1024   # capas costumam ter poucas centenas de KB
MIN_IMAGE_BYTES = 3 * 1024          # abaixo disso é placeholder / "sem capa"
MIN_WIDTH = 100                     # px; imagens de 1x1 e miniaturas minúsculas saem
MAX_CANDIDATES = 6
MAX_REDIRECTS = 5
TIMEOUT = httpx.Timeout(4.0, connect=3.0)
USER_AGENT = "bookvote/1.0 (clube de leitura; sugestao de capas)"

# Troca de transporte só para testes (httpx.MockTransport).
_TRANSPORT: Optional[httpx.AsyncBaseTransport] = None

_KEY_RE = re.compile(r"^[0-9]{1,13}$|^[0-9]{9}X$")


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}, follow_redirects=False, transport=_TRANSPORT
    )


# ------------------------------------------------------------------- ISBN

def normalize_isbn(raw: str) -> Optional[str]:
    """ISBN-10 (pode terminar em X) ou ISBN-13, sem hífens/espaços; None se
    não tiver esse formato. Não valida o dígito verificador (o Google Books
    às vezes devolve ISBNs com erro e a consulta ainda acha a capa)."""
    value = re.sub(r"[\s-]", "", raw or "").upper()
    return value if re.fullmatch(r"\d{9}[\dX]|\d{13}", value) else None


def _fold(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", (text or "").casefold()) if not unicodedata.combining(c)
    ).strip()


# ----------------------------------------------------------- tamanho/forma

def image_size(data: bytes) -> Optional[tuple[int, int]]:
    """(largura, altura) lendo o cabeçalho de PNG, GIF e JPEG — sem
    dependência de Pillow. None para WEBP (aceito sem checar dimensões) ou
    arquivo que não deu para interpretar."""
    ext = sniff_image_ext(data)
    try:
        if ext == "png" and len(data) >= 24:
            w, h = struct.unpack(">II", data[16:24])
            return w, h
        if ext == "gif" and len(data) >= 10:
            w, h = struct.unpack("<HH", data[6:10])
            return w, h
        if ext == "jpg":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker == 0xFF:  # preenchimento
                    i += 1
                    continue
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                seg = struct.unpack(">H", data[i + 2 : i + 4])[0]
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack(">HH", data[i + 5 : i + 9])
                    return w, h
                i += 2 + seg
    except struct.error:
        return None
    return None


def acceptable_image(data: Optional[bytes]) -> bool:
    if not data or len(data) < MIN_IMAGE_BYTES or len(data) > MAX_IMAGE_BYTES:
        return False
    if not sniff_image_ext(data):
        return False
    size = image_size(data)
    return size is None or size[0] >= MIN_WIDTH


# ------------------------------------------------------- download seguro

async def is_public_host(host: str) -> bool:
    """True só se TODOS os endereços do host forem públicos (bloqueia
    localhost, redes privadas, link-local/metadata de nuvem etc.)."""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return False
    try:
        return bool(infos) and all(ipaddress.ip_address(i[4][0]).is_global for i in infos)
    except ValueError:
        return False


async def _get_image(client: httpx.AsyncClient, url: str) -> Optional[bytes]:
    """Baixa uma imagem: só https, só host público (checado a cada
    redirecionamento), limite de tamanho durante o download."""
    for _ in range(MAX_REDIRECTS + 1):
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or not await is_public_host(parsed.hostname):
            return None
        try:
            async with client.stream("GET", url) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("location")
                    if not location:
                        return None
                    url = urljoin(url, location)
                    continue
                if resp.status_code != 200:
                    return None
                buf = bytearray()
                async for chunk in resp.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) > MAX_IMAGE_BYTES:
                        return None
        except httpx.HTTPError as exc:
            logger.info("Falha ao baixar capa candidata %s: %s", parsed.hostname, exc)
            return None
        data = bytes(buf)
        return data if acceptable_image(data) else None
    return None


# --------------------------------------------------------------- fontes

async def _open_library_isbn(client, isbn: str):
    url = f"https://covers.openlibrary.org/b/isbn/{isbn}-L.jpg?default=false"
    return await _get_image(client, url), ""


async def _open_library_id(client, cover_id: str):
    url = f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg?default=false"
    return await _get_image(client, url), ""


async def _brasilapi(client, isbn: str):
    try:
        resp = await client.get(f"https://brasilapi.com.br/api/isbn/v1/{isbn}")
        if resp.status_code != 200:
            return None, ""
        info = resp.json()
    except (httpx.HTTPError, ValueError):
        return None, ""
    if not isinstance(info, dict) or not isinstance(info.get("cover_url"), str):
        return None, ""
    provider = info.get("provider") if isinstance(info.get("provider"), str) else ""
    # a URL vem da API (não do usuário), mas mesmo assim passa pelo download seguro
    return await _get_image(client, info["cover_url"]), provider


async def _google_books(client, isbn: str):
    results = await search_books(f"isbn:{isbn}", max_results=1)
    thumb = results[0].get("thumbnail") if results else None
    if not thumb or not lcl.is_google_image_url(thumb):
        return None, ""
    best: Optional[bytes] = None
    for candidate in ([lcl._with_zoom(thumb, 2), thumb] if "zoom=" in thumb else [thumb]):
        data = await _get_image(client, candidate)
        if data and (best is None or len(data) > len(best)):
            best = data  # o zoom maior às vezes devolve só um "sem capa" pequeno
    return best, ""


async def _open_library_title_ids(client, title: str, author: str) -> list[str]:
    params = {"title": title, "limit": 8, "fields": "cover_i"}
    if author:
        params["author"] = author
    try:
        resp = await client.get("https://openlibrary.org/search.json", params=params)
        resp.raise_for_status()
        docs = resp.json().get("docs", [])
    except (httpx.HTTPError, ValueError, AttributeError):
        return []
    ids: list[str] = []
    for d in docs:
        cid = d.get("cover_i") if isinstance(d, dict) else None
        if isinstance(cid, int) and str(cid) not in ids:
            ids.append(str(cid))
    return ids


_FETCHERS = {"ol": _open_library_isbn, "olid": _open_library_id, "br": _brasilapi, "gb": _google_books}


async def _bytes_for(spec: dict, client) -> tuple[Optional[bytes], str]:
    """Imagem de uma candidata + detalhe extra para o rótulo. Nunca levanta:
    uma fonte fora do ar não pode derrubar as outras."""
    fetcher = _FETCHERS.get(spec.get("s"))
    key = spec.get("i")
    if not fetcher or not isinstance(key, str) or not _KEY_RE.match(key):
        return None, ""
    try:
        return await fetcher(client, key)
    except Exception:  # noqa: BLE001 — qualquer falha de fonte vira "sem candidata"
        logger.warning("Fonte de capa %s falhou para %s", spec.get("s"), key, exc_info=True)
        return None, ""


# ---------------------------------------------------------------- caches

_BYTES_TTL = 15 * 60
_BYTES_MAX_ENTRIES = 40
_BYTES_MAX_TOTAL = 40 * 1024 * 1024
_bytes_cache: "OrderedDict[str, tuple[float, bytes]]" = OrderedDict()

_RESULTS_TTL = 10 * 60
_RESULTS_MAX = 50
_results_cache: "OrderedDict[tuple, tuple[float, list[dict]]]" = OrderedDict()


def _bytes_get(token: str) -> Optional[bytes]:
    hit = _bytes_cache.get(token)
    if not hit:
        return None
    if hit[0] < time.monotonic():
        _bytes_cache.pop(token, None)
        return None
    _bytes_cache.move_to_end(token)
    return hit[1]


def _bytes_put(token: str, data: bytes) -> None:
    _bytes_cache[token] = (time.monotonic() + _BYTES_TTL, data)
    _bytes_cache.move_to_end(token)
    while len(_bytes_cache) > _BYTES_MAX_ENTRIES or sum(len(v[1]) for v in _bytes_cache.values()) > _BYTES_MAX_TOTAL:
        _bytes_cache.popitem(last=False)


# ------------------------------------------------------------ API pública

def make_token(spec: dict) -> str:
    return _serializer.dumps(spec)


async def fetch_candidate(token: str) -> Optional[bytes]:
    """Bytes da imagem de uma candidata, a partir do token assinado. Usa o
    cache da busca e, se expirou, baixa de novo da fonte. None se o token for
    inválido/adulterado ou a imagem não estiver mais disponível."""
    token = (token or "").strip()
    if not token or len(token) > 300:
        return None
    cached = _bytes_get(token)
    if cached:
        return cached
    try:
        spec = _serializer.loads(token)
    except BadSignature:
        return None
    if not isinstance(spec, dict):
        return None
    async with _client() as client:
        data, _ = await _bytes_for(spec, client)
    if data:
        _bytes_put(token, data)
    return data


async def find_covers(isbn_raw: str, title: str, author: str) -> list[dict]:
    """Candidatas de capa: [{token, source, label, width, height, preview}].
    Com ISBN consulta Open Library, BrasilAPI e Google Books em paralelo; se
    sobrarem poucas (ou não houver ISBN), completa com a busca por
    título/autor na Open Library. Imagens idênticas entre fontes aparecem uma
    vez só (a BrasilAPI costuma repetir a do Google/Open Library)."""
    isbn = normalize_isbn(isbn_raw)
    title, author = (title or "").strip()[:200], (author or "").strip()[:200]
    if not isbn and len(title) < 2:
        return []

    cache_key = (isbn, _fold(title), _fold(author))
    hit = _results_cache.get(cache_key)
    if hit and hit[0] > time.monotonic():
        _results_cache.move_to_end(cache_key)
        return hit[1]

    out: list[dict] = []
    seen: set[str] = set()

    def add(spec: dict, data: Optional[bytes], extra: str) -> None:
        if not data or len(out) >= MAX_CANDIDATES:
            return
        digest = hashlib.sha256(data).hexdigest()
        if digest in seen:
            return
        seen.add(digest)
        token = make_token(spec)
        _bytes_put(token, data)
        size = image_size(data)
        label = SOURCE_LABELS[spec["s"]] + (f" ({extra})" if extra else "")
        out.append({
            "token": token,
            "source": spec["s"],
            "label": label,
            "width": size[0] if size else None,
            "height": size[1] if size else None,
            "preview": f"/api/cover-preview?t={quote(token, safe='')}",
        })

    async with _client() as client:
        if isbn:
            specs = [{"s": "ol", "i": isbn}, {"s": "br", "i": isbn}, {"s": "gb", "i": isbn}]
            results = await asyncio.gather(*[_bytes_for(sp, client) for sp in specs])
            for sp, (data, extra) in zip(specs, results):
                add(sp, data, extra)
        if len(out) < 3 and title:
            ids = (await _open_library_title_ids(client, title, author))[:4]
            specs = [{"s": "olid", "i": cid} for cid in ids]
            results = await asyncio.gather(*[_bytes_for(sp, client) for sp in specs])
            for sp, (data, extra) in zip(specs, results):
                add(sp, data, extra)

    if out:  # busca vazia não é guardada: uma falha passageira das APIs não pode "grudar"
        _results_cache[cache_key] = (time.monotonic() + _RESULTS_TTL, out)
        _results_cache.move_to_end(cache_key)
        while len(_results_cache) > _RESULTS_MAX:
            _results_cache.popitem(last=False)
    return out
