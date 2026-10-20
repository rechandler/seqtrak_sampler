# SEQTRAK Sampler

Companion tools for the Yamaha SEQTRAK. The SEQTRAK app exports resampled sounds
as `.Y3S` files, which it can't import into a synth track. `y3s2wav` turns them
into stereo WAV files that it can, without a round trip through a DAW.

It only reads `.Y3S` files; it never writes Yamaha files. The format is described
in [FORMAT.md](FORMAT.md).

## Install

Python 3.9+, no other dependencies.

```
pip install .
```

## Usage

1. In the SEQTRAK app, export the resampled sound (Sound Manager → right-click → Export).
2. Convert it:

   ```
   y3s2wav Resampling-5-1.Y3S                # writes Resampling-5-1.wav next to it
   y3s2wav *.Y3S -o converted/               # batch into a folder
   y3s2wav take.Y3S --normalize              # peak-normalize to -1 dBFS
   ```

3. Import the WAV into a synth track in the SEQTRAK app.

Output is always 44.1 kHz, 16-bit stereo (mono sounds are duplicated to both sides).
Leading and trailing silence below -60 dBFS is trimmed (`--threshold-db`, `--pad-ms`,
`--no-trim`).

The SEQTRAK caps samples at 16 seconds. Longer results are refused unless you pass
`--truncate`, which cuts to 16 s with a 10 ms fade-out.

## Development

```
PYTHONPATH=src python3 -m unittest discover -s tests
```
