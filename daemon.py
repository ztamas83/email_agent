import os
import sys
import ssl
import time
import datetime
from typing import Optional
from dotenv import load_dotenv

# Ensure immediate unbuffered output so logs appear in real-time under systemd / journalctl
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(line_buffering=True)

from imap_tools import MailBox, MailBoxStartTls, MailBoxUnencrypted, MailboxStarttlsError, AND

# Try loading from .env, but do NOT override environment variables already present
load_dotenv(override=False)

from db import init_db, record_audit, is_uid_processed
from actions import forward_message, FORWARD_DEFAULT_TO
from email_classifier import EmailClassifier
from gemini_classifier import GeminiEmailClassifier
from jev_classifier import JevEmailClassifier

BRIDGE_HOST = os.getenv("BRIDGE_HOST", "127.0.0.1")
IMAP_PORT = int(os.getenv("IMAP_PORT") or 1143)
BRIDGE_USER = os.getenv("PROTON_USER")
BRIDGE_PASS = os.getenv("PROTON_PASS")
DRY_RUN = os.getenv("DRY_RUN", "false").lower() in ("true", "1", "yes", "t")
IMAP_SECURITY = os.getenv("IMAP_SECURITY", "auto").lower()

# HISTORY_DAYS: controls how many days back to process unread emails.
# 0 = entirely skip history (only process new emails arriving after startup).
# None / unset / "all" = process all unread history.
HISTORY_DAYS_RAW = os.getenv("HISTORY_DAYS")
HISTORY_DAYS: Optional[int] = None
if HISTORY_DAYS_RAW is not None and HISTORY_DAYS_RAW.strip() != "":
    val = HISTORY_DAYS_RAW.strip().lower()
    if val not in ("all", "none", "-1"):
        try:
            HISTORY_DAYS = int(val)
            if HISTORY_DAYS < 0:
                HISTORY_DAYS = None
        except ValueError:
            print(f"[!] Warning: Invalid HISTORY_DAYS value '{HISTORY_DAYS_RAW}'. Processing all history.")
            HISTORY_DAYS = None

def get_ssl_context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

def get_max_uid(mailbox: MailBox) -> int:
    try:
        uids = mailbox.uids()
        numeric_uids = []
        for u in uids:
            try:
                numeric_uids.append(int(u))
            except (ValueError, TypeError):
                pass
        return max(numeric_uids) if numeric_uids else 0
    except Exception as e:
        print(f"[!] Warning: Failed to retrieve mailbox UIDs: {e}")
        return 0

def get_fetch_criteria(history_days: Optional[int] = HISTORY_DAYS):
    if history_days is not None and history_days > 0:
        cutoff_date = datetime.date.today() - datetime.timedelta(days=history_days)
        return AND(seen=False, date_gte=cutoff_date)
    return AND(seen=False)

def connect_mailbox(
    host: str = BRIDGE_HOST,
    port: int = IMAP_PORT,
    user: str = BRIDGE_USER,
    pass_: str = BRIDGE_PASS,
    folder: str = 'INBOX',
    security: str = IMAP_SECURITY,
    ssl_ctx=None
):
    if ssl_ctx is None:
        ssl_ctx = get_ssl_context()

    sec = security.lower()
    if sec == "starttls":
        candidates = [("STARTTLS", lambda: MailBoxStartTls(host, port=port, ssl_context=ssl_ctx))]
    elif sec in ("ssl", "tls"):
        candidates = [("SSL/TLS", lambda: MailBox(host, port=port, ssl_context=ssl_ctx))]
    elif sec in ("plain", "none", "unencrypted"):
        candidates = [("Plaintext", lambda: MailBoxUnencrypted(host, port=port))]
    else:  # "auto": prioritize STARTTLS for port 1143/143, SSL/TLS for 993
        if port == 993 or port == 1993:
            candidates = [
                ("SSL/TLS", lambda: MailBox(host, port=port, ssl_context=ssl_ctx)),
                ("STARTTLS", lambda: MailBoxStartTls(host, port=port, ssl_context=ssl_ctx)),
            ]
        else:
            candidates = [
                ("STARTTLS", lambda: MailBoxStartTls(host, port=port, ssl_context=ssl_ctx)),
                ("SSL/TLS", lambda: MailBox(host, port=port, ssl_context=ssl_ctx)),
                ("Plaintext", lambda: MailBoxUnencrypted(host, port=port)),
            ]

    last_error = None
    for mode_name, factory in candidates:
        try:
            mb = factory()
            mb.login(user, pass_, folder)
            return mb, mode_name
        except (ssl.SSLError, MailboxStarttlsError, ValueError, ConnectionResetError, OSError) as err:
            last_error = err
            continue
    raise last_error

