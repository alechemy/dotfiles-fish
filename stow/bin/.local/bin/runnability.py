#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "essentia-tensorflow==2.1b6.dev1389",
#   "mutagen",
# ]
# ///
"""Per-track runnability scoring for the music library.

Analyzes audio once into a SQLite feature store, then scores tracks with a
pure function over stored features so weights can be re-tuned without
re-reading audio. Plan: ~/.dotfiles/.context/workout-runnability-plan.md

  analyze    extract features (BPM, beat confidence, danceability, energy)
             into ~/.local/state/runnability/features.db; skips rows whose
             path+size+mtime are already present
  score      rank tracks by runnability from stored features (no file writes)
  write      write RUNNABILITY / BPM_FOLDED / tmpo tags into files whose
             values changed, then refresh the store's size+mtime keys
  status     feature-store coverage summary

Weights and gates live in ~/.config/runnability/config.toml (stowed from
stow/runnability/). Models auto-download to ~/.local/share/runnability/models.

Analysis costs ~6 s/track single-core; batch runs gate on
should-run-background-job unless --force.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import sqlite3
import subprocess
import sys
import tomllib
import urllib.request
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

LIBRARY_ROOT = pathlib.Path("/Volumes/Media/Music")
DB_PATH = pathlib.Path("~/.local/state/runnability/features.db").expanduser()
CONFIG_PATH = pathlib.Path("~/.config/runnability/config.toml").expanduser()
MODEL_DIR = pathlib.Path("~/.local/share/runnability/models").expanduser()
GATE = pathlib.Path("~/.local/bin/should-run-background-job").expanduser()

MODELS = {
    "embedding": "https://essentia.upf.edu/models/feature-extractors/discogs-effnet/discogs-effnet-bs64-1.pb",
    "danceability": "https://essentia.upf.edu/models/classification-heads/danceability/danceability-discogs-effnet-1.pb",
    "mood_party": "https://essentia.upf.edu/models/classification-heads/mood_party/mood_party-discogs-effnet-1.pb",
    "mood_relaxed": "https://essentia.upf.edu/models/classification-heads/mood_relaxed/mood_relaxed-discogs-effnet-1.pb",
    "mood_aggressive": "https://essentia.upf.edu/models/classification-heads/mood_aggressive/mood_aggressive-discogs-effnet-1.pb",
}

AUDIO_EXTS = {".m4a", ".mp3", ".flac", ".ogg", ".opus"}


def ensure_models() -> dict[str, str]:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, url in MODELS.items():
        dest = MODEL_DIR / url.rsplit("/", 1)[1]
        if not dest.exists():
            print(f"downloading {url}", file=sys.stderr)
            tmp = dest.with_suffix(".part")
            urllib.request.urlretrieve(url, tmp)
            tmp.rename(dest)
        paths[name] = str(dest)
    return paths


def open_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS features (
            relpath TEXT PRIMARY KEY,
            size INTEGER NOT NULL,
            mtime REAL NOT NULL,
            analyzed_at TEXT NOT NULL,
            duration REAL,
            genre TEXT,
            bpm REAL,
            beat_confidence REAL,
            danceability REAL,
            mood_party REAL,
            mood_relaxed REAL,
            mood_aggressive REAL,
            mean_rms_db REAL,
            low_energy_ratio REAL,
            intro_rms_ratio REAL,
            error TEXT
        )
    """)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(features)")}
    for col in ("mood_party", "mood_relaxed", "mood_aggressive"):
        if col not in cols:
            conn.execute(f"ALTER TABLE features ADD COLUMN {col} REAL")
    return conn


_worker_models: dict | None = None


