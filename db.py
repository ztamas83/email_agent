import os
import sqlite3
import datetime
import math
from typing import Optional, Dict, Any, List
from contextlib import contextmanager
from dotenv import load_dotenv
from schemas import EmailAction

# Try loading from .env, but do NOT override environment variables already present
load_dotenv(override=False)

DB_PATH = os.getenv("DB_PATH") or "triage_history.db"

@contextmanager
def get_db_connection(path: Optional[str] = None):
    conn = sqlite3.connect(path or DB_PATH)
    try:
        yield conn
    finally:
        conn.close()

def init_db(db_path: Optional[str] = None):
    path = db_path or DB_PATH
    with get_db_connection(path) as conn:
        with conn:
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
                    reasoning TEXT,
                    is_dry_run INTEGER DEFAULT 0
                )
            """)
            # Migration: ensure is_dry_run column exists for existing tables
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(audit_log)")
            columns = [row[1] for row in cursor.fetchall()]
            if "is_dry_run" not in columns:
                cursor.execute("ALTER TABLE audit_log ADD COLUMN is_dry_run INTEGER DEFAULT 0")

def record_audit(
    uid: str,
    sender: str,
    subject: str,
    action: EmailAction,
    executed_summary: str,
    is_dry_run: bool = False,
    db_path: Optional[str] = None
):
    path = db_path or DB_PATH
    effective_uid = f"dry_{uid}" if is_dry_run and not str(uid).startswith("dry_") else str(uid)
    with get_db_connection(path) as conn:
        with conn:
            conn.execute("""
                INSERT OR IGNORE INTO audit_log 
                (timestamp, uid, sender, subject, category, urgency, actions_executed, reasoning, is_dry_run)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                datetime.datetime.now(datetime.timezone.utc).isoformat(),
                effective_uid,
                sender,
                subject,
                action.category,
                action.urgency,
                executed_summary,
                action.reasoning,
                1 if is_dry_run else 0
            ))

def is_uid_processed(uid: str, is_dry_run: bool = False, db_path: Optional[str] = None) -> bool:
    path = db_path or DB_PATH
    effective_uid = f"dry_{uid}" if is_dry_run and not str(uid).startswith("dry_") else str(uid)
    with get_db_connection(path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM audit_log WHERE uid = ? LIMIT 1", (effective_uid,))
        return cursor.fetchone() is not None

def get_unique_categories(db_path: Optional[str] = None) -> List[str]:
    path = db_path or DB_PATH
    default_categories = ["travel", "finance", "newsletter", "personal", "spam", "other"]
    try:
        with get_db_connection(path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT DISTINCT category FROM audit_log 
                WHERE category IS NOT NULL AND category != '' 
                ORDER BY category
            """)
            db_cats = [r[0] for r in cursor.fetchall()]
            merged = sorted(list(set(default_categories + db_cats)))
            return merged
    except Exception:
        return default_categories

def query_logs(
    page: int = 1,
    page_size: int = 20,
    category: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    mode: str = "all",
    search: Optional[str] = None,
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    path = db_path or DB_PATH
    page = max(1, page)
    page_size = max(1, min(100, page_size))
    offset = (page - 1) * page_size

    where_clauses = []
    params = []

    if category and category.lower() != "all":
        where_clauses.append("LOWER(category) = LOWER(?)")
        params.append(category)

    if start_date:
        where_clauses.append("date(timestamp) >= date(?)")
        params.append(start_date)

    if end_date:
        where_clauses.append("date(timestamp) <= date(?)")
        params.append(end_date)

    if mode == "live":
        where_clauses.append("(is_dry_run = 0 OR is_dry_run IS NULL)")
    elif mode == "dryrun":
        where_clauses.append("is_dry_run = 1")

    if search:
        search_pattern = f"%{search.strip()}%"
        where_clauses.append("(uid LIKE ? OR sender LIKE ? OR subject LIKE ? OR reasoning LIKE ?)")
        params.extend([search_pattern, search_pattern, search_pattern, search_pattern])

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    with get_db_connection(path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Count total matching rows
        count_query = f"SELECT COUNT(*) FROM audit_log {where_sql}"
        cursor.execute(count_query, params)
        total_rows = cursor.fetchone()[0]

        # Fetch page of rows
        data_query = f"""
            SELECT id, timestamp, uid, sender, subject, category, urgency, actions_executed, reasoning, is_dry_run
            FROM audit_log
            {where_sql}
            ORDER BY id DESC
            LIMIT ? OFFSET ?
        """
        cursor.execute(data_query, params + [page_size, offset])
        rows = [dict(r) for r in cursor.fetchall()]

    total_pages = max(1, math.ceil(total_rows / page_size)) if total_rows > 0 else 1

    return {
        "items": rows,
        "total": total_rows,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "categories": get_unique_categories(path)
    }