def process_message(
    mailbox: MailBox,
    msg,
    dry_run: bool = DRY_RUN,
    classifier: Optional[EmailClassifier] = None,
):
    if is_uid_processed(msg.uid, is_dry_run=dry_run):
        print(f"[-] UID {msg.uid} already processed ({'dry-run' if dry_run else 'live'}). Skipping.")
        return

    if classifier is None:
        classifier = JevEmailClassifier()

    mode_prefix = "[DRY-RUN] " if dry_run else ""
    print(f"[*] {mode_prefix}Processing UID {msg.uid}: '{msg.subject}' from {msg.from_}")
    decision = classifier.classify_email(msg)
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

def drain_unread(
    mailbox: MailBox,
    dry_run: bool = DRY_RUN,
    history_days: Optional[int] = HISTORY_DAYS,
    min_uid: Optional[int] = None,
    classifier: Optional[EmailClassifier] = None,
):
    criteria = get_fetch_criteria(history_days)
    cutoff_date = (
        datetime.date.today() - datetime.timedelta(days=history_days)
        if history_days is not None and history_days > 0
        else None
    )

    if classifier is None:
        classifier = GeminiEmailClassifier()

    for msg in mailbox.fetch(criteria, reverse=True):
        # If min_uid is specified (e.g. HISTORY_DAYS=0), skip messages that existed prior to startup
        if min_uid is not None:
            try:
                if int(msg.uid) <= min_uid:
                    continue
            except (ValueError, TypeError):
                pass

        # If history_days > 0, verify message date against cutoff
        if cutoff_date is not None and msg.date:
            msg_d = msg.date.date() if hasattr(msg.date, "date") else msg.date
            if msg_d < cutoff_date:
                continue

        process_message(mailbox, msg, dry_run=dry_run, classifier=classifier)

def run_daemon(dry_run: bool = DRY_RUN, history_days: Optional[int] = HISTORY_DAYS):
    init_db()
    ssl_ctx = get_ssl_context()
    classifier = GeminiEmailClassifier()
    print("[*] Starting Proton Mail Push Triage Daemon (IMAP IDLE)...")
    if dry_run:
        print("[*] DRY-RUN mode ACTIVE: Mailbox modifications and email forwarding are DISABLED.")
    else:
        print("[*] LIVE mode ACTIVE: Actions will be executed on mailbox.")

    if history_days == 0:
        print("[*] HISTORY_DAYS=0: Existing email history is SKIPPED. Only new emails arriving after startup will be processed.")
    elif history_days is not None:
        cutoff = datetime.date.today() - datetime.timedelta(days=history_days)
        print(f"[*] HISTORY_DAYS={history_days}: Processing unread emails from the last {history_days} days (since {cutoff}). Older emails will be skipped.")
    else:
        print("[*] HISTORY_DAYS not set: Processing all unread email history.")

    if not BRIDGE_USER or not BRIDGE_PASS:
        print("[!] Warning: PROTON_USER or PROTON_PASS is not configured in environment or .env file.")

    while True:
        try:
            mailbox, sec_mode = connect_mailbox(
                BRIDGE_HOST, IMAP_PORT, BRIDGE_USER, BRIDGE_PASS, 'INBOX',
                security=IMAP_SECURITY, ssl_ctx=ssl_ctx
            )
            with mailbox:
                print(f"[*] Connected to Bridge ({sec_mode}).")

                # If history_days == 0, record baseline max UID to skip pre-existing backlog
                initial_max_uid = get_max_uid(mailbox) if history_days == 0 else None
                if history_days == 0:
                    print(f"[*] Baseline max UID: {initial_max_uid}. Existing inbox history skipped.")
                else:
                    print("[*] Checking initial unread backlog...")
                    drain_unread(mailbox, dry_run=dry_run, history_days=history_days, classifier=classifier)

                print("[*] Entering IDLE state. Awaiting new mail push events...")
                while True:
                    responses = mailbox.idle.wait(timeout=29 * 60)
                    if responses and any('EXISTS' in str(r) for r in responses):
                        print("[!] IMAP Push Notification received.")
                    drain_unread(mailbox, dry_run=dry_run, history_days=history_days, min_uid=initial_max_uid, classifier=classifier)

        except Exception as e:
            print(f"[!] IMAP Connection interrupted: {e}. Reconnecting in 10s...")
            time.sleep(10)

if __name__ == "__main__":
    run_daemon()
