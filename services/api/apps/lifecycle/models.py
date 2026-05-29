from django.db import models

from apps.core.models import TimestampedModel
from apps.devices.models import Device


class LifecycleMilestone(TimestampedModel):
    class MilestoneType(models.TextChoices):
        END_OF_SALE = "eos", "End of Sale"
        END_OF_SOFTWARE_MAINTENANCE = "eosm", "End of Software Maintenance"
        END_OF_SECURITY_SUPPORT = "eoss", "End of Security Support"
        END_OF_LIFE = "eol", "End of Life"

    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="lifecycle_milestones")
    milestone_type = models.CharField(max_length=10, choices=MilestoneType.choices, db_index=True)
    milestone_date = models.DateField(db_index=True)
    source = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)

    class Meta(TimestampedModel.Meta):
        unique_together = [("device", "milestone_type")]

    def __str__(self):
        return f"{self.device.hostname} — {self.get_milestone_type_display()}: {self.milestone_date}"
