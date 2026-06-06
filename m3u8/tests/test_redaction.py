import unittest

from app.core.redaction import REDACTED, redact_headers, redact_text, redact_url


class RedactionTests(unittest.TestCase):
    def test_redact_headers_masks_sensitive_names_case_insensitively(self) -> None:
        headers = {
            "Cookie": "session=abc",
            "Authorization": "Bearer secret",
            "User-Agent": "LectureBot",
            "x-api-key": "key",
        }

        redacted = redact_headers(headers)

        self.assertEqual(redacted["Cookie"], REDACTED)
        self.assertEqual(redacted["Authorization"], REDACTED)
        self.assertEqual(redacted["x-api-key"], REDACTED)
        self.assertEqual(redacted["User-Agent"], "LectureBot")

    def test_redact_url_masks_signed_query_params(self) -> None:
        url = "https://cdn.example.com/master.m3u8?token=abc&Policy=p&auth_key=k&hdntl=h&quality=720"

        redacted = redact_url(url)

        self.assertIn("token=%5BREDACTED%5D", redacted)
        self.assertIn("Policy=%5BREDACTED%5D", redacted)
        self.assertIn("auth_key=%5BREDACTED%5D", redacted)
        self.assertIn("hdntl=%5BREDACTED%5D", redacted)
        self.assertIn("quality=720", redacted)
        self.assertNotIn("abc", redacted)
        self.assertNotIn("auth_key=k", redacted)
        self.assertNotIn("hdntl=h", redacted)

    def test_redact_text_masks_headers_and_urls(self) -> None:
        text = (
            "Authorization: Bearer secret\n"
            "Cookie=session=abc\n"
            "GET https://cdn.example.com/seg.ts?signature=sig&expires=1\n"
        )

        redacted = redact_text(text)

        self.assertIn(f"Authorization: {REDACTED}", redacted)
        self.assertIn(f"Cookie={REDACTED}", redacted)
        self.assertNotIn("Bearer secret", redacted)
        self.assertNotIn("signature=sig", redacted)
        self.assertNotIn("expires=1", redacted)


if __name__ == "__main__":
    unittest.main()
