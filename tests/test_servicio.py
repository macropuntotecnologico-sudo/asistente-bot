from datetime import datetime

import pytest

from asistente import tiempo
from asistente.servicio import ErrorAsistente
from tests.conftest import BOGOTA


def proxima_local(servicio, id_):
    r = servicio.almacen.obtener_recordatorio(id_)
    return tiempo.desde_utc_iso(r["proxima"]).astimezone(BOGOTA)


# --- crear ---------------------------------------------------------------------------------

def test_crea_recordatorio_con_fecha(servicio):
    r = servicio.crear_recordatorio("llamar a Danilo", fecha_hora="2026-10-20 15:00")
    assert r["texto"] == "llamar a Danilo"
    assert r["proxima"] == "martes 20 de octubre 3:00 pm"
    assert proxima_local(servicio, r["id"]) == datetime(2026, 10, 20, 15, 0, tzinfo=BOGOTA)


def test_crea_recordatorio_en_minutos(servicio):
    r = servicio.crear_recordatorio("sacar el cargador", en_minutos=20)
    assert r["proxima"] == "hoy 10:20 am"


def test_rechaza_hora_pasada(servicio):
    with pytest.raises(ErrorAsistente, match="ya pasó"):
        servicio.crear_recordatorio("algo", fecha_hora="2026-10-14 08:00")


def test_acepta_hora_recien_pasada_y_avisa_ya(servicio, reloj):
    reloj.avanzar(seconds=40)  # 10:00:40
    r = servicio.crear_recordatorio("ya", fecha_hora="2026-10-14 10:00")
    assert servicio.vencidos()[0]["id"] == r["id"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"texto": "  ", "fecha_hora": "2026-10-20 15:00"},
        {"texto": "x"},
        {"texto": "x", "fecha_hora": "2026-10-20 15:00", "en_minutos": 5},
        {"texto": "x", "en_minutos": -3},
        {"texto": "x", "fecha_hora": "2026-10-20 15:00", "repetir": {"frecuencia": "nunca"}},
    ],
)
def test_crear_valida_entradas(servicio, kwargs):
    with pytest.raises(ErrorAsistente):
        servicio.crear_recordatorio(**kwargs)


def test_recurrente_con_inicio_pasado_salta_al_futuro(servicio):
    r = servicio.crear_recordatorio(
        "revisar inventario",
        fecha_hora="2026-10-12 08:00",  # lunes pasado
        repetir={"frecuencia": "semanal", "dias_semana": ["lu"]},
    )
    assert proxima_local(servicio, r["id"]) == datetime(2026, 10, 19, 8, 0, tzinfo=BOGOTA)
    assert r["repite"] == "cada lunes"


def test_recurrente_terminada_se_rechaza(servicio):
    with pytest.raises(ErrorAsistente, match="terminó"):
        servicio.crear_recordatorio(
            "x", fecha_hora="2026-10-01 08:00", repetir={"frecuencia": "diaria", "hasta": "2026-10-05"}
        )


# --- modificar / borrar --------------------------------------------------------------------

def test_modificar_hora_y_texto(servicio):
    r = servicio.crear_recordatorio("llamar", fecha_hora="2026-10-20 15:00")
    m = servicio.modificar_recordatorio(r["id"], texto="llamar a Esteban", fecha_hora="2026-10-20 16:00")
    assert m["texto"] == "llamar a Esteban"
    assert m["proxima"] == "martes 20 de octubre 4:00 pm"


def test_modificar_agrega_y_quita_repeticion(servicio):
    r = servicio.crear_recordatorio("agua", fecha_hora="2026-10-14 12:00")
    m = servicio.modificar_recordatorio(r["id"], repetir={"frecuencia": "diaria"})
    assert m["repite"] == "todos los días"
    m = servicio.modificar_recordatorio(r["id"], quitar_repeticion=True)
    assert "repite" not in m
    assert m["proxima"] == "hoy 12:00 pm"


