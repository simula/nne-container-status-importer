"""Import of NNE container heartbeat files into the report table `container_rawdata` (hourly batch).

Heartbeat files: <data-dir>/<node>/heartbeats/<YYYY-MM-DD>/heartbeat_<container>_<YYYYMMDDTHHMMSS>.json.xz, times in
UTC. Files are selected by the timestamp in their name. Writing is an upsert (`update_heartbeat_status`), so a file
processed again does no harm, and no file is moved.
"""

import argparse
import json
import logging
import lzma
import re
import traceback
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from math import floor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import nnedbtools.connection as con
import nnedbtools.tables as tb
from nnedbtools.enums import DockerContainer
from nnedbtools.identityprovider import load_from_enviroment
from nnedbtools.utils import dt_functions
from sqlalchemy.dialects.mysql import insert

FILE_NAME = re.compile(r"heartbeat_(?P<container>[a-z]+)_(?P<ts>\d{8}T\d{6})\.json\.xz")


@dataclass
class HeartbeatUpdate:
    """
    Heartbeat Update Dataclass wrapper: one row of `container_rawdata`
    """

    # Node identifier
    node_id: int
    # Start timestamp of the container
    start_ts: datetime
    # Container enumeration
    container: DockerContainer
    # Container version (build name/tag)
    version: str
    # Running process name
    process: str
    # Last seen timestamp
    seen_ts: datetime
    # Uptime in seconds
    uptime: float
    # Extra information
    extra: Optional[str]


class HeartbeatFileStatus(Enum):
    WORK = 0
    GOOD = 1
    BAD = 2
    LEAVE = 3


@dataclass
class HeartbeatRecord(ABC):
    version: int

    @abstractmethod
    def _updates(self) -> List[Optional[HeartbeatUpdate]]:
        pass

    @classmethod
    @abstractmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HeartbeatRecord":
        pass

    def updates(self) -> List[HeartbeatUpdate]:
        return [one_update for one_update in self._updates() if one_update is not None]

    def process_update(
        self,
        node_id: int,
        start_ts: datetime,
        container: str,
        version: str,
        thread: str,
        seen_ts: datetime,
        uptime: float,
        sequence: int,
    ) -> Optional[HeartbeatUpdate]:
        process: Optional[str] = None
        extra: Optional[str] = None

        try:
            container: DockerContainer = DockerContainer(container)
        except ValueError:
            raise ValueError(f"Unsupported container for monitoring {container}")

        if container == DockerContainer.METADATA:
            if thread == "MetaColect":
                process = "metadata"
            elif thread == "HomeKeep":
                process = "homekeeping"
            # This is homekeeping fallback to mainthread
            elif thread == "MainThread":
                process = "homekeeping"
            else:
                return None
        elif container == DockerContainer.UDPPING:
            if thread == "MainThread":
                process = "container"
            elif thread.startswith("UdpSend"):
                measurement_instance: str = thread.removeprefix("UdpSend")
                process = f"sender-{measurement_instance}"
            elif thread.startswith("UdpRecv"):
                instance_thread: List[str] = thread.removeprefix("UdpRecv").split("-")
                measurement_instance: str = instance_thread[0]
                process = f"receiver-{measurement_instance}"
                extra = instance_thread[1]
            else:
                return None
        elif container == DockerContainer.SPEEDTEST:
            if thread == "MainThread":
                process = "container"
            else:
                return None
        elif container == DockerContainer.MQTT:
            if thread == "MainThread":
                process = "container"
            elif thread == "MqttClient":
                process = "mqtt"
            else:
                return None
        elif container == DockerContainer.NETPERFMETER:
            if thread == "MainThread":
                process = "container"
            else:
                return None
        elif container == DockerContainer.TRACEROUTE:
            if thread == "MainThread":
                process = "container"
            else:
                return None
        uptime_whole_hours: float = floor((seen_ts - start_ts).total_seconds() / 3600)

        if uptime_whole_hours >= sequence:
            return None

        return HeartbeatUpdate(
            node_id=node_id,
            start_ts=start_ts,
            container=container,
            version=version,
            process=process,
            seen_ts=seen_ts,
            uptime=uptime,
            extra=extra,
        )


