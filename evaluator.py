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


def read_files(file_paths: list[str]) -> str:
    """
    Reads all submitted files and concatenates them into a single string
    that can be passed to the AI model as context.
    """
    result = ""
    for path_str in file_paths:
        path = Path(path_str)
        if not path.exists():
            continue
        try:
            content = path.read_text(encoding="utf-8")
            result += f"\n\n--- FILE: {path.name} ---\n{content}\n--- END ---"
        except Exception:
            result += f"\n\n--- FILE: {path.name} ---\n[BINARY OR UNREADABLE FILE]\n--- END ---"
    return result


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

    files_content = read_files(file_paths)

    # Handle empty submission before calling AI
    if not files_content.strip():
        return {
            "detailed_report": (
                "## Evaluation Report\n\n"
                "**Status:** REJECTED - No submission provided\n\n"
                "### Summary\n"
                "No files were submitted or all files were unreadable. "
                "The freelancer did not provide any deliverables for evaluation.\n\n"
                "### Recommendation\n"
                "Reject this submission and apply reputation penalty for non-delivery."
            ),
            "recommendation": "reject",
            "confidence_score": 0,
            "task_complexity": 400,
            "suggested_reputation_delta": -10,
            "suggested_skills": {},
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

    response = client.chat.completions.create(
        model="meta/llama-3.3-70b-instruct",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a senior technical reviewer for a decentralized freelance platform. "
                    "Your job is to produce actionable, specific code reviews — not vague summaries. "
                    "Always cite the exact file name and approximate line number for every issue. "
                    "Quote the problematic code snippet and explain precisely why it is wrong or risky. "
                    "Always respond with valid JSON only. No extra text, no markdown fences, no code blocks."
                )
            },
            {
                "role": "user",
                "content": f"""
Evaluate this freelance submission and create a detailed technical report for the client and DAO voters.

=== CUSTOMER TASK ===
{customer_task}

=== REQUIRED SKILLS ===
{', '.join(required_skills)}

=== FREELANCER CURRENT PROFILE ===
Reputation: {current_reputation}
Skills:
{skills_context}

=== SUBMITTED FILES ===
{files_content}

=== INSTRUCTIONS ===
Produce a comprehensive report. Be brutally specific — every issue must name the file and line.

**Sections required in detailed_report (markdown, min 300 words):**
1. **What was delivered** — summarise what was built
2. **What the freelancer did wrong** — for EVERY problem:
   - State the file name and approximate line number
   - Paste the EXACT problematic lines inside a fenced code block (```language ... ```)
   - Explain precisely WHY it is wrong (security risk? bug? wrong pattern? missing requirement?)
   - Show the corrected version in a second code block
   The report must be fully self-contained: DAO voters cannot see the source files, so every issue must include the literal code so they can judge without accessing the repository.
3. **What was done well** — specific good practices, also quoting the code that demonstrates them
4. **Task completion** — which acceptance criteria are met and which are missing
5. **Skill demonstration** — how well each required skill was shown
6. **Final recommendation** — approve / reject / escalate_to_dao with one-sentence justification

**confidence_score** — your overall score 0-100 for the submission quality (85 = solid work with minor issues, 40 = significant problems, 10 = nearly nothing working)

**task_complexity** — rate the complexity of the TASK (not the submission) on a scale 100-1000:
  100-200: trivial — hello world, tiny scripts
  200-400: simple — basic CRUD, simple scripts, UI components
  400-600: moderate — authentication, basic smart contracts, REST APIs
  600-800: complex — DeFi protocols, multi-service architectures, security-critical systems
  800-1000: expert — novel algorithms, cross-chain bridges, highly optimised systems

Reputation delta guide: +20 excellent, +10 good, +5 acceptable, 0 borderline, -10 poor, -20 failed/dangerous code

Respond ONLY with this JSON (no markdown, no extra text):
{{
    "detailed_report": "Full markdown report with specific file:line references (min 300 words)",
    "recommendation": "approve" | "reject" | "escalate_to_dao",
    "confidence_score": integer 0-100,
    "task_complexity": integer 100-1000,
    "suggested_reputation_delta": integer -20 to +20,
    "suggested_skills": {{
        "skill_name": {{
            "new_level": 0.0-10.0,
            "reasoning": "one sentence"
        }}
    }},
    "code_issues": [
        {{
            "file": "filename.ext",
            "line": "approximate line or range e.g. 42 or 38-45",
            "severity": "critical" | "high" | "medium" | "low",
            "issue": "short title",
            "detail": "what the bad code does and why it is wrong",
            "snippet": "the actual problematic code (max 3 lines)",
            "fix": "what should be written instead"
        }}
    ]
}}
"""
            }
        ],
        temperature=0.2
    )

    raw = response.choices[0].message.content.strip()

    # Safe JSON parsing
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start != -1 and end > start:
            try:
                result = json.loads(raw[start:end])
            except json.JSONDecodeError:
                result = None
        else:
            result = None

    if result is None:
        return {
            "detailed_report": (
                "## Evaluation Report\n\n"
                "**Status:** ERROR — AI evaluation failed to produce a parseable response.\n\n"
                "Manual review is required."
            ),
            "recommendation": "escalate_to_dao",
            "confidence_score": 0,
            "task_complexity": 400,
            "suggested_reputation_delta": 0,
            "suggested_skills": {},
            "code_issues": []
        }

    # Ensure required fields exist with safe defaults
    result.setdefault("confidence_score", 50)
    result.setdefault("task_complexity", 400)
    result.setdefault("code_issues", [])
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
