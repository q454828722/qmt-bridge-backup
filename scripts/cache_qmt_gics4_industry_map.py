#!/usr/bin/env python3
"""Backward-compatible wrapper for the renamed industry cache script."""

from cache_starbridge_gics4_industry_map import main


if __name__ == "__main__":
    raise SystemExit(main())
