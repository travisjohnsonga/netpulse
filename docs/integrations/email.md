# Email / SMTP Integration

Configure outbound SMTP so spane can send alert email. Configure under
**Settings → Integrations → Email**. The SMTP password is stored in OpenBao
(`netpulse/integrations/smtp`), never in the database.

Pick a provider to auto-fill the host/port/TLS settings, fill in the credentials,
toggle **Enabled**, and use **Send Test Email** to verify.

## Gmail

- Host `smtp.gmail.com`, port `587`, TLS on.
- Gmail requires an **App Password**, not your normal password:
    1. Enable 2-Step Verification at [myaccount.google.com](https://myaccount.google.com).
    2. Create an App Password at
       [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords).
    3. Use that 16-character password here.

## Microsoft 365

- Host `smtp.office365.com`, port `587`, TLS on.
- Use your M365 email + password. **SMTP AUTH** must be enabled in the admin
  center (admin.microsoft.com → Settings → Org settings → Mail flow).

## SendGrid / Mailgun

- **SendGrid:** host `smtp.sendgrid.net`, port `587`; username is always
  `apikey`, password is your SendGrid API key.
- **Mailgun:** host `smtp.mailgun.org`, port `587`; use the SMTP credentials from
  your Mailgun domain settings.

## Custom SMTP

Enter your provider's host, port, username/password and TLS/SSL choice. From
Name / From Email set the sender shown on alerts.

Once enabled, the alert engine sends through these settings; if disabled it falls
back to the env-configured Django mail backend.
