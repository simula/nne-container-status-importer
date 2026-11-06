"""Command line of the batch `container-status-importer`: arguments, logging, run, failure email."""

import argparse
import logging
import sys
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import List, Optional

# Daily log files kept in --log-dir (rotation at midnight)
LOG_DAYS: int = 30


def setup_logging(log_dir: Optional[Path]) -> None:
    """Sends all log records, those of nnedbtools included, to `<log_dir>/importer.log` or to stderr

    Must run before nnedbtools is imported: the library sets up its own log file (`logs/robustnet<date-time>.log` in the
    working directory, one per run) with `logging.basicConfig`, which does nothing once the root logger has a handler.

    Args:
        log_dir (Optional[Path]): log directory; the file starts anew every midnight, LOG_DAYS files are kept. None:
            log to stderr
    """
    handler: logging.Handler = (
        TimedRotatingFileHandler(log_dir / "importer.log", when="midnight", backupCount=LOG_DAYS, encoding="utf-8")
        if log_dir
        else logging.StreamHandler(sys.stderr)
    )
    logging.basicConfig(
        handlers=[handler], format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO, force=True
    )


def main(argv: Optional[List[str]] = None) -> int:
    """Batch entry point `container-status-importer`

    Args:
        argv (Optional[List[str]], optional): command line arguments. Defaults to None (sys.argv).

    Returns:
        int: exit code 0; a failed run raises (after the failure email with `--notify`) before any file is deleted
    """
    # 1. Arguments
    parser = argparse.ArgumentParser(description="Import NNE container heartbeat files into container_rawdata")
    parser.add_argument("--data-dir", type=Path, default=Path("/nne/data"), help="node data (default /nne/data)")
    parser.add_argument(
        "--write",
        type=Path,
        metavar="DB_ENV",
        help="write into container_rawdata with the database settings of this env file and delete the GOOD files "
        "(default: dry run, nothing written or deleted)",
    )
    parser.add_argument(
        "--notify",
        nargs="+",
        metavar="EMAIL",
        help="email addresses informed when the run fails (SMTP settings from the --write env file)",
    )
    parser.add_argument(
        "--bad-dir",
        type=Path,
        help="with --write move the BAD files here, same path as in the inbox (default: they stay in the inbox)",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        help=f"write the log to LOG_DIR/importer.log, rotated at midnight, {LOG_DAYS} days kept (default: stderr)",
    )
    args = parser.parse_args(argv)
    if args.notify and args.write is None:
        parser.error("--notify needs --write: the SMTP settings are read from its env file")

    # 2. Logging before nnedbtools is imported (see setup_logging)
    setup_logging(args.log_dir)
    from container_status_importer.importer import run
    from container_status_importer.notification import send_failure_email

    # 3. Run, a failure is emailed and raised again
    started: datetime = datetime.now()
    logging.info(
        f"-- Run started: data dir {args.data_dir}, write {args.write}, bad dir {args.bad_dir}, notify {args.notify}"
    )
    try:
        exit_code: int = run(data_dir=args.data_dir, write=args.write, bad_dir=args.bad_dir)
    except Exception as error:
        logging.exception(f"-- Run failed: {error}")
        if args.notify:
            send_failure_email(env_file=args.write, recipients=args.notify, started=started, error=error)
        raise
    logging.info(f"-- Run finished: {datetime.now() - started}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
