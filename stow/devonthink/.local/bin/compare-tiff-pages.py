#!/usr/bin/env python3
"""Compare two TIFF files by OCR text similarity.

Extracts the first page of each TIFF via ImageMagick, runs Tesseract OCR,
and computes a text containment score using difflib. If both texts are too
short for meaningful comparison (<15 chars), prints USE_RMSE to signal
the caller should fall back to image-level comparison.

Usage: compare-tiff-pages.py <existing.tiff> <new.tiff>
Prints: a float (0.0-1.0 similarity) or "USE_RMSE"
"""

import difflib
import os
import subprocess
import sys
import tempfile


def get_text(path):
    tmp = ""
    try:
        tmp_file = tempfile.NamedTemporaryFile(suffix=".tiff", delete=False)
        tmp = tmp_file.name
        tmp_file.close()
        subprocess.run(
            ["/opt/homebrew/bin/magick", path + "[0]", tmp],
            stderr=subprocess.DEVNULL,
            check=True,
        )
        out = subprocess.check_output(
            ["/opt/homebrew/bin/tesseract", tmp, "stdout", "-l", "eng", "--psm", "3"],
            stderr=subprocess.DEVNULL,
        )
        return out.decode("utf-8").strip()
    except Exception:
        return ""
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except Exception:
                pass


def main():
    t1 = get_text(sys.argv[1])
    t2 = get_text(sys.argv[2])

    if len(t1) < 15 and len(t2) < 15:
        print("USE_RMSE")
    else:
        shorter = min(len(t1), len(t2))
        if shorter == 0:
            print(0)
        else:
            m = difflib.SequenceMatcher(None, t1, t2, autojunk=False)
            containment = (
                sum(b.size for b in m.get_matching_blocks() if b.size >= 3) / shorter
            )
            print(containment)


if __name__ == "__main__":
    main()
