"""Almacenamiento en SQLite: recordatorios y pendientes."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

ESQUEMA = """
CREATE TABLE IF NOT EXISTS recordatorios (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    texto              TEXT NOT NULL,
    inicio             TEXT NOT NULL,   -- ancla en hora local sin zona (para repeticiones)
    proxima            TEXT,            -- próxima ocurrencia en UTC ISO
    repeticion         TEXT,            -- JSON o NULL
    estado             TEXT NOT NULL DEFAULT 'activo',  -- activo | avisado | completado | borrado
    creado             TEXT NOT NULL,
    ultima_ocurrencia  TEXT             -- ocurrencia del último aviso enviado (UTC ISO)
);
CREATE INDEX IF NOT EXISTS idx_recordatorios_estado_proxima
    ON recordatorios (estado, proxima);

CREATE TABLE IF NOT EXISTS pendientes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    texto       TEXT NOT NULL,
    para_fecha  TEXT,                   -- AAAA-MM-DD opcional
    hecho       INTEGER NOT NULL DEFAULT 0,
    creado      TEXT NOT NULL,
    hecho_en    TEXT
);

CREATE TABLE IF NOT EXISTS estado (
    clave  TEXT PRIMARY KEY,
    valor  TEXT
);
"""

CAMPOS_RECORDATORIO = {"texto", "inicio", "proxima", "repeticion", "estado", "ultima_ocurrencia"}


class Almacen:
    def __init__(self, ruta: str | Path):
        if str(ruta) != ":memory:":
            Path(ruta).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(ruta), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.executescript(ESQUEMA)
        self._con.commit()

    def cerrar(self) -> None:
        self._con.close()

    # --- Recordatorios --------------------------------------------------------------------

    @staticmethod
    def _recordatorio(fila: sqlite3.Row | None) -> dict[str, Any] | None:
        if fila is None:
            return None
        datos = dict(fila)
        datos["repeticion"] = json.loads(datos["repeticion"]) if datos["repeticion"] else None
        return datos

    def crear_recordatorio(
        self, texto: str, inicio: str, proxima: str, repeticion: dict | None, creado: str
    ) -> int:
        cursor = self._con.execute(
            "INSERT INTO recordatorios (texto, inicio, proxima, repeticion, creado)"
            " VALUES (?, ?, ?, ?, ?)",
            (texto, inicio, proxima, json.dumps(repeticion) if repeticion else None, creado),
        )
        self._con.commit()
        return int(cursor.lastrowid)

    def obtener_recordatorio(self, id_: int) -> dict[str, Any] | None:
        fila = self._con.execute("SELECT * FROM recordatorios WHERE id = ?", (id_,)).fetchone()
        return self._recordatorio(fila)

    def actualizar_recordatorio(self, id_: int, **campos: Any) -> None:
        desconocidos = set(campos) - CAMPOS_RECORDATORIO
        if desconocidos:
            raise ValueError(f"Campos desconocidos: {desconocidos}")
        if not campos:
            return
        if "repeticion" in campos:
            rep = campos["repeticion"]
            campos["repeticion"] = json.dumps(rep) if rep else None
        asignaciones = ", ".join(f"{c} = ?" for c in campos)
        self._con.execute(
            f"UPDATE recordatorios SET {asignaciones} WHERE id = ?", (*campos.values(), id_)
        )
        self._con.commit()

    def recordatorios_activos(self) -> list[dict[str, Any]]:
        filas = self._con.execute(
            "SELECT * FROM recordatorios WHERE estado = 'activo' ORDER BY proxima, id"
        ).fetchall()
        return [self._recordatorio(f) for f in filas]

    def recordatorios_vencidos(self, ahora_utc_iso: str) -> list[dict[str, Any]]:
        filas = self._con.execute(
            "SELECT * FROM recordatorios WHERE estado = 'activo' AND proxima <= ?"
            " ORDER BY proxima, id",
            (ahora_utc_iso,),
        ).fetchall()
        return [self._recordatorio(f) for f in filas]

    # --- Pendientes -----------------------------------------------------------------------

    def crear_pendiente(self, texto: str, para_fecha: str | None, creado: str) -> int:
        cursor = self._con.execute(
            "INSERT INTO pendientes (texto, para_fecha, creado) VALUES (?, ?, ?)",
            (texto, para_fecha, creado),
        )
        self._con.commit()
        return int(cursor.lastrowid)

    def obtener_pendiente(self, id_: int) -> dict[str, Any] | None:
        fila = self._con.execute("SELECT * FROM pendientes WHERE id = ?", (id_,)).fetchone()
        return dict(fila) if fila else None

    def listar_pendientes(self, incluir_hechos: bool = False) -> list[dict[str, Any]]:
        consulta = "SELECT * FROM pendientes"
        if not incluir_hechos:
            consulta += " WHERE hecho = 0"
        # Primero los que tienen fecha (los más próximos arriba), luego el resto por antigüedad.
        consulta += " ORDER BY hecho, para_fecha IS NULL, para_fecha, id"
        return [dict(f) for f in self._con.execute(consulta).fetchall()]

    def completar_pendiente(self, id_: int, hecho_en: str) -> None:
        self._con.execute(
            "UPDATE pendientes SET hecho = 1, hecho_en = ? WHERE id = ?", (hecho_en, id_)
        )
        self._con.commit()

    def borrar_pendiente(self, id_: int) -> None:
        self._con.execute("DELETE FROM pendientes WHERE id = ?", (id_,))
        self._con.commit()

    # --- Estado interno (marcas del correo, etc.) ----------------------------------------

    def leer_estado(self, clave: str) -> str | None:
        fila = self._con.execute("SELECT valor FROM estado WHERE clave = ?", (clave,)).fetchone()
        return fila["valor"] if fila else None

    def guardar_estado(self, clave: str, valor: str) -> None:
        self._con.execute(
            "INSERT INTO estado (clave, valor) VALUES (?, ?)"
            " ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
            (clave, valor),
        )
        self._con.commit()
