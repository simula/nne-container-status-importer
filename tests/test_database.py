"""Write of the heartbeat importer into the local test database (docker/db.compose.yaml, 127.0.0.1:23307).

Needs the local database running and `.db_env_test` (git-ignored, see documentation/DEVELOPMENT.md). Only rows of the
test node 99999 are written; they are removed before and after the test.
"""

from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import text

import nnedbtools.connection as con
import nnedbtools.tables as tb
from nnedbtools.enums import DockerContainer
from nnedbtools.identityprovider import load_from_enviroment

from container_status_importer.importer import HeartbeatUpdate, update_heartbeat_status

DB_ENV = Path(__file__).parents[1] / ".db_env_test"
TEST_NODE = 99999
SELECT = text("SELECT seen_ts, uptime, extra FROM container_rawdata WHERE node_id = :node_id")
DELETE = text("DELETE FROM container_rawdata WHERE node_id = :node_id")


@pytest.fixture
def report_engine():
    if not DB_ENV.is_file():
        pytest.skip(f"{DB_ENV.name} missing: no local test database configured")
    load_from_enviroment(db_config_file=str(DB_ENV))
    engine = con.engines.report_engine
    url = engine.url
    assert (url.host, url.port, url.database) == ("127.0.0.1", 23307, "report_test"), "local test database only"
    tb.ReportContainerRawdata.__table__.create(bind=engine, checkfirst=True)
    with engine.begin() as connection:
        connection.execute(DELETE, {"node_id": TEST_NODE})
    yield engine
    with engine.begin() as connection:
        connection.execute(DELETE, {"node_id": TEST_NODE})


def _update(seen_ts: datetime, uptime: float, extra: str) -> HeartbeatUpdate:
    return HeartbeatUpdate(
        node_id=TEST_NODE,
        start_ts=datetime(2026, 11, 6, 8, 0),
        container=DockerContainer.UDPPING,
        version="electro-2-2-5",
        process="receiver-10506",
        seen_ts=seen_ts,
        uptime=uptime,
        extra=extra,
    )


def test_upsert_keeps_one_row_per_key(report_engine):
    update_heartbeat_status([_update(datetime(2026, 11, 6, 9), 3600.0, "313741")])
    update_heartbeat_status([_update(datetime(2026, 11, 6, 10), 7200.0, "313742")])
    with report_engine.connect() as connection:
        rows = connection.execute(SELECT, {"node_id": TEST_NODE}).all()
    assert [tuple(row) for row in rows] == [(datetime(2026, 11, 6, 10), 7200.0, "313742")]
