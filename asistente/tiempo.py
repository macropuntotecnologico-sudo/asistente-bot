"""Fechas, horas y repeticiones.

Convenciones:
- Las horas que escribe el usuario (o Claude) son hora local, formato "AAAA-MM-DD HH:MM".
- En la base de datos, las próximas ocurrencias se guardan en UTC (texto ISO) y el
  ancla de una repetición se guarda como hora local sin zona; la zona se agrega al leer.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone, tzinfo

from dateutil import rrule

DIAS = {
    "lu": rrule.MO,
    "ma": rrule.TU,
    "mi": rrule.WE,
    "ju": rrule.TH,
    "vi": rrule.FR,
    "sa": rrule.SA,
    "do": rrule.SU,
}
ORDEN_DIAS = list(DIAS)

FRECUENCIAS = {
    "horas": rrule.HOURLY,
    "diaria": rrule.DAILY,
    "semanal": rrule.WEEKLY,
    "mensual": rrule.MONTHLY,
    "anual": rrule.YEARLY,
}

NOMBRES_DIA = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
NOMBRES_MES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


class ErrorFecha(ValueError):
    """Fecha, hora o repetición inválida. El mensaje se le muestra a Claude o al usuario."""


# --- Conversión ---------------------------------------------------------------------------

def a_utc_iso(dt: datetime) -> str:
    """Datetime con zona -> texto ISO en UTC, con segundos (ordenable como texto)."""
    if dt.tzinfo is None:
        raise ValueError("Se esperaba un datetime con zona horaria")
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def desde_utc_iso(texto: str) -> datetime:
    return datetime.fromisoformat(texto).astimezone(timezone.utc)


def local_sin_zona(dt: datetime, tz: tzinfo) -> str:
    """Datetime -> hora local de `tz` como texto ISO sin zona (ancla de repeticiones)."""
    return dt.astimezone(tz).replace(tzinfo=None, microsecond=0).isoformat()


def desde_local_sin_zona(texto: str, tz: tzinfo) -> datetime:
    return datetime.fromisoformat(texto).replace(tzinfo=tz)


def interpretar_fecha_hora(texto: str, tz: tzinfo) -> datetime:
    """'AAAA-MM-DD HH:MM' (hora local) -> datetime con zona."""
    limpio = (texto or "").strip().replace("T", " ")
    for formato in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(limpio, formato).replace(tzinfo=tz)
        except ValueError:
            continue
    raise ErrorFecha(f"Fecha y hora inválidas: {texto!r}. Usa el formato AAAA-MM-DD HH:MM.")


def interpretar_fecha(texto: str) -> date:
    try:
        return date.fromisoformat((texto or "").strip())
    except ValueError:
        raise ErrorFecha(f"Fecha inválida: {texto!r}. Usa el formato AAAA-MM-DD.") from None


# --- Repeticiones -------------------------------------------------------------------------

def validar_repeticion(rep: dict | None) -> dict | None:
    """Normaliza la repetición que propone Claude. Devuelve None si no hay repetición."""
    if not rep:
        return None
    if not isinstance(rep, dict):
        raise ErrorFecha("La repetición debe ser un objeto.")

    frecuencia = str(rep.get("frecuencia", "")).strip().lower()
    if frecuencia not in FRECUENCIAS:
        raise ErrorFecha(
            f"Frecuencia inválida: {frecuencia!r}. Opciones: {', '.join(FRECUENCIAS)}."
        )

    try:
        bruto = rep.get("intervalo")
        intervalo = 1 if bruto in (None, "") else int(bruto)
    except (TypeError, ValueError):
        raise ErrorFecha("El intervalo debe ser un número entero.") from None
    if not 1 <= intervalo <= 1000:
        raise ErrorFecha("El intervalo debe estar entre 1 y 1000.")

    dias_entrada = rep.get("dias_semana") or []
    if isinstance(dias_entrada, str):
        dias_entrada = [dias_entrada]
    dias = []
    for d in dias_entrada:
        clave = str(d).strip().lower()[:2]
        if clave not in DIAS:
            raise ErrorFecha(f"Día inválido: {d!r}. Usa lu, ma, mi, ju, vi, sa, do.")
        if clave not in dias:
            dias.append(clave)
    if dias and frecuencia not in ("diaria", "semanal"):
        raise ErrorFecha("Los días de la semana solo aplican a repeticiones diarias o semanales.")
    dias.sort(key=ORDEN_DIAS.index)

    normal: dict = {"frecuencia": frecuencia, "intervalo": intervalo}
    if dias:
        normal["dias_semana"] = dias
    if rep.get("hasta"):
        normal["hasta"] = interpretar_fecha(str(rep["hasta"])).isoformat()
    return normal


def construir_regla(rep: dict, inicio: datetime) -> rrule.rrule:
    argumentos: dict = {
        "freq": FRECUENCIAS[rep["frecuencia"]],
        "interval": rep.get("intervalo", 1),
        "dtstart": inicio,
    }
    if rep.get("dias_semana"):
        argumentos["byweekday"] = [DIAS[d] for d in rep["dias_semana"]]
    if rep.get("hasta"):
        argumentos["until"] = datetime.combine(
            date.fromisoformat(rep["hasta"]), time(23, 59, 59), tzinfo=inicio.tzinfo
        )
    return rrule.rrule(**argumentos)


def siguiente_ocurrencia(rep: dict, inicio: datetime, despues_de: datetime) -> datetime | None:
    """Primera ocurrencia estrictamente posterior a `despues_de` (None si la serie terminó)."""
    regla = construir_regla(rep, inicio)
    return regla.after(despues_de.astimezone(inicio.tzinfo), inc=False)


def primera_ocurrencia(rep: dict, inicio: datetime, desde: datetime) -> datetime | None:
    """Primera ocurrencia en o después de `desde`."""
    regla = construir_regla(rep, inicio)
    return regla.after(desde.astimezone(inicio.tzinfo), inc=True)


def ocurrencias_entre(rep: dict, inicio: datetime, desde: datetime, hasta: datetime) -> list[datetime]:
    regla = construir_regla(rep, inicio)
    tz = inicio.tzinfo
    return list(regla.between(desde.astimezone(tz), hasta.astimezone(tz), inc=True))


def describir_repeticion(rep: dict | None) -> str:
    if not rep:
        return ""
    n = rep.get("intervalo", 1)
    frecuencia = rep["frecuencia"]
    unidades = {
        "horas": ("cada hora", f"cada {n} horas"),
        "diaria": ("todos los días", f"cada {n} días"),
        "semanal": ("cada semana", f"cada {n} semanas"),
        "mensual": ("cada mes", f"cada {n} meses"),
        "anual": ("cada año", f"cada {n} años"),
    }
    texto = unidades[frecuencia][0 if n == 1 else 1]
    dias = rep.get("dias_semana")
    if dias:
        if dias == ["lu", "ma", "mi", "ju", "vi"]:
            texto = "de lunes a viernes" if n == 1 else f"{texto}, de lunes a viernes"
        else:
            nombres = [NOMBRES_DIA[ORDEN_DIAS.index(d)] for d in dias]
            lista = nombres[0] if len(nombres) == 1 else ", ".join(nombres[:-1]) + " y " + nombres[-1]
            if n == 1:
                texto = f"cada {lista}" if len(nombres) == 1 else f"los {lista}"
            else:
                texto = f"{texto} ({lista})"
    if rep.get("hasta"):
        fin = date.fromisoformat(rep["hasta"])
        texto += f", hasta el {fin.day} de {NOMBRES_MES[fin.month - 1]}"
    return texto


# --- Formato para humanos -----------------------------------------------------------------

def formatear_hora(dt: datetime) -> str:
    sufijo = "am" if dt.hour < 12 else "pm"
    hora = dt.hour % 12 or 12
    return f"{hora}:{dt.minute:02d} {sufijo}"


def formatear_cuando(dt: datetime, ahora: datetime, tz: tzinfo) -> str:
    """'hoy 3:00 pm', 'mañana 8:00 am', 'martes 21 de octubre 3:00 pm'."""
    local = dt.astimezone(tz)
    hoy = ahora.astimezone(tz).date()
    dias = (local.date() - hoy).days
    hora = formatear_hora(local)
    if dias == 0:
        return f"hoy {hora}"
    if dias == 1:
        return f"mañana {hora}"
    if dias == -1:
        return f"ayer {hora}"
    nombre = NOMBRES_DIA[local.weekday()]
    texto = f"{nombre} {local.day} de {NOMBRES_MES[local.month - 1]}"
    if local.year != hoy.year:
        texto += f" de {local.year}"
    return f"{texto} {hora}"


def formatear_fecha(d: date, hoy: date) -> str:
    dias = (d - hoy).days
    if dias == 0:
        return "hoy"
    if dias == 1:
        return "mañana"
    if dias == -1:
        return "ayer"
    texto = f"{NOMBRES_DIA[d.weekday()]} {d.day} de {NOMBRES_MES[d.month - 1]}"
    if d.year != hoy.year:
        texto += f" de {d.year}"
    return texto


def calendario_proximo(ahora: datetime, tz: tzinfo, dias: int = 14) -> str:
    """Lista de los próximos días con su fecha, para que Claude no se equivoque al calcular."""
    hoy = ahora.astimezone(tz).date()
    partes = []
    for i in range(dias):
        d = hoy + timedelta(days=i)
        etiqueta = "hoy" if i == 0 else ("mañana" if i == 1 else NOMBRES_DIA[d.weekday()])
        if i < 2:
            etiqueta = f"{etiqueta} ({NOMBRES_DIA[d.weekday()]})"
        partes.append(f"{etiqueta} {d.isoformat()}")
    return "; ".join(partes)
