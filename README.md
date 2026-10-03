# VERITy — Verifiable Intelligence for Trusted Yardsticks

A standalone GenLayer Intelligent Contract primitive that evaluates work
deliverables across 4 dimensions using a **single AI consensus round**.

```
Contract address (Studio Net): 0x237C3d0935389756C932DfBBDEEffbfA53b446d1
Deploy tx: 0x448c6c1c52af1954ec132459c29494723f95004007ae89d50fca3ab65ed4c0b7
Explorer: https://explorer-studio.genlayer.com/address/0x237C3d0935389756C932DfBBDEEffbfA53b446d1
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

**`accept_job(job_id)`** — `@gl.public.write`
The **named agent** accepts the job, binding itself to the artifact,
requirements, deadline and slashing exposure together. Only the named agent may
call. Until this is called the job cannot touch reputation or stake in any
direction. Returns a digest of every accepted term; the issuer cannot alter the
job afterwards without voiding the acceptance.

**`decline_job(job_id)`** — `@gl.public.write`
The named agent (or the issuer) refuses. No reputation or stake effect.

**`verify(job_id)`** — `@gl.public.write`
Run consensus verification and update reputation + stake. **Refuses any job the
agent has not accepted**, and refuses if the terms no longer match what was
accepted. Before the deadline only the agent or issuer may call; after it,
anyone may.

**`settle_unclaimed(job_id)`** — `@gl.public.write`
Settle an expired, unverified job. An **accepted** job that was then abandoned
is slashed — acceptance is a real obligation. A job that was **never accepted**
simply expires as `EXPIRED_UNACCEPTED`: no reputation change, no stake change,
and the artifact key stays free.

**`deactivate()`** — `@gl.public.write`
Voluntary one-way exit. After it the full remaining stake can be withdrawn.
Irreversible — there is no `activate()`.

**`request_withdraw(amount)`** — `@gl.public.write`
Phase 1 of withdrawal: reserves `amount` out of stake and returns a nonce. An
**active** agent cannot reserve below the minimum stake; once deactivated the
floor no longer applies, so nothing is stranded.

**`claim_withdraw(nonce)`** — `@gl.public.write`
Phase 2: settles the reservation exactly once. A stale nonce or a second call
with nothing pending is refused.

**`dispose_slashed()`** — `@gl.public.write`
Moves the caller's slashed pool into the network sink. Burned value is never
recycled back into stake.

### View methods

**`get_pending_withdraw(agent)`** → JSON
Custody ledger: pending reservation, nonce, slashed pool, settled total, and
the network-wide slashed sink.

**`get_artifact_key(repo_url, commit_hash)`** → JSON
Whether an artifact has actually been consumed by a verification. Keys are
reserved at verification time, not at post time.

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

## Explorer

**Contract:** https://explorer-studio.genlayer.com/address/0x237C3d0935389756C932DfBBDEEffbfA53b446d1
**Deploy TX:** https://explorer-studio.genlayer.com/tx/0x448c6c1c52af1954ec132459c29494723f95004007ae89d50fca3ab65ed4c0b7

Deployed source is byte-identical to `contracts/verity.py`
(1071 lines, SHA-256 `0e2331c96795a8deea24bbcf…`).

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
