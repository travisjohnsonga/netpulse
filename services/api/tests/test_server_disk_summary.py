"""The Servers-list/Overview "worst disk" summary must ignore tiny system /
recovery partitions (EFI/recovery are 100% full by design) so the real disk —
not a 7.6 GB recovery volume at 100% — drives the headline stat."""
from apps.agents.metrics_read import MIN_DISK_SUMMARY_BYTES, _worst_disk

GB = 1024 * 1024 * 1024


def test_excludes_tiny_full_recovery_partition():
    # C: 500 GB @ 30%, D: 7.6 GB recovery @ 100% → summary should pick C:, not D:.
    disk = {
        "C:": {"usage_pct": 30.0, "total_bytes": 500 * GB},
        "D:": {"usage_pct": 100.0, "total_bytes": 7.6 * GB},
    }
    assert _worst_disk(disk) == (30.0, "C:")


def test_picks_worst_among_large_volumes():
    disk = {
        "C:": {"usage_pct": 30.0, "total_bytes": 500 * GB},
        "E:": {"usage_pct": 88.0, "total_bytes": 200 * GB},
    }
    assert _worst_disk(disk) == (88.0, "E:")


def test_unknown_size_is_not_excluded():
    # If total_bytes is missing, keep the mount rather than hide a real disk.
    disk = {"/data": {"usage_pct": 91.0, "total_bytes": None}}
    assert _worst_disk(disk) == (91.0, "/data")


def test_all_tiny_yields_none():
    disk = {"EFI": {"usage_pct": 100.0, "total_bytes": 0.5 * GB}}
    assert _worst_disk(disk) == (None, None)


def test_threshold_is_10_gib():
    assert MIN_DISK_SUMMARY_BYTES == 10 * GB
