"""Generate the HTTP Basic Auth header for the MCP SSE endpoint.

By default reads MCP_AUTH_USER / MCP_AUTH_PASSWORD from the project `.env`
and prints both the base64 token and the full `Authorization` header value
so you can paste either into the MCP Inspector, Claude Desktop config,
or a curl command.

Usage:
  python scripts/gen_basic_auth.py
  python scripts/gen_basic_auth.py --user mcpadmin --password secret
  python scripts/gen_basic_auth.py mcpadmin secret
"""
from __future__ import annotations

import argparse
import base64
import getpass
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("user", nargs="?", help="Username (defaults to MCP_AUTH_USER from .env)")
    parser.add_argument("password", nargs="?", help="Password (defaults to MCP_AUTH_PASSWORD from .env)")
    parser.add_argument("--user", dest="user_opt", help="Same as positional `user`")
    parser.add_argument("--password", dest="password_opt", help="Same as positional `password`")
    args = parser.parse_args()

    load_dotenv(dotenv_path=ENV_PATH)

    user = args.user_opt or args.user or os.getenv("MCP_AUTH_USER", "")
    password = args.password_opt or args.password or os.getenv("MCP_AUTH_PASSWORD", "")

    if not user:
        user = input("MCP_AUTH_USER: ").strip()
    if not password:
        password = getpass.getpass("MCP_AUTH_PASSWORD: ")

    if not user or not password:
        print("ERROR: both user and password are required.", file=sys.stderr)
        return 1

    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    header = f"Basic {token}"

    print(f"User:                {user}")
    print(f"Base64 token:        {token}")
    print(f"Authorization head:  {header}")
    print()
    print("--- ready-to-paste snippets ---")
    print()
    print("# curl:")
    print(f'curl -H "Authorization: {header}" http://localhost:8000/sse')
    print()
    print("# Claude Desktop / claude_desktop_config.json (mcpServers entry):")
    print('{')
    print('  "url": "http://localhost:8000/sse",')
    print(f'  "headers": {{ "Authorization": "{header}" }}')
    print('}')
    return 0


if __name__ == "__main__":
    sys.exit(main())
