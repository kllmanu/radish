# radish

Edit MP3, M4A, Opus, and FLAC metadata in Vim or any other text editor.
No beets, database, importing, transcoding, or Vim plugin required.
Python 3.10+, Click, Mutagen, and PyYAML are required.
The standalone binary bundles Python and its dependencies.

## Standalone binary

Run the Linux x86-64 executable directly:

```sh
./dist/radish edit song.mp3 song.m4a song.opus song.flac
```

To rebuild on your target operating system and architecture:

```sh
make build
```

The executable is written to `dist/radish`. No Python installation is needed
to run it, but a text editor and compatible system libraries are still required.
Linux builds are not fully static; build on the oldest Linux distribution you
intend to support. The one-file executable extracts its runtime into a temporary
directory at startup.

## Development

```sh
make install
make test
make build
```

`make build` installs the optional PyInstaller dependency before building.
Set `PYTHON` to choose another Python executable, e.g. `make test PYTHON=.venv/bin/python`.

## Install and use

```sh
python3 -m pip install --user .
radish edit song.mp3 album/*.flac
EDITOR=nvim radish edit song.m4a song.opus
radish edit --editor 'vim -f' song.mp3
```

Alternatively, with dependencies already installed:

```sh
python3 radish.py edit song.mp3
```

The editor is selected from `--editor`, `$VISUAL`, `$EDITOR`, then `vim`.
Use a foreground/wait flag for GUI editors, e.g. `--editor 'code --wait'`.
Commands are parsed as arguments, not run through a shell.
Use `--` before filenames starting with a dash.

By default, each input gets only the three most important tags in the same
YAML buffer:

```yaml
---
title: Butterfly
artist: Crazy Town
album: The Gift Of Game
```

Use `--all` or `-A` to show the full editable view: common fields (including
track, disc, date, genre, composer, and comment) plus all existing supported
text fields. Artwork, binary tags, and unsupported metadata stay hidden.
Tags not shown in the default view are preserved. Both modes allow adding
supported fields manually and work with `--print` and `--dry-run`; `--apply`
always applies the supported fields present in its YAML, regardless of mode.

```sh
radish edit --all song.mp3
./dist/radish edit -A song.flac
```

Save and quit (`:wq` in Vim) to apply; quit without saving (`:q!`) to leave
tags unchanged. YAML documents correspond to input files in order, so preserve
their order when editing or applying exported YAML.

- Set a field to blank, `null`, `''`, or `[]` to delete its tag.
- Removing a field's entire line **preserves** the existing tag.
- Editing `artist` updates both `artist` and `albumartist`, including deletion
  and multiple values. In the full view, editing `albumartist` works the same
  way. Existing differences remain untouched unless either field is edited.
  If both fields are changed to different values, the edit is rejected.
- Use YAML lists for multiple values: `artist: [Alice, Bob]`.
  MP3 ID3v2.3 text frames cannot store separate values losslessly; such edits
  are rejected. ID3v2.4, M4A, FLAC, and Opus support multiple text values.
- `track` and `disc` accept `number/total`, e.g. `'6/12'`.
- Dates, leading zeroes, and words like `yes` are treated as text.
- With `--all`/`-A`, existing supported text fields appear automatically. MP3/M4A accept
  Mutagen's easy-tag fields, plus comments, lyrics, and composer.
  FLAC/Opus also accept arbitrary Vorbis comment fields (lowercase keys).
- Artwork, binary tags, and unsupported metadata are not exposed in YAML.
  They remain in the file; only changed fields are written.
- Editing MP3 comments/lyrics replaces all existing language/description
  variants of that field. Unchanged variants are preserved.
- MP4 numeric tags have format-specific limits and normalization. Values
  that do not round-trip exactly (including leading zeroes in MP4 track
  numbers) are rejected, not silently truncated.

## Preview and noninteractive use

```sh
radish edit --dry-run song.mp3
radish edit --print song.mp3 song.flac > tags.yaml
radish edit -A --print song.mp3 song.flac > all-tags.yaml
# Edit tags.yaml, then:
radish edit --apply tags.yaml --dry-run song.mp3 song.flac
radish edit --apply tags.yaml song.mp3 song.flac
```

## Safety

All edits are validated and staged in temporary copies beside the originals.
Tags are read back to verify edited values before any original is replaced.
Replacement is atomic **per file**, not a transaction across files. An error
during replacement reports files already updated. Temporary copies are cleaned
up; editor buffers are retained on errors, with their path printed to stderr.
Invalid YAML, duplicate keys, unsupported fields, editor failures, and changes
to input files detected while editing abort the operation.

Symlinks resolve to their targets. Hard-linked files are rejected because
atomic replacement would break their shared identity. Permissions and other
metadata supported by `shutil.copy2` are retained; replacement files belong to
the user running the command. MP3 ID3v2.3/v2.4 version is retained (older tags
are upgraded to v2.4 by Mutagen). Keep backups of valuable audio files.
Concurrent writers cannot be fully excluded without cooperation; do not edit
the same files in another program during a session.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

Integration tests require `ffmpeg` to generate tiny audio fixtures locally.
