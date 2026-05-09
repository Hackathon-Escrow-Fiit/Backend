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
    "task_complexity": integer 400-2400,
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
