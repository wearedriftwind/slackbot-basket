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
    pa = sub.add_parser("aterstall", help="Radera allt och importera startunderlaget på nytt")
    pa.add_argument("csv", nargs="?")
    pa.add_argument("--ja", action="store_true", help="Bekräfta (krävs)")
    args = p.parse_args(argv)

    cfg = Config.fran_miljo()
    db = Database(cfg.databas)
    nu = datetime.now(cfg.tidszon)
    if args.kommando == "import":
        text = Path(args.csv or cfg.csv_import).read_text(encoding="utf-8-sig")
        n = csvio.importera(db, text, nu, cfg.arena)
        print(f"Importerade {n} matcher till {cfg.databas}")
    elif args.kommando == "aterstall":
        if not args.ja:
            print("Raderar ALLA bokningar, besök, påminnelser och loggar. Kör igen med --ja för att bekräfta.")
            return 1
        backup = Path(cfg.databas).with_name(f"backup_fore_aterstallning_{nu:%Y%m%d_%H%M%S}.csv")
        backup.write_text(csvio.exportera(db, utokad=True), encoding="utf-8", newline="")
        text = Path(args.csv or cfg.csv_import).read_text(encoding="utf-8-sig")
        n = csvio.aterstall(db, text, nu, cfg.arena, "cli")
        print(f"Återställt: {n} matcher importerade. Backup av tidigare läge: {backup}")
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
