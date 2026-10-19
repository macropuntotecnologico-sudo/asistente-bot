#!/usr/bin/env bash
# Instalador del asistente personal por Telegram.
#
# Uso, como root, en Debian 12/13 o Ubuntu 22.04+ (por ejemplo un LXC de Proxmox):
#   apt update && apt install -y curl
#   bash <(curl -fsSL https://raw.githubusercontent.com/macropuntotecnologico-sudo/asistente-bot/main/instalar.sh)
#
# Volver a correrlo actualiza el bot y conserva tu configuración.
set -euo pipefail

REPO="${ASISTENTE_REPO:-https://github.com/macropuntotecnologico-sudo/asistente-bot.git}"
RAMA="${ASISTENTE_RAMA:-main}"
BASE="${ASISTENTE_BASE:-/opt/asistente}"
API_TELEGRAM="${ASISTENTE_API_TELEGRAM:-https://api.telegram.org}"
API_ANTHROPIC="${ASISTENTE_API_ANTHROPIC:-https://api.anthropic.com}"
APP="$BASE/app"
VENV="$BASE/venv"
DATOS="$BASE/datos"
ENVF="$BASE/.env"
USUARIO="asistente"
SERVICIO="asistente"

paso()     { printf '\n\033[1m▶ %s\033[0m\n' "$*"; }
bien()     { printf '\033[32m✔ %s\033[0m\n' "$*"; }
aviso()    { printf '\033[33m! %s\033[0m\n' "$*"; }
morir()    { printf '\033[31m✖ %s\033[0m\n' "$*" >&2; exit 1; }

# preguntar "texto" variable [por_defecto]: lee de la terminal aunque el script venga por tubería.
preguntar() {
  local texto="$1" __var="$2" defecto="${3:-}" respuesta=""
  [[ -n "$defecto" ]] && texto="$texto [$defecto]"
  read -r -p "$texto: " respuesta < /dev/tty || true
  printf -v "$__var" '%s' "${respuesta:-$defecto}"
}

json() { # json 'expresión python sobre d' < archivo_json
  python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"
}

instalar_paquetes() {
  paso "Instalando paquetes del sistema"
  if [[ "${ASISTENTE_SIN_APT:-0}" == "1" ]]; then
    aviso "Saltando apt (ASISTENTE_SIN_APT=1)"
  else
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq git python3 python3-venv ca-certificates curl nano >/dev/null
  fi
  python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' \
    || morir "Se necesita Python 3.10 o más nuevo (Debian 12 o Ubuntu 22.04 en adelante)."
  bien "Paquetes listos ($(python3 --version))"
}

preparar_usuario() {
  if ! id -u "$USUARIO" >/dev/null 2>&1; then
    useradd --system --home-dir "$DATOS" --shell /usr/sbin/nologin "$USUARIO"
  fi
  mkdir -p "$BASE" "$DATOS"
  chmod 755 "$BASE"
  chown "$USUARIO:$USUARIO" "$DATOS"
  chmod 750 "$DATOS"
}

bajar_codigo() {
  paso "Descargando el código"
  if [[ -d "$APP/.git" ]]; then
    git -C "$APP" fetch --quiet --depth 1 origin "$RAMA"
    git -C "$APP" reset --quiet --hard "origin/$RAMA"
  else
    rm -rf "$APP"
    git clone --quiet --depth 1 --branch "$RAMA" "$REPO" "$APP"
  fi
  bien "Código en $APP ($(git -C "$APP" log -1 --format=%h))"
}

instalar_python() {
  paso "Instalando librerías de Python (puede tardar unos minutos)"
  [[ -x "$VENV/bin/python" ]] || python3 -m venv "$VENV"
  "$VENV/bin/pip" install --quiet --disable-pip-version-check --upgrade pip
  "$VENV/bin/pip" install --quiet --disable-pip-version-check -r "$APP/requirements.txt"
  bien "Librerías listas"
}

