import os
import sys
import json
import sqlite3
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager

# Ensure immediate unbuffered output so logs appear in real-time under systemd / journalctl
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(line_buffering=True)

from fastapi import FastAPI, Query, HTTPException, Body
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

RULES_FILE = os.getenv("RULES_FILE", "rules.json")

@app.get("/api/rules")
async def get_rules():
    try:
        if os.path.exists(RULES_FILE):
            with open(RULES_FILE, "r", encoding="utf-8") as f:
                rules = json.load(f)
        else:
            rules = []
        
        # Ensure rules is a list
        if not isinstance(rules, list):
            rules = []
            
        # Normalize/ensure necessary fields are present for the editor
        for r in rules:
            if not isinstance(r, dict):
                continue
            r["category"] = r.get("category", "")
            r["category_criteria"] = r.get("category_criteria", "")
            r["action_criteria"] = r.get("action_criteria", "")
            r["prompt"] = r.get("prompt", "")
            r["apply_folder"] = r.get("apply_folder", "")
            r["should_forward"] = bool(r.get("should_forward", False))
            r["forward_to"] = r.get("forward_to", "")
            
        return rules
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read rules: {e}")

@app.put("/api/rules")
async def save_rules(rules: list = Body(...)):
    try:
        validated_rules = []
        for rule in rules:
            if not isinstance(rule, dict):
                raise HTTPException(status_code=400, detail="Each rule must be an object")
            
            category = rule.get("category", "").strip()
            if not category:
                raise HTTPException(status_code=400, detail="Category name is required for all rules")
            
            category_criteria = rule.get("category_criteria", "").strip()
            action_criteria = rule.get("action_criteria", "").strip()
            prompt = rule.get("prompt", "").strip()
            apply_folder = rule.get("apply_folder", "").strip()
            should_forward = bool(rule.get("should_forward", False))
            forward_to = rule.get("forward_to", "").strip()
            
            # Enforce validation: All necessary fields must be present and not empty!
            if not category_criteria:
                raise HTTPException(status_code=400, detail=f"Category criteria is required for category '{category}'")
            if not action_criteria:
                raise HTTPException(status_code=400, detail=f"Action criteria is required for category '{category}'")
            if not prompt:
                raise HTTPException(status_code=400, detail=f"Prompt is required for category '{category}'")
            if not apply_folder:
                raise HTTPException(status_code=400, detail=f"Apply folder is required for category '{category}'")
            if should_forward and not forward_to:
                raise HTTPException(status_code=400, detail=f"Forward-to email is required for category '{category}' when forwarding is enabled")
                
            # Construct validated rule object, preserving any other keys
            new_rule = {
                "category": category,
                "category_criteria": category_criteria,
                "action_criteria": action_criteria,
                "prompt": prompt,
                "apply_folder": apply_folder,
                "should_forward": should_forward,
                "forward_to": forward_to,
            }
            # Copy other keys (like mark_as_read)
            for k, v in rule.items():
                if k not in new_rule:
                    new_rule[k] = v
                    
            validated_rules.append(new_rule)
            
        with open(RULES_FILE, "w", encoding="utf-8") as f:
            json.dump(validated_rules, f, indent=2, ensure_ascii=False)
            
        return {"status": "success", "rules": validated_rules}
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save rules: {e}")

@app.get("/healthz")
async def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    host = os.getenv("WEB_HOST", "0.0.0.0")
    port = int(os.getenv("WEB_PORT", "8000"))
    print(f"[*] Starting web server on http://{host}:{port}")
    uvicorn.run("web:app", host=host, port=port, reload=True)
