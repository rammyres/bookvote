"""Miniaturas do material de apoio.

Imagens são reduzidas; de PDFs vira miniatura a 1ª página. A miniatura é um
JPEG guardado ao lado do original, no mesmo armazenamento (tipo lc_materials),
com nome derivado do arquivo: `<nome>_t.jpg`. Por isso não precisa de coluna
nova no banco, e PDFs enviados antes desta versão ganham a miniatura sob
demanda, na primeira visita à página.

Tudo aqui é "melhor esforço": se faltar a biblioteca ou o arquivo estiver
corrompido, devolve None e a página mostra um ícone no lugar da miniatura."""
import io
import logging
from typing import Optional

from . import storage

logger = logging.getLogger("bookvote.thumbs")

THUMB_WIDTH = 360        # px; a grade mostra ~80-120 px, o dobro cobre telas de alta densidade
THUMB_QUALITY = 78
MAX_SOURCE_PIXELS = 60_000_000  # recusa imagens gigantes (proteção de memória)


def thumb_name(filename: str) -> str:
    stem = filename.rsplit(".", 1)[0]
    return f"{stem}_t.jpg"


def _to_jpeg(im) -> bytes:
    from PIL import Image

    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.getchannel("A"))
        im = bg
    elif im.mode != "RGB":
        im = im.convert("RGB")
    im.thumbnail((THUMB_WIDTH, THUMB_WIDTH * 2))  # PDFs altos não passam de 2x a largura
    out = io.BytesIO()
    im.save(out, "JPEG", quality=THUMB_QUALITY, optimize=True, progressive=True)
    return out.getvalue()


def _from_image(data: bytes) -> bytes:
    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        if im.width * im.height > MAX_SOURCE_PIXELS:
            raise ValueError("imagem grande demais para gerar miniatura")
        im.draft("RGB", (THUMB_WIDTH * 2, THUMB_WIDTH * 2))  # JPEG: decodifica já reduzido
        im.load()
        return _to_jpeg(im)


def _from_pdf(data: bytes) -> bytes:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    try:
        page = pdf[0]
        try:
            width = page.get_width() or 1
            bitmap = page.render(scale=THUMB_WIDTH / width, may_draw_forms=False)
            return _to_jpeg(bitmap.to_pil())
        finally:
            page.close()
    finally:
        pdf.close()


def make(data: bytes, is_pdf: bool) -> Optional[bytes]:
    try:
        return _from_pdf(data) if is_pdf else _from_image(data)
    except Exception as exc:  # noqa: BLE001 - arquivo ruim ou biblioteca ausente: sem miniatura
        logger.warning("Não foi possível gerar a miniatura (%s): %s", "pdf" if is_pdf else "imagem", exc)
        return None


def store(filename: str, data: bytes, is_pdf: bool) -> bool:
    """Gera e grava a miniatura. Devolve False se não deu (sem levantar erro)."""
    thumb = make(data, is_pdf)
    if not thumb:
        return False
    try:
        storage.put(storage.KIND_MATERIAL, thumb_name(filename), thumb, "image/jpeg")
    except storage.StorageError:
        logger.warning("Falha ao gravar a miniatura de %s", filename, exc_info=True)
        return False
    return True


def delete(filename: str) -> None:
    try:
        storage.delete(storage.KIND_MATERIAL, thumb_name(filename))
    except storage.StorageError:
        logger.warning("Falha ao apagar a miniatura de %s", filename, exc_info=True)
