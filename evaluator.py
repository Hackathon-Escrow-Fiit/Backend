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
            "suggested_reputation_delta": -10,
            "suggested_skills": {}
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
                    "You are a technical evaluator for a decentralized freelance platform. "
                    "Generate detailed evaluation reports that help clients and DAO voters make informed decisions. "
                    "Always respond with valid JSON only. No extra text, no markdown, no code blocks."
                )
            },
            {
                "role": "user",
                "content": f"""
Evaluate this freelance submission and create a detailed report for the client and DAO voters.

=== CUSTOMER TASK ===
{customer_task}

=== REQUIRED SKILLS ===
{', '.join(required_skills)}

=== FREELANCER CURRENT PROFILE ===
Reputation: {current_reputation}/1000
Skills:
{skills_context}

=== SUBMITTED FILES ===
{files_content}

=== INSTRUCTIONS ===
Create a comprehensive evaluation report that includes:

1. **What was delivered**: Describe what the freelancer actually built/submitted
2. **Code quality analysis**: Point out specific good practices and problems with file names and line references
3. **Task completion**: Does it solve the customer's requirements?
4. **Skill demonstration**: How well did they demonstrate each required skill?
5. **Issues found**: List specific problems (security issues, bugs, missing features, code smells)
6. **Positive aspects**: What was done well
7. **Final recommendation**: Should the client approve, reject, or escalate to DAO?

Be specific with code examples and file references. This report will be shown to the client and DAO voters.

Reputation delta guide: +20 excellent, +15 very good, +10 good, +5 acceptable, 0 barely acceptable, -10 poor, -20 failed

Respond ONLY with this JSON:
{{
    "detailed_report": "Full markdown-formatted text report explaining everything (minimum 300 words)",
    "recommendation": "approve" or "reject" or "escalate_to_dao",
    "suggested_reputation_delta": integer from -20 to +20,
    "suggested_skills": {{
        "skill_name": {{
            "new_level": 0.0-10.0,
            "reasoning": "why this skill level"
        }}
    }}
}}
"""
            }
        ],
        temperature=0.2
    )

    raw = response.choices[0].message.content.strip()

    # Safe JSON parsing
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start != -1 and end > start:
            return json.loads(raw[start:end])
        # Fallback if parsing completely fails
        return {
            "detailed_report": (
                "## Evaluation Report\n\n"
                "**Status:** ERROR - AI evaluation failed\n\n"
                "The AI evaluation system encountered an error while processing this submission. "
                "Manual review is required.\n\n"
                "### Recommendation\n"
                "Escalate to DAO for human review."
            ),
            "recommendation": "escalate_to_dao",
            "suggested_reputation_delta": 0,
            "suggested_skills": {}
        }
