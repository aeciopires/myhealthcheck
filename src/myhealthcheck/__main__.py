"""Allow ``python -m myhealthcheck``."""

import sys

from myhealthcheck.cli import main

if __name__ == "__main__":
    sys.exit(main())
