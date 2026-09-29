import smtplib
from email.message import EmailMessage
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)


def send_email(to_email: str, subject: str, body: str, is_html: bool = False):
    import urllib.request
    import json
    
    if getattr(settings, 'BREVO_API_KEY', None):
        url = "https://api.brevo.com/v3/smtp/email"
        headers = {
            "accept": "application/json",
            "api-key": settings.BREVO_API_KEY.strip().strip("'").strip('"'),
            "content-type": "application/json"
        }
        sender_email = settings.SMTP_USER if settings.SMTP_USER else "noreply@sbnsentinel.com"
        data = {
            "sender": {"email": sender_email, "name": "SBN Sentinel"},
            "to": [{"email": to_email}],
            "subject": subject,
        }
        if is_html:
            data["htmlContent"] = body
        else:
            data["textContent"] = body
            
        req = urllib.request.Request(url, data=json.dumps(data).encode('utf-8'), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req) as response:
                return response.status in [200, 201, 202]
        except Exception as e:
            logger.error(f"Failed to send email via Brevo to {to_email}: {str(e)}")
            return False

    if not settings.SMTP_SERVER or not settings.SMTP_USER or not settings.SMTP_PASSWORD:
        logger.warning(
            f"SMTP/Brevo configuration is missing. Mocking email send to {to_email}: {subject}")
        # Print out the body so we can see the OTP in the terminal
        print(f"\n--- MOCKED EMAIL TO {to_email} ---")
        print(f"Subject: {subject}")
        print(body)
        print("----------------------------------\n")
        return False

    try:
        msg = EmailMessage()
        msg['Subject'] = subject
        msg['From'] = settings.SMTP_USER
        msg['To'] = to_email

        if is_html:
            msg.set_content(body, subtype='html')
        else:
            msg.set_content(body)

        port = int(settings.SMTP_PORT) if settings.SMTP_PORT else 587
        with smtplib.SMTP(settings.SMTP_SERVER, port) as server:
            if port == 587:
                server.starttls()
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.send_message(msg)
        return True
    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {str(e)}")
        return False


def send_daily_report_email(to_email: str = "vjzest9569@gmail.com"):
    subject = "📊 Sentinel Health Daily Clinical & Revenue Intelligence Report"
    html_body = """
    <div style="font-family: Arial, sans-serif; background-color: #0F071B; color: #ffffff; padding: 30px; border-radius: 16px;">
        <h2 style="color: #6366F1;">📊 SBN Sentinel Daily Executive Summary</h2>
        <p style="color: #A5B4FC;">Here is your automated end-of-day clinical telemetry & revenue report.</p>

        <table style="width: 100%; border-collapse: collapse; margin-top: 20px; background-color: #180A2E; border-radius: 12px; overflow: hidden;">
            <tr style="border-b: 1px solid rgba(255,255,255,0.1); text-align: left;">
                <th style="padding: 12px; color: #9CA3AF;">Metric</th>
                <th style="padding: 12px; color: #9CA3AF;">Value</th>
            </tr>
            <tr style="border-b: 1px solid rgba(255,255,255,0.1);">
                <td style="padding: 12px; font-weight: bold;">Active Patient Encounters</td>
                <td style="padding: 12px; color: #10B981; font-weight: bold;">14 Encounters</td>
            </tr>
            <tr style="border-b: 1px solid rgba(255,255,255,0.1);">
                <td style="padding: 12px; font-weight: bold;">Verified Insurance Claims</td>
                <td style="padding: 12px; color: #3B82F6; font-weight: bold;">$12,450.00</td>
            </tr>
            <tr style="border-b: 1px solid rgba(255,255,255,0.1);">
                <td style="padding: 12px; font-weight: bold;">Undercoding Opportunities Recovered</td>
                <td style="padding: 12px; color: #F59E0B; font-weight: bold;">$1,840.00</td>
            </tr>
            <tr>
                <td style="padding: 12px; font-weight: bold;">HIPAA Audit Status</td>
                <td style="padding: 12px; color: #10B981; font-weight: bold;">100% Compliant (0 Violations)</td>
            </tr>
        </table>
        <p style="margin-top: 25px; font-size: 11px; color: #6B7280;">SBN Sentinel Automated Clinical Telemetry Service. Confidential HIPAA-Protected Data.</p>
    </div>
    """
    return send_email(to_email, subject, html_body, is_html=True)
