"""Gravação/remoção de imagens e arquivos enviados por usuários (capas de LC,
imagens de indicações, material de apoio). A escolha entre disco, R2 ou GCS
fica em app/storage.py; aqui ficam a validação do conteúdo (pelos bytes, não
pelo nome nem pelo Content-Type enviados) e os nomes aleatórios.

As funções `a...` são as versões para código `async`: o acesso ao bucket é
bloqueante (boto3), então roda em thread para não travar o servidor."""
import secrets
from typing import Optional

from starlette.concurrency import run_in_threadpool

from . import storage
from .storage import KIND_BOOK, KIND_COVER, KIND_MATERIAL  # noqa: F401  (re-exportados)

MAX_BOOK_IMAGE_BYTES = 5 * 1024 * 1024

# Pasta no disco das imagens das indicações (só vale com STORAGE_BACKEND=local
# ou como origem da migração). Mantida por compatibilidade.
BOOK_IMAGES_DIR = storage.local_dir(KIND_BOOK)


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


def sniff_material_ext(data: bytes) -> Optional[str]:
    """Material de apoio aceita PDF e as mesmas imagens. O cabeçalho do PDF
    pode vir precedido de lixo (a especificação tolera até 1 KB)."""
    if b"%PDF-" in data[:1024]:
        return "pdf"
    return sniff_image_ext(data)


def _store(data: bytes, kind: str, ext: str) -> str:
    name = f"{secrets.token_hex(12)}.{ext}"
    storage.put(kind, name, data, storage.content_type_for(name))
    return name


def save_image(data: bytes, kind: str, max_bytes: int) -> Optional[str]:
    """Grava a imagem com nome aleatório e devolve o nome do arquivo, ou None
    se não for uma imagem aceita (formato ou tamanho). Pode levantar
    storage.StorageError se o armazenamento estiver fora do ar."""
    ext = sniff_image_ext(data)
    if not ext or len(data) > max_bytes:
        return None
    return _store(data, kind, ext)


def save_material(data: bytes, ext: str) -> str:
    return _store(data, KIND_MATERIAL, ext)


def delete_image(kind: str, filename: Optional[str]) -> None:
    storage.delete(kind, filename)


def read_image(kind: str, filename: Optional[str]) -> Optional[bytes]:
    return storage.get(kind, filename) if filename else None


async def asave_image(data: bytes, kind: str, max_bytes: int) -> Optional[str]:
    return await run_in_threadpool(save_image, data, kind, max_bytes)


async def asave_material(data: bytes, ext: str) -> str:
    return await run_in_threadpool(save_material, data, ext)


async def adelete_image(kind: str, filename: Optional[str]) -> None:
    await run_in_threadpool(delete_image, kind, filename)


async def aread_image(kind: str, filename: Optional[str]) -> Optional[bytes]:
    return await run_in_threadpool(read_image, kind, filename)
