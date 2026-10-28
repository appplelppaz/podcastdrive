#!/usr/bin/env python3
"""tools/listening/convert.py の入口。使い方: python3 tools/build_listening.py --help"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from listening.convert import main  # noqa: E402

if __name__ == "__main__":
    main()
