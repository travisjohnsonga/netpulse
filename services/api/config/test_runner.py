"""Bridge Django's ``manage.py test`` command to pytest.

The NetPulse test suite is written in pytest style (plain ``test_*`` functions
with fixtures, not ``unittest.TestCase`` subclasses) and lives in
``services/api/tests/`` rather than inside each app. Django's built-in
``DiscoverRunner`` only finds ``TestCase`` subclasses under app packages, so
``manage.py test`` discovers nothing and reports "NO TESTS RAN".

This runner makes the obvious ``manage.py test [labels]`` command drive pytest
instead. It shells out to pytest in a subprocess so that ``pytest.ini``'s
``DJANGO_SETTINGS_MODULE = config.settings.test`` configures the run cleanly —
running pytest in-process would clash with the settings Django has already
loaded for the ``manage.py`` process.

The canonical way to run the suite is still ``python -m pytest`` (see
CLAUDE.md); this just keeps the standard Django command working too.
"""
import os
import subprocess
import sys
from pathlib import Path


class PytestTestRunner:
    """Run the pytest suite from ``manage.py test``."""

    def __init__(self, verbosity=1, failfast=False, **kwargs):
        self.verbosity = verbosity
        self.failfast = failfast

    @classmethod
    def add_arguments(cls, parser):  # pragma: no cover - arg plumbing
        # Django passes --keepdb/--reverse/etc.; we accept and ignore the ones
        # pytest doesn't need so the command line stays compatible.
        pass

    def run_tests(self, test_labels, **kwargs):
        """Translate Django test labels to pytest args and run them.

        Returns the number of failures (0 == success) so ``manage.py test``
        exits with the right status.
        """
        base = Path(__file__).resolve().parents[1]  # services/api
        argv = [sys.executable, "-m", "pytest"]
        if self.failfast:
            argv.append("-x")
        if self.verbosity == 0:
            argv.append("-q")
        elif self.verbosity >= 2:
            argv.append("-v")

        keywords = []
        for label in test_labels:
            # Already a pytest path or node id (tests/test_x.py::test_y).
            if "::" in label or label.endswith(".py") or os.sep in label:
                argv.append(label)
                continue
            # Dotted path that maps to a real file (e.g. tests.test_devices).
            as_path = label.replace(".", os.sep) + ".py"
            if (base / as_path).exists():
                argv.append(as_path)
                continue
            # App-style label (e.g. apps.devices) — no co-located test module,
            # so select matching tests by keyword on the leaf name.
            keywords.append(label.split(".")[-1])
        if keywords:
            argv += ["-k", " or ".join(keywords)]

        return subprocess.run(argv, cwd=base).returncode
