"""Etapa 2: guardar tudo em SQLite.

Duas tabelas:
  titles -> o que aparece na grade (um filme, uma série inteira)
  files  -> os arquivos de vídeo (1 por filme, vários por série)

IMPORTANTE: `display_name` e `cover_path` são suas edições. A re-varredura
NUNCA sobrescreve esses campos.
"""
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from scanner import Found


# Namespace de dados estável para preservar a biblioteca criada antes do
# rebranding. O nome visível Cine Prime não altera este caminho.
APP_NAME = "linuxtv"


def default_db_path() -> Path:
    # XDG: o lugar padrão no Linux para dados de aplicativos
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    folder = base / APP_NAME
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{APP_NAME}.db"
    if not path.exists():                   # compatibilidade com um caminho legado
        legacy = base / "cineflow" / "cineflow.db"
        if legacy.exists():
            return legacy
    return path


SCHEMA = """
CREATE TABLE IF NOT EXISTS titles (
    id           INTEGER PRIMARY KEY,
    key          TEXT UNIQUE NOT NULL,
    kind         TEXT NOT NULL,
    name         TEXT NOT NULL,      -- nome detectado (automático)
    display_name TEXT,               -- nome editado por você (opcional)
    year         INTEGER,
    cover_path   TEXT,
    favorite     INTEGER NOT NULL DEFAULT 0   -- 1 = favorito
);
CREATE TABLE IF NOT EXISTS files (
    id       INTEGER PRIMARY KEY,
    title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
    path     TEXT UNIQUE NOT NULL,
    season   INTEGER,
    episode  INTEGER,
    fingerprint TEXT,        -- impressão digital do conteúdo (ver scanner.py)
    fingerprint_size INTEGER,
    fingerprint_mtime_ns INTEGER,
    fingerprint_inode INTEGER,
    fingerprint_device INTEGER,
    media_validated_size INTEGER,
    media_validated_mtime_ns INTEGER,
    media_validated_inode INTEGER,
    media_validated_device INTEGER,
    credits_start REAL,      -- segundo em que começam os créditos finais (marcado pelo utilizador)
    subtitle_delay REAL NOT NULL DEFAULT 0.0 -- atraso de legendas, em segundos, por ficheiro
);
-- Onde você parou em cada arquivo. IF NOT EXISTS faz a tabela aparecer
-- sozinha em bancos antigos, sem apagar nada do que já existe.
CREATE TABLE IF NOT EXISTS progress (
    file_id    INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
    position   REAL NOT NULL,        -- segundos
    duration   REAL NOT NULL,        -- segundos
    updated_at TEXT NOT NULL
);
"""

FINISHED_FRACTION = 0.95   # passou de 95% = considerado assistido
MIN_RESUME_SECONDS = 5     # parou antes de 5 s = recomeça do zero


@dataclass
class ScanStats:
    added: int = 0
    moved: int = 0           # renomeados ou movidos (reconhecidos pela impressão digital)
    removed: int = 0
    orphan_covers: list[str] = field(default_factory=list)   # capas de títulos que sumiram


@dataclass
class Resume:
    paths: list[str]         # o que tocar, na ordem (já começa no episódio certo)
    start: float             # segundos dentro do primeiro arquivo
    duration: float | None   # duração do primeiro arquivo, se conhecida
    season: int | None
    episode: int | None
    completed: bool          # True se já assistiu tudo e vai rever do começo


@dataclass
class Episode:
    path: str
    season: int | None
    episode: int | None
    position: float | None   # onde parou (None = nunca abriu)
    duration: float | None
    credits_start: float | None = None

    @property
    def finished(self) -> bool:
        return bool(self.duration and self.position is not None
                    and self.position / self.duration >= FINISHED_FRACTION)

    @property
    def resume_at(self) -> float:
        """De onde começar ao tocar este episódio."""
        if self.finished or self.position is None or self.position < MIN_RESUME_SECONDS:
            return 0.0
        return self.position