def _worker_init(model_paths: dict[str, str]) -> None:
    global _worker_models
    os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
    import essentia

    essentia.log.infoActive = False
    essentia.log.warningActive = False
    from essentia.standard import (
        MonoLoader,
        RhythmExtractor2013,
        TensorflowPredict2D,
        TensorflowPredictEffnetDiscogs,
    )

    _worker_models = {
        "MonoLoader": MonoLoader,
        "RhythmExtractor2013": RhythmExtractor2013,
        "embedding": TensorflowPredictEffnetDiscogs(
            graphFilename=model_paths["embedding"], output="PartitionedCall:1"
        ),
        "danceability": TensorflowPredict2D(
            graphFilename=model_paths["danceability"], output="model/Softmax"
        ),
        "mood_party": TensorflowPredict2D(
            graphFilename=model_paths["mood_party"], output="model/Softmax"
        ),
        "mood_relaxed": TensorflowPredict2D(
            graphFilename=model_paths["mood_relaxed"], output="model/Softmax"
        ),
        "mood_aggressive": TensorflowPredict2D(
            graphFilename=model_paths["mood_aggressive"], output="model/Softmax"
        ),
    }


def _analyze_one(abspath: str, relpath: str) -> dict:
    import numpy as np
    from mutagen.mp4 import MP4

    m = _worker_models
    row: dict = {"relpath": relpath, "error": None}
    st = os.stat(abspath)
    row["size"], row["mtime"] = st.st_size, st.st_mtime
    try:
        try:
            tags = MP4(abspath)
            row["duration"] = tags.info.length
            row["genre"] = (tags.get("\xa9gen") or [None])[0]
        except Exception:
            import mutagen

            f = mutagen.File(abspath, easy=True)
            row["duration"] = f.info.length if f else None
            row["genre"] = (f.get("genre") or [None])[0] if f else None

        audio44 = m["MonoLoader"](filename=abspath, sampleRate=44100)()
        try:
            # fresh instance per track: a failed streaming run corrupts the
            # instance and poisons every later track in the worker
            bpm, _, conf, _, _ = m["RhythmExtractor2013"](method="multifeature")(audio44)
            row["bpm"], row["beat_confidence"] = float(bpm), float(conf)
        except Exception:
            # BPM is optional (cadence weight 0); mood/danceability are not
            row["bpm"] = row["beat_confidence"] = None

        sr, win = 44100, 44100
        n = len(audio44) // win
        if n > 0:
            frames = audio44[: n * win].reshape(n, win)
            rms = np.sqrt((frames**2).mean(axis=1)) + 1e-10
            mean_rms = float(rms.mean())
            row["mean_rms_db"] = 20 * math.log10(mean_rms)
            row["low_energy_ratio"] = float((rms < 0.5 * np.median(rms)).mean())
            intro = rms[: min(30, n)].mean()
            row["intro_rms_ratio"] = float(intro / mean_rms)

        audio16 = m["MonoLoader"](filename=abspath, sampleRate=16000)()
        emb = m["embedding"](audio16)
        probs = m["danceability"](emb)
        row["danceability"] = float(probs.mean(axis=0)[0])
        # class index 1 = positive for party/relaxed, 0 for aggressive
        row["mood_party"] = float(m["mood_party"](emb).mean(axis=0)[1])
        row["mood_relaxed"] = float(m["mood_relaxed"](emb).mean(axis=0)[1])
        row["mood_aggressive"] = float(m["mood_aggressive"](emb).mean(axis=0)[0])
    except Exception as e:
        row["error"] = f"{type(e).__name__}: {e}"
    return row


def collect_paths(args) -> list[pathlib.Path]:
    if args.paths:
        out = []
        for p in args.paths:
            p = pathlib.Path(p).expanduser()
            if p.is_dir():
                out.extend(
                    q for q in sorted(p.rglob("*"))
                    if q.suffix.lower() in AUDIO_EXTS and not q.name.startswith("._")
                )
            else:
                out.append(p)
        return out
    return [
        q for q in sorted(LIBRARY_ROOT.rglob("*"))
        if q.suffix.lower() in AUDIO_EXTS and not q.name.startswith("._")
        and "_runnability-test" not in q.parts
    ]


