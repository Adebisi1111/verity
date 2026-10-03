"""Tests for consensus verification, scorecards, and reputation updates."""

import json
from tests.direct.conftest import to_hex, scorecard_dict


def _mock_llm_for(scores: dict, direct_vm):
    """Register an LLM mock that returns the given scorecard dict."""
    direct_vm.mock_llm(
        r".*Evaluate this work deliverable.*",
        json.dumps(scores),
    )


def _register(contract, direct_vm, alice, stake):
    direct_vm.sender = alice
    direct_vm.value = stake
    contract.register()


def test_verify_pass_updates_reputation(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/verity.py")
    alice = to_hex(direct_alice)

    _register(contract, direct_vm, direct_alice, 100)

    _mock_llm_for(
        scorecard_dict(functional=90, quality=85, security=80, completeness=90),
        direct_vm,
    )

    contract.post_job(
        job_id="pass-job",
        agent=alice,
        repo_url="https://example.com/good-repo",
        commit_hash="v1",
        test_command="pytest",
        requirements=["must have tests"],
        deadline=9999999999,
        files=[],
    )
    contract.accept_job("pass-job")
    verdict = contract.verify("pass-job")
    assert verdict == "PASS"

    rec = contract.get_agent(alice)
    data = json.loads(rec)
    assert data["completed"] == 1
    assert data["failed"] == 0
    assert data["avg_score"] >= 80


def test_verify_partial_no_slash(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/verity.py")
    alice = to_hex(direct_alice)

    _register(contract, direct_vm, direct_alice, 100)

    _mock_llm_for(
        scorecard_dict(functional=50, quality=45, security=40, completeness=55),
        direct_vm,
    )

    contract.post_job(
        job_id="partial-job",
        agent=alice,
        repo_url="https://example.com/meh-repo",
        commit_hash="v1",
        test_command="pytest",
        requirements=["must have tests"],
        deadline=9999999999,
        files=[],
    )
    contract.accept_job("partial-job")
    verdict = contract.verify("partial-job")
    assert verdict == "PARTIAL"

    rec = contract.get_agent(alice)
    data = json.loads(rec)
    assert data["completed"] == 1
    assert data["failed"] == 0


def test_verify_fail_applies_slash(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/verity.py")
    alice = to_hex(direct_alice)

    _register(contract, direct_vm, direct_alice, 1000)

    _mock_llm_for(
        scorecard_dict(functional=20, quality=10, security=5, completeness=15),
        direct_vm,
    )

    contract.post_job(
        job_id="fail-job",
        agent=alice,
        repo_url="https://example.com/bad-repo",
        commit_hash="v1",
        test_command="pytest",
        requirements=["must have tests"],
        deadline=9999999999,
        files=[],
    )
    contract.accept_job("fail-job")
    verdict = contract.verify("fail-job")
    assert verdict == "FAIL"

    rec = contract.get_agent(alice)
    data = json.loads(rec)
    assert data["failed"] == 1
    assert data["slashed_count"] == 1
    assert data["slashed_total"] == 100
    assert data["staked"] == 900


def test_verify_stores_scorecard(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/verity.py")
    alice = to_hex(direct_alice)

    _register(contract, direct_vm, direct_alice, 100)

    _mock_llm_for(
        scorecard_dict(functional=80, quality=75, security=70, completeness=85),
        direct_vm,
    )

    contract.post_job(
        job_id="sc-job",
        agent=alice,
        repo_url="https://example.com/repo",
        commit_hash="v1",
        test_command="pytest",
        requirements=["must have tests"],
        deadline=9999999999,
        files=[],
    )
    contract.accept_job("sc-job")
    contract.verify("sc-job")

    sc = contract.get_scorecard("sc-job")
    data = json.loads(sc)
    assert data["exists"] is True
    assert data["verdict"] == "PASS"
    assert data["functional"] == 80
    assert data["quality"] == 75
    assert data["security"] == 70
    assert data["completeness"] == 85
    assert data["overall"] >= 70
    assert data["evidence_hash"] != ""


def test_verify_records_job_as_done(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/verity.py")
    alice = to_hex(direct_alice)

    _register(contract, direct_vm, direct_alice, 100)

    _mock_llm_for(
        scorecard_dict(functional=90, quality=90, security=90, completeness=90),
        direct_vm,
    )

    contract.post_job(
        job_id="done-job",
        agent=alice,
        repo_url="https://example.com/repo",
        commit_hash="v1",
        test_command="true",
        requirements=[],
        deadline=9999999999,
        files=[],
    )
    contract.accept_job("done-job")
    contract.verify("done-job")

    job = contract.get_job("done-job")
    data = json.loads(job)
    assert data["recorded"] is True
    assert data["verdict"] == "PASS"
    assert data["final_score"] > 0

    # Second verify attempt must fail
    try:
        contract.verify("done-job")
    except Exception as e:
        msg = str(e)
    else:
        msg = None
    assert msg is not None
    assert "already verified" in msg


def test_only_agent_or_issuer_before_deadline(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = direct_deploy("contracts/verity.py")
    alice = to_hex(direct_alice)
    bob = to_hex(direct_bob)

    _register(contract, direct_vm, direct_alice, 100)
    _register(contract, direct_vm, direct_bob, 100)

    contract.post_job(
        job_id="protected",
        agent=bob,
        repo_url="https://example.com/repo",
        commit_hash="v1",
        test_command="true",
        requirements=[],
        deadline=9999999999,
        files=[],
    )

    # Bob (agent) can verify before deadline
    direct_vm.sender = direct_bob
    _mock_llm_for(
        scorecard_dict(functional=90, quality=90, security=90, completeness=90),
        direct_vm,
    )
    contract.accept_job("protected")
    verdict = contract.verify("protected")
    assert verdict == "PASS"

    # Alice (issuer) can verify before deadline
    direct_vm.sender = direct_alice
    contract.post_job(
        job_id="issuer-verify",
        agent=bob,
        repo_url="https://example.com/repo2",
        commit_hash="v1",
        test_command="true",
        requirements=[],
        deadline=9999999999,
        files=[],
    )
    _mock_llm_for(
        scorecard_dict(functional=90, quality=90, security=90, completeness=90),
        direct_vm,
    )
    # only the NAMED agent may accept; the issuer triggering verify is a
    # separate capability from accepting
    direct_vm.sender = direct_bob
    contract.accept_job("issuer-verify")
    direct_vm.sender = direct_alice
    verdict = contract.verify("issuer-verify")
    assert verdict == "PASS"


def test_unverified_job_has_no_scorecard(direct_deploy, direct_alice):
    contract = direct_deploy("contracts/verity.py")

    contract.post_job(
        job_id="unverified",
        agent=to_hex(direct_alice),
        repo_url="https://example.com/repo",
        commit_hash="v1",
        test_command="true",
        requirements=[],
        deadline=9999999999,
        files=[],
    )

    sc = contract.get_scorecard("unverified")
    data = json.loads(sc)
    assert data["exists"] is False


def test_scorecard_not_shown_for_unverified_job(direct_deploy):
    contract = direct_deploy("contracts/verity.py")
    sc = contract.get_scorecard("nonexistent")
    data = json.loads(sc)
    assert data["exists"] is False


def test_evaluate_fetches_real_artifact_files(direct_vm, direct_deploy, direct_alice):
    """Prove the contract fetches actual code files from GitHub before evaluating.

    This is the test the steward asked for: the contract must retrieve the
    immutable artifact on-chain (via gl.nondet.web.render) and base the
    independent evaluation on that evidence, not just pass URL strings to
    the model.
    """
    contract = direct_deploy("contracts/verity.py")
    alice = to_hex(direct_alice)

    _register(contract, direct_vm, direct_alice, 100)

    # Simulate a real GitHub repo with verifiable source files.
    # raw.githubusercontent.com URLs are what the contract constructs
    # via _github_raw_base and fetches via gl.nondet.web.render.
    repo = "https://github.com/example/verity-test-repo"
    commit = "abc123def456"
    raw_base = contract._github_raw_base(repo, commit)
    assert raw_base is not None, "github raw base should resolve for github.com URLs"

    # Mock the web fetch for each file the contract will retrieve
    file_contents = {
        "src/main.py": "def add(a, b):\n    return a + b\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        "README.md": "# Test Repo\nA simple calculator library.\n",
        "requirements.txt": "pytest>=7.0\n",
    }
    # mock_web takes a {method, status, body} dict - passing a bare string makes
    # the mock raise internally, the contract swallows it, and the test silently
    # passes against the URL-only fallback prompt instead of the fetched one.
    for filepath, content in file_contents.items():
        direct_vm.mock_web(
            raw_base + filepath,
            {"method": "GET", "status": 200, "body": content},
        )

    # Also mock the LLM response — it must be the prompt built from the
    # FETCHED file contents, not the URL-only fallback. The pattern is a
    # single line on purpose: the mock matcher is anchored and does not honour
    # DOTALL, so a `.*` spanning newlines silently never matches.
    direct_vm.mock_llm(
        r"Artifact files \(https://github\.com/example/verity-test-repo@",
        json.dumps(scorecard_dict(functional=90, quality=85, security=80, completeness=90)),
    )

    contract.post_job(
        job_id="artifact-fetch-job",
        agent=alice,
        repo_url=repo,
        commit_hash=commit,
        test_command="pytest",
        requirements=["must have tests", "must have README"],
        deadline=9999999999,
        files=["src/main.py", "README.md", "requirements.txt"],
    )

    contract.accept_job("artifact-fetch-job")
    verdict = contract.verify("artifact-fetch-job")
    assert verdict == "PASS"

    # Verify the scorecard was stored
    sc = contract.get_scorecard("artifact-fetch-job")
    data = json.loads(sc)
    assert data["exists"] is True
    assert data["verdict"] == "PASS"
    assert data["functional"] == 90
    assert data["quality"] == 85
    assert data["security"] == 80
    assert data["completeness"] == 90
    assert data["evidence_hash"] != ""