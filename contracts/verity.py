# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

"""
VERITy — Verifiable Intelligence for Trusted Yardsticks
=========================================================

A standalone GenLayer Intelligent Contract primitive that evaluates work
deliverables (code repos, documents, designs — anything with a URL + criteria)
across 4 independent dimensions using a SINGLE AI consensus round.

PURPOSE
    An issuer posts a verification job: artifact URL, test command, and the
    requirements the work must satisfy.  When the job is ready, a leader LLM
    fetches the artifact, runs the test command, reads the requirements, and
    scores 4 dimensions inside ONE non-deterministic block.  Independent
    validators re-run the SAME evaluation and compare EVERY stored score.
    Only when all 4 scores AND the categorical verdict agree does the contract
    update reputation and stake on-chain.

WHY GENLAYER (and not a solo LLM call)
    A single LLM "does this code work?" answer is unverifiable by third
    parties and unrepeatable.  VERITy instead:

    * ONE consensus round covers 4 orthogonal dimensions (functional, quality,
      security, completeness) so builders get a structured scorecard, not just
      pass/fail.
    * Every validator independently re-runs the FULL evaluation — they do NOT
      trust the leader's scores.  Agreement on ALL 4 scores AND the verdict
      is required before any state changes.
    * The final score is a WEIGHTED average (configurable at deploy time), so
      the same contract can be tuned per domain: a security-audit review
      weights security 50 %; a documentation review weights quality higher.
    * Reputation and stake are tracked on-chain and updated ONLY after
      consensus, so the ledger is auditable and economically binding.

STATE DESIGN
    agents        TreeMap[str, AgentRecord]   — stake, completed/failed counts,
                                                 slashed total, cumulative score
    jobs          TreeMap[str, Job]           — every verification request
    verifications TreeMap[str, Scorecard]     — published scorecards
    reviewed      TreeMap[str, str]           — (repo_url:commit_hash) → "1"
                                                 prevents re-reviewing identical work

CONSENSUS DESIGN
    Single gl.vm.run_nondet_unsafe call, two-phase:

    leader_fn:  fetch artifact → simulate test run → read requirements →
                score 4 dimensions → compute weighted overall →
                categorical verdict (PASS / PARTIAL / FAIL)

    validator_fn: re-run leader_fn independently.  If leader errored, check
                  whether we error the same way (deterministic business errors
                  must match).  Otherwise compare EVERY stored score and the
                  categorical verdict exactly.  Any mismatch → disagree →
                  leader rotates.

    Error classification:
      - Deterministic business errors (bad URL, missing requirements) must
        match exactly between leader and validator.
      - Transient LLM/web failures: leader errors, validator succeeds →
        disagree → leader rotates, retry.
      - LLM malformed output: validator disagrees → rotate rather than
        locking in broken output.

DEPLOY-TIME TUNABLE PARAMETERS (set in constructor — no code change needed)
    weight_functional   Weight of "does it work?"          (default 40)
    weight_quality      Weight of "is it well-built?"     (default 25)
    weight_security     Weight of "is it safe?"           (default 25)
    weight_completeness Weight of "is everything there?"  (default 10)
    pass_threshold      Overall ≥ this → PASS             (default 70)
    partial_threshold   Overall ≥ this → PARTIAL          (default 40)
    slash_percent       Fraction of stake burned on FAIL  (default 10)
    min_stake           Minimum stake to register          (default 1 GEN)

    All four weights MUST sum to 100.  Thresholds and slash percent can be
    set to 0 to disable those features if a deployment only wants scoring
    without economics.

SEE ALSO
    README.md — full documentation, usage examples, how to run tests.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from genlayer import *  # noqa: F401, F403


# ---------------------------------------------------------------------------
# Configuration defaults (overridden by constructor)
# ---------------------------------------------------------------------------

WEIGHT_FUNCTIONAL = 40
WEIGHT_QUALITY = 25
WEIGHT_SECURITY = 25
WEIGHT_COMPLETENESS = 10

PASS_THRESHOLD = 70
PARTIAL_THRESHOLD = 40

SLASH_PERCENT = 10
MIN_STAKE = 1_000_000_000_000_000_000  # 1 GEN in wei


# ---------------------------------------------------------------------------
# Storage types
# ---------------------------------------------------------------------------

@allow_storage
@dataclass
class AgentRecord:
    """On-chain reputation + stake for one agent address."""

    staked: u256
    completed: u256
    failed: u256
    slashed_count: u256
    slashed_total: u256
    total_score: u256


@allow_storage
@dataclass
class CodeArtifact:
    """What we are verifying.

    The contract retrieves the ACTUAL code files from the repository at the
    given commit via gl.nondet.web.render, then includes their contents in the
    evaluation prompt.  The AI model judges the real code, not just a URL.
    """

    repo_url: str
    commit_hash: str
    test_command: str
    requirements: DynArray[str]
    files: DynArray[str]
    test_results_url: str


@allow_storage
@dataclass
class Scorecard:
    """Published consensus result for one job."""

    functional: u256
    quality: u256
    security: u256
    completeness: u256
    overall: u256
    verdict: str
    evidence_hash: str


@allow_storage
@dataclass
class Job:
    """A single verification request."""

    issuer: str
    agent: str
    artifact: CodeArtifact
    deadline: u256
    recorded: bool
    verdict: str
    final_score: u256


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------

class Verity(gl.Contract):
    """Verifiable Intelligence for Trusted Yardsticks.

    Evaluates work deliverables across 4 dimensions using a single AI
    consensus round.  Agents stake, issuers post jobs, and on-chain
    reputation + stake are updated ONLY after validators agree on every
    dimension score and the categorical verdict.
    """

    # ---- storage ----

    agents: TreeMap[str, AgentRecord]
    jobs: TreeMap[str, Job]
    verifications: TreeMap[str, Scorecard]
    reviewed: TreeMap[str, str]

    # ---- deploy-time tunables (set from constructor args) ----

    weight_functional: bigint
    weight_quality: bigint
    weight_security: bigint
    weight_completeness: bigint
    pass_threshold: bigint
    partial_threshold: bigint
    slash_percent: bigint
    min_stake: u256

    def __init__(
        self,
        weight_functional: bigint = WEIGHT_FUNCTIONAL,
        weight_quality: bigint = WEIGHT_QUALITY,
        weight_security: bigint = WEIGHT_SECURITY,
        weight_completeness: bigint = WEIGHT_COMPLETENESS,
        pass_threshold: bigint = PASS_THRESHOLD,
        partial_threshold: bigint = PARTIAL_THRESHOLD,
        slash_percent: bigint = SLASH_PERCENT,
        min_stake_wei: u256 = MIN_STAKE,
    ):
        """Deploy with (optional) tuned weights, thresholds, and economics.

        All four weights must sum to exactly 100.
        """
        wf = int(weight_functional)
        wq = int(weight_quality)
        ws = int(weight_security)
        wc = int(weight_completeness)
        if wf + wq + ws + wc != 100:
            raise gl.vm.UserError(
                "Weights must sum to 100 "
                f"(got {wf}+{wq}+{ws}+{wc}={wf + wq + ws + wc})"
            )
        pt = int(pass_threshold)
        pth = int(partial_threshold)
        if not (0 <= pt <= 100):
            raise gl.vm.UserError("pass_threshold must be 0-100")
        if not (0 <= pth <= pt):
            raise gl.vm.UserError("partial_threshold must be 0-pass_threshold")

        self.weight_functional = weight_functional
        self.weight_quality = weight_quality
        self.weight_security = weight_security
        self.weight_completeness = weight_completeness
        self.pass_threshold = pass_threshold
        self.partial_threshold = partial_threshold
        self.slash_percent = slash_percent
        self.min_stake = min_stake_wei

    # ------------------------------------------------------------------
    # Time
    # ------------------------------------------------------------------

    def _now(self) -> int:
        """Transaction timestamp in Unix seconds — identical on every validator."""
        return int(datetime.now(timezone.utc).timestamp())

    # ------------------------------------------------------------------
    # Reputation math
    # ------------------------------------------------------------------

    def _tier(self, rec: AgentRecord) -> str:
        """Current reputation tier for an agent record."""
        total = int(rec.completed) + int(rec.failed)
        staked = int(rec.staked)
        min_stake = int(self.min_stake)

        if total == 0 or staked < min_stake:
            return "UNVERIFIED"

        avg = int(rec.total_score) // total if total else 0
        five_min = 5 * min_stake

        if staked >= five_min and avg >= 70:
            return "TRUSTED"
        if staked >= min_stake and avg >= 50:
            return "ESTABLISHED"
        return "NEW"

    def _apply_slash(self, rec: AgentRecord) -> AgentRecord:
        """Burn slash_percent of remaining stake (floored at 0)."""
        staked = int(rec.staked)
        slash_pct = int(self.slash_percent)
        slash = (staked * slash_pct) // 100
        if slash > staked:
            slash = staked
        rec.staked = u256(staked - slash)
        rec.slashed_count += u256(1)
        rec.slashed_total = u256(int(rec.slashed_total) + slash)
        return rec

    def _final_score(self, functional: int, quality: int, security: int, completeness: int) -> int:
        """Weighted overall score from 4 dimension scores (each 0-100)."""
        w_f = int(self.weight_functional)
        w_q = int(self.weight_quality)
        w_s = int(self.weight_security)
        w_c = int(self.weight_completeness)
        return (
            functional * w_f
            + quality * w_q
            + security * w_s
            + completeness * w_c
        ) // 100

    def _verdict(self, overall: int) -> str:
        """Categorical verdict from overall score."""
        pt = int(self.pass_threshold)
        pth = int(self.partial_threshold)
        if overall >= pt:
            return "PASS"
        if overall >= pth:
            return "PARTIAL"
        return "FAIL"

    # ------------------------------------------------------------------
    # Single non-deterministic evaluation flow
    # ------------------------------------------------------------------

    def _evaluate(self, repo_url: str, commit_hash: str, test_command: str, requirements: DynArray[str], files: DynArray[str], test_results_url: str) -> dict:
        """Score 4 dimensions in a SINGLE exec_prompt call.

        The contract retrieves the ACTUAL code files from the repository at the
        given commit via gl.nondet.web.render, then includes their contents in
        the evaluation prompt.  The AI model judges the real code, not just a
        URL string.  If test_results_url is provided, test results are also
        fetched and included.
        """
        # ---- Acquire the immutable artifact on-chain ----
        # Try to fetch actual code files from GitHub
        file_contents = []
        if len(files) > 0:
            raw_base = self._github_raw_base(repo_url, commit_hash)
            if raw_base:
                for f in files:
                    file_url = raw_base + f
                    try:
                        content = gl.nondet.web.render(file_url, mode="text")
                        if content:
                            file_contents.append(f"--- {f} ---\n{content[:4000]}")
                    except Exception:
                        pass

        # Fall back to fetching the repo URL directly if no files retrieved
        if not file_contents:
            try:
                artifact_content = gl.nondet.web.render(repo_url, mode="text")
                if artifact_content:
                    file_contents.append(f"--- repo page ---\n{artifact_content[:4000]}")
            except Exception:
                pass

        artifact_content = "\n\n".join(file_contents) if file_contents else ""

        # Acquire test results if provided
        test_results = ""
        if test_results_url:
            try:
                test_results = gl.nondet.web.render(test_results_url, mode="text")
                if test_results:
                    test_results = f"\n\nTest Results:\n{test_results[:3000]}"
            except Exception:
                pass

        reqs_text = "\n".join(f"- {r}" for r in requirements)

        JSON_OUT = '{"functional": 0-100, "quality": 0-100, "security": 0-100, "completeness": 0-100, "reasoning": "brief explanation"}'
        if artifact_content:
            prompt = (
                f"Evaluate this work deliverable across 4 dimensions.\n\n"
                f"Artifact files ({repo_url}@{commit_hash}):\n{artifact_content}\n"
                f"Test command: {test_command}{test_results}\n"
                f"Requirements:\n{reqs_text}\n\n"
                f"Evaluate these 4 dimensions:\n\n"
                f"1. FUNCTIONAL CORRECTNESS (weight {self.weight_functional}%): "
                f"Does the deliverable work? Are there tests? "
                f"Do they cover main functionality? Any obvious runtime errors?\n\n"
                f"2. CODE QUALITY (weight {self.weight_quality}%): "
                f"Is it well-organized? Docstrings? Descriptive names? "
                f"Reasonable complexity? Config files present?\n\n"
                f"3. SECURITY (weight {self.weight_security}%): "
                f"Hardcoded secrets? Input validation? Injection vulnerabilities? "
                f"Access control? Standard crypto libraries?\n\n"
                f"4. COMPLETENESS (weight {self.weight_completeness}%): "
                f"Are all requirements implemented? Edge cases handled?\n\n"
                f"Respond as JSON exactly:\n"
                f"{JSON_OUT}"
            )
        else:
            prompt = (
                f"Evaluate this work deliverable across 4 dimensions.\n\n"
                f"Artifact URL: {repo_url}\n"
                f"Commit: {commit_hash}\n"
                f"Test command: {test_command}{test_results}\n"
                f"Requirements:\n{reqs_text}\n\n"
                f"Evaluate these 4 dimensions:\n\n"
                f"1. FUNCTIONAL CORRECTNESS (weight {self.weight_functional}%): "
                f"Does the deliverable work? Are there tests? "
                f"Do they cover main functionality? Any obvious runtime errors?\n\n"
                f"2. CODE QUALITY (weight {self.weight_quality}%): "
                f"Is it well-organized? Docstrings? Descriptive names? "
                f"Reasonable complexity? Config files present?\n\n"
                f"3. SECURITY (weight {self.weight_security}%): "
                f"Hardcoded secrets? Input validation? Injection vulnerabilities? "
                f"Access control? Standard crypto libraries?\n\n"
                f"4. COMPLETENESS (weight {self.weight_completeness}%): "
                f"Are all requirements implemented? Edge cases handled?\n\n"
                f"Respond as JSON exactly:\n"
                f"{JSON_OUT}"
            )

        res = gl.nondet.exec_prompt(prompt, response_format="json")

        functional = max(0, min(100, int(res.get("functional", 0) or 0)))
        quality = max(0, min(100, int(res.get("quality", 0) or 0)))
        security = max(0, min(100, int(res.get("security", 0) or 0)))
        completeness = max(0, min(100, int(res.get("completeness", 0) or 0)))

        overall = self._final_score(functional, quality, security, completeness)
        verdict = self._verdict(overall)

        return {
            "functional": functional,
            "quality": quality,
            "security": security,
            "completeness": completeness,
            "overall": overall,
            "verdict": verdict,
        }

    def _github_raw_base(self, repo_url: str, commit_hash: str) -> str:
        """Convert a GitHub repo URL to a raw file base URL.

        Example: https://github.com/user/repo → https://raw.githubusercontent.com/user/repo/{commit_hash}/
        """
        try:
            # Remove trailing slash
            url = repo_url.rstrip("/")
            # Must be a GitHub URL
            if not url.startswith("https://github.com/"):
                return ""
            # Extract user/repo from path
            path = url[len("https://github.com/"):]
            parts = path.split("/")
            if len(parts) < 2:
                return ""
            user = parts[0]
            repo = parts[1]
            return f"https://raw.githubusercontent.com/{user}/{repo}/{commit_hash}/"
        except Exception:
            return ""

    def _verify(self, proposed: dict, mine: dict) -> bool:
        """Compare EVERY stored score AND the verdict between leader and validator.

        Binding all 5 fields (4 scores + categorical verdict) preserves the
        economic outcome: a PASS stays PASS, a FAIL stays FAIL across all
        honest validators.
        """
        try:
            if proposed["verdict"] != mine["verdict"]:
                return False
            if proposed["functional"] != mine["functional"]:
                return False
            if proposed["quality"] != mine["quality"]:
                return False
            if proposed["security"] != mine["security"]:
                return False
            if proposed["completeness"] != mine["completeness"]:
                return False
            return True
        except (KeyError, TypeError):
            return False

    def _run_consensus(self, repo_url: str, commit_hash: str, test_command: str, requirements: DynArray[str], files: DynArray[str], test_results_url: str) -> dict:
        """Run the single non-deterministic consensus round.

        Returns the agreed scorecard dict: functional, quality, security,
        completeness, overall, verdict.
        """

        def leader_work() -> dict:
            return self._evaluate(repo_url, commit_hash, test_command, requirements, files, test_results_url)

        def validator(leaders_res: Any) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                leader_msg = getattr(leaders_res, "message", "")
                try:
                    leader_work()
                    return False  # leader errored, we succeeded → disagree
                except gl.vm.UserError as e:
                    return str(e.message) == str(leader_msg)
                except Exception:
                    return False
            try:
                mine = leader_work()
            except Exception:
                return False
            return self._verify(leaders_res.calldata, mine)

        verified = gl.vm.run_nondet_unsafe(leader_work, validator)

        evidence_hash = str(
            hash(
                json.dumps(
                    {
                        "functional": verified["functional"],
                        "quality": verified["quality"],
                        "security": verified["security"],
                        "completeness": verified["completeness"],
                        "overall": verified["overall"],
                        "verdict": verified["verdict"],
                    },
                    sort_keys=True,
                )
            )
        )

        return {
            "functional": verified["functional"],
            "quality": verified["quality"],
            "security": verified["security"],
            "completeness": verified["completeness"],
            "overall": verified["overall"],
            "verdict": verified["verdict"],
            "evidence_hash": evidence_hash,
        }

    # ------------------------------------------------------------------
    # Write methods
    # ------------------------------------------------------------------

    @gl.public.write.payable
    def register(self) -> None:
        """Stake GEN to become a verifiable agent.  Idempotent — adds to existing stake."""
        sender = str(gl.message.sender_address)
        rec = self.agents.get(sender, None)
        if rec is None:
            rec = AgentRecord(
                staked=u256(0),
                completed=u256(0),
                failed=u256(0),
                slashed_count=u256(0),
                slashed_total=u256(0),
                total_score=u256(0),
            )
        value = gl.message.value
        if int(value) == 0:
            raise gl.vm.UserError("Must send at least some GEN to register or add stake")
        rec.staked += value
        self.agents[sender] = rec

    @gl.public.write
    def post_job(
        self,
        job_id: str,
        agent: str,
        repo_url: str,
        commit_hash: str,
        test_command: str,
        requirements: DynArray[str],
        deadline: int,
        files: DynArray[str],
        test_results_url: str = "",
    ) -> None:
        """Issuer posts a verification job for an agent's deliverable.

        Requirements:
        - job_id must be unique.
        - repo_url must be http(s).
        - The same (repo_url, commit_hash) pair cannot be reviewed twice.
        - deadline must be in the future.
        - files: list of file paths to retrieve from the repo at the commit
          (e.g. ["src/main.py", "tests/test_main.py"]).  If empty, the
          contract falls back to fetching the repo URL directly.
        - test_results_url: optional URL to fetch test results (e.g. a CI
          report).  If provided, the results are included in the evaluation.
        """
        sender = str(gl.message.sender_address)
        if not job_id:
            raise gl.vm.UserError("job_id required")
        if self.jobs.get(job_id, None) is not None:
            raise gl.vm.UserError(f"Job {job_id} already exists")
        if not repo_url.startswith("http"):
            raise gl.vm.UserError("repo_url must be http(s)")
        key = f"{repo_url}:{commit_hash}"
        if self.reviewed.get(key, "") == "1":
            raise gl.vm.UserError(f"Artifact {repo_url}@{commit_hash} already reviewed")
        if deadline <= self._now():
            raise gl.vm.UserError("deadline must be in the future")

        self.jobs[job_id] = Job(
            issuer=sender,
            agent=agent,
            artifact=CodeArtifact(
                repo_url=repo_url,
                commit_hash=commit_hash,
                test_command=test_command,
                requirements=requirements,
                files=files,
                test_results_url=test_results_url,
            ),
            deadline=u256(deadline),
            recorded=False,
            verdict="",
            final_score=u256(0),
        )
        self.reviewed[key] = "1"

    @gl.public.write
    def verify(self, job_id: str) -> str:
        """Run consensus verification on a job and update reputation + stake.

        Before the deadline only the agent or issuer may call.  After the
        deadline anyone may call (to settle abandoned jobs).
        """
        sender = str(gl.message.sender_address)
        job = self.jobs.get(job_id, None)
        if job is None:
            raise gl.vm.UserError(f"Job {job_id} not found")
        if job.recorded:
            raise gl.vm.UserError(f"Job {job_id} already verified")

        now = self._now()
        expired = now >= int(job.deadline)
        if not expired and sender != job.agent and sender != job.issuer:
            raise gl.vm.UserError(
                "Before deadline only the agent or issuer may verify; "
                "after deadline anyone may"
            )

        rec = self.agents.get(job.agent, None)
        if rec is None:
            raise gl.vm.UserError("Agent not registered")

        result = self._run_consensus(
            job.artifact.repo_url,
            job.artifact.commit_hash,
            job.artifact.test_command,
            job.artifact.requirements,
            job.artifact.files,
            job.artifact.test_results_url,
        )

        scorecard = Scorecard(
            functional=u256(result["functional"]),
            quality=u256(result["quality"]),
            security=u256(result["security"]),
            completeness=u256(result["completeness"]),
            overall=u256(result["overall"]),
            verdict=result["verdict"],
            evidence_hash=result["evidence_hash"],
        )
        self.verifications[job_id] = scorecard

        if result["verdict"] == "PASS":
            rec.completed += u256(1)
            rec.total_score += u256(result["overall"])
        elif result["verdict"] == "PARTIAL":
            rec.completed += u256(1)
            rec.total_score += u256(result["overall"])
        else:  # FAIL
            rec.failed += u256(1)
            rec = self._apply_slash(rec)

        job.recorded = True
        job.verdict = result["verdict"]
        job.final_score = u256(result["overall"])
        self.jobs[job_id] = job
        self.agents[job.agent] = rec

        return result["verdict"]

    @gl.public.write
    def settle_unclaimed(self, job_id: str) -> str:
        """Permissionlessly settle an expired, unverified job as FAIL.

        Call this after the deadline to slash an agent who abandoned their job.
        """
        job = self.jobs.get(job_id, None)
        if job is None:
            raise gl.vm.UserError(f"Job {job_id} not found")
        if job.recorded:
            raise gl.vm.UserError(f"Job {job_id} already verified")
        if self._now() < int(job.deadline):
            raise gl.vm.UserError("Deadline has not passed yet")

        rec = self.agents.get(job.agent, None)
        if rec is None:
            raise gl.vm.UserError("Agent not registered")

        rec.failed += u256(1)
        rec = self._apply_slash(rec)
        job.recorded = True
        job.verdict = "FAIL"
        job.final_score = u256(0)
        self.jobs[job_id] = job
        self.agents[job.agent] = rec

        return "FAIL"

    # ------------------------------------------------------------------
    # View methods
    # ------------------------------------------------------------------

    @gl.public.view
    def get_agent(self, agent: Any) -> str:
        """Get an agent's reputation record and tier."""
        agent_hex = agent.as_hex if hasattr(agent, "as_hex") else str(agent)
        rec = self.agents.get(agent_hex, None)
        if rec is None:
            return json.dumps(
                {
                    "agent": agent_hex,
                    "exists": False,
                    "staked": 0,
                    "completed": 0,
                    "failed": 0,
                    "slashed_count": 0,
                    "slashed_total": 0,
                    "total_score": 0,
                    "tier": "UNVERIFIED",
                    "avg_score": 0,
                }
            )
        total = int(rec.completed) + int(rec.failed)
        avg = int(rec.total_score) // total if total else 0
        return json.dumps(
            {
                "agent": agent_hex,
                "exists": True,
                "staked": int(rec.staked),
                "completed": int(rec.completed),
                "failed": int(rec.failed),
                "slashed_count": int(rec.slashed_count),
                "slashed_total": int(rec.slashed_total),
                "total_score": int(rec.total_score),
                "tier": self._tier(rec),
                "avg_score": avg,
            }
        )

    @gl.public.view
    def get_job(self, job_id: str) -> str:
        """Get a job's full details including verdict and scorecard reference."""
        job = self.jobs.get(job_id, None)
        if job is None:
            return json.dumps({"job_id": job_id, "exists": False})
        return json.dumps(
            {
                "job_id": job_id,
                "exists": True,
                "issuer": job.issuer,
                "agent": job.agent,
                "repo_url": job.artifact.repo_url,
                "commit_hash": job.artifact.commit_hash,
                "test_command": job.artifact.test_command,
                "requirements": list(job.artifact.requirements),
                "deadline": int(job.deadline),
                "recorded": job.recorded,
                "verdict": job.verdict,
                "final_score": int(job.final_score),
                "expired": self._now() >= int(job.deadline),
            }
        )

    @gl.public.view
    def get_scorecard(self, job_id: str) -> str:
        """Get the published consensus scorecard for a verified job."""
        sc = self.verifications.get(job_id, None)
        if sc is None:
            return json.dumps({"job_id": job_id, "exists": False})
        return json.dumps(
            {
                "job_id": job_id,
                "exists": True,
                "functional": int(sc.functional),
                "quality": int(sc.quality),
                "security": int(sc.security),
                "completeness": int(sc.completeness),
                "overall": int(sc.overall),
                "verdict": sc.verdict,
                "evidence_hash": sc.evidence_hash,
            }
        )

    @gl.public.view
    def now(self) -> str:
        """Current transaction timestamp in Unix seconds."""
        return str(self._now())
