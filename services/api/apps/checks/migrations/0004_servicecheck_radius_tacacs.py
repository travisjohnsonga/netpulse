from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("checks", "0003_servicecheck_alert_on_degraded_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="servicecheck",
            name="check_type",
            field=models.CharField(
                choices=[
                    ("http", "HTTP"), ("https", "HTTPS"), ("tcp", "TCP"),
                    ("udp", "UDP"), ("icmp", "ICMP (ping)"), ("dns", "DNS"),
                    ("tls", "TLS certificate"), ("smtp", "SMTP"), ("ftp", "FTP"),
                    ("ssh", "SSH"), ("ssh_banner", "SSH banner"), ("ldap", "LDAP"),
                    ("radius", "RADIUS"), ("tacacs", "TACACS+"), ("custom", "Custom"),
                ],
                db_index=True, max_length=10,
            ),
        ),
    ]