@dataclass
class HeartbeatRecordv1(HeartbeatRecord):
    node_id: int
    start_ts: datetime
    seen_ts: datetime
    container_name: str
    container_version: str
    uptime: float
    threads: List[str]
    sequence: int

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HeartbeatRecordv1":
        return cls(
            node_id=data["node_id"],
            start_ts=dt_functions.normalize_to_date_time_utc(data["start_ts"]),
            seen_ts=dt_functions.normalize_to_date_time_utc(data["seen_ts"]),
            container_name=data["container_name"],
            container_version=data["container_version"],
            uptime=float(data["uptime"]),
            threads=list(data.get("threads", [])),
            sequence=data["sequence"],
            version=data.get("version", 1),
        )

    def _updates(self) -> List[Optional[HeartbeatUpdate]]:
        return [
            self.process_update(
                node_id=self.node_id,
                start_ts=self.start_ts,
                container=self.container_name,
                version=self.container_version,
                thread=thread,
                seen_ts=self.seen_ts,
                uptime=self.uptime,
                sequence=self.sequence,
            )
            for thread in self.threads
        ]


# Record class per file format version
RECORD_VERSIONS: Dict[int, type] = {1: HeartbeatRecordv1}


def _log_failure(message: str, file_path: Path, error: Exception) -> None:
    logging.info(f"-- {message} {file_path}: {error}")
    for line in traceback.format_exc().splitlines():
        logging.debug(f"---- {line}")


def process_heartbeat_file(
    file_path: Path,
) -> Tuple[HeartbeatFileStatus, Optional[List[HeartbeatUpdate]]]:
    """Reads one heartbeat file: GOOD with its updates (possibly none), BAD when it cannot be read or has an
    unsupported version or container, LEAVE when the file is gone."""
    logging.debug(f"-- Processing {file_path}")

    if not file_path.is_file():
        return (HeartbeatFileStatus.LEAVE, None)

    try:
        with lzma.open(file_path, "rt", encoding="utf-8") as file_descriptor:
            data: Any = json.load(file_descriptor)
    except Exception as error:
        _log_failure("Failed to open", file_path, error)
        return (HeartbeatFileStatus.BAD, None)

    if not isinstance(data, dict):
        logging.info(f"-- Malformed json in file {file_path}")
        return (HeartbeatFileStatus.BAD, None)

    record_class: Optional[type] = RECORD_VERSIONS.get(data.get("version"))
    if record_class is None:
        logging.info(f"-- Missing or unsupported version {data.get('version')!r} in {file_path}")
        return (HeartbeatFileStatus.BAD, None)

    try:
        updates: List[HeartbeatUpdate] = record_class.from_dict(data).updates()
    except Exception as error:
        _log_failure("Failed to extract updates from", file_path, error)
        return (HeartbeatFileStatus.BAD, None)

    if not updates:
        logging.debug(f"-- No valid updates found in {file_path}")
    return (HeartbeatFileStatus.GOOD, updates)


def heartbeat_files(data_dir: Path, since: datetime) -> List[Path]:
    """Heartbeat files of all nodes whose name timestamp is at or after `since` (naive UTC), oldest first."""
    first_day: str = since.date().isoformat()
    selected: List[Tuple[datetime, Path]] = []
    for day_directory in data_dir.glob("*/heartbeats/*"):
        # Day directories are named YYYY-MM-DD: older days are skipped without listing them
        if day_directory.name < first_day:
            continue
        for file_path in day_directory.glob("heartbeat_*.json.xz"):
            match = FILE_NAME.fullmatch(file_path.name)
            if match:
                ts: datetime = datetime.strptime(match["ts"], "%Y%m%dT%H%M%S")
                if ts >= since:
                    selected.append((ts, file_path))
    return [file_path for _, file_path in sorted(selected)]


