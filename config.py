"""Configuração do usuário: quais pastas fazem parte da biblioteca.

Fica em ~/.config/linuxtv/config.json (o lugar padrão do Linux para
configurações; os DADOS ficam em ~/.local/share, as CONFIGURAÇÕES em ~/.config).

Formato:
  {"folders": [{"path": "/home/helder/Vídeos/Filmes", "kind": "movie"}],
   "omdb_key": "..."}
  kind: "movie" (tudo é filme), "series" (tudo é série) ou "auto" (detecta).
"""
import json
import os
from pathlib import Path

# Namespace de armazenamento mantido por compatibilidade: renomear a aplicação
# para Cine Prime não deve mover nem duplicar configurações já existentes.
APP_NAME = "linuxtv"
KINDS = ("movie", "series", "auto")


def _base() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


def _config_file() -> Path:
    folder = _base() / APP_NAME
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "config.json"


def _legacy_config_file() -> Path:
    """Caminho legado opcional; a leitura nunca deve criar esta pasta."""
    return _base() / "cineflow" / "config.json"


def _load() -> dict:
    for target in (_config_file(), _legacy_config_file()):
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            continue
        if isinstance(data, dict):
            return data                       # novo primeiro; se não existir, o antigo
    return {}                               # sem arquivo ou arquivo estragado: começa vazio


def _save(data: dict) -> None:
    # Escreve num arquivo temporário e troca no final: se o programa travar
    # no meio da escrita, o arquivo de configuração antigo continua intacto.
    target = _config_file()
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.chmod(0o600)                    # só o dono lê: o arquivo guarda a chave da API
    tmp.replace(target)


def get_folders() -> list[dict]:
    folders = _load().get("folders", [])
    return [
        {"path": f["path"], "kind": f.get("kind", "auto") if f.get("kind") in KINDS else "auto"}
        for f in folders
        if isinstance(f, dict) and isinstance(f.get("path"), str)
    ]


def add_folder(path: str, kind: str) -> None:
    folders = [f for f in get_folders() if f["path"] != path]   # mesma pasta: substitui
    folders.append({"path": path, "kind": kind})
    data = _load()                      # carrega tudo e troca só "folders",
    data["folders"] = folders           # senão apagaríamos a chave do OMDb
    _save(data)


def remove_folder(path: str) -> None:
    data = _load()
    data["folders"] = [f for f in get_folders() if f["path"] != path]
    _save(data)


def get_omdb_key() -> str | None:
    """Chave do OMDb: variavel de ambiente OMDB_API_KEY ou o arquivo de configuracao."""
    key = os.environ.get("OMDB_API_KEY") or _load().get("omdb_key")
    return key.strip() if isinstance(key, str) and key.strip() else None


def set_omdb_key(key: str) -> None:
    data = _load()
    data["omdb_key"] = key.strip()
    _save(data)


def get_subtitle_font_size() -> int:
    """Tamanho global das legendas, limitado para manter leitura confortável."""
    value = _load().get("subtitle_font_size", 24)
    try:
        return max(16, min(40, int(value)))
    except (TypeError, ValueError):
        return 24


def set_subtitle_font_size(size: int) -> None:
    data = _load()
    data["subtitle_font_size"] = max(16, min(40, int(size)))
    _save(data)


def get_next_episode_notice_seconds() -> int:
    """Antecedência, em segundos, para mostrar o aviso do próximo episódio."""
    value = _load().get("next_episode_notice_seconds", 60)
    try:
        value = int(value)
    except (TypeError, ValueError):
        return 60
    return value if value in (30, 60, 90, 120, 240, 360) else 60


def set_next_episode_notice_seconds(seconds: int) -> None:
    seconds = int(seconds)
    if seconds not in (30, 60, 90, 120, 240, 360):
        raise ValueError("O intervalo deve ser 30, 60, 90, 120, 240 ou 360 segundos.")
    data = _load()
    data["next_episode_notice_seconds"] = seconds
    _save(data)