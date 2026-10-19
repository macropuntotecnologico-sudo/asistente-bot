"""Bot de Telegram: recibe mensajes, notas de voz y botones; envía avisos y el resumen diario."""

from __future__ import annotations

import asyncio
import logging
import tempfile
from datetime import time
from pathlib import Path

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .cerebro import Cerebro, ErrorCerebro
from .config import Config
from .programador import despachar_vencidos
from .servicio import Servicio
from .transcripcion import ErrorTranscripcion, Transcriptor

log = logging.getLogger(__name__)

LIMITE_TELEGRAM = 4000
MAX_SEGUNDOS_AUDIO = 300
INTERVALO_REVISION = 30  # segundos entre revisiones de avisos

AYUDA = """Hola 👋 Soy tu asistente. Háblame normal, por texto o nota de voz:

• "Recuérdame mañana a las 9 llamar al proveedor"
• "En 20 minutos sacar el cargador"
• "Todos los lunes a las 8 revisar inventario"
• "De lunes a viernes a las 7 pm cerrar caja"
• "Anota cotizar paneles para el viernes"
• "¿Qué tengo pendiente?" / "Ya hice lo de los paneles"
• "Cambia lo del proveedor para las 10" / "Cancela lo del inventario"

Atajos:
/hoy — lo que te aviso hoy y tus pendientes
/recordatorios — todo lo programado
/pendientes — tu lista de pendientes
/ayuda — este mensaje"""

COMANDOS = [
    BotCommand("hoy", "Lo de hoy y tus pendientes"),
    BotCommand("recordatorios", "Todo lo programado"),
    BotCommand("pendientes", "Tu lista de pendientes"),
    BotCommand("ayuda", "Ejemplos de lo que puedo hacer"),
]


def partir(texto: str, limite: int = LIMITE_TELEGRAM) -> list[str]:
    """Parte un texto largo en trozos que Telegram acepte, cortando en saltos de línea si se puede."""
    partes = []
    while len(texto) > limite:
        corte = texto.rfind("\n", 0, limite)
        if corte <= 0:
            corte = limite
        partes.append(texto[:corte])
        texto = texto[corte:].lstrip("\n")
    partes.append(texto)
    return partes


def botones_aviso(id_: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✅ Hecho", callback_data=f"h:{id_}"),
            InlineKeyboardButton("⏱ +1 hora", callback_data=f"p:{id_}"),
            InlineKeyboardButton("📅 Mañana", callback_data=f"m:{id_}"),
        ]]
    )


