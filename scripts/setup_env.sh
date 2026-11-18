#!/usr/bin/env bash
# Gera/atualiza o .env antes do deploy. Pode ser interativo (rode sem flags
# numa VM e responda as perguntas) ou não-interativo (passe flags, útil em
# CI/scripts de deploy automatizado).
#
# Uso interativo:
#   ./scripts/setup_env.sh
#
# Uso não-interativo (exemplo):
#   ./scripts/setup_env.sh --yes \
#     --turnstile-site 0x4AAxxxxx --turnstile-secret 0x4AAyyyyy \
#     --max-voters 8 --google-books-key SUA_CHAVE \
#     --base-url https://enquete.seudominio.com.br
#
# Armazenamento em bucket (Cloudflare R2 ou Google Cloud Storage):
#   ./scripts/setup_env.sh --yes --storage-backend r2 \
#     --storage-bucket meu-bucket --r2-account-id ACCOUNT_ID \
#     --r2-access-key-id KEY --r2-secret-access-key SECRET
#   ./scripts/setup_env.sh --yes --storage-backend gcs \
#     --storage-bucket meu-bucket --gcs-hmac-access-key KEY --gcs-hmac-secret SECRET
#   Opcionais: --storage-prefix, --storage-public-base-url, --storage-endpoint-url,
#   --storage-local-fallback true|false. Sem --storage-backend o padrão é "local".
#
# Sempre gera uma BOOKVOTE_SECRET_KEY nova se ainda não existir uma no .env.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$ROOT_DIR/.env"

NONINTERACTIVE=false
TURNSTILE_SITE=""
TURNSTILE_SECRET=""
MAX_VOTERS=""
COOKIE_SECURE=""
GOOGLE_BOOKS_KEY=""
RESEND_KEY=""
RESEND_FROM=""
BASE_URL=""
STORAGE_BACKEND=""
STORAGE_BUCKET=""
STORAGE_PREFIX=""
STORAGE_PUBLIC_BASE_URL=""
STORAGE_ENDPOINT_URL=""
STORAGE_LOCAL_FALLBACK=""
R2_ACCOUNT_ID=""
R2_ACCESS_KEY_ID=""
R2_SECRET_ACCESS_KEY=""
GCS_HMAC_ACCESS_KEY=""
GCS_HMAC_SECRET=""

usage() {
  awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "$0"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes) NONINTERACTIVE=true; shift ;;
    --turnstile-site) TURNSTILE_SITE="$2"; shift 2 ;;
    --turnstile-secret) TURNSTILE_SECRET="$2"; shift 2 ;;
    --max-voters) MAX_VOTERS="$2"; shift 2 ;;
    --cookie-secure) COOKIE_SECURE="$2"; shift 2 ;;
    --google-books-key) GOOGLE_BOOKS_KEY="$2"; shift 2 ;;
    --resend-key) RESEND_KEY="$2"; shift 2 ;;
    --resend-from) RESEND_FROM="$2"; shift 2 ;;
    --base-url) BASE_URL="$2"; shift 2 ;;
    --storage-backend) STORAGE_BACKEND="$2"; shift 2 ;;
    --storage-bucket) STORAGE_BUCKET="$2"; shift 2 ;;
    --storage-prefix) STORAGE_PREFIX="$2"; shift 2 ;;
    --storage-public-base-url) STORAGE_PUBLIC_BASE_URL="$2"; shift 2 ;;
    --storage-endpoint-url) STORAGE_ENDPOINT_URL="$2"; shift 2 ;;
    --storage-local-fallback) STORAGE_LOCAL_FALLBACK="$2"; shift 2 ;;
    --r2-account-id) R2_ACCOUNT_ID="$2"; shift 2 ;;
    --r2-access-key-id) R2_ACCESS_KEY_ID="$2"; shift 2 ;;
    --r2-secret-access-key) R2_SECRET_ACCESS_KEY="$2"; shift 2 ;;
    --gcs-hmac-access-key) GCS_HMAC_ACCESS_KEY="$2"; shift 2 ;;
    --gcs-hmac-secret) GCS_HMAC_SECRET="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Opção desconhecida: $1" >&2; usage; exit 1 ;;
  esac
