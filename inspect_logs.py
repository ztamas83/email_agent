import os
import sqlite3
from dotenv import load_dotenv
from tabulate import tabulate

# Try loading from .env, but do NOT override environment variables already present
load_dotenv(override=False)

DB_PATH = os.getenv("DB_PATH") or "triage_history.db"

def show_recent():
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, timestamp, CASE WHEN is_dry_run = 1 THEN 'DRY-RUN' ELSE 'LIVE' END as mode, sender, subject, category, actions_executed, reasoning
            FROM audit_log ORDER BY id DESC LIMIT 15
        """)
        rows = cursor.fetchall()
        headers = ["ID", "Time", "Mode", "Sender", "Subject", "Category", "Actions", "Reasoning"]
        print(tabulate(rows, headers=headers, tablefmt="github"))

if __name__ == "__main__":
    show_recent()
