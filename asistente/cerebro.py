"""Conversación con Claude: entiende lo que escribe el usuario y usa las herramientas del bot."""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any

import anthropic

from . import herramientas, tiempo
from .servicio import Servicio

log = logging.getLogger(__name__)

MAX_VUELTAS = 8          # máximo de rondas de herramientas por mensaje
MAX_HISTORIAL = 12       # mensajes (usuario + asistente) que se recuerdan
CADUCIDAD_HISTORIAL = timedelta(hours=6)

INSTRUCCIONES = """Eres la asistente personal de {nombre} y le hablas por Telegram.
Escribe en español de Colombia, tuteándolo, en mensajes cortos. Usa texto plano: nada de markdown \
(ni asteriscos, ni #, ni tablas). Algún emoji está bien, sin exagerar.

Ahora es {ahora} (zona horaria {zona}).
Próximos días: {calendario}.

Lo que puedes hacer, siempre con las herramientas:
- Recordatorios: únicos o que se repiten. Le llegan por Telegram con botones Hecho, +1 hora y Mañana.
- Lista de pendientes: anotar, mostrar, marcar como hecho, borrar.

Reglas:
- Una acción solo está hecha si la herramienta respondió sin error. Nunca digas que hiciste algo que no hiciste.
- Si la herramienta devuelve un error, corrígelo tú si es obvio; si no, explícaselo en una línea.
- Fechas: calcula a partir de la fecha de hoy y usa la lista de próximos días. "En X minutos/horas" va con en_minutos.
- Si no dice la hora: "en la mañana" = 8:00, "al mediodía" = 12:00, "en la tarde" = 15:00, \
"en la noche" = 19:00; si solo dice el día, usa 8:00. Dile la hora que elegiste.
- "Recuérdame" con fecha u hora = recordatorio. "Anota", "agrega a la lista", "tengo que" sin hora = pendiente.
- Para cambiar, borrar o completar algo sin id, primero lista y elige el que coincide; \
si hay varios que podrían ser, pregunta cuál.
- Al confirmar, di qué y cuándo en palabras (ej.: "Listo, te aviso el martes 20 a las 3:00 pm: llamar a Danilo"). \
No muestres los ids salvo que te los pida.
- Si te pide algo que no puedes hacer (mandar correos, llamar, buscar en internet, comprar), dilo en una línea \
y ofrece dejarle un recordatorio.
"""


class ErrorCerebro(RuntimeError):
    """Claude no respondió. El mensaje es apto para mostrarle al usuario."""


class Cerebro:
    def __init__(
        self,
        cliente: Any,
        modelo: str,
        servicio: Servicio,
        nombre_usuario: str = "tu jefe",
        max_tokens: int = 1024,
    ):
        self.cliente = cliente
        self.modelo = modelo
        self.servicio = servicio
        self.nombre_usuario = nombre_usuario
        self.max_tokens = max_tokens
        self._historial: deque[dict[str, str]] = deque(maxlen=MAX_HISTORIAL)
        self._ultimo_mensaje: datetime | None = None

    def olvidar(self) -> None:
        self._historial.clear()

    def instrucciones(self) -> str:
        tz = self.servicio.tz
        ahora = self.servicio.ahora().astimezone(tz)
        return INSTRUCCIONES.format(
            nombre=self.nombre_usuario,
            ahora=(
                f"{tiempo.NOMBRES_DIA[ahora.weekday()]} {ahora.strftime('%Y-%m-%d %H:%M')}"
                f" ({tiempo.formatear_hora(ahora)})"
            ),
            zona=getattr(tz, "key", str(tz)),
            calendario=tiempo.calendario_proximo(ahora, tz),
        )

    def _historial_vigente(self) -> list[dict[str, str]]:
        ahora = datetime.now(timezone.utc)
        if self._ultimo_mensaje and ahora - self._ultimo_mensaje > CADUCIDAD_HISTORIAL:
            self._historial.clear()
        mensajes = list(self._historial)
        # La API exige empezar con un mensaje del usuario.
        while mensajes and mensajes[0]["role"] != "user":
            mensajes.pop(0)
        return mensajes

    async def responder(self, texto: str) -> str:
        mensajes: list[dict[str, Any]] = [*self._historial_vigente(), {"role": "user", "content": texto}]
        sistema = self.instrucciones()
        cambios_hechos = 0  # acciones que ya modificaron datos en este mensaje

        for _ in range(MAX_VUELTAS):
            try:
                respuesta = await self.cliente.messages.create(
                    model=self.modelo,
                    max_tokens=self.max_tokens,
                    system=sistema,
                    tools=herramientas.HERRAMIENTAS,
                    messages=mensajes,
                )
            except anthropic.APIError as e:
                raise ErrorCerebro(self._explicar_error(e, cambios_hechos)) from e

            usos = [b for b in respuesta.content if b.type == "tool_use"]
            if respuesta.stop_reason != "tool_use" or not usos:
                final = "\n".join(b.text for b in respuesta.content if b.type == "text").strip()
                if not final:
                    final = "Listo." if respuesta.stop_reason == "end_turn" else (
                        "No alcancé a responder completo. ¿Me lo repites?"
                    )
                self._guardar(texto, final)
                return final

            mensajes.append({"role": "assistant", "content": respuesta.content})
            resultados = []
            for uso in usos:
                salida, es_error = herramientas.ejecutar(self.servicio, uso.name, uso.input)
                log.info("Herramienta %s -> %s", uso.name, "error" if es_error else "ok")
                if not es_error and not uso.name.startswith("listar_"):
                    cambios_hechos += 1
                resultados.append(
                    {"type": "tool_result", "tool_use_id": uso.id, "content": salida, "is_error": es_error}
                )
            mensajes.append({"role": "user", "content": resultados})

        log.warning("Se alcanzó el máximo de %s vueltas de herramientas", MAX_VUELTAS)
        aviso = "Me enredé con eso. ¿Me lo dices de otra forma, más corto?"
        self._guardar(texto, aviso)
        return aviso

    def _explicar_error(self, e: Exception, cambios_hechos: int) -> str:
        if isinstance(e, anthropic.AuthenticationError):
            log.error("Clave de Anthropic rechazada: %s", e)
            mensaje = "La clave de Anthropic no es válida. Revisa ANTHROPIC_API_KEY en el archivo .env."
        elif isinstance(e, anthropic.NotFoundError):
            log.error("Modelo no encontrado (%s): %s", self.modelo, e)
            mensaje = f"El modelo {self.modelo} no está disponible. Revisa CLAUDE_MODELO en el archivo .env."
        elif isinstance(e, anthropic.BadRequestError) and "credit balance" in str(e).lower():
            log.error("La cuenta de Anthropic no tiene saldo: %s", e)
            mensaje = (
                "Tu cuenta de Anthropic se quedó sin saldo. Cárgalo en platform.claude.com > Billing "
                "y vuelve a escribirme. Mientras tanto, /hoy, /recordatorios y /pendientes sí funcionan."
            )
        else:
            log.error("Error de la API de Anthropic: %s", e)
            mensaje = "No pude hablar con Claude en este momento. Intenta de nuevo en un rato."
        if cambios_hechos:
            mensaje += " Ojo: alcancé a guardar algún cambio; revísalo con /recordatorios o /pendientes."
        return mensaje

    def _guardar(self, texto_usuario: str, texto_asistente: str) -> None:
        self._historial.append({"role": "user", "content": texto_usuario})
        self._historial.append({"role": "assistant", "content": texto_asistente})
        self._ultimo_mensaje = datetime.now(timezone.utc)
