"""
Thin SMTP mailer for Lenticularis transactional notifications.

Supports any STARTTLS SMTP server (Proton Mail for dev, Resend/Brevo for prod).
Switch providers by changing the ``smtp`` section in config.yml — no code changes needed.
"""
from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from lenticularis.config import SmtpConfig

logger = logging.getLogger(__name__)


def send_email(
    cfg: SmtpConfig,
    to_address: str,
    subject: str,
    body_text: str,
    body_html: str | None = None,
) -> bool:
    """
    Send a single email via STARTTLS SMTP.

    Returns ``True`` on success, ``False`` on any error (errors are logged but
    never raised — notifications are best-effort and must not crash the caller).
    """
    if not cfg.enabled:
        logger.debug("SMTP disabled — skipping email to %s: %s", to_address, subject)
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{cfg.from_name} <{cfg.from_address}>"
        msg["To"] = to_address

        msg.attach(MIMEText(body_text, "plain", "utf-8"))
        if body_html:
            msg.attach(MIMEText(body_html, "html", "utf-8"))

        with smtplib.SMTP(cfg.host, cfg.port, timeout=cfg.timeout_seconds) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(cfg.user, cfg.password)
            server.sendmail(cfg.from_address, [to_address], msg.as_string())

        logger.info("Email sent to %s: %s", to_address, subject)
        return True

    except Exception as exc:
        logger.error("Failed to send email to %s (%s): %s", to_address, subject, exc)
        return False