def cmd_analyze(args) -> int:
    if not args.force and GATE.exists():
        if subprocess.run([str(GATE)]).returncode != 0:
            print("on battery; skipping (use --force to override)", file=sys.stderr)
            return 0
    paths = collect_paths(args)
    conn = open_db()
    known = {
        r[0]: (r[1], r[2])
        for r in conn.execute("SELECT relpath, size, mtime FROM features WHERE error IS NULL")
    }
    todo = []
    for p in paths:
        try:
            rel = str(p.resolve().relative_to(LIBRARY_ROOT))
        except ValueError:
            rel = str(p)
        st = p.stat()
        if not args.force and known.get(rel) == (st.st_size, st.st_mtime):
            continue
        todo.append((str(p), rel))
    print(f"{len(paths)} candidates, {len(todo)} to analyze", file=sys.stderr)
    if not todo:
        return 0

    model_paths = ensure_models()
    done = 0
    with ProcessPoolExecutor(
        max_workers=args.workers, initializer=_worker_init, initargs=(model_paths,)
    ) as pool:
        futs = {pool.submit(_analyze_one, a, r): r for a, r in todo}
        for fut in as_completed(futs):
            row = fut.result()
            row["analyzed_at"] = datetime.now(timezone.utc).isoformat()
            cols = ",".join(row)
            conn.execute(
                f"INSERT OR REPLACE INTO features ({cols}) VALUES ({','.join(':'+c for c in row)})",
                row,
            )
            conn.commit()
            done += 1
            tag = f"ERROR {row['error']}" if row["error"] else (
                f"bpm={row.get('bpm', 0):.1f} conf={row.get('beat_confidence', 0):.2f} "
                f"dance={row.get('danceability', 0):.2f}"
            )
            print(f"[{done}/{len(todo)}] {row['relpath']}: {tag}", file=sys.stderr)
    return 0


def load_config() -> dict:
    with open(CONFIG_PATH, "rb") as f:
        return tomllib.load(f)


def fold_bpm(bpm: float, target: float) -> float:
    if bpm <= 0:
        return 0.0
    lo, hi = target / math.sqrt(2), target * math.sqrt(2)
    while bpm < lo:
        bpm *= 2
    while bpm > hi:
        bpm /= 2
    return bpm


def _ramp(x: float, x0: float, y0: float, x1: float, y1: float) -> float:
    if x <= x0:
        return y0
    if x >= x1:
        return y1
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def score_row(r: dict, cfg: dict) -> tuple[int, dict]:
    gates = cfg["gates"]
    genre = (r["genre"] or "").strip().lower()
    if genre not in {g.lower() for g in gates["genres_allow"]}:
        return 0, {"gate": f"genre:{r['genre']}"}
    dur = r["duration"] or 0
    if not gates["duration_min"] <= dur <= gates["duration_max"]:
        return 0, {"gate": f"duration:{dur:.0f}s"}
    if r["danceability"] is None:
        return 0, {"gate": "missing-features"}

    bpm = r["bpm"] or 0.0
    t = cfg["cadence"]
    folded = fold_bpm(bpm, t["target_spm"])
    cadence = math.exp(-0.5 * ((folded - t["target_spm"]) / t["sigma_spm"]) ** 2)

    if bpm < 123:
        arousal = _ramp(bpm, 100, 0.0, 123, 1.0)
    elif bpm <= 140:
        arousal = 1.0
    elif bpm <= 160:
        arousal = _ramp(bpm, 145, 1.0, 160, 0.4)
    else:
        arousal = 0.3

    conf = r["beat_confidence"] or 0.0
    pulse = _ramp(conf, 0.5, 0.0, 3.5, 1.0)

    energy = _ramp(r["mean_rms_db"] or -60, -30.0, 0.0, -10.0, 1.0)

    party = r.get("mood_party") or 0.0
    nonrelaxed = 1.0 - (r.get("mood_relaxed") if r.get("mood_relaxed") is not None else 1.0)

    continuity = 1.0 - (r["low_energy_ratio"] or 0.0)
    if (r["intro_rms_ratio"] or 1.0) < 0.35:
        continuity *= 0.85

    w = cfg["weights"]
    base = (
        w["cadence"] * cadence
        + w["arousal"] * arousal
        + w["pulse"] * pulse
        + w["energy"] * energy
        + w["danceability"] * r["danceability"]
        + w["mood_party"] * party
        + w["mood_nonrelaxed"] * nonrelaxed
    ) / sum(w.values())
    final = base * continuity
    parts = {
        "folded_bpm": round(folded, 1),
        "cadence": round(cadence, 2),
        "arousal": round(arousal, 2),
        "pulse": round(pulse, 2),
        "energy": round(energy, 2),
        "dance": round(r["danceability"], 2),
        "party": round(party, 2),
        "nonrel": round(nonrelaxed, 2),
        "continuity": round(continuity, 2),
    }
    return round(100 * final), parts


