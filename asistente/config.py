"""Configuración desde variables de entorno (archivo .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ErrorConfig(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    telegram_token: str
    telegram_user_id: int | None
    anthropic_api_key: str
    claude_modelo: str
    zona: ZoneInfo
    resumen_hora: time | None
    whisper_modelo: str | None
    carpeta_datos: Path
    nombre_usuario: str
    gmail_usuario: str | None = None
    gmail_clave_app: str | None = None
    alertas_correo_min: int = 15

    @property
    def gmail_activo(self) -> bool:
        return bool(self.gmail_usuario and self.gmail_clave_app)

    @property
    def ruta_db(self) -> Path:
        return self.carpeta_datos / "asistente.db"

    @property
    def carpeta_modelos(self) -> Path:
        return self.carpeta_datos / "modelos"


def _hora(texto: str) -> time | None:
    texto = texto.strip().lower()
    if texto in ("", "off", "no", "0"):
        return None
    try:
        horas, minutos = texto.split(":")
        return time(int(horas), int(minutos))
    except ValueError:
        raise ErrorConfig(f"RESUMEN_HORA inválida: {texto!r}. Usa HH:MM (24 h) u 'off'.") from None


def cargar(entorno: dict[str, str] | None = None) -> Config:
    e = os.environ if entorno is None else entorno
    faltan = [v for v in ("TELEGRAM_TOKEN", "ANTHROPIC_API_KEY") if not e.get(v, "").strip()]
    if faltan:
        raise ErrorConfig(f"Faltan variables en el .env: {', '.join(faltan)}")

    usuario = e.get("TELEGRAM_USER_ID", "").strip()
    if usuario and not usuario.lstrip("-").isdigit():
        raise ErrorConfig("TELEGRAM_USER_ID debe ser un número (el bot te lo dice con /start).")

    nombre_zona = e.get("ZONA_HORARIA", "America/Bogota").strip() or "America/Bogota"
    try:
        zona = ZoneInfo(nombre_zona)
    except ZoneInfoNotFoundError:
        raise ErrorConfig(f"ZONA_HORARIA desconocida: {nombre_zona!r}") from None

    whisper = e.get("WHISPER_MODELO", "small").strip()
    if whisper.lower() in ("", "off", "no", "0"):
        whisper = None

    gmail_usuario = e.get("GMAIL_USUARIO", "").strip() or None
    gmail_clave = e.get("GMAIL_CLAVE_APP", "").replace(" ", "").strip() or None
    if gmail_usuario and "@" not in gmail_usuario:
        raise ErrorConfig("GMAIL_USUARIO debe ser un correo completo, por ejemplo tucorreo@gmail.com.")
    texto_alertas = e.get("ALERTAS_CORREO_MIN", "15").strip().lower()
    if texto_alertas in ("", "off", "no"):
        texto_alertas = "0"
    if not texto_alertas.isdigit():
        raise ErrorConfig("ALERTAS_CORREO_MIN debe ser un número de minutos (0 u off para apagarlas).")

    return Config(
        telegram_token=e["TELEGRAM_TOKEN"].strip(),
        telegram_user_id=int(usuario) if usuario else None,
        anthropic_api_key=e["ANTHROPIC_API_KEY"].strip(),
        claude_modelo=e.get("CLAUDE_MODELO", "").strip() or "claude-haiku-5-5",
        zona=zona,
        resumen_hora=_hora(e.get("RESUMEN_HORA", "07:00")),
        whisper_modelo=whisper,
        carpeta_datos=Path(e.get("CARPETA_DATOS", "/data").strip() or "/data"),
        nombre_usuario=e.get("NOMBRE_USUARIO", "").strip() or "tu jefe",
        gmail_usuario=gmail_usuario,
        gmail_clave_app=gmail_clave,
        alertas_correo_min=int(texto_alertas),
    )
