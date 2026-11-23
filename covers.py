"""Guarda as capas escolhidas pelo usuário dentro da pasta de dados do app.

Por que COPIAR a imagem em vez de só guardar o caminho dela?
Se você apagar ou mover a imagem original (ex.: limpar a pasta Downloads),
o cartão ficaria sem capa. Com a cópia, o app não depende de nada externo.
"""
import shutil
import time
from pathlib import Path

from database import default_db_path


def covers_dir() -> Path:
    folder = default_db_path().parent / "covers"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def import_cover(src: Path, title_id: int) -> Path:
    """Copia `src` para a pasta de capas e devolve o novo caminho.

    O timestamp no nome garante que trocar a capa gera um arquivo novo
    (evita o GTK mostrar uma versão antiga em cache).
    """
    dest = covers_dir() / f"{title_id}-{int(time.time())}{src.suffix.lower()}"
    shutil.copy2(src, dest)
    return dest


def remove_if_managed(path: str) -> None:
    """Apaga a capa SOMENTE se ela estiver na pasta de capas do app.

    Capas definidas antes (pelo terminal) apontam para as suas imagens
    originais, e essas nunca devem ser apagadas por nós.
    """
    p = Path(path)
    if p.parent.resolve() == covers_dir().resolve() and p.is_file():
        p.unlink()