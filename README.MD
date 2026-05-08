# DecentraWork — Backend

AI evaluation engine for the DecentraWork freelance platform.
Evaluates freelancer submissions using NVIDIA NIM (free tier),
reads profiles from the blockchain, and stores pending reports in SQLite
until the client approves or the DAO resolves the dispute.

## Stack

| Tool | Purpose |
|------|---------|
| FastAPI | Web server |
| NVIDIA NIM | Free AI inference (llama-3.3-70b) |
| SQLite | Temporary storage for pending evaluations |
| web3.py | Reads freelancer profiles from smart contract |

## Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Copy env file and fill in your keys
cp .env.example .env

# 3. Run locally
uvicorn main:app --reload --port 8000

# 4. Test health check
curl http://localhost:8000/health
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/evaluate` | Generates detailed evaluation report |
| GET | `/report/{escrow_id}` | Get full evaluation report |
| POST | `/finalize` | Calculate final skill/reputation changes |
| GET | `/health` | Health check |

---

### POST `/evaluate`

Generates a detailed evaluation report for the client and DAO voters.

**Accepts multipart form data:**

| Field | Type | Example |
|-------|------|---------|
| `escrow_id` | string | `"escrow_42"` |
| `freelancer_address` | string | `"0xABC..."` |
| `customer_task` | string | `"Build an escrow contract..."` |
| `required_skills` | JSON string | `'["solidity","react"]'` |
| `files` | file(s) | `.sol`, `.jsx`, `.md`, etc. |

**Response:**
```json
{
  "escrow_id": "escrow_42",
  "freelancer": "0xABC...",
  "detailed_report": "## Evaluation Report\n\n**Status:** APPROVED\n\n### What Was Delivered\nThe freelancer submitted a complete escrow smart contract...\n\n### Code Quality Analysis\n- **Good:** Proper use of modifiers in Escrow.sol:15-20\n- **Issue:** Missing ReentrancyGuard in withdraw() function (Escrow.sol:45)\n...",
  "recommendation": "approve",
  "current_reputation": 300,
  "trigger_dao": false
}
```

The `detailed_report` is a markdown-formatted text that explains:
- What was delivered
- Code quality with specific file/line references
- Issues found (security, bugs, missing features)
- Positive aspects
- Final recommendation

This report is shown to the client and DAO voters for decision-making.

---

### GET `/report/{escrow_id}`

Retrieves the full evaluation report for an escrow.

**Response:**
```json
{
  "escrow_id": "escrow_42",
  "freelancer": "0xABC...",
  "detailed_report": "## Evaluation Report\n\n...",
  "recommendation": "approve"
}
```

---

### POST `/finalize`

Called after client approves/rejects or DAO votes. Calculates final skill and reputation changes.

**Body:**
```json
{
  "escrow_id": "escrow_42",
  "work_approved": true
}
```

`work_approved`: `true` if work was approved (by client or DAO), `false` if rejected

**Response:**
```json
{
  "freelancer": "0xABC...",
  "skill_changes": {
    "solidity": 7.2,
    "react": 6.5
  },
  "reputation_delta": 15
}
```

If `work_approved` is `true`:
- Applies AI's suggested skill levels and reputation delta

If `work_approved` is `false`:
- Applies -15 reputation penalty
- No skill improvements

After `/finalize` is called, all uploaded files and the report are permanently deleted from the backend.

---

## File Lifecycle

```
POST /evaluate
  → files saved to tmp/submissions/{escrow_id}/
  → AI generates detailed evaluation report
  → report saved to SQLite with status = "pending"
  → client/DAO reviews the detailed report

GET /report/{escrow_id}
  → retrieve full evaluation report for display

POST /finalize (work_approved: true/false)
  → calculate final skill changes and reputation delta
  → return changes for blockchain update
  → DELETE tmp/submissions/{escrow_id}/
  → DELETE record from SQLite
```

---

## Deploy to Railway (free)

1. Push this folder to a GitHub repository
2. Go to railway.app → New Project → Deploy from GitHub
3. Add environment variables from `.env.example` in the Railway dashboard
4. Railway auto-detects FastAPI and deploys automatically

Your backend URL will be something like `https://your-app.railway.app`.
