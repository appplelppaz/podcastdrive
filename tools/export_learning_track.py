#!/usr/bin/env python3
"""tools/listening/export.py の入口。使い方: python3 tools/export_learning_track.py --help"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from listening.export import main  # noqa: E402

if __name__ == "__main__":
    main()