done

gen_secret() {
  if command -v python3 >/dev/null 2>&1; then
    python3 -c "import secrets; print(secrets.token_hex(32))"
  elif command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    echo "Preciso de python3 ou openssl para gerar a chave secreta." >&2
    exit 1
  fi
}

ask() {
  local prompt="$1" default="$2" var
  if [[ "$NONINTERACTIVE" == true ]]; then
    echo "$default"
    return
  fi
  read -r -p "$prompt [$default]: " var </dev/tty || true
  echo "${var:-$default}"
}

# Como ask(), mas sem imprimir o valor atual (chaves de acesso ao bucket):
# mostra só "(mantido)" quando já existe um valor e Enter o conserva.
ask_secret() {
  local prompt="$1" default="$2" var hint="vazio"
  if [[ "$NONINTERACTIVE" == true ]]; then
    echo "$default"
    return
  fi
  [[ -n "$default" ]] && hint="mantido"
  read -r -s -p "$prompt [$hint]: " var </dev/tty || true
  echo >&2
  echo "${var:-$default}"
}

# Reads KEY=VALUE out of a .env file as plain text (grep + string slicing) —
# deliberately NOT `source`, since that executes the file as a bash script.
# A value with shell-special characters (spaces, <, >, $, `...`) would
# otherwise break parsing, or worse, run something. This also means an
# old .env corrupted by that very bug can still be read back safely,
# instead of crashing this script before it gets a chance to fix it.
get_env_value() {
  local key="$1" file="$2" line val
  [[ -f "$file" ]] || return
  line="$(grep -m1 "^${key}=" "$file" 2>/dev/null || true)"
  [[ -z "$line" ]] && return
  val="${line#*=}"
  if [[ "${val:0:1}" == "'" && "${val: -1}" == "'" && "${#val}" -ge 2 ]]; then
    val="${val:1:-1}"
    val="${val//\'\\\'\'/\'}"
  elif [[ "${val:0:1}" == '"' && "${val: -1}" == '"' && "${#val}" -ge 2 ]]; then
    val="${val:1:-1}"
  fi
  printf '%s' "$val"
}

# --- carrega valores existentes do .env, se houver, para não perder nada ---
EXISTING_SECRET=""
EXISTING_TS_SITE=""
EXISTING_TS_SECRET=""
EXISTING_MAX_VOTERS="6"
EXISTING_COOKIE_SECURE="true"
EXISTING_GOOGLE_BOOKS_KEY=""
EXISTING_RESEND_KEY=""
EXISTING_RESEND_FROM="Enquete de Livros <onboarding@resend.dev>"
EXISTING_BASE_URL=""
EXISTING_STORAGE_BACKEND="local"
EXISTING_STORAGE_BUCKET=""
EXISTING_STORAGE_PREFIX=""
EXISTING_STORAGE_PUBLIC_BASE_URL=""
EXISTING_STORAGE_ENDPOINT_URL=""
EXISTING_STORAGE_LOCAL_FALLBACK="true"
EXISTING_R2_ACCOUNT_ID=""
EXISTING_R2_ACCESS_KEY_ID=""
EXISTING_R2_SECRET_ACCESS_KEY=""
EXISTING_GCS_HMAC_ACCESS_KEY=""
EXISTING_GCS_HMAC_SECRET=""

