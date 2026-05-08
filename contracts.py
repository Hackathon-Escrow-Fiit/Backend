import os
import json
from web3 import Web3
from dotenv import load_dotenv

load_dotenv()

w3 = Web3(Web3.HTTPProvider(os.getenv("RPC_URL")))

# Minimal ABI — only the getter functions the backend needs to read freelancer profiles.
# Replace with the full ABI from Hardhat after compiling:
# artifacts/contracts/FreelancerRegistry.sol/FreelancerRegistry.json
REGISTRY_ABI = json.loads("""[
    {
        "name": "getProfile",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "freelancer", "type": "address"}],
        "outputs": [
            {"name": "reputation", "type": "uint256"},
            {"name": "jobsCompleted", "type": "uint256"},
            {"name": "tokensEarned", "type": "uint256"},
            {"name": "exists", "type": "bool"}
        ]
    },
    {
        "name": "getSkillNames",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "freelancer", "type": "address"}],
        "outputs": [{"name": "", "type": "string[]"}]
    },
    {
        "name": "getSkill",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "freelancer", "type": "address"},
            {"name": "skillName", "type": "string"}
        ],
        "outputs": [
            {"name": "level", "type": "uint256"},
            {"name": "jobsCount", "type": "uint256"}
        ]
    }
]""")

registry_address = os.getenv("REGISTRY_ADDRESS", "")

# Only connect to the contract if an address is provided in .env
registry = None
if registry_address:
    registry = w3.eth.contract(
        address=Web3.to_checksum_address(registry_address),
        abi=REGISTRY_ABI
    )


def get_profile_from_chain(wallet_address: str) -> dict:
    """
    Reads a freelancer's profile directly from the smart contract.

    Returns reputation score, jobs completed, and skill ratings.
    If the contract is not connected or the address is unknown,
    returns the default profile (reputation 300, no skills).
    """
    if not registry:
        # Fallback for local testing without a deployed contract
        return {
            "reputation": 300,
            "jobs_completed": 0,
            "skills": {}
        }

    try:
        addr = Web3.to_checksum_address(wallet_address)

        reputation, jobs_completed, _, exists = \
            registry.functions.getProfile(addr).call()

        # Get list of skill names stored for this freelancer
        skill_names = registry.functions.getSkillNames(addr).call()

        skills = {}
        for skill in skill_names:
            level, jobs = registry.functions.getSkill(addr, skill).call()
            # Contract stores level as integer (720), convert back to float (7.2)
            skills[skill] = {
                "level": round(level / 100, 1),
                "jobs": jobs
            }

        return {
            "reputation": reputation if exists else 300,  # default 300 for new users
            "jobs_completed": jobs_completed,
            "skills": skills
        }

    except Exception as e:
        print(f"[contracts] Error reading profile for {wallet_address}: {e}")
        # Return default profile if anything goes wrong
        return {
            "reputation": 300,
            "jobs_completed": 0,
            "skills": {}
        }
