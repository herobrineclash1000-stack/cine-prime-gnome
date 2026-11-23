"""Backup e restauro verificáveis da biblioteca Cine Prime.

O arquivo inclui uma cópia consistente da BD via SQLite Backup API, a
configuração (incluindo chave OMDb) e as capas geridas pela aplicação.
Os vídeos originais nunca são incluídos nem modificados.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import config
import covers

FORMAT = "gnome-tv-backup"
FORMAT_VERSION = 1
MAX_ARCHIVE_BYTES = 8 * 1024**3
MAX_MEMBERS = 100_000


class BackupError(Exception):
    """Erro seguro e legível na criação ou restauração de um backup."""


def _database_path(connection: sqlite3.Connection) -> Path:
    row = connection.execute("PRAGMA database_list").fetchone()
    if not row or not row[2]:
        raise BackupError("A base de dados não tem um caminho persistente.")
    return Path(row[2])


def _config_source() -> Path | None:
    for candidate in (config._config_file(), config._legacy_config_file()):
        if candidate.is_file():
            try:
                json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            return candidate
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_backup(connection: sqlite3.Connection, destination: Path) -> dict:
    """Cria um ZIP consistente, escreve primeiro em temporário e troca no fim."""
    destination = Path(destination).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.is_dir():
        raise BackupError("O destino escolhido é uma pasta, não um ficheiro.")
    covers_root = covers.covers_dir()
    config_source = _config_source()

    with tempfile.TemporaryDirectory(prefix="cineprime-backup-") as work:
        workdir = Path(work)
        db_copy = workdir / "library.sqlite"
        snapshot = sqlite3.connect(db_copy)
        try:
            connection.backup(snapshot)
            snapshot.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            integrity = snapshot.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise BackupError(f"A cópia da base de dados não passou a verificação: {integrity}")
        finally:
            snapshot.close()

        payload: dict[str, Path] = {"database/library.sqlite": db_copy}
        if config_source:
            payload["config/config.json"] = config_source
        for path in sorted(covers_root.iterdir()):
            if path.is_file() and not path.is_symlink():
                payload[f"covers/{path.name}"] = path

        manifest = {
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "database_source": str(_database_path(connection)),
            "files": {
                name: {"size": path.stat().st_size, "sha256": _sha256(path)}
                for name, path in payload.items()
            },
        }
        manifest_path = workdir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        temp_output = destination.with_name(destination.name + ".tmp")
        try:
            with zipfile.ZipFile(temp_output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                archive.write(manifest_path, "manifest.json")
                for name, path in payload.items():
                    archive.write(path, name)
            os.chmod(temp_output, 0o600)
            os.replace(temp_output, destination)
        finally:
            try:
                temp_output.unlink()
            except FileNotFoundError:
                pass

    return {"path": destination, "files": len(payload), "size": destination.stat().st_size}


def inspect_backup(archive_path: Path) -> dict:
    """Valida estrutura, tamanhos, hashes, JSON e integridade SQLite sem restaurar."""
    archive_path = Path(archive_path).expanduser()
    if not archive_path.is_file():
        raise BackupError("O ficheiro de backup não existe.")
    if archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise BackupError("O backup excede o limite de tamanho permitido.")
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            infos = archive.infolist()
            if len(infos) > MAX_MEMBERS:
                raise BackupError("O backup contém demasiados ficheiros.")
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise BackupError("O backup contém caminhos duplicados.")
            total_uncompressed = 0
            manifest_info = next((info for info in infos if info.filename == "manifest.json"), None)
            if manifest_info is not None and manifest_info.file_size > 2 * 1024 * 1024:
                raise BackupError("O manifesto do backup excede o tamanho permitido.")
            for info in infos:
                member = PurePosixPath(info.filename)
                if member.is_absolute() or ".." in member.parts or "\\" in info.filename:
                    raise BackupError("O backup contém um caminho inseguro.")
                allowed = (
                    info.filename == "manifest.json"
                    or info.filename == "database/library.sqlite"
                    or info.filename == "config/config.json"
                    or (len(member.parts) == 2 and member.parts[0] == "covers")
                )
                if not allowed:
                    raise BackupError("O backup contém um ficheiro fora da estrutura permitida.")
                if info.is_dir():
                    continue
                total_uncompressed += info.file_size
                if total_uncompressed > MAX_ARCHIVE_BYTES:
                    raise BackupError("O conteúdo descomprimido excede o limite permitido.")
            if "manifest.json" not in names or "database/library.sqlite" not in names:
                raise BackupError("O backup está incompleto.")
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("format") != FORMAT or manifest.get("format_version") != FORMAT_VERSION:
                raise BackupError("Formato ou versão de backup não suportados.")
            declared = manifest.get("files")
            if not isinstance(declared, dict) or set(declared) != set(names) - {"manifest.json"}:
                raise BackupError("O manifesto não corresponde aos ficheiros do backup.")
            with tempfile.TemporaryDirectory(prefix="cineprime-verify-") as work:
                root = Path(work)
                for name, metadata in declared.items():
                    target = root / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    digest = hashlib.sha256()
                    size = 0
                    with archive.open(name, "r") as source, target.open("wb") as output:
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            size += len(chunk)
                            digest.update(chunk)
                            output.write(chunk)
                    if size != metadata.get("size") or digest.hexdigest() != metadata.get("sha256"):
                        raise BackupError(f"Verificação de integridade falhou: {name}")
                db_file = root / "database/library.sqlite"
                check = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
                try:
                    if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise BackupError("A base de dados dentro do backup está corrompida.")
                    if check.execute("PRAGMA foreign_key_check").fetchall():
                        raise BackupError("A base de dados contém violações de chaves estrangeiras.")
                    tables = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if not {"titles", "files", "progress"}.issubset(tables):
                        raise BackupError("A base de dados não tem o esquema esperado.")
                finally:
                    check.close()
                config_file = root / "config/config.json"
                if config_file.exists():
                    config_data = json.loads(config_file.read_text(encoding="utf-8"))
                    if not isinstance(config_data, dict):
                        raise BackupError("A configuração no backup não é um objecto JSON válido.")
                cover_count = sum(name.startswith("covers/") for name in declared)
            return {
                "created_at": manifest.get("created_at"),
                "files": len(declared),
                "covers": cover_count,
                "database_size": declared["database/library.sqlite"]["size"],
            }
    except BackupError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError, zipfile.BadZipFile, sqlite3.Error) as error:
        raise BackupError(f"Não foi possível validar o backup: {error}") from error


def restore_backup(archive_path: Path, connection: sqlite3.Connection) -> dict:
    """Restaura a BD na ligação aberta e configurações/capas com rollback em falhas."""
    summary = inspect_backup(archive_path)
    archive_path = Path(archive_path).expanduser()
    config_target = config._config_file()
    covers_target = covers.covers_dir()

    with tempfile.TemporaryDirectory(prefix="cineprime-restore-") as work:
        root = Path(work)
        with zipfile.ZipFile(archive_path, "r") as archive:
            archive.extract("database/library.sqlite", root)
            if "config/config.json" in archive.namelist():
                archive.extract("config/config.json", root)
            for name in archive.namelist():
                if name.startswith("covers/") and not name.endswith("/"):
                    archive.extract(name, root)

        incoming_db = sqlite3.connect(f"file:{root / 'database/library.sqlite'}?mode=ro", uri=True)
        rollback_db_path = root / "rollback.sqlite"
        rollback_db = sqlite3.connect(rollback_db_path)
        config_old = config_target.read_bytes() if config_target.exists() else None
        installed_covers: list[tuple[Path, bytes | None, int | None]] = []
        db_changed = False
        config_changed = False
        try:
            connection.commit()
            connection.backup(rollback_db)
            incoming_db.backup(connection)
            db_changed = True

            incoming_config = root / "config/config.json"
            if incoming_config.exists():
                config_target.parent.mkdir(parents=True, exist_ok=True)
                staged_config = config_target.with_name(config_target.name + ".restore-tmp")
                staged_config.write_bytes(incoming_config.read_bytes())
                os.chmod(staged_config, 0o600)
                os.replace(staged_config, config_target)
                config_changed = True

            for source in sorted((root / "covers").glob("*")) if (root / "covers").exists() else []:
                if source.is_file():
                    target = covers_target / source.name
                    staged = target.with_name(target.name + ".restore-tmp")
                    previous = target.read_bytes() if target.is_file() else None
                    previous_mode = target.stat().st_mode & 0o777 if target.is_file() else None
                    shutil.copyfile(source, staged)
                    os.chmod(staged, 0o600)
                    os.replace(staged, target)
                    installed_covers.append((target, previous, previous_mode))

            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BackupError("A base de dados restaurada não passou a verificação.")
        except Exception:
            if db_changed:
                rollback_db.backup(connection)
            if config_changed:
                if config_old is None:
                    config_target.unlink(missing_ok=True)
                else:
                    config_target.write_bytes(config_old)
                    os.chmod(config_target, 0o600)
            for path, previous, previous_mode in installed_covers:
                if previous is None:
                    path.unlink(missing_ok=True)
                else:
                    rollback_cover = path.with_name(path.name + ".rollback-tmp")
                    rollback_cover.write_bytes(previous)
                    os.chmod(rollback_cover, previous_mode or 0o600)
                    os.replace(rollback_cover, path)
            raise
        finally:
            incoming_db.close()
            rollback_db.close()

    return summary