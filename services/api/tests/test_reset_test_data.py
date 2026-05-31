import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

pytestmark = pytest.mark.django_db


def test_reset_clears_app_data_keeps_users():
    from apps.checks.models import ServiceCheck
    from apps.devices.models import Device, Site

    User = get_user_model()
    User.objects.create_user(username="keep-me", password="x", role="admin")
    site = Site.objects.create(name="DC-1")
    Device.objects.create(hostname="r1", ip_address="10.0.0.1", site=site)
    ServiceCheck.objects.create(name="web", check_type="https", host="app.co")

    call_command("reset_test_data")

    # App data wiped...
    assert Device.objects.count() == 0
    assert Site.objects.count() == 0
    assert ServiceCheck.objects.count() == 0
    # ...auth users kept.
    assert User.objects.filter(username="keep-me").exists()
