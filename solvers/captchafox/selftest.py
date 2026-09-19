"""Selftest: verify CaptchaFox encryption round-trip and solver modes.

Run: python -m solvers.captchafox.selftest
"""
import asyncio
import base64
import json
import sys
from pathlib import Path

# Ensure parent is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from solvers.captchafox.solve import encrypt_data, decrypt_data, solve_captchafox


def test_encryption_roundtrip():
    """Test encrypt → decrypt preserves data."""
    test_cases = [
        {"offset": 142, "ts": 1234567890},
        {"nested": {"a": [1, 2, 3]}, "bool": True, "none": None},
        {"large": "x" * 10000},  # Test compression
        {},  # Empty dict
        {"unicode": "日本語テスト"},  # Unicode
    ]
    
    for i, original in enumerate(test_cases):
        encrypted = encrypt_data(original)
        
        # Verify header
        assert encrypted[0] == 0x01, f"Case {i}: wrong header byte 0: {encrypted[0]}"
        assert encrypted[1] == 0x04, f"Case {i}: wrong header byte 1: {encrypted[1]}"
        
        # Verify size (compressed should be smaller than raw JSON for large payloads)
        raw_json = json.dumps(original).encode()
        if len(raw_json) > 100:
            assert len(encrypted) < len(raw_json) + 100, \
                f"Case {i}: encrypted ({len(encrypted)}) not smaller than raw ({len(raw_json)})"
        
        # Decrypt and verify
        decrypted = decrypt_data(encrypted)
        assert decrypted == original, f"Case {i}: round-trip failed\n  orig:    {original}\n  decrypt: {decrypted}"
    
    print("[PASS] Encryption round-trip: 5/5 cases")


def test_solver_modes():
    """Test async solver entry points."""
    
    # Mode: encrypt JSON payload
    result = asyncio.run(solve_captchafox(
        image_b64=json.dumps({"offset": 42, "answer": "yes"})
    ))
    assert result["solved"] is True
    assert result["method"] == "encrypt"
    assert result["error"] is None
    assert len(result["token"]) > 0
    
    # Decrypt the token
    encrypted_bytes = base64.b64decode(result["token"])
    decrypted = decrypt_data(encrypted_bytes)
    assert decrypted["offset"] == 42
    assert decrypted["answer"] == "yes"
    
    # Mode: decrypt base64 string
    result2 = asyncio.run(solve_captchafox(encrypted_data=result["token"]))
    assert result2["solved"] is True
    assert result2["method"] == "decrypt"
    assert result2["decrypted"]["offset"] == 42
    
    print("[PASS] Solver encrypt/decrypt modes")


def test_error_handling():
    """Test error cases return uniform dict, never raise."""
    
    # No args
    result = asyncio.run(solve_captchafox())
    assert result["solved"] is False
    assert "image_b64" in result["error"]
    
    # Missing piece
    result = asyncio.run(solve_captchafox(image_b64="dGVzdA=="))
    assert result["solved"] is False
    assert "piece_b64" in result["error"]
    
    # Invalid encrypted data
    result = asyncio.run(solve_captchafox(encrypted_data="not-base64!!!"))
    assert result["solved"] is False
    assert "captchafox" in result["error"]
    
    # Truncated encrypted data
    result = asyncio.run(solve_captchafox(encrypted_data=base64.b64encode(b"\x01\x04").decode()))
    assert result["solved"] is False
    
    print("[PASS] Error handling: 4/4 cases never raised")


def test_synthetic_fingerprint():
    """Test fingerprint generation."""
    from solvers.captchafox.solve import _make_synthetic_fingerprint
    
    fp = _make_synthetic_fingerprint("example.com")
    assert fp["host"] == "example.com"
    assert fp["type"] == "slide"
    assert "CF0115" in fp["cs"]
    assert "Mozilla" in fp["cs"]["CF0115"]
    
    fp2 = _make_synthetic_fingerprint()
    assert fp2["host"] == "unknown"
    
    print("[PASS] Synthetic fingerprint generation")


if __name__ == "__main__":
    test_encryption_roundtrip()
    test_synthetic_fingerprint()
    test_solver_modes()
    test_error_handling()
    print("\n=== ALL TESTS PASSED ===")
