"""
Compliance-framework scoping.

Operators declare which regulatory frameworks their environment is actually
subject to via the ``APPLICABLE_COMPLIANCE_FRAMEWORKS`` setting (env-backed,
comma-separated framework keys — see ``config.settings.base``). Frameworks
outside that scope are excluded from *every* compliance surface: the API list,
the per-framework assessment + PDF evidence package, the ``/compliance`` page and
the TV/NOC compliance screen (both derive everything from the scoped list).

The guarantee: a framework you are not subject to can never surface as
failing/partial/non-compliant to a non-technical viewer (compliance/audit/
management), nor count toward any "N frameworks" denominator or fleet-coverage
average. It is simply not part of the compliance picture.

Back-compat: an unset/empty allowlist means *all* frameworks apply, so existing
deployments keep every framework until they deliberately opt into scoping.

Skip-framework-unique-checks bonus: because out-of-scope frameworks are never
evaluated, evidence checks that are *unique* to them (e.g. PCI-DSS's
network-segmentation control) never run and never drag down the fleet picture —
only in-scope frameworks contribute to the headline numbers.

Scope is deliberately operator-controlled (``.env``, not a web toggle): defining
applicable frameworks in config is effectively documenting your scope (a
Statement-of-Applicability), so it can't be changed casually from the UI.
"""
from __future__ import annotations

from django.conf import settings

from .models import RegulatoryFramework

# Every framework key spane ships. Used to validate the allowlist so a typo can't
# silently widen scope to a non-existent framework.
VALID_FRAMEWORK_KEYS = frozenset(k for k, _ in RegulatoryFramework.Key.choices)


def applicable_framework_keys() -> set[str] | None:
    """The set of in-scope framework keys, or ``None`` meaning *all apply*.

    ``None`` (allowlist unset/empty) is the back-compat default: every framework
    is in scope. Unknown keys in the allowlist are dropped (validated against
    :data:`VALID_FRAMEWORK_KEYS`) — fail-closed, so a misconfigured scope never
    leaks a framework you didn't intend.
    """
    configured = getattr(settings, "APPLICABLE_COMPLIANCE_FRAMEWORKS", None) or []
    keys = {str(k).strip().lower() for k in configured if str(k).strip()}
    if not keys:
        return None
    return keys & set(VALID_FRAMEWORK_KEYS)


def is_framework_applicable(key: str) -> bool:
    """True when ``key`` is in scope (always True when no allowlist is set)."""
    allowed = applicable_framework_keys()
    return allowed is None or key in allowed


def applicable_frameworks(queryset=None):
    """Filter a ``RegulatoryFramework`` queryset to the in-scope frameworks.

    With no allowlist set, returns the queryset unchanged (all frameworks).
    """
    qs = RegulatoryFramework.objects.all() if queryset is None else queryset
    allowed = applicable_framework_keys()
    if allowed is None:
        return qs
    return qs.filter(key__in=allowed)


def out_of_scope_keys() -> set[str]:
    """Known framework keys that are NOT in scope (empty set when all apply)."""
    allowed = applicable_framework_keys()
    if allowed is None:
        return set()
    return set(VALID_FRAMEWORK_KEYS) - allowed
