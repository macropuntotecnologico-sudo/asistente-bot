import json
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from asistente import herramientas
from asistente.cerebro import Cerebro, ErrorCerebro


def texto(t):
    return SimpleNamespace(type="text", text=t)


def uso(id_, nombre, entrada):
    return SimpleNamespace(type="tool_use", id=id_, name=nombre, input=entrada)


def respuesta(*bloques, stop="end_turn"):
    return SimpleNamespace(content=list(bloques), stop_reason=stop)


class ClienteFalso:
    """Imita client.messages.create devolviendo respuestas en orden y guardando las llamadas."""

    def __init__(self, *respuestas):
        self.respuestas = list(respuestas)
        self.llamadas = []
        self.messages = self

    async def create(self, **kwargs):
        self.llamadas.append(json.loads(json.dumps(kwargs, default=lambda o: o.__dict__)))
        siguiente = self.respuestas.pop(0)
        if isinstance(siguiente, Exception):
            raise siguiente
        return siguiente


def error_api(clase, status):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return clase("falló", response=httpx.Response(status, request=req), body=None)


# --- herramientas --------------------------------------------------------------------------

def test_esquemas_bien_formados():
    nombres = set()
    for h in herramientas.HERRAMIENTAS:
        assert h["name"] not in nombres
        nombres.add(h["name"])
        assert h["description"]
        assert h["input_schema"]["type"] == "object"
        for requerido in h["input_schema"].get("required", []):
            assert requerido in h["input_schema"]["properties"]


def test_ejecutar_crea_y_lista(servicio):
    salida, error = herramientas.ejecutar(
        servicio, "crear_recordatorio", {"texto": "llamar", "fecha_hora": "2026-10-20 15:00", "intruso": 1}
    )
    assert not error
    assert json.loads(salida)["proxima"] == "martes 20 de octubre 3:00 pm"
    salida, error = herramientas.ejecutar(servicio, "listar_recordatorios", {})
    assert json.loads(salida)["total"] == 1


def test_ejecutar_devuelve_errores_legibles(servicio):
    salida, error = herramientas.ejecutar(servicio, "crear_recordatorio", {"texto": "x", "fecha_hora": "2020-01-01 10:00"})
    assert error and "ya pasó" in json.loads(salida)["error"]
    salida, error = herramientas.ejecutar(servicio, "borrar_sistema", {})
    assert error and "desconocida" in json.loads(salida)["error"]
    salida, error = herramientas.ejecutar(servicio, "borrar_recordatorio", {})
    assert error and "Argumentos inválidos" in json.loads(salida)["error"]


# --- cerebro -------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ciclo_con_herramienta(servicio):
    cliente = ClienteFalso(
        respuesta(
            uso("t1", "crear_recordatorio", {"texto": "llamar a Danilo", "fecha_hora": "2026-10-20 15:00"}),
            stop="tool_use",
        ),
        respuesta(texto("Listo, te aviso el martes 20 a las 3:00 pm: llamar a Danilo")),
    )
    cerebro = Cerebro(cliente, "modelo-x", servicio, "Manuelle")
    final = await cerebro.responder("recuérdame el martes a las 3 llamar a Danilo")
    assert final.startswith("Listo, te aviso el martes 20")
    assert len(servicio.listar_recordatorios()) == 1

    segunda = cliente.llamadas[1]
    resultado = segunda["messages"][-1]["content"][0]
    assert resultado["type"] == "tool_result" and resultado["tool_use_id"] == "t1"
    assert resultado["is_error"] is False
    assert "Manuelle" in segunda["system"]
    assert "miércoles 2026-10-14 10:00" in segunda["system"]


@pytest.mark.asyncio
async def test_historial_se_envia_en_el_siguiente_mensaje(servicio):
    cliente = ClienteFalso(respuesta(texto("Hola")), respuesta(texto("Claro")))
    cerebro = Cerebro(cliente, "m", servicio)
    await cerebro.responder("hola")
    await cerebro.responder("y otra cosa")
    mensajes = cliente.llamadas[1]["messages"]
    assert [m["role"] for m in mensajes] == ["user", "assistant", "user"]
    assert mensajes[0]["content"] == "hola" and mensajes[1]["content"] == "Hola"


@pytest.mark.asyncio
async def test_error_de_clave(servicio):
    cliente = ClienteFalso(error_api(anthropic.AuthenticationError, 401))
    with pytest.raises(ErrorCerebro, match="ANTHROPIC_API_KEY"):
        await Cerebro(cliente, "m", servicio).responder("hola")


@pytest.mark.asyncio
async def test_error_despues_de_un_cambio_lo_advierte(servicio):
    cliente = ClienteFalso(
        respuesta(uso("t1", "anotar_pendiente", {"texto": "cotizar"}), stop="tool_use"),
        error_api(anthropic.InternalServerError, 500),
    )
    with pytest.raises(ErrorCerebro, match="alcancé a guardar"):
        await Cerebro(cliente, "m", servicio).responder("anota cotizar")
    assert len(servicio.listar_pendientes()) == 1


@pytest.mark.asyncio
async def test_limite_de_vueltas(servicio):
    bucle = [respuesta(uso(f"t{i}", "listar_pendientes", {}), stop="tool_use") for i in range(8)]
    cerebro = Cerebro(ClienteFalso(*bucle), "m", servicio)
    assert "de otra forma" in await cerebro.responder("???")