def test_modificar_sin_cambios_o_inexistente(servicio):
    r = servicio.crear_recordatorio("x", fecha_hora="2026-10-20 15:00")
    with pytest.raises(ErrorAsistente):
        servicio.modificar_recordatorio(r["id"])
    with pytest.raises(ErrorAsistente):
        servicio.modificar_recordatorio(999, texto="y")


def test_borrar_recordatorio(servicio):
    r = servicio.crear_recordatorio("x", fecha_hora="2026-10-20 15:00")
    servicio.borrar_recordatorio(r["id"])
    assert servicio.listar_recordatorios() == []
    with pytest.raises(ErrorAsistente):
        servicio.borrar_recordatorio(r["id"])


# --- avisos --------------------------------------------------------------------------------

def test_aviso_unico_queda_avisado(servicio, reloj):
    r = servicio.crear_recordatorio("pagar internet", en_minutos=5)
    assert servicio.vencidos() == []
    reloj.avanzar(minutes=5)
    vencidos = servicio.vencidos()
    assert [v["id"] for v in vencidos] == [r["id"]]
    assert servicio.texto_aviso(vencidos[0]) == "⏰ pagar internet"
    servicio.registrar_aviso(vencidos[0])
    assert servicio.vencidos() == []
    assert servicio.almacen.obtener_recordatorio(r["id"])["estado"] == "avisado"


def test_aviso_recurrente_avanza_y_marca_atrasado(servicio, reloj):
    r = servicio.crear_recordatorio(
        "revisar inventario", fecha_hora="2026-10-19 08:00", repetir={"frecuencia": "semanal", "dias_semana": ["lu"]}
    )
    reloj.poner(2026, 10, 20, 9, 0)  # el servidor estuvo caído todo el lunes
    v = servicio.vencidos()[0]
    texto = servicio.texto_aviso(v)
    assert "atrasado: era para ayer 8:00 am" in texto
    assert "🔁 cada lunes" in texto
    servicio.registrar_aviso(v)
    assert proxima_local(servicio, r["id"]) == datetime(2026, 10, 26, 8, 0, tzinfo=BOGOTA)


def test_serie_que_termina_queda_completada(servicio, reloj):
    r = servicio.crear_recordatorio(
        "x", fecha_hora="2026-10-14 11:00", repetir={"frecuencia": "diaria", "hasta": "2026-10-14"}
    )
    reloj.poner(2026, 10, 14, 11, 0)
    servicio.registrar_aviso(servicio.vencidos()[0])
    assert servicio.almacen.obtener_recordatorio(r["id"])["estado"] == "completado"


# --- botones -------------------------------------------------------------------------------

def test_boton_hecho(servicio, reloj):
    r = servicio.crear_recordatorio("x", en_minutos=1)
    reloj.avanzar(minutes=1)
    servicio.registrar_aviso(servicio.vencidos()[0])
    assert servicio.marcar_hecho(r["id"]) == "✅ Hecho"
    assert servicio.almacen.obtener_recordatorio(r["id"])["estado"] == "completado"
    assert servicio.marcar_hecho(r["id"]) == "✅ Hecho"  # idempotente


def test_boton_mas_una_hora_reprograma_unico(servicio, reloj):
    r = servicio.crear_recordatorio("x", en_minutos=1)
    reloj.avanzar(minutes=1)
    servicio.registrar_aviso(servicio.vencidos()[0])
    assert servicio.posponer(r["id"]) == "⏱ Te lo recuerdo hoy 11:01 am"
    reloj.avanzar(hours=1)
    assert [v["id"] for v in servicio.vencidos()] == [r["id"]]


def test_boton_manana_usa_la_hora_original(servicio, reloj):
    r = servicio.crear_recordatorio("x", fecha_hora="2026-10-14 15:00")
    reloj.poner(2026, 10, 14, 15, 7)
    servicio.registrar_aviso(servicio.vencidos()[0])
    assert servicio.posponer_manana(r["id"]) == "📅 Te lo recuerdo mañana 3:00 pm"


