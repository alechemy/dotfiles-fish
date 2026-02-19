#!/usr/bin/env python
# Requires: pip install mutagen

import argparse
import sys
import os
from mutagen.mp4 import MP4


def process_file(filepath, genre, is_compilation=False):
    """Attempts to set the genre, compilation flag, and clear comment & copyright tags for a single M4A file."""
    if not filepath.lower().endswith(".m4a"):
        return  # Silently skip non-m4a files

    try:
        audio = MP4(filepath)

        actions_taken = []

        # Set the genre
        audio["\xa9gen"] = genre
        actions_taken.append("set genre")

        # Set compilation flag if requested
        if is_compilation:
            audio["cpil"] = [True]
            actions_taken.append("set compilation")

        # Clear unwanted tags
        if "\xa9cmt" in audio:
            del audio["\xa9cmt"]
            actions_taken.append("cleared comment")

        if "cprt" in audio:
            del audio["cprt"]
            actions_taken.append("cleared copyright")

        audio.save()

        # Report the actions performed on the file
        action_summary = " & ".join(actions_taken)
        print(f"  -> Updated ({action_summary}): {filepath}")

    except Exception as e:
        print(f"  -> ERROR: Could not process {filepath}: {e}", file=sys.stderr)


# --- Argument Parser Setup ---
parser = argparse.ArgumentParser(description="A simple M4A genre and tag editor.")
parser.add_argument("--genre", required=True, help="The genre to set for the files.")
parser.add_argument(
    "--compilation",
    action="store_true",
    help="Mark the files as part of a compilation album.",
)
parser.add_argument(
    "paths", nargs="+", help="A list of .m4a files or directories to process."
)

args = parser.parse_args()

compilation_text = " and marking as compilation" if args.compilation else ""
print(f"Setting genre to: '{args.genre}'{compilation_text} and adjusting tags...")

for path in args.paths:
    if os.path.isdir(path):
        print(f"Processing directory (recursively): {path}")
        # Use os.walk to recursively traverse all subdirectories
        for root, dirs, files in os.walk(path):
            for filename in files:
                filepath = os.path.join(root, filename)
                if os.path.isfile(filepath):
                    process_file(filepath, args.genre, args.compilation)
    elif os.path.isfile(path):
        process_file(path, args.genre, args.compilation)
    else:
        print(f"  -> WARNING: Path not found, skipping: {path}", file=sys.stderr)

print("Done.")
