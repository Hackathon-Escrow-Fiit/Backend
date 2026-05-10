import json
import os
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# NVIDIA NIM uses the same interface as OpenAI — just a different base_url and key
client = OpenAI(
    api_key=os.getenv("NVIDIA_API_KEY"),
    base_url="https://integrate.api.nvidia.com/v1"
)


def read_files(file_paths: list[str]) -> tuple[str, list[str]]:
    """
    Reads all submitted files with line numbers so the AI can cite exact lines.
    Returns (concatenated_content, list_of_file_names).
    """
    result = ""
    names: list[str] = []
    for path_str in file_paths:
        path = Path(path_str)
        if not path.exists():
            continue
        names.append(path.name)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            numbered = "\n".join(f"{i + 1:4d} | {line}" for i, line in enumerate(lines))
            result += f"\n\n=== FILE: {path.name} ({len(lines)} lines) ===\n{numbered}\n=== END {path.name} ===\n"
        except Exception:
            result += f"\n\n=== FILE: {path.name} ===\n[BINARY OR UNREADABLE]\n=== END {path.name} ===\n"
    return result, names


def run_ai_evaluation(
    customer_task: str,
    file_paths: list[str],
    required_skills: list[str],
    current_reputation: int,
    current_skills: dict
) -> dict:
    """
    Generates a detailed evaluation report for the client and DAO voters.

    This function analyzes the freelancer's submission and creates a comprehensive
    text report explaining what was done, what's good, what's bad, code issues,
    and recommendations. The report is shown to the client and DAO for decision-making.

    Args:
        customer_task       - task description written by the client
        file_paths          - list of paths to submitted files
        required_skills     - e.g. ["solidity", "react"]
        current_reputation  - freelancer's current reputation score (0-1000)
        current_skills      - e.g. {"solidity": {"level": 6.2, "jobs": 5}}

    Returns:
        dict with:
            - detailed_report: full text explanation for client/DAO
            - recommendation: "approve" or "reject" or "escalate_to_dao"
            - suggested_reputation_delta: AI's suggestion for reputation change
            - suggested_skills: AI's suggestion for skill level changes
    """

    files_content, submitted_names = read_files(file_paths)

    # Handle empty submission before calling AI
    if not files_content.strip():
        return {
            "detailed_report": (
                "## Evaluation Report\n\n"
                "**Status:** REJECTED — No files submitted\n\n"
                "The freelancer did not upload any readable files. "
                "Nothing can be evaluated."
            ),
            "recommendation": "reject",
            "confidence_score": 0,
            "task_complexity": 400,
            "suggested_reputation_delta": -15,
            "suggested_skills": {},
            "requirements_check": [],
            "files_submitted": [],
            "files_missing": ["all deliverables"],
            "code_issues": []
        }

    # Build skill context string for the prompt
    if current_skills:
        skills_context = "\n".join(
            f"- {skill}: {data['level']}/10 ({data['jobs']} jobs completed)"
            for skill, data in current_skills.items()
        )
    else:
        skills_context = "No previous skill evaluations (new freelancer)"

    submitted_list = "\n".join(f"  - {n}" for n in submitted_names) or "  (none)"

    response = client.chat.completions.create(
        model="meta/llama-3.3-70b-instruct",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a strict senior code reviewer for a decentralized freelance platform. "
                    "Real money is locked in escrow and only released when you say the work is good. "
                    "Your entire response MUST be a single valid JSON object — no text before or after, "
                    "no triple backticks inside any string value. "
                    "Every issue you report MUST reference the exact line number from the numbered source code provided. "
                    "The snippet field must be a single line copied verbatim from the file (replace any \" inside with '). "
                    "Never invent issues that are not visible in the source code shown."
                )
            },
            {
                "role": "user",
                "content": f"""
You are reviewing a freelancer submission. The client will only release payment if you confirm the work is complete and correct.

=== TASK DESCRIPTION (what the client paid for) ===
{customer_task}

=== REQUIRED SKILLS ===
{', '.join(required_skills)}

=== FILES SUBMITTED BY FREELANCER ({len(submitted_names)} file(s)) ===
{submitted_list}

=== FULL FILE CONTENTS WITH LINE NUMBERS ===
{files_content}

=== FREELANCER PROFILE ===
Reputation: {current_reputation} | Skills: {skills_context}

=== YOUR JOB — DO EACH STEP IN ORDER ===

STEP 1 — FILES CHECK:
  Look at the submitted files list above. Based on the task, what files SHOULD be there?
  List every file that is MISSING. If a file is empty or has dummy/placeholder content, mark it missing.
  Put results in: files_submitted (exactly what was given), files_missing (what should be there but is not).

STEP 2 — LINE-BY-LINE CODE REVIEW:
  Read every file carefully. For EACH real bug, vulnerability, or missing feature you find:
  - State the exact file name
  - State the exact line number (from the numbered source above — e.g. line 23)
  - Copy the EXACT line of code from that file as the snippet (replace any double-quote with single-quote)
  - Explain precisely what is wrong and why it matters
  If the file is too short, incomplete, or clearly not what the task asked for — that itself is a critical issue.

STEP 3 — REQUIREMENTS CHECK:
  Go through EACH requirement implied by the task description.
  For each: say whether it is met, and if not — exactly what is missing (which function, which file, which line).

STEP 4 — SCORE:
  confidence_score 0-100 (grade the SUBMISSION quality, not the task difficulty):
    90-100: production-ready, deploy tomorrow
    70-89:  works, a few real issues to fix
    50-69:  partially done, important pieces missing
    20-49:  major gaps, significant rework needed
    0-19:   almost nothing works, empty, or dangerous

  task_complexity 100-1000 (grade the TASK difficulty, not the submission):
    100-300: simple scripts, basic tokens
    300-500: standard contracts, REST APIs
    500-700: DeFi protocols, multi-contract systems
    700-1000: cross-chain, novel algorithms, security-critical

  suggested_reputation_delta: +20 excellent, +10 good, +5 acceptable, 0 borderline, -10 poor, -15 dangerous/empty

STEP 5 — RECOMMENDATION:
  "approve"         — task is done, meets requirements, no critical bugs
  "reject"          — task is incomplete, has critical bugs, or files are missing
  "escalate_to_dao" — borderline case needing human judgment

=== JSON FORMAT (respond with ONLY this, no other text) ===
{{
    "files_submitted": ["list", "of", "actual", "submitted", "filenames"],
    "files_missing": ["list of files that should exist but do not"],
    "recommendation": "approve",
    "confidence_score": 75,
    "task_complexity": 400,
    "suggested_reputation_delta": 8,
    "suggested_skills": {{
        "solidity": {{"new_level": 7.0, "reasoning": "one sentence"}}
    }},
    "requirements_check": [
        {{
            "requirement": "exact requirement from the task",
            "met": true,
            "detail": "how it was met OR exactly what is missing (which file, which function, which line)"
        }}
    ],
    "code_issues": [
        {{
            "file": "Contract.sol",
            "line": "23",
            "severity": "critical",
            "issue": "Short title of the problem",
            "detail": "Full explanation: what the code does, why it is wrong, what can go wrong",
            "snippet": "exact line 23 copied from source (single quotes only, max 120 chars)"
        }}
    ],
    "detailed_report": "## Files Received\\n\\n- file1.sol (45 lines)\\n\\n## Missing Files\\n\\n- tests/\\n\\n## Code Issues\\n\\n### Critical: Reentrancy in withdraw() (Contract.sol line 23)\\nLine 23: ...\\nThis is dangerous because...\\n\\n## What Was Done Well\\n\\n...\\n\\n## Final Verdict\\n\\nREJECT — missing test suite and reentrancy vulnerability on line 23."
}}
"""
            }
        ],
        temperature=0.2,
        max_tokens=4096,
    )

    raw = response.choices[0].message.content.strip()

    # Strip markdown fences the model sometimes wraps around the JSON
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        if raw.endswith("```"):
            raw = raw.rsplit("```", 1)[0]
        raw = raw.strip()

    # Safe JSON parsing with progressively broader extraction
    result = None
    for attempt in (raw, raw[raw.find("{"):raw.rfind("}") + 1] if "{" in raw else ""):
        if not attempt:
            continue
        try:
            result = json.loads(attempt)
            break
        except json.JSONDecodeError:
            pass

    if result is None:
        print(f"[evaluator] JSON parse failed. Raw response (first 500 chars): {raw[:500]}")
        return {
            "detailed_report": (
                "## Evaluation Report\n\n"
                "**Note:** AI response could not be parsed as JSON. Raw output below:\n\n"
                + raw[:3000]
            ),
            "recommendation": "escalate_to_dao",
            "confidence_score": 0,
            "task_complexity": 400,
            "suggested_reputation_delta": 0,
            "suggested_skills": {},
            "requirements_check": [],
            "files_submitted": submitted_names,
            "files_missing": [],
            "code_issues": []
        }

    # Ensure required fields exist with safe defaults
    result.setdefault("confidence_score", 50)
    result.setdefault("task_complexity", 400)
    result.setdefault("code_issues", [])
    result.setdefault("requirements_check", [])
    result.setdefault("files_submitted", submitted_names)
    result.setdefault("files_missing", [])
    return result


