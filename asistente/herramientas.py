"""Las acciones que Claude puede pedirle al bot. No hay ninguna otra."""

from __future__ import annotations

import json
import logging
from typing import Any

from .servicio import ErrorAsistente, Servicio

log = logging.getLogger(__name__)

_REPETICION = {
    "type": "object",
    "description": (
        "Solo si el recordatorio se repite. Ejemplos: todos los días -> {frecuencia: diaria}; "
        "de lunes a viernes -> {frecuencia: semanal, dias_semana: [lu,ma,mi,ju,vi]}; "
        "cada 3 horas -> {frecuencia: horas, intervalo: 3}; el 5 de cada mes -> {frecuencia: mensual} "
        "con fecha_hora en un día 5."
    ),
    "properties": {
        "frecuencia": {"type": "string", "enum": ["horas", "diaria", "semanal", "mensual", "anual"]},
        "intervalo": {"type": "integer", "minimum": 1, "description": "Cada cuántas unidades. Por defecto 1."},
        "dias_semana": {
            "type": "array",
            "items": {"type": "string", "enum": ["lu", "ma", "mi", "ju", "vi", "sa", "do"]},
            "description": "Días en que suena (solo para diaria o semanal).",
        },
        "hasta": {"type": "string", "description": "Último día de la repetición, AAAA-MM-DD (opcional)."},
    },
    "required": ["frecuencia"],
}

_FECHA_HORA = {
    "type": "string",
    "description": "Hora local del usuario, formato AAAA-MM-DD HH:MM (24 h).",
}
_EN_MINUTOS = {
    "type": "integer",
    "minimum": 1,
    "description": "Para 'en X minutos/horas': minutos desde ahora. Usa esto o fecha_hora, no ambos.",
}

HERRAMIENTAS: list[dict[str, Any]] = [
    {
        "name": "crear_recordatorio",
        "description": "Programa un aviso que le llegará al usuario por Telegram en el momento indicado.",
        "input_schema": {
            "type": "object",
            "properties": {
                "texto": {"type": "string", "description": "Qué hay que recordar, corto y claro."},
                "fecha_hora": _FECHA_HORA,
                "en_minutos": _EN_MINUTOS,
                "repetir": _REPETICION,
            },
            "required": ["texto"],
        },
    },
    {
        "name": "listar_recordatorios",
        "description": "Lista los recordatorios programados (con su id, próxima fecha y repetición).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "modificar_recordatorio",
        "description": (
            "Cambia el texto, la hora o la repetición de un recordatorio existente. "
            "Envía solo los campos que cambian. Si no sabes el id, usa listar_recordatorios primero."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "texto": {"type": "string"},
                "fecha_hora": _FECHA_HORA,
                "en_minutos": _EN_MINUTOS,
                "repetir": _REPETICION,
                "quitar_repeticion": {"type": "boolean", "description": "true para que deje de repetirse."},
            },
            "required": ["id"],
        },
    },
    {
        "name": "borrar_recordatorio",
        "description": "Cancela un recordatorio. Si no sabes el id, usa listar_recordatorios primero.",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
        },
    },
    {
        "name": "anotar_pendiente",
        "description": "Agrega una tarea a la lista de pendientes (no genera aviso a una hora).",
        "input_schema": {
            "type": "object",
            "properties": {
                "texto": {"type": "string"},
                "para_fecha": {"type": "string", "description": "Fecha límite opcional, AAAA-MM-DD."},
            },
            "required": ["texto"],
        },
    },
    {
        "name": "listar_pendientes",
        "description": "Lista los pendientes abiertos (con su id).",
        "input_schema": {
            "type": "object",
            "properties": {
                "incluir_hechos": {"type": "boolean", "description": "true para incluir los ya hechos."}
            },
        },
    },
    {
        "name": "completar_pendiente",
        "description": "Marca un pendiente como hecho. Si no sabes el id, usa listar_pendientes primero.",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
        },
    },
    {
        "name": "borrar_pendiente",
        "description": "Elimina un pendiente de la lista (para errores; si ya lo hizo, usa completar_pendiente).",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
        },
    },
]

_PARAMETROS = {h["name"]: set(h["input_schema"].get("properties", {})) for h in HERRAMIENTAS}


def ejecutar(servicio: Servicio, nombre: str, entrada: dict[str, Any] | None) -> tuple[str, bool]:
    """Ejecuta una herramienta. Devuelve (resultado en JSON, es_error)."""
    entrada = dict(entrada or {})
    if nombre not in _PARAMETROS:
        return json.dumps({"error": f"Herramienta desconocida: {nombre}"}), True
    # Se ignora cualquier parámetro que no esté en el esquema.
    argumentos = {k: v for k, v in entrada.items() if k in _PARAMETROS[nombre]}
    try:
        funcion = {
            "crear_recordatorio": servicio.crear_recordatorio,
            "listar_recordatorios": servicio.listar_recordatorios,
            "modificar_recordatorio": servicio.modificar_recordatorio,
            "borrar_recordatorio": servicio.borrar_recordatorio,
            "anotar_pendiente": servicio.anotar_pendiente,
            "listar_pendientes": servicio.listar_pendientes,
            "completar_pendiente": servicio.completar_pendiente,
            "borrar_pendiente": servicio.borrar_pendiente,
        }[nombre]
        resultado = funcion(**argumentos)
    except ErrorAsistente as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False), True
    except TypeError as e:
        log.warning("Argumentos inválidos para %s: %s", nombre, e)
        return json.dumps({"error": f"Argumentos inválidos: {e}"}, ensure_ascii=False), True
    except Exception:
        log.exception("Falla interna ejecutando %s", nombre)
        return json.dumps({"error": "Falla interna del bot; no se hizo el cambio."}), True
    if isinstance(resultado, list):
        resultado = {"total": len(resultado), "elementos": resultado}
    return json.dumps(resultado, ensure_ascii=False), False
