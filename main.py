#!/usr/bin/env python3
"""Interface de terminal do Cine Prime (temporária, até a janela GTK4 existir).

Uso:
  ./main.py scan ~/Videos [--kind auto|movie|series]
  ./main.py list [--kind movie|series]
  ./main.py rename 3 "John Wick 1"
  ./main.py cover 3 ~/Imagens/johnwick.jpg
"""
import argparse
import sys
from pathlib import Path

import covers
from database import Database
from scanner import scan_folders

KIND_LABEL = {"movie": "Filme", "series": "Série"}


def cmd_scan(db: Database, folder: str, kind: str) -> int:
    root = Path(folder).expanduser().resolve()
    if not root.is_dir():
        # Proteção: se o HD externo estiver desmontado, NÃO apagamos a biblioteca
        print(f"Erro: '{root}' não existe ou não é uma pasta.")
        return 1
    result = scan_folders(
        [(root, None if kind == "auto" else kind)],
        db.known_fingerprints(), db.known_media_signatures(), validate_media=True,
    )
    stats = db.save_scan(result.found, result.prune_roots)
    for cover in stats.orphan_covers:
        covers.remove_if_managed(cover)
    print(f"{len(result.found)} vídeos: {stats.added} novos, "
          f"{stats.moved} renomeados/movidos, {stats.removed} removidos.")
    if result.empty:
        print("Aviso: nenhum vídeo encontrado; mantive o que já estava registrado.")
    return 0


def cmd_list(db: Database, kind: str | None) -> int:
    rows = db.list_titles(kind)
    if not rows:
        print("Biblioteca vazia. Rode: ./main.py scan <pasta>")
    for r in rows:
        year = f" ({r['year']})" if r["year"] else ""
        cover = "capa ✓" if r["cover_path"] else "sem capa"
        print(f"[{r['id']:>3}] {KIND_LABEL.get(r['kind'], r['kind']):<8} {r['shown']}{year}"
              f"  - {r['n_files']} arquivo(s), {cover}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="cineprime")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("scan")
    p.add_argument("folder")
    p.add_argument("--kind", choices=["auto", "movie", "series"], default="auto")
    p = sub.add_parser("list")
    p.add_argument("--kind", choices=KIND_LABEL)
    p = sub.add_parser("rename")
    p.add_argument("id", type=int)
    p.add_argument("name")
    p = sub.add_parser("cover")
    p.add_argument("id", type=int)
    p.add_argument("image")
    args = ap.parse_args()

    db = Database()
    if args.cmd == "scan":
        return cmd_scan(db, args.folder, args.kind)
    if args.cmd == "list":
        return cmd_list(db, args.kind)
    if args.cmd == "rename":
        ok = db.set_display_name(args.id, args.name)
    else:
        img = Path(args.image).expanduser().resolve()
        if not img.is_file():
            print(f"Erro: imagem '{img}' não encontrada.")
            return 1
        ok = db.set_cover(args.id, str(img))
    print("Atualizado." if ok else f"Nenhum título com id {args.id}.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())