def cmd_score(args) -> int:
    cfg = load_config()
    conn = open_db()
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM features WHERE error IS NULL").fetchall()
    scored = []
    for r in rows:
        s, parts = score_row(dict(r), cfg)
        scored.append({"relpath": r["relpath"], "score": s, "bpm": r["bpm"], **parts})
    scored.sort(key=lambda x: -x["score"])
    if args.limit:
        scored = scored[: args.limit]
    if args.json:
        json.dump(scored, sys.stdout, indent=1)
        print()
        return 0
    for s in scored:
        detail = s.get("gate") or (
            f"bpm={(s['bpm'] or 0):.0f}→{s['folded_bpm']} cad={s['cadence']} aro={s['arousal']} "
            f"pul={s['pulse']} nrg={s['energy']} dan={s['dance']} pty={s['party']} "
            f"nrl={s['nonrel']} cont={s['continuity']}"
        )
        print(f"{s['score']:3d}  {s['relpath']}  [{detail}]")
    return 0


def _write_mp4(p: pathlib.Path, score: int, folded: int | None, tmpo: int | None, dry_run: bool):
    from mutagen.mp4 import MP4, MP4FreeForm

    key_r = "----:com.apple.iTunes:RUNNABILITY"
    key_b = "----:com.apple.iTunes:BPM_FOLDED"
    audio = MP4(str(p))
    if score == 0 and key_r not in audio:
        return None
    want = {key_r: str(score).encode()}
    if folded is not None:
        want[key_b] = str(folded).encode()
    changed = False
    for k, v in want.items():
        cur = bytes(audio[k][0]) if audio.get(k) else None
        if cur != v:
            audio[k] = [MP4FreeForm(v)]
            changed = True
    if tmpo is not None and list(audio.get("tmpo") or []) != [tmpo]:
        audio["tmpo"] = [tmpo]
        changed = True
    if changed and not dry_run:
        audio.save()
    return changed


def _write_mp3(p: pathlib.Path, score: int, folded: int | None, tmpo: int | None, dry_run: bool):
    from mutagen.id3 import ID3, TBPM, TXXX
    from mutagen.id3._util import ID3NoHeaderError

    try:
        tags = ID3(str(p))
    except ID3NoHeaderError:
        tags = ID3()
    if score == 0 and not tags.getall("TXXX:RUNNABILITY"):
        return None
    changed = False
    want = {"RUNNABILITY": str(score)}
    if folded is not None:
        want["BPM_FOLDED"] = str(folded)
    for desc, val in want.items():
        cur = tags.getall(f"TXXX:{desc}")
        if not cur or list(cur[0].text) != [val]:
            tags.setall(f"TXXX:{desc}", [TXXX(encoding=3, desc=desc, text=[val])])
            changed = True
    if tmpo is not None:
        cur = tags.getall("TBPM")
        if not cur or list(cur[0].text) != [str(tmpo)]:
            tags.setall("TBPM", [TBPM(encoding=3, text=[str(tmpo)])])
            changed = True
    if changed and not dry_run:
        tags.save(str(p))
    return changed


def _write_vorbis(p: pathlib.Path, score: int, folded: int | None, tmpo: int | None, dry_run: bool):
    import mutagen

    audio = mutagen.File(str(p))
    if audio is None:
        raise ValueError("unreadable file")
    if score == 0 and "RUNNABILITY" not in audio:
        return None
    want = {"RUNNABILITY": str(score)}
    if folded is not None:
        want["BPM_FOLDED"] = str(folded)
    if tmpo is not None:
        want["BPM"] = str(tmpo)
    changed = False
    for k, v in want.items():
        if list(audio.get(k) or []) != [v]:
            audio[k] = [v]
            changed = True
    if changed and not dry_run:
        audio.save()
    return changed


