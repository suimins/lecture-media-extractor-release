"""Minimal HLS playlist analysis used before extraction."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx

from app.core.errors import PlaylistError
from app.models.settings import PlaylistKind, PlaylistType, RetryConfig


@dataclass(slots=True)
class SubtitleTrack:
    track_id: str
    group_id: str | None
    name: str | None
    language: str | None
    uri: str | None
    default: bool
    autoselect: bool
    forced: bool
    raw_attributes: dict[str, str]


@dataclass(slots=True)
class VariantStream:
    uri: str | None
    bandwidth: int | None
    resolution: str | None
    codecs: str | None
    audio_group: str | None
    subtitle_group: str | None
    raw_attributes: dict[str, str]


@dataclass(slots=True)
class PlaylistAnalysis:
    source_url: str | None
    kind: PlaylistKind
    playlist_type: PlaylistType
    is_m3u8: bool
    has_endlist: bool
    target_duration: float | None
    duration_seconds: float | None
    variant_streams: list[VariantStream] = field(default_factory=list)
    subtitle_tracks: list[SubtitleTrack] = field(default_factory=list)
    media_segment_count: int = 0
    media_segments: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def has_subtitles(self) -> bool:
        return bool(self.subtitle_tracks)


def fetch_playlist_text(url: str, *, headers: dict[str, str] | None = None, retry: RetryConfig | None = None) -> str:
    """Fetch a playlist with timeout and retry/backoff policy."""

    retry = retry or RetryConfig()
    last_error: Exception | None = None
    attempts = max(1, retry.max_attempts)

    for attempt in range(1, attempts + 1):
        try:
            response = httpx.get(url, headers=headers or {}, timeout=retry.timeout_seconds, follow_redirects=True)
            response.raise_for_status()
            return response.text
        except httpx.HTTPStatusError as exc:
            last_error = exc
            if exc.response.status_code not in retry.retry_status_codes or attempt == attempts:
                break
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt == attempts:
                break

        time.sleep(retry.backoff_seconds * attempt)

    detail = str(last_error) if last_error else "unknown fetch error"
    raise PlaylistError("m3u8 playlist 다운로드 실패", detail=detail)


def analyze_playlist_url(url: str, *, headers: dict[str, str] | None = None, retry: RetryConfig | None = None) -> PlaylistAnalysis:
    """Fetch and analyze an m3u8 playlist URL."""

    text = fetch_playlist_text(url, headers=headers, retry=retry)
    return analyze_playlist_text(text, source_url=url)


def analyze_playlist_text(text: str, *, source_url: str | None = None) -> PlaylistAnalysis:
    """Analyze a playlist string without fetching nested resources."""

    lines = _meaningful_lines(text)
    is_m3u8 = bool(lines and lines[0].startswith("#EXTM3U"))
    if not is_m3u8:
        return PlaylistAnalysis(
            source_url=source_url,
            kind="unknown",
            playlist_type="unknown",
            is_m3u8=False,
            has_endlist=False,
            target_duration=None,
            duration_seconds=None,
            warnings=["입력 텍스트가 #EXTM3U로 시작하지 않음"],
        )

    kind = _detect_playlist_kind(lines)
    playlist_type = _detect_playlist_type(lines, kind)
    has_endlist = any(line.startswith("#EXT-X-ENDLIST") for line in lines)
    target_duration = _parse_target_duration(lines)
    duration_seconds = _parse_duration(lines)
    variant_streams = _parse_variant_streams(lines, source_url)
    subtitle_tracks = _parse_subtitle_tracks(lines, source_url)
    media_segments = _parse_media_segments(lines, source_url)

    warnings: list[str] = []
    if kind == "media":
        warnings.append("media playlist 입력: master-level 자막 메타데이터가 없을 수 있음")
    if playlist_type in {"event", "live_or_open"}:
        warnings.append("live/event/open playlist 입력: 전체 길이와 진행률이 부정확할 수 있음")

    return PlaylistAnalysis(
        source_url=source_url,
        kind=kind,
        playlist_type=playlist_type,
        is_m3u8=True,
        has_endlist=has_endlist,
        target_duration=target_duration,
        duration_seconds=duration_seconds,
        variant_streams=variant_streams,
        subtitle_tracks=subtitle_tracks,
        media_segment_count=len(media_segments),
        media_segments=media_segments,
        warnings=warnings,
    )


def parse_attribute_list(raw: str) -> dict[str, str]:
    """Parse an HLS attribute list, preserving quoted values containing commas."""

    attrs: dict[str, str] = {}
    index = 0
    length = len(raw)

    while index < length:
        while index < length and raw[index] in " ,\t":
            index += 1
        if index >= length:
            break

        key_start = index
        while index < length and raw[index] not in "=,":
            index += 1
        key = raw[key_start:index].strip()
        if not key or index >= length or raw[index] != "=":
            while index < length and raw[index] != ",":
                index += 1
            continue

        index += 1
        if index < length and raw[index] == '"':
            index += 1
            value_chars: list[str] = []
            while index < length:
                char = raw[index]
                if char == '"':
                    index += 1
                    break
                value_chars.append(char)
                index += 1
            value = "".join(value_chars)
        else:
            value_start = index
            while index < length and raw[index] != ",":
                index += 1
            value = raw[value_start:index].strip()

        attrs[key.upper()] = value

        while index < length and raw[index] != ",":
            index += 1
        if index < length and raw[index] == ",":
            index += 1

    return attrs


def _meaningful_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _detect_playlist_kind(lines: list[str]) -> PlaylistKind:
    has_master_tags = any(
        line.startswith("#EXT-X-STREAM-INF")
        or line.startswith("#EXT-X-I-FRAME-STREAM-INF")
        or line.startswith("#EXT-X-MEDIA:")
        for line in lines
    )
    if has_master_tags:
        return "master"

    has_media_tags = any(
        line.startswith("#EXTINF")
        or line.startswith("#EXT-X-TARGETDURATION")
        or line.startswith("#EXT-X-MAP")
        or line.startswith("#EXT-X-KEY")
        for line in lines
    )
    if has_media_tags:
        return "media"

    return "unknown"


def _detect_playlist_type(lines: list[str], kind: PlaylistKind) -> PlaylistType:
    playlist_type = None
    for line in lines:
        if line.startswith("#EXT-X-PLAYLIST-TYPE:"):
            playlist_type = line.split(":", 1)[1].strip().lower()
            break

    if playlist_type == "vod":
        return "vod"
    if playlist_type == "event":
        return "event"
    if any(line.startswith("#EXT-X-ENDLIST") for line in lines):
        return "vod"
    if kind == "media":
        return "live_or_open"
    return "unknown"


def _parse_target_duration(lines: list[str]) -> float | None:
    for line in lines:
        if line.startswith("#EXT-X-TARGETDURATION:"):
            return _parse_float(line.split(":", 1)[1])
    return None


def _parse_duration(lines: list[str]) -> float | None:
    total = 0.0
    found = False
    for line in lines:
        if line.startswith("#EXTINF:"):
            value = line.split(":", 1)[1].split(",", 1)[0]
            duration = _parse_float(value)
            if duration is not None:
                total += duration
                found = True
    return total if found else None


def _parse_variant_streams(lines: list[str], source_url: str | None) -> list[VariantStream]:
    streams: list[VariantStream] = []
    pending_attrs: dict[str, str] | None = None

    for line in lines:
        if line.startswith("#EXT-X-STREAM-INF:"):
            pending_attrs = parse_attribute_list(line.split(":", 1)[1])
            continue

        if pending_attrs is not None:
            if line.startswith("#"):
                continue
            streams.append(_variant_from_attrs(pending_attrs, _resolve_uri(source_url, line)))
            pending_attrs = None

    return streams


def _variant_from_attrs(attrs: dict[str, str], uri: str | None) -> VariantStream:
    return VariantStream(
        uri=uri,
        bandwidth=_parse_int(attrs.get("BANDWIDTH")),
        resolution=attrs.get("RESOLUTION"),
        codecs=attrs.get("CODECS"),
        audio_group=attrs.get("AUDIO"),
        subtitle_group=attrs.get("SUBTITLES"),
        raw_attributes=attrs,
    )


def _parse_subtitle_tracks(lines: list[str], source_url: str | None) -> list[SubtitleTrack]:
    tracks: list[SubtitleTrack] = []
    for line in lines:
        if not line.startswith("#EXT-X-MEDIA:"):
            continue
        attrs = parse_attribute_list(line.split(":", 1)[1])
        if attrs.get("TYPE", "").upper() != "SUBTITLES":
            continue

        group_id = attrs.get("GROUP-ID")
        name = attrs.get("NAME")
        language = attrs.get("LANGUAGE")
        uri = _resolve_uri(source_url, attrs.get("URI"))
        track_id = ":".join(part for part in (group_id, language, name) if part) or f"subtitle-{len(tracks) + 1}"
        tracks.append(
            SubtitleTrack(
                track_id=track_id,
                group_id=group_id,
                name=name,
                language=language,
                uri=uri,
                default=_truthy(attrs.get("DEFAULT")),
                autoselect=_truthy(attrs.get("AUTOSELECT")),
                forced=_truthy(attrs.get("FORCED")),
                raw_attributes=attrs,
            )
        )
    return tracks


def _parse_media_segments(lines: list[str], source_url: str | None) -> list[str]:
    segments: list[str] = []
    expect_segment = False
    for line in lines:
        if line.startswith("#EXTINF:"):
            expect_segment = True
            continue
        if expect_segment and not line.startswith("#"):
            resolved = _resolve_uri(source_url, line)
            if resolved:
                segments.append(resolved)
            expect_segment = False
    return segments


def _resolve_uri(source_url: str | None, uri: str | None) -> str | None:
    if not uri:
        return None
    parsed = urlparse(uri)
    if parsed.scheme:
        return uri
    if source_url:
        return urljoin(source_url, uri)
    return uri


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _truthy(value: str | None) -> bool:
    return bool(value and value.upper() == "YES")
