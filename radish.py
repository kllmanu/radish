#!/usr/bin/env python3
"""Edit audio tags through a YAML buffer and an external editor."""

import argparse
from dataclasses import dataclass
import fnmatch
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

import click
import mutagen
from mutagen.easyid3 import EasyID3
from mutagen.easymp4 import EasyMP4Tags
from mutagen.flac import FLAC
from mutagen.id3 import COMM, USLT
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from mutagen.oggopus import OggOpus
import yaml


class EditError(Exception):
    pass


class TagLoader(yaml.SafeLoader):
    pass


# Keep dates, leading zeroes, and words such as "yes" as tag text.
TagLoader.yaml_implicit_resolvers = {
    key: [
        (tag, pattern) for tag, pattern in resolvers
        if tag not in {
            "tag:yaml.org,2002:bool", "tag:yaml.org,2002:int",
            "tag:yaml.org,2002:float", "tag:yaml.org,2002:timestamp",
        }
    ]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def unique_mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str):
            raise EditError("YAML field names must be strings")
        if key in result:
            raise EditError(f"Duplicate YAML field: {key}")
        result[key] = loader.construct_object(value_node)
    return result


TagLoader.add_constructor("tag:yaml.org,2002:map", unique_mapping)


def register_id3_text(key, frame_id, frame_type):
    def get(tags, name):
        frames = tags.getall(frame_id)
        if not frames:
            raise KeyError(name)
        return [text for frame in frames for text in (
            frame.text if isinstance(frame.text, list) else [frame.text]
        )]

    def set_value(tags, name, values):
        tags.delall(frame_id)
        if frame_id == "USLT":
            if len(values) != 1:
                raise EditError("MP3 lyrics must have exactly one value")
            tags.add(frame_type(encoding=3, lang="eng", desc="", text=values[0]))
        else:
            tags.add(frame_type(encoding=3, lang="eng", desc="", text=values))

    def delete(tags, name):
        tags.delall(frame_id)

    EasyID3.RegisterKey(key, get, set_value, delete)


register_id3_text("comment", "COMM", COMM)
register_id3_text("lyrics", "USLT", USLT)
EasyMP4Tags.RegisterTextKey("composer", "\xa9wrt")
EasyMP4Tags.RegisterTextKey("lyrics", "\xa9lyr")

IMPORTANT = ("title", "artist", "album")
COMMON = ("album", "artist", "albumartist", "title", "track", "disc",
          "date", "genre", "composer", "comment")
ALIASES = {"track": "tracknumber", "disc": "discnumber"}
REVERSE_ALIASES = {value: key for key, value in ALIASES.items()}
HIDDEN_FIELDS = {"file", "metadata_block_picture", "coverart", "coverartmime"}
SUPPORTED_AUDIO_SUFFIXES = {".mp3", ".m4a", ".flac", ".opus"}
HEADER = (
    "# Edit values, save, and quit to apply. Quit without saving to cancel.\n"
    "# Documents match input files in order. Blank/null/[] deletes a tag; omitted fields stay unchanged.\n"
    "# Multiple values: artist: [First Artist, Second Artist]\n"
)


def signature(path):
    stat = path.stat()
    return (stat.st_dev, stat.st_ino, stat.st_size,
            stat.st_mtime_ns, stat.st_ctime_ns)


@dataclass
class Audio:
    path: Path
    kind: str
    values: dict[str, list[str]]
    original_signature: tuple[int, ...]
    id3_version: int = 4


def load_audio(path):
    raw = mutagen.File(path)
    if isinstance(raw, MP3):
        kind = "mp3"
    elif isinstance(raw, MP4):
        kind = "m4a"
    elif isinstance(raw, FLAC):
        kind = "flac"
    elif isinstance(raw, OggOpus):
        kind = "opus"
    else:
        raise EditError(f"{path}: not an MP3, M4A, FLAC, or Opus audio file")
    audio = mutagen.File(path, easy=True)
    if audio.tags is None:
        audio.add_tags()
    values = {}
    for key in audio.tags.keys():
        if key.lower() in HIDDEN_FIELDS:
            continue
        normalized = REVERSE_ALIASES.get(key.lower(), key.lower())
        values[normalized] = list(audio.tags[key])
    version = raw.tags.version[1] if kind == "mp3" and raw.tags else 4
    return Audio(path, kind, values, signature(path), 3 if version == 3 else 4)


def read_files(filenames):
    result = []
    seen = set()
    for filename in filenames:
        path = Path(filename).expanduser().resolve(strict=True)
        if not path.is_file():
            raise EditError(f"{path}: not a regular file")
        stat = path.stat()
        identity = (stat.st_dev, stat.st_ino)
        if identity in seen:
            raise EditError(f"{path}: the same file was passed more than once")
        if stat.st_nlink > 1:
            raise EditError(f"{path}: hard-linked files are not supported by atomic replacement")
        seen.add(identity)
        before = signature(path)
        audio = load_audio(path)
        if before != audio.original_signature:
            raise EditError(f"{path}: file changed while reading")
        result.append(audio)
    return result


