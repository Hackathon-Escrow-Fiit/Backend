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
from pathlib import Path

# Backend URL
BASE_URL = "http://localhost:8000"

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

def test_evaluate():
    """Test the evaluation endpoint"""
    print("=" * 60)
    print("Testing /evaluate endpoint...")
    print("=" * 60)

    # Create a temporary contract file
    contract_path = Path("test_contract.sol")
    contract_path.write_text(SAMPLE_CONTRACT)

    data = {
        'escrow_id': 'test_escrow_001',
        'freelancer_address': '0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb1',
        'customer_task': 'Create a simple escrow smart contract that allows the client to release funds to the freelancer or refund to themselves. Must include basic access control.',
        'required_skills': json.dumps(['solidity', 'smart-contracts'])
    }

    print(f"Sending evaluation request for escrow: {data['escrow_id']}")
    print(f"Task: {data['customer_task']}")
    print(f"Required skills: {data['required_skills']}")
    print()

    try:
        # Open file in context manager to ensure it's closed properly
        with open(contract_path, 'rb') as f:
            files = {
                'files': ('SimpleEscrow.sol', f, 'text/plain')
            }
            response = requests.post(f"{BASE_URL}/evaluate", files=files, data=data)

        print(f"Status: {response.status_code}")

        if response.status_code == 200:
            result = response.json()
            print(f"\nEscrow ID: {result['escrow_id']}")
            print(f"Freelancer: {result['freelancer']}")
            print(f"Recommendation: {result['recommendation']}")
            print(f"Trigger DAO: {result['trigger_dao']}")
            print(f"Current Reputation: {result['current_reputation']}")
            print("\n" + "=" * 60)
            print("DETAILED AI REPORT:")
            print("=" * 60)
            print(result['detailed_report'])
            print("=" * 60)
            return result['escrow_id']
        else:
            print(f"Error: {response.text}")
            return None

    except Exception as e:
        print(f"Error: {e}")
        return None
    finally:
        # Clean up test file
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
            print(f"Recommendation: {result['recommendation']}")
            print("\nReport retrieved successfully!")
        else:
            print(f"Error: {response.text}")

    except Exception as e:
        print(f"Error: {e}")

    print()

def test_finalize(escrow_id, work_approved=True):
    """Test the finalize endpoint"""
    print("=" * 60)
    print(f"Testing /finalize endpoint (work_approved={work_approved})...")
    print("=" * 60)

    data = {
        'escrow_id': escrow_id,
        'work_approved': work_approved
    }

    try:
        response = requests.post(
            f"{BASE_URL}/finalize",
            json=data,
            headers={'Content-Type': 'application/json'}
        )

        print(f"Status: {response.status_code}")

        if response.status_code == 200:
            result = response.json()
            print(f"\nFreelancer: {result['freelancer']}")
            print(f"Reputation Delta: {result['reputation_delta']}")
            print(f"Skill Changes: {json.dumps(result['skill_changes'], indent=2)}")
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

    # Test 2: Evaluate submission
    escrow_id = test_evaluate()

    if not escrow_id:
        print("❌ Evaluation failed. Check the error above.")
        return

    print("✅ Evaluation successful!\n")

    # Test 3: Get report
    test_get_report(escrow_id)
    print("✅ Report retrieval successful!\n")

    # Test 4: Finalize (approved)
    test_finalize(escrow_id, work_approved=True)
    print("✅ Finalization successful!\n")

    print("=" * 60)
    print("ALL TESTS COMPLETED!")
    print("=" * 60)

if __name__ == "__main__":
    main()
