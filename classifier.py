"""Etapa 3: decidir se um arquivo é filme ou série e limpar o nome.

Regra principal: a PRIMEIRA subpasta dentro da sua pasta de vídeos define a
categoria (a menos que você tenha dito o tipo ao adicionar a pasta). Exemplo:

    Videos/
    ├── Filmes/John Wick (2014).mkv                    -> filme
    └── Series/Spider Noir/T1/Spider-Noir-EP01.mp4     -> série

Se o vídeo estiver fora dessas pastas, usamos um palpite: se o nome tem um
marcador de episódio (S01E02, 1x02, EP02) é série, senão é filme.
"""
import re
from dataclasses import dataclass
from pathlib import Path

CATEGORY_FOLDERS = {
    "filmes": "movie", "filme": "movie", "movies": "movie", "movie": "movie",
    "series": "series", "séries": "series", "serie": "series", "série": "series",
}

# Marcador completo (traz temporada E episódio): "S01E02", "s1e2", "S01 E02", "1x02"
EPISODE_RE = re.compile(r"[Ss](\d{1,2})[ ._-]?[Ee](\d{1,3})|\b(\d{1,2})x(\d{2})\b")
# Marcador só de episódio: "EP01", "Ep 3", "Episódio 5", "Capítulo 2".
# (?<![A-Za-z]) impede casar "ep" dentro de palavras como "Step 3".
EP_ONLY_RE = re.compile(
    r"(?<![A-Za-z])(?:ep|epis[oó]dio|cap[ií]tulo)[ ._-]?(\d{1,3})(?!\d)", re.IGNORECASE
)
# Pasta de temporada: "Temporada 2", "Season 02" (em qualquer parte do nome)...
SEASON_WORD_RE = re.compile(r"(?:temporada|season|temp)\D*(\d+)", re.IGNORECASE)
# ...ou abreviada, com o nome da pasta INTEIRO assim: "T1", "S02", "t 3"
SEASON_SHORT_RE = re.compile(r"[TtSs][ ._-]?(\d{1,2})")
PAREN_YEAR_RE = re.compile(r"\(((?:19|20)\d{2})\)")
DOT_YEAR_RE = re.compile(r"[ ._]((?:19|20)\d{2})(?=[ ._\]]|$)")


@dataclass
class Item:
    kind: str            # "movie" | "series"
    key: str             # identidade para agrupar arquivos novos (ex.: "movie:john wick:2014")
    title: str           # nome limpo, sugerido pelo app
    year: int | None
    season: int | None
    episode: int | None


def season_from_folder(name: str) -> int | None:
    m = SEASON_WORD_RE.search(name) or SEASON_SHORT_RE.fullmatch(name.strip())
    return int(m.group(1)) if m else None


def clean_title(text: str) -> tuple[str, int | None]:
    """'John.Wick.2014.1080p' -> ('John Wick', 2014)."""
    original = text
    year = None

    cuts = [m.start() for m in (EPISODE_RE.search(text), EP_ONLY_RE.search(text)) if m]
    if cuts:
        text = text[: min(cuts)]

    m = PAREN_YEAR_RE.search(text) or DOT_YEAR_RE.search(text)
    if m:
        year = int(m.group(1))
        text = text[: m.start()]

    text = re.sub(r"[._]", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -")
    return (text or original), year


def classify(path: Path, root: Path, forced_kind: str | None = None) -> Item:
    """forced_kind: "movie"/"series" quando o usuário disse o que a pasta contém."""
    parts = path.relative_to(root).parts
    kind = forced_kind

    if kind is None and len(parts) > 1 and parts[0].lower() in CATEGORY_FOLDERS:
        kind = CATEGORY_FOLDERS[parts[0].lower()]
        parts = parts[1:]
    in_category = kind is not None

    *folders, filename = parts
    stem = Path(filename).stem

    season = episode = None
    full = EPISODE_RE.search(stem)
    only = None if full else EP_ONLY_RE.search(stem)
    if full:
        season = int(full.group(1) or full.group(3))
        episode = int(full.group(2) or full.group(4))
    elif only:
        episode = int(only.group(1))
    marker = full or only                  # qualquer marcador de episódio no nome

    if kind is None:  # palpite quando não há pasta de categoria
        kind = "series" if marker else "movie"

    # Temporada pela pasta ("T1", "Temporada 2"), se o nome do arquivo não disse
    if season is None:
        for folder in folders:
            season = season_from_folder(folder)
            if season is not None:
                break
    # Série com episódio mas sem nenhuma pista de temporada: assume a 1ª
    if kind == "series" and episode is not None and season is None:
        season = 1

    # Dentro de uma pasta de categoria, a subpasta é o nome do filme/série.
    # Fora dela não sabemos o que a pasta significa, então confiamos no arquivo
    # (exceto série cujo arquivo é só "S01E01", sem nome antes do episódio).
    file_has_name = not marker or marker.start() > 0
    if folders and (in_category or not file_has_name):
        title_src = folders[0]
    else:
        title_src = stem                # arquivo solto: o nome do arquivo é o título

    title, year = clean_title(title_src)
    key = f"{kind}:{title.lower()}:{year or ''}"
    return Item(kind, key, title, year, season, episode)