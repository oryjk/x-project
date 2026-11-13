import importlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zlib

from x_project import content
from x_project.content import ContentError, Draft, validate_draft
from x_project.research import CodexResearcher, SourceVerifier, output_schema
from test_content import NOW, draft


def note(body="模型更新值得关注，但落地还要看成本和可靠性。", title="模型更新的实际意义"):
    item = draft(body)
    item.platform = "xiaohongshu"
    item.title = title
    item.images = []
    return item


def png_bytes():
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00")) + chunk(b"IEND", b""))


class PlatformDraftTest(unittest.TestCase):
    def test_old_rows_gain_independent_defaults_and_keep_original_text(self):
        old = {"body": "已核对的科技信息", "topic": "事件", "sources": [draft().sources[0].__dict__]}
        first = Draft.from_dict(old)
        second = Draft.from_dict(old)
        self.assertEqual(getattr(first, "platform", None), "x")
        self.assertEqual(getattr(first, "title", None), "")
        self.assertEqual(getattr(first, "images", None), [])
        first.images.append("one.png")
        self.assertEqual(second.images, [])
        self.assertEqual(first.text, "已核对的科技信息\nhttps://example.org/release")

    def test_note_round_trip_keeps_title_and_images_separate_from_body(self):
        item = note()
        item.images = ["/tmp/cover.png"]
        decoded = Draft.from_dict(item.to_dict())
        self.assertEqual(decoded.platform, "xiaohongshu")
        self.assertEqual(decoded.title, "模型更新的实际意义")
        self.assertEqual(decoded.images, ["/tmp/cover.png"])
        self.assertNotIn(decoded.title, decoded.text)

    def test_x_budget_is_retained_while_notes_use_character_count(self):
        with self.assertRaises(ContentError):
            validate_draft(draft("模" * 140), NOW)
        self.assertEqual(validate_draft(note("模" * 500), NOW).platform, "xiaohongshu")

    def test_note_title_and_body_include_exact_conservative_limits(self):
        item = note("a" * 971, "标" * 20)
        self.assertEqual(len(item.text), 999)
        self.assertIs(validate_draft(item, NOW), item)
        item.body += "a"
        self.assertEqual(len(item.text), 1000)
        validate_draft(item, NOW)
        item.body += "a"
        with self.assertRaisesRegex(ContentError, "1000"):
            validate_draft(item, NOW)
        for title in ("", "标" * 21, "标题\n正文", "https://example.org", "标题\x00"):
            with self.subTest(title=title), self.assertRaises(ContentError):
                validate_draft(note(title=title), NOW)

    def test_unknown_platform_and_malformed_media_metadata_are_rejected(self):
        item = note()
        for platform in ("unknown", "X", None):
            item.platform = platform
            with self.subTest(platform=platform), self.assertRaises(ContentError):
                validate_draft(item, NOW)
        for images in ("cover.png", [123], ["one.png"] * 10):
            item = note()
            item.images = images
            with self.subTest(images=images), self.assertRaises(ContentError):
                validate_draft(item, NOW)

    def test_notes_keep_fresh_source_and_verbatim_verification_requirements(self):
        item = note("模" * 500)
        item.sources[0].published_at = "2020-01-01T00:00:00Z"
        with self.assertRaises(ContentError):
            validate_draft(item, NOW)
        item = note()
        page = '<meta property="article:published_time" content="2026-11-13"><p>Different article facts</p>'
        with patch("x_project.research.fetch", return_value=page), self.assertRaises(ContentError):
            SourceVerifier().verify(item)


