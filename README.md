# VERITy — Verifiable Intelligence for Trusted Yardsticks

A standalone GenLayer Intelligent Contract primitive that evaluates work
deliverables across 4 dimensions using a **single AI consensus round**.

```
Contract address (Studio Net): 0xE319bD232a8F00D7B058136ad8966d1DCaE1D6f7
Deploy tx: 0x1da1bda4aa05364c831686e2ebd0df6ef05a1313872f0849f4c863ec181bf6e3
```

## What it does

An issuer posts a verification job: an artifact URL, a test command, and the
requirements the work must satisfy.  When the job is ready, a leader LLM
evaluates the deliverable across 4 dimensions inside ONE non-deterministic
block.  Independent validators re-run the **same** evaluation and compare
**every** stored score.  Only when all 4 dimension scores AND the categorical
verdict agree does the contract update reputation and stake on-chain.

## Why GenLayer (and not a solo LLM)

A single LLM "does this code work?" answer is unverifiable by third parties
and unrepeatable.  VERITy instead:

- **ONE consensus round covers 4 orthogonal dimensions** (functional, quality,
  security, completeness) so builders get a structured scorecard, not just
  pass/fail.
- **Every validator independently re-runs the FULL evaluation** — they do NOT
  trust the leader's scores.  Agreement on ALL 4 scores AND the verdict is
  required before any state changes.
- **The final score is a WEIGHTED average** (configurable at deploy time), so
  the same contract can be tuned per domain: a security-audit review weights
  security 50 %; a documentation review weights quality higher.
- **Reputation and stake are tracked on-chain** and updated ONLY after
  consensus, so the ledger is auditable and economically binding.

## State design

| Storage | Type | Purpose |
|---|---|---|
| `agents` | `TreeMap[str, AgentRecord]` | Stake, completed/failed counts, slashed total, cumulative score per agent |
| `jobs` | `TreeMap[str, Job]` | Every verification request |
| `verifications` | `TreeMap[str, Scorecard]` | Published consensus scorecards |
| `reviewed` | `TreeMap[str, str]` | `(repo_url:commit_hash) → "1"` — prevents re-reviewing identical work |

## Consensus design

Single `gl.vm.run_nondet_unsafe` call, two-phase:

1. **leader_fn**: fetch artifact → simulate test run → read requirements →
   score 4 dimensions → compute weighted overall → categorical verdict
   (PASS / PARTIAL / FAIL)
2. **validator_fn**: re-run leader_fn independently.  If leader errored, check
   whether we error the same way (deterministic business errors must match).
   Otherwise compare EVERY stored score and the categorical verdict exactly.
   Any mismatch → disagree → leader rotates.

**Error classification:**
- Deterministic business errors (bad URL, missing requirements) must match
  exactly between leader and validator.
- Transient LLM/web failures: leader errors, validator succeeds → disagree →
  leader rotates, retry.
- LLM malformed output: validator disagrees → rotate rather than locking in
  broken output.

## Deploy-time tunable parameters

All set via constructor arguments — no code change needed:

| Parameter | Default | Description |
|---|---|---|
| `weight_functional` | 40 | Weight of "does it work?" |
| `weight_quality` | 25 | Weight of "is it well-built?" |
| `weight_security` | 25 | Weight of "is it safe?" |
| `weight_completeness` | 10 | Weight of "is everything there?" |
| `pass_threshold` | 70 | Overall ≥ this → PASS |
| `partial_threshold` | 40 | Overall ≥ this → PARTIAL |
| `slash_percent` | 10 | Fraction of stake burned on FAIL |
| `min_stake_wei` | 1e18 (1 GEN) | Minimum stake to register |

All four weights **must** sum to exactly 100.  Thresholds and slash percent
can be set to 0 to disable those features if a deployment only wants scoring
without economics.

## API

### Write methods

**`register()`** — `@gl.public.write.payable`
Stake GEN to become a verifiable agent.  Idempotent — adds to existing stake.

**`post_job(job_id, agent, repo_url, commit_hash, test_command, requirements, deadline)`** —
`@gl.public.write`
Issuer posts a verification job for an agent's deliverable.

**`verify(job_id)`** — `@gl.public.write`
Run consensus verification on a job and update reputation + stake.  Before the
deadline only the agent or issuer may call.  After the deadline anyone may
call (to settle abandoned jobs).

**`settle_unclaimed(job_id)`** — `@gl.public.write`
Permissionlessly settle an expired, unverified job as FAIL.

### View methods

**`get_agent(agent)`** → JSON
Get an agent's reputation record and tier (UNVERIFIED / NEW / ESTABLISHED / TRUSTED).

**`get_job(job_id)`** → JSON
Get a job's full details including verdict and scorecard reference.

**`get_scorecard(job_id)`** → JSON
Get the published consensus scorecard for a verified job.

**`now()`** → string
Current transaction timestamp in Unix seconds.

## Reputation tiers

| Tier | Requirements |
|---|---|
| UNVERIFIED | No stake or no completed jobs |
| NEW | Min stake met, avg score < 50 |
| ESTABLISHED | Min stake met, avg score ≥ 50 |
| TRUSTED | 5× min stake met, avg score ≥ 70 |

## Testing

```bash
# Direct mode tests (in-memory, no Studio needed)
export PYTHONPATH="$HOME/.local/lib/python3.14/site-packages/genlayer_py/client:$PYTHONPATH"
python3.14 -m pytest tests/direct/ -v
```

Direct mode tests use mocked LLM responses to cover PASS, PARTIAL, and FAIL
paths, slashing logic, reputation tier transitions, and job validation rules.

## Linting

```bash
genvm-lint check contracts/verity.py
```

## Deployment

```bash
# Set network
genlayer network set studionet

# Deploy (default weights)
echo "your_password" | genlayer deploy \
  --contract contracts/verity.py \
  --rpc https://studio.genlayer.com/api

# Deploy with custom weights (security-heavy)
echo "your_password" | genlayer deploy \
  --contract contracts/verity.py \
  --rpc https://studio.genlayer.com/api \
  --args 15 15 60 10 70 40 10 1000000000000000000
```

## Contract address

**Studio Net:** `0xE319bD232a8F00D7B058136ad8966d1DCaE1D6f7`

## File structure

```
agent-verity/
├── contracts/
│   └── verity.py          # The intelligent contract (679 lines)
├── tests/
│   └── direct/
│       ├── conftest.py    # Shared test helpers
│       ├── test_register.py   # Agent registration + stake tests
│       ├── test_jobs.py       # Job posting validation tests
│       └── test_verify.py     # Consensus + reputation + scorecard tests
└── README.md
```

## License

MIT
