"""
email_service.py

Sends email over SMTP. Configure it in the project's .env file:

    SMTP_HOST=smtp.gmail.com
    SMTP_PORT=587
    SMTP_USERNAME=you@gmail.com
    SMTP_PASSWORD=your-app-password
    EMAIL_FROM=Game Night <you@gmail.com>

    # Optional: the address used in links inside emails.
    APP_BASE_URL=http://127.0.0.1:8000

For Gmail, SMTP_PASSWORD must be an "app password"
(Google Account > Security > 2-Step Verification > App passwords),
not your normal Gmail password.

If SMTP_HOST isn't set, emails are printed to the terminal instead of
being sent, so the feature can be tried out without any setup.
"""

import logging
import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

logger = logging.getLogger(__name__)

# Deliberately simple: one "@", no spaces or newlines, a dot in the domain.
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_valid_email(address: str) -> bool:
    return len(address) <= 254 and bool(EMAIL_PATTERN.match(address))


def app_base_url(fallback: str) -> str:
    """Base URL for links in emails: APP_BASE_URL, else the request's."""
    return (os.getenv("APP_BASE_URL") or fallback).rstrip("/")


def send_email(to: str, subject: str, text: str, html: str | None = None) -> bool:
    """Send one email. Returns True on success; never raises."""
    host = os.getenv("SMTP_HOST")

    if not host:
        print(
            "\n[email] SMTP_HOST is not set, so this email was not sent.\n"
            f"  To:      {to}\n"
            f"  Subject: {subject}\n"
            f"{text}\n"
        )
        return True

    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USERNAME")
    password = os.getenv("SMTP_PASSWORD")
    sender = os.getenv("EMAIL_FROM") or username

    message = EmailMessage()
    message["From"] = sender
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text)

    if html:
        message.add_alternative(html, subtype="html")

    try:
        context = ssl.create_default_context()

        # Port 465 uses SSL from the start; others upgrade with STARTTLS.
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=context, timeout=20) as smtp:
                if username:
                    smtp.login(username, password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=20) as smtp:
                smtp.starttls(context=context)
                if username:
                    smtp.login(username, password)
                smtp.send_message(message)

        return True

    except (smtplib.SMTPException, OSError):
        logger.exception("Failed to send email to %s", to)
        return False
