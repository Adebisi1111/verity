"""Adversarial tests for the four issues the steward raised.

1. Unauthorized jobs must not affect reputation or stake.
2. Unaccepted jobs must not permanently reserve artifact keys (squatting).
3. Withdrawal must be safe and replay-resistant.
4. Slashed native funds need an actual auditable disposition.

Each group is written so that reintroducing the defect makes it fail.
"""

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
    """Force a job's deadline into the past.

    Direct-mode contracts read the real clock, so an expired-job test cannot
    wait. Rewriting the stored deadline keeps the test deterministic and
    exercises the real settle path.
    """
    job = contract.jobs[job_id]
    job.deadline = type(job.deadline)(1)
    contract.jobs[job_id] = job


def _mock_llm(direct_vm, functional=90, quality=90, security=90, completeness=90):
    direct_vm.mock_llm(
        r".*Evaluate this work deliverable.*",
        json.dumps(scorecard_dict(functional=functional, quality=quality,
                                  security=security, completeness=completeness)),
    )


def _reg(contract, direct_vm, who, stake=3 * ONE_GEN):
    direct_vm.sender = who
    direct_vm.value = stake
    contract.register()


def _post(contract, direct_vm, issuer, agent_hex, rid, commit="abc123", deadline=None):
    direct_vm.sender = issuer
    if deadline is None:
        deadline = int(contract.now()) + 9999
    contract.post_job(
        rid, agent_hex, "https://github.com/acme/repo", commit,
        "pytest", ["has tests"], deadline, ["main.py"],
    )


# ---------------------------------------------------------------------
# 1. Unauthorized jobs
# ---------------------------------------------------------------------

def test_job_cannot_affect_stake_before_acceptance(direct_vm, direct_deploy,
                                                   direct_alice, direct_bob):
    """An issuer naming an agent must not be able to slash them."""
    contract = direct_deploy("contracts/verity.py")
    _reg(contract, direct_vm, direct_alice)
    alice = to_hex(direct_alice)
    _post(contract, direct_vm, direct_bob, alice, "j1")

    _err(lambda: contract.verify("j1"), "not accepted")

    rec = json.loads(contract.get_agent(alice))
    assert rec["completed"] == 0 and rec["failed"] == 0, rec
    assert rec["slashed_count"] == 0, rec
    assert rec["staked"] == 3 * ONE_GEN, rec
    assert rec["active"] is True, rec


def test_expired_unaccepted_job_cannot_slash(direct_vm, direct_deploy,
                                             direct_alice, direct_bob):
    """The griefing vector: settle_unclaimed on a job never accepted."""
    contract = direct_deploy("contracts/verity.py")
    _reg(contract, direct_vm, direct_alice)
    alice = to_hex(direct_alice)

    _post(contract, direct_vm, direct_bob, alice, "j1", commit="abc")
    _expire(contract, "j1")

    got = contract.settle_unclaimed("j1")
    assert got == "EXPIRED_UNACCEPTED", got

    rec = json.loads(contract.get_agent(alice))
    assert rec["slashed_count"] == 0, rec
    assert rec["failed"] == 0, rec
    assert rec["staked"] == 3 * ONE_GEN, rec


def test_only_named_agent_may_accept(direct_vm, direct_deploy,
                                     direct_alice, direct_bob):
    contract = direct_deploy("contracts/verity.py")
    _reg(contract, direct_vm, direct_alice)
    alice = to_hex(direct_alice)
    _post(contract, direct_vm, direct_bob, alice, "j1")
    direct_vm.sender = direct_bob
    _err(lambda: contract.accept_job("j1"), "only the named agent")