def process_heartbeat_files(
    file_paths: List[Path],
) -> Tuple[Counter, List[HeartbeatUpdate]]:
    """Processes the files in the given order (oldest first): counts the files per (status, container of the file
    name) and keeps the last update per primary key of `container_rawdata`."""
    file_counts: Counter = Counter()
    latest: Dict[tuple, HeartbeatUpdate] = {}
    for file_path in file_paths:
        status, updates = process_heartbeat_file(file_path=file_path)
        file_counts[(status.name, FILE_NAME.fullmatch(file_path.name)["container"])] += 1
        for update in updates or []:
            key: tuple = (update.node_id, update.start_ts, update.container, update.version, update.process)
            latest[key] = update
    return (file_counts, list(latest.values()))


def update_heartbeat_status(updates: List[HeartbeatUpdate]) -> None:
    """
    Updates records in table `container_rawdata` of the report database with data contained in heartbeat updates

    Args:
        updates (List[HeartbeatUpdate]): Listing of heartbeat updates parsed from files.


    SQL statement example:
    ```sql
        INSERT INTO container_rawdata (
            node_id, start_ts, container, version, process, seen_ts, uptime, extra
        )
        VALUES
            (8080, '2026-04-08 10:00:00', 'mqtt', 'locum-2-1-7', 'container', '2026-04-08 10:05:00', 12345, NULL)
        ON DUPLICATE KEY UPDATE
            seen_ts = VALUES(seen_ts),
            uptime  = VALUES(uptime),
            extra   = VALUES(extra);
    ```
    """

    # 1. Check applicability: at least one update record
    if len(updates) == 0:
        logging.info(f"-- No updates provided, there is no need to update {tb.ReportContainerRawdata.__tablename__}")
        return

    # 2. Create the table if it does not exist
    table = tb.ReportContainerRawdata
    table.__table__.create(bind=con.engines.report_engine, checkfirst=True)

    # 3. Insert all updates, ON DUPLICATE KEY UPDATE of the non-key values seen, uptime and extra
    with con.engines.report_session as session:
        insert_or_update_values = insert(table).values(
            [
                {
                    "node_id": u.node_id,
                    "start_ts": u.start_ts,
                    "container": u.container.value,
                    "version": u.version,
                    "process": u.process,
                    "seen_ts": u.seen_ts,
                    "uptime": u.uptime,
                    "extra": u.extra,
                }
                for u in updates
            ]
        )
        insert_or_update_values = insert_or_update_values.on_duplicate_key_update(
            seen_ts=insert_or_update_values.inserted.seen_ts,
            uptime=insert_or_update_values.inserted.uptime,
            extra=insert_or_update_values.inserted.extra,
        )
        session.execute(insert_or_update_values)
        session.commit()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Import NNE container heartbeat files into container_rawdata")
    parser.add_argument("--data-dir", type=Path, default=Path("/nne/data"), help="node data (default /nne/data)")
    window = parser.add_mutually_exclusive_group()
    window.add_argument("--hours", type=int, default=2, help="files of the last N hours (default 2)")
    window.add_argument("--since", type=datetime.fromisoformat, help="files from this UTC time, e.g. 2026-04-08")
    parser.add_argument(
        "--write",
        type=Path,
        metavar="DB_ENV",
        help="write into container_rawdata with the database settings of this env file (default: dry run)",
    )
    args = parser.parse_args(argv)

    since: datetime = args.since or datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=args.hours)
    file_counts, updates = process_heartbeat_files(heartbeat_files(data_dir=args.data_dir, since=since))

    print(f"Heartbeat files in {args.data_dir} since {since.isoformat()} UTC:")
    for (status, container), count in sorted(file_counts.items()):
        print(f"  {status:5} {container:13} {count}")
    process_counts: Counter = Counter((u.container.value, u.process.split("-")[0]) for u in updates)
    print(f"Updates, one per container_rawdata key: {len(updates)}")
    for (container, process), count in sorted(process_counts.items()):
        print(f"  {container:13} {process:12} {count}")

    if args.write is None:
        print("Dry run: nothing written")
        return 0
    load_from_enviroment(db_config_file=str(args.write))
    update_heartbeat_status(updates=updates)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
