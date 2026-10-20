import struct
import tempfile
import unittest
import wave
import zipfile
from array import array
from pathlib import Path

from seqtrak_sampler import audio, cli
from seqtrak_sampler.y3s import Y3SError, read_y3s

UUID = "201a7082-00e4-8957-1058-d4719a979ea0"


def keybank_record(frames: int, rate: int = 44100, root: int = 60) -> bytes:
    rec = bytearray(64)
    rec[0:2] = b"\x00\x7f"
    rec[8] = root
    struct.pack_into("<I", rec, 36, rate)
    struct.pack_into("<I", rec, 48, frames - 1)
    struct.pack_into("<I", rec, 60, frames)
    return bytes(rec)


def make_y3s(path: Path, channels: list[list[int]], rate: int = 44100,
             declared_frames: int | None = None) -> None:
    """Build a .Y3S shaped like a SEQTRAK sampler export."""
    n = len(channels[0])
    waveform = struct.pack("<I", len(channels)) + b"".join(
        keybank_record(declared_frames or n, rate) for _ in channels
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{UUID}/element.xml",
                    f"<ElementData><ContentNumber>519</ContentNumber>"
                    f"<UUID>{UUID}</UUID></ElementData>")
        zf.writestr(f"{UUID}/element.bin", bytes(512))
        zf.writestr(f"{UUID}/waveform0.xml", "<WaveformData/>")
        zf.writestr(f"{UUID}/waveform0.bin", waveform)
        for i, ch in enumerate(channels):
            data = struct.pack(f"<{n}h", *ch)
            zf.writestr(f"{UUID}/sample{i}.xml",
                        f"<SampleData><WaveformNumber>8599</WaveformNumber>"
                        f"<KeybankNumber>{i}</KeybankNumber>"
                        f"<DataSize>{len(data)}</DataSize></SampleData>")
            zf.writestr(f"{UUID}/sample{i}.bin", data)


def read_wav(path: Path) -> tuple[int, int, list[int]]:
    with wave.open(str(path)) as w:
        raw = w.readframes(w.getnframes())
        return w.getnchannels(), w.getframerate(), list(struct.unpack(f"<{len(raw) // 2}h", raw))


class Y3STests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_reads_stereo_keybanks(self):
        src = self.tmp / "s.y3s"
        make_y3s(src, [[1, 2, 3], [-1, -2, -3]])
        sound = read_y3s(src)
        self.assertEqual(sound.uuid, UUID)
        self.assertEqual(sound.sample_rate, 44100)
        self.assertEqual([list(kb.frames) for kb in sound.keybanks], [[1, 2, 3], [-1, -2, -3]])

    def test_rejects_non_zip(self):
        src = self.tmp / "bad.y3s"
        src.write_bytes(b"not a zip")
        with self.assertRaises(Y3SError):
            read_y3s(src)

    def test_rejects_size_mismatch(self):
        src = self.tmp / "s.y3s"
        make_y3s(src, [[1, 2, 3]], declared_frames=99)
        with self.assertRaises(Y3SError):
            read_y3s(src)


class ConvertTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_trims_silence_and_interleaves(self):
        src = self.tmp / "s.y3s"
        make_y3s(src, [[0] * 1000 + [100, 200] + [0] * 1000,
                       [0] * 1000 + [-100, -200] + [0] * 1000])
        self.assertEqual(cli.main([str(src), "--pad-ms", "0"]), 0)
        channels, rate, data = read_wav(src.with_suffix(".wav"))
        self.assertEqual((channels, rate), (2, 44100))
        self.assertEqual(data, [100, -100, 200, -200])

    def test_mono_is_duplicated_to_stereo(self):
        src = self.tmp / "m.y3s"
        make_y3s(src, [[5, 6]])
        cli.main([str(src), "--no-trim"])
        self.assertEqual(read_wav(src.with_suffix(".wav"))[2], [5, 5, 6, 6])

    def test_rejects_over_16_seconds(self):
        src = self.tmp / "long.y3s"
        n = 44100 * 16 + 1
        make_y3s(src, [[1000] * n, [1000] * n])
        self.assertEqual(cli.main([str(src)]), 1)
        self.assertFalse(src.with_suffix(".wav").exists())

    def test_truncate_cuts_to_16_seconds(self):
        src = self.tmp / "long.y3s"
        n = 44100 * 17
        make_y3s(src, [[1000] * n, [1000] * n])
        self.assertEqual(cli.main([str(src), "--truncate"]), 0)
        _, _, data = read_wav(src.with_suffix(".wav"))
        self.assertEqual(len(data), 2 * 44100 * 16)
        self.assertEqual(data[-2:], [0, 0])  # faded out

    def test_normalize_peak(self):
        left, right = audio.normalize(array("h", [100, -200]), array("h", [50, 0]), 0.0)
        self.assertEqual(list(left), [16384, -32767])
        self.assertEqual(list(right), [8192, 0])

    def test_refuses_to_overwrite(self):
        src = self.tmp / "s.y3s"
        make_y3s(src, [[1, 2]])
        src.with_suffix(".wav").write_bytes(b"keep")
        self.assertEqual(cli.main([str(src)]), 1)
        self.assertEqual(src.with_suffix(".wav").read_bytes(), b"keep")


if __name__ == "__main__":
    unittest.main()