if [[ -f "$ENV_FILE" ]]; then
  EXISTING_SECRET="$(get_env_value BOOKVOTE_SECRET_KEY "$ENV_FILE")"
  EXISTING_TS_SITE="$(get_env_value TURNSTILE_SITE_KEY "$ENV_FILE")"
  EXISTING_TS_SECRET="$(get_env_value TURNSTILE_SECRET_KEY "$ENV_FILE")"
  EXISTING_MAX_VOTERS="$(get_env_value BOOKVOTE_MAX_VOTERS_PER_IP "$ENV_FILE")"
  EXISTING_MAX_VOTERS="${EXISTING_MAX_VOTERS:-6}"
  EXISTING_COOKIE_SECURE="$(get_env_value BOOKVOTE_COOKIE_SECURE "$ENV_FILE")"
  EXISTING_COOKIE_SECURE="${EXISTING_COOKIE_SECURE:-true}"
  EXISTING_GOOGLE_BOOKS_KEY="$(get_env_value GOOGLE_BOOKS_API_KEY "$ENV_FILE")"
  EXISTING_RESEND_KEY="$(get_env_value RESEND_API_KEY "$ENV_FILE")"
  val="$(get_env_value RESEND_FROM_EMAIL "$ENV_FILE")"
  EXISTING_RESEND_FROM="${val:-Enquete de Livros <onboarding@resend.dev>}"
  EXISTING_BASE_URL="$(get_env_value BOOKVOTE_BASE_URL "$ENV_FILE")"
  val="$(get_env_value STORAGE_BACKEND "$ENV_FILE")"
  EXISTING_STORAGE_BACKEND="${val:-local}"
  EXISTING_STORAGE_BUCKET="$(get_env_value STORAGE_BUCKET "$ENV_FILE")"
  EXISTING_STORAGE_PREFIX="$(get_env_value STORAGE_PREFIX "$ENV_FILE")"
  EXISTING_STORAGE_PUBLIC_BASE_URL="$(get_env_value STORAGE_PUBLIC_BASE_URL "$ENV_FILE")"
  EXISTING_STORAGE_ENDPOINT_URL="$(get_env_value STORAGE_ENDPOINT_URL "$ENV_FILE")"
  val="$(get_env_value STORAGE_LOCAL_FALLBACK "$ENV_FILE")"
  EXISTING_STORAGE_LOCAL_FALLBACK="${val:-true}"
  EXISTING_R2_ACCOUNT_ID="$(get_env_value R2_ACCOUNT_ID "$ENV_FILE")"
  EXISTING_R2_ACCESS_KEY_ID="$(get_env_value R2_ACCESS_KEY_ID "$ENV_FILE")"
  EXISTING_R2_SECRET_ACCESS_KEY="$(get_env_value R2_SECRET_ACCESS_KEY "$ENV_FILE")"
  EXISTING_GCS_HMAC_ACCESS_KEY="$(get_env_value GCS_HMAC_ACCESS_KEY "$ENV_FILE")"
  EXISTING_GCS_HMAC_SECRET="$(get_env_value GCS_HMAC_SECRET "$ENV_FILE")"
  cp "$ENV_FILE" "$ENV_FILE.bak.$(date +%s)"
  echo "Backup do .env anterior salvo em $ENV_FILE.bak.<timestamp>"
fi

SECRET_KEY="$EXISTING_SECRET"
if [[ -z "$SECRET_KEY" ]]; then
  SECRET_KEY="$(gen_secret)"
  echo "Gerada nova BOOKVOTE_SECRET_KEY."
fi

if [[ -z "$TURNSTILE_SITE" ]]; then
  TURNSTILE_SITE="$(ask "Turnstile site key (Cloudflare, deixe em branco para desativar captcha)" "$EXISTING_TS_SITE")"
fi
if [[ -z "$TURNSTILE_SECRET" ]]; then
  TURNSTILE_SECRET="$(ask "Turnstile secret key" "$EXISTING_TS_SECRET")"
fi
if [[ -z "$MAX_VOTERS" ]]; then
  MAX_VOTERS="$(ask "Máximo de votantes distintos por IP/enquete" "$EXISTING_MAX_VOTERS")"
