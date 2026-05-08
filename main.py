from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import List
import json

from storage import init_db, save_uploaded_files, save_pending, get_pending, delete_submission
from evaluator import run_ai_evaluation
from contracts import get_profile_from_chain

app = FastAPI()

# Allow frontend to call this backend from any origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize SQLite database on startup
init_db()


# ─────────────────────────────────────────
# 1. EVALUATE — freelancer submits work
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
    Generates a detailed evaluation report for the client and DAO voters.

    Returns:
        - detailed_report: Full text explanation of what was done, issues, quality, etc.
        - recommendation: AI's suggestion (approve/reject/escalate_to_dao)
        - trigger_dao: Whether this should go to DAO voting
    """
    # Check if evaluation already exists for this escrow
    existing = get_pending(escrow_id)
    if existing:
        raise HTTPException(400, "Evaluation already exists for this escrow")

    # 1. Save uploaded files temporarily
    saved_paths = save_uploaded_files(escrow_id, files)

    # 2. Read freelancer profile from blockchain
    profile = get_profile_from_chain(freelancer_address)

    # 3. Run AI evaluation - generates detailed report
    skills_list = json.loads(required_skills)
    ai_report = run_ai_evaluation(
        customer_task=customer_task,
        file_paths=saved_paths,
        required_skills=skills_list,
        current_reputation=profile["reputation"],
        current_skills=profile["skills"]
    )

    # 4. Save AI report as pending (waiting for client approve or DAO vote)
    save_pending(escrow_id, freelancer_address, ai_report)

    # 5. Return detailed report to frontend for display to client/DAO
    return {
        "escrow_id": escrow_id,
        "freelancer": freelancer_address,
        "detailed_report": ai_report["detailed_report"],
        "recommendation": ai_report["recommendation"],
        "current_reputation": profile["reputation"],
        "trigger_dao": ai_report["recommendation"] == "escalate_to_dao"
    }


# ─────────────────────────────────────────
# 2. FINALIZE — called after client/DAO decision
# ─────────────────────────────────────────
@app.post("/finalize")
async def finalize(data: dict):
    """
    Called after the client approves/rejects or DAO votes.

    Takes the decision and calculates the final skill changes and reputation delta.
    Returns only the changes needed for blockchain update.

    Args:
        escrow_id: The escrow identifier
        work_approved: boolean - true if work was approved (by client or DAO), false if rejected

    Returns:
        - freelancer: wallet address
        - skill_changes: dict of {skill_name: new_level} - only changed skills
        - reputation_delta: +/- reputation change
    """
    escrow_id = data.get("escrow_id")
    work_approved = data.get("work_approved")  # boolean: true = approved, false = rejected

    if not escrow_id or work_approved is None:
        raise HTTPException(400, "escrow_id and work_approved are required")

    # Get the stored AI report
    record = get_pending(escrow_id)
    if not record:
        raise HTTPException(404, "No pending evaluation found for this escrow")

    ai_report = record["ai_report"]
    freelancer = record["freelancer"]

    # Calculate final changes based on decision
    if work_approved:
        # Work approved - apply AI's suggested changes
        reputation_delta = ai_report["suggested_reputation_delta"]
        skill_changes = {
            skill_name: data["new_level"]
            for skill_name, data in ai_report.get("suggested_skills", {}).items()
        }
    else:
        # Work rejected - apply penalty
        reputation_delta = -15  # penalty for rejected work
        skill_changes = {}  # no skill improvements for rejected work

    # Delete files and DB record — no longer needed after finalization
    delete_submission(escrow_id)

    # Return only the changes needed for blockchain update
    return {
        "freelancer": freelancer,
        "skill_changes": skill_changes,  # dict: {"solidity": 7.2, "react": 6.5}
        "reputation_delta": reputation_delta  # integer: -15 to +20
    }


# ─────────────────────────────────────────
# 3. GET REPORT — retrieve evaluation report
# ─────────────────────────────────────────
@app.get("/report/{escrow_id}")
async def get_report(escrow_id: str):
    """
    Returns the full evaluation report for display to client or DAO voters.
    """
    record = get_pending(escrow_id)
    if not record:
        raise HTTPException(404, "No evaluation found for this escrow")

    return {
        "escrow_id": escrow_id,
        "freelancer": record["freelancer"],
        "detailed_report": record["ai_report"]["detailed_report"],
        "recommendation": record["ai_report"]["recommendation"]
    }


# ─────────────────────────────────────────
# 4. HEALTH CHECK
# ─────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok"}
