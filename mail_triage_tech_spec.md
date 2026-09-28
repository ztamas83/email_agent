# Technical Specification: Deterministic Email Triage Daemon

## Objective
Build a headless, event-driven background service in Python that listens for incoming Proton Mail messages via a local Proton Mail Bridge, classifies emails into structured action intents using LangChain with Pydantic structured output, executes deterministic email actions (forwarding, folder moves, marking read), and records all operations to a local SQLite audit database.

---

## 1. System Architecture & Flow

```text
[ Proton Mail Cloud ]
       │  (E2E Push)
       ▼
[ Proton Mail Bridge ] (127.0.0.1:1143 IMAP / 127.0.0.1:1025 SMTP)
       │  (IMAP IDLE Push notification)
       ▼
[ daemon.py ] (Event-driven listener using imap-tools)
       │
       ▼ (Extract subject, sender, date, clean text body)
[ classifier.py ] (LangChain LLM with .with_structured_output(EmailAction))
       │
       ▼ (Strict Pydantic payload)
[ actions.py ] (Deterministic executor)
       ├─► SMTP: Forward email (if should_forward == True)
       ├─► IMAP: Move message to target folder / label
       └─► IMAP: Mark message as read / seen
       │
       ▼
[ db.py ] (Append record to triage_history.db SQLite)

```

---

## 2. Directory Structure

```text
/home/agentuser/mail-agent/
├── .env
├── venv/
├── requirements.txt
├── schemas.py          # Pydantic schema for structured LLM decision
├── db.py               # SQLite schema and audit logging helpers
├── actions.py          # SMTP forwarder and IMAP flag/folder mutators
├── classifier.py       # LangChain model initialization & prompt template
├── daemon.py           # Main entry point: IMAP IDLE push loop
└── inspect_logs.py     # CLI helper to view recent audit entries

```

---

## 3. Environment Variables (`.env`)

```ini
PROTON_USER="your-email@proton.me"
PROTON_PASS="bridge-16-char-password"
BRIDGE_HOST="127.0.0.1"
IMAP_PORT=1143
SMTP_PORT=1025
FORWARD_DEFAULT_TO="target-inbox@example.com"
GEMINI_API_KEY="your-api-key"
# or ANTHROPIC_API_KEY="..." if using ChatAnthropic

```

---

## 4. Dependencies (`requirements.txt`)

```text
imap-tools>=1.7.0
langchain>=0.3.0
langchain-google-genai>=2.0.0
pydantic>=2.7.0
python-dotenv>=1.0.0
tabulate>=0.9.0

```

---

## 5. Component Implementations

### `schemas.py`

```python
from typing import Literal, Optional
from pydantic import BaseModel, Field

class EmailAction(BaseModel):
    category: Literal["travel", "finance", "newsletter", "personal", "spam", "other"] = Field(
        description="The primary classification category for the email."
    )
    urgency: Literal["low", "medium", "high"] = Field(
        description="Urgency level based on deadlines, time-sensitivity, or required response."
    )
    should_forward: bool = Field(
        description="True if the email matches forwarding criteria (e.g., travel bookings, tickets, itineraries)."
    )
    forward_to: Optional[str] = Field(
        default=None,
        description="Destination email address if forwarding. If null, fallback to system default."
    )
    apply_folder: Optional[str] = Field(
        default=None,
        description="Proton folder or label name to move/apply (e.g., 'Travel', 'Newsletters', 'Archive')."
    )
    mark_as_read: bool = Field(
        default=True,
        description="Whether to mark the message as read after processing."
    )
    reasoning: str = Field(
        description="A concise 1-sentence audit rationale explaining why these actions were chosen."
    )

```

### `db.py`

```python
import sqlite3
import datetime
from schemas import EmailAction

DB_PATH = "triage_history.db"

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                uid TEXT UNIQUE NOT NULL,
                sender TEXT,
                subject TEXT,
                category TEXT,
                urgency TEXT,
                actions_executed TEXT,
                reasoning TEXT
            )
        """)
        conn.commit()

def record_audit(uid: str, sender: str, subject: str, action: EmailAction, executed_summary: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT OR IGNORE INTO audit_log 
            (timestamp, uid, sender, subject, category, urgency, actions_executed, reasoning)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.datetime.now(datetime.timezone.utc).isoformat(),
            uid,
            sender,
            subject,
            action.category,
            action.urgency,
            executed_summary,
            action.reasoning
        ))
        conn.commit()

```

### `actions.py`

```python
import os
import ssl
import smtplib
from email.message import EmailMessage

BRIDGE_HOST = os.getenv("BRIDGE_HOST", "127.0.0.1")
SMTP_PORT = int(os.getenv("SMTP_PORT", 1025))
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
        server.starttls(context=ctx)
        server.login(BRIDGE_USER, BRIDGE_PASS)
        server.send_message(fwd)
    
    return recipient

```

