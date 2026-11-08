import base64
import contextlib
import hashlib
import io
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import struct
import sys
import tempfile
import unittest
import zlib
from unittest.mock import patch

import mutagen
from mutagen.flac import Picture
from mutagen.id3 import APIC, TXXX
from mutagen.mp4 import MP4Cover
import yaml
from click.testing import CliRunner

import radish as tag_edit


def png():
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data)))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
            + chunk(b"IEND", b""))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audio_digest(path):
    return subprocess.check_output([
        "ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0",
        "-c:a", "copy", "-f", "hash", "-hash", "sha256", "-",
    ])


class ParsingTests(unittest.TestCase):
    def setUp(self):
        self.audio = tag_edit.Audio(
            Path("/music/song.flac"), "flac",
            {"title": ["Original"], "artist": ["Someone"]}, (),
        )

    def parse(self, fields):
        return tag_edit.parse_changes(fields, [self.audio])

    def test_default_fields_include_missing_values(self):
        document, = yaml.safe_load_all(tag_edit.render([self.audio]))
        self.assertEqual(list(document), ["title", "artist", "album"])
        self.assertEqual(document["title"], "Original")
        self.assertIsNone(document["album"])

    def test_documents_match_input_order(self):
        second = tag_edit.Audio(
            Path("/music/second.flac"), "flac", {"title": ["Second"]}, (),
        )
        changes = tag_edit.parse_changes(
            "title: First edited\n---\ntitle: Second edited\n",
            [self.audio, second],
        )
        self.assertEqual(changes, [
            (self.audio, {"title": ["First edited"]}),
            (second, {"title": ["Second edited"]}),
        ])

    def test_all_fields_include_existing_and_common_fields(self):
        self.audio.values["custom"] = ["Value"]
        document, = yaml.safe_load_all(tag_edit.render([self.audio], all_fields=True))
        self.assertTrue(set(tag_edit.COMMON).issubset(document))
        self.assertEqual(document["custom"], "Value")
        self.assertEqual(tag_edit.parse_changes(
            tag_edit.render([self.audio], all_fields=True), [self.audio],
        ), [])

    def test_text_scalars_and_multivalue(self):
        changes = self.parse("track: 01\ndate: 2026-11-08\ncomment: yes\nartist: [A, B]\n")
        self.assertEqual(changes[0][1], {
            "track": ["01"], "date": ["2026-11-08"],
            "comment": ["yes"], "artist": ["A", "B"], "albumartist": ["A", "B"],
        })

    def test_artist_aliases_sync_only_when_edited(self):
        self.audio.values["albumartist"] = ["Various Artists"]
        self.assertEqual(self.parse("artist: Someone\nalbumartist: Various Artists\n"), [])
        self.assertEqual(self.parse("title: New title\n"), [(self.audio, {"title": ["New title"]})])
        for fields in (
            "artist: New artist\n", "albumartist: New artist\n",
            "artist: New artist\nalbumartist: Various Artists\n",
            "artist: Someone\nalbumartist: New artist\n",
            "artist: New artist\nalbumartist: New artist\n",
        ):
            with self.subTest(fields=fields):
                self.assertEqual(self.parse(fields), [(self.audio, {
                    "artist": ["New artist"], "albumartist": ["New artist"],
                })])
        for fields in ("artist:\n", "albumartist: []\n"):
            with self.subTest(fields=fields):
                self.assertEqual(self.parse(fields), [(self.audio, {
                    "artist": [], "albumartist": [],
                })])
        with self.assertRaisesRegex(tag_edit.EditError, "must have the same values"):
            self.parse("artist: One\nalbumartist: Two\n")

    def test_empty_deletes_and_omission_preserves(self):
        self.assertEqual(self.parse("title:\n"), [(self.audio, {"title": []})])
        self.assertEqual(self.parse("title: []\n"), [(self.audio, {"title": []})])
        self.assertEqual(self.parse("title: Original\n"), [])

    def test_invalid_documents(self):
        for fields in (
            "title: A\ntitle: B\n", "title: {nested: value}\n",
            "title: [null]\n", "Title: A\n", "tracknumber: 1\n",
            "metadata_block_picture: nope\n", "'bad=key': value\n",
        ):
            with self.subTest(fields=fields), self.assertRaises(tag_edit.EditError):
                self.parse(fields)
        with self.assertRaisesRegex(tag_edit.EditError, "unsupported field 'file'"):
            tag_edit.parse_changes("file: /different\n", [self.audio])
        with self.assertRaises(tag_edit.EditError):
            tag_edit.parse_changes("", [self.audio])


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is required for integration fixtures")
class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = tempfile.TemporaryDirectory(prefix="radish-fixtures-")
        cls.fixture_dir = Path(cls.fixtures.name)
        for extension, codec in (
            ("mp3", "libmp3lame"), ("m4a", "aac"),
            ("flac", "flac"), ("opus", "libopus"),
        ):
            subprocess.run([
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                "sine=frequency=440:duration=0.1", "-c:a", codec,
                str(cls.fixture_dir / f"fixture.{extension}"),
            ], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.fixtures.cleanup()

    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory(prefix="radish-test-")
        self.directory = Path(self.workspace.name)
        self.paths = []
        picture = Picture()
        picture.type = 3
        picture.mime = "image/png"
        picture.data = png()
        for source in self.fixture_dir.iterdir():
            path = self.directory / f"audio with spaces{source.suffix}"
            shutil.copy2(source, path)
            audio = mutagen.File(path, easy=True)
            if audio.tags is None:
                audio.add_tags()
            audio["title"] = ["Original"]
            audio["artist"] = ["Original Artist"]
            audio["album"] = ["Keep this album"]
            audio.save()
            raw = mutagen.File(path)
            if path.suffix == ".mp3":
                raw.tags.add(APIC(encoding=3, mime="image/png", type=3, data=picture.data))
                raw.tags.add(TXXX(encoding=3, desc="PRIVATE", text=["preserve me"]))
                raw.save(v2_version=3)
            elif path.suffix == ".m4a":
                raw["covr"] = [MP4Cover(picture.data, imageformat=MP4Cover.FORMAT_PNG)]
                raw["----:com.apple.iTunes:PRIVATE"] = [b"preserve me"]
                raw.save()
            elif path.suffix == ".flac":
                raw.add_picture(picture)
                raw["private"] = ["preserve me"]
                raw.save()
            else:
                raw["metadata_block_picture"] = [
                    base64.b64encode(picture.write()).decode("ascii")
                ]
                raw["private"] = ["preserve me"]
                raw.save()
            self.paths.append(path)

    def tearDown(self):
        self.workspace.cleanup()

    def files(self):
        return tag_edit.read_files([str(path) for path in self.paths])

    def documents(self, files):
        return list(yaml.safe_load_all(tag_edit.render(files, all_fields=True)))

    def test_cli_field_modes_for_export_and_editor(self):
        files = self.files()
        for flags in ([], ["--all"], ["-A"]):
            with self.subTest(flags=flags):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(tag_edit.main([
                        *flags, "--print", *map(str, self.paths),
                    ]), 0)
                exported = list(yaml.safe_load_all(output.getvalue()))
                for document, audio in zip(exported, files):
                    expected = (set(tag_edit.COMMON) | set(audio.values)
                                if flags else set(tag_edit.IMPORTANT))
                    self.assertEqual(set(document), expected)

                captured = []

                def editor(command, check):
                    captured.extend(yaml.safe_load_all(
                        Path(command[-1]).read_text(encoding="utf-8"),
                    ))
                    return subprocess.CompletedProcess(command, 0)

                with patch.object(tag_edit.subprocess, "run", side_effect=editor):
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(tag_edit.main([
                            *flags, "--editor", "vim", *map(str, self.paths),
                        ]), 0)
                self.assertEqual(captured, exported)

    def test_default_edits_preserve_unshown_fields(self):
        files = self.files()
        for audio in files:
            tag_edit.apply_changes([(audio, {"track": ["4/10"], "comment": ["Keep me"]})])
        files = self.files()
        documents = list(yaml.safe_load_all(tag_edit.render(files)))
        for document in documents:
            self.assertEqual(set(document), set(tag_edit.IMPORTANT))
            document["title"] = "New title"
        tag_edit.apply_changes(tag_edit.parse_changes(yaml.safe_dump_all(documents), files))
        for audio in self.files():
            self.assertEqual(audio.values["title"], ["New title"])
            self.assertEqual(audio.values["track"], ["4/10"])
            self.assertEqual(audio.values["comment"], ["Keep me"])
            if audio.kind in ("flac", "opus"):
                self.assertEqual(audio.values["private"], ["preserve me"])

    def test_all_formats_edit_delete_preserve_art_and_audio(self):
        files = self.files()
        documents = self.documents(files)
        before = {path: audio_digest(path) for path in self.paths}
        for document, audio in zip(documents, files):
            document.update({
                "title": "A new title \u00e9",
                "artist": ["Alice"] if audio.kind == "mp3" else ["Alice", "Bob"],
                "track": "6/12", "disc": "1/2", "date": "2026",
                "comment": "Hello", "composer": "Composer", "lyrics": "Line one\nLine two",
                "genre": "Rock",
            })
        changes = tag_edit.parse_changes(yaml.safe_dump_all(documents), files)
        tag_edit.apply_changes(changes)
        for audio in self.files():
            self.assertEqual(audio.values["title"], ["A new title \u00e9"])
            self.assertEqual(audio.values["artist"],
                             ["Alice"] if audio.kind == "mp3" else ["Alice", "Bob"])
            self.assertEqual(audio.values["albumartist"], audio.values["artist"])
            self.assertEqual(audio.values["track"], ["6/12"])
            self.assertEqual(audio.values["lyrics"], ["Line one\nLine two"])
            self.assertEqual(audio.values["album"], ["Keep this album"])
            self.assertEqual(audio_digest(audio.path), before[audio.path])
            raw = mutagen.File(audio.path)
            if audio.kind == "mp3":
                self.assertEqual(raw.tags.version[1], 3)
                self.assertEqual(raw.tags.getall("APIC")[0].data, png())
                self.assertEqual(raw.tags["TXXX:PRIVATE"].text, ["preserve me"])
            elif audio.kind == "m4a":
                self.assertEqual(bytes(raw["covr"][0]), png())
                self.assertEqual(raw["----:com.apple.iTunes:PRIVATE"], [b"preserve me"])
            elif audio.kind == "flac":
                self.assertEqual(raw.pictures[0].data, png())
                self.assertEqual(raw["private"], ["preserve me"])
            else:
                self.assertNotIn("metadata_block_picture", audio.values)
                self.assertIn("metadata_block_picture", raw)
                self.assertEqual(raw["private"], ["preserve me"])
        files = self.files()
        documents = self.documents(files)
        for document in documents:
            document["title"] = None
            document["comment"] = []
            document["lyrics"] = ""
        tag_edit.apply_changes(tag_edit.parse_changes(yaml.safe_dump_all(documents), files))
        for audio in self.files():
            for key in ("title", "comment", "lyrics"):
                self.assertNotIn(key, audio.values)
        self.assertFalse(list(self.directory.glob(".*.radish-*")))

    def test_artist_sync_in_both_views_for_all_formats(self):
        for all_fields in (False, True):
            for edited_key in (("artist", "albumartist") if all_fields else ("artist",)):
                with self.subTest(all_fields=all_fields, edited_key=edited_key):
                    for audio in self.files():
                        tag_edit.apply_changes([(audio, {
                            "artist": ["Track Artist"], "albumartist": ["Album Artist"],
                        })])
                    files = self.files()
                    before = {audio.path: digest(audio.path) for audio in files}
                    text = tag_edit.render(files, all_fields=all_fields)
                    self.assertEqual(tag_edit.parse_changes(text, files), [])
                    self.assertEqual({audio.path: digest(audio.path) for audio in files}, before)
                    documents = list(yaml.safe_load_all(text))
                    for document in documents:
                        document[edited_key] = "Unified Artist"
                    tag_edit.apply_changes(tag_edit.parse_changes(
                        yaml.safe_dump_all(documents), files,
                    ))
                    for audio in self.files():
                        self.assertEqual(audio.values["artist"], ["Unified Artist"])
                        self.assertEqual(audio.values["albumartist"], ["Unified Artist"])
                    files = self.files()
                    documents = list(yaml.safe_load_all(
                        tag_edit.render(files, all_fields=all_fields),
                    ))
                    for document in documents:
                        document[edited_key] = None
                    tag_edit.apply_changes(tag_edit.parse_changes(
                        yaml.safe_dump_all(documents), files,
                    ))
                    for audio in self.files():
                        self.assertNotIn("artist", audio.values)
                        self.assertNotIn("albumartist", audio.values)

    def test_noop_leaves_bytes_unchanged(self):
        files = self.files()
        before = {path: digest(path) for path in self.paths}
        changes = tag_edit.parse_changes(tag_edit.render(files), files)
        self.assertEqual(changes, [])
        tag_edit.apply_changes(changes)
        self.assertEqual({path: digest(path) for path in self.paths}, before)

    def test_mp3_multivalue_requires_id3v24(self):
        path = next(path for path in self.paths if path.suffix == ".mp3")
        before = digest(path)
        with self.assertRaisesRegex(tag_edit.EditError, "cannot be stored losslessly"):
            tag_edit.apply_changes([(tag_edit.load_audio(path), {"artist": ["Alice", "Bob"]})])
        self.assertEqual(digest(path), before)
        raw = mutagen.File(path)
        raw.save(v2_version=4)
        tag_edit.apply_changes([(tag_edit.load_audio(path), {"artist": ["Alice", "Bob"]})])
        self.assertEqual(tag_edit.load_audio(path).values["artist"], ["Alice", "Bob"])

    def test_failed_staging_does_not_change_any_original(self):
        files = sorted(self.files(), key=lambda audio: audio.kind == "m4a")
        before = {path: digest(path) for path in self.paths}
        changes = [
            (audio, {"track": ["999999"]} if audio.kind == "m4a" else {"title": ["New"]})
            for audio in files
        ]
        with self.assertRaisesRegex(tag_edit.EditError, "no originals updated"):
            tag_edit.apply_changes(changes)
        self.assertEqual({path: digest(path) for path in self.paths}, before)
        self.assertFalse(list(self.directory.glob(".*.radish-*")))

    def test_concurrent_modification_rejected(self):
        audio = self.files()[0]
        audio.path.touch()
        with self.assertRaisesRegex(tag_edit.EditError, "changed since opening"):
            tag_edit.apply_changes([(audio, {"title": ["New"]})])

    def test_partial_commit_reports_updated_files_and_cleans_staging(self):
        files = self.files()
        changes = [(audio, {"title": ["New"]}) for audio in files]
        real_replace = os.replace
        calls = 0

        def replace(source, destination):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated replacement failure")
            real_replace(source, destination)

        with patch.object(tag_edit.os, "replace", side_effect=replace):
            with self.assertRaisesRegex(tag_edit.EditError, "already updated") as raised:
                tag_edit.apply_changes(changes)
        self.assertIn(str(files[0].path), str(raised.exception))
        self.assertEqual(tag_edit.load_audio(files[0].path).values["title"], ["New"])
        self.assertEqual(tag_edit.load_audio(files[1].path).values["title"], ["Original"])
        self.assertFalse(list(self.directory.glob(".*.radish-*")))

    def test_symlink_resolves_to_original_target(self):
        link = self.directory / "link.mp3"
        link.symlink_to(self.paths[0])
        audio = tag_edit.read_files([str(link)])[0]
        tag_edit.apply_changes([(audio, {"title": ["New"]})])
        self.assertTrue(link.is_symlink())
        self.assertEqual(tag_edit.load_audio(self.paths[0]).values["title"], ["New"])

    def test_duplicate_and_hardlinked_files_rejected(self):
        path = self.paths[0]
        with self.assertRaisesRegex(tag_edit.EditError, "more than once"):
            tag_edit.read_files([str(path), str(path)])
        os.link(path, self.directory / "hardlink")
        with self.assertRaisesRegex(tag_edit.EditError, "hard-linked"):
            tag_edit.read_files([str(path)])

    def test_cli_export_apply_and_dry_run(self):
        result = CliRunner().invoke(
            tag_edit.cli, ["edit", "--print", *map(str, self.paths)],
        )
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            len(list(yaml.safe_load_all(result.output))),
            len(self.paths),
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(tag_edit.main(["--print", *map(str, self.paths)]), 0)
        documents = list(yaml.safe_load_all(output.getvalue()))
        for document in documents:
            document["title"] = "CLI title"
        exported = self.directory / "tags.yaml"
        exported.write_text(yaml.safe_dump_all(documents), encoding="utf-8")
        before = {path: digest(path) for path in self.paths}
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(tag_edit.main([
                "--apply", str(exported), "--dry-run", *map(str, self.paths),
            ]), 0)
        self.assertEqual({path: digest(path) for path in self.paths}, before)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(tag_edit.main([
                "--apply", str(exported), *map(str, self.paths),
            ]), 0)
        self.assertTrue(all(audio.values["title"] == ["CLI title"] for audio in self.files()))

    def test_artists_lists_unique_values_and_accepts_path_overrides(self):
        for path, artist in zip(self.paths, ("Beta Artist", "Alpha Artist", "Beta Artist", "  ")):
            audio = mutagen.File(path, easy=True)
            audio["artist"] = [artist]
            audio.save()
        nested = self.directory / "nested"
        nested.mkdir()
        moved = nested / self.paths[0].name
        shutil.move(self.paths[0], moved)

        result = CliRunner().invoke(tag_edit.cli, ["artists", str(self.directory)])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.output.splitlines(), ["Alpha Artist", "Beta Artist"])

        result = CliRunner().invoke(
            tag_edit.cli, ["artists", str(moved), str(self.paths[1])],
        )
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.output.splitlines(), ["Alpha Artist", "Beta Artist"])

    def test_artists_uses_configured_library_and_reports_invalid_paths(self):
        result = CliRunner().invoke(
            tag_edit.cli, ["artists"], env={"RADISH_LIBRARY": str(self.directory)},
        )
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.output.splitlines(), ["Original Artist"])

        result = CliRunner().invoke(
            tag_edit.cli, ["artists", str(self.directory / "missing")],
        )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("path does not exist", result.output)

    def test_real_editor_process(self):
        editor = self.directory / "fake editor.py"
        editor.write_text(
            "import pathlib, sys\n"
            "p = pathlib.Path(sys.argv[1])\n"
            "p.write_text(p.read_text().replace('title: Original', 'title: Edited'))\n",
            encoding="utf-8",
        )
        with contextlib.redirect_stdout(io.StringIO()):
            code = tag_edit.main([
                "--editor", f"{shlex.quote(sys.executable)} {shlex.quote(str(editor))}",
                str(self.paths[0]),
            ])
        self.assertEqual(code, 0)
        self.assertEqual(tag_edit.load_audio(self.paths[0]).values["title"], ["Edited"])

    def test_quit_without_saving_changes_nothing(self):
        before = digest(self.paths[0])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(tag_edit.main([
                "--editor", f'{shlex.quote(sys.executable)} -c "pass"',
                str(self.paths[0]),
            ]), 0)
        self.assertEqual(digest(self.paths[0]), before)

    def test_editor_failure_retains_buffer_and_changes_nothing(self):
        before = digest(self.paths[0])
        stderr = io.StringIO()
        real_mkstemp = tempfile.mkstemp
        with patch.object(tag_edit.tempfile, "mkstemp", side_effect=lambda **kwargs:
                          real_mkstemp(**{**kwargs, "dir": self.directory})):
            with contextlib.redirect_stderr(stderr):
                code = tag_edit.main([
                    "--editor", f'{shlex.quote(sys.executable)} -c "import sys; sys.exit(7)"',
                    str(self.paths[0]),
                ])
        self.assertEqual(code, 1)
        self.assertIn("status 7", stderr.getvalue())
        self.assertEqual(digest(self.paths[0]), before)
        self.assertEqual(len(list(self.directory.glob("radish-*.yaml"))), 1)

    def test_unsupported_and_corrupt_inputs_fail(self):
        path = self.directory / "bad.mp3"
        path.write_bytes(b"not audio")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(tag_edit.main(["--print", str(path)]), 1)
            self.assertEqual(tag_edit.main(["--print", str(self.directory / "missing")]), 1)


if __name__ == "__main__":
    unittest.main()
