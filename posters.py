"""Busca de capas SEM documento: OMDb (filmes) + TVmaze (series).

- OMDb: chave gratis pedida so com um email em omdbapi.com. Cobre filmes.
- TVmaze: nao precisa de chave nenhuma. Cobre so series/TV.

Este modulo substitui o tmdb.py e mantem as MESMAS funcoes e classes que o
app ja usava (search, pick_confident, download_poster, fetch_missing_covers).

So usa a biblioteca padrao. NAO mexe na interface nem no banco: so conversa com
a internet, por isso pode rodar numa thread.
"""
import json
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

OMDB_BASE = "https://www.omdbapi.com/"
TVMAZE_BASE = "https://api.tvmaze.com"
TIMEOUT = 10                  # segundos: nunca esperar a internet para sempre
MAX_IMAGE_BYTES = 5_000_000
# Os links de imagem vem do servidor, entao so baixamos de lugares conhecidos:
OMDB_IMAGE_HOSTS = ("m.media-amazon.com", "ia.media-imdb.com")
TVMAZE_HOST = "tvmaze.com"
ATTRIBUTION = "Capas de filmes: OMDb (omdbapi.com). Capas de s\u00e9ries: TVmaze (tvmaze.com)."


class PosterError(Exception):
    """Falha com mensagem pronta para mostrar ao usuario."""


class PosterAuthError(PosterError):
    """Chave do OMDb recusada."""


class PosterNetworkError(PosterError):
    """Sem internet, timeout ou servidor fora do ar."""


@dataclass
class Candidate:
    source: str                  # "omdb" ou "tvmaze"
    ref: str                     # imdbID ou id do TVmaze (so para identificar)
    title: str
    original_title: str
    year: int | None
    overview: str
    poster_url: str | None       # URL completa da imagem
    popularity: float


@dataclass
class BatchResult:
    found: list = field(default_factory=list)      # (id do titulo, arquivo, descricao)
    unsure: list = field(default_factory=list)     # achou opcoes, mas sem certeza
    notfound: list = field(default_factory=list)   # nada com capa
    error: str | None = None                       # parou no meio por este motivo
    auth_failed: bool = False


def _get(url: str, limit: int | None = None) -> bytes:
    # A chave do OMDb vai dentro da propria URL (e assim que a API funciona),
    # por isso nenhum erro abaixo repassa a URL: `from None` esconde a origem.
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "Cine Prime/0.1"}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as resp:
            return resp.read(limit) if limit else resp.read()
    except urllib.error.HTTPError as err:               # antes de URLError: e uma subclasse
        if err.code == 401:
            raise PosterAuthError("O OMDb recusou a chave. Confira se copiou inteira.") from None
        if err.code == 429:
            raise PosterError("Muitas requisi\u00e7\u00f5es. Tente de novo em instantes.") from None
        raise PosterError(f"O servidor respondeu com erro {err.code}.") from None
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        raise PosterNetworkError("N\u00e3o consegui falar com o servidor. Verifique a internet.") from None


def normalize(text: str) -> str:
    """'Homem-Aranha: Um Novo Dia' -> 'homem aranha um novo dia' (sem acento/pontuacao)."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", plain.lower()).strip()


def _year(text) -> int | None:
    m = re.match(r"(\d{4})", str(text or ""))
    return int(m.group(1)) if m else None


def _strip_html(text: str) -> str:
    """O TVmaze envia a sinopse com tags (<p>, <b>...). Tiramos tudo."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()


def search_movies(query: str, key: str) -> list:
    params = urllib.parse.urlencode({"s": query, "type": "movie", "apikey": key})
    try:
        data = json.loads(_get(f"{OMDB_BASE}?{params}"))
    except ValueError:
        raise PosterError("Resposta inesperada do OMDb.") from None
    if not isinstance(data, dict):
        raise PosterError("Resposta inesperada do OMDb.")
    if data.get("Response") == "False":
        error = str(data.get("Error", ""))
        if "api key" in error.lower():            # "Invalid API key!" / "No API key provided."
            raise PosterAuthError("O OMDb recusou a chave. Confira se copiou inteira.")
        if "limit" in error.lower():              # "Request limit reached!"
            raise PosterError("Limite di\u00e1rio do OMDb atingido. Tente de novo amanh\u00e3.")
        return []                                 # "Movie not found!" e similares
    out = []
    for item in data.get("Search") or []:
        try:
            poster = item.get("Poster")
            out.append(Candidate(
                source="omdb", ref=str(item["imdbID"]), title=str(item["Title"]),
                original_title=str(item["Title"]), year=_year(item.get("Year")),
                overview="", poster_url=poster if poster and poster != "N/A" else None,
                popularity=0.0,
            ))
        except (KeyError, TypeError, AttributeError):
            continue                                  # item malformado: ignora so ele
    return out


