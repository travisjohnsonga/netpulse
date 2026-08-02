# Merge migration for the dual-0007 leaves created by the rule-management arc
# reconcile: main shipped 0007_rename_notificationlog_indexes (PR #163's fix,
# re-rooted on 0006) while the arc carried 0007_alertrule_kind. The arc's
# 0008_rename_notificationlog_indexes was deleted here — its renames are the
# same ones main's 0007 already applied and would fail a second time.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("alerts", "0007_alertrule_kind"),
        ("alerts", "0007_rename_notificationlog_indexes"),
    ]

    operations = []
