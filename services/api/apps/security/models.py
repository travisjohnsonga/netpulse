from django.db import models

from apps.core.models import TimestampedModel
from apps.devices.models import Device


class DeviceRiskScore(TimestampedModel):
    device = models.OneToOneField(Device, on_delete=models.CASCADE, related_name="risk_score")
    # Composite 0–100 score; higher = riskier
    score = models.DecimalField(max_digits=5, decimal_places=2, db_index=True)
    cve_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    compliance_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    lifecycle_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    anomaly_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    last_computed_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.device.hostname}: {self.score}"
