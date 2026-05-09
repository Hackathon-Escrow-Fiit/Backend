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
                    ai_report   JSONB NOT NULL,
                    status      TEXT DEFAULT 'pending',
                    created_at  TIMESTAMPTZ DEFAULT NOW()
                )
            """)


def save_pending(escrow_id: str, freelancer: str, ai_report: dict):
    """Saves an AI evaluation report with status 'pending'."""
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO pending_evaluations (escrow_id, freelancer, ai_report)
                VALUES (%s, %s, %s)
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
