from datetime import time

import pytest
from telegram.error import BadRequest, NetworkError

from asistente import config as configuracion
from asistente.bot import partir
from asistente.programador import despachar_vencidos

# --- programador ---------------------------------------------------------------------------


class Enviador:
    def __init__(self, fallas=None):
        self.enviados = []
        self.fallas = dict(fallas or {})

    async def __call__(self, id_, texto):
        if id_ in self.fallas:
            raise self.fallas[id_]
        self.enviados.append((id_, texto))


@pytest.mark.asyncio
async def test_despacha_solo_lo_vencido(servicio, reloj):
    a = servicio.crear_recordatorio("a", en_minutos=1)
    servicio.crear_recordatorio("b", en_minutos=30)
    reloj.avanzar(minutes=2)
    enviador = Enviador()
    assert await despachar_vencidos(servicio, enviador) == 1
    assert enviador.enviados == [(a["id"], "⏰ a")]
    assert await despachar_vencidos(servicio, enviador) == 0


@pytest.mark.asyncio
async def test_falla_de_red_reintenta_despues(servicio, reloj):
    a = servicio.crear_recordatorio("a", en_minutos=1)
    reloj.avanzar(minutes=2)
    assert await despachar_vencidos(servicio, Enviador({a["id"]: NetworkError("sin red")})) == 0
    enviador = Enviador()
    assert await despachar_vencidos(servicio, enviador) == 1
    assert enviador.enviados[0][0] == a["id"]


@pytest.mark.asyncio
async def test_mensaje_rechazado_no_se_reintenta_para_siempre(servicio, reloj):
    a = servicio.crear_recordatorio("a", en_minutos=1)
    b = servicio.crear_recordatorio("b", en_minutos=1)
    reloj.avanzar(minutes=2)
    enviador = Enviador({a["id"]: BadRequest("texto inválido")})
    assert await despachar_vencidos(servicio, enviador) == 2
    assert [e[0] for e in enviador.enviados] == [b["id"]]
    assert servicio.vencidos() == []


@pytest.mark.asyncio
async def test_cambio_mientras_sale_el_aviso_se_respeta(servicio, reloj):
    a = servicio.crear_recordatorio("a", en_minutos=1)
    reloj.avanzar(minutes=2)

    async def enviar_y_borrar(id_, texto):
        servicio.modificar_recordatorio(id_, fecha_hora="2026-10-30 09:00")

    await despachar_vencidos(servicio, enviar_y_borrar)
    r = servicio.almacen.obtener_recordatorio(a["id"])
    assert r["estado"] == "activo" and r["proxima"].startswith("2026-10-30")


# --- config --------------------------------------------------------------------------------

BASE = {"TELEGRAM_TOKEN": "123:abc", "ANTHROPIC_API_KEY": "sk-ant-x"}


def test_config_por_defecto():
    c = configuracion.cargar(dict(BASE))
    assert c.telegram_user_id is None
    assert c.claude_modelo == "claude-haiku-5-5"
    assert c.zona.key == "America/Bogota"
    assert c.resumen_hora == time(7, 0)
    assert c.whisper_modelo == "small"
    assert str(c.ruta_db) == "/data/asistente.db"


def test_config_personalizada():
    c = configuracion.cargar(
        {**BASE, "TELEGRAM_USER_ID": "987654", "RESUMEN_HORA": "off", "WHISPER_MODELO": "off",
         "CLAUDE_MODELO": "claude-sonnet-5-5", "NOMBRE_USUARIO": "Manuelle"}
    )
    assert c.telegram_user_id == 987654
    assert c.resumen_hora is None and c.whisper_modelo is None
    assert c.claude_modelo == "claude-sonnet-5-5" and c.nombre_usuario == "Manuelle"


@pytest.mark.parametrize(
    "entorno, mensaje",
    [
        ({}, "TELEGRAM_TOKEN"),
        ({**BASE, "TELEGRAM_USER_ID": "abc"}, "TELEGRAM_USER_ID"),
        ({**BASE, "RESUMEN_HORA": "7am"}, "RESUMEN_HORA"),
        ({**BASE, "ZONA_HORARIA": "Marte/Base"}, "ZONA_HORARIA"),
    ],
)
def test_config_invalida(entorno, mensaje):
    with pytest.raises(configuracion.ErrorConfig, match=mensaje):
        configuracion.cargar(entorno)


# --- partir --------------------------------------------------------------------------------

def test_partir_textos_largos():
    texto = "\n".join(f"línea {i}" for i in range(2000))
    partes = partir(texto, 1000)
    assert all(len(p) <= 1000 for p in partes)
    assert "\n".join(partes) == texto
    assert partir("corto") == ["corto"]
    assert partir("x" * 2500, 1000) == ["x" * 1000, "x" * 1000, "x" * 500]
