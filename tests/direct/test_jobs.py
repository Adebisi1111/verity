"""Tests for job posting validation."""

import json
from tests.direct.conftest import to_hex, scorecard_dict


def test_post_job_stores_artifact(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/verity.py")
    alice = to_hex(direct_alice)

    direct_vm.sender = alice
    contract.post_job(
        job_id="job1",
        agent=alice,
        repo_url="https://example.com/repo",
        commit_hash="abc123",
        test_command="pytest -x",
        requirements=["must have README", "must pass tests"],
        deadline=9999999999,
        files=[],
    )

    job = contract.get_job("job1")
    data = json.loads(job)
    assert data["job_id"] == "job1"
    assert data["agent"] == alice
    assert data["repo_url"] == "https://example.com/repo"
    assert data["commit_hash"] == "abc123"
    assert data["test_command"] == "pytest -x"
    assert data["recorded"] is False
    assert data["verdict"] == ""


def test_post_job_rejects_duplicate_job_id(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/verity.py")
    alice = to_hex(direct_alice)

    direct_vm.sender = alice
    contract.post_job(
        job_id="dup-job",
        agent=alice,
        repo_url="https://example.com/repo",
        commit_hash="abc123",
        test_command="true",
        requirements=[],
        deadline=9999999999,
        files=[],
    )

    try:
        contract.post_job(
            job_id="dup-job",
            agent=alice,
            repo_url="https://example.com/repo",
            commit_hash="abc123",
            test_command="true",
            requirements=[],
            deadline=9999999999,
            files=[],
        )
    except Exception as e:
        msg = str(e)
    else:
        msg = None
    assert msg is not None
    assert "already exists" in msg or "duplicate" in msg.lower()


def test_post_job_rejects_non_http_url(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/verity.py")
    alice = to_hex(direct_alice)

    direct_vm.sender = alice
    try:
        contract.post_job(
            job_id="bad-url",
            agent=alice,
            repo_url="not-a-url",
            commit_hash="abc123",
            test_command="true",
            requirements=[],
            deadline=9999999999,
            files=[],
        )
    except Exception as e:
        msg = str(e)
    else:
        msg = None
    assert msg is not None
    assert "http" in msg.lower()


def test_post_job_rejects_past_deadline(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/verity.py")
    alice = to_hex(direct_alice)

    direct_vm.sender = alice
    try:
        contract.post_job(
            job_id="past-job",
            agent=alice,
            repo_url="https://example.com/repo",
            commit_hash="abc123",
            test_command="true",
            requirements=[],
            deadline=100,
            files=[],
        )
    except Exception as e:
        msg = str(e)
    else:
        msg = None
    assert msg is not None
    assert "deadline" in msg.lower() or "past" in msg.lower()


def test_post_job_prevents_duplicate_artifact_review(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/verity.py")
    alice = to_hex(direct_alice)

    direct_vm.sender = alice
    contract.post_job(
        job_id="job-artifact-1",
        agent=alice,
        repo_url="https://example.com/repo",
        commit_hash="same-commit",
        test_command="true",
        requirements=[],
        deadline=9999999999,
        files=[],
    )

    try:
        contract.post_job(
            job_id="job-artifact-2",
            agent=alice,
            repo_url="https://example.com/repo",
            commit_hash="same-commit",
            test_command="true",
            requirements=[],
            deadline=9999999999,
            files=[],
        )
    except Exception as e:
        msg = str(e)
    else:
        msg = None
    assert msg is not None
    assert "already reviewed" in msg.lower() or "duplicate" in msg.lower()


def test_post_job_different_commits_allowed(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/verity.py")
    alice = to_hex(direct_alice)

    direct_vm.sender = alice
    contract.post_job(
        job_id="job-v1",
        agent=alice,
        repo_url="https://example.com/repo",
        commit_hash="commit-v1",
        test_command="true",
        requirements=[],
        deadline=9999999999,
        files=[],
    )
    contract.post_job(
        job_id="job-v2",
        agent=alice,
        repo_url="https://example.com/repo",
        commit_hash="commit-v2",
        test_command="true",
        requirements=[],
        deadline=9999999999,
        files=[],
    )

    j1 = json.loads(contract.get_job("job-v1"))
    j2 = json.loads(contract.get_job("job-v2"))
    assert j1["job_id"] == "job-v1"
    assert j2["job_id"] == "job-v2"