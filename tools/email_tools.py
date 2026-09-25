"""
Email tools: send email (SMTP) and read your recent inbox (IMAP, read-only).

Free with Gmail (or most other providers). For Gmail:
  1. Turn on 2-Step Verification in your Google account.
  2. Create an "App password" at https://myaccount.google.com/apppasswords
  3. Put your address and that app password in .env:
        EMAIL_ADDRESS=you@gmail.com
        EMAIL_PASSWORD=abcd efgh ijkl mnop
The password stays in your local .env file (never committed to git).
Sending always asks for your approval.
"""

import email
import imaplib
import smtplib
from email.message import EmailMessage
from email.policy import default as default_policy

from tools.base import Tool, ToolError
from utils.text import truncate

NOT_CONFIGURED = (
    "Email is not set up. Add EMAIL_ADDRESS and EMAIL_PASSWORD (an app password) to .env. "
    "See the README section 'Email setup'."
)


class SendEmail(Tool):
    name = "send_email"
    description = "Send an email from the user's email account. The user must approve it."
    parameters = {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient address(es), comma-separated"},
            "subject": {"type": "string", "description": "Subject line"},
            "body": {"type": "string", "description": "Plain-text message"},
        },
        "required": ["to", "subject", "body"],
    }
    requires_confirmation = True

    def describe(self, arguments: dict) -> str:
        return (
            f"Send email\nFrom: {self.config.email_address or '(not configured)'}\n"
            f"To: {arguments.get('to', '')}\nSubject: {arguments.get('subject', '')}\n\n"
            f"{truncate(str(arguments.get('body', '')), 1500)}"
        )

    def run(self, to: str, subject: str, body: str) -> str:
        if not self.config.email_configured:
            raise ToolError(NOT_CONFIGURED)
        message = EmailMessage()
        message["From"] = self.config.email_address
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)

        host, port = self.config.smtp_host, self.config.smtp_port
        try:
            if port == 465:
                server = smtplib.SMTP_SSL(host, port, timeout=30)
            else:
                server = smtplib.SMTP(host, port, timeout=30)
            with server:
                if port != 465:
                    server.starttls()
                server.login(self.config.email_address, self.config.email_password)
                server.send_message(message)
        except smtplib.SMTPAuthenticationError:
            raise ToolError("The email server rejected the login. Check EMAIL_ADDRESS / EMAIL_PASSWORD (use an app password).")
        except (smtplib.SMTPException, OSError) as error:
            raise ToolError(f"Could not send the email: {error}")
        return f"Email sent to {to} with subject '{subject}'."


def _plain_text(message) -> str:
    """The readable text part of an email."""
    part = message.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        text = part.get_content()
    except (LookupError, UnicodeDecodeError):
        return ""
    if part.get_content_type() == "text/html":
        from tools.web import strip_tags  # local import keeps modules independent
        text = strip_tags(text)
    return " ".join(text.split())


class ReadEmails(Tool):
    name = "read_emails"
    description = "Read the most recent emails in the user's inbox (read-only; does not mark them as read)."
    parameters = {
        "type": "object",
        "properties": {
            "count": {"type": "integer", "description": "How many recent emails (1-20, default 5)"},
            "unread_only": {"type": "boolean", "description": "Only unread emails"},
        },
        "required": [],
    }

    def run(self, count: int = 5, unread_only: bool = False) -> str:
        if not self.config.email_configured:
            raise ToolError(NOT_CONFIGURED)
        count = max(1, min(int(count or 5), 20))
        try:
            with imaplib.IMAP4_SSL(self.config.imap_host, timeout=30) as mailbox:
                mailbox.login(self.config.email_address, self.config.email_password)
                mailbox.select("INBOX", readonly=True)
                status, data = mailbox.search(None, "UNSEEN" if unread_only else "ALL")
                if status != "OK":
                    raise ToolError("Could not search the inbox")
                ids = data[0].split()[-count:]
                summaries = []
                for message_id in reversed(ids):
                    status, parts = mailbox.fetch(message_id, "(BODY.PEEK[])")
                    if status != "OK" or not parts or not isinstance(parts[0], tuple):
                        continue
                    message = email.message_from_bytes(parts[0][1], policy=default_policy)
                    summaries.append(
                        f"From: {message['from']}\nDate: {message['date']}\n"
                        f"Subject: {message['subject']}\n{truncate(_plain_text(message), 400)}"
                    )
        except imaplib.IMAP4.error as error:
            raise ToolError(f"The email server refused: {error}. Check your app password and that IMAP is enabled.")
        except OSError as error:
            raise ToolError(f"Could not connect to {self.config.imap_host}: {error}")
        if not summaries:
            return "No emails found."
        return f"{len(summaries)} most recent email(s):\n\n" + "\n\n---\n\n".join(summaries)
