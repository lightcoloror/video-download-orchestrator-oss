from video_orchestrator.backends.feishu import extract_feishu_video_url
from video_orchestrator.backends.feishu import extract_feishu_video_urls


def test_extract_feishu_video_url_from_plain_html():
    html = (
        '<script>{"src":"https://internal-api-drive-stream.feishu.cn/space/api/box/stream/'
        'download/video/file-token/?quality=1080p&data_version=1&mount_point=docx_file"}</script>'
    )

    assert extract_feishu_video_url(html).startswith("https://internal-api-drive-stream.feishu.cn/")


def test_extract_feishu_video_urls_unescapes_embedded_json_and_prefers_1080p():
    html = (
        'https:\\/\\/internal-api-drive-stream.feishu.cn\\/space\\/api\\/box\\/stream\\/download\\/video'
        '\\/file-token\\/?quality=720p\\u0026data_version=1 '
        'https:\\/\\/internal-api-drive-stream.feishu.cn\\/space\\/api\\/box\\/stream\\/download\\/video'
        '\\/file-token\\/?quality=1080p\\u0026data_version=1'
    )

    urls = extract_feishu_video_urls(html)

    assert len(urls) == 2
    assert "quality=1080p" in urls[0]
