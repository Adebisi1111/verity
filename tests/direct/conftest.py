"""Shared helpers for VERITy direct-mode tests."""

from typing import Any


def to_hex(addr: Any) -> str:
    """Return checksummed hex for an address-like value."""
    if hasattr(addr, "as_hex"):
        return addr.as_hex
    if isinstance(addr, bytes):
        try:
            from genlayer.py.types import Address
            return str(Address(addr))
        except Exception:
            return "0x" + addr.hex()
    s = str(addr)
    if s.startswith("0x") or s.startswith("0X"):
        return s
    return "0x" + s


def scorecard_dict(
    functional: int,
    quality: int,
    security: int,
    completeness: int,
    *,
    weight_functional: int = 40,
    weight_quality: int = 25,
    weight_security: int = 25,
    weight_completeness: int = 10,
    pass_threshold: int = 70,
    partial_threshold: int = 40,
) -> dict:
    """Build the LLM response dict a mock should return for _evaluate.

    Computes the weighted overall score and categorical verdict using the
    same formula VERITy uses, so the mock matches what the contract expects.
    """
    overall = (
        functional * weight_functional
        + quality * weight_quality
        + security * weight_security
        + completeness * weight_completeness
    ) // 100
    if overall >= pass_threshold:
        verdict = "PASS"
    elif overall >= partial_threshold:
        verdict = "PARTIAL"
    else:
        verdict = "FAIL"
    return {
        "functional": functional,
        "quality": quality,
        "security": security,
        "completeness": completeness,
        "overall": overall,
        "verdict": verdict,
    }