class NoteMediaTest(unittest.TestCase):
    def validate_media(self, item):
        validator = getattr(content, "validate_note_media", None)
        self.assertTrue(callable(validator), "local image validator is required")
        return validator(item)

    def cards(self):
        self.assertIsNotNone(importlib.util.find_spec("x_project.cards"), "optional cover renderer is required")
        return importlib.import_module("x_project.cards")

    def test_one_to_nine_local_images_are_required_at_media_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cover.png"
            path.write_bytes(png_bytes())
            item = note()
            with self.assertRaises(ContentError):
                self.validate_media(item)
            item.images = [str(path)]
            self.assertIs(self.validate_media(item), item)
            item.images *= 9
            self.validate_media(item)
            item.images.append(str(path))
            with self.assertRaises(ContentError):
                self.validate_media(item)

    def test_nonlocal_missing_and_disguised_files_are_rejected_without_downloads(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = root / "disguised.png"
            secret.write_text("synthetic private text, not an image")
            wrong_extension = root / "cover.txt"
            wrong_extension.write_bytes(png_bytes())
            truncated = root / "truncated.png"
            truncated.write_bytes(b"\x89PNG\r\n\x1a\n")
            for path in ("https://example.org/cover.png", str(root / "missing.jpg"), str(root), str(secret), str(wrong_extension), str(truncated)):
                item = note()
                item.images = [path]
                with self.subTest(path=path), self.assertRaises(ContentError):
                    self.validate_media(item)

    def test_supplied_images_are_preserved_without_loading_optional_pillow(self):
        cards = self.cards()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cover.png"
            path.write_bytes(png_bytes())
            item = note()
            item.images = [str(path)]
            with patch.dict(sys.modules, {"PIL": None}):
                result = cards.ensure_note_images(item, Path(directory) / "state")
            self.assertEqual(result.images, [str(path)])
            self.assertFalse((Path(directory) / "state").exists())

    def test_png_with_image_signature_but_corrupted_chunks_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "corrupt.png"
            raw = bytearray(png_bytes())
            raw[29] ^= 1  # IHDR checksum, while signature/dimensions remain valid.
            path.write_bytes(raw)
            item = note()
            item.images = [str(path)]
            with self.assertRaises(ContentError):
                self.validate_media(item)

    @unittest.skipUnless(importlib.util.find_spec("PIL"), "optional Pillow unavailable")
    def test_real_jpeg_is_accepted_and_wrong_extension_is_refused(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cover.JPEG"
            Image.new("RGB", (8, 8), "#123c3c").save(path, format="JPEG")
            item = note()
            item.images = [str(path)]
            with patch.dict(sys.modules, {"PIL": None}):
                self.validate_media(item)
            wrong_extension = path.with_suffix(".png")
            wrong_extension.write_bytes(path.read_bytes())
            item.images = [str(wrong_extension)]
            with self.assertRaises(ContentError):
                self.validate_media(item)

    @unittest.skipUnless(importlib.util.find_spec("PIL"), "optional Pillow unavailable")
    def test_latin_only_font_and_output_symlink_cannot_create_cover(self):
        cards = self.cards()
        cjk = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
        latin = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
        if not cjk.is_file() or not latin.is_file():
            self.skipTest("fixture fonts unavailable")
        with tempfile.TemporaryDirectory() as directory, patch("x_project.cards.utcnow", return_value=NOW):
            state = Path(directory) / "state"
            with self.assertRaisesRegex(ContentError, "字体"):
                cards.ensure_note_images(note(), state, str(latin))
            outside = Path(directory) / "outside"
            outside.mkdir()
            state.mkdir()
            (state / "note-cards").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ContentError):
                cards.ensure_note_images(note(), state, str(cjk))
            self.assertEqual(list(outside.iterdir()), [])

    def test_cover_missing_pillow_fails_clearly(self):
        cards = self.cards()
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(sys.modules, {"PIL": None}), self.assertRaisesRegex(ContentError, "Pillow"):
                cards.ensure_note_images(note(), directory)

    @unittest.skipUnless(importlib.util.find_spec("PIL"), "optional Pillow unavailable")
    def test_cover_missing_cjk_font_fails_clearly(self):
        cards = self.cards()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ContentError, "字体"):
                cards.ensure_note_images(note(), directory, str(Path(directory) / "missing.ttf"))

    @unittest.skipUnless(importlib.util.find_spec("PIL"), "optional Pillow unavailable")
    def test_cover_is_real_png_inside_state_and_keeps_sourced_note(self):
        cards = self.cards()
        from PIL import Image
        font = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
        if not font.is_file():
            self.skipTest("CJK font unavailable")
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            item = note("模" * 800)
            with patch("x_project.cards.utcnow", return_value=NOW):
                result = cards.ensure_note_images(item, state, str(font))
            self.assertEqual(item.images, [], "renderer should return an enriched draft")
            self.assertEqual(result.title, item.title)
            self.assertEqual(result.text, item.text)
            self.assertEqual(len(result.images), 1)
            path = Path(result.images[0])
            self.assertTrue(path.resolve().is_relative_to(state.resolve()))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.validate_media(result)
            with Image.open(path) as picture:
                picture.verify()
                self.assertEqual(picture.format, "PNG")
                self.assertGreaterEqual(picture.width, 1080)


