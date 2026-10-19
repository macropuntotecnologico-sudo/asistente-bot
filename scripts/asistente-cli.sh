#!/usr/bin/env bash
# Atajos para administrar el asistente. El instalador reemplaza las marcas @...@.
set -euo pipefail

APP="@APP@"
VENV="@VENV@"
DATOS="@DATOS@"
ENVF="@ENVF@"
USUARIO="@USUARIO@"
SERVICIO="@SERVICIO@"

conectar_gmail() {
  local usuario clave
  echo "Conectar Gmail (solo lectura: el bot nunca envía, borra ni marca correos)."
  echo "Necesitas una contraseña de aplicación: https://myaccount.google.com/apppasswords"
  read -r -p "Tu correo de Gmail: " usuario < /dev/tty
  usuario="${usuario//[[:space:]]/}"
  read -r -s -p "Contraseña de aplicación (16 letras; no se verá en pantalla): " clave < /dev/tty
  echo
  clave="${clave//[[:space:]]/}"
  if [[ -z "$usuario" || -z "$clave" ]]; then
    echo "✖ Faltó el correo o la contraseña."; exit 1
  fi
  echo "Probando la conexión con Gmail..."
  if ! GM_U="$usuario" GM_P="$clave" "$VENV/bin/python" -c '
import imaplib, os
m = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=20)
m.login(os.environ["GM_U"], os.environ["GM_P"])
m.logout()' 2>/dev/null; then
    echo "✖ Gmail rechazó ese correo o esa contraseña de aplicación. Revísalos e inténtalo otra vez."
    exit 1
  fi
  sed -i '/^GMAIL_USUARIO=/d;/^GMAIL_CLAVE_APP=/d' "$ENVF"
  printf 'GMAIL_USUARIO=%s\nGMAIL_CLAVE_APP=%s\n' "$usuario" "$clave" >> "$ENVF"
  grep -q '^ALERTAS_CORREO_MIN=' "$ENVF" || echo 'ALERTAS_CORREO_MIN=15' >> "$ENVF"
  systemctl restart "$SERVICIO"
  echo "✔ Gmail conectado. Escríbele /correo al bot para probar."
}

case "${1:-}" in
  estado)     systemctl status "$SERVICIO" --no-pager ;;
  logs)       journalctl -u "$SERVICIO" -f ;;
  reiniciar)  systemctl restart "$SERVICIO" && echo "Reiniciado." ;;
  config)     "${EDITOR:-nano}" "$ENVF" && systemctl restart "$SERVICIO" && echo "Configuración aplicada." ;;
  gmail)      conectar_gmail ;;
  respaldo)   cd "$APP" && runuser -u "$USUARIO" -- env CARPETA_DATOS="$DATOS" "$VENV/bin/python" -m asistente.respaldo "${2:-$DATOS/respaldo-$(date +%F).db}" ;;
  actualizar) cp "$APP/instalar.sh" /tmp/instalar-asistente.sh && bash /tmp/instalar-asistente.sh ;;
  *)          echo "Uso: asistente {estado|logs|reiniciar|config|gmail|respaldo|actualizar}" ;;
esac
