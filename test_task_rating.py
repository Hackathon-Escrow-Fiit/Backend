"""
Test script for the /rate-task endpoint.

Sends three tasks (easy / medium / hard) to the AI and prints a comparison
table so you can verify the complexity scores are well-calibrated.

Usage:
    python test_task_rating.py
"""

import requests
import json

BASE_URL = "http://localhost:8000"

# ── Task definitions ──────────────────────────────────────────────────────────

TASKS = [
    {
        "label": "EASY",
        "escrow_id": "test_easy_001",
        "task_description": (
            "Write a simple ERC20 token contract in Solidity. "
            "The token should have a fixed supply of 1,000,000 tokens minted to the deployer. "
            "Name: 'MyToken', symbol: 'MTK', 18 decimals. "
            "Use OpenZeppelin ERC20 base contract. No extra features needed."
        ),
        "required_skills": ["solidity", "erc-20"],
    },
    {
        "label": "MEDIUM",
        "escrow_id": "test_medium_001",
        "task_description": (
            "Build a decentralized escrow smart contract system. "
            "Requirements:\n"
            "- Client deposits ETH into escrow when creating the job\n"
            "- Freelancer submits work by uploading a deliverable URI (IPFS hash)\n"
            "- Client can approve (releases funds) or reject (refunds client) within 48 hours\n"
            "- If no decision within 48 hours, freelancer can claim automatically\n"
            "- Platform takes a 2.5% fee on successful completion\n"
            "- Must use ReentrancyGuard and Ownable from OpenZeppelin\n"
            "- Write unit tests covering all state transitions\n"
            "- Deploy script for Hardhat\n"
            "Deliverables: Escrow.sol, Escrow.test.ts, deploy script."
        ),
        "required_skills": ["solidity", "hardhat", "typescript", "smart-contracts"],
    },
    {
        "label": "HARD",
        "escrow_id": "test_hard_001",
        "task_description": (
            "Design and implement a full DeFi lending protocol with the following features:\n\n"
            "CORE PROTOCOL:\n"
            "- Users can supply ERC20 tokens as collateral and borrow other tokens\n"
            "- Dynamic interest rates based on utilization ratio (like Aave's rate model)\n"
            "- Liquidation mechanism: positions below 120% collateral ratio can be liquidated\n"
            "- Liquidator receives 5% bonus on liquidated collateral\n"
            "- Price oracle integration (Chainlink) for asset valuation\n\n"
            "TOKENOMICS:\n"
            "- Protocol governance token with voting on interest rate parameters\n"
            "- Liquidity mining: suppliers earn protocol tokens proportional to supplied value\n\n"
            "SECURITY REQUIREMENTS:\n"
            "- Full test suite with 95%+ coverage (Hardhat + Foundry)\n"
            "- Slither static analysis with zero critical findings\n"
            "- Formal specification of the liquidation invariant\n"
            "- Gas optimization: all core operations under 200k gas\n\n"
            "DELIVERABLES:\n"
            "- 8+ Solidity contracts (LendingPool, RateModel, Oracle, Liquidator, GovernanceToken, etc.)\n"
            "- Full test suite\n"
            "- Deployment scripts for Base Sepolia and Mainnet\n"
            "- Technical documentation and architecture diagram\n"
            "- Security audit checklist"
        ),
        "required_skills": ["solidity", "defi", "security-audit", "chainlink", "foundry", "hardhat"],
    },
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def bar(value: int, max_val: int = 100, width: int = 30) -> str:
    filled = round(value / max_val * width)
    return "█" * filled + "░" * (width - filled)


def clarity_label(score: int) -> str:
    if score >= 75: return "✅ Clear"
    if score >= 50: return "⚠️  Partial"
    return "❌ Vague"


# ── Test ──────────────────────────────────────────────────────────────────────

def test_health() -> bool:
    try:
        r = requests.get(f"{BASE_URL}/health", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def rate_task(task: dict) -> dict | None:
    try:
        r = requests.post(
            f"{BASE_URL}/rate-task",
            json={
                "escrow_id":        task["escrow_id"],
                "task_description": task["task_description"],
                "required_skills":  task["required_skills"],
            },
            timeout=60,
        )
        if r.status_code != 200:
            print(f"  HTTP {r.status_code}: {r.text}")
            return None
        return r.json()
    except Exception as e:
        print(f"  Error: {e}")
        return None


def print_result(label: str, task: dict, result: dict):
    print()
    print("=" * 60)
    print(f"  {label} TASK")
    print("=" * 60)
    print(f"  Task:             {task['task_description'][:80].strip()}…")
    print(f"  Skills:           {', '.join(task['required_skills'])}")
    print()
    print(f"  Difficulty:       {result['task_rating']:3d}/100  {bar(result['task_rating'])}")
    print(f"  Complexity score: {result['complexity_score']:4d}/1000  "
          f"(Elo scale — used in freelancer rating formula)")
    print(f"  Est. files:       ~{result['estimated_files']} files")
    print(f"  Est. hours:       ~{result['estimated_hours']}h for an experienced dev")
    print()
    print(f"  Reasoning:")
    for line in result['reasoning'].split(". "):
        line = line.strip()
        if line:
            print(f"    • {line.rstrip('.')}")
    print()
    print(f"  Clarity:          {result['clarity_score']:3d}/100  {clarity_label(result['clarity_score'])}")
    if result.get("clarity_issues"):
        print(f"  Issues found:")
        for issue in result["clarity_issues"]:
            print(f"    ⚠  {issue}")
    else:
        print(f"  No clarity issues — description is complete.")


def main():
    print("\n" + "=" * 60)
    print("  TASK RATING CALIBRATION TEST")
    print("  Testing 3 tasks: Easy / Medium / Hard")
    print("=" * 60)

    if not test_health():
        print("\n❌ Backend not running. Start with: uvicorn main:app --reload --port 8000")
        return

    print("✅ Backend is running\n")

    results = []
    for task in TASKS:
        print(f"Sending {task['label']} task to AI…", end=" ", flush=True)
        result = rate_task(task)
        if result is None:
            print("FAILED")
            continue
        print("done")
        results.append((task["label"], task, result))

    # Print individual results
    for label, task, result in results:
        print_result(label, task, result)

    # Summary comparison table
    if len(results) == 3:
        print()
        print("=" * 60)
        print("  SUMMARY COMPARISON")
        print("=" * 60)
        print(f"  {'Task':<8}  {'Difficulty':>10}  {'Complexity':>10}  {'Files':>6}  {'Hours':>6}  {'Clarity':>8}")
        print(f"  {'-'*8}  {'-'*10}  {'-'*10}  {'-'*6}  {'-'*6}  {'-'*8}")
        for label, _, r in results:
            print(
                f"  {label:<8}  "
                f"{r['task_rating']:>9}/100  "
                f"{r['complexity_score']:>9}/1000  "
                f"{r['estimated_files']:>5}f  "
                f"{r['estimated_hours']:>5}h  "
                f"{r['clarity_score']:>7}/100"
            )
        print()

        # Sanity check
        scores = [r["complexity_score"] for _, _, r in results]
        if scores[0] < scores[1] < scores[2]:
            print("✅ Scores are correctly ordered: Easy < Medium < Hard")
        else:
            print("⚠️  Warning: scores are NOT in expected order Easy < Medium < Hard")
            print(f"   Got: {scores[0]} / {scores[1]} / {scores[2]}")

    print()
    print("=" * 60)
    print("  DONE")
    print("=" * 60)


if __name__ == "__main__":
    main()