def artist_names(roots):
    paths = set()
    for root in roots:
        path = Path(root).expanduser()
        if not path.exists():
            raise EditError(f"{path}: path does not exist")
        if path.is_file():
            if path.suffix.lower() not in SUPPORTED_AUDIO_SUFFIXES:
                raise EditError(f"{path}: not an MP3, M4A, FLAC, or Opus audio file")
            paths.add(path.resolve())
        elif path.is_dir():
            paths.update(
                child.resolve()
                for child in path.rglob("*")
                if child.is_file() and child.suffix.lower() in SUPPORTED_AUDIO_SUFFIXES
            )
        else:
            raise EditError(f"{path}: not a regular file or directory")

    artists = set()
    for path in sorted(paths):
        try:
            audio = mutagen.File(path, easy=True)
        except (OSError, ValueError, mutagen.MutagenError) as error:
            raise EditError(f"{path}: could not read audio tags: {error}") from error
        if audio is None:
            raise EditError(f"{path}: could not identify audio format")
        if audio.tags is not None:
            artists.update(
                value.strip()
                for value in audio.tags.get("artist", [])
                if isinstance(value, str) and value.strip()
            )
    return sorted(artists, key=str.casefold)


def render(files, all_fields=False):
    documents = []
    for audio in files:
        document = {}
        fields = dict.fromkeys((*COMMON, *sorted(audio.values))) if all_fields else IMPORTANT
        for key in fields:
            values = audio.values.get(key, [])
            document[key] = values[0] if len(values) == 1 else values or None
        documents.append(document)
    return HEADER + yaml.safe_dump_all(
        documents, sort_keys=False, allow_unicode=True, explicit_start=True,
        width=1000,
    )


def supported(audio, key):
    if key in HIDDEN_FIELDS:
        return False
    native = ALIASES.get(key, key)
    if audio.kind in ("flac", "opus"):
        return bool(re.fullmatch(r"[ -<>-}]+", native))
    keys = EasyID3.Set if audio.kind == "mp3" else EasyMP4Tags.Set
    return any(fnmatch.fnmatchcase(native, pattern) for pattern in keys)


def parse_changes(text, files):
    documents = list(yaml.load_all(text, Loader=TagLoader))
    if len(documents) != len(files):
        raise EditError("Keep exactly one YAML document for each input file")
    changes = []
    for document, audio in zip(documents, files):
        if not isinstance(document, dict):
            raise EditError("Each YAML document must contain a mapping of tags")
        path = str(audio.path)
        updates = {}
        for key, value in document.items():
            if (key != key.lower() or key in REVERSE_ALIASES
                    or not supported(audio, key)):
                raise EditError(f"{path}: unsupported field {key!r}; use 'track'/'disc' aliases")
            if value is None or value == "":
                values = []
            elif isinstance(value, str):
                values = [value]
            elif isinstance(value, list) and all(isinstance(item, str) and item for item in value):
                values = value
            else:
                raise EditError(f"{path}: {key} must be text, a list of nonempty text, or null")
            if any("\x00" in item for item in values):
                raise EditError(f"{path}: {key} cannot contain NUL characters")
            if values != audio.values.get(key, []):
                updates[key] = values
        artist_fields = [key for key in ("artist", "albumartist") if key in updates]
        if len(artist_fields) == 2 and updates["artist"] != updates["albumartist"]:
            raise EditError(f"{path}: artist and albumartist edits must have the same values")
        if artist_fields:
            values = updates[artist_fields[0]]
            for key in ("artist", "albumartist"):
                if values != audio.values.get(key, []):
                    updates[key] = values
        if updates:
            changes.append((audio, updates))
    return changes


def ensure_unchanged(audio):
    if signature(audio.path) != audio.original_signature:
        raise EditError(f"{audio.path}: file changed since opening; refusing to overwrite it")


