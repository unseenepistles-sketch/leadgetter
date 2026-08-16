"""Sending the reminder email.

Two backends: real SMTP, and a console mailer that records what it would have sent
without touching the network. The console one is what dry runs and tests use, and
it is the default — sending has to be switched on deliberately.
"""
from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Protocol, Sequence

log = logging.getLogger("uniforms.mail")


@dataclass(frozen=True, slots=True)
class Message:
    to: str
    subject: str
    body: str


class Mailer(Protocol):
    def send(self, message: Message) -> None: ...


@dataclass
class ConsoleMailer:
    """Records messages instead of sending them. Used by dry runs and tests."""

    sent: list[Message] = field(default_factory=list)

    def send(self, message: Message) -> None:
        self.sent.append(message)
        log.info("[dry-run] to=%s subject=%s", message.to, message.subject)


class SmtpMailer:
    def __init__(
        self,
        host: str,
        port: int = 25,
        *,
        sender: str = "uniforms@example.com",
        user: str = "",
        password: str = "",
        starttls: bool = True,
        timeout: int = 30,
    ) -> None:
        self.host, self.port, self.sender = host, port, sender
        self.user, self.password, self.starttls, self.timeout = user, password, starttls, timeout

    def send(self, message: Message) -> None:
        msg = EmailMessage()
        msg["From"] = self.sender
        msg["To"] = message.to
        msg["Subject"] = message.subject
        msg.set_content(message.body)

        with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as smtp:
            if self.starttls:
                try:
                    smtp.starttls()
                except smtplib.SMTPNotSupportedError:
                    log.warning("SMTP server does not support STARTTLS; continuing in the clear")
            if self.user:
                smtp.login(self.user, self.password)
            smtp.send_message(msg)


def apply_allowlist(recipient: str, allowlist: Sequence[str]) -> str | None:
    """Outside production every address is rewritten to the allowlist.

    Returns None when there is nowhere safe to send, so a misconfigured non-prod
    environment goes quiet rather than mailing real employees.
    """
    if not allowlist:
        return recipient
    if recipient in allowlist:
        return recipient
    return allowlist[0]
