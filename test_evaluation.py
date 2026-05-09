"""
Test script to verify AI evaluation endpoint works correctly.

This script:
1. Creates a sample smart contract file
2. Sends it to the /evaluate endpoint
3. Prints the detailed AI report
4. Tests the /report endpoint
5. Tests the /finalize endpoint

Usage:
    python test_evaluation.py
"""

import requests
import json
import time
from pathlib import Path

BASE_URL        = "http://localhost:8000"
TASK_DESCRIPTION = (
    "Create a simple escrow smart contract that allows the client to release "
    "funds to the freelancer or refund to themselves. Must include basic access control."
)
REQUIRED_SKILLS = ["solidity", "smart-contracts"]
FREELANCER_ADDR = "0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb1"

# Sample Solidity contract for testing
SAMPLE_CONTRACT = """
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract SimpleEscrow {
    address public client;
    address public freelancer;
    uint256 public amount;
    bool public isCompleted;

    constructor(address _freelancer) payable {
        client = msg.sender;
        freelancer = _freelancer;
        amount = msg.value;
        isCompleted = false;
    }

    function release() public {
        require(msg.sender == client, "Only client can release");
        require(!isCompleted, "Already completed");

        isCompleted = true;
        payable(freelancer).transfer(amount);
    }

    function refund() public {
        require(msg.sender == client, "Only client can refund");
        require(!isCompleted, "Already completed");

        isCompleted = true;
        payable(client).transfer(amount);
    }
}
"""

def test_health():
    """Test if the server is running"""
    print("=" * 60)
    print("Testing /health endpoint...")
    print("=" * 60)

    response = requests.get(f"{BASE_URL}/health")
    print(f"Status: {response.status_code}")
    print(f"Response: {response.json()}")
    print()
    return response.status_code == 200

def test_rate_task(escrow_id: str) -> bool:
    """Step 0: AI rates the task difficulty before any work is submitted."""
    print("=" * 60)
    print("Testing /rate-task endpoint...")
    print("=" * 60)

    payload = {
        "escrow_id":        escrow_id,
        "task_description": TASK_DESCRIPTION,
        "required_skills":  REQUIRED_SKILLS,
    }

    try:
        response = requests.post(f"{BASE_URL}/rate-task", json=payload)
        print(f"Status: {response.status_code}")

        if response.status_code != 200:
            print(f"Error: {response.text}")
            return False

        r = response.json()
        print(f"\n  Escrow ID:        {r['escrow_id']}")
        print(f"  Task Rating:      {r['task_rating']} / 100")
        print(f"  Complexity Score: {r['complexity_score']} / 1000  (used in Elo formula)")
        print(f"  Reasoning:        {r['reasoning']}")
        print()
        return True

    except Exception as e:
        print(f"Error: {e}")
        return False


def test_evaluate(escrow_id: str):
    """Test the evaluation endpoint (async — polls /report until done)."""
    print("=" * 60)
    print("Testing /evaluate endpoint...")
    print("=" * 60)

    contract_path = Path("test_contract.sol")
    contract_path.write_text(SAMPLE_CONTRACT)

    data = {
        'escrow_id':         escrow_id,
        'freelancer_address': FREELANCER_ADDR,
        'customer_task':     TASK_DESCRIPTION,
        'required_skills':   json.dumps(REQUIRED_SKILLS),
    }

    print(f"Sending evaluation request for escrow: {data['escrow_id']}")
    print(f"Task: {data['customer_task']}")
    print(f"Required skills: {data['required_skills']}")
    print()

    try:
        with open(contract_path, 'rb') as f:
            response = requests.post(
                f"{BASE_URL}/evaluate",
                files={'files': ('SimpleEscrow.sol', f, 'text/plain')},
                data=data
            )

        print(f"Status: {response.status_code}")

        if response.status_code != 200:
            print(f"Error: {response.text}")
            return None

        result = response.json()
        print(f"Accepted — status: {result['status']} (AI running in background…)\n")

        # Poll /report until status leaves 'evaluating'
        print("Polling /report for result", end="", flush=True)
        report = {}
        for _ in range(120):  # up to 2 minutes
            time.sleep(5)
            print(".", end="", flush=True)
            report = requests.get(f"{BASE_URL}/report/{escrow_id}").json()
            if report.get("status") != "evaluating":
                break
        print()

        status = report.get("status")
        if status == "evaluating":
            print("Timed out waiting for AI evaluation.")
            return None
        if status == "error":
            print("Background evaluation failed — check server logs.")
            return None

        print(f"\nEscrow ID:      {report['escrow_id']}")
        print(f"Freelancer:     {report['freelancer']}")
        print(f"Recommendation: {report['recommendation']}")
        print(f"Confidence:     {report.get('confidence_score')}%")
        print(f"Complexity:     {report.get('task_complexity')}")

        elo = report.get("elo")
        if elo:
            fb = elo.get("formula_breakdown", {})
            print("\n" + "=" * 60)
            print("ELO FORMULA BREAKDOWN:")
            print("=" * 60)
            print(f"  Formula:           {fb.get('formula')}")
            print(f"  BASE               = {fb.get('BASE')}  (max Elo per task)")
            print(f"  confidence_score   = {fb.get('confidence_score')}%")
            print(f"  ai                 = {fb.get('ai')}  (confidence / 100)")
            print(f"  freelancer_elo     = {fb.get('freelancer_elo')}")
            print(f"  task_complexity    = {fb.get('task_complexity')}")
            print(f"  E (expected score) = {fb.get('E_expected')}  "
                  f"= 1 / (1 + 10^(({fb.get('task_complexity')} - {fb.get('freelancer_elo')}) / 400))")
            print(f"  tasks_completed    = {fb.get('tasks_completed')}")
            print(f"  nur (new-user mult)= {fb.get('nur_multiplier')}  "
                  f"(2.5 at task 0 → 1.0 at task 30+)")
            print(f"  rating_factor      = {fb.get('rating_factor')}  "
                  f"(1.0 at elo 300 → 0.1 at elo 1000, gains shrink as rating rises)")
            print(f"  raw_delta          = {fb.get('raw_delta')}")
            print(f"  final_delta        = {fb.get('final_delta')}")
            print(f"  approved           = {fb.get('approved')}")
            print(f"\n  Elo: {elo.get('old_elo')} → {elo.get('new_elo')}  "
                  f"(+{elo.get('elo_delta')} | tier: {elo.get('old_tier')} → {elo.get('new_tier')})")
            print("=" * 60)

        print("\n" + "=" * 60)
        print("DETAILED AI REPORT:")
        print("=" * 60)
        print(report['detailed_report'])
        print("=" * 60)
        return True

    except Exception as e:
        print(f"Error: {e}")
        return False
    finally:
        try:
            if contract_path.exists():
                contract_path.unlink()
        except PermissionError:
            print(f"Warning: Could not delete {contract_path} (file in use)")
        print()

