#!/usr/bin/python3
"""Compress base64-embedded images in a SingleFile HTML file.

Finds base64 data URIs, decodes each image, recompresses as JPEG via
macOS sips, and replaces the original if the result is smaller. Images
under 10KB and SVGs are skipped. The file is modified in place.

Usage: compress-singlefile-images.py <html-file>
"""

import base64
import os
import re
import subprocess
import sys
import tempfile
import traceback


image_failures = []


def process_image(match):
    original = match.group(0)
    mime_type = match.group(1)
    b64_data = match.group(2)

    if len(b64_data) < 10000 or "svg" in mime_type:
        return original

    temp_path = None
    out_path = None
    try:
        img_data = base64.b64decode(b64_data)
        fd, temp_path = tempfile.mkstemp(suffix=".img")
        out_path = temp_path + ".jpeg"
        with os.fdopen(fd, "wb") as f:
            f.write(img_data)
        subprocess.run(
            [
                "sips",
                "-s", "format", "jpeg",
                "-s", "formatOptions", "60",
                "-Z", "1024",
                temp_path,
                "--out", out_path,
            ],
            capture_output=True,
            check=True,
            timeout=10,
        )
        with open(out_path, "rb") as f:
            new_img_data = f.read()
        new_b64 = base64.b64encode(new_img_data).decode("utf-8")
        new_str = "data:image/jpeg;base64," + new_b64
        if len(new_str) < len(original):
            return new_str
        return original
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or b"").decode("utf-8", "replace").strip()
        image_failures.append(
            f"sips failed for {mime_type} image ({len(b64_data)} b64 chars): {stderr or e}"
        )
        return original
    except subprocess.TimeoutExpired:
        image_failures.append(
            f"sips timed out for {mime_type} image ({len(b64_data)} b64 chars)"
        )
        return original
    except Exception as e:
        image_failures.append(
            f"unexpected error compressing {mime_type} image "
            f"({len(b64_data)} b64 chars): {e.__class__.__name__}: {e}"
        )
        return original
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
        if out_path and os.path.exists(out_path):
            os.remove(out_path)


def main():
    html_file = sys.argv[1]
    try:
        with open(html_file, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        sys.stderr.write(f"compress-singlefile-images: failed to read {html_file}\n")
        sys.stderr.write(traceback.format_exc())
        sys.exit(1)

    pattern = re.compile(r"data:image/([^;]+);base64,([A-Za-z0-9+/=]+)")
    new_content = pattern.sub(process_image, content)

    try:
        with open(html_file, "w", encoding="utf-8") as f:
            f.write(new_content)
    except Exception:
        sys.stderr.write(f"compress-singlefile-images: failed to write {html_file}\n")
        sys.stderr.write(traceback.format_exc())
        sys.exit(1)

    if image_failures:
        sys.stderr.write(
            f"compress-singlefile-images: {len(image_failures)} image(s) "
            f"failed to compress in {html_file}\n"
        )
        for msg in image_failures:
            sys.stderr.write(f"  - {msg}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
