"""Per-client bearer token management for HTTP transports.

Provides the ``librenms-mcp-tokens`` CLI to generate, list, show and revoke
bearer tokens stored in a JSON file, plus a FastMCP token verifier that
re-reads that file on every request so generating or revoking a token takes
effect without restarting the server.
"""

import argparse
import json
import os
import secrets
import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

from fastmcp.server.auth import AccessToken
from fastmcp.server.auth import TokenVerifier

TOKEN_PREFIX = "lnms_"  # noqa: S105 - token name prefix, not a secret
DEFAULT_TOKENS_FILE = ".tokens"
TOKEN_SCOPES = ["read", "write"]


def default_tokens_file() -> str:
    """Return the tokens file path from MCP_HTTP_TOKENS_FILE, or the default."""
    env_path = os.getenv("MCP_HTTP_TOKENS_FILE")
    if env_path is not None:
        env_path = env_path.strip()
    return env_path or DEFAULT_TOKENS_FILE


def generate_token() -> str:
    """Generate a secure bearer token with the lnms_ prefix."""
    return f"{TOKEN_PREFIX}{secrets.token_urlsafe(24)}"


def load_tokens(path: str) -> dict[str, Any]:
    """Load tokens from file, returning an empty dict if missing or invalid."""
    try:
        with Path(path).open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_tokens(path: str, tokens: dict[str, Any]) -> None:
    """Save tokens to file, readable only by the owner."""
    file = Path(path)
    file.write_text(json.dumps(tokens, indent=2) + "\n", encoding="utf-8")
    file.chmod(0o600)


class FileTokenVerifier(TokenVerifier):
    """Verify bearer tokens against a JSON tokens file.

    The file is re-read on every request, so tokens added or revoked with the
    ``librenms-mcp-tokens`` CLI take effect immediately. The token ID becomes
    the authenticated client_id, identifying each client in logs. An optional
    static token (MCP_HTTP_BEARER_TOKEN) is still accepted as a fallback for
    backward compatibility.
    """

    def __init__(self, tokens_file: str, static_token: str | None = None):
        super().__init__()
        self.tokens_file = tokens_file
        self.static_token = static_token

    async def verify_token(self, token: str) -> AccessToken | None:
        for token_id, token_data in load_tokens(self.tokens_file).items():
            stored = token_data.get("token") if isinstance(token_data, dict) else None
            if isinstance(stored, str) and secrets.compare_digest(stored, token):
                return AccessToken(
                    token=token,
                    client_id=token_id,
                    scopes=TOKEN_SCOPES,
                )
        if self.static_token and secrets.compare_digest(self.static_token, token):
            return AccessToken(
                token=token,
                client_id="authenticated-client",
                scopes=TOKEN_SCOPES,
            )
        return None


def generate_token_command(path: str, token_id: str, description: str | None) -> None:
    """Generate a new bearer token and store it in the tokens file."""
    tokens = load_tokens(path)

    if token_id in tokens:
        print(f"Error: Token ID '{token_id}' already exists")
        sys.exit(1)

    token = generate_token()
    tokens[token_id] = {
        "token": token,
        "description": description or f"Token for {token_id}",
        "created": datetime.now(UTC).isoformat(),
    }
    save_tokens(path, tokens)

    print("Generated new token:")
    print(f"  ID: {token_id}")
    print(f"  Token: {token}")
    print(f"  Description: {tokens[token_id]['description']}")
    print(f"\nClients authenticate with: Authorization: Bearer {token}")


def list_tokens_command(path: str) -> None:
    """List all tokens without showing the token values."""
    tokens = load_tokens(path)

    if not tokens:
        print("No tokens found")
        return

    print(f"{'ID':<20} {'Description':<40} {'Created':<25}")
    print("-" * 85)
    for token_id, token_data in tokens.items():
        description = token_data.get("description", "No description")
        created = token_data.get("created", "Unknown")
        print(f"{token_id:<20} {description:<40} {created:<25}")


def show_token_command(path: str, token_id: str) -> None:
    """Show the actual token value (for recovery purposes)."""
    tokens = load_tokens(path)

    if token_id not in tokens:
        print(f"Error: Token ID '{token_id}' not found")
        sys.exit(1)

    token_data = tokens[token_id]
    print(f"Token ID: {token_id}")
    print(f"Token: {token_data['token']}")
    print(f"Description: {token_data.get('description', 'No description')}")
    print(f"Created: {token_data.get('created', 'Unknown')}")


def revoke_token_command(path: str, token_id: str) -> None:
    """Revoke (delete) a token."""
    tokens = load_tokens(path)

    if token_id not in tokens:
        print(f"Error: Token ID '{token_id}' not found")
        sys.exit(1)

    del tokens[token_id]
    save_tokens(path, tokens)
    print(f"Token '{token_id}' has been revoked")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="LibreNMS MCP Server token manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s generate --id "workstation-alice" --description "Alice's laptop"
  %(prog)s list
  %(prog)s show --id "workstation-alice"
  %(prog)s revoke --id "workstation-alice"
        """,
    )
    parser.add_argument(
        "--file",
        default=None,
        help=(
            "Tokens file path (default: MCP_HTTP_TOKENS_FILE environment "
            f"variable, or {DEFAULT_TOKENS_FILE})"
        ),
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    generate_parser = subparsers.add_parser("generate", help="Generate a new token")
    generate_parser.add_argument(
        "--id", required=True, help="Unique identifier for the token"
    )
    generate_parser.add_argument("--description", help="Description of the token usage")

    subparsers.add_parser("list", help="List all tokens (without token values)")

    show_parser = subparsers.add_parser("show", help="Show the actual token value")
    show_parser.add_argument("--id", required=True, help="Token ID to show")

    revoke_parser = subparsers.add_parser("revoke", help="Revoke (delete) a token")
    revoke_parser.add_argument("--id", required=True, help="Token ID to revoke")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    path = args.file or default_tokens_file()

    if args.command == "generate":
        generate_token_command(path, args.id, args.description)
    elif args.command == "list":
        list_tokens_command(path)
    elif args.command == "show":
        show_token_command(path, args.id)
    elif args.command == "revoke":
        revoke_token_command(path, args.id)


if __name__ == "__main__":
    main()
