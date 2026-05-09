from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Literal
import json

from storage import (
    init_db, save_uploaded_files, save_pending, get_pending,
    update_status, delete_submission,
    list_submission_files, get_submission_file_path,
)
from evaluator import run_ai_evaluation
from contracts import get_profile_from_chain

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

init_db()


# ─────────────────────────────────────────
# 1. EVALUATE — freelancer submits work (zip or individual files)
# ─────────────────────────────────────────
@app.post("/evaluate")
async def evaluate(
    escrow_id: str = Form(...),
    freelancer_address: str = Form(...),
    customer_task: str = Form(...),
    required_skills: str = Form(...),   # JSON string: '["solidity","react"]'
    files: List[UploadFile] = File(...)
):
    """
    Step 1: Freelancer uploads their work (zip or individual files).

    Flow:
      1. Save uploaded files to tmp/submissions/{escrow_id}/
      2. Extract zip archives automatically
      3. Read freelancer profile from blockchain
      4. Run AI evaluation → detailed markdown report
      5. Save report to DB with status 'pending'
      6. Return report so client can make a decision

    Returns:
        escrow_id, freelancer, detailed_report, recommendation, trigger_dao
    """
    if get_pending(escrow_id):
        raise HTTPException(400, "Evaluation already exists for this escrow")

    saved_paths = save_uploaded_files(escrow_id, files)

    profile = get_profile_from_chain(freelancer_address)

    skills_list = json.loads(required_skills)
    ai_report = run_ai_evaluation(
        customer_task=customer_task,
        file_paths=saved_paths,
        required_skills=skills_list,
        current_reputation=profile["reputation"],
        current_skills=profile["skills"]
    )

    save_pending(escrow_id, freelancer_address, ai_report)

    return {
        "escrow_id": escrow_id,
        "freelancer": freelancer_address,
        "status": "pending",
        "detailed_report": ai_report["detailed_report"],
        "recommendation": ai_report["recommendation"],
        "trigger_dao": ai_report["recommendation"] == "escalate_to_dao",
    }


# ─────────────────────────────────────────
# 2. REPORT — client or DAO voters read the evaluation
# ─────────────────────────────────────────
@app.get("/report/{escrow_id}")
async def get_report(escrow_id: str):
    """
    Step 2: Client (or DAO voters) read the AI evaluation report.
    """
    record = get_pending(escrow_id)
    if not record:
        raise HTTPException(404, "No evaluation found for this escrow")

    return {
        "escrow_id": escrow_id,
        "freelancer": record["freelancer"],
        "status": record["status"],
        "detailed_report": record["ai_report"]["detailed_report"],
        "recommendation": record["ai_report"]["recommendation"],
    }


# ─────────────────────────────────────────
# 3. CLIENT DECISION — approve, reject, or escalate to DAO
# ─────────────────────────────────────────
class ClientDecisionRequest(BaseModel):
    escrow_id: str
    decision: Literal["approve", "reject", "escalate_to_dao"]


@app.post("/client-decision")
async def client_decision(body: ClientDecisionRequest):
    """
    Step 3: Client reviews the report and picks one of three paths:

      "approve"          → work accepted, apply AI-suggested rewards, clean up files
      "reject"           → work rejected, apply -15 reputation penalty, clean up files
      "escalate_to_dao"  → disputed, status set to 'dao_pending', files kept for DAO review

    Returns:
      - For approve/reject: freelancer, skill_changes, reputation_delta
      - For escalate_to_dao: escrow_id, status='dao_pending'
    """
    record = get_pending(body.escrow_id)
    if not record:
        raise HTTPException(404, "No pending evaluation found for this escrow")

    if record["status"] not in ("pending",):
        raise HTTPException(400, f"Escrow is already in status '{record['status']}'")

    if body.decision == "escalate_to_dao":
        update_status(body.escrow_id, "dao_pending")
        return {
            "escrow_id": body.escrow_id,
            "status": "dao_pending",
            "message": "Escalated to DAO. DAO voters can review the report and cast votes.",
        }

    # approve or reject — finalise immediately
    return _finalise(body.escrow_id, record, approved=(body.decision == "approve"))


