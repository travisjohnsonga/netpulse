"""
Register the on-disk MIB directories (mounted at /app/mibs) with a pysnmp
MibBuilder so additional vendor/community/custom MIBs are available for OID
resolution. Best-effort: any failure (missing dirs, pysnmp quirks) is logged
and never blocks startup — the built-in mib_resolver map remains the fallback.
"""
import logging
import os

logger = logging.getLogger(__name__)

MIB_DIRS = [
    "/app/mibs/standard",
    "/app/mibs/vendor/cisco",
    "/app/mibs/vendor/juniper",
    "/app/mibs/vendor/fortinet",
    "/app/mibs/vendor/arista",
    "/app/mibs/vendor/aruba",
    "/app/mibs/vendor/aos-cx",
    "/app/mibs/vendor/sonicwall",
    "/app/mibs/vendor/paloalto",
    "/app/mibs/vendor/mikrotik",
    "/app/mibs/vendor/community",
    "/app/mibs/custom",
]


def register_mib_sources(extra_dirs: list[str] | None = None):
    """Add existing MIB directories as DirMibSource entries. Returns the count."""
    dirs = [d for d in (MIB_DIRS + (extra_dirs or [])) if os.path.isdir(d)]
    try:
        from pysnmp.smi import builder, view

        mib_builder = builder.MibBuilder()
        if dirs:
            mib_builder.addMibSources(*[builder.DirMibSource(d) for d in dirs])
        view.MibViewController(mib_builder)
        logger.info("registered %d MIB source dir(s) for OID resolution", len(dirs))
        return len(dirs)
    except Exception as exc:  # pysnmp/pysmi differences must not break ingest
        logger.warning("MIB source registration skipped: %s", exc)
        return 0
