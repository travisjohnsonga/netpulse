# Merge migration for the feature/rule-management reconcile gate.
#
# On the arc this was a RenameIndex fixing the NotificationLog index-name
# drift, but main already applies the identical renames in
# 0007_rename_notificationlog_indexes (#177) — running them again would fail.
# It is kept under the same name (so 0009 and any box that applied the arc's
# version stay consistent) and now only joins the dual-0007 leaves.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('alerts', '0007_alertrule_kind'),
        ('alerts', '0007_rename_notificationlog_indexes'),
    ]

    operations = []
