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