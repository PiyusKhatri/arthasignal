from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from src.config import settings

logger = logging.getLogger(__name__)


def password_reset_email_configured() -> bool:
    return bool(settings.smtp_host and settings.smtp_from_email)


def send_password_reset_email(recipient: str, reset_url: str) -> bool:
    """Send a password-reset link without ever logging the bearer token.

    Returns False when SMTP is not configured or delivery fails. Callers should
    keep their public response generic so account existence is never revealed.
    """

    if not password_reset_email_configured():
        logger.error("Password reset email requested but SMTP is not configured")
        return False

    message = EmailMessage()
    message["Subject"] = "Reset your ArthaSignal password"
    message["From"] = settings.smtp_from_email
    message["To"] = recipient
    message.set_content(
        "A password reset was requested for your ArthaSignal account.\n\n"
        f"Use this link within 30 minutes:\n{reset_url}\n\n"
        "If you did not request this reset, you can ignore this email."
    )

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password or "")
            smtp.send_message(message)
    except Exception:
        logger.exception("Password reset email delivery failed")
        return False

    return True
