# SEQTRAK `.Y3S` sound format (observed)

Notes from one SEQTRAK sampler export (`Resampling-5-1.Y3S`, stereo, 11.6 s).
This was worked out by reading the exported file only. Fields marked **?** are guesses.

## Container

A standard zip archive (deflate) with a single folder named after the sound's UUID:

```
<uuid>/element.xml     catalog entry for the sound
<uuid>/element.bin     512-byte sound parameter block
<uuid>/waveform0.xml   catalog entry for the waveform
<uuid>/waveform0.bin   keybank table
<uuid>/sample0.xml     metadata for keybank 0
<uuid>/sample0.bin     PCM for keybank 0
<uuid>/sample1.xml     metadata for keybank 1 (stereo exports only)
<uuid>/sample1.bin     PCM for keybank 1
```

## XML files

`element.xml` (UTF-8 with BOM):

```xml
<ElementData>
  <ContentNumber>519</ContentNumber>
  <UUID>201a7082-00e4-8957-1058-d4719a979ea0</UUID>
  <Order>404</Order>
  <Tag1></Tag1><Tag2></Tag2>
  <SubCategory>14</SubCategory>
  <Commercial>false</Commercial>
  <Favorite>true</Favorite>
</ElementData>
```

`waveform0.xml`: `ContentNumber` (8599, the waveform number), its own `UUID`, `Order`,
`Tag1`/`Tag2`, `MainCategory` (2), `SubCategory` (0), `Commercial`.

`sampleN.xml`:

```xml
<SampleData>
  <WaveformNumber>8599</WaveformNumber>   <!-- matches waveform0.xml ContentNumber -->
  <KeybankNumber>0</KeybankNumber>
  <DataSize>1023136</DataSize>            <!-- byte length of sampleN.bin -->
</SampleData>
```

## `sampleN.bin`

Raw PCM with no header: signed 16-bit little-endian, mono, 44.1 kHz.
A stereo sound is stored as two keybanks of equal length: keybank 0 = left,
keybank 1 = right (**?**, the two channels differ slightly; pan bytes are both centered).

The export keeps the silence recorded around the take (about 2.4 s before and
1.4 s after in the sample); `y3s2wav` trims it.

## `waveform0.bin`

```
0x00  u32 LE   keybank count (2)
0x04  64-byte record per keybank
```

Keybank record (offsets relative to record start):

| Offset | Value seen | Meaning |
|---|---|---|
| 0x00 | `00` | low key **?** |
| 0x01 | `7F` | high key **?** |
| 0x02 | `01` | low velocity **?** |
| 0x03 | `7F` | high velocity **?** |
| 0x05 | `40` | pan (center) **?** |
| 0x08 | `3C` | root note (60) **?** |
| 0x24 | u32 LE `44100` | sample rate |
| 0x30 | u32 LE `511567` | last frame index |
| 0x38 | u16 LE `0x6C43` / `0x93BD` | unknown; differs per keybank and is not a common checksum (byte or word sums, CRC-16, CRC-32 halves and Adler-32 were tried) |
| 0x3C | u32 LE `511568` | frame count |

## `element.bin`

512 bytes. Starts `00 00 00 71 00`, then the sound name (`Resampl` `00` `ing-5-1`;
the extra null inside the name isn't explained). From 0x65: u32 LE values
`511567` (last frame), `105388` (start point **?**, just before the first
non-silent frame at 106823), `222527` (loop point **?**), `511568` (frame count).
The rest looks like 7-bit parameter values (level, filter, envelope, ...), padded
with `FF` from 0x102.
