"""Fail if any named file contains a non-ASCII byte.

pip before 25.0 reads requirements files in the locale code page when they carry
no encoding marker, so a UTF-8 comment breaks `pip install -r` on Chinese
Windows (GBK). Usage: python scripts/check_ascii.py requirements*.txt
"""
import glob
import sys


def non_ascii(path):
    with open(path, "rb") as stream:
        for number, line in enumerate(stream, 1):
            if not line.isascii():
                yield number


def main(patterns):
    paths = sorted({p for pattern in patterns for p in (glob.glob(pattern) or [pattern])})
    if not paths:
        sys.exit("FAIL no files given")
    bad = 0
    for path in paths:
        for number in non_ascii(path):
            print("FAIL %s:%d has non-ASCII bytes" % (path, number))
            bad += 1
    if bad:
        sys.exit(1)
    print("PASS %d file(s) are ASCII: %s" % (len(paths), ", ".join(paths)))


if __name__ == "__main__":
    main(sys.argv[1:])