pedir_token() {
  local respuesta
  while true; do
    preguntar "Pega el token de tu bot (te lo dio @BotFather)" TOKEN
    TOKEN="${TOKEN//[[:space:]]/}"
    if [[ -n "$TOKEN" ]] && respuesta=$(curl -fsS --max-time 15 "$API_TELEGRAM/bot$TOKEN/getMe" 2>/dev/null); then
      BOT_USUARIO=$(json 'd["result"]["username"]' <<<"$respuesta")
      bien "Token válido: @$BOT_USUARIO"
      return
    fi
    aviso "Ese token no funcionó. Revísalo en @BotFather (/mybots > tu bot > API Token)."
  done
}

pedir_clave() {
  local codigo
  while true; do
    preguntar "Pega tu clave de la API de Anthropic (empieza por sk-ant-)" CLAVE
    CLAVE="${CLAVE//[[:space:]]/}"
    [[ -z "$CLAVE" ]] && continue
    codigo=$(curl -sS --max-time 15 -o /dev/null -w '%{http_code}' "$API_ANTHROPIC/v1/models" \
      -H "x-api-key: $CLAVE" -H "anthropic-version: 2023-06-01" 2>/dev/null || echo "000")
    case "$codigo" in
      200) bien "Clave válida"; return ;;
      401|403) aviso "Anthropic rechazó esa clave. Revísala en console.anthropic.com > API Keys." ;;
      *) aviso "No pude comprobar la clave (respuesta $codigo). La guardo igual; si falla, la cambias con: asistente config"; return ;;
    esac
  done
}

detectar_id() {
  local respuesta datos nombre confirmar
  curl -fsS --max-time 15 "$API_TELEGRAM/bot$TOKEN/deleteWebhook" >/dev/null 2>&1 || true
  echo
  echo "Ahora abre Telegram, busca @$BOT_USUARIO y envíale /start."
  for _ in 1 2 3; do
    preguntar "Cuando lo hayas enviado, presiona Enter" _nada
    if respuesta=$(curl -fsS --max-time 15 "$API_TELEGRAM/bot$TOKEN/getUpdates" 2>/dev/null); then
      datos=$(json '"\t".join(map(str, next(((u["message"]["from"]["id"], u["message"]["from"].get("first_name", "")) for u in reversed(d.get("result", [])) if "message" in u and u["message"]["chat"]["type"] == "private"), ("", ""))))' <<<"$respuesta")
      TELEGRAM_ID="${datos%%$'\t'*}"
      nombre="${datos#*$'\t'}"
      if [[ -n "$TELEGRAM_ID" ]]; then
        preguntar "Recibí el mensaje de $nombre (ID $TELEGRAM_ID). ¿Eres tú? (s/n)" confirmar "s"
        if [[ "${confirmar,,}" == s* ]]; then
          NOMBRE_DETECTADO="$nombre"
          bien "ID guardado: $TELEGRAM_ID"
          return
        fi
      else
        aviso "Todavía no me llega ningún mensaje. Escríbele /start a @$BOT_USUARIO e inténtalo otra vez."
      fi
    else
      aviso "No pude leer los mensajes del bot. ¿Hay otra copia del bot corriendo en otro lado?"
    fi
  done
  preguntar "Escribe tu ID de Telegram a mano (lo ves con @userinfobot)" TELEGRAM_ID
  [[ "$TELEGRAM_ID" =~ ^[0-9]+$ ]] || morir "El ID debe ser un número."
}

configurar() {
  paso "Configuración"
  if [[ -f "$ENVF" ]]; then
    grep -q '^CARPETA_DATOS=' "$ENVF" || echo "CARPETA_DATOS=$DATOS" >> "$ENVF"
    bien "Se conserva tu configuración actual ($ENVF)"
    return
  fi
  TOKEN="${TELEGRAM_TOKEN:-}"; CLAVE="${ANTHROPIC_API_KEY:-}"; TELEGRAM_ID="${TELEGRAM_USER_ID:-}"
  NOMBRE_DETECTADO=""
  if [[ -n "$TOKEN" ]]; then
    BOT_USUARIO=$(curl -fsS --max-time 15 "$API_TELEGRAM/bot$TOKEN/getMe" | json 'd["result"]["username"]') \
      || morir "El TELEGRAM_TOKEN del entorno no es válido."
  else
    pedir_token
  fi
  [[ -n "$CLAVE" ]] || pedir_clave
  [[ -n "$TELEGRAM_ID" ]] || detectar_id
  local nombre="${NOMBRE_USUARIO:-}"
  [[ -n "$nombre" ]] || preguntar "¿Cómo quieres que te llame?" nombre "${NOMBRE_DETECTADO:-Manuelle}"
  nombre="${nombre//\"/}"

  ( umask 077
    cat > "$ENVF" <<EOF
# Configuración del asistente. Después de cambiar algo: systemctl restart $SERVICIO
TELEGRAM_TOKEN=$TOKEN
ANTHROPIC_API_KEY=$CLAVE
TELEGRAM_USER_ID=$TELEGRAM_ID
NOMBRE_USUARIO="$nombre"
CLAUDE_MODELO=claude-haiku-5-5
ZONA_HORARIA=America/Bogota
# Hora del resumen diario (24 h) u off
RESUMEN_HORA=07:00
# Notas de voz: small, base u off
WHISPER_MODELO=small
CARPETA_DATOS=$DATOS
EOF
  )
  bien "Configuración guardada en $ENVF (solo root puede leerla)"
}

