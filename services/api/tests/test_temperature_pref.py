"""User temperature-unit preference + conversion helpers."""
import pytest

from apps.core.temperature import (
    celsius_to_fahrenheit,
    fahrenheit_to_celsius,
    format_temperature,
)

pytestmark = pytest.mark.django_db


class TestConversion:
    def test_c_to_f(self):
        assert celsius_to_fahrenheit(0) == 32
        assert celsius_to_fahrenheit(100) == 212
        assert celsius_to_fahrenheit(37) == pytest.approx(98.6)

    def test_f_to_c(self):
        assert fahrenheit_to_celsius(32) == 0
        assert fahrenheit_to_celsius(212) == pytest.approx(100)

    def test_round_trip(self):
        assert fahrenheit_to_celsius(celsius_to_fahrenheit(75)) == pytest.approx(75)

    def test_format(self):
        assert format_temperature(75.0, "C") == "75.0°C"
        assert format_temperature(75.0, "F") == "167.0°F"
        assert format_temperature(75.0, "f") == "167.0°F"   # case-insensitive
        assert format_temperature(75.0, "x") == "75.0°C"    # unknown → celsius
        assert format_temperature(None, "F") == "—"


class TestPreferenceApi:
    def test_default_is_celsius_and_exposed(self, auth_client):
        resp = auth_client.get("/api/users/me/")
        assert resp.status_code == 200
        assert resp.json()["preferences"]["temperature_unit"] == "C"

    def test_update_via_preferences_endpoint(self, auth_client):
        resp = auth_client.put("/api/users/me/preferences/",
                               {"temperature_unit": "F"}, format="json")
        assert resp.status_code in (200, 202)
        assert resp.json()["temperature_unit"] == "F"
        # Persisted + reflected on /me/.
        me = auth_client.get("/api/users/me/").json()
        assert me["preferences"]["temperature_unit"] == "F"

    def test_invalid_unit_rejected(self, auth_client):
        resp = auth_client.put("/api/users/me/preferences/",
                               {"temperature_unit": "K"}, format="json")
        assert resp.status_code == 400
