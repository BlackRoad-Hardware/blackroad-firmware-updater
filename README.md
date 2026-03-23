# blackroad-firmware-updater

> OTA firmware update management system

Part of the [BlackRoad OS](https://blackroad.io) ecosystem — [BlackRoad-Hardware](https://github.com/BlackRoad-Hardware)

---

# blackroad-firmware-updater

> OTA firmware update management system — part of the BlackRoad Hardware platform.

## Features

- **Release Management** — Publish, version, and manage firmware releases per device type
- **Update Jobs** — Track OTA update progress through downloading → validating → flashing → rebooting
- **Rollback Support** — Define rollback versions for safe recovery
- **Hash Validation** — SHA-256 integrity verification
- **Version Ordering** — Semantic version comparison for latest-release detection
- **Statistics** — Fleet-wide update status reporting

## Quick Start

```bash
pip install -r requirements.txt
python firmware_updater.py stats
```

## Usage

```python
from firmware_updater import FirmwareUpdater

fu = FirmwareUpdater()

fu.register_device("dev-001", "sensor", "1.0.0", hw_rev="2.0")

fu.publish_release(
    device_type="sensor", version="2.0.0",
    notes="Performance improvements", file_hash="sha256hash",
    rollback_version="1.0.0"
)

update_info = fu.check_update("dev-001", "1.0.0")
if update_info["available"]:
    job = fu.start_update("dev-001", update_info["latest"])
    fu.update_progress(job.id, 50, "downloading")
    fu.update_progress(job.id, 100, "complete")

# Rollback if needed
fu.rollback("dev-001")
```

## Update Status Flow

```
pending → downloading → validating → flashing → rebooting → complete
                                                          ↘ failed
```

## Testing

```bash
pytest --tb=short -v
```

## License

Proprietary — BlackRoad OS, Inc. All rights reserved.
