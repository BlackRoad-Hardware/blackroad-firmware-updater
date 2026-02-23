"""
BlackRoad Firmware Updater - OTA firmware update management system.
"""
from __future__ import annotations
import hashlib, json, logging, sqlite3, uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
DB_PATH = Path("firmware_updater.db")

class UpdateStatus(str, Enum):
    PENDING = "pending"; DOWNLOADING = "downloading"; VALIDATING = "validating"
    FLASHING = "flashing"; REBOOTING = "rebooting"; COMPLETE = "complete"; FAILED = "failed"

def _parse_version(v: str) -> tuple:
    try: return tuple(int(x) for x in v.lstrip("v").split("."))
    except ValueError: return (0, 0, 0)

def version_gt(a: str, b: str) -> bool:
    return _parse_version(a) > _parse_version(b)

@dataclass
class FirmwareRelease:
    id: str; device_type: str; version: str; release_notes: str
    file_hash: str; file_size: int; min_hardware_rev: str
    dependencies: List[str]; rollback_version: Optional[str]; created_at: str

    @classmethod
    def from_row(cls, row) -> "FirmwareRelease":
        return cls(
            id=row["id"], device_type=row["device_type"], version=row["version"],
            release_notes=row["release_notes"], file_hash=row["file_hash"],
            file_size=row["file_size"], min_hardware_rev=row["min_hardware_rev"],
            dependencies=json.loads(row["dependencies"]),
            rollback_version=row["rollback_version"], created_at=row["created_at"])

@dataclass
class UpdateJob:
    id: str; device_id: str; target_version: str; status: UpdateStatus
    progress_pct: int; started_at: str; completed_at: Optional[str]; error_msg: Optional[str]

    @classmethod
    def from_row(cls, row) -> "UpdateJob":
        return cls(
            id=row["id"], device_id=row["device_id"], target_version=row["target_version"],
            status=UpdateStatus(row["status"]), progress_pct=row["progress_pct"],
            started_at=row["started_at"], completed_at=row["completed_at"],
            error_msg=row["error_msg"])

@dataclass
class DeviceRecord:
    id: str; device_type: str; current_version: str; hardware_rev: str
    name: Optional[str] = None

    @classmethod
    def from_row(cls, row) -> "DeviceRecord":
        return cls(id=row["id"], device_type=row["device_type"],
                   current_version=row["current_version"],
                   hardware_rev=row["hardware_rev"], name=row["name"])

@contextmanager
def db_conn(db_path: Path = DB_PATH):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn; conn.commit()
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()