def test_get_report(escrow_id):
    """Test the get report endpoint"""
    print("=" * 60)
    print(f"Testing /report/{escrow_id} endpoint...")
    print("=" * 60)

    try:
        response = requests.get(f"{BASE_URL}/report/{escrow_id}")

        print(f"Status: {response.status_code}")

        if response.status_code == 200:
            result = response.json()
            print(f"\nEscrow ID: {result['escrow_id']}")
            print(f"Freelancer: {result['freelancer']}")
            print(f"Status: {result['status']}")
            print(f"Recommendation: {result['recommendation']}")
            print("\nReport retrieved successfully!")
        else:
            print(f"Error: {response.text}")

    except Exception as e:
        print(f"Error: {e}")

    print()

def test_finalize(escrow_id, work_approved=True):
    """Test the client-decision endpoint"""
    print("=" * 60)
    print(f"Testing /client-decision endpoint (work_approved={work_approved})...")
    print("=" * 60)

    data = {
        'escrow_id': escrow_id,
        'decision': 'approve' if work_approved else 'reject'
    }

    try:
        response = requests.post(
            f"{BASE_URL}/client-decision",
            json=data,
            headers={'Content-Type': 'application/json'}
        )

        print(f"Status: {response.status_code}")

        if response.status_code == 200:
            result = response.json()
            print(f"\nFreelancer:       {result['freelancer']}")
            print(f"Reputation Delta: {result['reputation_delta']}")
            print(f"Skill Changes:    {json.dumps(result['skill_changes'], indent=2)}")

            elo = result.get("elo")
            if elo:
                fb = elo.get("formula_breakdown", {})
                print("\n--- Elo formula at decision time ---")
                print(f"  Formula:  {fb.get('formula')}")
                print(f"  BASE={fb.get('BASE')}  ai={fb.get('ai')}  "
                      f"E={fb.get('E_expected')}  nur={fb.get('nur_multiplier')}")
                print(f"  raw={fb.get('raw_delta')}  →  delta={fb.get('final_delta')}")
                print(f"  Elo: {elo['old_elo']} → {elo['new_elo']}  "
                      f"(tier: {elo['old_tier']} → {elo['new_tier']})")

            print("\nFinalization successful!")
        else:
            print(f"Error: {response.text}")

    except Exception as e:
        print(f"Error: {e}")

    print()

def main():
    print("\n" + "=" * 60)
    print("DECENTRAWORK AI EVALUATION TEST")
    print("=" * 60)
    print()

    # Test 1: Health check
    if not test_health():
        print("❌ Server is not running. Start it with: uvicorn main:app --reload")
        return

    print("✅ Server is running!\n")

    escrow_id = f"test_escrow_{int(time.time())}"

    # Test 2: Rate the task before any work is submitted
    if not test_rate_task(escrow_id):
        print("❌ Task rating failed. Check the error above.")
        return

    print("✅ Task rated!\n")

    # Test 3: Evaluate submission
    if not test_evaluate(escrow_id):
        print("❌ Evaluation failed. Check the error above.")
        return

    print("✅ Evaluation successful!\n")

    # Test 4: Get report
    test_get_report(escrow_id)
    print("✅ Report retrieval successful!\n")

    # Test 5: Finalize (approved)
    test_finalize(escrow_id, work_approved=True)
    print("✅ Finalization successful!\n")

    print("=" * 60)
    print("ALL TESTS COMPLETED!")
    print("=" * 60)

if __name__ == "__main__":
    main()
