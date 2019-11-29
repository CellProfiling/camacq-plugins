#!/usr/bin/env python3
"""Start camacq to debug in vscode."""
from camacq.__main__ import main


if __name__ == "__main__":
    main(args=["--log-level", "debug"])