def _write_one(rel: str, score: int, folded: int | None, tmpo: int | None, dry_run: bool):
    p = LIBRARY_ROOT / rel
    try:
        if not p.exists():
            return rel, "missing", None, None, None
        ext = p.suffix.lower()
        writer = {".m4a": _write_mp4, ".mp3": _write_mp3}.get(ext, _write_vorbis)
        changed = writer(p, score, folded, tmpo, dry_run)
        if changed is None:
            return rel, "skipped-gated", None, None, None
        if not changed:
            return rel, "unchanged", None, None, None
        if dry_run:
            return rel, "would-write", None, None, None
        st = p.stat()
        return rel, "written", st.st_size, st.st_mtime, None
    except Exception as e:
        return rel, "error", None, None, f"{type(e).__name__}: {e}"


def cmd_write(args) -> int:
    if not args.dry_run and not args.force and GATE.exists():
        if subprocess.run([str(GATE)]).returncode != 0:
            print("on battery; skipping (use --force to override)", file=sys.stderr)
            return 0
    cfg = load_config()
    quant = int(cfg.get("output", {}).get("quantize", 1))
    target = cfg["cadence"]["target_spm"]
    conn = open_db()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM features WHERE error IS NULL ORDER BY relpath"
    ).fetchall()
    jobs = []
    for r in rows:
        if r["relpath"].startswith("_runnability-test"):
            continue
        score, _ = score_row(dict(r), cfg)
        if quant > 1:
            score = quant * round(score / quant)
        bpm = r["bpm"]
        tmpo = round(bpm) if bpm else None
        folded = round(fold_bpm(bpm, target)) if bpm else None
        jobs.append((r["relpath"], score, folded, tmpo))

    counts: dict[str, int] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = [pool.submit(_write_one, *j, args.dry_run) for j in jobs]
        for fut in as_completed(futs):
            rel, status, size, mtime, err = fut.result()
            counts[status] = counts.get(status, 0) + 1
            done += 1
            if err:
                print(f"ERROR {rel}: {err}", file=sys.stderr)
            elif status == "written":
                conn.execute(
                    "UPDATE features SET size=?, mtime=? WHERE relpath=?",
                    (size, mtime, rel),
                )
                if done % 50 == 0:
                    conn.commit()
            if done % 500 == 0:
                print(f"[{done}/{len(jobs)}] {counts}", file=sys.stderr)
    conn.commit()
    print(json.dumps(counts, sort_keys=True))
    return 0


def cmd_status(args) -> int:
    conn = open_db()
    total, errors = conn.execute(
        "SELECT COUNT(*), SUM(error IS NOT NULL) FROM features"
    ).fetchone()
    print(f"{total} tracks analyzed, {errors or 0} errors, db={DB_PATH}")
    for (e,) in conn.execute(
        "SELECT DISTINCT error FROM features WHERE error IS NOT NULL LIMIT 10"
    ):
        print(f"  error: {e}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analyze", help="extract features into the store")
    a.add_argument("paths", nargs="*", help="files/dirs (default: whole library)")
    a.add_argument("--workers", type=int, default=4)
    a.add_argument("--force", action="store_true", help="re-analyze and ignore the AC-power gate")
    a.set_defaults(fn=cmd_analyze)

    s = sub.add_parser("score", help="rank tracks from stored features")
    s.add_argument("--limit", type=int, default=0)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_score)

    w = sub.add_parser("write", help="write scores into file tags")
    w.add_argument("--dry-run", action="store_true", help="report changes without saving")
    w.add_argument("--workers", type=int, default=4)
    w.add_argument("--force", action="store_true", help="ignore the AC-power gate")
    w.set_defaults(fn=cmd_write)

    st = sub.add_parser("status", help="feature-store coverage")
    st.set_defaults(fn=cmd_status)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