class AsistenteBot:
    def __init__(
        self,
        config: Config,
        servicio: Servicio,
        cerebro: Cerebro,
        transcriptor: Transcriptor,
    ):
        self.config = config
        self.servicio = servicio
        self.cerebro = cerebro
        self.transcriptor = transcriptor
        self._tareas: set[asyncio.Task] = set()

    # --- armado ---------------------------------------------------------------------------

    def construir(self, url_api: str | None = None, url_archivos: str | None = None) -> Application:
        """Arma la aplicación. url_api/url_archivos sirven para un servidor Bot API propio o de pruebas."""
        constructor = Application.builder().token(self.config.telegram_token).post_init(self._al_iniciar)
        if url_api:
            constructor = constructor.base_url(url_api).base_file_url(url_archivos or url_api)
        app = constructor.build()
        app.add_error_handler(self._error)

        if self.config.telegram_user_id is None:
            # Modo configuración: solo le dice a quien escriba cuál es su ID.
            app.add_handler(MessageHandler(filters.ChatType.PRIVATE, self._decir_id))
            log.warning("TELEGRAM_USER_ID no está configurado: el bot solo responde con el ID de quien escriba.")
            return app

        dueno = filters.User(user_id=self.config.telegram_user_id) & filters.ChatType.PRIVATE
        app.add_handler(CommandHandler(["start", "ayuda", "help"], self._ayuda, filters=dueno))
        app.add_handler(CommandHandler("hoy", self._hoy, filters=dueno))
        app.add_handler(CommandHandler("recordatorios", self._recordatorios, filters=dueno))
        app.add_handler(CommandHandler("pendientes", self._pendientes, filters=dueno))
        app.add_handler(MessageHandler(dueno & filters.TEXT & ~filters.COMMAND, self._texto))
        app.add_handler(MessageHandler(dueno & (filters.VOICE | filters.AUDIO), self._voz))
        app.add_handler(CallbackQueryHandler(self._boton))
        app.add_handler(MessageHandler(dueno, self._no_soportado))
        app.add_handler(MessageHandler(~dueno, self._ignorar))

        app.job_queue.run_repeating(self._revisar_avisos, interval=INTERVALO_REVISION, first=3)
        if self.config.resumen_hora:
            hora = self.config.resumen_hora
            app.job_queue.run_daily(
                self._resumen_diario, time=time(hora.hour, hora.minute, tzinfo=self.config.zona)
            )
        return app

    async def _al_iniciar(self, app: Application) -> None:
        if self.config.telegram_user_id is None:
            return
        try:
            await app.bot.set_my_commands(COMANDOS)
        except Exception:
            log.exception("No se pudo registrar el menú de comandos")
        if self.transcriptor.activo:
            tarea = asyncio.create_task(self.transcriptor.precargar())
            self._tareas.add(tarea)
            tarea.add_done_callback(self._tareas.discard)
        log.info("Asistente listo. Modelo: %s. Zona: %s.", self.config.claude_modelo, self.config.zona.key)

    # --- envío ----------------------------------------------------------------------------

    async def _enviar(self, context: ContextTypes.DEFAULT_TYPE, texto: str, **kwargs) -> None:
        partes = partir(texto)
        for i, parte in enumerate(partes):
            extra = kwargs if i == len(partes) - 1 else {}
            await context.bot.send_message(self.config.telegram_user_id, parte, **extra)

    async def _responder(self, update: Update, texto: str) -> None:
        for parte in partir(texto):
            await update.effective_message.reply_text(parte)

    # --- mensajes -------------------------------------------------------------------------

    async def _decir_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        usuario = update.effective_user
        await update.effective_message.reply_text(
            f"Tu ID de Telegram es {usuario.id}.\n\n"
            "Ponlo en TELEGRAM_USER_ID en el archivo .env y reinicia el bot. "
            "Desde ese momento solo te responderé a ti."
        )

    async def _ignorar(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        usuario = update.effective_user
        log.info("Mensaje ignorado de un usuario no autorizado (id %s)", usuario.id if usuario else "?")

    async def _no_soportado(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text("Por ahora entiendo texto y notas de voz.")

    async def _ayuda(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._responder(update, AYUDA)

    async def _hoy(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._responder(update, self.servicio.texto_hoy())

    async def _recordatorios(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._responder(update, self.servicio.texto_recordatorios())

    async def _pendientes(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._responder(update, self.servicio.texto_pendientes())

    async def _pensar(self, update: Update, texto: str) -> str:
        await update.effective_chat.send_action(ChatAction.TYPING)
        try:
            return await self.cerebro.responder(texto)
        except ErrorCerebro as e:
            return f"⚠️ {e}"

    async def _texto(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        texto = (update.effective_message.text or "").strip()
        if not texto:
            return
        await self._responder(update, await self._pensar(update, texto))

    async def _voz(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        mensaje = update.effective_message
        audio = mensaje.voice or mensaje.audio
        if not self.transcriptor.activo:
            await mensaje.reply_text("Las notas de voz están desactivadas. Escríbeme por texto.")
            return
        duracion = audio.duration
        if hasattr(duracion, "total_seconds"):  # versiones nuevas de la librería usan timedelta
            duracion = duracion.total_seconds()
        if duracion and duracion > MAX_SEGUNDOS_AUDIO:
            await mensaje.reply_text("Ese audio es muy largo. Mándame uno de menos de 5 minutos.")
            return

        await update.effective_chat.send_action(ChatAction.TYPING)
        try:
            archivo = await audio.get_file()
            with tempfile.TemporaryDirectory() as carpeta:
                ruta = Path(carpeta) / "audio"
                await archivo.download_to_drive(ruta)
                texto = await self.transcriptor.transcribir(ruta)
        except ErrorTranscripcion as e:
            await mensaje.reply_text(f"⚠️ {e}")
            return
        except Exception:
            log.exception("No se pudo descargar o procesar el audio")
            await mensaje.reply_text("⚠️ No pude descargar el audio. Intenta de nuevo.")
            return

        if not texto:
            await mensaje.reply_text("No alcancé a entender el audio. ¿Me lo repites?")
            return
        respuesta = await self._pensar(update, texto)
        await self._responder(update, f"🎤 «{texto}»\n\n{respuesta}")

    # --- botones --------------------------------------------------------------------------

    async def _boton(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        consulta = update.callback_query
        if consulta.from_user.id != self.config.telegram_user_id:
            await consulta.answer()
            return
        accion, _, id_texto = (consulta.data or "").partition(":")
        if accion not in ("h", "p", "m") or not id_texto.isdigit():
            await consulta.answer("Botón no válido.")
            return

        id_ = int(id_texto)
        if accion == "h":
            resultado = self.servicio.marcar_hecho(id_)
        elif accion == "p":
            resultado = self.servicio.posponer(id_, 60)
        else:
            resultado = self.servicio.posponer_manana(id_)

        await consulta.answer(resultado[:190])
        # Mensajes de más de 48 h llegan como "inaccesibles" y no traen texto.
        original = getattr(consulta.message, "text", None) or ""
        try:
            await consulta.edit_message_text(f"{original}\n\n{resultado}")
        except Exception:
            log.debug("No se pudo editar el mensaje del aviso", exc_info=True)

    # --- tareas programadas ---------------------------------------------------------------

    async def _revisar_avisos(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        async def enviar(id_: int, texto: str) -> None:
            await context.bot.send_message(
                self.config.telegram_user_id, texto[:LIMITE_TELEGRAM], reply_markup=botones_aviso(id_)
            )

        enviados = await despachar_vencidos(self.servicio, enviar)
        if enviados:
            log.info("Avisos enviados: %s", enviados)

    async def _resumen_diario(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._enviar(context, self.servicio.texto_resumen_diario())

    async def _error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        log.error("Error procesando una actualización", exc_info=context.error)
