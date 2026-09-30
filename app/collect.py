"""Run collectors once on a scheduled GitHub Actions runner."""
import asyncio
import json
import os
import sys


def validate_environment():
    required = ("GOOGLE_SHEETS_ID", "GOOGLE_SERVICE_ACCOUNT_JSON",
                "TOTALBIDDATA_USERNAME", "TOTALBIDDATA_PASSWORD",
                "MYVENDORLINK_EMAIL", "MYVENDORLINK_PASSWORD")
    missing = [key for key in required if not os.getenv(key, "").strip()]
    if missing:
        raise ValueError("Missing GitHub Actions secrets: " + ", ".join(missing))
    try:
        account = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    except (ValueError, TypeError):
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON must contain valid JSON") from None
    if not isinstance(account, dict) or not account.get("client_email") or not account.get("private_key"):
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON must contain the existing service-account key")


async def main():
    validate_environment()
    from .main import run_full_scrape
    summary = await run_full_scrape()
    # Only operational counters/status, never authenticated HTML or contact data.
    failed = [name for name, result in summary.items()
              if isinstance(result, dict) and (result.get("error") or
                 result.get("status") == "not_configured" or result.get("detail_errors", 0))]
    if failed:
        print("Collectors requiring attention: " + ", ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
