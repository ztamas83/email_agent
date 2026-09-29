# email_agent
A custom e-mail classification AI helper daemon for Proton Mail.

## Local Testing with Mock Mail Server (GreenMail)

For testing without touching your actual Proton Mail account, the compose stack runs inside an isolated container network (`mail-net`). It launches GreenMail as a mock Proton Mail Bridge with full IMAP IDLE push events and local SMTP delivery, using a fake email address (`agentuser@example.com`).

**Network Security & Isolation:**
- No host networking (`network_mode: host` is disabled).
- Only port `1025` (SMTP) is mapped to the host for injecting test emails.
- IMAP (`1143`) remains internal to the container network, accessed only by `mail-triage-daemon`.

### 1. Start the Compose Stack
Ensure `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) is set in your environment (or `.env`):
```bash
podman-compose up -d --build --force-recreate
# or: docker compose up -d --build --force-recreate
```
This automatically starts both `mock-mail-bridge` and `triage-daemon` on the isolated network.
The rebuild and recreation flags ensure changes to `requirements.txt` are installed and
existing containers do not keep running an older image.

### 2. Follow Daemon Logs
```bash
podman logs -f mail-triage-daemon
# or: docker compose logs -f triage-daemon
```

### 3. Send Test Emails
Use the standalone `send_test_email.py` tool from your host to inject test emails into port 1025:
```bash
# Travel ticket/itinerary (triggers forwarding + moving to 'Travel' folder)
python3 send_test_email.py --category travel

# Monthly invoice / bills (triggers moving to 'Finance' folder)
python3 send_test_email.py --category finance

# Weekly newsletter (triggers moving to 'Newsletters' folder + marking read)
python3 send_test_email.py --category newsletter

# General conversation (triggers default 'other' category with no action)
python3 send_test_email.py --category other

# Send all test templates sequentially
python3 send_test_email.py --category all
```

### 4. Inspect Audit Logs & Web Interface

#### Web Interface (Browser)
A web interface is available at **http://localhost:8000** featuring:
- **Pagination**: Browse audit records with configurable page size (10, 20, 50, 100).
- **Category Filtering**: Filter by category (`travel`, `finance`, `newsletter`, `personal`, `spam`, `other`).
- **Date Filtering**: Filter by date range (From / To date) with quick-range shortcuts (Today, Last 7 Days, Last 30 Days, All Time).
- **Mode Filtering**: View All, Live Only, or Dry Run Only logs.
- **Search & Inspection**: Full-text search across sender, subject, and LLM reasoning, plus clickable row detail modals.
- **Auto-Refresh**: Live updates as new emails are processed.

Start the web UI:
```bash
# In container stack (started automatically if using docker-compose up)
podman-compose up -d web-ui
# or run natively on host:
python web.py
```

#### CLI Log Inspector
View classification decisions and executed actions directly in your terminal:
```bash
podman-compose run --rm inspect-logs
# or natively:
python inspect_logs.py
```

---

## Dry-Run Mode (Simulation Only)

You can run the triage classifier in **dry-run mode** to observe classification decisions and simulated actions without actually sending/forwarding emails, moving folders, or marking messages as read in the mailbox:

- Set `DRY_RUN=true` in your `.env` or environment:
  ```bash
  # Standalone host execution
  DRY_RUN=true python daemon.py

  # In compose stack
  DRY_RUN=true podman-compose up -d triage-daemon
  ```
- In dry-run mode:
  - Emails are classified using the LLM.
  - Actions that would be taken (e.g. `would_forward:recipient@example.com`, `would_move:Travel`, `would_mark_read`) are recorded to `audit_log` with `is_dry_run = 1`.
  - No IMAP mutations (moving/marking seen) or SMTP emails are sent.
  - Emails remain untouched and unread in your inbox.
  - Duplicate processing prevention ensures unread emails are not repeatedly sent to the LLM on every push event.

## Quick Installation (One-Liner via curl)

To install on any remote or local Linux host without cloning manually or configuring GitHub tokens/SSH keys:

```bash
curl -sSL https://raw.githubusercontent.com/ztamas83/email_agent/master/install.sh | bash
```

This will automatically:
1. Download the latest source files into `~/email_agent` (via public HTTPS or archive tarball).
2. Install the `uv` package manager (if missing).
3. Create a `.venv` virtual environment and install all dependencies.
4. Generate the `.env` template and systemd unit service files.
5. Register the `mail-triage.service` and `mail-triage-web.service` with systemd.

#### Customizing Installation
```bash
# Custom directory
INSTALL_DIR=/opt/email_agent curl -sSL https://raw.githubusercontent.com/ztamas83/email_agent/master/install.sh | bash

