"""Manual DhanHQ authentication smoke test.

Usage:
    PYTHONPATH=. python -m src.dhan_healthcheck

The script never prints the access token.
"""

from .dhan_api import DhanHQClient


def main() -> int:
    client = DhanHQClient()
    result = client.health()
    print(result)
    return 0 if result.get("authenticated") else 1


if __name__ == "__main__":
    raise SystemExit(main())