def rate_task(task_description: str, required_skills: list[str]) -> dict:
    """
    AI rates the task difficulty AND clarity.

    Returns:
        task_rating      — overall difficulty 0-100
        complexity_score — Elo-scale complexity 100-1000
        estimated_files  — rough number of files/contracts needed
        estimated_hours  — rough man-hours for an experienced dev
        reasoning        — what makes it hard/easy
        clarity_score    — how clear the task description is (0-100)
        clarity_issues   — list of missing/ambiguous details
    """
    response = client.chat.completions.create(
        model="meta/llama-3.3-70b-instruct",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a senior tech lead evaluating freelance tasks for a decentralized platform. "
                    "You assess BOTH difficulty and whether the task description gives a freelancer enough information to start work. "
                    "Be calibrated and use the full range — most tasks fall between 150 and 750. "
                    "Always respond with valid JSON only. No extra text, no markdown fences."
                )
            },
            {
                "role": "user",
                "content": f"""
Evaluate this freelance task for complexity and clarity.

=== TASK DESCRIPTION ===
{task_description}

=== REQUIRED SKILLS ===
{', '.join(required_skills) if required_skills else 'not specified'}

=== COMPLEXITY CALIBRATION ===

Use these concrete anchors. Be precise — do not cluster everything at 60-80.

task_rating (0-100):
  0-15:   Copy-paste job — any beginner, < 30 min (e.g. "add a button to this page")
  16-30:  Junior task — 1-4 hours (e.g. "write a simple ERC20 token", "add a REST endpoint")
  31-50:  Mid-level — half a day to 2 days (e.g. "build a basic escrow contract with tests", "CRUD API with auth")
  51-70:  Senior — 3-7 days (e.g. "audit a 4-contract DeFi protocol", "multi-chain bridge prototype")
  71-85:  Expert — 1-3 weeks (e.g. "build an AMM from scratch", "full security audit with formal report")
  86-100: Research-level — requires novel design or deep specialisation

complexity_score (100-1000) — estimate based on deliverable size and depth:
  100-150:  1 file, < 50 LOC, trivial logic
  150-250:  1-2 files, 50-150 LOC, standard patterns only
  250-400:  2-5 files, 150-400 LOC, some custom logic (e.g. simple ERC20, basic escrow)
  400-550:  4-10 files, 400-800 LOC, moderate complexity (e.g. auditing one contract, auth system)
  550-700:  8-20 files, 800-2000 LOC, non-trivial design decisions (e.g. multi-contract DeFi, full dApp)
  700-850:  15-40 files, 2000-5000 LOC, security-critical or highly interdependent systems
  850-1000: 40+ files, 5000+ LOC, or requires novel research / cross-chain architecture

estimated_files: integer — rough number of source files the freelancer would need to produce
estimated_hours: integer — realistic hours for an experienced developer (not a beginner)

=== CLARITY EVALUATION ===

clarity_score (0-100):
  0-30:   Vague — a freelancer cannot start without asking many questions
  31-60:  Partial — key pieces are there but important details are missing
  61-80:  Good — most things are clear, minor gaps
  81-100: Excellent — acceptance criteria, tech stack, constraints all specified

clarity_issues: list of specific missing or ambiguous items. Examples:
  - "No acceptance criteria defined — what exactly does 'done' look like?"
  - "Tech stack not specified — which Solidity version / framework?"
  - "No mention of test coverage expectations"
  - "Budget seems low for the described scope"
  - "Deadline not mentioned"
  - "Unclear who the end users are"
  If everything is clear, return an empty list.

Respond ONLY with this JSON (no markdown, no extra text):
{{
    "task_rating": integer 0-100,
    "complexity_score": integer 100-1000,
    "estimated_files": integer,
    "estimated_hours": integer,
    "reasoning": "2-3 sentences explaining the complexity rating with specific references to the task",
    "clarity_score": integer 0-100,
    "clarity_issues": ["issue 1", "issue 2"]
}}
"""
            }
        ],
        temperature=0.1
    )

    raw = response.choices[0].message.content.strip()

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end   = raw.rfind("}") + 1
        if start != -1 and end > start:
            try:
                result = json.loads(raw[start:end])
            except json.JSONDecodeError:
                result = None
        else:
            result = None

    if result is None:
        return {
            "task_rating": 50, "complexity_score": 400,
            "estimated_files": 5, "estimated_hours": 16,
            "reasoning": "AI rating failed — defaulting to moderate.",
            "clarity_score": 50, "clarity_issues": [],
        }

    result["task_rating"]      = max(0,   min(100,  int(result.get("task_rating",      50))))
    result["complexity_score"] = max(100, min(1000, int(result.get("complexity_score", 400))))
    result["estimated_files"]  = max(1,         int(result.get("estimated_files",  5)))
    result["estimated_hours"]  = max(1,         int(result.get("estimated_hours", 16)))
    result["clarity_score"]    = max(0,   min(100,  int(result.get("clarity_score",    50))))
    result.setdefault("reasoning", "")
    result.setdefault("clarity_issues", [])
    return result