# Download and set up venv only (do not touch systemd services)
SKIP_SERVICE=1 curl -sSL https://raw.githubusercontent.com/ztamas83/email_agent/master/install.sh | bash
```

---

## Production / Live Proton Mail Bridge Usage

### 1. Environment Configuration
Provide credentials via a `.env` file (copied from `.env.example`) or directly in your shell environment:
- `PROTON_USER`: Your Proton Mail address
- `PROTON_PASS`: 16-character bridge password
- `BRIDGE_HOST`: `127.0.0.1`
- `IMAP_PORT`: `1143`
- `SMTP_PORT`: `1025`
- `FORWARD_DEFAULT_TO`: Target forward destination
- `GEMINI_API_KEY`: Google Gemini API key
- `LLM_MODEL`: Configurable Gemini model (defaults to `gemini-3.5-flash-lite`)
- `MAILBOX_OWNER`: Optional name of the mailbox owner used in the triage prompt
- `DRY_RUN`: `false` (default) or `true` for dry-run observation mode
- `IMAP_SECURITY`: `auto` (default, negotiates STARTTLS or SSL), or explicitly `starttls`, `ssl`, `plain`
- `HISTORY_DAYS`: Controls unread history lookback window:
  - `0`: **Entirely skip history** (establishes baseline at startup and only processes new emails arriving while the daemon is running)
  - `7`: Process unread emails from the last 7 days only
  - Empty or `all`: Process all unread emails in the mailbox backlog
- `RULES_FILE`: Path to custom JSON rules file (defaults to `rules.json`, see `rules.json.example`). If no rules are configured, LLM calls are skipped entirely.
- `CATEGORY_RULES_JSON`: Optional inline JSON string for category rules (alternative to `rules.json`).

### 2. Custom JSON Category Rules (`rules.json`)
You can define category-specific business rules in a `rules.json` file.
- **No Rules Configured:** If no rules are defined in `rules.json`, the daemon skips all LLM calls entirely, avoiding any token consumption and keeping all email content private.
- **Category with a Prompt:** The daemon performs Step 1 (sender + subject) classification and forwards the body to Step 2 using **only that specific category's prompt and constraints**.
- **Category without a Prompt:** The body is strictly withheld from the LLM, and any configured default actions (e.g. folder move) are applied deterministically.

```json
[
  {
    "category": "travel",
    "prompt": "If the email contains tickets, reservations, or itineraries and {user} is explicitly mentioned on the travelers list, confirm details and forward.",
    "apply_folder": "Travel",
    "should_forward": true
  },
  {
    "category": "finance",
    "prompt": "Analyze this invoice or statement for amount due and payment deadline. Set urgency='high' if due within 3 days.",
    "apply_folder": "Finance",
    "should_forward": false
  },
  {
    "category": "newsletter",
    "prompt": "Promotional newsletter, digest, or marketing blast.",
    "apply_folder": "Newsletters",
    "should_forward": false,
    "mark_as_read": true
  }
]
```

In Step 2, the daemon will send **only the prompt and required constraints for the selected category**, preventing cross-category rule leakage and reducing token usage.

### 3. Native Host Service Deployment
To deploy as a native systemd service on a Linux host pointing to your real local Proton Mail Bridge:
```bash
./setup.sh
```
