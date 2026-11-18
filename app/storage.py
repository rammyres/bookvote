"""Armazenamento dos arquivos enviados: imagens das indicações, capas e
imagens de cronograma das LCs e material de apoio.

Três backends, escolhidos no .env (STORAGE_BACKEND):

  local  (padrão)  pasta dentro de BOOKVOTE_DATA_DIR — o comportamento antigo.
  r2               Cloudflare R2.
  gcs              Google Cloud Storage.

R2 e GCS são falados pela API S3 (boto3): no R2 é o modo nativo; no GCS é a
"interoperabilidade" XML com chaves HMAC. Um caminho só de código para os
dois, o que também o torna testável sem credenciais de nenhum deles.

Os arquivos vivem em "tipos" (kinds), que viram prefixos no bucket:
    book_images/   imagem enviada numa indicação
    lc_covers/     capas e imagens de cronograma das LCs
    lc_materials/  material de apoio (PDF e imagens)
O banco guarda só o nome do arquivo; o tipo é implícito pela coluna.

Entrega ao navegador — dois modos:
  - sem STORAGE_PUBLIC_BASE_URL (padrão): o próprio app serve /media/... lendo
    do armazenamento. Funciona com bucket PRIVADO e mantém as URLs de sempre.
  - com STORAGE_PUBLIC_BASE_URL: as páginas apontam direto para o bucket
    (domínio público do R2, bucket público do GCS ou um CDN na frente).

Ao trocar de local para um bucket, o app continua lendo do disco o que ainda
não foi migrado (STORAGE_LOCAL_FALLBACK=true, padrão) até você rodar
`scripts/manage.py storage-migrate`.
"""
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Callable, Iterator, Optional

from .database import DATA_DIR

logger = logging.getLogger("bookvote.storage")

KIND_BOOK = "book_images"
KIND_COVER = "lc_covers"
KIND_MATERIAL = "lc_materials"
KINDS = (KIND_BOOK, KIND_COVER, KIND_MATERIAL)

# URL pública (servida pelo app) de cada tipo. O material não tem rota de
# arquivo: passa por /lc/<id>/material/<id>, que consulta o banco.
MEDIA_ROUTES = {KIND_BOOK: "books", KIND_COVER: "lc"}

CONTENT_TYPES = {
    "jpg": "image/jpeg", "png": "image/png", "gif": "image/gif",
    "webp": "image/webp", "pdf": "application/pdf",
}
CACHE_CONTROL = "public, max-age=31536000, immutable"  # nomes aleatórios: nunca mudam

# nomes que o app gera (hex aleatório + extensão); também aceita nomes
# antigos "sensatos" — o que não pode existir é barra, ponto no início, etc.
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}\.(jpg|png|gif|webp|pdf)$")


class StorageError(Exception):
    """Falha de comunicação com o armazenamento (rede, credencial, bucket)."""


def valid_name(name: Optional[str]) -> bool:
    return bool(name) and bool(_NAME_RE.match(name))


def content_type_for(name: str) -> str:
    return CONTENT_TYPES.get(name.rsplit(".", 1)[-1].lower(), "application/octet-stream")


@dataclass
class StoredObject:
    size: Optional[int]
    content_type: str
    chunks: Callable[[], Iterator[bytes]]
    local_path: Optional[str] = None  # preenchido no backend local (permite FileResponse)


# ------------------------------------------------------------------- local

class LocalStorage:
    label = "local"

    def __init__(self, root: str):
        self.root = root

    def dir(self, kind: str) -> str:
        return os.path.join(self.root, kind)

    def path(self, kind: str, name: str) -> str:
        return os.path.join(self.dir(kind), name)

    def put(self, kind: str, name: str, data: bytes, content_type: Optional[str] = None) -> None:
        os.makedirs(self.dir(kind), exist_ok=True)
        final = self.path(kind, name)
        tmp = final + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, final)  # gravação atômica: ninguém lê arquivo pela metade

    def get(self, kind: str, name: str) -> Optional[bytes]:
        try:
            with open(self.path(kind, name), "rb") as fh:
                return fh.read()
        except OSError:
            return None

    def open(self, kind: str, name: str) -> Optional[StoredObject]:
        path = self.path(kind, name)
        try:
            size = os.path.getsize(path)
        except OSError:
            return None

        def chunks() -> Iterator[bytes]:
            with open(path, "rb") as fh:
                while block := fh.read(64 * 1024):
                    yield block

        return StoredObject(size, content_type_for(name), chunks, local_path=path)

    def delete(self, kind: str, name: str) -> None:
        try:
            os.remove(self.path(kind, name))
        except FileNotFoundError:
            pass
        except OSError:
            logger.warning("Não consegui apagar %s/%s", kind, name, exc_info=True)

    def exists(self, kind: str, name: str) -> bool:
        return os.path.isfile(self.path(kind, name))

    def size(self, kind: str, name: str) -> Optional[int]:
        try:
            return os.path.getsize(self.path(kind, name))
        except OSError:
            return None

    def names(self, kind: str) -> list[str]:
        try:
            return sorted(n for n in os.listdir(self.dir(kind)) if valid_name(n))
        except OSError:
            return []


