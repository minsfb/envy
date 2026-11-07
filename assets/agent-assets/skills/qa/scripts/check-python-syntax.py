#!/usr/bin/env python3
"""Compile Python source in memory; never execute it or echo its contents."""

import argparse
import json
import sys
import warnings
from pathlib import Path


def check(path):
    result = {"file": str(path), "status": "pass"}
    try:
        source = path.read_bytes()
        # SyntaxWarning can include a source line. Syntax-only diagnostics omit
        # warnings as well as SyntaxError.text/msg and arbitrary exception text.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            compile(source, str(path), "exec", dont_inherit=True)
    except SyntaxError as error:
        result.update(status="fail", category=type(error).__name__,
                      line=error.lineno, column=error.offset)
    except OSError as error:
        result.update(status="fail", category="FileError", errno=error.errno)
    except (ValueError, UnicodeError):
        result.update(status="fail", category="SourceError")
    except Exception:
        # A runtime/compiler failure must not leak an arbitrary exception body
        # or be mistaken for valid syntax. No catch of KeyboardInterrupt here.
        result.update(status="fail", category="CheckerError")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()
    failed = False
    try:
        for path in args.files:
            result = check(path)
            print(json.dumps(result), flush=True)
            failed |= result["status"] != "pass"
    except KeyboardInterrupt:
        print(json.dumps({"status": "interrupted"}), flush=True)
        return 130
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
