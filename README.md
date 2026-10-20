# SEQTRAK Sampler

**Website:** https://rechandler.github.io/seqtrak_sampler/

Companion tools for the Yamaha SEQTRAK. The SEQTRAK app exports resampled sounds
as `.Y3S` files, which it can't import into a synth track. SEQTRAK Sampler (a desktop
app) and `y3s2wav` (its command-line twin) turn them into trimmed stereo WAV files that
it can, without a round trip through a DAW.

It only reads `.Y3S` files; it never writes Yamaha files. The format is described
in [FORMAT.md](FORMAT.md).

## Desktop app

![](docs/screenshot.png)

1. In the SEQTRAK app, export the resampled sound (Sound Manager → right-click → Export).
2. Drag the `.Y3S` files into SEQTRAK Sampler (or use **Add files…**).
3. Leading and trailing silence is trimmed automatically. Drag the orange handles to
   fine-tune, press **Play** (or Space) to listen to exactly what will be exported.
4. Name the sample and press **Export**. The WAV lands in the output folder
   (default `~/Music/SEQTRAK Samples`); **Show** opens it.
5. Import the WAV into a synth track in the SEQTRAK app.

### Faster round trips

- **Watch folder:** click **Watch folder…** and pick the folder you export `.Y3S` files
  to. New exports load on their own within a second, and re-exporting a sound with the
  same name reloads it (keeping the name you gave it).
- **Drag out:** drag a sample from the list, or the **Drag WAV** button, straight onto
  the SEQTRAK app's import. No file dialogs.
- **Auto-save:** turn it on and every sample is written to the output folder as soon as
  it loads or you edit it. Renaming a sample renames its file.

The length meter shows the selection against the SEQTRAK's 16-second limit. Anything
past the limit is shaded red and Export is disabled until you drag the end handle in
or press **Cut to 16 s**. Edges that cut through sound get a short fade so they don't
click. **Normalize** raises the peak to -1 dBFS.

### Getting the app

Download the latest build from the [website](https://rechandler.github.io/seqtrak_sampler/#download)
or the [Releases page](https://github.com/rechandler/seqtrak_sampler/releases/latest).
Every push also builds macOS and Windows apps (Actions → Build → Artifacts); pushing a
`v*` tag publishes them as a Release. They aren't code-signed: on macOS, right-click the app
and choose **Open** the first time; on Windows, choose **More info → Run anyway**.

To run from source instead:

```
pip install ".[gui]"
seqtrak-sampler
```

## Command line

Python 3.9+, no other dependencies.

```
pip install .
```

```
y3s2wav Resampling-5-1.Y3S                # writes Resampling-5-1.wav next to it
y3s2wav *.Y3S -o converted/               # batch into a folder
y3s2wav take.Y3S --normalize              # peak-normalize to -1 dBFS
```

Output is always 44.1 kHz, 16-bit stereo (mono sounds are duplicated to both sides).
Leading and trailing silence below -60 dBFS is trimmed (`--threshold-db`, `--pad-ms`,
`--no-trim`).

The SEQTRAK caps samples at 16 seconds. Longer results are refused unless you pass
`--truncate`, which cuts to 16 s with a 10 ms fade-out.

## Development

```
PYTHONPATH=src python3 -m unittest discover -s tests
PYTHONPATH=src python3 -m seqtrak_sampler.gui     # run the app from a checkout

# Build the app locally (on the OS you're targeting)
pip install pyinstaller ".[gui]"
pyinstaller --noconfirm --windowed --name "SEQTRAK Sampler" packaging/launch.py
```
