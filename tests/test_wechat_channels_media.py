from __future__ import annotations

import pytest

from video_orchestrator.runtime.wechat_channels_media import parse_profile_payload
from video_orchestrator.runtime.wechat_channels_media import resolution_fingerprint


@pytest.mark.parametrize("invalid", ["inf", "Infinity", float("inf"), float("nan")])
def test_nonfinite_dimensions_are_ignored(invalid):
    payload = _payload()
    payload["data"]["data"]["feedInfo"]["h264VideoInfo"]["width"] = invalid
    parsed = parse_profile_payload(payload)
    candidate = next(item for item in parsed["media_candidates"] if item["variant_key"] == "h264")
    assert "width" not in candidate


def _payload() -> dict:
    return {
        "data": {
            "data": {
                "feedInfo": {
                    "objectId": 987654321012345678,
                    "description": "合成标题\n完整简介",
                    "mediaType": 4,
                    "createTime": "1700000000",
                    "coverUrl": "https://qpic.cn/cover.jpg?token=fixture",
                    "originVideoInfo": {
                        "videoUrl": "https://finder.video.qq.com/original.mp4?token=original",
                        "width": "720",
                        "height": "1280",
                        "bitRate": "1600000",
                    },
                    "h264VideoInfo": {
                        "videoUrl": "https://finder.video.qq.com/h264.mp4?token=h264",
                        "width": 1080,
                        "height": 1920,
                        "bitrate": 4200000,
                        "frameRate": "60",
                    },
                    "h265VideoInfo": {
                        "videoUrl": "https://finder.video.qq.com/h265.mp4?token=h265",
                        "width": "1080",
                        "height": "1920",
                        "bitrate": "3600000",
                    },
                    "duration_ms": "95000",
                    "bgmInfo": {"name": "测试配乐", "artist": "测试作者"},
                },
                "authorInfo": {"username": 12345, "nickname": "作者"},
            }
        }
    }


def test_profile_parser_accepts_nested_and_flexible_scalar_fields():
    parsed = parse_profile_payload(_payload(), quality="highest")

    assert parsed["content_id"] == "987654321012345678"
    assert parsed["media_type"] == 4
    assert parsed["created_at"] == 1700000000
    assert parsed["duration_seconds"] == 95.0
    assert parsed["author"] == {"id": "12345", "name": "作者", "avatar_url": None}
    assert parsed["background_music"] == {"name": "测试配乐", "artist": "测试作者"}


def test_highest_quality_uses_measured_resolution_then_bitrate():
    parsed = parse_profile_payload(_payload(), quality="highest")

    assert parsed["selected_variant"]["variant_key"] == "h264"
    assert parsed["selected_variant"]["height"] == 1920
    assert parsed["selected_variant"]["frame_rate"] == 60.0


def test_compatible_quality_prefers_h264_and_public_candidates_have_no_url():
    parsed = parse_profile_payload(_payload(), quality="compatible")

    assert parsed["selected_variant"]["variant_key"] == "h264"
    assert all("media_url" not in candidate for candidate in parsed["media_candidates"])


def test_resolution_fingerprint_uses_content_and_variant_not_signed_url():
    first = parse_profile_payload(_payload(), quality="h264")
    second = {**first, "media_url": "https://finder.video.qq.com/h264.mp4?token=rotated"}

    assert resolution_fingerprint("https://weixin.qq.com/sph/Fixture", first) == resolution_fingerprint(
        "https://weixin.qq.com/sph/Fixture",
        second,
    )
    assert resolution_fingerprint("https://weixin.qq.com/sph/Fixture", first) != resolution_fingerprint(
        "https://weixin.qq.com/sph/Fixture",
        {**first, "selected_variant": {"variant_key": "h265"}},
    )


def test_profile_parser_accepts_latest_object_media_shape():
    payload = {
        "data": {
            "object": {
                "id": 9988776655,
                "createtime": 1700000123,
                "contact": {"username": "fixture-user", "nickname": "对象作者"},
                "objectDesc": {
                    "description": "对象结构标题\n对象结构简介",
                    "mediaType": 4,
                    "followPostInfo": {"musicInfo": {"name": "对象配乐", "artist": "对象歌手"}},
                    "media": [
                        {
                            "url": "https://finder.video.qq.com/object.mp4?token=secret",
                            "width": 1080,
                            "height": 1920,
                            "fileSize": "12345678",
                            "videoPlayLen": 86,
                            "spec": [{"fileFormat": "mp4", "width": 1080, "height": 1920}],
                            "coverUrl": "https://qpic.cn/object-cover.jpg?token=secret",
                        }
                    ],
                },
            }
        }
    }

    parsed = parse_profile_payload(payload, quality="highest")

    assert parsed["title"] == "对象结构标题"
    assert parsed["content_id"] == "9988776655"
    assert parsed["author"]["name"] == "对象作者"
    assert parsed["media_type"] == 4
    assert parsed["selected_variant"]["height"] == 1920
    assert parsed["background_music"] == {"name": "对象配乐", "artist": "对象歌手"}


def test_manual_codec_mode_selects_highest_measured_candidate_of_same_codec():
    payload = {
        "data": {
            "object": {
                "id": "same-codec-fixture",
                "objectDesc": {
                    "description": "同编码多清晰度",
                    "media": [
                        {
                            "url": "https://finder.video.qq.com/720p.mp4?token=fixture",
                            "codec": "h264",
                            "width": 720,
                            "height": 1280,
                            "bitrate": 1_600_000,
                        },
                        {
                            "url": "https://finder.video.qq.com/1080p.mp4?token=fixture",
                            "codec": "avc1.640028",
                            "width": 1080,
                            "height": 1920,
                            "bitrate": 4_200_000,
                        },
                    ],
                },
            }
        }
    }

    parsed = parse_profile_payload(payload, quality="h264")

    assert parsed["selected_variant"]["variant_key"] == "h264"
    assert parsed["selected_variant"]["height"] == 1920
