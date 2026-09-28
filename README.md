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
podman-compose up -d
# or: docker compose up -d
```
This automatically starts both `mock-mail-bridge` and `triage-daemon` on the isolated network.

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

### 2. Native Host Service Deployment
To deploy as a native systemd service on a Linux host pointing to your real local Proton Mail Bridge:
```bash
./setup.sh
```