def init_db(db_path: Path = DB_PATH) -> None:
    with db_conn(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS devices (
                id TEXT PRIMARY KEY, device_type TEXT NOT NULL,
                current_version TEXT NOT NULL, hardware_rev TEXT NOT NULL DEFAULT '1.0',
                name TEXT
            );
            CREATE TABLE IF NOT EXISTS firmware_releases (
                id TEXT PRIMARY KEY, device_type TEXT NOT NULL,
                version TEXT NOT NULL, release_notes TEXT NOT NULL DEFAULT '',
                file_hash TEXT NOT NULL, file_size INTEGER NOT NULL DEFAULT 0,
                min_hardware_rev TEXT NOT NULL DEFAULT '1.0',
                dependencies TEXT NOT NULL DEFAULT '[]',
                rollback_version TEXT, created_at TEXT NOT NULL,
                UNIQUE(device_type, version)
            );
            CREATE TABLE IF NOT EXISTS update_jobs (
                id TEXT PRIMARY KEY, device_id TEXT NOT NULL,
                target_version TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                progress_pct INTEGER NOT NULL DEFAULT 0, started_at TEXT NOT NULL,
                completed_at TEXT, error_msg TEXT,
                FOREIGN KEY (device_id) REFERENCES devices(id)
            );
            CREATE INDEX IF NOT EXISTS idx_releases_type ON firmware_releases(device_type, version);
            CREATE INDEX IF NOT EXISTS idx_jobs_device ON update_jobs(device_id, status);
        """)
    logger.info("Firmware updater DB initialised at %s", db_path)

class FirmwareUpdater:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path; init_db(db_path)

    # -- Device Registration --
    def register_device(self, device_id: str, device_type: str, current_version: str,
                         hardware_rev: str = "1.0", hw_rev: Optional[str] = None,
                         name: Optional[str] = None) -> DeviceRecord:
        if hw_rev is not None:
            hardware_rev = hw_rev
        with db_conn(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO devices (id,device_type,current_version,hardware_rev,name) "
                "VALUES (?,?,?,?,?)", (device_id, device_type, current_version, hardware_rev, name))
        return self.get_device(device_id)

    def get_device(self, device_id: str) -> DeviceRecord:
        with db_conn(self.db_path) as conn:
            row = conn.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
        if not row: raise ValueError(f"Device not found: {device_id}")
        return DeviceRecord.from_row(row)

    # -- Firmware Releases --
    def publish_release(self, device_type: str, version: str, notes: str,
                         file_hash: str, file_size: int = 0, min_hardware_rev: str = "1.0",
                         dependencies: Optional[List[str]] = None,
                         rollback_version: Optional[str] = None) -> FirmwareRelease:
        if dependencies is None: dependencies = []
        rel_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with db_conn(self.db_path) as conn:
            try:
                conn.execute(
                    "INSERT INTO firmware_releases (id,device_type,version,release_notes,"
                    "file_hash,file_size,min_hardware_rev,dependencies,rollback_version,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (rel_id, device_type, version, notes, file_hash, file_size,
                     min_hardware_rev, json.dumps(dependencies), rollback_version, now))
            except sqlite3.IntegrityError:
                raise ValueError(f"Release {device_type}@{version} already exists")
        logger.info("Published firmware %s@%s", device_type, version)
        return self.get_release(device_type, version)

    def get_release(self, device_type: str, version: str) -> FirmwareRelease:
        with db_conn(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM firmware_releases WHERE device_type=? AND version=?",
                (device_type, version)).fetchone()
        if not row: raise ValueError(f"Release not found: {device_type}@{version}")
        return FirmwareRelease.from_row(row)

    def list_releases(self, device_type: Optional[str] = None) -> List[FirmwareRelease]:
        q = "SELECT * FROM firmware_releases WHERE 1=1"; params: List[Any] = []
        if device_type: q += " AND device_type=?"; params.append(device_type)
        q += " ORDER BY created_at DESC"
        with db_conn(self.db_path) as conn:
            rows = conn.execute(q, params).fetchall()
        return [FirmwareRelease.from_row(r) for r in rows]

    def get_latest_release(self, device_type: str) -> Optional[FirmwareRelease]:
        releases = self.list_releases(device_type)
        if not releases: return None
        return max(releases, key=lambda r: _parse_version(r.version))

    # -- Update Checks --
    def check_update(self, device_id: str, current_version: str) -> Optional[Dict[str, Any]]:
        device = self.get_device(device_id)
        latest = self.get_latest_release(device.device_type)
        if not latest: return None
        if version_gt(latest.version, current_version):
            return {"available": True, "current": current_version,
                    "latest": latest.version, "release": asdict(latest)}
        return {"available": False, "current": current_version, "latest": latest.version}

    # -- Update Jobs --
    def start_update(self, device_id: str, target_version: str) -> UpdateJob:
        device = self.get_device(device_id)
        # Verify release exists
        self.get_release(device.device_type, target_version)
        job_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with db_conn(self.db_path) as conn:
            conn.execute(
                "INSERT INTO update_jobs (id,device_id,target_version,status,progress_pct,started_at) "
                "VALUES (?,?,?,'pending',0,?)",
                (job_id, device_id, target_version, now))
        logger.info("Update job %s started for device %s → %s", job_id, device_id, target_version)
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> UpdateJob:
        with db_conn(self.db_path) as conn:
            row = conn.execute("SELECT * FROM update_jobs WHERE id=?", (job_id,)).fetchone()
        if not row: raise ValueError(f"Job not found: {job_id}")
        return UpdateJob.from_row(row)

    def update_progress(self, job_id: str, pct: int, status: str) -> UpdateJob:
        pct = max(0, min(100, pct)); now = datetime.now(timezone.utc).isoformat()
        s = UpdateStatus(status)
        completed = now if s in (UpdateStatus.COMPLETE, UpdateStatus.FAILED) else None
        with db_conn(self.db_path) as conn:
            conn.execute(
                "UPDATE update_jobs SET progress_pct=?, status=?, completed_at=? WHERE id=?",
                (pct, s.value, completed, job_id))
        # If complete, update device current_version
        if s == UpdateStatus.COMPLETE:
            job = self.get_job(job_id)
            with db_conn(self.db_path) as conn:
                conn.execute("UPDATE devices SET current_version=? WHERE id=?",
                             (job.target_version, job.device_id))
        return self.get_job(job_id)

    def fail_job(self, job_id: str, error_msg: str) -> UpdateJob:
        now = datetime.now(timezone.utc).isoformat()
        with db_conn(self.db_path) as conn:
            conn.execute(
                "UPDATE update_jobs SET status='failed', completed_at=?, error_msg=? WHERE id=?",
                (now, error_msg, job_id))
        return self.get_job(job_id)

    # -- Rollback --
    def rollback(self, device_id: str) -> Optional[UpdateJob]:
        device = self.get_device(device_id)
        latest = self.get_latest_release(device.device_type)
        if not latest or not latest.rollback_version: return None
        # Only start rollback if the rollback release actually exists
        try:
            self.get_release(device.device_type, latest.rollback_version)
        except ValueError:
            return None
        return self.start_update(device_id, latest.rollback_version)

    # -- Stats --
    def get_update_stats(self) -> Dict[str, Any]:
        with db_conn(self.db_path) as conn:
            total_devices = conn.execute("SELECT COUNT(*) FROM devices").fetchone()[0]
            total_jobs = conn.execute("SELECT COUNT(*) FROM update_jobs").fetchone()[0]
            status_breakdown = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM update_jobs GROUP BY status"
            ).fetchall()
            total_releases = conn.execute(
                "SELECT COUNT(*) FROM firmware_releases").fetchone()[0]
        return {
            "total_devices": total_devices, "total_jobs": total_jobs,
            "total_releases": total_releases,
            "job_status": {r["status"]: r["cnt"] for r in status_breakdown},
        }

    def validate_hash(self, device_type: str, version: str, data: bytes) -> bool:
        release = self.get_release(device_type, version)
        return hashlib.sha256(data).hexdigest() == release.file_hash

    def get_device_jobs(self, device_id: str, limit: int = 10) -> List[UpdateJob]:
        with db_conn(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM update_jobs WHERE device_id=? ORDER BY started_at DESC LIMIT ?",
                (device_id, limit)).fetchall()
        return [UpdateJob.from_row(r) for r in rows]

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="BlackRoad Firmware Updater")
    sub = parser.add_subparsers(dest="cmd"); sub.add_parser("stats")
    args = parser.parse_args(); fu = FirmwareUpdater()
    if args.cmd == "stats": print(json.dumps(fu.get_update_stats(), indent=2))
    else: parser.print_help()

if __name__ == "__main__":
    main()
