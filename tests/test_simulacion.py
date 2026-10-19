"""Simulación de punta a punta: el bot real contra un Telegram falso y un Claude falso.

Prueba el cableado completo: filtros de usuario, comandos, conversación con herramientas
(con el SDK real de Anthropic serializando los bloques), avisos con botones, notas de voz
y el modo de configuración.
"""

from __future__ import annotations

import asyncio
import json
import time as reloj_sistema
from datetime import time

import anthropic
import pytest
import pytest_asyncio
from aiohttp import web

from asistente import bot as modulo_bot
from asistente.almacen import Almacen
from asistente.bot import AsistenteBot
from asistente.cerebro import Cerebro
from asistente.config import Config
from asistente.servicio import Servicio
from tests.conftest import BOGOTA, RelojFalso

TOKEN = "123456:PRUEBA"
DUENO = 777
EXTRANO = 999
BOT_USER = {"id": 1, "is_bot": True, "first_name": "Asistente", "username": "asistente_bot"}


class TelegramFalso:
    def __init__(self):
        self.actualizaciones: list[dict] = []
        self.salientes: list[tuple[str, dict]] = []
        self._id_update = 0
        self._id_mensaje = 100
        self._hay_nuevas = asyncio.Event()

    def _usuario(self, uid):
        return {"id": uid, "is_bot": False, "first_name": "Manuelle" if uid == DUENO else "Otro"}

    def _encolar(self, cuerpo: dict) -> None:
        self._id_update += 1
        self.actualizaciones.append({"update_id": self._id_update, **cuerpo})
        self._hay_nuevas.set()

    def _mensaje_base(self, uid: int) -> dict:
        self._id_mensaje += 1
        return {
            "message_id": self._id_mensaje,
            "date": int(reloj_sistema.time()),
            "chat": {"id": uid, "type": "private", "first_name": "x"},
            "from": self._usuario(uid),
        }

    def escribir(self, texto: str, uid: int = DUENO) -> None:
        mensaje = {**self._mensaje_base(uid), "text": texto}
        if texto.startswith("/"):
            mensaje["entities"] = [{"type": "bot_command", "offset": 0, "length": len(texto.split()[0])}]
        self._encolar({"message": mensaje})

    def nota_de_voz(self, uid: int = DUENO) -> None:
        voz = {"file_id": "voz1", "file_unique_id": "u-voz1", "duration": 3, "mime_type": "audio/ogg"}
        self._encolar({"message": {**self._mensaje_base(uid), "voice": voz}})

    def foto(self, uid: int = DUENO) -> None:
        foto = [{"file_id": "f1", "file_unique_id": "u-f1", "width": 1, "height": 1}]
        self._encolar({"message": {**self._mensaje_base(uid), "photo": foto}})

    def presionar(self, mensaje: dict, datos: str, uid: int = DUENO) -> None:
        self._encolar({
            "callback_query": {
                "id": f"cb{self._id_update}",
                "from": self._usuario(uid),
                "chat_instance": "ci",
                "data": datos,
                "message": mensaje,
            }
        })

    async def _leer(self, request: web.Request) -> dict:
        if request.content_type == "application/json":
            return await request.json()
        datos = dict(await request.post())
        for clave, valor in list(datos.items()):
            if isinstance(valor, str) and valor[:1] in "[{":
                try:
                    datos[clave] = json.loads(valor)
                except ValueError:
                    pass
        return datos

    async def manejar(self, request: web.Request) -> web.Response:
        metodo = request.match_info["metodo"]
        datos = await self._leer(request)
        if metodo == "getUpdates":
            desde = int(datos.get("offset") or 0)
            espera = min(float(datos.get("timeout") or 0), 0.5)
            pendientes = [u for u in self.actualizaciones if u["update_id"] >= desde]
            if not pendientes and espera:
                self._hay_nuevas.clear()
                try:
                    await asyncio.wait_for(self._hay_nuevas.wait(), espera)
                except asyncio.TimeoutError:
                    pass
                pendientes = [u for u in self.actualizaciones if u["update_id"] >= desde]
            return web.json_response({"ok": True, "result": pendientes})

        self.salientes.append((metodo, datos))
        if metodo == "getMe":
            resultado = BOT_USER
        elif metodo in ("sendMessage", "editMessageText"):
            chat = int(datos.get("chat_id") or DUENO)
            self._id_mensaje += 1
            resultado = {
                "message_id": self._id_mensaje,
                "date": int(reloj_sistema.time()),
                "chat": {"id": chat, "type": "private"},
                "from": BOT_USER,
                "text": datos.get("text", ""),
            }
            if datos.get("reply_markup"):
                resultado["reply_markup"] = datos["reply_markup"]
        elif metodo == "getFile":
            resultado = {"file_id": "voz1", "file_unique_id": "u-voz1", "file_size": 4, "file_path": "voice/voz1.oga"}
        else:
            resultado = True
        return web.json_response({"ok": True, "result": resultado})

    async def archivo(self, request: web.Request) -> web.Response:
        return web.Response(body=b"OggS")

    def enviados(self, metodo: str = "sendMessage") -> list[dict]:
        return [d for m, d in self.salientes if m == metodo]

    async def esperar(self, condicion, segundos: float = 8.0):
        limite = asyncio.get_running_loop().time() + segundos
        while asyncio.get_running_loop().time() < limite:
            resultado = condicion()
            if resultado:
                return resultado
            await asyncio.sleep(0.05)
        raise AssertionError("La condición no se cumplió a tiempo")


