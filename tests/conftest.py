from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from asistente.almacen import Almacen  # noqa: E402
from asistente.servicio import Servicio  # noqa: E402

BOGOTA = ZoneInfo("America/Bogota")


class RelojFalso:
    """Reloj controlable. Arranca el miércoles 14 de octubre de 2026, 10:00 am en Bogotá."""

    def __init__(self, inicio: datetime | None = None):
        self.momento = inicio or datetime(2026, 10, 14, 10, 0, tzinfo=BOGOTA)

    def __call__(self) -> datetime:
        return self.momento.astimezone(timezone.utc)

    def avanzar(self, **kwargs) -> None:
        self.momento = self.momento + timedelta(**kwargs)

    def poner(self, *args) -> None:
        self.momento = datetime(*args, tzinfo=BOGOTA)


@pytest.fixture
def reloj() -> RelojFalso:
    return RelojFalso()


@pytest.fixture
def almacen() -> Almacen:
    return Almacen(":memory:")


@pytest.fixture
def servicio(almacen: Almacen, reloj: RelojFalso) -> Servicio:
    return Servicio(almacen, BOGOTA, reloj)
