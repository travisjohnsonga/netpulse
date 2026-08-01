# Reconciles the NotificationLog index-name drift on MAIN. Django 6 derives
# index-name hashes differently than the names 0005_notificationlog created, so
# `makemigrations --check` flagged a rename on every run. PR #163 already fixed
# this — but it merged into feature/rule-management (as its 0008, depending on
# the arc-only 0007_alertrule_kind), so main never received it.
#
# ⚠ Arc-merge note (feature/rule-management reconcile gate): when the arc lands,
# DELETE its 0008_rename_notificationlog_indexes — these renames will already
# have been applied by this migration and would fail a second time — and add a
# merge migration for the dual-0007 leaf (this one + the arc's
# 0007_alertrule_kind, both depending on 0006).

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('alerts', '0006_alertrule_notify_enabled'),
    ]

    operations = [
        migrations.RenameIndex(
            model_name='notificationlog',
            new_name='alerts_noti_channel_05c10b_idx',
            old_name='alerts_noti_channel_4d8f9e_idx',
        ),
        migrations.RenameIndex(
            model_name='notificationlog',
            new_name='alerts_noti_status_0d187b_idx',
            old_name='alerts_noti_status_7c1a2b_idx',
        ),
    ]
