#!/usr/bin/env python3
"""hub's internal step. The switch skill runs it to show and change hub's parts. It is not a user command."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from hub import cli  # noqa: E402

sys.exit(cli.main())
