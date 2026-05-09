import os
from web3 import Web3
from dotenv import load_dotenv

load_dotenv()

RPC_URL = os.getenv("RPC_URL", "http://127.0.0.1:8545")
w3 = Web3(Web3.HTTPProvider(RPC_URL))

# ── Minimal ABIs ─────────────────────────────────────────────────────────────

REPUTATION_ABI = [
    {"name": "getReputation", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "user", "type": "address"}],
     "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "getTasksCompleted", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "user", "type": "address"}],
     "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "getSkill", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "user", "type": "address"}, {"name": "skill", "type": "string"}],
     "outputs": [{"name": "", "type": "uint8"}]},
    {"name": "setSkill", "type": "function", "stateMutability": "nonpayable",
     "inputs": [
         {"name": "user", "type": "address"}, {"name": "skill", "type": "string"},
         {"name": "score", "type": "uint8"}, {"name": "jobId", "type": "uint256"}
     ], "outputs": []},
    {"name": "updateReputation", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "user", "type": "address"}, {"name": "delta", "type": "int256"}],
     "outputs": []},
    {"name": "incrementTasksCompleted", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "user", "type": "address"}], "outputs": []},
]

MARKETPLACE_ABI = [
    {"name": "approveWork", "type": "function", "stateMutability": "nonpayable",
     "inputs": [
         {"name": "jobId", "type": "uint256"},
         {"name": "freelancerStars", "type": "uint8"},
         {"name": "clientStars", "type": "uint8"},
     ], "outputs": []},
    {"name": "rejectWork", "type": "function", "stateMutability": "nonpayable",
     "inputs": [
         {"name": "jobId", "type": "uint256"},
         {"name": "reason", "type": "string"},
     ], "outputs": []},
]

# ── Contract instances ────────────────────────────────────────────────────────

reputation_address  = os.getenv("REPUTATION_ADDRESS", "")
marketplace_address = os.getenv("JOBMARKETPLACE_ADDRESS", "")
agent_private_key   = os.getenv("AGENT_PRIVATE_KEY", "")

reputation  = None
marketplace = None
agent       = None

if reputation_address:
    reputation = w3.eth.contract(
        address=Web3.to_checksum_address(reputation_address), abi=REPUTATION_ABI
    )
if marketplace_address:
    marketplace = w3.eth.contract(
        address=Web3.to_checksum_address(marketplace_address), abi=MARKETPLACE_ABI
    )
if agent_private_key:
    agent = w3.eth.account.from_key(agent_private_key)


# ── Read helpers ──────────────────────────────────────────────────────────────

def get_elo_from_chain(wallet_address: str) -> dict:
    """Reads reputation (Elo) and tasksCompleted from the contract."""
    if not reputation:
        return {"elo": 300, "tasks_completed": 0}
    try:
        addr = Web3.to_checksum_address(wallet_address)
        elo   = reputation.functions.getReputation(addr).call()
        tasks = reputation.functions.getTasksCompleted(addr).call()
        return {"elo": elo, "tasks_completed": tasks}
    except Exception as e:
        print(f"[contracts] Error reading Elo for {wallet_address}: {e}")
        return {"elo": 300, "tasks_completed": 0}


def update_elo_on_chain(wallet_address: str, delta: int) -> bool:
    """Calls ReputationSystem.updateReputation with the Elo delta."""
    if not reputation:
        print("[contracts] REPUTATION_ADDRESS not set — skipping updateReputation")
        return False
    addr = Web3.to_checksum_address(wallet_address)
    return _send(reputation.functions.updateReputation(addr, delta))


def increment_tasks_on_chain(wallet_address: str) -> bool:
    """Calls ReputationSystem.incrementTasksCompleted."""
    if not reputation:
        return False
    addr = Web3.to_checksum_address(wallet_address)
    return _send(reputation.functions.incrementTasksCompleted(addr))


def get_profile_from_chain(wallet_address: str, required_skills: list[str] | None = None) -> dict:
    """
    Reads reputation score and per-skill levels from ReputationSystem.
    Falls back to defaults when contract is not configured.
    """
    if not reputation:
        return {"reputation": 300, "jobs_completed": 0, "skills": {}}

    try:
        addr = Web3.to_checksum_address(wallet_address)
        rep  = reputation.functions.getReputation(addr).call()

        skills = {}
        for skill in (required_skills or []):
            try:
                score = reputation.functions.getSkill(addr, skill).call()
                skills[skill] = {"level": float(score), "jobs": 0}
            except Exception:
                skills[skill] = {"level": 0.0, "jobs": 0}

        return {"reputation": rep, "jobs_completed": 0, "skills": skills}

    except Exception as e:
        print(f"[contracts] Error reading profile for {wallet_address}: {e}")
        return {"reputation": 300, "jobs_completed": 0, "skills": {}}


# ── Write helpers ─────────────────────────────────────────────────────────────

def _send(fn) -> bool:
    """Sign and broadcast a transaction; return True on success."""
    if not agent:
        print("[contracts] AGENT_PRIVATE_KEY not set — skipping on-chain write")
        return False
    try:
        tx = fn.build_transaction({
            "from":     agent.address,
            "nonce":    w3.eth.get_transaction_count(agent.address),
            "gas":      400_000,
            "gasPrice": w3.eth.gas_price,
        })
        signed  = agent.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        ok = receipt["status"] == 1
        print(f"[contracts] {'✓' if ok else '✗'} tx {tx_hash.hex()} block {receipt['blockNumber']}")
        return ok
    except Exception as e:
        print(f"[contracts] Error sending tx: {e}")
        return False


def _delta_to_stars(delta: int) -> int:
    """Map AI reputation delta (-20..+20) to 1-5 star rating."""
    if delta >= 15: return 5
    if delta >=  8: return 4
    if delta >=  0: return 3
    if delta >= -10: return 2
    return 1


def approve_work_on_chain(job_id: int, reputation_delta: int) -> bool:
    """
    Calls JobMarketplace.approveWork with star ratings derived from AI delta.
    Requires the agent wallet to hold AI_ORACLE_ROLE.
    """
    if not marketplace:
        print("[contracts] JOBMARKETPLACE_ADDRESS not set — skipping approveWork")
        return False
    stars = _delta_to_stars(reputation_delta)
    return _send(marketplace.functions.approveWork(job_id, stars, 4))


def reject_work_on_chain(job_id: int, reason: str) -> bool:
    """
    Calls JobMarketplace.rejectWork.
    Job status reverts to Assigned so the freelancer can resubmit.
    """
    if not marketplace:
        print("[contracts] JOBMARKETPLACE_ADDRESS not set — skipping rejectWork")
        return False
    return _send(marketplace.functions.rejectWork(job_id, reason[:200]))


def set_skill_on_chain(freelancer_address: str, skill: str, new_level: float, job_id: int) -> bool:
    """
    Calls ReputationSystem.setSkill with the AI-suggested score (0-10 uint8).
    Requires the agent wallet to hold AI_ORACLE_ROLE.
    """
    if not reputation:
        return False
    score = min(10, max(0, round(new_level)))
    addr  = Web3.to_checksum_address(freelancer_address)
    return _send(reputation.functions.setSkill(addr, skill, score, job_id))
