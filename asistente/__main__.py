"""Punto de entrada: python -m asistente"""

from __future__ import annotations

import logging
import sys

import anthropic
from telegram import Update

from . import config as configuracion
from .almacen import Almacen
from .bot import AsistenteBot
from .cerebro import Cerebro
from .correo import AnalistaCorreo, Buzon
from .servicio import Servicio
from .transcripcion import Transcriptor


def main() -> int:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=logging.INFO,
    )
    # httpx registra cada petición (con el token del bot en la URL): se silencia.
    for ruidoso in ("httpx", "httpx2", "httpcore", "httpcore2"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)

    try:
        cfg = configuracion.cargar()
    except configuracion.ErrorConfig as e:
        logging.error("Configuración inválida: %s", e)
        return 2

    almacen = Almacen(cfg.ruta_db)
    servicio = Servicio(almacen, cfg.zona)
    cliente = anthropic.AsyncAnthropic(api_key=cfg.anthropic_api_key)
    buzon = analista = None
    if cfg.gmail_activo:
        buzon = Buzon(cfg.gmail_usuario, cfg.gmail_clave_app)
        analista = AnalistaCorreo(cliente, cfg.claude_modelo, cfg.nombre_usuario)
        logging.info("Gmail conectado (solo lectura): %s", cfg.gmail_usuario)
    cerebro = Cerebro(cliente, cfg.claude_modelo, servicio, cfg.nombre_usuario, buzon=buzon)
    transcriptor = Transcriptor(cfg.whisper_modelo, cfg.carpeta_modelos)

    app = AsistenteBot(cfg, servicio, cerebro, transcriptor, buzon=buzon, analista=analista).construir()
    try:
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    finally:
        almacen.cerrar()
    return 0


if __name__ == "__main__":
    sys.exit(main())
