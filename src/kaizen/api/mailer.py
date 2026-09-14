"""Sending the one-time sign-in code over SMTP.

The only outbound connection Kaizen makes. It is configured entirely by environment variables so that a
checkout with none of them set cannot mail anything, and `KAIZEN_OTP_DEV_MODE=1` skips SMTP altogether and
logs the code instead — that is how the demo and the tests sign in without a mail server.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

log = logging.getLogger(__name__)

SUBJECT = "Your Kaizen Cross-Check sign-in code"
TTL_MINUTES = 10


def dev_mode() -> bool:
    """Skip SMTP and log codes. Never true unless the environment asks for it explicitly."""
    return os.environ.get("KAIZEN_OTP_DEV_MODE") == "1"


def _body(code: str) -> str:
    return (
        f"Your Kaizen Cross-Check sign-in code is {code}\n\n"
        f"The code expires in {TTL_MINUTES} minutes and can be used once.\n"
        "If you did not ask to sign in, ignore this message: nobody can sign in without this code.\n"
    )


def send_otp_email(to: str, code: str) -> None:
    """Mail `code` to `to`. Raises RuntimeError when SMTP is not configured or the send fails."""
    if dev_mode():
        log.warning("KAIZEN_OTP_DEV_MODE: sign-in code for %s is %s (not emailed)", to, code)
        return

    host = os.environ.get("KAIZEN_SMTP_HOST")
    sender = os.environ.get("KAIZEN_SMTP_FROM")
    if not host or not sender:
        raise RuntimeError("Email sign-in is not configured on this server: set KAIZEN_SMTP_HOST and KAIZEN_SMTP_FROM.")

    msg = EmailMessage()
    msg["Subject"] = SUBJECT
    msg["From"] = sender
    msg["To"] = to
    msg.set_content(_body(code))

    port = int(os.environ.get("KAIZEN_SMTP_PORT", "587"))
    user = os.environ.get("KAIZEN_SMTP_USER")
    password = os.environ.get("KAIZEN_SMTP_PASSWORD")
    try:
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            smtp.starttls()
            if user and password:
                smtp.login(user, password)
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException) as e:
        # The address and the failure, never the code.
        log.error("could not send the sign-in code to %s: %s", to, e)
        raise RuntimeError("Could not send the sign-in code. Try again, or ask an administrator to check the mail settings.") from e