# ─────────────────────────────────────────
# 4. DAO RESOLVE — DAO casts final verdict
# ─────────────────────────────────────────
class DaoResolveRequest(BaseModel):
    escrow_id: str
    approved: bool   # True = DAO approves the work, False = DAO sides with client rejection


@app.post("/dao-resolve")
async def dao_resolve(body: DaoResolveRequest):
    """
    Step 4 (DAO path only): DAO votes and sends the final resolution.

    Files are kept after DAO resolution so users can still download the code.
    Use DELETE /submission/{escrow_id} to clean up when no longer needed.

    Returns freelancer, skill_changes, reputation_delta — same shape as a direct decision.
    """
    record = get_pending(body.escrow_id)
    if not record:
        raise HTTPException(404, "No evaluation found for this escrow")

    if record["status"] != "dao_pending":
        raise HTTPException(400, f"Escrow is not awaiting DAO resolution (status: '{record['status']}')")

    # keep_files=True so users can still download the code after the DAO verdict
    return _finalise(body.escrow_id, record, approved=body.approved, keep_files=True)


# ─────────────────────────────────────────
# 5. FILE ACCESS — browse and download code after DAO resolution
# ─────────────────────────────────────────
@app.get("/files/{escrow_id}")
async def list_files(escrow_id: str):
    """
    Lists all files available for a submission.
    Only works after DAO resolution (files are deleted on direct approve/reject).
    """
    record = get_pending(escrow_id)
    if not record:
        raise HTTPException(404, "No evaluation found for this escrow")
    if record["status"] != "dao_resolved":
        raise HTTPException(400, "Files are only accessible after DAO resolution")

    return {
        "escrow_id": escrow_id,
        "files": list_submission_files(escrow_id),
    }


@app.get("/files/{escrow_id}/{filename}")
async def download_file(escrow_id: str, filename: str):
    """
    Downloads a single file from a DAO-resolved submission.
    """
    record = get_pending(escrow_id)
    if not record:
        raise HTTPException(404, "No evaluation found for this escrow")
    if record["status"] != "dao_resolved":
        raise HTTPException(400, "Files are only accessible after DAO resolution")

    path = get_submission_file_path(escrow_id, filename)
    if path is None:
        raise HTTPException(404, f"File '{filename}' not found")

    return FileResponse(path=str(path), filename=filename)


@app.delete("/submission/{escrow_id}")
async def delete_submission_files(escrow_id: str):
    """
    Manually cleans up files and DB record for a DAO-resolved submission.
    Call this when the client no longer needs to download the code.
    """
    record = get_pending(escrow_id)
    if not record:
        raise HTTPException(404, "No evaluation found for this escrow")
    if record["status"] != "dao_resolved":
        raise HTTPException(400, "Only dao_resolved submissions can be manually deleted")

    delete_submission(escrow_id)
    return {"deleted": escrow_id}


# ─────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok"}


# ─────────────────────────────────────────
# INTERNAL HELPER
# ─────────────────────────────────────────
def _finalise(escrow_id: str, record: dict, approved: bool, keep_files: bool = False) -> dict:
    """
    Calculates final skill/reputation changes.
    keep_files=False (direct client decision): deletes files and DB record immediately.
    keep_files=True  (DAO resolution): sets status to 'dao_resolved', keeps files for download.
    """
    ai_report = record["ai_report"]

    if approved:
        reputation_delta = ai_report["suggested_reputation_delta"]
        skill_changes = {
            skill: data["new_level"]
            for skill, data in ai_report.get("suggested_skills", {}).items()
        }
    else:
        reputation_delta = -15
        skill_changes = {}

    if keep_files:
        update_status(escrow_id, "dao_resolved")
    else:
        delete_submission(escrow_id)

    return {
        "freelancer": record["freelancer"],
        "skill_changes": skill_changes,
        "reputation_delta": reputation_delta,
    }
