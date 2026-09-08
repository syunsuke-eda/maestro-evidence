from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import support

from melib.secretscan import redact_text, scan_directory


class RedactTest(unittest.TestCase):
    def test_credentialの実値を置換する(self):
        redacted, kinds = redact_text("email=tester@example.com pass=s3cret", ["s3cret"])
        self.assertNotIn("s3cret", redacted)
        self.assertIn("credential", kinds)

    def test_Bearerトークンを置換する(self):
        redacted, kinds = redact_text("Authorization: Bearer abc.def.ghi", [])
        self.assertEqual(redacted, "Authorization: Bearer [REDACTED]")
        self.assertIn("bearer", kinds)

    def test_id_tokenのJSONを置換する(self):
        redacted, kinds = redact_text('{"id_token": "eyJhbGciOi"}', [])
        self.assertIn("[REDACTED]", redacted)
        self.assertNotIn("eyJhbGciOi", redacted)
        self.assertIn("token_json", kinds)

    def test_クエリのaccess_tokenを置換する(self):
        redacted, _ = redact_text("https://api.example.com/x?access_token=zzz&a=1", [])
        self.assertIn("access_token=[REDACTED]", redacted)
        self.assertIn("&a=1", redacted)

    def test_署名付きURLのX_Amz_Signatureを置換する(self):
        redacted, kinds = redact_text("https://s3/x?X-Amz-Signature=deadbeef&y=2", [])
        self.assertIn("X-Amz-Signature=[REDACTED]", redacted)
        self.assertIn("signed_url", kinds)

    def test_Signatureクエリも置換する(self):
        redacted, kinds = redact_text("https://cdn/x?Signature=abc", [])
        self.assertIn("Signature=[REDACTED]", redacted)
        self.assertIn("signature_query", kinds)

    def test_該当が無ければ変更しない(self):
        original = "flutter: ok"
        redacted, kinds = redact_text(original, ["s3cret"])
        self.assertEqual(redacted, original)
        self.assertEqual(kinds, [])


class ScanDirectoryTest(unittest.TestCase):
    def test_テキスト成果物を上書きして違反を返す(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = support.write(root / "cases" / "a" / "log.txt", "pass=s3cret\n")
            support.write(root / "cases" / "a" / "clean.txt", "ok\n")
            violations = scan_directory(root, ["s3cret"])
            self.assertEqual(len(violations), 1)
            self.assertEqual(violations[0]["file"], "cases/a/log.txt")
            self.assertNotIn("s3cret", target.read_text(encoding="utf-8"))

    def test_バイナリ拡張子は触らない(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "video.mp4").write_bytes(b"s3cret-binary")
            self.assertEqual(scan_directory(root, ["s3cret"]), [])
            self.assertEqual((root / "video.mp4").read_bytes(), b"s3cret-binary")

    def test_再走査で違反が残らない(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            support.write(root / "log.txt", "Authorization: Bearer abc\n")
            scan_directory(root, [])
            self.assertEqual(scan_directory(root, []), [])


if __name__ == "__main__":
    unittest.main()
