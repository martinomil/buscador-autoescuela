"""CLI de administracion del proyecto.

Uso:
    python -m app.cli init-db
    python -m app.cli import-csv ruta/al/fichero.csv
    python -m app.cli list [--status ESTADO]
"""
from __future__ import annotations

import argparse
import logging
import sys

from app.db import get_session, init_db
from app.importer import import_csv
from app.logging_setup import setup_logging
from app.repository import count_by_status, list_autoescuelas

logger = logging.getLogger(__name__)


def cmd_init_db(_args: argparse.Namespace) -> None:
    init_db()
    logger.info("Base de datos inicializada")


def cmd_import_csv(args: argparse.Namespace) -> None:
    with get_session() as session:
        summary = import_csv(session, args.csv_path)
    print(
        f"Creadas: {summary.created} | Duplicadas: {summary.skipped_duplicate} | "
        f"Invalidas: {summary.skipped_invalid} | Total filas: {summary.total_rows}"
    )


def cmd_list(args: argparse.Namespace) -> None:
    with get_session() as session:
        autoescuelas = list_autoescuelas(session, status=args.status)
        if not autoescuelas:
            print("No hay autoescuelas registradas.")
            return
        for a in autoescuelas:
            print(f"[{a.id:>3}] {a.name:<35} {a.city or '-':<20} {a.email:<30} {a.status}")
        print()
        counts = count_by_status(session)
        print("Resumen por estado:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gestion de busqueda de autoescuela")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_init = subparsers.add_parser("init-db", help="Crea las tablas de la base de datos")
    p_init.set_defaults(func=cmd_init_db)

    p_import = subparsers.add_parser("import-csv", help="Importa autoescuelas desde un CSV")
    p_import.add_argument("csv_path", help="Ruta al fichero CSV")
    p_import.set_defaults(func=cmd_import_csv)

    p_list = subparsers.add_parser("list", help="Lista las autoescuelas registradas")
    p_list.add_argument("--status", default=None, help="Filtra por estado")
    p_list.set_defaults(func=cmd_list)

    return parser


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
