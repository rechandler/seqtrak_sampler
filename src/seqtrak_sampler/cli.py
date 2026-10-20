"""y3s2wav: convert SEQTRAK .Y3S sound exports into stereo WAV files."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import audio
from .y3s import Y3SError, read_y3s


def convert(src: Path, dst: Path, args: argparse.Namespace) -> float:
    sound = read_y3s(src)
    left, right = audio.to_stereo([kb.frames for kb in sound.keybanks])
    rate = sound.sample_rate

    if not args.no_trim:
        threshold = audio.dbfs_to_amplitude(args.threshold_db)
        pad = int(rate * args.pad_ms / 1000)
        start, end = audio.silence_bounds(left, right, threshold, pad)
        if start == end:
            raise Y3SError("sound is silent")
        left, right = left[start:end], right[start:end]

    left, right = audio.enforce_max_length(left, right, rate, args.truncate)
    if args.normalize is not None:
        left, right = audio.normalize(left, right, args.normalize)

    audio.write_wav(dst, left, right, rate)
    return len(left) / rate


def output_path(src: Path, out: Path | None, many: bool) -> Path:
    if out is None:
        return src.with_suffix(".wav")
    if many or out.is_dir():
        return out / (src.stem + ".wav")
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="y3s2wav", description="Convert SEQTRAK .Y3S sound exports to stereo WAV."
    )
    p.add_argument("inputs", nargs="+", type=Path, metavar="FILE.Y3S")
    p.add_argument("-o", "--output", type=Path,
                   help="output file, or a directory when converting several files")
    p.add_argument("--no-trim", action="store_true",
                   help="keep leading and trailing silence")
    p.add_argument("--threshold-db", type=float, default=-60.0,
                   help="silence threshold for trimming, in dBFS (default: -60)")
    p.add_argument("--pad-ms", type=float, default=5.0,
                   help="audio kept before and after the trimmed region (default: 5)")
    p.add_argument("--normalize", type=float, nargs="?", const=-1.0, metavar="DBFS",
                   help="normalize peak to DBFS (default when given: -1)")
    p.add_argument("--truncate", action="store_true",
                   help=f"cut samples longer than {audio.MAX_SECONDS:g} s instead of failing")
    p.add_argument("-f", "--force", action="store_true", help="overwrite existing files")
    args = p.parse_args(argv)

    many = len(args.inputs) > 1
    if many and args.output is not None and not args.output.is_dir():
        p.error("--output must be an existing directory when converting several files")

    failures = 0
    for src in args.inputs:
        dst = output_path(src, args.output, many)
        try:
            if dst.exists() and not args.force:
                raise FileExistsError(f"{dst} exists (use --force to overwrite)")
            seconds = convert(src, dst, args)
        except (OSError, ValueError) as e:
            print(f"{src}: {e}", file=sys.stderr)
            failures += 1
            continue
        print(f"{src} -> {dst} ({seconds:.2f} s, stereo, 16-bit)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
