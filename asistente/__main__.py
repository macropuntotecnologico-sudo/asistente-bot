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
    cerebro = Cerebro(cliente, cfg.claude_modelo, servicio, cfg.nombre_usuario)
    transcriptor = Transcriptor(cfg.whisper_modelo, cfg.carpeta_modelos)

    app = AsistenteBot(cfg, servicio, cerebro, transcriptor).construir()
    try:
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    finally:
        almacen.cerrar()
    return 0


if __name__ == "__main__":
    sys.exit(main())
