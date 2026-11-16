"""Gravação/remoção segura de imagens enviadas por usuários (capas de LC,
imagens de indicações). Sem dependência de models/main — pode ser importado
de qualquer lugar."""
import logging
import os
import secrets
from typing import Optional

logger = logging.getLogger("bookvote.media")


def sniff_image_ext(data: bytes) -> Optional[str]:
    """Extensão pela assinatura (magic bytes), não pelo nome/Content-Type
    enviados — e SVG fica de fora de propósito (pode carregar script)."""
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def save_image(data: bytes, directory: str, max_bytes: int) -> Optional[str]:
    """Grava a imagem em `directory` com nome aleatório e devolve o nome do
    arquivo, ou None se não for uma imagem aceita (formato ou tamanho)."""
    ext = sniff_image_ext(data)
    if not ext or len(data) > max_bytes:
        return None
    os.makedirs(directory, exist_ok=True)
    name = f"{secrets.token_hex(12)}.{ext}"
    with open(os.path.join(directory, name), "wb") as fh:
        fh.write(data)
    return name


def delete_image(directory: str, filename: Optional[str]) -> None:
    if not filename or "/" in filename or "\\" in filename or filename.startswith("."):
        return
    try:
        os.remove(os.path.join(directory, filename))
    except FileNotFoundError:
        pass
    except OSError:
        logger.warning("Não consegui apagar a imagem %s", filename, exc_info=True)


def read_image(directory: str, filename: Optional[str]) -> Optional[bytes]:
    if not filename or "/" in filename or "\\" in filename or filename.startswith("."):
        return None
    try:
        with open(os.path.join(directory, filename), "rb") as fh:
            return fh.read()
    except OSError:
        return None


# ------------------------------------------------- imagens das indicações

from .database import DATA_DIR  # noqa: E402  (no fim para manter o topo sem dependências)

BOOK_IMAGES_DIR = os.path.join(DATA_DIR, "book_images")
os.makedirs(BOOK_IMAGES_DIR, exist_ok=True)
MAX_BOOK_IMAGE_BYTES = 5 * 1024 * 1024
