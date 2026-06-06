import unittest

from app.core.ffmpeg import parse_formats, parse_protocols


class FfmpegParserTests(unittest.TestCase):
    def test_parse_protocols_collects_input_and_output_protocols(self) -> None:
        output = """
Supported file protocols:
Input:
  async
  cache
  file
  http
  https
  tls
Output:
  file
  crypto
"""

        protocols = parse_protocols(output)

        self.assertTrue({"file", "http", "https", "tls", "crypto"} <= protocols)

    def test_parse_formats_splits_comma_separated_aliases(self) -> None:
        output = """
File formats:
 D  hls             Apple HTTP Live Streaming
 DE mov,mp4,m4a     QuickTime / MOV
 DE webvtt          WebVTT subtitle
 DE srt             SubRip subtitle
"""

        formats = parse_formats(output)

        self.assertTrue({"hls", "mov", "mp4", "webvtt", "srt"} <= formats)


if __name__ == "__main__":
    unittest.main()

