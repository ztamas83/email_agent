import os
import ssl
import smtplib
from email.message import EmailMessage
from dotenv import load_dotenv

# Try loading from .env, but do NOT override environment variables already present
load_dotenv(override=False)

BRIDGE_HOST = os.getenv("BRIDGE_HOST", "127.0.0.1")
SMTP_PORT = int(os.getenv("SMTP_PORT") or 1025)
BRIDGE_USER = os.getenv("PROTON_USER")
BRIDGE_PASS = os.getenv("PROTON_PASS")
FORWARD_DEFAULT_TO = os.getenv("FORWARD_DEFAULT_TO")

def forward_message(original_msg, target_email: str = None):
    recipient = target_email or FORWARD_DEFAULT_TO
    if not recipient:
        raise ValueError("No destination forward address configured.")

    fwd = EmailMessage()
    fwd["Subject"] = f"Fwd: {original_msg.subject}"
    fwd["From"] = BRIDGE_USER
    fwd["To"] = recipient
    
    body = original_msg.text or original_msg.html or "(No text body)"
    fwd.set_content(
        f"--- Auto-Forwarded by AI Triage Worker ---\n"
        f"Original From: {original_msg.from_}\n"
        f"Original Date: {original_msg.date_str}\n"
        f"Original Subject: {original_msg.subject}\n\n"
        f"{body}"
    )

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # Proton Bridge uses self-signed localhost certs

    with smtplib.SMTP(BRIDGE_HOST, SMTP_PORT) as server:
        try:
            server.starttls(context=ctx)
        except smtplib.SMTPNotSupportedError:
            # Server does not support STARTTLS (e.g., local mock mail server)
            pass
        server.login(BRIDGE_USER, BRIDGE_PASS)
        server.send_message(fwd)
    
    return recipient
