from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage


class NotifyError(RuntimeError):
    pass


@dataclass(frozen=True)
class EmailConfig:
    host: str
    port: int
    username: str
    password: str
    sender: str
    recipients: list[str]
    use_ssl: bool = True

    @classmethod
    def from_env(cls) -> "EmailConfig":
        host = os.getenv("SMTP_HOST", "").strip()
        username = os.getenv("SMTP_USERNAME", "").strip()
        password = os.getenv("SMTP_PASSWORD", "").strip()
        sender = os.getenv("EMAIL_FROM", "").strip() or username
        recipients = [
            item.strip()
            for item in os.getenv("EMAIL_TO", "").replace(";", ",").split(",")
            if item.strip()
        ]
        if not host:
            raise NotifyError("SMTP_HOST is not set in .env or environment.")
        if not username:
            raise NotifyError("SMTP_USERNAME is not set in .env or environment.")
        if not password:
            raise NotifyError("SMTP_PASSWORD is not set in .env or environment.")
        if not sender:
            raise NotifyError("EMAIL_FROM is not set and SMTP_USERNAME is empty.")
        if not recipients:
            raise NotifyError("EMAIL_TO is not set in .env or environment.")

        port = int(os.getenv("SMTP_PORT", "465").strip() or "465")
        use_ssl = os.getenv("SMTP_USE_SSL", "true").strip().lower() not in {"0", "false", "no"}
        return cls(
            host=host,
            port=port,
            username=username,
            password=password,
            sender=sender,
            recipients=recipients,
            use_ssl=use_ssl,
        )


@dataclass(frozen=True)
class EmailNotifier:
    config: EmailConfig

    @classmethod
    def from_env(cls) -> "EmailNotifier":
        return cls(config=EmailConfig.from_env())

    def send(self, title: str, content: str) -> None:
        message = EmailMessage()
        message["Subject"] = title
        message["From"] = self.config.sender
        message["To"] = ", ".join(self.config.recipients)
        message.set_content(content, subtype="plain", charset="utf-8")

        try:
            if self.config.use_ssl:
                with smtplib.SMTP_SSL(self.config.host, self.config.port, timeout=30) as smtp:
                    smtp.login(self.config.username, self.config.password)
                    smtp.send_message(message)
            else:
                with smtplib.SMTP(self.config.host, self.config.port, timeout=30) as smtp:
                    smtp.starttls()
                    smtp.login(self.config.username, self.config.password)
                    smtp.send_message(message)
        except OSError as exc:
            raise NotifyError(f"Email send failed: {exc}") from exc
        except smtplib.SMTPException as exc:
            raise NotifyError(f"SMTP send failed: {exc}") from exc