### `classifier.py`

```python
from langchain_google_genai import ChatGoogleGenerativeAI
from schemas import EmailAction

llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.0)
structured_classifier = llm.with_structured_output(EmailAction)

SYSTEM_PROMPT_TEMPLATE = """You are a mail triage assistant. Analyze this incoming email and output structured actions.

Sender: {sender}
Subject: {subject}
Date: {date}
Body:
<email_body>
{body}
</email_body>

Business Rules:
1. Travel: If the email contains tickets, reservations, itineraries, boarding passes, or travel receipts (flights, trains, hotels, rental cars), set category='travel', should_forward=True, apply_folder='Travel'.
2. Newsletters/Marketing: If it is a promotional newsletter, marketing blast, or digest, set category='newsletter', apply_folder='Newsletters', mark_as_read=True.
3. Finance: If it contains monthly bills, invoices, bank alerts, set category='finance', should_forward=False, apply_folder='Finance'.
4. Default: If no rule triggers, set category='other', should_forward=False, apply_folder=None, mark_as_read=False.
"""

def classify_email(msg) -> EmailAction:
    clean_body = (msg.text or "")[:3000].strip()
    prompt = SYSTEM_PROMPT_TEMPLATE.format(
        sender=msg.from_,
        subject=msg.subject,
        date=msg.date_str,
        body=clean_body
    )
    return structured_classifier.invoke(prompt)

```

### `daemon.py`

```python
import os
import ssl
import time
from dotenv import load_dotenv
from imap_tools import MailBox, AND

load_dotenv()

from db import init_db, record_audit
from actions import forward_message
from classifier import classify_email

BRIDGE_HOST = os.getenv("BRIDGE_HOST", "127.0.0.1")
IMAP_PORT = int(os.getenv("IMAP_PORT", 1143))
BRIDGE_USER = os.getenv("PROTON_USER")
BRIDGE_PASS = os.getenv("PROTON_PASS")

def get_ssl_context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

def process_message(mailbox: MailBox, msg):
    print(f"[*] Processing UID {msg.uid}: '{msg.subject}' from {msg.from_}")
    decision = classify_email(msg)
    executed = []

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
    record_audit(msg.uid, msg.from_, msg.subject, decision, status_str)
    print(f"  └─► Actions: [{status_str}] | Reason: {decision.reasoning}")

def run_daemon():
    init_db()
    ssl_ctx = get_ssl_context()
    print("[*] Starting Proton Mail Push Triage Daemon (IMAP IDLE)...")

    while True:
        try:
            with MailBox(BRIDGE_HOST, port=IMAP_PORT, ssl_context=ssl_ctx).login(BRIDGE_USER, BRIDGE_PASS, 'INBOX') as mailbox:
                print("[*] Connected to Bridge. Checking initial unread backlog...")
                for msg in mailbox.fetch(AND(seen=False), reverse=True):
                    process_message(mailbox, msg)

                print("[*] Entering IDLE state. Awaiting new mail push events...")
                while True:
                    responses = mailbox.idle.wait(timeout=29 * 60)
                    if responses and any('EXISTS' in str(r) for r in responses):
                        print("[!] IMAP Push Notification received.")
                        for msg in mailbox.fetch(AND(seen=False), reverse=True):
                            process_message(mailbox, msg)

        except Exception as e:
            print(f"[!] IMAP Connection interrupted: {e}. Reconnecting in 10s...")
            time.sleep(10)

if __name__ == "__main__":
    run_daemon()

```

### `inspect_logs.py`

```python
import sqlite3
from tabulate import tabulate

def show_recent():
    with sqlite3.connect("triage_history.db") as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, timestamp, sender, subject, category, actions_executed, reasoning
            FROM audit_log ORDER BY id DESC LIMIT 15
        """)
        rows = cursor.fetchall()
        headers = ["ID", "Time", "Sender", "Subject", "Category", "Actions", "Reasoning"]
        print(tabulate(rows, headers=headers, tablefmt="github"))

if __name__ == "__main__":
    show_recent()

```

---

## 6. Systemd Service Deployment

File: `/etc/systemd/system/mail-triage.service`

```ini
[Unit]
Description=Proton Mail AI Push Triage Daemon
After=proton-bridge.service
Requires=proton-bridge.service

[Service]
Type=simple
User=agentuser
WorkingDirectory=/home/agentuser/mail-agent
ExecStart=/home/agentuser/mail-agent/venv/bin/python daemon.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target

```

**Commands to enable and verify:**

```bash
systemctl daemon-reload
systemctl enable --now mail-triage.service
journalctl -u mail-triage.service -f

```