fi
if [[ -z "$GOOGLE_BOOKS_KEY" ]]; then
  GOOGLE_BOOKS_KEY="$(ask "Google Books API key (opcional, deixe em branco para cota pública)" "$EXISTING_GOOGLE_BOOKS_KEY")"
fi
if [[ -z "$RESEND_KEY" ]]; then
  RESEND_KEY="$(ask "Resend API key (opcional, deixe em branco para não enviar e-mails)" "$EXISTING_RESEND_KEY")"
fi
if [[ -z "$RESEND_FROM" ]]; then
  RESEND_FROM="$(ask "Remetente dos e-mails (Resend)" "$EXISTING_RESEND_FROM")"
fi
if [[ -z "$COOKIE_SECURE" ]]; then
  COOKIE_SECURE="$(ask "Exigir HTTPS para o cookie de votante (true/false)" "$EXISTING_COOKIE_SECURE")"
fi
if [[ -z "$BASE_URL" ]]; then
  BASE_URL="$(ask "URL pública completa (ex.: https://enquete.seudominio.com.br, sem barra no final) — necessária para o envio pontual de e-mails de mudança de fase" "$EXISTING_BASE_URL")"
fi

# --- armazenamento de arquivos (disco local, Cloudflare R2 ou GCS) ---
if [[ -z "$STORAGE_BACKEND" ]]; then
  STORAGE_BACKEND="$(ask "Armazenamento de arquivos enviados: local, r2 (Cloudflare) ou gcs (Google Cloud)" "$EXISTING_STORAGE_BACKEND")"
fi
STORAGE_BACKEND="$(printf '%s' "$STORAGE_BACKEND" | tr '[:upper:]' '[:lower:]')"
case "$STORAGE_BACKEND" in
  local|r2|gcs) ;;
  *) echo "STORAGE_BACKEND inválido: '$STORAGE_BACKEND' (use local, r2 ou gcs)." >&2; exit 1 ;;
esac

