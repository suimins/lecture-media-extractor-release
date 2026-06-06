import unittest

from app.core.playlist import analyze_playlist_text, parse_attribute_list


MASTER_PLAYLIST = """#EXTM3U
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",LANGUAGE="ko",NAME="Korean",DEFAULT=YES,AUTOSELECT=YES,FORCED=NO,URI="subs/ko/prog.m3u8"
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",LANGUAGE="en",NAME="English, CC",DEFAULT=NO,AUTOSELECT=YES,FORCED=NO,URI="https://cdn.example.com/en.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1280x720,CODECS="avc1.64001f,mp4a.40.2",AUDIO="aud",SUBTITLES="subs"
video/720/prog.m3u8
"""

MEDIA_PLAYLIST = """#EXTM3U
#EXT-X-TARGETDURATION:10
#EXTINF:9.5,
seg0.ts
#EXTINF:10.0,
seg1.ts
"""

VOD_PLAYLIST = """#EXTM3U
#EXT-X-TARGETDURATION:10
#EXTINF:9.5,
seg0.ts
#EXT-X-ENDLIST
"""

EVENT_PLAYLIST = """#EXTM3U
#EXT-X-PLAYLIST-TYPE:EVENT
#EXT-X-TARGETDURATION:10
#EXTINF:9.5,
seg0.ts
"""


class PlaylistTests(unittest.TestCase):
    def test_parse_attribute_list_preserves_commas_inside_quotes(self) -> None:
        attrs = parse_attribute_list('LANGUAGE="en",NAME="English, CC",DEFAULT=YES')

        self.assertEqual(attrs["LANGUAGE"], "en")
        self.assertEqual(attrs["NAME"], "English, CC")
        self.assertEqual(attrs["DEFAULT"], "YES")

    def test_master_playlist_extracts_subtitles_and_variants(self) -> None:
        analysis = analyze_playlist_text(MASTER_PLAYLIST, source_url="https://cdn.example.com/master.m3u8")

        self.assertEqual(analysis.kind, "master")
        self.assertEqual(analysis.playlist_type, "unknown")
        self.assertEqual(len(analysis.subtitle_tracks), 2)
        self.assertEqual(analysis.subtitle_tracks[0].uri, "https://cdn.example.com/subs/ko/prog.m3u8")
        self.assertEqual(analysis.subtitle_tracks[1].name, "English, CC")
        self.assertFalse(analysis.subtitle_tracks[0].forced)
        self.assertEqual(len(analysis.variant_streams), 1)
        self.assertEqual(analysis.variant_streams[0].subtitle_group, "subs")

    def test_media_playlist_warns_about_missing_master_subtitle_metadata(self) -> None:
        analysis = analyze_playlist_text(MEDIA_PLAYLIST, source_url="https://cdn.example.com/video/prog.m3u8")

        self.assertEqual(analysis.kind, "media")
        self.assertEqual(analysis.playlist_type, "live_or_open")
        self.assertEqual(analysis.media_segment_count, 2)
        self.assertEqual(analysis.duration_seconds, 19.5)
        self.assertIn("media playlist 입력", analysis.warnings[0])

    def test_vod_playlist_detects_endlist(self) -> None:
        analysis = analyze_playlist_text(VOD_PLAYLIST)

        self.assertEqual(analysis.playlist_type, "vod")
        self.assertTrue(analysis.has_endlist)

    def test_event_playlist_detects_event_type(self) -> None:
        analysis = analyze_playlist_text(EVENT_PLAYLIST)

        self.assertEqual(analysis.playlist_type, "event")
        self.assertTrue(any("live/event/open" in warning for warning in analysis.warnings))

    def test_invalid_playlist_is_unknown(self) -> None:
        analysis = analyze_playlist_text("not a playlist")

        self.assertFalse(analysis.is_m3u8)
        self.assertEqual(analysis.kind, "unknown")
        self.assertEqual(analysis.playlist_type, "unknown")


if __name__ == "__main__":
    unittest.main()

