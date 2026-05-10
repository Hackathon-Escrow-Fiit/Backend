from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Literal
import json

from storage import (
    init_db, save_uploaded_files, save_evaluating, save_pending,
    update_report, get_pending, update_status, delete_submission,
    list_submission_files, get_submission_file_path,
    save_task_rating, get_task_rating,
)
from evaluator import run_ai_evaluation, rate_task
from contracts import (
    get_profile_from_chain,
    approve_work_on_chain, reject_work_on_chain, set_skill_on_chain,
    get_elo_from_chain, update_elo_on_chain, increment_tasks_on_chain,
)
from elo import apply_elo, get_tier, STARTING_ELO

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
    background_tasks: BackgroundTasks,
    escrow_id: str = Form(...),
    freelancer_address: str = Form(...),
    customer_task: str = Form(...),
    required_skills: str = Form(...),   # JSON string: '["solidity","react"]'
    files: List[UploadFile] = File(...)
):
    """
    Step 1: Freelancer uploads their work (zip or individual files).

    Returns immediately with status='evaluating'. Poll GET /report/{escrow_id}
    until status changes to 'pending' (AI done) or 'error'.

    Background flow:
      1. Save files, insert placeholder row (status='evaluating')
      2. Read blockchain profile
      3. Run AI evaluation
      4. Write approveWork / rejectWork + setSkill txs to chain
      5. Update DB row to status='pending' with full report
    """
    existing = get_pending(escrow_id)
    if existing:
        if existing["status"] == "evaluating":
            raise HTTPException(400, "Evaluation already in progress for this escrow — please wait")
        # Previous attempt finished or errored — delete it and allow re-submission
        delete_submission(escrow_id)

    saved_paths = save_uploaded_files(escrow_id, files)
    skills_list = json.loads(required_skills)

    save_evaluating(escrow_id, freelancer_address)

    background_tasks.add_task(
        _run_evaluation_background,
        escrow_id, freelancer_address, customer_task, skills_list, saved_paths
    )

    return {"escrow_id": escrow_id, "freelancer": freelancer_address, "status": "evaluating"}


def _run_evaluation_background(
    escrow_id: str,
    freelancer_address: str,
    customer_task: str,
    skills_list: list,
    saved_paths: list,
):
    """Runs AI evaluation and on-chain writes in the background. Elo is applied at decision time."""
    try:
        profile = get_profile_from_chain(freelancer_address, required_skills=skills_list)

        ai_report = run_ai_evaluation(
            customer_task=customer_task,
            file_paths=saved_paths,
            required_skills=skills_list,
            current_reputation=profile["reputation"],
            current_skills=profile["skills"]
        )

        update_report(escrow_id, ai_report)

        job_id = _parse_job_id(escrow_id)
        if job_id is not None:
            recommendation = ai_report["recommendation"]
            if recommendation in ("approve", "escalate_to_dao"):
                on_chain = approve_work_on_chain(job_id, ai_report["suggested_reputation_delta"])
                if on_chain:
                    for skill, data in ai_report.get("suggested_skills", {}).items():
                        set_skill_on_chain(freelancer_address, skill, data["new_level"], job_id)
            else:
                reject_work_on_chain(job_id, ai_report["detailed_report"][:200])

    except Exception as e:
        print(f"[evaluate] Background task failed for {escrow_id}: {e}")
        update_status(escrow_id, "error")


