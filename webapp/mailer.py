"""Email delivery. Inert unless SMTP is configured, and says so."""
from __future__ import annotations
import os, smtplib, ssl, mimetypes
from email.message import EmailMessage
from typing import List, Optional


def configured() -> bool:
    return bool(os.environ.get("FBC_SMTP_HOST") and os.environ.get("FBC_MAIL_FROM"))


def status() -> str:
    if configured():
        return f"SMTP {os.environ['FBC_SMTP_HOST']} as {os.environ['FBC_MAIL_FROM']}"
    return ("not configured — set FBC_SMTP_HOST, FBC_SMTP_PORT, FBC_SMTP_USER, "
            "FBC_SMTP_PASS and FBC_MAIL_FROM to enable")


def send_review(to: List[str], subject: str, body: str,
                attachment: Optional[str] = None) -> str:
    """Returns a human-readable result. Never raises into the job runner."""
    if not to:
        return "no recipients"
    if not configured():
        return "skipped — SMTP not configured on this server"
    msg = EmailMessage()
    msg["From"] = os.environ["FBC_MAIL_FROM"]
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg.set_content(body)
    if attachment and os.path.exists(attachment):
        if os.path.getsize(attachment) > 20 * 1024 * 1024:
            msg.set_content(body + "\n\nThe marked-up PDF exceeded the 20 MB attachment "
                                   "limit — use the download link above.")
        else:
            ctype, _ = mimetypes.guess_type(attachment)
            maintype, subtype = (ctype or "application/pdf").split("/", 1)
            with open(attachment, "rb") as fh:
                msg.add_attachment(fh.read(), maintype=maintype, subtype=subtype,
                                   filename=os.path.basename(attachment))
    host = os.environ["FBC_SMTP_HOST"]
    port = int(os.environ.get("FBC_SMTP_PORT", 587))
    user = os.environ.get("FBC_SMTP_USER")
    pw = os.environ.get("FBC_SMTP_PASS")
    try:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls(context=ssl.create_default_context())
            if user:
                s.login(user, pw or "")
            s.send_message(msg)
        return f"sent to {', '.join(to)}"
    except Exception as exc:
        return f"failed: {type(exc).__name__}: {exc}"
