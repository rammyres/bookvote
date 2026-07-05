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
#     --max-voters 8 --google-books-key SUA_CHAVE
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

usage() {
  grep '^#' "$0" | sed 's/^# \{0,1\}//' | sed -n '2,20p'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes) NONINTERACTIVE=true; shift ;;
    --turnstile-site) TURNSTILE_SITE="$2"; shift 2 ;;
    --turnstile-secret) TURNSTILE_SECRET="$2"; shift 2 ;;
    --max-voters) MAX_VOTERS="$2"; shift 2 ;;
    --cookie-secure) COOKIE_SECURE="$2"; shift 2 ;;
    --google-books-key) GOOGLE_BOOKS_KEY="$2"; shift 2 ;;
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

# --- carrega valores existentes do .env, se houver, para não perder nada ---
EXISTING_SECRET=""
EXISTING_TS_SITE=""
EXISTING_TS_SECRET=""
EXISTING_MAX_VOTERS="6"
EXISTING_COOKIE_SECURE="true"
EXISTING_GOOGLE_BOOKS_KEY=""

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a
  source "$ENV_FILE"
  set +a
  EXISTING_SECRET="${BOOKVOTE_SECRET_KEY:-}"
  EXISTING_TS_SITE="${TURNSTILE_SITE_KEY:-}"
  EXISTING_TS_SECRET="${TURNSTILE_SECRET_KEY:-}"
  EXISTING_MAX_VOTERS="${BOOKVOTE_MAX_VOTERS_PER_IP:-6}"
  EXISTING_COOKIE_SECURE="${BOOKVOTE_COOKIE_SECURE:-true}"
  EXISTING_GOOGLE_BOOKS_KEY="${GOOGLE_BOOKS_API_KEY:-}"
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
if [[ -z "$COOKIE_SECURE" ]]; then
  COOKIE_SECURE="$(ask "Exigir HTTPS para o cookie de votante (true/false)" "$EXISTING_COOKIE_SECURE")"
fi
cat > "$ENV_FILE" <<EOF
BOOKVOTE_SECRET_KEY=$SECRET_KEY
TURNSTILE_SITE_KEY=$TURNSTILE_SITE
TURNSTILE_SECRET_KEY=$TURNSTILE_SECRET
BOOKVOTE_COOKIE_SECURE=$COOKIE_SECURE
BOOKVOTE_MAX_VOTERS_PER_IP=$MAX_VOTERS
GOOGLE_BOOKS_API_KEY=$GOOGLE_BOOKS_KEY
EOF

chmod 600 "$ENV_FILE"
echo ".env escrito em $ENV_FILE (permissão 600)."

if [[ -z "$TURNSTILE_SITE" || -z "$TURNSTILE_SECRET" ]]; then
  echo
  echo "Aviso: Turnstile não configurado — o captcha ficará DESATIVADO."
  echo "Recomendado para produção: crie chaves em https://dash.cloudflare.com/ > Turnstile"
  echo "e rode este script de novo (ou edite o .env manualmente)."
fi

echo
echo "Pronto. Para subir/atualizar o container:"
echo "  docker compose up -d --build"
echo "Depois, aponte o nginx pro container usando deploy/nginx-bookvote.conf."
