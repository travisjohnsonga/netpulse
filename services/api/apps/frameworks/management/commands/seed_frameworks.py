"""
Seed the regulatory frameworks + their representative control catalogs.

Idempotent (update_or_create), so it's safe to run on every startup. Control
catalogs are representative subsets of each standard, scoped to the technical
controls spane can evidence — not verbatim reproductions of the full standards.
"""
from django.core.management.base import BaseCommand

from apps.frameworks.models import FrameworkControl, RegulatoryFramework

# framework key → (name, version, description, [(control_id, title, category, mapping_key), ...])
FRAMEWORKS = {
    "sox": ("SOX (ITGC)", "2024", "Sarbanes-Oxley IT General Controls for change "
            "management, access and operations affecting financial systems.", [
        ("ITGC-CM-1", "Change management — config baselines", "Change Management", "config_compliance"),
        ("ITGC-CM-2", "Change audit trail", "Change Management", "change_management"),
        ("ITGC-AC-1", "Logical access control", "Access", "access_control_rbac"),
        ("ITGC-OP-1", "Configuration backup & recovery", "Operations", "config_backup"),
        ("ITGC-OP-2", "Audit logging of privileged actions", "Operations", "audit_logging"),
        ("ITGC-OP-3", "Saved/running config integrity", "Operations", "startup_saved"),
    ]),
    "iso27001": ("ISO/IEC 27001", "2022", "ISO/IEC 27001:2022 Annex A technical controls.", [
        ("A.5.9", "Inventory of information assets", "Asset Management", "asset_inventory"),
        ("A.8.9", "Configuration management", "Technological", "config_compliance"),
        ("A.8.8", "Management of technical vulnerabilities", "Technological", "vulnerability_mgmt"),
        ("A.8.15", "Logging", "Technological", "audit_logging"),
        ("A.8.5", "Secure authentication / access", "Technological", "access_control_rbac"),
        ("A.8.24", "Use of cryptography (in transit)", "Technological", "encryption_in_transit"),
        ("A.8.13", "Information backup", "Technological", "config_backup"),
        ("A.5.23", "Secrets / key management", "Organizational", "secrets_management"),
    ]),
    "nist_csf": ("NIST CSF", "2.0", "NIST Cybersecurity Framework 2.0 functions/categories.", [
        ("ID.AM", "Asset Management", "Identify", "asset_inventory"),
        ("PR.AA", "Identity Management & Access Control", "Protect", "access_control_rbac"),
        ("PR.DS", "Data Security (in transit)", "Protect", "encryption_in_transit"),
        ("PR.IP", "Configuration baselines", "Protect", "config_compliance"),
        ("PR.PS", "Platform/OS lifecycle", "Protect", "os_lifecycle"),
        ("DE.CM", "Continuous Monitoring (logging)", "Detect", "audit_logging"),
        ("RC.RP", "Recovery — config backups", "Recover", "config_backup"),
    ]),
    "pci_dss": ("PCI-DSS", "4.0", "Payment Card Industry Data Security Standard v4.0.", [
        ("PCI-1.2", "Network segmentation controls", "Network", "network_segmentation"),
        ("PCI-2.2", "Secure configuration standards", "Config", "config_compliance"),
        ("PCI-6.3", "Vulnerability management", "Vuln Mgmt", "vulnerability_mgmt"),
        ("PCI-8.3", "Strong access control / authentication", "Access", "access_control_rbac"),
        ("PCI-10.2", "Audit logging of system activity", "Logging", "audit_logging"),
        ("PCI-4.2", "Strong cryptography in transit", "Crypto", "encryption_in_transit"),
        ("PCI-3.6", "Secure key/secret storage", "Crypto", "secrets_management"),
    ]),
    "hipaa": ("HIPAA Security Rule", "2013", "HIPAA Security Rule §164.308/.312 technical safeguards.", [
        ("164.312(a)", "Access control", "Technical Safeguards", "access_control_rbac"),
        ("164.312(b)", "Audit controls", "Technical Safeguards", "audit_logging"),
        ("164.312(e)", "Transmission security", "Technical Safeguards", "encryption_in_transit"),
        ("164.308(a)(1)", "Risk management — vulnerabilities", "Administrative", "vulnerability_mgmt"),
        ("164.308(a)(7)", "Contingency — config backup", "Administrative", "config_backup"),
        ("164.312(c)", "Integrity — config baselines", "Technical Safeguards", "config_compliance"),
    ]),
    "cis": ("CIS Controls v8", "8.0", "CIS Critical Security Controls v8 (network-relevant subset).", [
        ("CIS-1", "Inventory of enterprise assets", "Basic", "asset_inventory"),
        ("CIS-4", "Secure configuration of assets", "Basic", "config_compliance"),
        ("CIS-7", "Continuous vulnerability management", "Foundational", "vulnerability_mgmt"),
        ("CIS-6", "Access control management", "Basic", "access_control_rbac"),
        ("CIS-8", "Audit log management", "Foundational", "audit_logging"),
        ("CIS-12", "Network infrastructure management", "Foundational", "config_backup"),
        ("CIS-3", "Data protection (in transit)", "Basic", "encryption_in_transit"),
    ]),
}


class Command(BaseCommand):
    help = "Seed regulatory frameworks and their representative control catalogs."

    def handle(self, *args, **options):
        fw_n = ctl_n = 0
        for key, (name, version, desc, controls) in FRAMEWORKS.items():
            fw, _ = RegulatoryFramework.objects.update_or_create(
                key=key, defaults={"name": name, "version": version, "description": desc})
            fw_n += 1
            for control_id, title, category, mapping_key in controls:
                FrameworkControl.objects.update_or_create(
                    framework=fw, control_id=control_id,
                    defaults={"title": title, "category": category, "mapping_key": mapping_key})
                ctl_n += 1
        self.stdout.write(self.style.SUCCESS(
            f"Seeded {fw_n} frameworks, {ctl_n} controls."))