# --------------------------------------------------------------- S3 (R2/GCS)

class S3Storage:
    """Cliente S3-compatível (R2, GCS). Chamadas bloqueantes: quem chama de
    dentro de código `async` deve usar run_in_threadpool (ver media.py)."""

    def __init__(self, *, label: str, bucket: str, endpoint_url: str, access_key: str,
                 secret_key: str, region: str = "auto", prefix: str = ""):
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "STORAGE_BACKEND=%s precisa do pacote boto3 (pip install 'boto3>=1.36')." % label
            ) from exc
        self.label = label
        self.bucket = bucket
        self.prefix = prefix
        opts = dict(
            signature_version="s3v4",
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=5,
            read_timeout=30,
            s3={"addressing_style": "path"},
        )
        try:
            # boto3/botocore >= 1.36 manda checksums CRC32 por padrão
            # (x-amz-checksum-*, aws-chunked), que R2 e GCS rejeitam ou tratam
            # mal. "when_required" volta ao comportamento compatível.
            config = Config(request_checksum_calculation="when_required",
                            response_checksum_validation="when_required", **opts)
        except TypeError:  # botocore antigo: nem existe esse comportamento
            config = Config(**opts)
        self.client = boto3.client(
            "s3", endpoint_url=endpoint_url, aws_access_key_id=access_key,
            aws_secret_access_key=secret_key, region_name=region, config=config,
        )

    def key(self, kind: str, name: str) -> str:
        return f"{self.prefix}{kind}/{name}"

    @staticmethod
    def _is_missing(exc) -> bool:
        err = getattr(exc, "response", {}).get("Error", {})
        status = getattr(exc, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode")
        return err.get("Code") in ("NoSuchKey", "404", "NotFound") or status == 404

    def _wrap(self, exc: Exception, what: str) -> StorageError:
        return StorageError(f"{self.label}: falha em {what}: {type(exc).__name__}: {exc}")

    def put(self, kind: str, name: str, data: bytes, content_type: Optional[str] = None) -> None:
        from botocore.exceptions import BotoCoreError, ClientError
        try:
            self.client.put_object(
                Bucket=self.bucket, Key=self.key(kind, name), Body=data,
                ContentType=content_type or content_type_for(name), CacheControl=CACHE_CONTROL,
            )
        except (BotoCoreError, ClientError) as exc:
            raise self._wrap(exc, f"gravar {kind}/{name}") from exc

    def get(self, kind: str, name: str) -> Optional[bytes]:
        from botocore.exceptions import BotoCoreError, ClientError
        try:
            return self.client.get_object(Bucket=self.bucket, Key=self.key(kind, name))["Body"].read()
        except ClientError as exc:
            if self._is_missing(exc):
                return None
            raise self._wrap(exc, f"ler {kind}/{name}") from exc
        except BotoCoreError as exc:
            raise self._wrap(exc, f"ler {kind}/{name}") from exc

    def open(self, kind: str, name: str) -> Optional[StoredObject]:
        from botocore.exceptions import BotoCoreError, ClientError
        try:
            resp = self.client.get_object(Bucket=self.bucket, Key=self.key(kind, name))
        except ClientError as exc:
            if self._is_missing(exc):
                return None
            raise self._wrap(exc, f"abrir {kind}/{name}") from exc
        except BotoCoreError as exc:
            raise self._wrap(exc, f"abrir {kind}/{name}") from exc
        body = resp["Body"]

        def chunks() -> Iterator[bytes]:
            try:
                yield from body.iter_chunks(64 * 1024)
            finally:
                body.close()  # também quando o cliente desiste no meio

        return StoredObject(resp.get("ContentLength"), content_type_for(name), chunks)

    def delete(self, kind: str, name: str) -> None:
        from botocore.exceptions import BotoCoreError, ClientError
        try:
            self.client.delete_object(Bucket=self.bucket, Key=self.key(kind, name))
        except (BotoCoreError, ClientError):
            logger.warning("Não consegui apagar %s/%s do bucket", kind, name, exc_info=True)

    def size(self, kind: str, name: str) -> Optional[int]:
        from botocore.exceptions import BotoCoreError, ClientError
        try:
            return self.client.head_object(Bucket=self.bucket, Key=self.key(kind, name))["ContentLength"]
        except ClientError as exc:
            if self._is_missing(exc):
                return None
            raise self._wrap(exc, f"consultar {kind}/{name}") from exc
        except BotoCoreError as exc:
            raise self._wrap(exc, f"consultar {kind}/{name}") from exc

    def exists(self, kind: str, name: str) -> bool:
        return self.size(kind, name) is not None


class FallbackStorage:
    """Grava no bucket, mas lê do disco o que ainda não foi migrado, e apaga
    dos dois lados (assim arquivos antigos não ficam órfãos no disco)."""

    def __init__(self, primary: S3Storage, local: LocalStorage):
        self.primary, self.local = primary, local
        self.label = primary.label

    def put(self, kind, name, data, content_type=None):
        self.primary.put(kind, name, data, content_type)

    def get(self, kind, name):
        data = self.primary.get(kind, name)
        return data if data is not None else self.local.get(kind, name)

    def open(self, kind, name):
        return self.primary.open(kind, name) or self.local.open(kind, name)

    def delete(self, kind, name):
        self.primary.delete(kind, name)
        self.local.delete(kind, name)

    def exists(self, kind, name):
        return self.primary.exists(kind, name) or self.local.exists(kind, name)

    def size(self, kind, name):
        s = self.primary.size(kind, name)
        return s if s is not None else self.local.size(kind, name)


# -------------------------------------------------------------- configuração

@dataclass
class _State:
    backend: object = None
    remote: Optional[S3Storage] = None
    local: LocalStorage = None
    name: str = "local"
    public_base: str = ""
    prefix: str = ""
    bucket: str = ""
    extra: dict = field(default_factory=dict)


_S = _State()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip().strip('"').strip("'").strip()


def configure() -> None:
    """(Re)lê o .env e monta o backend. Chamado no import; os testes chamam de
    novo depois de mudar o ambiente. Configuração incompleta derruba a subida
    com mensagem clara — melhor do que gravar em silêncio no lugar errado."""
    local = LocalStorage(DATA_DIR)
    name = _env("STORAGE_BACKEND", "local").lower() or "local"
    prefix = _env("STORAGE_PREFIX").strip("/")
    prefix = f"{prefix}/" if prefix else ""
    public_base = _env("STORAGE_PUBLIC_BASE_URL").rstrip("/")

    _S.local, _S.name, _S.prefix, _S.public_base, _S.remote, _S.bucket = local, name, prefix, "", None, ""
    if name == "local":
        _S.backend = local
        return
    if name not in ("r2", "gcs"):
        raise RuntimeError(f"STORAGE_BACKEND inválido: {name!r} (use local, r2 ou gcs).")

    bucket = _env("STORAGE_BUCKET")
    if name == "r2":
        account, key, secret = _env("R2_ACCOUNT_ID"), _env("R2_ACCESS_KEY_ID"), _env("R2_SECRET_ACCESS_KEY")
        required = {"STORAGE_BUCKET": bucket, "R2_ACCOUNT_ID": account, "R2_ACCESS_KEY_ID": key, "R2_SECRET_ACCESS_KEY": secret}
        endpoint = _env("STORAGE_ENDPOINT_URL") or f"https://{account}.r2.cloudflarestorage.com"
    else:
        key, secret = _env("GCS_HMAC_ACCESS_KEY"), _env("GCS_HMAC_SECRET")
        required = {"STORAGE_BUCKET": bucket, "GCS_HMAC_ACCESS_KEY": key, "GCS_HMAC_SECRET": secret}
        endpoint = _env("STORAGE_ENDPOINT_URL") or "https://storage.googleapis.com"
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise RuntimeError(f"STORAGE_BACKEND={name} exige no .env: {', '.join(missing)}")

    remote = S3Storage(label=name, bucket=bucket, endpoint_url=endpoint, access_key=key,
                       secret_key=secret, region="auto", prefix=prefix)
    _S.remote, _S.bucket, _S.public_base = remote, bucket, public_base
    fallback = _env("STORAGE_LOCAL_FALLBACK", "true").lower() != "false"
    _S.backend = FallbackStorage(remote, local) if fallback else remote
    logger.info("Armazenamento de arquivos: %s (bucket %s%s)", name, bucket,
                ", leitura do disco como reserva" if fallback else "")


# ------------------------------------------------- API do módulo (delegação)

def backend_name() -> str:
    return _S.name


def put(kind: str, name: str, data: bytes, content_type: Optional[str] = None) -> None:
    _S.backend.put(kind, name, data, content_type)


def get(kind: str, name: str) -> Optional[bytes]:
    return _S.backend.get(kind, name) if valid_name(name) else None


def open_object(kind: str, name: str) -> Optional[StoredObject]:
    return _S.backend.open(kind, name) if valid_name(name) else None


def delete(kind: str, name: Optional[str]) -> None:
    if valid_name(name):
        _S.backend.delete(kind, name)


def exists(kind: str, name: str) -> bool:
    return valid_name(name) and _S.backend.exists(kind, name)


def local_dir(kind: str) -> str:
    """Pasta do kind no disco (backend local, ou origem da migração)."""
    return _S.local.dir(kind)


def local_backend() -> LocalStorage:
    return _S.local


def remote_backend() -> Optional[S3Storage]:
    return _S.remote


def is_public_mode() -> bool:
    return bool(_S.public_base) and _S.name != "local"


def public_url(kind: str, name: Optional[str]) -> Optional[str]:
    """URL que vai no HTML. No modo público aponta direto para o bucket; senão,
    para a rota /media do próprio app (material usa /lc/<id>/material/<id>)."""
    if not name:
        return None
    if is_public_mode():
        return f"{_S.public_base}/{_S.prefix}{kind}/{name}"
    route = MEDIA_ROUTES.get(kind)
    return f"/media/{route}/{name}" if route else None


def kind_for_route(route: str) -> Optional[str]:
    for kind, r in MEDIA_ROUTES.items():
        if r == route:
            return kind
    return None


configure()
