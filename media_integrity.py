"""Diagnóstico profundo e somente de leitura da integridade dos fluxos A/V."""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class IntegrityResult:
    path: Path
    ok: bool
    detail: str = ""


def check_media_integrity(path: Path, timeout: float = 1800.0) -> IntegrityResult:
    """Descodifica todos os fluxos de vídeo/áudio sem criar ficheiros de saída.

    É intencionalmente uma operação explícita: descodificar um filme inteiro
    pode consumir bastante CPU e tempo. Não altera o ficheiro de origem.
    """
    executable = shutil.which("ffmpeg")
    if not executable:
        return IntegrityResult(path, False, "FFmpeg não está instalado.")
    if not path.is_file():
        return IntegrityResult(path, False, "Ficheiro inexistente ou inacessível.")
    if path.stat().st_size == 0:
        return IntegrityResult(path, False, "Ficheiro vazio (0 bytes).")

    env = os.environ.copy()
    env["LANG"] = "C"
    env["LC_ALL"] = "C"
    command = [
        executable, "-hide_banner", "-nostdin", "-v", "error",
        "-xerror", "-err_detect", "explode", "-i", str(path),
        "-map", "0:v?", "-map", "0:a?", "-f", "null", "-",
    ]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout,
            env=env, check=False,
        )
    except subprocess.TimeoutExpired:
        return IntegrityResult(path, False, f"Tempo limite excedido ({timeout:g} s).")
    except OSError as error:
        return IntegrityResult(path, False, f"Não foi possível executar FFmpeg: {error}")

    if completed.returncode == 0:
        return IntegrityResult(path, True)
    detail = (completed.stderr or "FFmpeg terminou com erro sem detalhes.").strip()
    # Limita o texto guardado/apresentado; stderr pode conter muitas linhas.
    lines = [line.strip() for line in detail.splitlines() if line.strip()]
    return IntegrityResult(path, False, "\n".join(lines[-8:])[:1600])