def test_posponer_recurrente_crea_copia_y_serie_sigue(servicio, reloj):
    r = servicio.crear_recordatorio("agua", fecha_hora="2026-10-14 11:00", repetir={"frecuencia": "diaria"})
    reloj.poner(2026, 10, 14, 11, 0)
    servicio.registrar_aviso(servicio.vencidos()[0])
    servicio.posponer(r["id"], 60)
    activos = servicio.almacen.recordatorios_activos()
    assert len(activos) == 2
    assert proxima_local(servicio, r["id"]) == datetime(2026, 10, 15, 11, 0, tzinfo=BOGOTA)


def test_boton_de_recordatorio_borrado(servicio):
    r = servicio.crear_recordatorio("x", fecha_hora="2026-10-20 15:00")
    servicio.borrar_recordatorio(r["id"])
    assert servicio.posponer(r["id"]) == "Ese recordatorio ya no existe."
    assert servicio.marcar_hecho(12345) == "Ese recordatorio ya no existe."


# --- pendientes ----------------------------------------------------------------------------

def test_pendientes_ciclo_completo(servicio):
    a = servicio.anotar_pendiente("comprar celdas 18650")
    b = servicio.anotar_pendiente("renovar dominio", para_fecha="2026-10-13")
    assert [p["id"] for p in servicio.listar_pendientes()] == [b["id"], a["id"]]
    texto = servicio.texto_pendientes()
    assert "renovar dominio (vencido: era para ayer)" in texto
    servicio.completar_pendiente(a["id"])
    assert [p["id"] for p in servicio.listar_pendientes()] == [b["id"]]
    assert len(servicio.listar_pendientes(incluir_hechos=True)) == 2
    servicio.borrar_pendiente(b["id"])
    assert servicio.texto_pendientes() == "No tienes pendientes anotados."


def test_pendiente_valida(servicio):
    with pytest.raises(ErrorAsistente):
        servicio.anotar_pendiente(" ")
    with pytest.raises(ErrorAsistente):
        servicio.anotar_pendiente("x", para_fecha="el viernes")
    with pytest.raises(ErrorAsistente):
        servicio.completar_pendiente(77)


# --- vistas --------------------------------------------------------------------------------

def test_texto_hoy_y_resumen(servicio, reloj):
    reloj.poner(2026, 10, 14, 7, 0)
    servicio.crear_recordatorio("agua", fecha_hora="2026-10-14 09:00", repetir={"frecuencia": "horas", "intervalo": 4})
    servicio.crear_recordatorio("llamar a Danilo", fecha_hora="2026-10-14 15:30")
    servicio.crear_recordatorio("mañana no", fecha_hora="2026-10-15 09:00")
    servicio.anotar_pendiente("cotizar paneles")
    resumen = servicio.texto_resumen_diario()
    assert resumen.startswith("☀️ Buenos días. Hoy es miércoles 14 de octubre.")
    assert "• 9:00 am — agua" in resumen
    assert "• 1:00 pm — agua" in resumen
    assert "• 3:30 pm — llamar a Danilo" in resumen
    assert "• 5:00 pm — agua" in resumen
    assert "• 9:00 pm — agua" in resumen
    assert "mañana no" not in resumen
    assert "• cotizar paneles" in resumen


def test_texto_hoy_vacio(servicio):
    assert servicio.texto_hoy() == "Hoy no tienes avisos ni pendientes. 🙌"


def test_texto_recordatorios(servicio):
    servicio.crear_recordatorio("inventario", fecha_hora="2026-10-19 08:00", repetir={"frecuencia": "semanal", "dias_semana": ["lu"]})
    texto = servicio.texto_recordatorios()
    assert "inventario — lunes 19 de octubre 8:00 am (🔁 cada lunes)" in texto
