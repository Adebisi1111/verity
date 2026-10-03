"""Audit of paths the four fixes could still have left open."""

import json

from tests.direct.conftest import to_hex, scorecard_dict

ONE_GEN = 1000000000000000000


def _err(fn, *needles):
    try:
        fn()
    except Exception as e:
        msg = str(e).lower()
        for n in needles:
            assert n.lower() in msg, f"expected {n!r} in {msg!r}"
        return msg
    raise AssertionError("expected rejection, but the call succeeded")


def _expire(contract, job_id):
    """Force a job's deadline into the past (direct mode reads the real clock)."""
    job = contract.jobs[job_id]
    job.deadline = type(job.deadline)(1)
    contract.jobs[job_id] = job


def _reg(contract, direct_vm, who, stake=3 * ONE_GEN):
    direct_vm.sender = who
    direct_vm.value = stake
    contract.register()


def _post(contract, direct_vm, issuer, agent_hex, rid, commit="abc123", deadline=9999999999):
    direct_vm.sender = issuer
    contract.post_job(rid, agent_hex, "https://github.com/acme/repo", commit,
                      "pytest", ["has tests"], deadline, ["main.py"])


# ---------------------------------------------------------------------
# The hole: accept an obligation, then walk away with the stake backing it
# ---------------------------------------------------------------------

def test_cannot_withdraw_stake_that_backs_an_accepted_job(direct_vm, direct_deploy,
                                                          direct_alice, direct_bob):
    """Acceptance encumbers stake. Otherwise the slash becomes a no-op.

    An agent could otherwise: accept a job -> deactivate -> withdraw
    everything -> let the job expire -> be 'slashed' 0 wei. That makes the
    whole acceptance gate worthless as an economic mechanism.
    """
    contract = direct_deploy("contracts/verity.py")
    _reg(contract, direct_vm, direct_alice, stake=5 * ONE_GEN)
    alice = to_hex(direct_alice)
    _post(contract, direct_vm, direct_bob, alice, "j1", commit="aaa")
    direct_vm.sender = direct_alice
    contract.accept_job("j1")

    before = json.loads(contract.get_agent(alice))
    assert before["staked"] == 5 * ONE_GEN, before
    assert before["open_jobs"] == 1, before

    # 5 GEN staked: taking 4 leaves 1, which is ABOVE the floor - so the only
    # thing that can block this is the encumbrance, not the minimum-stake rule
    _err(lambda: contract.request_withdraw(4 * ONE_GEN), "accepted job")


def test_cannot_deactivate_while_an_accepted_job_is_open(direct_vm, direct_deploy,
                                                         direct_alice, direct_bob):
    contract = direct_deploy("contracts/verity.py")
    _reg(contract, direct_vm, direct_alice)
    alice = to_hex(direct_alice)
    _post(contract, direct_vm, direct_bob, alice, "j1", commit="bbb")
    direct_vm.sender = direct_alice
    contract.accept_job("j1")
    _err(lambda: contract.deactivate(), "accepted job")


def test_stake_is_releasable_once_the_accepted_job_is_settled(direct_vm, direct_deploy,
                                                              direct_alice, direct_bob):
    """The guard must not permanently trap the stake either."""
    contract = direct_deploy("contracts/verity.py")
    _reg(contract, direct_vm, direct_alice, stake=10 * ONE_GEN)
    alice = to_hex(direct_alice)

    _post(contract, direct_vm, direct_bob, alice, "j1", commit="ccc")
    _expire(contract, "j1")

    direct_vm.sender = direct_alice
    contract.accept_job("j1")
    contract.settle_unclaimed("j1")

    # obligation discharged - the agent is free to leave with its stake
    contract.deactivate()
    nonce = contract.request_withdraw(10 * ONE_GEN - ONE_GEN)
    assert contract.claim_withdraw(nonce) == 10 * ONE_GEN - ONE_GEN


# ---------------------------------------------------------------------
# Artifact keys
# ---------------------------------------------------------------------

def test_only_verification_writes_the_artifact_key(direct_vm, direct_deploy,
                                                    direct_alice, direct_bob):
    """Posting, accepting, declining and expiring must all leave the key free."""
    contract = direct_deploy("contracts/verity.py")
    _reg(contract, direct_vm, direct_alice)
    alice = to_hex(direct_alice)

    _post(contract, direct_vm, direct_bob, alice, "p1", commit="kkk")
    _post(contract, direct_vm, direct_bob, alice, "p2", commit="kkk")
    assert json.loads(contract.get_artifact_key("https://github.com/acme/repo", "kkk"))["reserved"] is False

    direct_vm.sender = direct_alice
    contract.accept_job("p1")
    assert json.loads(contract.get_artifact_key("https://github.com/acme/repo", "kkk"))["reserved"] is False

    contract.decline_job("p2")
    assert json.loads(contract.get_artifact_key("https://github.com/acme/repo", "kkk"))["reserved"] is False