class PlatformResearchTest(unittest.TestCase):
    def test_schema_requires_note_title_and_retains_legacy_x_schema(self):
        x_schema = output_schema(3)["properties"]["items"]["items"]
        self.assertNotIn("title", x_schema["properties"])
        schema = output_schema(3, platform="xiaohongshu")["properties"]["items"]["items"]
        self.assertIn("title", schema["required"])
        self.assertEqual(schema["properties"]["title"]["maxLength"], 20)
        self.assertEqual(schema["properties"]["body"]["maxLength"], 1000)
        self.assertNotIn("images", schema["properties"])
        with self.assertRaises(ContentError):
            output_schema(1, platform="unknown")

    def test_note_model_gets_platform_prompt_but_no_device_environment_or_media_paths(self):
        captured = {}
        item = draft().to_dict()
        item.update(title="模型更新的实际意义", images=["/private/device.png"], platform="x")
        def run(args, **kwargs):
            captured.update(env=kwargs["env"], prompt=kwargs["input"], args=args,
                            schema=json.loads(Path(args[args.index("--output-schema") + 1]).read_text()))
            Path(args[args.index("--output-last-message") + 1]).write_text(json.dumps({"items": [item]}))
            event = {"type": "item.completed", "item": {"type": "web_search", "action": {"type": "search"}}}
            return SimpleNamespace(returncode=0, stdout=json.dumps(event), stderr="")
        secrets = {"X_DEVICE_PHONE_A_PHONE_PIN": "synthetic-pin", "X_DEVICE_PHONE_B_PHONE_PATTERN": "synthetic-pattern",
                   "X_DEVICE_PHONE_A_ADB_PAIR_CODE": "synthetic-pair", "X_PROFILE_NOTE_IMAGES": "synthetic-image-path"}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, secrets), patch("x_project.research.subprocess.run", side_effect=run):
            result = CodexResearcher(executable=sys.executable, audit_dir=directory, platform="xiaohongshu").generate(1, [], "专业中文")
        self.assertEqual(result[0].platform, "xiaohongshu")
        self.assertEqual(result[0].title, "模型更新的实际意义")
        self.assertEqual(result[0].images, [])
        self.assertTrue(set(secrets).isdisjoint(captured["env"]))
        self.assertIn("小红书", captured["prompt"])
        self.assertIn("1000", captured["prompt"])
        self.assertNotIn("不超过280", captured["prompt"])
        for value in secrets.values():
            self.assertNotIn(value, captured["prompt"])
        pairs = [captured["args"][i:i+2] for i in range(len(captured["args"]) - 1)]
        self.assertIn(["--enable", "code_mode_host"], pairs)
        self.assertIn(["--disable", "shell_tool"], pairs)
        self.assertIn(["--disable", "computer_use"], pairs)


if __name__ == "__main__":
    unittest.main()