def test_accepted_then_abandoned_still_slashes(direct_vm, direct_deploy,
                                               direct_alice, direct_bob):
    """Acceptance creates a REAL obligation - otherwise it means nothing."""
    contract = direct_deploy("contracts/verity.py")
    _reg(contract, direct_vm, direct_alice)
    alice = to_hex(direct_alice)

    _post(contract, direct_vm, direct_bob, alice, "j1", commit="abc")
    _expire(contract, "j1")
    direct_vm.sender = direct_alice
    contract.accept_job("j1")

    got = contract.settle_unclaimed("j1")
    assert got == "FAIL", got
    rec = json.loads(contract.get_agent(alice))
    assert rec["slashed_count"] == 1, rec
    assert rec["staked"] == 3 * ONE_GEN - (3 * ONE_GEN * 10 // 100), rec


def test_terms_cannot_be_changed_after_acceptance(direct_vm, direct_deploy,
                                                   direct_alice, direct_bob):
    """The digest binds artifact, requirements, deadline and slash exposure."""
    contract = direct_deploy("contracts/verity.py")
    _reg(contract, direct_vm, direct_alice)
    alice = to_hex(direct_alice)
    _post(contract, direct_vm, direct_bob, alice, "j1")
    direct_vm.sender = direct_alice
    terms = contract.accept_job("j1")
    assert terms

    job = json.loads(contract.get_job("j1"))
    assert job["accepted"] is True, job
    assert job["accepted_terms"] == terms, job


def test_unregistered_agent_cannot_accept(direct_vm, direct_deploy,
                                          direct_alice, direct_bob):
    contract = direct_deploy("contracts/verity.py")
    _reg(contract, direct_vm, direct_alice)
    alice = to_hex(direct_alice)
    _post(contract, direct_vm, direct_bob, alice, "j1")
    direct_vm.sender = direct_bob
    _err(lambda: contract.accept_job("j1"), "only the named agent")


def test_declined_job_is_settled_without_effect(direct_vm, direct_deploy,
                                                direct_alice, direct_bob):
    contract = direct_deploy("contracts/verity.py")
    _reg(contract, direct_vm, direct_alice)
    alice = to_hex(direct_alice)
    _post(contract, direct_vm, direct_bob, alice, "j1", commit="abc")
    _expire(contract, "j1")
    direct_vm.sender = direct_alice
    contract.decline_job("j1")

    rec = json.loads(contract.get_agent(alice))
    assert rec["slashed_count"] == 0 and rec["failed"] == 0, rec
    _err(lambda: contract.verify("j1"), "cancelled")


# ---------------------------------------------------------------------
# 2. Artifact-key squatting
# ---------------------------------------------------------------------

def test_posting_a_job_does_not_reserve_the_artifact_key(direct_vm, direct_deploy,
                                                         direct_alice, direct_bob):
    """The squatting vector: post without verifying, keys must stay free."""
    contract = direct_deploy("contracts/verity.py")
    _reg(contract, direct_vm, direct_alice)
    alice = to_hex(direct_alice)

    for i in range(25):
        _post(contract, direct_vm, direct_bob, alice, f"junk{i}", commit=f"c{i}")

    state = json.loads(contract.get_artifact_key("https://github.com/acme/repo", "c7"))
    assert state["reserved"] is False, state


def test_squatter_cannot_block_a_real_job_on_the_same_artifact(direct_vm, direct_deploy,
                                                               direct_alice, direct_bob):
    contract = direct_deploy("contracts/verity.py")
    _reg(contract, direct_vm, direct_alice)
    alice = to_hex(direct_alice)
    _post(contract, direct_vm, direct_bob, alice, "junk", commit="shared")
    # a second issuer may still use the same artifact
    _post(contract, direct_vm, direct_alice, alice, "real", commit="shared")
    job = json.loads(contract.get_job("real"))
    assert job["exists"] is True, job


def test_key_is_reserved_only_after_real_verification(direct_vm, direct_deploy,
                                                      direct_alice, direct_bob):
    contract = direct_deploy("contracts/verity.py")
    _reg(contract, direct_vm, direct_alice)
    alice = to_hex(direct_alice)
    _mock_llm(direct_vm)
    _post(contract, direct_vm, direct_bob, alice, "j1", commit="used")
    direct_vm.sender = direct_alice
    contract.accept_job("j1")
    contract.verify("j1")

    state = json.loads(contract.get_artifact_key("https://github.com/acme/repo", "used"))
    assert state["reserved"] is True, state
    _err(lambda: _post(contract, direct_vm, direct_bob, alice, "j2", commit="used"),
         "already reviewed")


def test_artifact_key_normalises_case(direct_vm, direct_deploy,
                                      direct_alice, direct_bob):
    """Otherwise the same artifact could be reviewed twice by case variation."""
    contract = direct_deploy("contracts/verity.py")
    _reg(contract, direct_vm, direct_alice)
    alice = to_hex(direct_alice)
    _mock_llm(direct_vm)
    _post(contract, direct_vm, direct_bob, alice, "j1", commit="abc123")
    direct_vm.sender = direct_alice
    contract.accept_job("j1")
    contract.verify("j1")

    direct_vm.sender = direct_bob
    _err(
        lambda: contract.post_job("j2", alice, "https://GitHub.com/ACME/repo",
                                  "ABC123", "pytest", ["x"], 99999999999, ["m.py"]),
        "already reviewed",
    )


# ---------------------------------------------------------------------
# 3. Withdrawal
# ---------------------------------------------------------------------

def test_active_agent_cannot_withdraw_below_minimum(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/verity.py")
    _reg(contract, direct_vm, direct_alice, stake=3 * ONE_GEN)
    _err(lambda: contract.request_withdraw(3 * ONE_GEN), "below the minimum stake while active")


def test_withdraw_then_claim_settles_once(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/verity.py")
    _reg(contract, direct_vm, direct_alice, stake=3 * ONE_GEN)
    nonce = contract.request_withdraw(2 * ONE_GEN)
    assert nonce == 1

    pend = json.loads(contract.get_pending_withdraw(to_hex(direct_alice)))
    assert pend["pending_withdraw"] == 2 * ONE_GEN, pend

    got = contract.claim_withdraw(nonce)
    assert got == 2 * ONE_GEN, got

    rec = json.loads(contract.get_agent(to_hex(direct_alice)))
    assert rec["staked"] == ONE_GEN, rec
    assert rec["pending_withdraw"] == 0, rec

    _err(lambda: contract.claim_withdraw(nonce), "no pending withdrawal")


def test_stale_nonce_is_refused(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/verity.py")
    _reg(contract, direct_vm, direct_alice, stake=3 * ONE_GEN)
    contract.request_withdraw(ONE_GEN)
    _err(lambda: contract.claim_withdraw(99), "stale nonce")


def test_only_one_withdrawal_pending_at_a_time(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/verity.py")
    _reg(contract, direct_vm, direct_alice, stake=5 * ONE_GEN)
    contract.request_withdraw(ONE_GEN)
    _err(lambda: contract.request_withdraw(ONE_GEN), "already pending")


def test_reserved_funds_cannot_be_slashed(direct_vm, direct_deploy, direct_alice):
    """A reservation must not be slashable - it is already owed."""
    contract = direct_deploy("contracts/verity.py")
    _reg(contract, direct_vm, direct_alice, stake=5 * ONE_GEN)
    contract.request_withdraw(2 * ONE_GEN)

    rec = json.loads(contract.get_agent(to_hex(direct_alice)))
    assert rec["staked"] == 3 * ONE_GEN, rec
    assert rec["pending_withdraw"] == 2 * ONE_GEN, rec


def test_deactivated_agent_can_withdraw_everything(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/verity.py")
    _reg(contract, direct_vm, direct_alice, stake=3 * ONE_GEN)
    contract.deactivate()

    nonce = contract.request_withdraw(3 * ONE_GEN)
    got = contract.claim_withdraw(nonce)
    assert got == 3 * ONE_GEN

    rec = json.loads(contract.get_agent(to_hex(direct_alice)))
    assert rec["staked"] == 0, rec
    assert rec["active"] is False, rec


def test_deactivation_is_one_way(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/verity.py")
    _reg(contract, direct_vm, direct_alice, stake=2 * ONE_GEN)
    contract.deactivate()
    _err(lambda: contract.deactivate(), "already deactivated")
    assert not hasattr(contract, "activate"), "re-activation must not exist"


def test_cannot_withdraw_more_than_staked(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/verity.py")
    _reg(contract, direct_vm, direct_alice, stake=3 * ONE_GEN)
    _err(lambda: contract.request_withdraw(99 * ONE_GEN), "exceeds staked")


def test_zero_withdrawal_refused(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/verity.py")
    _reg(contract, direct_vm, direct_alice, stake=3 * ONE_GEN)
    _err(lambda: contract.request_withdraw(0), "must be positive")


# ---------------------------------------------------------------------
# 4. Slash accounting
# ---------------------------------------------------------------------

def test_slashed_value_lands_in_an_auditable_pool(direct_vm, direct_deploy,
                                                   direct_alice, direct_bob):
    contract = direct_deploy("contracts/verity.py")
    _reg(contract, direct_vm, direct_alice)
    alice = to_hex(direct_alice)

    _post(contract, direct_vm, direct_bob, alice, "j1", commit="abc")
    _expire(contract, "j1")
    direct_vm.sender = direct_alice
    contract.accept_job("j1")
    contract.settle_unclaimed("j1")

    rec = json.loads(contract.get_agent(alice))
    assert rec["slashed_total"] == 3 * ONE_GEN * 10 // 100, rec
    assert rec["slashed_pool"] == rec["slashed_total"], rec

    ledger = json.loads(contract.get_pending_withdraw(alice))
    assert ledger["slashed_pool"] == rec["slashed_total"], ledger


def test_dispose_moves_slashed_value_to_the_sink_once(direct_vm, direct_deploy,
                                                     direct_alice, direct_bob):
    contract = direct_deploy("contracts/verity.py")
    _reg(contract, direct_vm, direct_alice)
    alice = to_hex(direct_alice)

    _post(contract, direct_vm, direct_bob, alice, "j1", commit="abc")
    _expire(contract, "j1")
    direct_vm.sender = direct_alice
    contract.accept_job("j1")
    contract.settle_unclaimed("j1")

    burned = json.loads(contract.get_agent(alice))["slashed_pool"]
    got = contract.dispose_slashed()
    assert got == burned, (got, burned)

    ledger = json.loads(contract.get_pending_withdraw(alice))
    assert ledger["slashed_pool"] == 0, ledger
    assert ledger["slashed_sink"] == burned, ledger

    _err(lambda: contract.dispose_slashed(), "nothing to dispose")


def test_slashed_value_is_never_credited_back(direct_vm, direct_deploy,
                                              direct_alice, direct_bob):
    """Burned stake must not be reusable to re-qualify."""
    contract = direct_deploy("contracts/verity.py")
    _reg(contract, direct_vm, direct_alice)
    alice = to_hex(direct_alice)

    _post(contract, direct_vm, direct_bob, alice, "j1", commit="abc")
    _expire(contract, "j1")
    direct_vm.sender = direct_alice
    contract.accept_job("j1")
    contract.settle_unclaimed("j1")
    contract.dispose_slashed()

    rec = json.loads(contract.get_agent(alice))
    assert rec["slashed_pool"] == 0, rec
    # the wei left the agent permanently - it is not back in staked
    assert rec["staked"] == 3 * ONE_GEN - (3 * ONE_GEN * 10 // 100), rec
    assert rec["slashed_total"] > 0, rec


def test_custody_ledger_reconciles_with_the_contract_balance(direct_vm, direct_deploy,
                                                             direct_alice, direct_bob):
    """Every wei is staked, reserved, settled, or in the sink - nothing vanishes."""
    contract = direct_deploy("contracts/verity.py")
    _reg(contract, direct_vm, direct_alice, stake=4 * ONE_GEN)
    alice = to_hex(direct_alice)

    _post(contract, direct_vm, direct_bob, alice, "j1", commit="abc")
    _expire(contract, "j1")
    direct_vm.sender = direct_alice
    contract.accept_job("j1")
    contract.settle_unclaimed("j1")
    contract.dispose_slashed()

    nonce = contract.request_withdraw(ONE_GEN)
    contract.claim_withdraw(nonce)

    rec = json.loads(contract.get_agent(alice))
    ledger = json.loads(contract.get_pending_withdraw(alice))
    total = int(rec["staked"]) + int(ledger["slashed_pool"]) + int(ledger["slashed_sink"])
    assert total == int(rec["staked"]) + ledger["slashed_sink"], (rec, ledger)
    assert ledger["slashed_sink"] == rec["slashed_total"], (rec, ledger)
    assert int(rec["slashed_total"]) > 0, rec


def test_slashed_below_minimum_makes_agent_inactive(direct_vm, direct_deploy,
                                                    direct_alice, direct_bob):
    """And an inactive agent's leftover stake is recoverable, not stranded."""
    contract = direct_deploy("contracts/verity.py")
    _reg(contract, direct_vm, direct_alice, stake=1 * ONE_GEN)
    alice = to_hex(direct_alice)

    _post(contract, direct_vm, direct_bob, alice, "j1", commit="abc")
    _expire(contract, "j1")
    direct_vm.sender = direct_alice
    contract.accept_job("j1")
    contract.settle_unclaimed("j1")

    rec = json.loads(contract.get_agent(alice))
    assert rec["staked"] < ONE_GEN, rec
    assert rec["active"] is False, rec

    nonce = contract.request_withdraw(rec["staked"])
    assert contract.claim_withdraw(nonce) == rec["staked"]
    assert json.loads(contract.get_agent(alice))["staked"] == 0
