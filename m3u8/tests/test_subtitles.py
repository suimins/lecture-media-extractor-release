import unittest

from app.core.playlist import SubtitleTrack
from app.core.subtitles import (
    build_subtitle_extract_command,
    evaluate_subtitle_quality,
    format_ffmpeg_headers,
    parse_srt,
    parse_vtt,
    select_subtitle_track,
    should_fallback_to_stt,
    transcript_text,
)
from app.models.settings import FfmpegConfig, SubtitleQualityThresholds


def track(
    track_id: str,
    *,
    language: str | None = None,
    default: bool = False,
    autoselect: bool = False,
    forced: bool = False,
    uri: str | None = "https://cdn.example.com/subs.m3u8",
) -> SubtitleTrack:
    return SubtitleTrack(
        track_id=track_id,
        group_id="subs",
        name=track_id,
        language=language,
        uri=uri,
        default=default,
        autoselect=autoselect,
        forced=forced,
        raw_attributes={},
    )


class SubtitleSelectionTests(unittest.TestCase):
    def test_auto_selection_prefers_language_over_default(self) -> None:
        tracks = [
            track("en", language="en", default=True, autoselect=True),
            track("ko", language="ko", default=False, autoselect=True),
        ]

        selection = select_subtitle_track(tracks, preferred_language="ko")

        self.assertEqual(selection.track.track_id, "ko")
        self.assertEqual(selection.reason, "auto selected")

    def test_auto_selection_excludes_forced_when_non_forced_exists(self) -> None:
        tracks = [
            track("ko-forced", language="ko", forced=True, default=True),
            track("ko-full", language="ko", forced=False),
        ]

        selection = select_subtitle_track(tracks, preferred_language="ko")

        self.assertEqual(selection.track.track_id, "ko-full")

    def test_manual_selection_requires_matching_id(self) -> None:
        tracks = [track("ko", language="ko")]

        selection = select_subtitle_track(tracks, mode="manual", selected_track_id="ko")

        self.assertEqual(selection.track.track_id, "ko")

    def test_skip_mode_returns_no_track(self) -> None:
        selection = select_subtitle_track([track("ko")], mode="skip")

        self.assertIsNone(selection.track)
        self.assertEqual(selection.reason, "subtitle_mode=skip")

    def test_tracks_without_uri_are_ignored(self) -> None:
        selection = select_subtitle_track([track("ko", uri=None)])

        self.assertIsNone(selection.track)
        self.assertIn("URI 없는", selection.warnings[0])


class SubtitleCommandTests(unittest.TestCase):
    def test_build_subtitle_extract_command_includes_headers_and_redacts_command_copy(self) -> None:
        command = build_subtitle_extract_command(
            "https://cdn.example.com/subs.m3u8?token=secret",
            "out.srt",
            config=FfmpegConfig(ffmpeg_path="/opt/homebrew/bin/ffmpeg"),
            headers={"Cookie": "session=abc", "Referer": "https://example.com"},
            output_format="srt",
        )

        self.assertEqual(command.args[0], "/opt/homebrew/bin/ffmpeg")
        self.assertIn("-headers", command.args)
        self.assertIn("Cookie: session=abc\r\nReferer: https://example.com\r\n", command.args)
        self.assertIn("-c:s", command.args)
        self.assertIn("srt", command.args)
        self.assertNotIn("token=secret", " ".join(command.redacted_args))

    def test_format_ffmpeg_headers_uses_crlf(self) -> None:
        header_blob = format_ffmpeg_headers({"Cookie": "a=b", "User-Agent": "UA"})

        self.assertEqual(header_blob, "Cookie: a=b\r\nUser-Agent: UA\r\n")


class SubtitleParserTests(unittest.TestCase):
    def test_parse_srt_extracts_segments_and_strips_tags(self) -> None:
        srt = """1
00:00:01,000 --> 00:00:03,500
<b>안녕하세요</b>

2
00:00:04,000 --> 00:00:05,000
두 번째 줄
"""

        segments = parse_srt(srt)

        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0].start, 1.0)
        self.assertEqual(segments[0].end, 3.5)
        self.assertEqual(segments[0].text, "안녕하세요")
        self.assertEqual(transcript_text(segments), "안녕하세요\n두 번째 줄")

    def test_parse_vtt_supports_identifier_before_timing(self) -> None:
        vtt = """WEBVTT

cue-1
00:00:01.000 --> 00:00:03.000 align:start
Hello

00:00:04.000 --> 00:00:06.250
World
"""

        segments = parse_vtt(vtt)

        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0].start, 1.0)
        self.assertEqual(segments[1].end, 6.25)


class SubtitleQualityTests(unittest.TestCase):
    def test_quality_ok_for_dense_subtitles(self) -> None:
        srt = "\n\n".join(
            f"{index}\n00:00:{index:02d},000 --> 00:00:{index + 1:02d},000\n이것은 충분히 긴 자막 문장입니다 {index}"
            for index in range(1, 15)
        )
        segments = parse_srt(srt)

        quality = evaluate_subtitle_quality(segments, duration_seconds=60)

        self.assertEqual(quality.status, "ok")
        self.assertFalse(should_fallback_to_stt(quality, allow_stt_fallback=True))

    def test_quality_failed_for_empty_subtitles(self) -> None:
        quality = evaluate_subtitle_quality([], duration_seconds=60)

        self.assertEqual(quality.status, "failed")
        self.assertTrue(should_fallback_to_stt(quality, allow_stt_fallback=True))

    def test_quality_suspect_for_low_coverage(self) -> None:
        segments = parse_srt("1\n00:00:01,000 --> 00:00:02,000\n짧음")

        quality = evaluate_subtitle_quality(
            segments,
            duration_seconds=600,
            thresholds=SubtitleQualityThresholds(min_text_chars=1),
        )

        self.assertEqual(quality.status, "suspect")
        self.assertTrue(any("coverage_ratio" in reason for reason in quality.reasons))

    def test_fallback_can_be_disabled(self) -> None:
        quality = evaluate_subtitle_quality([], duration_seconds=60)

        self.assertFalse(should_fallback_to_stt(quality, allow_stt_fallback=False))


if __name__ == "__main__":
    unittest.main()