def resume_from(episodes: list[Episode], index: int) -> Resume:
    """Plano para tocar o episódio `index` e depois os seguintes, em sequência."""
    ep = episodes[index]
    start = ep.resume_at
    return Resume(
        paths=[e.path for e in episodes[index:]],
        start=start,
        duration=ep.duration if start > 0 else None,
        season=ep.season,
        episode=ep.episode,
        completed=False,
    )


@dataclass
class ContinueItem:
    """Um título que você começou e não terminou (usado na página Início)."""
    title: sqlite3.Row      # a mesma linha que list_titles() devolve
    season: int | None      # onde vai continuar (None nos filmes)
    episode: int | None
    fraction: float         # 0 a 1: quanto já viu do arquivo atual; 0 = ainda não começou


class Database:
    def __init__(self, path: Path | None = None):
        self.conn = sqlite3.connect(path or default_db_path())
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        # Migração: bancos criados antes da coluna "fingerprint" ganham a coluna,
        # sem perder nada. (CREATE TABLE IF NOT EXISTS não altera tabelas antigas.)
        columns = {r["name"] for r in self.conn.execute("PRAGMA table_info(files)")}
        if "fingerprint" not in columns:
            self.conn.execute("ALTER TABLE files ADD COLUMN fingerprint TEXT")
            self.conn.commit()
            columns.add("fingerprint")
        for column in ("fingerprint_size", "fingerprint_mtime_ns",
                       "fingerprint_inode", "fingerprint_device"):
            if column not in columns:
                self.conn.execute(f"ALTER TABLE files ADD COLUMN {column} INTEGER")
                self.conn.commit()
                columns.add(column)
        for column in ("media_validated_size", "media_validated_mtime_ns",
                       "media_validated_inode", "media_validated_device"):
            if column not in columns:
                self.conn.execute(f"ALTER TABLE files ADD COLUMN {column} INTEGER")
                self.conn.commit()
                columns.add(column)
        if "credits_start" not in columns:
            self.conn.execute("ALTER TABLE files ADD COLUMN credits_start REAL")
            self.conn.commit()
            columns.add("credits_start")
        if "subtitle_delay" not in columns:
            self.conn.execute("ALTER TABLE files ADD COLUMN subtitle_delay REAL NOT NULL DEFAULT 0.0")
            self.conn.commit()
        title_columns = {r["name"] for r in self.conn.execute("PRAGMA table_info(titles)")}
        if "favorite" not in title_columns:           # idem para a coluna dos favoritos
            self.conn.execute("ALTER TABLE titles ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0")
            self.conn.commit()

    def known_fingerprints(self) -> dict[str, tuple[str, tuple[int, int, int, int]]]:
        """Hash e assinatura do ficheiro para validar a cache sem reler todos os vídeos."""
        rows = self.conn.execute(
            "SELECT path, fingerprint, fingerprint_size, fingerprint_mtime_ns, "
            "fingerprint_inode, fingerprint_device FROM files "
            "WHERE fingerprint IS NOT NULL AND fingerprint_size IS NOT NULL "
            "AND fingerprint_mtime_ns IS NOT NULL AND fingerprint_inode IS NOT NULL "
            "AND fingerprint_device IS NOT NULL"
        ).fetchall()
        return {
            r["path"]: (
                r["fingerprint"],
                (r["fingerprint_size"], r["fingerprint_mtime_ns"],
                 r["fingerprint_inode"], r["fingerprint_device"]),
            )
            for r in rows
        }

    def known_media_signatures(self) -> dict[str, tuple[int, int, int, int]]:
        """Assinaturas dos ficheiros cuja estrutura multimédia já foi validada."""
        rows = self.conn.execute(
            "SELECT path, media_validated_size, media_validated_mtime_ns, "
            "media_validated_inode, media_validated_device FROM files "
            "WHERE media_validated_size IS NOT NULL AND media_validated_mtime_ns IS NOT NULL "
            "AND media_validated_inode IS NOT NULL AND media_validated_device IS NOT NULL"
        ).fetchall()
        return {r["path"]: (r["media_validated_size"], r["media_validated_mtime_ns"],
                            r["media_validated_inode"], r["media_validated_device"]) for r in rows}

    def save_scan(self, found: list[Found], prune_roots: list[Path]) -> ScanStats:
        """Grava o resultado da varredura.

        Para cada arquivo encontrado há três casos:
          1. o caminho já existe no banco  -> continua no mesmo título
          2. caminho novo, mas a impressão digital é de um arquivo que SUMIU
             -> foi renomeado/movido: troca só o caminho (título, capa e
             progresso ficam intactos)
          3. caso contrário -> arquivo realmente novo
        Só apaga do banco o que sumiu DENTRO de `prune_roots`.
        """
        stats = ScanStats()
        with self.conn:  # transação: ou grava tudo, ou nada
            existing = {
                r["path"]: r
                for r in self.conn.execute(
                    "SELECT id, title_id, path, fingerprint FROM files"
                )
            }
            seen = {str(f.path) for f in found}

            # Só reassocia um caminho novo a um ficheiro desaparecido quando
            # a correspondência é inequívoca nos dois lados. Hashes repetidos
            # podem representar cópias legítimas; escolher o primeiro registo
            # poderia transferir progresso/capa de um título para outro.
            vanished: dict[str, list] = {}
            for path, row in existing.items():
                if path not in seen and row["fingerprint"]:
                    vanished.setdefault(row["fingerprint"], []).append(row)

            new_by_fingerprint: dict[str, int] = {}
            for f in found:
                path = str(f.path)
                if path not in existing and f.fingerprint:
                    new_by_fingerprint[f.fingerprint] = new_by_fingerprint.get(f.fingerprint, 0) + 1

            unambiguous_vanished = {
                fingerprint: rows[0]
                for fingerprint, rows in vanished.items()
                if len(rows) == 1 and new_by_fingerprint.get(fingerprint) == 1
            }
            matched: set[int] = set()

            for f in found:
                path = str(f.path)
                item = f.item
                if path in existing:                                   # caso 1
                    row = existing[path]
                    if f.fingerprint and row["fingerprint"] and f.fingerprint != row["fingerprint"]:
                        # Conteúdo diferente no mesmo caminho (arquivo trocado):
                        # o progresso antigo não vale mais.
                        self.conn.execute("DELETE FROM progress WHERE file_id = ?", (row["id"],))
                    signature = f.file_signature
                    self.conn.execute(
                        "UPDATE files SET season = COALESCE(?, season), "
                        "episode = COALESCE(?, episode), "
                        "fingerprint = COALESCE(?, fingerprint), "
                        "fingerprint_size = ?, fingerprint_mtime_ns = ?, "
                        "fingerprint_inode = ?, fingerprint_device = ?, "
                        "media_validated_size = COALESCE(?, media_validated_size), "
                        "media_validated_mtime_ns = COALESCE(?, media_validated_mtime_ns), "
                        "media_validated_inode = COALESCE(?, media_validated_inode), "
                        "media_validated_device = COALESCE(?, media_validated_device) WHERE id = ?",
                        (item.season, item.episode, f.fingerprint,
                         *(signature or (None, None, None, None)),
                         *(f.media_signature or (None, None, None, None)), row["id"]),
                    )
                    continue

                row = unambiguous_vanished.get(f.fingerprint) if f.fingerprint else None
                if row is not None:                                    # caso 2: match único
                    matched.add(row["id"])
                    signature = f.file_signature
                    self.conn.execute(
                        # COALESCE: se o novo nome não traz temporada/episódio,
                        # mantém o que já sabíamos em vez de apagar.
                        "UPDATE files SET path = ?, season = COALESCE(?, season), "
                        "episode = COALESCE(?, episode), fingerprint = COALESCE(?, fingerprint), "
                        "fingerprint_size = ?, fingerprint_mtime_ns = ?, "
                        "fingerprint_inode = ?, fingerprint_device = ?, "
                        "media_validated_size = ?, media_validated_mtime_ns = ?, "
                        "media_validated_inode = ?, media_validated_device = ? WHERE id = ?",
                        (path, item.season, item.episode, f.fingerprint,
                         *(signature or (None, None, None, None)),
                         *(f.media_signature or (None, None, None, None)), row["id"]),
                    )
                    stats.moved += 1
                    continue

                self.conn.execute(                                     # caso 3
                    "INSERT INTO titles (key, kind, name, year) VALUES (?,?,?,?) "
                    "ON CONFLICT(key) DO NOTHING",
                    (item.key, item.kind, item.title, item.year),
                )
                title_id = self.conn.execute(
                    "SELECT id FROM titles WHERE key = ?", (item.key,)
                ).fetchone()["id"]
                self.conn.execute(
                    "INSERT INTO files (title_id, path, season, episode, fingerprint, "
                    "fingerprint_size, fingerprint_mtime_ns, fingerprint_inode, fingerprint_device, "
                    "media_validated_size, media_validated_mtime_ns, media_validated_inode, media_validated_device) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (title_id, path, item.season, item.episode, f.fingerprint,
                     *(f.file_signature or (None, None, None, None)),
                     *(f.media_signature or (None, None, None, None))),
                )
                stats.added += 1

            for path, row in existing.items():
                if path in seen or row["id"] in matched:
                    continue
                if any(Path(path).is_relative_to(root) for root in prune_roots):
                    self.conn.execute("DELETE FROM files WHERE id = ?", (row["id"],))
                    stats.removed += 1

            stats.orphan_covers = self._prune_orphans()
        return stats

    def duplicate_groups(self) -> list[dict]:
        """Devolve grupos de ficheiros com conteúdo idêntico, sem os fundir.

        Cada grupo pode conter títulos diferentes. A identidade do conteúdo
        serve apenas para diagnóstico; não altera título, progresso ou ficheiro.
        """
        rows = self.conn.execute(
            "WITH duplicate_fingerprints AS ("
            " SELECT fingerprint, COUNT(*) AS copies FROM files "
            " WHERE fingerprint IS NOT NULL AND fingerprint != '' "
            " GROUP BY fingerprint HAVING COUNT(*) > 1"
            ") "
            "SELECT d.fingerprint, d.copies, f.path, f.season, f.episode, "
            "t.id AS title_id, t.kind, t.name, t.display_name, t.year "
            "FROM duplicate_fingerprints d "
            "JOIN files f ON f.fingerprint = d.fingerprint "
            "JOIN titles t ON t.id = f.title_id "
            "ORDER BY d.copies DESC, d.fingerprint, t.name COLLATE NOCASE, "
            "f.season, f.episode, f.path"
        ).fetchall()

        # Uma única consulta evita N+1 acessos à BD quando existem muitos grupos.
        result = []
        for row in rows:
            fingerprint = row["fingerprint"]
            if not result or result[-1]["fingerprint"] != fingerprint:
                result.append({"fingerprint": fingerprint, "files": []})
            result[-1]["files"].append(row)
        return result

    def _prune_orphans(self) -> list[str]:
        """Apaga títulos sem nenhum arquivo e devolve as capas que ficaram soltas."""
        orphan_sql = "SELECT DISTINCT title_id FROM files"
        rows = self.conn.execute(
            f"SELECT cover_path FROM titles WHERE id NOT IN ({orphan_sql})"
        ).fetchall()
        self.conn.execute(f"DELETE FROM titles WHERE id NOT IN ({orphan_sql})")
        return [r["cover_path"] for r in rows if r["cover_path"]]

    def remove_files_under(self, root: Path) -> list[str]:
        """Tira da biblioteca tudo que está dentro de `root` (os vídeos NÃO são apagados)."""
        with self.conn:
            for r in self.conn.execute("SELECT id, path FROM files").fetchall():
                if Path(r["path"]).is_relative_to(root):
                    self.conn.execute("DELETE FROM files WHERE id = ?", (r["id"],))
            return self._prune_orphans()

    def get_title(self, title_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM titles WHERE id = ?", (title_id,)
        ).fetchone()

    def list_titles(self, kind: str | None = None, favorites_only: bool = False) -> list[sqlite3.Row]:
        sql = (
            "SELECT t.*, COALESCE(t.display_name, t.name) AS shown, "
            "COUNT(f.id) AS n_files, MIN(f.path) AS sample_path FROM titles t "
            "LEFT JOIN files f ON f.title_id = t.id "
        )
        conditions, args = [], []
        if kind:
            conditions.append("t.kind = ?")
            args.append(kind)
        if favorites_only:
            conditions.append("t.favorite = 1")
        if conditions:
            sql += "WHERE " + " AND ".join(conditions) + " "
        sql += "GROUP BY t.id ORDER BY shown COLLATE NOCASE"
        return self.conn.execute(sql, tuple(args)).fetchall()

    def set_favorite(self, title_id: int, favorite: bool) -> bool:
        with self.conn:
            cur = self.conn.execute(
                "UPDATE titles SET favorite = ? WHERE id = ?", (1 if favorite else 0, title_id)
            )
        return cur.rowcount > 0

    def set_display_name(self, title_id: int, new_name: str | None) -> bool:
        with self.conn:
            cur = self.conn.execute(
                "UPDATE titles SET display_name = ? WHERE id = ?", (new_name, title_id)
            )
        return cur.rowcount > 0

    def set_cover(self, title_id: int, cover_path: str | None) -> bool:
        with self.conn:
            cur = self.conn.execute(
                "UPDATE titles SET cover_path = ? WHERE id = ?", (cover_path, title_id)
            )
        return cur.rowcount > 0

    def save_credits_start(self, path: str, position: float) -> None:
        """Guarda o ponto dos créditos finais para este episódio."""
        with self.conn:
            self.conn.execute("UPDATE files SET credits_start = ? WHERE path = ?", (max(0.0, float(position)), path))

    def subtitle_delay_for(self, path: str) -> float:
        """Devolve o atraso de legendas guardado para este ficheiro."""
        row = self.conn.execute(
            "SELECT subtitle_delay FROM files WHERE path = ?", (path,)
        ).fetchone()
        return float(row["subtitle_delay"]) if row else 0.0

    def save_subtitle_delay(self, path: str, seconds: float) -> None:
        """Guarda o atraso de legendas por filme/episódio, limitado a ±10 s."""
        value = max(-10.0, min(10.0, float(seconds)))
        with self.conn:
            self.conn.execute(
                "UPDATE files SET subtitle_delay = ? WHERE path = ?", (value, path)
            )

    def save_progress(self, path: str, position: float, duration: float) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO progress (file_id, position, duration, updated_at) "
                "SELECT id, ?, ?, datetime('now') FROM files WHERE path = ? "
                "ON CONFLICT(file_id) DO UPDATE SET position = excluded.position, "
                "duration = excluded.duration, updated_at = excluded.updated_at",
                (position, duration, path),
            )

    def resume_plan(self, title_id: int) -> Resume | None:
        """Decide o que tocar e de onde, olhando o último arquivo que você mexeu."""
        rows = self.conn.execute(
            "SELECT f.path, f.season, f.episode, p.position, p.duration, p.updated_at "
            "FROM files f LEFT JOIN progress p ON p.file_id = f.id "
            "WHERE f.title_id = ? "
            "ORDER BY COALESCE(f.season, 0), COALESCE(f.episode, 0), f.path",
            (title_id,),
        ).fetchall()
        if not rows:
            return None

        # Último arquivo assistido (empate = o mais adiante na ordem).
        last, last_time = None, ""
        for i, r in enumerate(rows):
            if r["updated_at"] and r["updated_at"] >= last_time:
                last, last_time = i, r["updated_at"]

        index, start, completed = 0, 0.0, False
        if last is not None:
            r = rows[last]
            finished = r["duration"] and r["position"] / r["duration"] >= FINISHED_FRACTION
            if not finished:
                index = last
                start = r["position"] if r["position"] >= MIN_RESUME_SECONDS else 0.0
            elif last + 1 < len(rows):
                index = last + 1          # terminou este: vai para o próximo
            else:
                completed = True          # terminou o último: começa de novo do 1º

        chosen = rows[index]
        return Resume(
            paths=[r["path"] for r in rows[index:]],
            start=start,
            duration=chosen["duration"] if start > 0 else None,
            season=chosen["season"],
            episode=chosen["episode"],
            completed=completed,
        )

    def continue_watching(self) -> list[ContinueItem]:
        """Títulos começados e não terminados, do mais recente para o mais antigo.

        Carrega os títulos e os episódios relevantes em duas consultas, evitando
        consultas individuais por título (padrão N+1) durante a construção do Início.
        """
        titles = {t["id"]: t for t in self.list_titles()}
        rows = self.conn.execute(
            "SELECT f.title_id, f.path, f.season, f.episode, "
            "p.position, p.duration, p.updated_at "
            "FROM files f LEFT JOIN progress p ON p.file_id = f.id "
            "WHERE f.title_id IN ("
            "  SELECT DISTINCT pf.title_id FROM files pf "
            "  JOIN progress pp ON pp.file_id = pf.id"
            ") "
            "ORDER BY f.title_id, COALESCE(f.season, 0), "
            "COALESCE(f.episode, 0), f.path"
        ).fetchall()

        grouped: dict[int, list[sqlite3.Row]] = {}
        for row in rows:
            grouped.setdefault(row["title_id"], []).append(row)

        items = []
        for title_id, episodes in grouped.items():
            title = titles.get(title_id)
            if title is None:
                continue

            # Mantém a regra de resume_plan(): em empate de horário,
            # vence o episódio que aparece mais adiante na ordem.
            last_index, last_time = None, ""
            has_started = False
            for index, episode in enumerate(episodes):
                position = episode["position"]
                duration = episode["duration"]
                finished = bool(duration and position is not None
                                and position / duration >= FINISHED_FRACTION)
                if episode["updated_at"] and episode["updated_at"] >= last_time:
                    last_index, last_time = index, episode["updated_at"]
                if finished or (position or 0) >= MIN_RESUME_SECONDS:
                    has_started = True

            if last_index is None or not has_started:
                continue

            last = episodes[last_index]
            duration = last["duration"]
            position = last["position"] or 0.0
            finished = bool(duration and position / duration >= FINISHED_FRACTION)

            if not finished:
                chosen_index = last_index
                start = position if position >= MIN_RESUME_SECONDS else 0.0
            elif last_index + 1 < len(episodes):
                chosen_index = last_index + 1
                start = 0.0
            else:
                continue                         # toda a série/filme já foi concluída

            chosen = episodes[chosen_index]
            fraction = start / chosen["duration"] if start > 0 and chosen["duration"] else 0.0
            items.append(ContinueItem(
                title, chosen["season"], chosen["episode"], min(fraction, 1.0)
            ))

        # A página Início continua ordenada pelo progresso mais recente.
        items.sort(
            key=lambda item: (
                max(
                    (row["updated_at"] or "" for row in grouped[item.title["id"]]),
                    default=""
                ),
                item.title["id"],
            ),
            reverse=True,
        )
        return items

    def episodes_for_title(self, title_id: int) -> list[Episode]:
        """Todos os arquivos do título, em ordem de temporada/episódio, com o progresso."""
        rows = self.conn.execute(
            "SELECT f.path, f.season, f.episode, p.position, p.duration, f.credits_start "
            "FROM files f LEFT JOIN progress p ON p.file_id = f.id "
            "WHERE f.title_id = ? "
            "ORDER BY COALESCE(f.season, 0), COALESCE(f.episode, 0), f.path",
            (title_id,),
        ).fetchall()
        return [Episode(r["path"], r["season"], r["episode"], r["position"], r["duration"], r["credits_start"])
                for r in rows]

    def all_file_paths(self) -> list[str]:
        """Devolve os caminhos da biblioteca para operações de diagnóstico."""
        return [row[0] for row in self.conn.execute("SELECT path FROM files ORDER BY path")]

    def files_for_title(self, title_id: int) -> list[str]:
        """Caminhos dos vídeos de um título, na ordem de temporada/episódio."""
        rows = self.conn.execute(
            "SELECT path FROM files WHERE title_id = ? "
            "ORDER BY COALESCE(season, 0), COALESCE(episode, 0), path",
            (title_id,),
        ).fetchall()
        return [r["path"] for r in rows]