class ClaudeFalso:
    """Responde como la API de mensajes. Guarda cada petición tal como la serializó el SDK."""

    GUIONES = {
        "recuérdame en 1 minuto probar el bot": ("crear_recordatorio", {"texto": "probar el bot", "en_minutos": 1}),
        "anota cotizar paneles": ("anotar_pendiente", {"texto": "cotizar paneles"}),
    }

    def __init__(self):
        self.peticiones: list[dict] = []

    async def manejar(self, request: web.Request) -> web.Response:
        cuerpo = await request.json()
        self.peticiones.append(cuerpo)
        ultimo = cuerpo["messages"][-1]
        if isinstance(ultimo["content"], str):
            nombre, entrada = self.GUIONES.get(ultimo["content"], (None, None))
            if nombre:
                bloques = [
                    {"type": "text", "text": "Va."},
                    {"type": "tool_use", "id": f"toolu_{len(self.peticiones)}", "name": nombre, "input": entrada},
                ]
                return self._respuesta(bloques, "tool_use")
            return self._respuesta([{"type": "text", "text": f"Recibí: {ultimo['content']}"}], "end_turn")
        resultado = json.loads(ultimo["content"][0]["content"])
        return self._respuesta([{"type": "text", "text": f"Listo: {resultado.get('texto')}"}], "end_turn")

    def _respuesta(self, bloques, stop):
        return web.json_response({
            "id": f"msg_{len(self.peticiones)}", "type": "message", "role": "assistant",
            "model": "claude-prueba", "content": bloques, "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })


class TranscriptorFalso:
    activo = True

    async def transcribir(self, ruta):
        assert ruta.read_bytes() == b"OggS"
        return "anota cotizar paneles"

    async def precargar(self):
        return None


def _config(usuario: int | None) -> Config:
    from pathlib import Path

    return Config(
        telegram_token=TOKEN, telegram_user_id=usuario, anthropic_api_key="sk-prueba",
        claude_modelo="claude-prueba", zona=BOGOTA, resumen_hora=time(7, 0),
        whisper_modelo="small", carpeta_datos=Path("/tmp/no-usar"), nombre_usuario="Manuelle",
    )


@pytest_asyncio.fixture
async def servidores():
    telegram, claude = TelegramFalso(), ClaudeFalso()
    app = web.Application()
    app.router.add_post(f"/bot{TOKEN}/{{metodo}}", telegram.manejar)
    app.router.add_get(f"/file/bot{TOKEN}/{{ruta:.*}}", telegram.archivo)
    app.router.add_post("/v1/messages", claude.manejar)
    runner = web.AppRunner(app)
    await runner.setup()
    sitio = web.TCPSite(runner, "127.0.0.1", 0)
    await sitio.start()
    puerto = sitio._server.sockets[0].getsockname()[1]
    yield telegram, claude, f"http://127.0.0.1:{puerto}"
    await runner.cleanup()


async def _arrancar(asistente: AsistenteBot, url: str):
    app = asistente.construir(url_api=f"{url}/bot", url_archivos=f"{url}/file/bot")
    await app.initialize()
    await app.post_init(app)
    await app.start()
    await app.updater.start_polling(poll_interval=0.05, timeout=1)
    return app


async def _detener(app):
    await app.updater.stop()
    await app.stop()
    await app.shutdown()


@pytest.mark.asyncio
async def test_recorrido_completo(servidores, monkeypatch):
    telegram, claude, url = servidores
    monkeypatch.setattr(modulo_bot, "INTERVALO_REVISION", 0.2)
    reloj = RelojFalso()
    servicio = Servicio(Almacen(":memory:"), BOGOTA, reloj)
    cliente = anthropic.AsyncAnthropic(api_key="sk-prueba", base_url=url, max_retries=0)
    cerebro = Cerebro(cliente, "claude-prueba", servicio, "Manuelle")
    asistente = AsistenteBot(_config(DUENO), servicio, cerebro, TranscriptorFalso())
    app = await _arrancar(asistente, url)
    try:
        assert len(app.job_queue.jobs()) == 2  # revisión de avisos + resumen diario
        assert telegram.enviados("setMyCommands")

        # Un extraño no recibe respuesta.
        telegram.escribir("hola, soy otro", uid=EXTRANO)
        telegram.escribir("/start")
        bienvenida = await telegram.esperar(lambda: telegram.enviados())
        assert len(bienvenida) == 1 and "Soy tu asistente" in bienvenida[0]["text"]
        assert int(bienvenida[0]["chat_id"]) == DUENO

        # Conversación con herramienta: crea un recordatorio.
        telegram.escribir("recuérdame en 1 minuto probar el bot")
        await telegram.esperar(lambda: any("Listo: probar el bot" in m["text"] for m in telegram.enviados()))
        assert len(servicio.listar_recordatorios()) == 1
        # El SDK real reenvió el tool_use y nuestro tool_result con el mismo id.
        segunda = claude.peticiones[1]
        assert segunda["messages"][1]["role"] == "assistant"
        assert segunda["messages"][1]["content"][1]["type"] == "tool_use"
        resultado = segunda["messages"][2]["content"][0]
        assert resultado["type"] == "tool_result" and resultado["tool_use_id"] == "toolu_1"
        assert segunda["tools"][0]["name"] == "crear_recordatorio"

        # Llega la hora: sale el aviso con sus botones.
        reloj.avanzar(minutes=2)
        aviso = await telegram.esperar(
            lambda: next((m for m in telegram.enviados() if m["text"].startswith("⏰")), None)
        )
        assert aviso["text"] == "⏰ probar el bot"
        botones = [b["callback_data"] for b in aviso["reply_markup"]["inline_keyboard"][0]]
        assert botones[1].startswith("p:")

        # Botón +1 hora.
        mensaje_aviso = {
            "message_id": 555, "date": int(reloj_sistema.time()),
            "chat": {"id": DUENO, "type": "private"}, "from": BOT_USER, "text": aviso["text"],
        }
        telegram.presionar(mensaje_aviso, botones[1])
        editado = await telegram.esperar(lambda: telegram.enviados("editMessageText"))
        assert "⏱ Te lo recuerdo hoy 11:02 am" in editado[0]["text"]
        assert telegram.enviados("answerCallbackQuery")
        assert servicio.listar_recordatorios()[0]["proxima"] == "hoy 11:02 am"

        # Botón de un extraño: no cambia nada.
        telegram.presionar(mensaje_aviso, botones[0], uid=EXTRANO)
        await telegram.esperar(lambda: len(telegram.enviados("answerCallbackQuery")) == 2)
        assert servicio.almacen.obtener_recordatorio(1)["estado"] == "activo"

        # Nota de voz -> pendiente.
        telegram.nota_de_voz()
        respuesta_voz = await telegram.esperar(
            lambda: next((m for m in telegram.enviados() if m["text"].startswith("🎤")), None)
        )
        assert respuesta_voz["text"] == "🎤 «anota cotizar paneles»\n\nListo: cotizar paneles"

        # Comando directo, sin pasar por Claude.
        peticiones_antes = len(claude.peticiones)
        telegram.escribir("/pendientes")
        lista = await telegram.esperar(
            lambda: next((m for m in telegram.enviados() if m["text"].startswith("📝")), None)
        )
        assert "1. cotizar paneles" in lista["text"]
        assert len(claude.peticiones) == peticiones_antes

        # Algo que no entiende.
        telegram.foto()
        await telegram.esperar(
            lambda: any(m["text"] == "Por ahora entiendo texto y notas de voz." for m in telegram.enviados())
        )

        # Nunca se le escribió al extraño.
        assert all(int(m["chat_id"]) == DUENO for m in telegram.enviados())
    finally:
        await _detener(app)


@pytest.mark.asyncio
async def test_modo_configuracion_dice_el_id(servidores):
    telegram, _claude, url = servidores
    servicio = Servicio(Almacen(":memory:"), BOGOTA, RelojFalso())
    asistente = AsistenteBot(_config(None), servicio, Cerebro(None, "m", servicio), TranscriptorFalso())
    app = await _arrancar(asistente, url)
    try:
        assert app.job_queue.jobs() == ()
        telegram.escribir("/start", uid=4242)
        respuesta = await telegram.esperar(lambda: telegram.enviados())
        assert "Tu ID de Telegram es 4242" in respuesta[0]["text"]
    finally:
        await _detener(app)
