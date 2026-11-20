"""Kommandorad för drift: `python -m biljettbot.cli import|export ...`.

I vardagen sköts allt via Slack; detta är för första uppsättning och felsökning.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from . import csvio
from .config import Config
from .db import Database


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="biljettbot.cli")
    sub = p.add_subparsers(dest="kommando", required=True)
    pi = sub.add_parser("import", help="Importera CSV till en tom databas")
    pi.add_argument("csv", nargs="?")
    pe = sub.add_parser("export", help="Exportera databasen till CSV")
    pe.add_argument("utfil", nargs="?", help="Fil att skriva (standard: stdout)")
    pe.add_argument("--utokad", action="store_true", help="Ta med kolumnerna serie och kalla_id")
    args = p.parse_args(argv)

    cfg = Config.fran_miljo()
    db = Database(cfg.databas)
    nu = datetime.now(cfg.tidszon)
    if args.kommando == "import":
        text = Path(args.csv or cfg.csv_import).read_text(encoding="utf-8-sig")
        n = csvio.importera(db, text, nu, cfg.arena)
        print(f"Importerade {n} matcher till {cfg.databas}")
    elif args.kommando == "export":
        data = csvio.exportera(db, utokad=args.utokad)
        if args.utfil:
            Path(args.utfil).write_text(data, encoding="utf-8", newline="")
            print(f"Skrev {args.utfil}")
        else:
            sys.stdout.write(data.lstrip("﻿"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
