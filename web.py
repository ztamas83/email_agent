import os
import sqlite3
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import HTMLResponse
from dotenv import load_dotenv

load_dotenv(override=False)

import db

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
INDEX_HTML_PATH = TEMPLATES_DIR / "index.html"

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure database tables and columns are properly initialized
    db.init_db()
    yield

app = FastAPI(
    title="Email Triage Audit Logs",
    description="Web interface for monitoring Proton Mail AI triage logs, classifications, and dry-run simulations.",
    version="1.0.0",
    lifespan=lifespan
)

@app.get("/", response_class=HTMLResponse)
async def get_index():
    if not INDEX_HTML_PATH.exists():
        raise HTTPException(status_code=404, detail="Index template not found")
    return HTMLResponse(content=INDEX_HTML_PATH.read_text(encoding="utf-8"))

@app.get("/api/logs")
async def get_logs(
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    category: Optional[str] = Query(None, description="Category filter (e.g. travel, finance, newsletter)"),
    start_date: Optional[str] = Query(None, description="Start date filter (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date filter (YYYY-MM-DD)"),
    mode: str = Query("all", description="Mode filter: all, live, or dryrun"),
    search: Optional[str] = Query(None, description="Search keyword in sender, subject, or reasoning")
):
    try:
        return db.query_logs(
            page=page,
            page_size=page_size,
            category=category,
            start_date=start_date,
            end_date=end_date,
            mode=mode,
            search=search
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/stats")
async def get_stats():
    db_path = db.DB_PATH
    try:
        with db.get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute("SELECT COUNT(*) FROM audit_log")
            total = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM audit_log WHERE is_dry_run = 0 OR is_dry_run IS NULL")
            live = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM audit_log WHERE is_dry_run = 1")
            dry_run = cursor.fetchone()[0]

            cursor.execute("""
                SELECT category, COUNT(*) 
                FROM audit_log 
                WHERE category IS NOT NULL AND category != '' 
                GROUP BY category
            """)
            category_counts = {cat: count for cat, count in cursor.fetchall()}

        return {
            "total": total,
            "live": live,
            "dry_run": dry_run,
            "categories": category_counts
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/healthz")
async def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    host = os.getenv("WEB_HOST", "0.0.0.0")
    port = int(os.getenv("WEB_PORT", "8000"))
    print(f"[*] Starting web server on http://{host}:{port}")
    uvicorn.run("web:app", host=host, port=port, reload=True)