# ─────────────────────────────────────────
# 2. REPORT — client or DAO voters read the evaluation
# ─────────────────────────────────────────
@app.get("/report/{escrow_id}")
async def get_report(escrow_id: str):
    """
    Step 2: Client (or DAO voters) read the AI evaluation report.
    Poll this endpoint — status progresses: evaluating → pending → dao_pending/dao_resolved.
    """
    record = get_pending(escrow_id)
    if not record:
        raise HTTPException(404, "No evaluation found for this escrow")

    ai_report = record.get("ai_report")
    return {
        "escrow_id": escrow_id,
        "freelancer": record["freelancer"],
        "status": record["status"],
        "detailed_report": ai_report["detailed_report"] if ai_report else None,
        "recommendation": ai_report["recommendation"] if ai_report else None,
        "confidence_score": ai_report.get("confidence_score") if ai_report else None,
        "task_complexity": ai_report.get("task_complexity") if ai_report else None,
        "code_issues": ai_report.get("code_issues", []) if ai_report else [],
        "requirements_check": ai_report.get("requirements_check", []) if ai_report else [],
        "files_submitted": ai_report.get("files_submitted", []) if ai_report else [],
        "files_missing": ai_report.get("files_missing", []) if ai_report else [],
        "elo": ai_report.get("elo") if ai_report else None,
        "suggested_reputation_delta": ai_report.get("suggested_reputation_delta") if ai_report else None,
        "suggested_skills": ai_report.get("suggested_skills", {}) if ai_report else {},
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
    result = _finalise(body.escrow_id, record, approved=body.approved, keep_files=True)

    return result


# ─────────────────────────────────────────
# 5. FILE ACCESS — browse and download code after DAO resolution
# ─────────────────────────────────────────
_FILE_ACCESSIBLE_STATUSES = {"pending", "dao_pending", "dao_resolved"}


@app.get("/files/{escrow_id}")
async def list_files(escrow_id: str):
    """
    Lists all files available for a submission.
    Files are accessible during review (pending), DAO voting (dao_pending), and after DAO resolution.
    They are deleted on direct client approve/reject.
    """
    record = get_pending(escrow_id)
    if not record:
        raise HTTPException(404, "No evaluation found for this escrow")
    if record["status"] not in _FILE_ACCESSIBLE_STATUSES:
        raise HTTPException(400, f"Files are not accessible in status '{record['status']}'")

    return {
        "escrow_id": escrow_id,
        "files": list_submission_files(escrow_id),
    }


@app.get("/files/{escrow_id}/{filename}")
async def download_file(escrow_id: str, filename: str):
    """
    Downloads a single file from a submission.
    Accessible during pending, dao_pending, and dao_resolved statuses.
    """
    record = get_pending(escrow_id)
    if not record:
        raise HTTPException(404, "No evaluation found for this escrow")
    if record["status"] not in _FILE_ACCESSIBLE_STATUSES:
        raise HTTPException(400, f"Files are not accessible in status '{record['status']}'")

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
# RATE TASK — AI rates the task difficulty before work starts
# ─────────────────────────────────────────
class RateTaskRequest(BaseModel):
    escrow_id: str
    task_description: str
    required_skills: list[str] = []


@app.post("/rate-task")
async def rate_task_endpoint(body: RateTaskRequest):
    """
    AI rates the task difficulty and stores the result in the DB.

    Call this when a task is posted / before freelancers apply.
    The stored complexity_score is later used in the Elo formula when
    evaluating the freelancer's submission.

    Returns:
        escrow_id, task_rating (0-100), complexity_score (100-1000), reasoning
    """
    result = rate_task(body.task_description, body.required_skills)

    save_task_rating(
        body.escrow_id,
        result["task_rating"],
        result["complexity_score"],
        result["reasoning"],
    )

    return {
        "escrow_id":        body.escrow_id,
        "task_rating":      result["task_rating"],
        "complexity_score": result["complexity_score"],
        "estimated_files":  result["estimated_files"],
        "estimated_hours":  result["estimated_hours"],
        "reasoning":        result["reasoning"],
        "clarity_score":    result["clarity_score"],
        "clarity_issues":   result["clarity_issues"],
    }


@app.get("/rate-task/{escrow_id}")
async def get_task_rating_endpoint(escrow_id: str):
    """Returns the stored task rating for a given escrow."""
    record = get_task_rating(escrow_id)
    if not record:
        raise HTTPException(404, "No task rating found for this escrow")
    return {"escrow_id": escrow_id, **record}


# ─────────────────────────────────────────
# ELO — read a freelancer's current Elo rating
# ─────────────────────────────────────────
@app.get("/elo/{address}")
async def get_elo(address: str):
    """Returns current Elo, tier, and task count for a freelancer wallet address (from chain)."""
    data = get_elo_from_chain(address)
    return {
        "address": address,
        "elo": data["elo"],
        "tier": get_tier(data["elo"]),
        "tasks_completed": data["tasks_completed"],
        "starting_elo": STARTING_ELO,
    }


# ─────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok"}


# ─────────────────────────────────────────
# UTILITIES
# ─────────────────────────────────────────
def _parse_job_id(escrow_id: str) -> int | None:
    """Returns integer job ID if escrow_id is a plain number, else None."""
    try:
        return int(escrow_id)
    except (ValueError, TypeError):
        return None


# ─────────────────────────────────────────
# INTERNAL HELPER
# ─────────────────────────────────────────
def _rejection_penalty(tasks_completed: int) -> int:
    """
    Scales the reputation penalty for rejected work by experience.
    New freelancers get a lighter penalty; veterans are held to a higher standard.
      0-3 jobs:  -5
      4-10 jobs: -8
      11-20 jobs:-12
      21+ jobs:  -15
    """
    if tasks_completed <= 3:
        return -5
    if tasks_completed <= 10:
        return -8
    if tasks_completed <= 20:
        return -12
    return -15


def _finalise(escrow_id: str, record: dict, approved: bool, keep_files: bool = False) -> dict:
    """
    Applies Elo + reputation changes at the moment of client or DAO decision.
    keep_files=False: deletes files and DB record immediately (direct client decision).
    keep_files=True:  sets status to 'dao_resolved', keeps files for download (DAO path).
    """
    ai_report = record["ai_report"]
    freelancer = record["freelancer"]

    # ── Reputation (on-chain style delta) ─────────────────────────────
    if approved:
        # Always positive on approval — AI may suggest negative for poor quality,
        # but the client's decision overrides direction. Floor at +3.
        reputation_delta = max(3, ai_report.get("suggested_reputation_delta", 5))
        skill_changes = {
            skill: data["new_level"]
            for skill, data in ai_report.get("suggested_skills", {}).items()
        }
    else:
        chain_pre = get_elo_from_chain(freelancer)
        reputation_delta = _rejection_penalty(chain_pre["tasks_completed"])
        skill_changes = {}

    # ── Elo (applied here, at decision time — read/write chain) ───────
    chain_elo = get_elo_from_chain(freelancer)
    elo_result = apply_elo(
        current_elo=chain_elo["elo"],
        tasks_completed=chain_elo["tasks_completed"],
        active_modifiers=[],
        task_complexity=ai_report.get("task_complexity", 400),
        confidence_score=ai_report.get("confidence_score", 50),
        approved=approved,
    )
    update_elo_on_chain(freelancer, elo_result["elo_delta"])
    increment_tasks_on_chain(freelancer)

    if keep_files:
        update_status(escrow_id, "dao_resolved")
    else:
        delete_submission(escrow_id)

    return {
        "freelancer": freelancer,
        "skill_changes": skill_changes,
        "reputation_delta": reputation_delta,
        "elo": elo_result,
    }
