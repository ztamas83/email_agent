import os
import ssl
import time
from dotenv import load_dotenv
from imap_tools import MailBox, AND

# Try loading from .env, but do NOT override environment variables already present
load_dotenv(override=False)

from db import init_db, record_audit, is_uid_processed
from actions import forward_message, FORWARD_DEFAULT_TO
from classifier import classify_email

BRIDGE_HOST = os.getenv("BRIDGE_HOST", "127.0.0.1")
IMAP_PORT = int(os.getenv("IMAP_PORT") or 1143)
BRIDGE_USER = os.getenv("PROTON_USER")
BRIDGE_PASS = os.getenv("PROTON_PASS")
DRY_RUN = os.getenv("DRY_RUN", "false").lower() in ("true", "1", "yes", "t")

def get_ssl_context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

def process_message(mailbox: MailBox, msg, dry_run: bool = DRY_RUN):
    if is_uid_processed(msg.uid, is_dry_run=dry_run):
        print(f"[-] UID {msg.uid} already processed ({'dry-run' if dry_run else 'live'}). Skipping.")
        return

    mode_prefix = "[DRY-RUN] " if dry_run else ""
    print(f"[*] {mode_prefix}Processing UID {msg.uid}: '{msg.subject}' from {msg.from_}")
    decision = classify_email(msg)
    executed = []

    if dry_run:
        # Simulate actions without mutating mailbox or sending emails
        if decision.should_forward:
            recipient = decision.forward_to or FORWARD_DEFAULT_TO or "system-default"
            executed.append(f"would_forward:{recipient}")

        if decision.apply_folder:
            executed.append(f"would_move:{decision.apply_folder}")

        if decision.mark_as_read:
            executed.append("would_mark_read")

        status_str = f"[dry-run] {', '.join(executed)}" if executed else "[dry-run] no_action"
    else:
        # 1. Forward
        if decision.should_forward:
            recipient = forward_message(msg, decision.forward_to)
            executed.append(f"forwarded:{recipient}")

        # 2. Folder Move
        if decision.apply_folder:
            try:
                mailbox.folder.create(decision.apply_folder)
            except Exception:
                pass
            mailbox.move(msg.uid, decision.apply_folder)
            executed.append(f"moved:{decision.apply_folder}")

        # 3. Mark Seen
        if decision.mark_as_read:
            mailbox.flag(msg.uid, '\\Seen', True)
            executed.append("marked_read")

        status_str = ", ".join(executed) if executed else "no_action"

    record_audit(msg.uid, msg.from_, msg.subject, decision, status_str, is_dry_run=dry_run)
    print(f"  └─► Actions: [{status_str}] | Reason: {decision.reasoning}")

def drain_unread(mailbox: MailBox, dry_run: bool = DRY_RUN):
    for msg in mailbox.fetch(AND(seen=False), reverse=True):
        process_message(mailbox, msg, dry_run=dry_run)

def run_daemon(dry_run: bool = DRY_RUN):
    init_db()
    ssl_ctx = get_ssl_context()
    print("[*] Starting Proton Mail Push Triage Daemon (IMAP IDLE)...")
    if dry_run:
        print("[*] DRY-RUN mode ACTIVE: Mailbox modifications and email forwarding are DISABLED.")
    else:
        print("[*] LIVE mode ACTIVE: Actions will be executed on mailbox.")

    if not BRIDGE_USER or not BRIDGE_PASS:
        print("[!] Warning: PROTON_USER or PROTON_PASS is not configured in environment or .env file.")

    while True:
        try:
            with MailBox(BRIDGE_HOST, port=IMAP_PORT, ssl_context=ssl_ctx).login(BRIDGE_USER, BRIDGE_PASS, 'INBOX') as mailbox:
                print("[*] Connected to Bridge. Checking initial unread backlog...")
                drain_unread(mailbox, dry_run=dry_run)

                print("[*] Entering IDLE state. Awaiting new mail push events...")
                while True:
                    responses = mailbox.idle.wait(timeout=29 * 60)
                    if responses and any('EXISTS' in str(r) for r in responses):
                        print("[!] IMAP Push Notification received.")
                    drain_unread(mailbox, dry_run=dry_run)

        except Exception as e:
            print(f"[!] IMAP Connection interrupted: {e}. Reconnecting in 10s...")
            time.sleep(10)

if __name__ == "__main__":
    run_daemon()
