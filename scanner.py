"""Encontra vídeos nas pastas da biblioteca e calcula a "impressão digital".

Impressão digital = tamanho do arquivo + hash (SHA-1) dos primeiros e dos
últimos 64 KB. Ela não depende do NOME nem do LUGAR do arquivo: se você
renomear ou mover um vídeo, ele continua com a mesma impressão digital, e o
app reconhece que é o mesmo vídeo (mantendo nome editado, capa e progresso).

Este módulo roda numa thread (tarefa paralela) para não travar a janela, então
ele NÃO mexe no banco de dados: só lê arquivos e devolve resultados.
"""
import hashlib
import os
import shutil
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

from classifier import Item, classify

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".flv", ".webm", ".ts", ".m4v"}
CHUNK = 64 * 1024


@dataclass
class Found:
    path: Path
    item: Item
    fingerprint: str | None
    file_signature: tuple[int, int, int, int] | None = None
    media_signature: tuple[int, int, int, int] | None = None


@dataclass
class ScanResult:
    found: list[Found] = field(default_factory=list)
    prune_roots: list[Path] = field(default_factory=list)  # raízes totalmente lidas e seguras para limpeza
    missing: list[Path] = field(default_factory=list)      # pastas que não existem (HD desmontado?)
    empty: list[Path] = field(default_factory=list)        # pastas existentes, mas sem nenhum vídeo
    errors: list[tuple[Path, str]] = field(default_factory=list)  # caminhos que não puderam ser lidos


def find_videos(
    root: Path, on_error: Callable[[OSError], None] | None = None
) -> Iterator[Path]:
    """Percorre a árvore sem materializar todos os caminhos na memória.

    A ordenação é local a cada pasta, não global. Erros de acesso são
    comunicados ao chamador para impedir que uma varredura parcial apague
    registos de ficheiros que continuam numa subpasta inacessível.
    """
    def report(error: OSError) -> None:
        if on_error is not None:
            on_error(error)

    for current, dirs, files in os.walk(root, topdown=True, onerror=report, followlinks=False):
        dirs.sort(key=str.casefold)
        for name in sorted(files, key=str.casefold):
            path = Path(current) / name
            if path.suffix.lower() not in VIDEO_EXTS:
                continue
            try:
                if stat.S_ISREG(path.stat().st_mode):
                    yield path
            except OSError as error:
                report(error)


def fingerprint(path: Path) -> str | None:
    try:
        size = path.stat().st_size
        if size == 0:
            return None                   # arquivo vazio: todos teriam a mesma impressão
        digest = hashlib.sha1()
        with path.open("rb") as f:
            digest.update(f.read(CHUNK))
            if size > CHUNK:
                f.seek(max(CHUNK, size - CHUNK))
                digest.update(f.read(CHUNK))
        return f"{size}-{digest.hexdigest()}"
    except OSError:
        return None


def file_signature(path: Path) -> tuple[int, int, int, int] | None:
    """Metadados baratos para validar a cache sem reler o conteúdo."""
    try:
        info = path.stat()
        return (info.st_size, info.st_mtime_ns, info.st_ino, info.st_dev)
    except OSError:
        return None


def _fingerprint_cached(
    path: Path,
    known: dict[str, tuple[str, tuple[int, int, int, int]]],
) -> tuple[str | None, tuple[int, int, int, int] | None]:
    """Reutiliza o hash apenas se tamanho, data, inode e dispositivo coincidirem."""
    signature = file_signature(path)
    old = known.get(str(path))
    if old and signature is not None and old[1] == signature:
        return old[0], signature
    return fingerprint(path), signature



def discover_video(path: Path, timeout: float = 6.0) -> tuple[bool, str | None]:
    """Confirma que o GStreamer reconhece pelo menos um fluxo de vídeo.

    É uma descoberta estrutural (contentor/streams), não uma descodificação
    integral: não garante que todos os frames do ficheiro estejam íntegros.
    """
    executable = shutil.which("gst-discoverer-1.0")
    if not executable:
        return False, "gst-discoverer-1.0 não está instalado."
    env = os.environ.copy()
    env["LANG"] = "C"
    env["LC_ALL"] = "C"
    try:
        completed = subprocess.run(
            [executable, "-t", str(max(1, int(timeout))), str(path)],
            capture_output=True, text=True, timeout=timeout + 2, env=env, check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"Tempo limite excedido ao analisar {path.name}."
    except OSError as error:
        return False, f"Não foi possível executar o GStreamer Discoverer: {error}"
    output = completed.stdout + "\n" + completed.stderr
    if "An error was encountered" in output or "video #" not in output:
        detail = output.strip().splitlines()[-1] if output.strip() else "formato não reconhecido"
        return False, f"GStreamer não encontrou um fluxo de vídeo válido: {detail}"
    return True, None


def scan_folders(
    folders: list[tuple[Path, str | None]],
    known: dict[str, tuple[str, tuple[int, int, int, int]]],
    known_media: dict[str, tuple[int, int, int, int]] | None = None,
    *, validate_media: bool = False,
) -> ScanResult:
    """known: hash e assinatura dos ficheiros já registados, indexados pelo caminho."""
    result = ScanResult()
    known_media = known_media or {}
    seen: set[str] = set()
    for root, kind in folders:
        if not root.is_dir():
            result.missing.append(root)
            continue
        count = 0
        root_errors: list[OSError] = []

        def record_error(error: OSError) -> None:
            root_errors.append(error)
            error_path = Path(error.filename) if error.filename else root
            result.errors.append((error_path, str(error)))

        for path in find_videos(root, on_error=record_error):
            if str(path) in seen:         # pastas sobrepostas: a primeira vence
                continue
            seen.add(str(path))
            cached_fingerprint, signature = _fingerprint_cached(path, known)
            if cached_fingerprint is None:
                # Um ficheiro vazio ou que deixou de poder ser lido não pode
                # entrar na biblioteca nem autorizar a limpeza da pasta.
                if signature is None:
                    message = "O ficheiro desapareceu ou não foi possível consultar os seus metadados."
                elif signature[0] == 0:
                    message = "Ficheiro vazio (0 bytes); ignorado durante a indexação."
                else:
                    message = "Não foi possível ler o conteúdo para calcular a impressão digital."
                result.errors.append((path, message))
                root_errors.append(OSError(message, str(path)))
                continue
            media_signature = None
            if validate_media:
                if signature is not None and known_media.get(str(path)) == signature:
                    media_signature = signature
                else:
                    valid_media, validation_error = discover_video(path)
                    if not valid_media:
                        message = validation_error or "Conteúdo multimédia inválido."
                        result.errors.append((path, message))
                        root_errors.append(OSError(message, str(path)))
                        continue
                    media_signature = signature
            result.found.append(
                Found(path, classify(path, root, kind), cached_fingerprint, signature, media_signature)
            )
            count += 1

        # Só uma árvore lida por completo pode autorizar a remoção de registos.
        # Uma subpasta inacessível torna a varredura parcial, mesmo que outros
        # vídeos tenham sido encontrados na mesma raiz.
        if root_errors:
            continue
        if count:
            result.prune_roots.append(root)
        else:
            result.empty.append(root)
    return result