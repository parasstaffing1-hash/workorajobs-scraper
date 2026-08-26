"""Email sender for Workora Jobs alerts and notifications."""
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime


SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
FROM_EMAIL = os.environ.get("ALERT_FROM", "Workora Jobs <alerts@workorajobs.com>")


def send_email(to: str, subject: str, html_body: str, text_body: str = None) -> bool:
    """Send an email via SMTP."""
    if not SMTP_USER or not SMTP_PASS:
        print(f"[EMAIL-SKIP] No SMTP configured. Would send to {to}: {subject}")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = FROM_EMAIL
        msg["To"] = to
        msg["Subject"] = subject

        if text_body:
            msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(FROM_EMAIL, to, msg.as_string())
        print(f"[EMAIL] Sent to {to}: {subject}")
        return True
    except Exception as e:
        print(f"[EMAIL-ERROR] {e}")
        return False


def send_alert_email(to: str, alert_name: str, matches: list[dict], total_new: int) -> bool:
    """Send a job alert email with matching jobs."""
    job_rows = ""
    for job in matches[:10]:
        title = job.get("title", "Unknown")
        company = job.get("company", "Unknown")
        location = job.get("location", "Remote")
        url = job.get("url", "#")
        salary = job.get("salary", "")
        salary_html = f'<span style="color:#10b981;font-weight:600">{salary}</span>' if salary else ''

        job_rows += f"""
        <tr>
            <td style="padding:12px;border-bottom:1px solid #e2e8f0">
                <a href="{url}" style="color:#2563eb;font-weight:600;text-decoration:none">{title}</a>
                <br><span style="color:#64748b;font-size:13px">{company} | {location}</span>
                {f'<br>{salary_html}' if salary else ''}
            </td>
        </tr>
        """

    html = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#f8fafc;padding:20px">
    <div style="max-width:600px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.1)">
        <div style="background:linear-gradient(135deg,#2563eb,#8b5cf6);padding:24px;text-align:center">
            <h1 style="color:#fff;margin:0;font-size:24px">🚀 Workora Jobs Alert</h1>
            <p style="color:rgba(255,255,255,0.8);margin:8px 0 0">New jobs matching "{alert_name}"</p>
        </div>
        <div style="padding:24px">
            <p style="font-size:16px;color:#1e293b">
                Found <strong>{total_new} new jobs</strong> matching your alert "<strong>{alert_name}</strong>"
                {f'with keywords: {", ".join(m.strip() for m in matches[0].get("tags","").split(",")[:3])}' if matches and matches[0].get('tags') else ''}
            </p>

            <table style="width:100%;border-collapse:collapse;margin:16px 0">
                {job_rows}
            </table>

            {f'<p style="text-align:center;margin:20px 0"><a href="https://workorajobs.com/alerts" style="background:#2563eb;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600">View All Matching Jobs →</a></p>' if total_new > 10 else ''}

            <hr style="border:none;border-top:1px solid #e2e8f0;margin:20px 0">
            <p style="color:#64748b;font-size:12px;text-align:center">
                You're receiving this because you set up a job alert on Workora Jobs.<br>
                <a href="https://workorajobs.com/alerts" style="color:#2563eb">Manage alerts</a> |
                <a href="https://workorajobs.com/unsubscribe?email={to}" style="color:#2563eb">Unsubscribe</a>
            </p>
        </div>
    </div>
    </body>
    </html>
    """

    text = f"Workora Jobs Alert: {alert_name}\n\n{total_new} new jobs found!\n\n"
    for job in matches[:10]:
        text += f"- {job.get('title', 'Unknown')} at {job.get('company', 'Unknown')} ({job.get('location', '')})\n"
        text += f"  {job.get('url', '')}\n\n"

    return send_email(to, f"🚀 {total_new} new jobs: {alert_name} | Workora Jobs", html, text)


def send_welcome_email(to: str, username: str) -> bool:
    """Send welcome email to new users."""
    html = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#f8fafc;padding:20px">
    <div style="max-width:600px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.1)">
        <div style="background:linear-gradient(135deg,#2563eb,#8b5cf6);padding:24px;text-align:center">
            <h1 style="color:#fff;margin:0">Welcome to Workora Jobs! 🎉</h1>
        </div>
        <div style="padding:24px">
            <p style="font-size:16px;color:#1e293b">Hi <strong>{username}</strong>,</p>
            <p>Welcome to Workora Jobs! You now have access to 700K+ job listings from 20+ platforms.</p>
            <h3 style="color:#2563eb">Get started:</h3>
            <ul style="color:#1e293b">
                <li>🔍 <a href="https://workorajobs.com/jobs">Search jobs</a> by keyword, location, or salary</li>
                <li>❤️ <a href="https://workorajobs.com/saved">Save jobs</a> you're interested in</li>
                <li>🔔 Set up <a href="https://workorajobs.com/alerts">job alerts</a> to get notified daily</li>
                <li>📊 Check <a href="https://workorajobs.com/salary">salary data</a> for your skills</li>
            </ul>
            <p style="text-align:center;margin:24px 0">
                <a href="https://workorajobs.com/jobs" style="background:#2563eb;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600">Start Searching →</a>
            </p>
        </div>
    </div>
    </body>
    </html>
    """
    return send_email(to, "Welcome to Workora Jobs! 🚀", html)
