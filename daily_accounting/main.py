"""Daily Accounting — entrypoint."""
import logging
import sys

from dispatch_v2.common import ENABLE_DAILY_ACCOUNTING

log = logging.getLogger(__name__)


def main() -> int:
    if not ENABLE_DAILY_ACCOUNTING:
        log.info("ENABLE_DAILY_ACCOUNTING=False, skipping run")
        return 0

    log.warning("Main logic not yet implemented (Step 2+)")
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    sys.exit(main())
