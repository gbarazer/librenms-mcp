import json

import pytest

from librenms_mcp.token_manager import TOKEN_PREFIX
from librenms_mcp.token_manager import FileTokenVerifier
from librenms_mcp.token_manager import generate_token
from librenms_mcp.token_manager import generate_token_command
from librenms_mcp.token_manager import load_tokens
from librenms_mcp.token_manager import revoke_token_command
from librenms_mcp.token_manager import save_tokens


def test_generate_token_format():
    token = generate_token()
    assert token.startswith(TOKEN_PREFIX)
    assert len(token) > len(TOKEN_PREFIX) + 20
    assert token != generate_token()


def test_generate_and_revoke_roundtrip(tmp_path):
    path = str(tmp_path / "tokens.json")

    generate_token_command(path, "workstation-alice", "Alice's laptop")
    tokens = load_tokens(path)
    assert set(tokens) == {"workstation-alice"}
    assert tokens["workstation-alice"]["description"] == "Alice's laptop"
    assert tokens["workstation-alice"]["token"].startswith(TOKEN_PREFIX)

    generate_token_command(path, "workstation-bob", None)
    assert set(load_tokens(path)) == {"workstation-alice", "workstation-bob"}

    revoke_token_command(path, "workstation-alice")
    assert set(load_tokens(path)) == {"workstation-bob"}


def test_generate_duplicate_id_exits(tmp_path):
    path = str(tmp_path / "tokens.json")
    generate_token_command(path, "dup", None)
    with pytest.raises(SystemExit):
        generate_token_command(path, "dup", None)


def test_revoke_unknown_id_exits(tmp_path):
    path = str(tmp_path / "tokens.json")
    with pytest.raises(SystemExit):
        revoke_token_command(path, "missing")


def test_tokens_file_permissions(tmp_path):
    path = tmp_path / "tokens.json"
    save_tokens(str(path), {"id": {"token": "lnms_x"}})
    assert path.stat().st_mode & 0o777 == 0o600


def test_load_tokens_missing_or_invalid(tmp_path):
    assert load_tokens(str(tmp_path / "missing.json")) == {}

    bad = tmp_path / "bad.json"
    bad.write_text("not json")
    assert load_tokens(str(bad)) == {}

    not_dict = tmp_path / "list.json"
    not_dict.write_text(json.dumps(["a", "b"]))
    assert load_tokens(str(not_dict)) == {}


@pytest.mark.asyncio
async def test_file_token_verifier_accepts_valid_token(tmp_path):
    path = str(tmp_path / "tokens.json")
    save_tokens(path, {"workstation-alice": {"token": "lnms_valid"}})
    verifier = FileTokenVerifier(path)

    access = await verifier.verify_token("lnms_valid")
    assert access is not None
    assert access.client_id == "workstation-alice"
    assert set(access.scopes) == {"read", "write"}

    assert await verifier.verify_token("lnms_wrong") is None


@pytest.mark.asyncio
async def test_file_token_verifier_picks_up_changes_without_restart(tmp_path):
    path = str(tmp_path / "tokens.json")
    save_tokens(path, {})
    verifier = FileTokenVerifier(path)

    assert await verifier.verify_token("lnms_new") is None

    save_tokens(path, {"new-client": {"token": "lnms_new"}})
    access = await verifier.verify_token("lnms_new")
    assert access is not None
    assert access.client_id == "new-client"

    save_tokens(path, {})
    assert await verifier.verify_token("lnms_new") is None


@pytest.mark.asyncio
async def test_file_token_verifier_static_token_fallback(tmp_path):
    path = str(tmp_path / "tokens.json")
    save_tokens(path, {"workstation-alice": {"token": "lnms_valid"}})
    verifier = FileTokenVerifier(path, static_token="legacy-secret")

    access = await verifier.verify_token("legacy-secret")
    assert access is not None
    assert access.client_id == "authenticated-client"

    file_access = await verifier.verify_token("lnms_valid")
    assert file_access is not None
    assert file_access.client_id == "workstation-alice"


@pytest.mark.asyncio
async def test_file_token_verifier_missing_file_rejects(tmp_path):
    verifier = FileTokenVerifier(str(tmp_path / "missing.json"))
    assert await verifier.verify_token("lnms_anything") is None
