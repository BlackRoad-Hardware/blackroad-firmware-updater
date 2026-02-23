"""Tests for BlackRoad Firmware Updater."""
import pytest
from firmware_updater import FirmwareUpdater, UpdateStatus, version_gt

@pytest.fixture
def fu(tmp_path):
    return FirmwareUpdater(db_path=tmp_path / "test.db")

@pytest.fixture
def device(fu):
    return fu.register_device("dev-001", "sensor", "1.0.0", hw_rev="2.0", name="Test Sensor")

@pytest.fixture
def release(fu):
    return fu.publish_release(
        device_type="sensor", version="2.0.0",
        notes="Major update", file_hash="abc123",
        file_size=1024, rollback_version="1.0.0")

def test_version_gt():
    assert version_gt("2.0.0", "1.0.0") is True
    assert version_gt("1.0.0", "2.0.0") is False
    assert version_gt("1.1.0", "1.0.9") is True
    assert version_gt("1.0.0", "1.0.0") is False

def test_register_device(fu):
    d = fu.register_device("dev-999", "camera", "1.0.0")
    assert d.device_type == "camera"
    assert d.current_version == "1.0.0"

def test_register_device_idempotent(fu):
    fu.register_device("dev-X", "sensor", "1.0.0")
    d2 = fu.register_device("dev-X", "sensor", "1.2.0")
    assert d2.current_version == "1.2.0"

def test_publish_release(fu):
    r = fu.publish_release("thermostat", "3.0.0", "Thermostat update", "hash123")
    assert r.version == "3.0.0"
    assert r.device_type == "thermostat"

def test_publish_release_duplicate(fu, release):
    with pytest.raises(ValueError, match="already exists"):
        fu.publish_release("sensor", "2.0.0", "dup", "hash456")

def test_check_update_available(fu, device, release):
    result = fu.check_update(device.id, "1.0.0")
    assert result["available"] is True
    assert result["latest"] == "2.0.0"

def test_check_update_not_available(fu, device, release):
    result = fu.check_update(device.id, "2.0.0")
    assert result["available"] is False

def test_check_update_no_releases(fu, device):
    result = fu.check_update(device.id, "1.0.0")
    assert result is None

def test_start_update(fu, device, release):
    job = fu.start_update(device.id, "2.0.0")
    assert job.status == UpdateStatus.PENDING
    assert job.target_version == "2.0.0"
    assert job.progress_pct == 0

def test_update_progress(fu, device, release):
    job = fu.start_update(device.id, "2.0.0")
    updated = fu.update_progress(job.id, 50, "downloading")
    assert updated.progress_pct == 50
    assert updated.status == UpdateStatus.DOWNLOADING

def test_complete_update(fu, device, release):
    job = fu.start_update(device.id, "2.0.0")
    fu.update_progress(job.id, 100, "complete")
    d = fu.get_device(device.id)
    assert d.current_version == "2.0.0"

def test_fail_job(fu, device, release):
    job = fu.start_update(device.id, "2.0.0")
    failed = fu.fail_job(job.id, "Flash timeout")
    assert failed.status == UpdateStatus.FAILED
    assert failed.error_msg == "Flash timeout"

def test_rollback(fu, device, release):
    # Publish the rollback target release first
    fu.publish_release("sensor", "1.0.0", "Original", "hash_orig")
    rollback_job = fu.rollback(device.id)
    assert rollback_job is not None
    assert rollback_job.target_version == "1.0.0"

def test_rollback_no_rollback_version(fu, device):
    fu.publish_release("sensor", "3.0.0", "No rollback", "hash789")
    # Should attempt rollback to whatever rollback_version is set
    result = fu.rollback(device.id)
    # 3.0.0 has no rollback_version
    assert result is None

def test_get_update_stats(fu, device, release):
    fu.start_update(device.id, "2.0.0")
    stats = fu.get_update_stats()
    assert stats["total_devices"] == 1
    assert stats["total_releases"] == 1
    assert stats["total_jobs"] == 1

def test_validate_hash(fu, release):
    import hashlib
    data = b"firmware binary content"
    correct_hash = hashlib.sha256(data).hexdigest()
    fu.publish_release("camera", "1.5.0", "Hash test", correct_hash)
    assert fu.validate_hash("camera", "1.5.0", data) is True
    assert fu.validate_hash("camera", "1.5.0", b"wrong data") is False

def test_list_releases(fu):
    fu.publish_release("lock", "1.0.0", "v1", "h1")
    fu.publish_release("lock", "2.0.0", "v2", "h2")
    releases = fu.list_releases("lock")
    assert len(releases) == 2

def test_get_latest_release(fu):
    fu.publish_release("actuator", "1.0.0", "v1", "h1")
    fu.publish_release("actuator", "3.0.0", "v3", "h3")
    fu.publish_release("actuator", "2.0.0", "v2", "h2")
    latest = fu.get_latest_release("actuator")
    assert latest.version == "3.0.0"

def test_get_device_jobs(fu, device, release):
    fu.start_update(device.id, "2.0.0")
    jobs = fu.get_device_jobs(device.id)
    assert len(jobs) == 1
