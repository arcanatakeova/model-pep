#!/usr/bin/env python3
"""Extract API endpoint definitions from source code.

Scans source files for common web framework route patterns and outputs
a structured summary of discovered endpoints.

Usage:
    python extract-endpoints.py --source <directory>
"""

import argparse
import os
import re
import json

ROUTE_PATTERNS = {
    "express": [
        re.compile(r"""(?:app|router)\.(get|post|put|patch|delete)\s*\(\s*['"]([^'"]+)['"]""", re.IGNORECASE),
    ],
    "fastapi": [
        re.compile(r"""@(?:app|router)\.(get|post|put|patch|delete)\s*\(\s*['"]([^'"]+)['"]""", re.IGNORECASE),
    ],
    "flask": [
        re.compile(r"""@(?:app|blueprint|bp)\s*\.route\s*\(\s*['"]([^'"]+)['"]\s*(?:,\s*methods\s*=\s*\[([^\]]+)\])?""", re.IGNORECASE),
    ],
    "django": [
        re.compile(r"""path\s*\(\s*['"]([^'"]+)['"]"""),
    ],
}

FILE_EXTENSIONS = {".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".rb"}


def scan_file(filepath):
    """Scan a single file for route definitions."""
    endpoints = []
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except (OSError, IOError):
        return endpoints

    for framework, patterns in ROUTE_PATTERNS.items():
        for pattern in patterns:
            for match in pattern.finditer(content):
                groups = match.groups()
                if framework == "flask":
                    path = groups[0]
                    methods = [m.strip().strip("'\"") for m in groups[1].split(",")] if groups[1] else ["GET"]
                    for method in methods:
                        endpoints.append({
                            "method": method.upper(),
                            "path": path,
                            "file": filepath,
                            "framework": framework,
                            "line": content[:match.start()].count("\n") + 1,
                        })
                elif framework == "django":
                    endpoints.append({
                        "method": "VIEW",
                        "path": groups[0],
                        "file": filepath,
                        "framework": framework,
                        "line": content[:match.start()].count("\n") + 1,
                    })
                else:
                    endpoints.append({
                        "method": groups[0].upper(),
                        "path": groups[1],
                        "file": filepath,
                        "framework": framework,
                        "line": content[:match.start()].count("\n") + 1,
                    })
    return endpoints


def scan_directory(source_dir):
    """Recursively scan a directory for route definitions."""
    all_endpoints = []
    for root, _dirs, files in os.walk(source_dir):
        for filename in files:
            ext = os.path.splitext(filename)[1]
            if ext in FILE_EXTENSIONS:
                filepath = os.path.join(root, filename)
                all_endpoints.extend(scan_file(filepath))
    return all_endpoints


def main():
    parser = argparse.ArgumentParser(description="Extract API endpoints from source code")
    parser.add_argument("--source", required=True, help="Source directory to scan")
    parser.add_argument("--format", choices=["json", "table"], default="table", help="Output format")
    args = parser.parse_args()

    if not os.path.isdir(args.source):
        print(f"Error: {args.source} is not a directory")
        return

    endpoints = scan_directory(args.source)

    if not endpoints:
        print("No endpoints found.")
        return

    if args.format == "json":
        print(json.dumps(endpoints, indent=2))
    else:
        print(f"{'Method':<8} {'Path':<40} {'Framework':<10} {'File':<40} {'Line':<6}")
        print("-" * 104)
        for ep in sorted(endpoints, key=lambda e: (e["path"], e["method"])):
            print(f"{ep['method']:<8} {ep['path']:<40} {ep['framework']:<10} {ep['file']:<40} {ep['line']:<6}")

    print(f"\nTotal: {len(endpoints)} endpoint(s) found")


if __name__ == "__main__":
    main()