def search_series(query: str) -> list:
    params = urllib.parse.urlencode({"q": query})
    try:
        data = json.loads(_get(f"{TVMAZE_BASE}/search/shows?{params}"))
    except ValueError:
        raise PosterError("Resposta inesperada do TVmaze.") from None
    if not isinstance(data, list):
        raise PosterError("Resposta inesperada do TVmaze.")
    out = []
    for entry in data:
        try:
            show = entry["show"]
            image = show.get("image") or {}
            out.append(Candidate(
                source="tvmaze", ref=str(show["id"]), title=str(show["name"]),
                original_title=str(show["name"]), year=_year(show.get("premiered")),
                overview=_strip_html(show.get("summary")),
                poster_url=image.get("original") or image.get("medium"),
                popularity=float(entry.get("score") or 0),
            ))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return out


def search(kind: str, query: str, key: str | None) -> list:
    """kind: "movie" (OMDb, precisa de chave) ou "series" (TVmaze, sem chave)."""
    if kind == "movie":
        if not key:
            raise PosterAuthError("Falta a chave do OMDb.")
        return search_movies(query, key)
    return search_series(query)


def pick_confident(candidates: list, title: str, year: int | None):
    """Escolhe um resultado SO se houver certeza. Na duvida, devolve None.

    - o titulo precisa bater exatamente, sem acento/pontuacao;
    - se sabemos o ano, o resultado precisa ter ano igual ou +-1;
    - sem ano e com mais de um candidato: nao chutamos;
    - com ano e mais de um candidato: so se um tiver pontuacao MUITO maior (2x).
    """
    wanted = normalize(title)
    if not wanted:
        return None
    pool = [c for c in candidates
            if c.poster_url and wanted in (normalize(c.title), normalize(c.original_title))]
    if year:
        pool = [c for c in pool if c.year and abs(c.year - year) <= 1]
    if not pool:
        return None
    if len(pool) == 1:
        return pool[0]
    if not year:
        return None
    pool.sort(key=lambda c: c.popularity, reverse=True)
    return pool[0] if pool[0].popularity >= 2 * pool[1].popularity else None


def _image_allowed(url: str) -> bool:
    """A URL da capa vem do servidor: so aceitamos https e hosts conhecidos."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https":
        return False
    return host in OMDB_IMAGE_HOSTS or host == TVMAZE_HOST or host.endswith("." + TVMAZE_HOST)


def download_poster(poster_url: str, dest_dir, name: str) -> Path:
    """Baixa a capa para `dest_dir/name.ext` e devolve o caminho."""
    if not poster_url or not _image_allowed(poster_url):
        raise PosterError("O servidor enviou um endere\u00e7o de imagem inesperado.")
    data = _get(poster_url, limit=MAX_IMAGE_BYTES + 1)
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise PosterError("A imagem baixada \u00e9 inv\u00e1lida.")
    # Extensao decidida pelo CONTEUDO (os primeiros bytes), nao pelo nome do link
    if data.startswith(b"\x89PNG"):
        ext = ".png"
    elif data.startswith(b"\xff\xd8"):
        ext = ".jpg"
    else:
        raise PosterError("A imagem baixada n\u00e3o \u00e9 PNG nem JPG.")
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", name)       # o nome pode vir do servidor
    dest = Path(dest_dir) / f"{safe}{ext}"
    dest.write_bytes(data)
    return dest


def fetch_missing_covers(todo, key: str | None, dest_dir) -> BatchResult:
    """todo: lista de (id_do_titulo, tipo, nome, ano). Baixa so as capas com certeza."""
    result = BatchResult()
    for title_id, kind, name, year in todo:
        try:
            candidates = search(kind, name, key)
            chosen = pick_confident(candidates, name, year)
            if chosen is None:
                has_poster = any(c.poster_url for c in candidates)
                (result.unsure if has_poster else result.notfound).append(name)
                continue
            path = download_poster(chosen.poster_url, dest_dir, str(title_id))
            label = f"{chosen.title} ({chosen.year})" if chosen.year else chosen.title
            result.found.append((title_id, path, label))
        except PosterAuthError as err:      # chave ruim: inutil continuar
            result.error, result.auth_failed = str(err), True
            break
        except PosterNetworkError as err:   # sem internet: idem
            result.error = str(err)
            break
        except PosterError:                 # falha isolada: segue para o proximo titulo
            result.notfound.append(name)
    return result