def test_expired_unaccepted_job_leaves_the_key_free(direct_vm, direct_deploy,
                                                    direct_alice, direct_bob):
    contract = direct_deploy("contracts/verity.py")
    _reg(contract, direct_vm, direct_alice)
    alice = to_hex(direct_alice)

    _post(contract, direct_vm, direct_bob, alice, "j1", commit="exp")
    _expire(contract, "j1")

    assert contract.settle_unclaimed("j1") == "EXPIRED_UNACCEPTED"
    assert json.loads(contract.get_artifact_key("https://github.com/acme/repo", "exp"))["reserved"] is False


def test_two_unverified_jobs_may_share_one_artifact(direct_vm, direct_deploy,
                                                    direct_alice, direct_bob):
    contract = direct_deploy("contracts/verity.py")
    _reg(contract, direct_vm, direct_alice)
    alice = to_hex(direct_alice)
    for i in range(10):
        _post(contract, direct_vm, direct_bob, alice, f"j{i}", commit="shared")
    assert json.loads(contract.get_job("j9"))["exists"] is True


# ---------------------------------------------------------------------
# Custody reconciliation
# ---------------------------------------------------------------------

def test_ledger_accounts_for_every_wei(direct_vm, direct_deploy,
                                       direct_alice, direct_bob):
    """staked + slashed_pool + sink must reconstruct what was ever staked."""
    contract = direct_deploy("contracts/verity.py")
    _reg(contract, direct_vm, direct_alice, stake=8 * ONE_GEN)
    alice = to_hex(direct_alice)

    # slash once
    _post(contract, direct_vm, direct_bob, alice, "j1", commit="s1")
    _expire(contract, "j1")
    direct_vm.sender = direct_alice
    contract.accept_job("j1")
    contract.settle_unclaimed("j1")

    # withdraw part of it
    nonce = contract.request_withdraw(ONE_GEN)
    contract.claim_withdraw(nonce)

    # dispose the slash
    contract.dispose_slashed()

    rec = json.loads(contract.get_agent(alice))
    led = json.loads(contract.get_pending_withdraw(alice))
    accounted = int(rec["staked"]) + int(led["slashed_pool"]) + int(led["slashed_sink"]) \
        + int(led["settled_withdrawals"])
    assert accounted == 8 * ONE_GEN, (accounted, rec, led)


def test_settled_withdrawals_is_a_lifetime_total(direct_vm, direct_deploy, direct_alice):
    """Guards against the harness bug of treating it as a per-payout figure."""
    contract = direct_deploy("contracts/verity.py")
    _reg(contract, direct_vm, direct_alice, stake=10 * ONE_GEN)
    alice = to_hex(direct_alice)

    for expected_total, amount in [(ONE_GEN, ONE_GEN), (2 * ONE_GEN, ONE_GEN)]:
        nonce = contract.request_withdraw(amount)
        contract.claim_withdraw(nonce)
        led = json.loads(contract.get_pending_withdraw(alice))
        assert led["settled_withdrawals"] == expected_total, led


def test_pending_funds_are_never_slashed(direct_vm, direct_deploy,
                                         direct_alice, direct_bob):
    """Reserved stake has left `staked`, so a slash cannot touch it."""
    contract = direct_deploy("contracts/verity.py")
    _reg(contract, direct_vm, direct_alice, stake=10 * ONE_GEN)
    alice = to_hex(direct_alice)

    contract.request_withdraw(2 * ONE_GEN)

    _post(contract, direct_vm, direct_bob, alice, "j1", commit="ps")
    _expire(contract, "j1")
    direct_vm.sender = direct_alice
    contract.accept_job("j1")
    contract.settle_unclaimed("j1")

    rec = json.loads(contract.get_agent(alice))
    assert rec["pending_withdraw"] == 2 * ONE_GEN, rec
    # 8 GEN remained slashable; 10% of that is the burn
    assert rec["slashed_total"] == 8 * ONE_GEN * 10 // 100, rec


# ---------------------------------------------------------------------
# No unaccepted path to reputation
# ---------------------------------------------------------------------

def test_reputation_requires_acceptance_on_every_path(direct_vm, direct_deploy,
                                                      direct_alice, direct_bob):
    """Both reputation-mutating entry points must refuse an unaccepted job."""
    contract = direct_deploy("contracts/verity.py")
    _reg(contract, direct_vm, direct_alice)
    alice = to_hex(direct_alice)

    _post(contract, direct_vm, direct_bob, alice, "j1", commit="noacc")
    _expire(contract, "j1")

    direct_vm.sender = direct_alice
    _err(lambda: contract.verify("j1"), "not accepted")
    contract.settle_unclaimed("j1")

    rec = json.loads(contract.get_agent(alice))
    assert rec["completed"] == 0 and rec["failed"] == 0, rec
    assert rec["slashed_count"] == 0, rec