if [[ "$STORAGE_BACKEND" != "local" ]]; then
  [[ -z "$STORAGE_BUCKET" ]] && STORAGE_BUCKET="$(ask "Nome do bucket" "$EXISTING_STORAGE_BUCKET")"
  if [[ "$STORAGE_BACKEND" == "r2" ]]; then
    [[ -z "$R2_ACCOUNT_ID" ]] && R2_ACCOUNT_ID="$(ask "R2 Account ID" "$EXISTING_R2_ACCOUNT_ID")"
    [[ -z "$R2_ACCESS_KEY_ID" ]] && R2_ACCESS_KEY_ID="$(ask "R2 Access Key ID" "$EXISTING_R2_ACCESS_KEY_ID")"
    [[ -z "$R2_SECRET_ACCESS_KEY" ]] && R2_SECRET_ACCESS_KEY="$(ask_secret "R2 Secret Access Key" "$EXISTING_R2_SECRET_ACCESS_KEY")"
  else
    [[ -z "$GCS_HMAC_ACCESS_KEY" ]] && GCS_HMAC_ACCESS_KEY="$(ask "GCS HMAC Access Key" "$EXISTING_GCS_HMAC_ACCESS_KEY")"
    [[ -z "$GCS_HMAC_SECRET" ]] && GCS_HMAC_SECRET="$(ask_secret "GCS HMAC Secret" "$EXISTING_GCS_HMAC_SECRET")"
  fi
  [[ -z "$STORAGE_PREFIX" ]] && STORAGE_PREFIX="$(ask "Prefixo ('pasta') dentro do bucket (opcional)" "$EXISTING_STORAGE_PREFIX")"
  [[ -z "$STORAGE_PUBLIC_BASE_URL" ]] && STORAGE_PUBLIC_BASE_URL="$(ask "URL pública do bucket/CDN (opcional; vazio = o app serve /media, funciona com bucket privado)" "$EXISTING_STORAGE_PUBLIC_BASE_URL")"
  [[ -z "$STORAGE_ENDPOINT_URL" ]] && STORAGE_ENDPOINT_URL="$(ask "Endpoint S3 customizado (opcional; vazio = padrão do provedor)" "$EXISTING_STORAGE_ENDPOINT_URL")"
  [[ -z "$STORAGE_LOCAL_FALLBACK" ]] && STORAGE_LOCAL_FALLBACK="$(ask "Ler do disco o que ainda não foi migrado para o bucket (true/false)" "$EXISTING_STORAGE_LOCAL_FALLBACK")"

  missing=()
  [[ -z "$STORAGE_BUCKET" ]] && missing+=(STORAGE_BUCKET)
  if [[ "$STORAGE_BACKEND" == "r2" ]]; then
    [[ -z "$R2_ACCOUNT_ID" ]] && missing+=(R2_ACCOUNT_ID)
    [[ -z "$R2_ACCESS_KEY_ID" ]] && missing+=(R2_ACCESS_KEY_ID)
    [[ -z "$R2_SECRET_ACCESS_KEY" ]] && missing+=(R2_SECRET_ACCESS_KEY)
  else
    [[ -z "$GCS_HMAC_ACCESS_KEY" ]] && missing+=(GCS_HMAC_ACCESS_KEY)
    [[ -z "$GCS_HMAC_SECRET" ]] && missing+=(GCS_HMAC_SECRET)
  fi
  if (( ${#missing[@]} > 0 )); then
    echo "Erro: STORAGE_BACKEND=$STORAGE_BACKEND exige: ${missing[*]}" >&2
    echo "(o app se recusaria a subir com essa configuração; .env não foi alterado)" >&2
    exit 1
  fi
fi

esc() {
  # Single-quotes the value for .env: since this file gets `source`d by
  # this very script on its next run (to preserve existing settings),
  # every value needs to survive being parsed as shell. Single quotes
  # block all expansion ($, `, \, "...), so this is safe even for things
  # like the default RESEND_FROM_EMAIL, which contains spaces and <>.
  # Embedded single quotes are escaped with the classic '\'' trick.
  printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

# Variáveis que este script não gerencia (GEMINI_*, MATERIAL_MAX_MB...) são lidas ANTES de reescrever o arquivo e devolvidas no
# fim, como texto, sem interpretar nada — senão uma nova execução apagaria
# a leitura automática do cronograma e outros ajustes manuais.
EXTRA_LINES=""
if [[ -f "$ENV_FILE" ]]; then
  EXTRA_LINES="$(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$ENV_FILE" 2>/dev/null \
    | grep -Ev '^(BOOKVOTE_SECRET_KEY|TURNSTILE_SITE_KEY|TURNSTILE_SECRET_KEY|BOOKVOTE_COOKIE_SECURE|BOOKVOTE_MAX_VOTERS_PER_IP|GOOGLE_BOOKS_API_KEY|RESEND_API_KEY|RESEND_FROM_EMAIL|BOOKVOTE_BASE_URL|STORAGE_BACKEND|STORAGE_BUCKET|STORAGE_PREFIX|STORAGE_PUBLIC_BASE_URL|STORAGE_ENDPOINT_URL|STORAGE_LOCAL_FALLBACK|R2_ACCOUNT_ID|R2_ACCESS_KEY_ID|R2_SECRET_ACCESS_KEY|GCS_HMAC_ACCESS_KEY|GCS_HMAC_SECRET)=' || true)"
fi

cat > "$ENV_FILE" <<EOF
BOOKVOTE_SECRET_KEY=$(esc "$SECRET_KEY")
TURNSTILE_SITE_KEY=$(esc "$TURNSTILE_SITE")
TURNSTILE_SECRET_KEY=$(esc "$TURNSTILE_SECRET")
BOOKVOTE_COOKIE_SECURE=$(esc "$COOKIE_SECURE")
BOOKVOTE_MAX_VOTERS_PER_IP=$(esc "$MAX_VOTERS")
GOOGLE_BOOKS_API_KEY=$(esc "$GOOGLE_BOOKS_KEY")
RESEND_API_KEY=$(esc "$RESEND_KEY")
RESEND_FROM_EMAIL=$(esc "$RESEND_FROM")
BOOKVOTE_BASE_URL=$(esc "$BASE_URL")
STORAGE_BACKEND=$(esc "$STORAGE_BACKEND")
EOF

if [[ "$STORAGE_BACKEND" != "local" ]]; then
  {
    echo "STORAGE_BUCKET=$(esc "$STORAGE_BUCKET")"
    echo "STORAGE_PREFIX=$(esc "$STORAGE_PREFIX")"
    echo "STORAGE_PUBLIC_BASE_URL=$(esc "$STORAGE_PUBLIC_BASE_URL")"
    echo "STORAGE_ENDPOINT_URL=$(esc "$STORAGE_ENDPOINT_URL")"
    echo "STORAGE_LOCAL_FALLBACK=$(esc "$STORAGE_LOCAL_FALLBACK")"
    if [[ "$STORAGE_BACKEND" == "r2" ]]; then
      echo "R2_ACCOUNT_ID=$(esc "$R2_ACCOUNT_ID")"
      echo "R2_ACCESS_KEY_ID=$(esc "$R2_ACCESS_KEY_ID")"
      echo "R2_SECRET_ACCESS_KEY=$(esc "$R2_SECRET_ACCESS_KEY")"
    else
      echo "GCS_HMAC_ACCESS_KEY=$(esc "$GCS_HMAC_ACCESS_KEY")"
      echo "GCS_HMAC_SECRET=$(esc "$GCS_HMAC_SECRET")"
    fi
  } >> "$ENV_FILE"
fi

if [[ -n "$EXTRA_LINES" ]]; then
  printf '\n# preservado do .env anterior (não gerenciado por este script)\n%s\n' "$EXTRA_LINES" >> "$ENV_FILE"
fi

chmod 600 "$ENV_FILE"
echo ".env escrito em $ENV_FILE (permissão 600)."

if [[ -z "$TURNSTILE_SITE" || -z "$TURNSTILE_SECRET" ]]; then
  echo
  echo "Aviso: Turnstile não configurado — o captcha ficará DESATIVADO."
  echo "Recomendado para produção: crie chaves em https://dash.cloudflare.com/ > Turnstile"
  echo "e rode este script de novo (ou edite o .env manualmente)."
fi

if [[ -z "$RESEND_KEY" ]]; then
  echo
  echo "Aviso: Resend não configurado — e-mails de administração (criação e"
  echo "recuperação de link) não serão enviados. Crie uma API key em"
  echo "https://resend.com/api-keys e rode este script de novo."
fi

if [[ -n "$RESEND_KEY" && -z "$BASE_URL" ]]; then
  echo
  echo "Aviso: BOOKVOTE_BASE_URL não configurada — os e-mails de mudança de"
  echo "fase (indicações encerradas, empates, resultado final) só vão sair"
  echo "quando alguém abrir a enquete ou o painel de admin, o que pode levar"
  echo "horas numa enquete com pouco tráfego. Rode este script de novo com"
  echo "--base-url https://seudominio.com.br e agende scripts/notify.py no"
  echo "crontab do host (veja o README) para envio pontual."
fi

if [[ "$STORAGE_BACKEND" != "local" ]]; then
  echo
  echo "Bucket configurado ($STORAGE_BACKEND). Depois de subir o container:"
  echo "  docker compose exec web python scripts/manage.py storage-check"
  echo "  docker compose exec web python scripts/manage.py storage-migrate --dry-run"
fi

echo
echo "Pronto. Para subir/atualizar o container:"
echo "  docker compose up -d --build"
echo "Depois, aponte o nginx pro container usando deploy/nginx-bookvote.conf."