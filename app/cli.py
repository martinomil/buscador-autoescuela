"""CLI de administracion del proyecto.

Uso:
    python -m app.cli init-db
    python -m app.cli import-csv ruta/al/fichero.csv
    python -m app.cli list [--status ESTADO]

    # Fase 2: Gmail
    python -m app.cli gmail-auth
    python -m app.cli send-preview [--status ESTADO | --ids 1,2,3]
    python -m app.cli send-test --autoescuela-id ID [--to email@ejemplo.com] [--force]
    python -m app.cli send-batch [--status ESTADO | --ids 1,2,3] [--confirm]
"""
from __future__ import annotations

import argparse
import logging
import sys

from app import config
from app.db import get_session, init_db
from app.email_sender import (
    DuplicateEmailError,
    has_sent_kind,
    preview_initial_email,
    send_batch,
    send_initial_email,
)
from app.importer import import_csv
from app.logging_setup import setup_logging
from app.repository import count_by_status, get_autoescuela, list_autoescuelas

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


def _select_autoescuelas(session, args: argparse.Namespace) -> list:
    if getattr(args, "ids", None):
        ids = [int(x) for x in args.ids.split(",") if x.strip()]
        selected = [get_autoescuela(session, i) for i in ids]
        missing = [i for i, a in zip(ids, selected) if a is None]
        if missing:
            print(f"Aviso: no existen autoescuelas con id {missing}, se ignoran.")
        return [a for a in selected if a is not None]
    return list_autoescuelas(session, status=args.status)


def cmd_gmail_auth(_args: argparse.Namespace) -> None:
    from app.gmail_client import get_gmail_service, get_own_email_address

    service = get_gmail_service()
    email_addr = get_own_email_address(service)
    print(f"Autenticacion correcta. Cuenta de Gmail conectada: {email_addr}")


def cmd_send_preview(args: argparse.Namespace) -> None:
    with get_session() as session:
        autoescuelas = _select_autoescuelas(session, args)
        if not autoescuelas:
            print("No hay autoescuelas que coincidan con el filtro.")
            return

        for a in autoescuelas:
            preview = preview_initial_email(a)
            already_sent = has_sent_kind(session, a.id, "initial")
            print("=" * 72)
            print(f"Autoescuela: {a.name} (id={a.id}, estado={a.status})")
            print(f"Para: {preview['to']}")
            if already_sent:
                print("[AVISO] Ya se envio un email inicial a esta autoescuela anteriormente.")
            print(f"Asunto: {preview['subject']}")
            print("-" * 72)
            print(preview["body"])

        print("=" * 72)
        print(
            f"MODO PREVIEW: no se ha enviado nada. {len(autoescuelas)} email(s) "
            "se enviarian con 'send-test' (uno) o 'send-batch --confirm' (varios)."
        )


def cmd_send_test(args: argparse.Namespace) -> None:
    from app.gmail_client import get_gmail_service

    to = args.to or config.GMAIL_USER_EMAIL
    if not to:
        print("Indica --to o configura GMAIL_USER_EMAIL en .env")
        return

    with get_session() as session:
        autoescuela = get_autoescuela(session, args.autoescuela_id)
        if autoescuela is None:
            print(f"No existe ninguna autoescuela con id={args.autoescuela_id}")
            return

        preview = preview_initial_email(autoescuela)
        print(f"Enviando email de PRUEBA (contenido real de '{autoescuela.name}') a: {to}")
        print(f"Asunto: {preview['subject']}")

        service = get_gmail_service()
        try:
            email_message = send_initial_email(
                session, service, autoescuela, override_to=to, force=args.force
            )
        except DuplicateEmailError as exc:
            print(f"[BLOQUEADO] {exc}")
            print("Usa --force si realmente quieres volver a enviarlo.")
            return

        print(
            f"Email enviado y registrado correctamente: EmailMessage id={email_message.id}, "
            f"gmail_message_id={email_message.gmail_message_id}, thread_id={email_message.gmail_thread_id}"
        )


def cmd_send_batch(args: argparse.Namespace) -> None:
    with get_session() as session:
        autoescuelas = _select_autoescuelas(session, args)
        if not autoescuelas:
            print("No hay autoescuelas que coincidan con el filtro.")
            return

        pending = [a for a in autoescuelas if not has_sent_kind(session, a.id, "initial")]
        already = [a for a in autoescuelas if a not in pending]

        print(
            f"Seleccionadas: {len(autoescuelas)} | Pendientes de primer envio: {len(pending)} "
            f"| Ya contactadas (se omiten): {len(already)}"
        )
        for a in already:
            print(f"  - omitida (ya contactada): {a.name}")

        if not args.confirm:
            print()
            print("MODO SIMULACION: no se ha enviado nada. Anade --confirm para enviar de verdad.")
            for a in pending:
                print(f"  - se enviaria a: {a.name} <{a.email}>")
            return

        if not pending:
            print("No hay autoescuelas pendientes de contactar.")
            return

        from app.gmail_client import get_gmail_service

        service = get_gmail_service()
        result = send_batch(session, service, pending)
        print(
            f"Enviados: {len(result['sent'])} | Omitidos (duplicado): {len(result['skipped'])} "
            f"| Errores: {len(result['errors'])}"
        )
        for a in result["errors"]:
            print(f"  - ERROR enviando a: {a.name} <{a.email}> (ver logs arriba)")


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

    p_auth = subparsers.add_parser("gmail-auth", help="Autentica con Gmail (OAuth) y prueba la conexion")
    p_auth.set_defaults(func=cmd_gmail_auth)

    p_preview = subparsers.add_parser(
        "send-preview", help="Muestra que se enviaria, sin enviar nada (dry-run)"
    )
    p_preview.add_argument("--status", default=None, help="Filtra por estado")
    p_preview.add_argument("--ids", default=None, help="Lista de ids separados por comas")
    p_preview.set_defaults(func=cmd_send_preview)

    p_test = subparsers.add_parser(
        "send-test", help="Envia UN email real de prueba (a tu propia cuenta por defecto)"
    )
    p_test.add_argument("--autoescuela-id", type=int, required=True, help="Id de la autoescuela a usar como contenido")
    p_test.add_argument("--to", default=None, help="Direccion de destino real (por defecto GMAIL_USER_EMAIL)")
    p_test.add_argument("--force", action="store_true", help="Ignora la proteccion anti-duplicados")
    p_test.set_defaults(func=cmd_send_test)

    p_batch = subparsers.add_parser(
        "send-batch", help="Envia el email inicial a varias autoescuelas (requiere --confirm)"
    )
    p_batch.add_argument("--status", default="not_contacted", help="Filtra por estado (default: not_contacted)")
    p_batch.add_argument("--ids", default=None, help="Lista de ids separados por comas (ignora --status)")
    p_batch.add_argument("--confirm", action="store_true", help="Envia de verdad (si no, solo simula)")
    p_batch.set_defaults(func=cmd_send_batch)

    return parser


def main(argv: list[str] | None = None) -> int:
    # En Windows la consola no siempre usa UTF-8 por defecto, lo que
    # desfigura tildes/enes al imprimir el contenido de los emails.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    setup_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[ERROR] {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
