from django.conf import settings
from django.db import models


class LogFilter(models.Model):
    """A regex rule applied to fleet log messages — suppress noise, highlight
    important lines, or tag matches. Optionally scoped to specific platforms."""

    class Action(models.TextChoices):
        SUPPRESS = "suppress", "Suppress"
        HIGHLIGHT = "highlight", "Highlight"
        TAG = "tag", "Tag"

    name = models.CharField(max_length=128)
    pattern = models.TextField(help_text="Regular expression pattern")
    action = models.CharField(
        max_length=20, choices=Action.choices, default=Action.SUPPRESS)
    color = models.CharField(
        max_length=7, blank=True, help_text="Hex color for highlight action")
    tag = models.CharField(max_length=64, blank=True)
    platforms = models.JSONField(
        default=list, blank=True, help_text="Empty = all platforms")
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["name"]

    def test(self, message: str) -> bool:
        """True if this filter's regex matches ``message`` (case-insensitive).
        An invalid regex never matches (never raises)."""
        import re
        try:
            return bool(re.search(self.pattern, message or "", re.IGNORECASE))
        except re.error:
            return False

    def __str__(self):
        return self.name
