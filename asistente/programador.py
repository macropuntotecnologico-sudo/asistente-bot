"""Envío de los avisos que ya llegaron a su hora."""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from telegram.error import BadRequest

from .servicio import Servicio

log = logging.getLogger(__name__)

EnviarAviso = Callable[[int, str], Awaitable[None]]


async def despachar_vencidos(servicio: Servicio, enviar: EnviarAviso) -> int:
    """Envía cada aviso vencido y lo registra. Devuelve cuántos salieron.

    - Si Telegram rechaza un mensaje en particular (BadRequest), se registra igual para no
      reintentarlo para siempre.
    - Si falla la red o Telegram no responde, se corta la ronda: lo pendiente sale en la próxima.
    """
    enviados = 0
    for r in servicio.vencidos():
        try:
            await enviar(r["id"], servicio.texto_aviso(r))
        except BadRequest:
            log.exception("Telegram rechazó el aviso %s; se marca como enviado", r["id"])
        except Exception as e:
            log.warning("No se pudo enviar el aviso %s (%s); se reintenta luego", r["id"], e)
            break
        servicio.registrar_aviso(r)
        enviados += 1
    return enviados
