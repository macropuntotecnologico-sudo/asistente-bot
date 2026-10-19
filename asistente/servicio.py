"""Lógica del asistente: lo que se puede hacer con recordatorios y pendientes.

La usan tres caminos: las herramientas de Claude, los comandos (/hoy, /pendientes...)
y los botones de los avisos. Ninguno toca la base de datos directamente.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone, tzinfo
from typing import Any, Callable

from . import tiempo
from .almacen import Almacen
from .tiempo import ErrorFecha

Reloj = Callable[[], datetime]

# Un aviso que se envía más de esto después de su hora se marca como atrasado.
TOLERANCIA_ATRASO = timedelta(minutes=10)
# Una hora "en el pasado" dentro de este margen se acepta y se avisa de una vez.
TOLERANCIA_PASADO = timedelta(minutes=2)
MAX_MINUTOS = 60 * 24 * 366 * 5


class ErrorAsistente(ValueError):
    """Error que se le explica al usuario (o a Claude) tal cual."""


def reloj_real() -> datetime:
    return datetime.now(timezone.utc)


class Servicio:
    def __init__(self, almacen: Almacen, tz: tzinfo, reloj: Reloj = reloj_real):
        self.almacen = almacen
        self.tz = tz
        self.reloj = reloj

    # --- utilidades -----------------------------------------------------------------------

    def ahora(self) -> datetime:
        return self.reloj().astimezone(timezone.utc)

    def cuando(self, dt: datetime) -> str:
        return tiempo.formatear_cuando(dt, self.ahora(), self.tz)

    def _momento(self, fecha_hora: str | None, en_minutos: Any) -> datetime:
        """Resuelve la hora pedida: una fecha-hora local o 'en N minutos'."""
        tiene_fecha = bool(fecha_hora and str(fecha_hora).strip())
        tiene_minutos = en_minutos not in (None, "", 0)
        if tiene_fecha and tiene_minutos:
            raise ErrorAsistente("Indica fecha_hora o en_minutos, no ambos.")
        if tiene_minutos:
            try:
                minutos = int(en_minutos)
            except (TypeError, ValueError):
                raise ErrorAsistente("en_minutos debe ser un número entero.") from None
            if not 1 <= minutos <= MAX_MINUTOS:
                raise ErrorAsistente("en_minutos debe ser mayor que 0.")
            return (self.ahora() + timedelta(minutes=minutos)).replace(microsecond=0)
        if tiene_fecha:
            return tiempo.interpretar_fecha_hora(str(fecha_hora), self.tz)
        raise ErrorAsistente("Falta la hora: indica fecha_hora o en_minutos.")

    def _programar(self, inicio: datetime, repeticion: dict | None) -> datetime:
        """Calcula la próxima ocurrencia a partir del inicio pedido."""
        ahora = self.ahora()
        if repeticion:
            proxima = tiempo.primera_ocurrencia(repeticion, inicio, ahora)
            if proxima is None:
                raise ErrorAsistente("Esa repetición ya terminó: no queda ninguna fecha futura.")
            return proxima
        if inicio < ahora - TOLERANCIA_PASADO:
            raise ErrorAsistente(
                f"Esa hora ya pasó ({self.cuando(inicio)}); ahora es {self.cuando(ahora)}."
            )
        return max(inicio, ahora)

    def _vista_recordatorio(self, r: dict[str, Any]) -> dict[str, Any]:
        vista: dict[str, Any] = {"id": r["id"], "texto": r["texto"], "estado": r["estado"]}
        if r.get("proxima") and r["estado"] == "activo":
            proxima = tiempo.desde_utc_iso(r["proxima"])
            vista["proxima"] = self.cuando(proxima)
            vista["proxima_fecha_hora"] = proxima.astimezone(self.tz).strftime("%Y-%m-%d %H:%M")
        if r.get("repeticion"):
            vista["repite"] = tiempo.describir_repeticion(r["repeticion"])
        return vista

    def _recordatorio_vigente(self, id_: Any) -> dict[str, Any]:
        try:
            id_ = int(id_)
        except (TypeError, ValueError):
            raise ErrorAsistente("El id del recordatorio debe ser un número.") from None
        r = self.almacen.obtener_recordatorio(id_)
        if r is None or r["estado"] in ("borrado", "completado"):
            raise ErrorAsistente(f"No existe un recordatorio vigente con id {id_}.")
        return r

    # --- recordatorios --------------------------------------------------------------------

    def crear_recordatorio(
        self,
        texto: str,
        fecha_hora: str | None = None,
        en_minutos: Any = None,
        repetir: dict | None = None,
    ) -> dict[str, Any]:
        texto = (texto or "").strip()
        if not texto:
            raise ErrorAsistente("El recordatorio necesita un texto.")
        try:
            repeticion = tiempo.validar_repeticion(repetir)
        except ErrorFecha as e:
            raise ErrorAsistente(str(e)) from None
        inicio = self._momento(fecha_hora, en_minutos)
        proxima = self._programar(inicio, repeticion)
        id_ = self.almacen.crear_recordatorio(
            texto=texto,
            inicio=tiempo.local_sin_zona(inicio, self.tz),
            proxima=tiempo.a_utc_iso(proxima),
            repeticion=repeticion,
            creado=tiempo.a_utc_iso(self.ahora()),
        )
        return self._vista_recordatorio(self.almacen.obtener_recordatorio(id_))

    def modificar_recordatorio(
        self,
        id: Any,
        texto: str | None = None,
        fecha_hora: str | None = None,
        en_minutos: Any = None,
        repetir: dict | None = None,
        quitar_repeticion: bool = False,
    ) -> dict[str, Any]:
        r = self._recordatorio_vigente(id)
        cambios: dict[str, Any] = {}

        if texto is not None:
            texto = texto.strip()
            if not texto:
                raise ErrorAsistente("El texto no puede quedar vacío.")
            cambios["texto"] = texto

        repeticion = r["repeticion"]
        if quitar_repeticion:
            repeticion = None
        elif repetir:
            try:
                repeticion = tiempo.validar_repeticion(repetir)
            except ErrorFecha as e:
                raise ErrorAsistente(str(e)) from None

        cambia_hora = bool(fecha_hora and str(fecha_hora).strip()) or en_minutos not in (None, "", 0)
        cambia_repeticion = repeticion != r["repeticion"]
        if cambia_hora or cambia_repeticion:
            if cambia_hora:
                inicio = self._momento(fecha_hora, en_minutos)
            elif repeticion is None and r.get("proxima"):
                # Se quitó la repetición: queda un aviso único en la próxima fecha que tenía.
                inicio = tiempo.desde_utc_iso(r["proxima"])
            else:
                inicio = tiempo.desde_local_sin_zona(r["inicio"], self.tz)
            proxima = self._programar(inicio, repeticion)
            cambios.update(
                inicio=tiempo.local_sin_zona(inicio, self.tz),
                proxima=tiempo.a_utc_iso(proxima),
                repeticion=repeticion,
                estado="activo",
            )

        if not cambios:
            raise ErrorAsistente("No indicaste ningún cambio.")
        self.almacen.actualizar_recordatorio(r["id"], **cambios)
        return self._vista_recordatorio(self.almacen.obtener_recordatorio(r["id"]))

    def borrar_recordatorio(self, id: Any) -> dict[str, Any]:
        r = self._recordatorio_vigente(id)
        self.almacen.actualizar_recordatorio(r["id"], estado="borrado")
        return {"id": r["id"], "texto": r["texto"], "borrado": True}

    def listar_recordatorios(self) -> list[dict[str, Any]]:
        return [self._vista_recordatorio(r) for r in self.almacen.recordatorios_activos()]

    # --- avisos (los usa el programador) --------------------------------------------------

    def vencidos(self) -> list[dict[str, Any]]:
        return self.almacen.recordatorios_vencidos(tiempo.a_utc_iso(self.ahora()))

    def texto_aviso(self, r: dict[str, Any]) -> str:
        ocurrencia = tiempo.desde_utc_iso(r["proxima"])
        texto = f"⏰ {r['texto']}"
        if self.ahora() - ocurrencia > TOLERANCIA_ATRASO:
            texto += f"\n(atrasado: era para {self.cuando(ocurrencia)})"
        if r.get("repeticion"):
            texto += f"\n🔁 {tiempo.describir_repeticion(r['repeticion'])}"
        return texto

    def registrar_aviso(self, r: dict[str, Any]) -> None:
        """Marca un aviso como enviado y, si se repite, programa la siguiente vez."""
        actual = self.almacen.obtener_recordatorio(r["id"])
        if actual is None or actual["estado"] != "activo" or actual["proxima"] != r["proxima"]:
            return  # se modificó o borró mientras salía el aviso: se respeta el cambio
        r = actual
        ocurrencia = r["proxima"]
        if r.get("repeticion"):
            inicio = tiempo.desde_local_sin_zona(r["inicio"], self.tz)
            siguiente = tiempo.siguiente_ocurrencia(r["repeticion"], inicio, self.ahora())
            if siguiente is None:
                self.almacen.actualizar_recordatorio(
                    r["id"], estado="completado", ultima_ocurrencia=ocurrencia
                )
            else:
                self.almacen.actualizar_recordatorio(
                    r["id"], proxima=tiempo.a_utc_iso(siguiente), ultima_ocurrencia=ocurrencia
                )
        else:
            self.almacen.actualizar_recordatorio(
                r["id"], estado="avisado", ultima_ocurrencia=ocurrencia
            )

    # --- botones del aviso ----------------------------------------------------------------

    def _desde_boton(self, id_: int) -> dict[str, Any] | None:
        r = self.almacen.obtener_recordatorio(id_)
        if r is None or r["estado"] == "borrado":
            return None
        return r

    def marcar_hecho(self, id_: int) -> str:
        r = self._desde_boton(id_)
        if r is None:
            return "Ese recordatorio ya no existe."
        if not r.get("repeticion") and r["estado"] in ("activo", "avisado"):
            self.almacen.actualizar_recordatorio(id_, estado="completado")
        return "✅ Hecho"

    def _posponer_a(self, r: dict[str, Any], cuando: datetime) -> str:
        if r.get("repeticion"):
            # La serie sigue igual; se crea un aviso único aparte.
            self.almacen.crear_recordatorio(
                texto=r["texto"],
                inicio=tiempo.local_sin_zona(cuando, self.tz),
                proxima=tiempo.a_utc_iso(cuando),
                repeticion=None,
                creado=tiempo.a_utc_iso(self.ahora()),
            )
        else:
            self.almacen.actualizar_recordatorio(
                r["id"],
                inicio=tiempo.local_sin_zona(cuando, self.tz),
                proxima=tiempo.a_utc_iso(cuando),
                estado="activo",
            )
        return f"Te lo recuerdo {self.cuando(cuando)}"

    def posponer(self, id_: int, minutos: int = 60) -> str:
        r = self._desde_boton(id_)
        if r is None:
            return "Ese recordatorio ya no existe."
        cuando = (self.ahora() + timedelta(minutes=minutos)).replace(microsecond=0)
        return "⏱ " + self._posponer_a(r, cuando)

    def posponer_manana(self, id_: int) -> str:
        r = self._desde_boton(id_)
        if r is None:
            return "Ese recordatorio ya no existe."
        base = r.get("ultima_ocurrencia") or r.get("proxima")
        cuando = tiempo.desde_utc_iso(base) + timedelta(days=1) if base else None
        if cuando is None or cuando <= self.ahora():
            cuando = self.ahora() + timedelta(days=1)
        return "📅 " + self._posponer_a(r, cuando.replace(microsecond=0))

    # --- pendientes -----------------------------------------------------------------------

    def _vista_pendiente(self, p: dict[str, Any]) -> dict[str, Any]:
        vista: dict[str, Any] = {"id": p["id"], "texto": p["texto"], "hecho": bool(p["hecho"])}
        if p.get("para_fecha"):
            hoy = self.ahora().astimezone(self.tz).date()
            vista["para"] = tiempo.formatear_fecha(
                tiempo.interpretar_fecha(p["para_fecha"]), hoy
            )
            vista["para_fecha"] = p["para_fecha"]
        return vista

    def _pendiente(self, id_: Any) -> dict[str, Any]:
        try:
            id_ = int(id_)
        except (TypeError, ValueError):
            raise ErrorAsistente("El id del pendiente debe ser un número.") from None
        p = self.almacen.obtener_pendiente(id_)
        if p is None:
            raise ErrorAsistente(f"No existe un pendiente con id {id_}.")
        return p

    def anotar_pendiente(self, texto: str, para_fecha: str | None = None) -> dict[str, Any]:
        texto = (texto or "").strip()
        if not texto:
            raise ErrorAsistente("El pendiente necesita un texto.")
        fecha = None
        if para_fecha and str(para_fecha).strip():
            try:
                fecha = tiempo.interpretar_fecha(str(para_fecha)).isoformat()
            except ErrorFecha as e:
                raise ErrorAsistente(str(e)) from None
        id_ = self.almacen.crear_pendiente(texto, fecha, tiempo.a_utc_iso(self.ahora()))
        return self._vista_pendiente(self.almacen.obtener_pendiente(id_))

    def listar_pendientes(self, incluir_hechos: bool = False) -> list[dict[str, Any]]:
        return [self._vista_pendiente(p) for p in self.almacen.listar_pendientes(incluir_hechos)]

    def completar_pendiente(self, id: Any) -> dict[str, Any]:
        p = self._pendiente(id)
        if not p["hecho"]:
            self.almacen.completar_pendiente(p["id"], tiempo.a_utc_iso(self.ahora()))
        return {"id": p["id"], "texto": p["texto"], "hecho": True}

    def borrar_pendiente(self, id: Any) -> dict[str, Any]:
        p = self._pendiente(id)
        self.almacen.borrar_pendiente(p["id"])
        return {"id": p["id"], "texto": p["texto"], "borrado": True}

    # --- vistas para comandos y resumen ---------------------------------------------------

    def avisos_restantes_hoy(self) -> list[tuple[datetime, dict[str, Any]]]:
        """(momento, recordatorio) de lo que falta por avisar hoy, en orden."""
        ahora = self.ahora()
        local = ahora.astimezone(self.tz)
        fin_del_dia = datetime.combine(local.date(), time(23, 59, 59), tzinfo=self.tz)
        resultado: list[tuple[datetime, dict[str, Any]]] = []
        for r in self.almacen.recordatorios_activos():
            if not r.get("proxima"):
                continue
            proxima = tiempo.desde_utc_iso(r["proxima"])
            if r.get("repeticion"):
                inicio = tiempo.desde_local_sin_zona(r["inicio"], self.tz)
                desde = proxima
                if proxima <= ahora:
                    # Aviso atrasado que aún no sale: se lista una vez y luego lo que sigue.
                    resultado.append((proxima, r))
                    desde = ahora + timedelta(seconds=1)
                for momento in tiempo.ocurrencias_entre(r["repeticion"], inicio, desde, fin_del_dia):
                    resultado.append((momento, r))
            elif proxima <= fin_del_dia:
                resultado.append((proxima, r))
        resultado.sort(key=lambda par: (par[0], par[1]["id"]))
        return resultado

    def texto_hoy(self) -> str:
        lineas = []
        avisos = self.avisos_restantes_hoy()
        if avisos:
            lineas.append("⏰ Hoy te aviso:")
            ahora = self.ahora()
            for momento, r in avisos:
                hora = tiempo.formatear_hora(momento.astimezone(self.tz))
                marca = " (atrasado)" if momento < ahora - TOLERANCIA_ATRASO else ""
                lineas.append(f"• {hora}{marca} — {r['texto']}")
        pendientes = self._texto_pendientes_relevantes()
        if pendientes:
            if lineas:
                lineas.append("")
            lineas.extend(pendientes)
        return "\n".join(lineas) if lineas else "Hoy no tienes avisos ni pendientes. 🙌"

    def _texto_pendientes_relevantes(self) -> list[str]:
        pendientes = self.almacen.listar_pendientes()
        if not pendientes:
            return []
        hoy = self.ahora().astimezone(self.tz).date()
        lineas = [f"📝 Pendientes ({len(pendientes)}):"]
        for p in pendientes:
            lineas.append("• " + self._linea_pendiente(p, hoy))
        return lineas

    def _linea_pendiente(self, p: dict[str, Any], hoy) -> str:
        linea = f"{p['texto']}"
        if p.get("para_fecha"):
            fecha = tiempo.interpretar_fecha(p["para_fecha"])
            etiqueta = tiempo.formatear_fecha(fecha, hoy)
            if fecha < hoy:
                linea += f" (vencido: era para {etiqueta})"
            else:
                linea += f" (para {etiqueta})"
        return linea

    def texto_pendientes(self) -> str:
        pendientes = self.almacen.listar_pendientes()
        if not pendientes:
            return "No tienes pendientes anotados."
        hoy = self.ahora().astimezone(self.tz).date()
        lineas = [f"📝 Pendientes ({len(pendientes)}):"]
        for p in pendientes:
            lineas.append(f"{p['id']}. " + self._linea_pendiente(p, hoy))
        return "\n".join(lineas)

    def texto_recordatorios(self) -> str:
        activos = self.almacen.recordatorios_activos()
        if not activos:
            return "No tienes recordatorios programados."
        lineas = [f"⏰ Recordatorios ({len(activos)}):"]
        for r in activos:
            linea = f"{r['id']}. {r['texto']} — {self.cuando(tiempo.desde_utc_iso(r['proxima']))}"
            if r.get("repeticion"):
                linea += f" (🔁 {tiempo.describir_repeticion(r['repeticion'])})"
            lineas.append(linea)
        return "\n".join(lineas)

    def texto_resumen_diario(self) -> str:
        local = self.ahora().astimezone(self.tz)
        encabezado = (
            f"☀️ Buenos días. Hoy es {tiempo.NOMBRES_DIA[local.weekday()]} "
            f"{local.day} de {tiempo.NOMBRES_MES[local.month - 1]}."
        )
        return f"{encabezado}\n\n{self.texto_hoy()}"
