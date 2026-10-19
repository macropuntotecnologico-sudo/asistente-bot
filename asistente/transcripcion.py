"""Notas de voz a texto con faster-whisper, en el mismo servidor (sin enviar el audio a terceros)."""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path

log = logging.getLogger(__name__)


class ErrorTranscripcion(RuntimeError):
    pass


class Transcriptor:
    def __init__(self, modelo: str | None, carpeta_modelos: Path, idioma: str = "es"):
        self.modelo = modelo
        self.carpeta_modelos = carpeta_modelos
        self.idioma = idioma
        self._whisper = None
        self._candado = threading.Lock()

    @property
    def activo(self) -> bool:
        return bool(self.modelo)

    def _cargar(self):
        with self._candado:
            if self._whisper is None:
                try:
                    from faster_whisper import WhisperModel
                except ImportError as e:
                    raise ErrorTranscripcion("faster-whisper no está instalado.") from e
                log.info("Cargando modelo de voz %s (la primera vez se descarga)...", self.modelo)
                try:
                    self.carpeta_modelos.mkdir(parents=True, exist_ok=True)
                    self._whisper = WhisperModel(
                        self.modelo,
                        device="cpu",
                        compute_type="int8",
                        download_root=str(self.carpeta_modelos),
                    )
                except Exception as e:
                    log.exception("No se pudo cargar el modelo de voz %s", self.modelo)
                    raise ErrorTranscripcion(
                        "No pude cargar el modelo de voz. Revisa que el servidor tenga internet "
                        "y mira los logs del contenedor."
                    ) from e
                log.info("Modelo de voz listo.")
            return self._whisper

    def _transcribir(self, ruta: Path) -> str:
        whisper = self._cargar()
        segmentos, _info = whisper.transcribe(
            str(ruta), language=self.idioma, vad_filter=True, beam_size=5
        )
        return " ".join(s.text.strip() for s in segmentos).strip()

    async def transcribir(self, ruta: Path) -> str:
        if not self.activo:
            raise ErrorTranscripcion("Las notas de voz están desactivadas (WHISPER_MODELO=off).")
        try:
            return await asyncio.to_thread(self._transcribir, ruta)
        except ErrorTranscripcion:
            raise
        except Exception as e:
            log.exception("Falló la transcripción")
            raise ErrorTranscripcion("No pude convertir el audio a texto.") from e

    async def precargar(self) -> None:
        """Carga el modelo en segundo plano al arrancar, para que el primer audio no tarde."""
        if not self.activo:
            return
        try:
            await asyncio.to_thread(self._cargar)
        except Exception:
            log.exception("No se pudo precargar el modelo de voz; se intentará con el primer audio")
