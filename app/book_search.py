import logging
import os

import httpx

logger = logging.getLogger("bookvote.book_search")

GOOGLE_BOOKS_API = "https://www.googleapis.com/books/v1/volumes"


def _clean_env(raw: str) -> str:
    """Docker Compose's env_file does NOT strip quotes like a shell would —
    `KEY="abc"` is loaded literally as the 4-char string `"abc"`. Strip
    accidental quotes/whitespace so a copy-pasted key still works."""
    return raw.strip().strip('"').strip("'").strip()


GOOGLE_BOOKS_API_KEY = _clean_env(os.environ.get("GOOGLE_BOOKS_API_KEY", ""))

if GOOGLE_BOOKS_API_KEY:
    _masked = GOOGLE_BOOKS_API_KEY[:4] + "…" + GOOGLE_BOOKS_API_KEY[-4:]
    logger.info("Google Books API key carregada (%s), buscas usarão cota autenticada.", _masked)
else:
    logger.warning("Google Books API key NÃO configurada — buscas usarão a cota pública anônima.")


async def search_books(query: str, max_results: int = 6) -> list[dict]:
    """Looks up candidate books on Google Books for the autocomplete field.

    Amazon has no equivalent open/keyless search API (its Product
    Advertising API requires an approved affiliate account), so this is
    Google Books only. Returns [] on any error — the nomination form
    always still accepts free-text titles as a fallback — but logs the
    error so a broken deployment/network is visible in `docker compose logs`.
    """
    params = {"q": query, "maxResults": max_results}
    if GOOGLE_BOOKS_API_KEY:
        params["key"] = GOOGLE_BOOKS_API_KEY

    async with httpx.AsyncClient(timeout=8) as client:
        try:
            resp = await client.get(GOOGLE_BOOKS_API, params=params)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            logger.warning("Google Books search failed for %r: %s", query, exc)
            return []
        except ValueError as exc:  # non-JSON response
            logger.warning("Google Books returned non-JSON for %r: %s", query, exc)
            return []

    out = []
    for item in data.get("items", []) or []:
        info = item.get("volumeInfo", {}) or {}
        title = info.get("title")
        if not title:
            continue

        authors = ", ".join(info.get("authors", []) or []) or None

        isbn = None
        for ident in info.get("industryIdentifiers", []) or []:
            if ident.get("type") == "ISBN_13":
                isbn = ident.get("identifier")
                break
        if not isbn:
            for ident in info.get("industryIdentifiers", []) or []:
                if ident.get("type") == "ISBN_10":
                    isbn = ident.get("identifier")
                    break

        thumb = (info.get("imageLinks") or {}).get("thumbnail") or (info.get("imageLinks") or {}).get(
            "smallThumbnail"
        )
        if thumb:
            thumb = thumb.replace("http://", "https://")

        subtitle = info.get("subtitle")
        display_title = f"{title}: {subtitle}" if subtitle else title

        out.append({"title": display_title, "author": authors, "isbn": isbn, "thumbnail": thumb})

    return out