def apply_changes(changes):
    staged = []
    committed = []
    try:
        for audio, updates in changes:
            ensure_unchanged(audio)
            fd, filename = tempfile.mkstemp(
                prefix=f".{audio.path.name}.radish-", suffix=audio.path.suffix,
                dir=audio.path.parent,
            )
            os.close(fd)
            temporary = Path(filename)
            staged.append((audio, temporary))
            shutil.copy2(audio.path, temporary)
            ensure_unchanged(audio)
            editable = mutagen.File(temporary, easy=True)
            if editable.tags is None:
                editable.add_tags()
            for key, values in updates.items():
                native = ALIASES.get(key, key)
                if values:
                    editable.tags[native] = values
                elif native in editable.tags:
                    del editable.tags[native]
            if audio.kind == "mp3":
                editable.save(v2_version=audio.id3_version)
            else:
                editable.save()
            verified = load_audio(temporary)
            for key, values in updates.items():
                if verified.values.get(key, []) != values:
                    raise EditError(
                        f"{audio.path}: {key} cannot be stored losslessly as {values!r}"
                    )
            with temporary.open("rb") as stream:
                os.fsync(stream.fileno())
        # All files must stage successfully before any original is replaced.
        for audio, temporary in staged:
            ensure_unchanged(audio)
        for audio, temporary in staged:
            ensure_unchanged(audio)
            os.replace(temporary, audio.path)
            committed.append(str(audio.path))
            directory_fd = os.open(audio.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except (OSError, ValueError, mutagen.MutagenError, EditError) as error:
        detail = f"; already updated: {', '.join(committed)}" if committed else "; no originals updated"
        raise EditError(f"Could not apply tags: {error}{detail}") from error
    finally:
        for _, temporary in staged:
            temporary.unlink(missing_ok=True)


def editor_command(override):
    command = shlex.split(override or os.environ.get("VISUAL")
                          or os.environ.get("EDITOR") or "vim")
    if not command:
        raise EditError("Editor command is empty")
    return command


def run(args):
    files = read_files(args.files)
    original = render(files, all_fields=args.all_fields)
    if args.print_tags:
        sys.stdout.write(original)
        return
    buffer = None
    try:
        if args.apply:
            edited = Path(args.apply).read_text(encoding="utf-8")
        else:
            fd, name = tempfile.mkstemp(prefix="radish-", suffix=".yaml")
            buffer = Path(name)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(original)
            process = subprocess.run([*editor_command(args.editor), str(buffer)], check=False)
            if process.returncode:
                raise EditError(f"Editor exited with status {process.returncode}; no tags applied")
            edited = buffer.read_text(encoding="utf-8")
        changes = parse_changes(edited, files)
        if args.dry_run:
            for audio, updates in changes:
                print(f"{audio.path}:")
                for key, values in updates.items():
                    print(f"  {key}: {audio.values.get(key, [])!r} -> {values!r}")
        else:
            apply_changes(changes)
        print(f"{'Would update' if args.dry_run else 'Updated'} {len(changes)} file(s).")
    except (EditError, OSError, ValueError, yaml.YAMLError, mutagen.MutagenError,
            KeyboardInterrupt):
        if buffer:
            print(f"Edit buffer retained at {buffer}", file=sys.stderr)
        raise
    else:
        if buffer:
            buffer.unlink()


def report_error(error):
    print(f"radish: {error}", file=sys.stderr)


def execute(args):
    try:
        run(args)
    except (EditError, OSError, ValueError, yaml.YAMLError, mutagen.MutagenError) as error:
        report_error(error)
        return 1
    except KeyboardInterrupt:
        report_error("interrupted")
        return 130
    return 0


@click.group()
def cli():
    """Edit audio metadata in your text editor, without beets."""


@cli.command()
@click.argument("paths", nargs=-1, type=click.Path(path_type=Path))
@click.option(
    "--library", "library_path", envvar="RADISH_LIBRARY", default="~/Music",
    show_default=True, help="library directory (default: ~/Music or $RADISH_LIBRARY)",
)
def artists(paths, library_path):
    """List unique track artists, one per line, for tools such as fzf."""
    try:
        for artist in artist_names(paths or [library_path]):
            click.echo(artist)
    except (EditError, OSError, ValueError, mutagen.MutagenError) as error:
        raise click.ClickException(str(error)) from error


@cli.command()
@click.argument("files", nargs=-1, required=True)
@click.option("--editor", help="editor command (default: $VISUAL, $EDITOR, vim)")
@click.option("--all", "-A", "all_fields", is_flag=True,
              help="show all editable fields (default: title, artist, album)")
@click.option("--print", "print_tags", is_flag=True, help="print editable YAML")
@click.option("--apply", "apply_file", metavar="YAML",
              help="apply a previously exported YAML file")
@click.option("--dry-run", is_flag=True, help="show changes without writing audio files")
def edit(files, editor, all_fields, print_tags, apply_file, dry_run):
    """Edit tags for one or more audio files."""
    if print_tags and apply_file:
        raise click.UsageError("--print and --apply cannot be used together")
    args = argparse.Namespace(
        files=files,
        editor=editor,
        all_fields=all_fields,
        print_tags=print_tags,
        apply=apply_file,
        dry_run=dry_run,
    )
    code = execute(args)
    if code:
        raise click.exceptions.Exit(code)
    return code


def main(argv=None):
    try:
        return edit.main(args=argv, prog_name="radish edit", standalone_mode=False)
    except click.ClickException as error:
        error.show()
        return error.exit_code
    except click.exceptions.Exit as exit:
        return exit.exit_code


if __name__ == "__main__":
    cli()
