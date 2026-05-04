"""Entry point: `python -m dispatch_v2.shift_notifications`."""
import sys

from dispatch_v2.shift_notifications.worker import main

if __name__ == "__main__":
    sys.exit(main())
