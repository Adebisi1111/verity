"""Tests for agent registration and stake management."""

import json
from tests.direct.conftest import to_hex


def test_register_sets_stake(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/verity.py")
    alice = to_hex(direct_alice)

    direct_vm.sender = direct_alice
    direct_vm.value = 100
    contract.register()

    rec = contract.get_agent(alice)
    data = json.loads(rec)
    assert data["exists"] is True
    assert data["staked"] == 100
    assert data["tier"] == "UNVERIFIED"


def test_register_is_idempotent_adds_stake(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/verity.py")
    alice = to_hex(direct_alice)

    direct_vm.sender = direct_alice
    direct_vm.value = 100
    contract.register()
    direct_vm.value = 50
    contract.register()

    rec = contract.get_agent(alice)
    data = json.loads(rec)
    assert data["staked"] == 150


def test_register_requires_value(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/verity.py")

    direct_vm.sender = direct_alice
    direct_vm.value = 0
    try:
        contract.register()
    except Exception as e:
        msg = str(e)
    else:
        msg = None
    assert msg is not None
    assert "send at least some GEN" in msg or "send at least" in msg.lower()


def test_unregistered_agent_has_default_view(direct_deploy):
    contract = direct_deploy("contracts/verity.py")
    rec = contract.get_agent("0xdeadbeef1234567890abcdef1234567890abcdef")
    data = json.loads(rec)
    assert data["exists"] is False
    assert data["tier"] == "UNVERIFIED"
    assert data["staked"] == 0


def test_trusted_tier_after_stake_and_passes(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/verity.py")
    alice = to_hex(direct_alice)

    # Register with high stake (5x min_stake = 5 GEN for TRUSTED tier)
    direct_vm.sender = direct_alice
    direct_vm.value = 5000000000000000000
    contract.register()

    # Mock a passing verification
    import json as json_mod
    direct_vm.mock_llm(
        r".*Evaluate this work deliverable.*",
        json_mod.dumps({"functional": 90, "quality": 85, "security": 80, "completeness": 90, "reasoning": "good"}),
    )

    # Post and verify a job
    contract.post_job(
        job_id="job1",
        agent=alice,
        repo_url="https://example.com/repo",
        commit_hash="abc123",
        test_command="pytest",
        requirements=["must have tests", "must handle errors"],
        deadline=9999999999,
        files=[],
    )
    contract.accept_job("job1")
    contract.verify("job1")

    rec = contract.get_agent(alice)
    data = json.loads(rec)
    assert data["completed"] == 1
    assert data["avg_score"] > 0
    assert data["tier"] == "TRUSTED"