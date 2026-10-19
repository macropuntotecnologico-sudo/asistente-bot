from datetime import date, datetime

import pytest

from asistente import tiempo
from asistente.tiempo import ErrorFecha
from tests.conftest import BOGOTA


def test_interpreta_fecha_hora_local():
    dt = tiempo.interpretar_fecha_hora("2026-10-20 15:30", BOGOTA)
    assert dt == datetime(2026, 10, 20, 15, 30, tzinfo=BOGOTA)
    assert tiempo.interpretar_fecha_hora("2026-10-20T15:30", BOGOTA) == dt


@pytest.mark.parametrize("malo", ["", "mañana", "20/10/2026 15:30", "2026-13-01 10:00"])
def test_fecha_hora_invalida(malo):
    with pytest.raises(ErrorFecha):
        tiempo.interpretar_fecha_hora(malo, BOGOTA)


def test_utc_ida_y_vuelta():
    dt = datetime(2026, 10, 20, 15, 30, tzinfo=BOGOTA)
    texto = tiempo.a_utc_iso(dt)
    assert texto == "2026-10-20T20:30:00+00:00"
    assert tiempo.desde_utc_iso(texto) == dt


def test_valida_y_normaliza_repeticion():
    rep = tiempo.validar_repeticion(
        {"frecuencia": "SEMANAL", "dias_semana": ["viernes", "lunes", "lu"], "hasta": "2026-12-31"}
    )
    assert rep == {
        "frecuencia": "semanal",
        "intervalo": 1,
        "dias_semana": ["lu", "vi"],
        "hasta": "2026-12-31",
    }
    assert tiempo.validar_repeticion(None) is None
    assert tiempo.validar_repeticion({}) is None


@pytest.mark.parametrize(
    "rep",
    [
        {"frecuencia": "quincenal"},
        {"frecuencia": "diaria", "intervalo": 0},
        {"frecuencia": "mensual", "dias_semana": ["lu"]},
        {"frecuencia": "semanal", "dias_semana": ["xx"]},
        {"frecuencia": "diaria", "hasta": "pronto"},
    ],
)
def test_repeticion_invalida(rep):
    with pytest.raises(ErrorFecha):
        tiempo.validar_repeticion(rep)


def test_siguiente_ocurrencia_dias_habiles():
    rep = tiempo.validar_repeticion({"frecuencia": "semanal", "dias_semana": ["lu", "ma", "mi", "ju", "vi"]})
    inicio = datetime(2026, 10, 14, 8, 0, tzinfo=BOGOTA)  # miércoles
    viernes = datetime(2026, 10, 16, 8, 0, tzinfo=BOGOTA)
    lunes = datetime(2026, 10, 19, 8, 0, tzinfo=BOGOTA)
    assert tiempo.siguiente_ocurrencia(rep, inicio, viernes) == lunes


def test_repeticion_por_horas_y_hasta():
    rep = tiempo.validar_repeticion({"frecuencia": "horas", "intervalo": 3, "hasta": "2026-10-14"})
    inicio = datetime(2026, 10, 14, 9, 0, tzinfo=BOGOTA)
    despues = datetime(2026, 10, 14, 10, 0, tzinfo=BOGOTA)
    assert tiempo.siguiente_ocurrencia(rep, inicio, despues) == datetime(2026, 10, 14, 12, 0, tzinfo=BOGOTA)
    tarde = datetime(2026, 10, 14, 21, 0, tzinfo=BOGOTA)
    assert tiempo.siguiente_ocurrencia(rep, inicio, tarde) is None


def test_describe_repeticiones():
    d = tiempo.describir_repeticion
    assert d({"frecuencia": "diaria", "intervalo": 1}) == "todos los días"
    assert d({"frecuencia": "semanal", "intervalo": 1, "dias_semana": ["lu"]}) == "cada lunes"
    assert d({"frecuencia": "semanal", "intervalo": 1, "dias_semana": ["lu", "ma", "mi", "ju", "vi"]}) == "de lunes a viernes"
    assert d({"frecuencia": "semanal", "intervalo": 1, "dias_semana": ["lu", "mi", "vi"]}) == "los lunes, miércoles y viernes"
    assert d({"frecuencia": "horas", "intervalo": 2}) == "cada 2 horas"
    assert d({"frecuencia": "mensual", "intervalo": 1, "hasta": "2026-12-31"}) == "cada mes, hasta el 31 de diciembre"


def test_formatear_cuando():
    ahora = datetime(2026, 10, 14, 10, 0, tzinfo=BOGOTA)
    f = lambda *a: tiempo.formatear_cuando(datetime(*a, tzinfo=BOGOTA), ahora, BOGOTA)  # noqa: E731
    assert f(2026, 10, 14, 15, 0) == "hoy 3:00 pm"
    assert f(2026, 10, 15, 0, 5) == "mañana 12:05 am"
    assert f(2026, 10, 20, 12, 0) == "martes 20 de octubre 12:00 pm"
    assert f(2027, 1, 4, 9, 30) == "lunes 4 de enero de 2027 9:30 am"


def test_calendario_proximo():
    ahora = datetime(2026, 10, 14, 10, 0, tzinfo=BOGOTA)
    cal = tiempo.calendario_proximo(ahora, BOGOTA, dias=3)
    assert cal == "hoy (miércoles) 2026-10-14; mañana (jueves) 2026-10-15; viernes 2026-10-16"


def test_formatear_fecha():
    hoy = date(2026, 10, 14)
    assert tiempo.formatear_fecha(date(2026, 10, 13), hoy) == "ayer"
    assert tiempo.formatear_fecha(date(2026, 10, 17), hoy) == "sábado 17 de octubre"
