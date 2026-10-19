"""Copia consistente de la base de datos, aunque el bot esté corriendo.

Uso dentro del contenedor:
    python -m asistente.respaldo [ruta_destino]
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path


def respaldar(origen: Path, destino: Path) -> Path:
    if not origen.exists():
        raise FileNotFoundError(f"No existe la base de datos {origen}")
    destino.parent.mkdir(parents=True, exist_ok=True)
    fuente = sqlite3.connect(str(origen))
    copia = sqlite3.connect(str(destino))
    try:
        fuente.backup(copia)
    finally:
        copia.close()
        fuente.close()
    return destino


def main() -> int:
    carpeta = Path(os.environ.get("CARPETA_DATOS", "/data"))
    destino = Path(sys.argv[1]) if len(sys.argv) > 1 else carpeta / "respaldo.db"
    try:
        ruta = respaldar(carpeta / "asistente.db", destino)
    except Exception as e:
        print(f"No se pudo respaldar: {e}", file=sys.stderr)
        return 1
    print(f"Respaldo listo: {ruta}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
