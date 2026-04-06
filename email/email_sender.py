"""Email utility for sending mode-change notifications.

Uses Gmail SMTP with a resilient transport strategy:
1) SMTP over SSL (465)
2) SMTP with STARTTLS (587) fallback
"""

import smtplib
import ssl
from email.message import EmailMessage


class EmailSender:
    """Simple Gmail sender wrapper used by scheduler notifications."""

    def __init__(self, sender_gmail: str, gmail_app_password: str):
        """Initialize sender credentials.

        Args:
            sender_gmail: Gmail address used as sender.
            gmail_app_password: Gmail app password for SMTP auth.
        """
        self.sender_gmail = sender_gmail
        self.password = gmail_app_password

    def send(self, receiver_email: str, subject: str, text: str):
        """Send an email message.

        Args:
            receiver_email: Destination email address.
            subject: Message subject.
            text: Plain-text body.

        Raises:
            RuntimeError: If all SMTP connection strategies fail.
        """
        smtp_server = "smtp.gmail.com"
        timeout_seconds = 30
        context = ssl.create_default_context()

        msg = EmailMessage()
        msg["From"] = self.sender_gmail
        msg["To"] = receiver_email
        msg["Subject"] = subject
        msg.set_content(text)

        errors: list[str] = []

        # Primary: SMTP over implicit TLS (port 465).
        try:
            with smtplib.SMTP_SSL(
                smtp_server,
                465,
                context=context,
                timeout=timeout_seconds,
            ) as server:
                server.login(self.sender_gmail, self.password)
                server.send_message(msg)
                return
        except Exception as exc:
            errors.append(f"SMTP_SSL: {exc}")

        # Fallback: SMTP + STARTTLS (port 587).
        try:
            with smtplib.SMTP(smtp_server, 587, timeout=timeout_seconds) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                server.login(self.sender_gmail, self.password)
                server.send_message(msg)
                return
        except Exception as exc:
            errors.append(f"SMTP_STARTTLS: {exc}")

        raise RuntimeError("Failed to send email via Gmail SMTP. " + " | ".join(errors))

