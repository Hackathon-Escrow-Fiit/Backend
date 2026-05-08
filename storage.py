import sqlite3
import shutil
import json
import zipfile
from pathlib import Path

DB_PATH = "decentrawork.db"

# Temporary directory for uploaded submission files
SUBMISSIONS_DIR = Path("tmp/submissions")
SUBMISSIONS_DIR.mkdir(parents=True, exist_ok=True)


def init_db():
    """Creates the database tables if they do not exist yet."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pending_evaluations (
            escrow_id   TEXT PRIMARY KEY,
            freelancer  TEXT NOT NULL,
            ai_report   TEXT NOT NULL,
            status      TEXT DEFAULT 'pending',
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Status values:
    #   pending      — AI evaluated, waiting for client approve or DAO
    #   approved     — client approved, finalized
    #   rejected     — client rejected, finalized
    #   dao_resolved — DAO voted and resolved the dispute
    conn.commit()
    conn.close()


def save_pending(escrow_id: str, freelancer: str, ai_report: dict):
    """Saves an AI evaluation report with status 'pending'."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO pending_evaluations (escrow_id, freelancer, ai_report) VALUES (?, ?, ?)",
        (escrow_id, freelancer, json.dumps(ai_report))
    )
    conn.commit()
    conn.close()


def get_pending(escrow_id: str) -> dict | None:
    """Returns the pending evaluation record for a given escrow_id, or None if not found."""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT freelancer, ai_report, status FROM pending_evaluations WHERE escrow_id = ?",
        (escrow_id,)
    ).fetchone()
    conn.close()

    if not row:
        return None

    return {
        "freelancer": row[0],
        "ai_report": json.loads(row[1]),
        "status": row[2]
    }


def update_status(escrow_id: str, status: str):
    """Updates the status of an evaluation record."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE pending_evaluations SET status = ? WHERE escrow_id = ?",
        (status, escrow_id)
    )
    conn.commit()
    conn.close()


def delete_submission(escrow_id: str):
    """
    Deletes all uploaded files and the database record for a given escrow.
    Called after the evaluation is finalized (approved, rejected, or DAO resolved).
    Files are no longer needed once the result is written to the smart contract.
    """
    # Remove the submission folder with all files
    submission_dir = SUBMISSIONS_DIR / escrow_id
    if submission_dir.exists():
        shutil.rmtree(submission_dir)

    # Remove the database record
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "DELETE FROM pending_evaluations WHERE escrow_id = ?",
        (escrow_id,)
    )
    conn.commit()
    conn.close()


def save_uploaded_files(escrow_id: str, files: list) -> list[str]:
    """
    Saves uploaded files to tmp/submissions/{escrow_id}/.
    If a zip file is uploaded, it will be automatically extracted.
    Returns a list of file paths that can be passed to the AI evaluator.
    """
    submission_dir = SUBMISSIONS_DIR / escrow_id
    submission_dir.mkdir(parents=True, exist_ok=True)

    saved_paths = []
    for file in files:
        file_path = submission_dir / file.filename
        content = file.file.read()
        file_path.write_bytes(content)

        # If it's a zip file, extract it
        if file.filename.lower().endswith('.zip'):
            try:
                with zipfile.ZipFile(file_path, 'r') as zip_ref:
                    # Extract all files to the submission directory
                    zip_ref.extractall(submission_dir)

                # Add all extracted files to saved_paths (excluding the zip itself)
                for extracted_file in submission_dir.rglob('*'):
                    if extracted_file.is_file() and extracted_file != file_path:
                        saved_paths.append(str(extracted_file))

                # Optionally remove the zip file after extraction
                file_path.unlink()
            except zipfile.BadZipFile:
                # If it's not a valid zip, just treat it as a regular file
                saved_paths.append(str(file_path))
        else:
            saved_paths.append(str(file_path))

    return saved_paths