instalar_comando() {
  cat > /usr/local/bin/asistente <<EOF
#!/usr/bin/env bash
# Atajos para administrar el asistente.
set -euo pipefail
case "\${1:-}" in
  estado)     systemctl status $SERVICIO --no-pager ;;
  logs)       journalctl -u $SERVICIO -f ;;
  reiniciar)  systemctl restart $SERVICIO && echo "Reiniciado." ;;
  config)     \${EDITOR:-nano} $ENVF && systemctl restart $SERVICIO && echo "Configuración aplicada." ;;
  respaldo)   cd $APP && runuser -u $USUARIO -- env CARPETA_DATOS=$DATOS $VENV/bin/python -m asistente.respaldo "\${2:-$DATOS/respaldo-\$(date +%F).db}" ;;
  actualizar) cp $APP/instalar.sh /tmp/instalar-asistente.sh && bash /tmp/instalar-asistente.sh ;;
  *)          echo "Uso: asistente {estado|logs|reiniciar|config|respaldo|actualizar}" ;;
esac
EOF
  chmod 755 /usr/local/bin/asistente
}

instalar_servicio() {
  paso "Dejando el bot como servicio (arranca solo al prender el servidor)"
  cat > "/etc/systemd/system/$SERVICIO.service" <<EOF
[Unit]
Description=Asistente personal por Telegram
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USUARIO
Group=$USUARIO
WorkingDirectory=$APP
EnvironmentFile=$ENVF
Environment=HOME=$DATOS HF_HOME=$DATOS/hf HF_HUB_DISABLE_TELEMETRY=1 PYTHONUNBUFFERED=1
ExecStart=$VENV/bin/python -m asistente
Restart=always
RestartSec=10
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF
  instalar_comando
  if [[ "${ASISTENTE_SIN_SYSTEMD:-0}" == "1" ]]; then
    aviso "Saltando systemd (ASISTENTE_SIN_SYSTEMD=1)"
    return
  fi
  systemctl daemon-reload
  systemctl enable --quiet "$SERVICIO"
  systemctl restart "$SERVICIO"
  sleep 6
  if systemctl is-active --quiet "$SERVICIO"; then
    bien "El bot está corriendo"
  else
    journalctl -u "$SERVICIO" -n 30 --no-pager || true
    morir "El bot no arrancó. Arriba están los últimos mensajes del registro."
  fi
}

main() {
  [[ $EUID -eq 0 ]] || morir "Corre el instalador como root."
  command -v apt-get >/dev/null || morir "Este instalador es para Debian o Ubuntu."
  echo "Asistente personal por Telegram: instalación"
  instalar_paquetes
  preparar_usuario
  bajar_codigo
  instalar_python
  configurar
  instalar_servicio

  cat <<EOF

$(bien "¡Listo!")
Escríbele a tu bot en Telegram: "recuérdame en 2 minutos probar el bot".
La primera vez descarga el modelo de notas de voz (~500 MB) en segundo plano.

Atajos en este servidor:
  asistente logs        ver qué está pasando
  asistente estado      ver si está corriendo
  asistente config      cambiar la configuración
  asistente respaldo    copia de seguridad de tus recordatorios
  asistente actualizar  instalar la última versión
EOF
}

main "$@"
