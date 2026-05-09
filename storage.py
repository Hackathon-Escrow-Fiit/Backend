import shutil
import json
import zipfile
import os
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

SUBMISSIONS_DIR = Path("tmp/submissions")
SUBMISSIONS_DIR.mkdir(parents=True, exist_ok=True)


def _get_conn():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    """Creates the database tables if they do not exist yet."""
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS pending_evaluations (
                    escrow_id   TEXT PRIMARY KEY,
                    freelancer  TEXT NOT NULL,
                    ai_report   JSONB,
                    status      TEXT DEFAULT 'evaluating',
                    created_at  TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            # Idempotent migration: ensure ai_report allows NULL
            cur.execute("""
                DO $$
                BEGIN
                    ALTER TABLE pending_evaluations ALTER COLUMN ai_report DROP NOT NULL;
                EXCEPTION WHEN others THEN NULL;
                END $$
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS freelancer_elo (
                    address          TEXT PRIMARY KEY,
                    elo              INT  DEFAULT 600,
                    tasks_completed  INT  DEFAULT 0,
                    active_modifiers JSONB DEFAULT '[]'
                )
            """)


def save_evaluating(escrow_id: str, freelancer: str):
    """Inserts a placeholder row with status='evaluating' before the AI runs."""
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO pending_evaluations (escrow_id, freelancer, ai_report, status)
                VALUES (%s, %s, NULL, 'evaluating')
                """,
                (escrow_id, freelancer)
            )


def update_report(escrow_id: str, ai_report: dict):
    """Stores the finished AI report and advances status to 'pending'."""
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE pending_evaluations
                   SET ai_report = %s, status = 'pending'
                 WHERE escrow_id = %s
                """,
                (json.dumps(ai_report), escrow_id)
            )


def save_pending(escrow_id: str, freelancer: str, ai_report: dict):
    """Upserts a completed evaluation report with status 'pending'."""
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO pending_evaluations (escrow_id, freelancer, ai_report, status)
                VALUES (%s, %s, %s, 'pending')
                ON CONFLICT (escrow_id) DO UPDATE
                    SET freelancer = EXCLUDED.freelancer,
                        ai_report  = EXCLUDED.ai_report,
                        status     = 'pending'
                """,
                (escrow_id, freelancer, json.dumps(ai_report))
            )


def get_pending(escrow_id: str) -> dict | None:
    """Returns the pending evaluation record for a given escrow_id, or None if not found."""
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT freelancer, ai_report, status FROM pending_evaluations WHERE escrow_id = %s",
                (escrow_id,)
            )
            row = cur.fetchone()

    if not row:
        return None

    return {
        "freelancer": row[0],
        "ai_report": row[1],   # psycopg2 deserialises JSONB automatically
        "status": row[2]
    }


def update_status(escrow_id: str, status: str):
    """Updates the status of an evaluation record."""
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE pending_evaluations SET status = %s WHERE escrow_id = %s",
                (status, escrow_id)
            )


def delete_submission(escrow_id: str):
    """
    Deletes all uploaded files and the database record for a given escrow.
    Called after direct client approve/reject.
    """
    submission_dir = SUBMISSIONS_DIR / escrow_id
    if submission_dir.exists():
        shutil.rmtree(submission_dir)

    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM pending_evaluations WHERE escrow_id = %s",
                (escrow_id,)
            )


def list_submission_files(escrow_id: str) -> list[dict]:
    """
    Returns metadata for all files in a submission directory.
    Used after DAO resolution so users can still browse/download the code.
    """
    submission_dir = SUBMISSIONS_DIR / escrow_id
    if not submission_dir.exists():
        return []
    return [
        {"filename": f.name, "size": f.stat().st_size}
        for f in sorted(submission_dir.rglob("*"))
        if f.is_file()
    ]


def get_submission_file_path(escrow_id: str, filename: str) -> Path | None:
    """
    Returns the absolute Path of a single file inside a submission directory,
    or None if it does not exist. Prevents path-traversal by checking the
    resolved path stays inside the submission directory.
    """
    submission_dir = (SUBMISSIONS_DIR / escrow_id).resolve()
    candidate = (submission_dir / filename).resolve()
    if not str(candidate).startswith(str(submission_dir)):
        return None
    return candidate if candidate.is_file() else None


def save_uploaded_files(escrow_id: str, files: list) -> list[str]:
    """
    Saves uploaded files to tmp/submissions/{escrow_id}/.
    Zip files are automatically extracted.
    Returns a list of saved file paths.
    """
    submission_dir = SUBMISSIONS_DIR / escrow_id
    submission_dir.mkdir(parents=True, exist_ok=True)

    saved_paths = []
    for file in files:
        file_path = submission_dir / file.filename
        content = file.file.read()
        file_path.write_bytes(content)

        if file.filename.lower().endswith('.zip'):
            try:
                with zipfile.ZipFile(file_path, 'r') as zip_ref:
                    zip_ref.extractall(submission_dir)

                for extracted_file in submission_dir.rglob('*'):
                    if extracted_file.is_file() and extracted_file != file_path:
                        saved_paths.append(str(extracted_file))

                file_path.unlink()
            except zipfile.BadZipFile:
                saved_paths.append(str(file_path))
        else:
            saved_paths.append(str(file_path))

    return saved_paths


# ── Elo helpers ────────────────────────────────────────────────────────────────

def get_elo_record(address: str) -> dict:
    """Returns the freelancer's Elo record, auto-initialising to 600 if missing."""
    addr = address.lower()
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT elo, tasks_completed, active_modifiers FROM freelancer_elo WHERE address = %s",
                (addr,)
            )
            row = cur.fetchone()

    if row:
        return {"elo": row[0], "tasks_completed": row[1], "active_modifiers": row[2] or []}

    return {"elo": 600, "tasks_completed": 0, "active_modifiers": []}


def save_elo_record(address: str, elo: int, tasks_completed: int, active_modifiers: list) -> None:
    """Upserts the freelancer's Elo record."""
    addr = address.lower()
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO freelancer_elo (address, elo, tasks_completed, active_modifiers)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (address) DO UPDATE
                    SET elo              = EXCLUDED.elo,
                        tasks_completed  = EXCLUDED.tasks_completed,
                        active_modifiers = EXCLUDED.active_modifiers
                """,
                (addr, elo, tasks_completed, json.dumps(active_modifiers))
            )


def add_dispute_loss_modifier(address: str) -> None:
    """Adds a dispute_loss modifier (K boost for next 3 tasks) to the freelancer's record."""
    record = get_elo_record(address)
    mods = record["active_modifiers"]
    mods.append({"type": "dispute_loss", "remaining": 3})
    save_elo_record(address, record["elo"], record["tasks_completed"], mods)
