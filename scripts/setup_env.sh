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
    --resend-key) RESEND_KEY="$2"; shift 2 ;;
    --resend-from) RESEND_FROM="$2"; shift 2 ;;
    --base-url) BASE_URL="$2"; shift 2 ;;
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
esc() {
  # Single-quotes the value for .env: since this file gets `source`d by
  # this very script on its next run (to preserve existing settings),
  # every value needs to survive being parsed as shell. Single quotes
  # block all expansion ($, `, \, "...), so this is safe even for things
  # like the default RESEND_FROM_EMAIL, which contains spaces and <>.
  # Embedded single quotes are escaped with the classic '\'' trick.
  printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

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
EOF

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
  echo "--base-url https://seudominio.com.br para envio pontual em segundo plano."
fi

echo
echo "Pronto. Para subir/atualizar o container:"
echo "  docker compose up -d --build"
echo "Depois, aponte o nginx pro container usando deploy/nginx-bookvote.conf."