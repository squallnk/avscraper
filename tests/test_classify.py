import pytest

from server.classify import classify, has_uncensored_marker, is_video_file, parse_number
from server.models import ContentType


@pytest.mark.parametrize(
    ("filename", "number", "content_type"),
    [
        ("MIDV-123.mp4", "MIDV-123", ContentType.CENSORED),
        ("midv-123 4K.mkv", "MIDV-123", ContentType.CENSORED),
        ("MIDV-123-UC.mp4", "MIDV-123", ContentType.UNCENSORED),
        ("MIDV-123-U.mp4", "MIDV-123", ContentType.UNCENSORED),
        ("MIDV-123-无码.mp4", "MIDV-123", ContentType.UNCENSORED),
        ("FC2-PPV-1234567.mp4", "FC2-PPV-1234567", ContentType.FC2),
        ("FC2PPV 1234567.mp4", "FC2-PPV-1234567", ContentType.FC2),
        ("300MIUM-123.mp4", "300MIUM-123", ContentType.AMATEUR),
        ("SIRO-4567.mp4", "SIRO-4567", ContentType.AMATEUR),
        ("118abs001.mp4", "ABS-001", ContentType.CENSORED),
        ("123456-789.mp4", "123456-789", ContentType.UNCENSORED),
    ],
)
def test_parse_number(filename, number, content_type):
    result = parse_number(filename)
    assert result.number == number
    assert result.content_type is content_type
    assert result.evidence


def test_container_prefix_is_not_treated_as_studio():
    """"MP4" 这类容器标签不能当成厂牌前缀。"""
    result = parse_number("MP4-123.mp4")
    assert result.number is None


def test_uncensored_marker_reads_raw_name():
    """-UC 这类尾缀会在清洗阶段被摘掉，所以标记必须在原始名上判。"""
    assert has_uncensored_marker("MIDV-123-UC.mp4")
    assert has_uncensored_marker("ABC-001 無碼.mp4")
    assert not has_uncensored_marker("MIDV-123.mp4")


def test_janime_by_path_keyword():
    """里番没有番号，靠路径关键词分类。"""
    result = classify("/mnt/115/里番/Queen Bee/无题.mp4")
    assert result.content_type is ContentType.JANIME
    assert result.number is None
    assert any("里番" in item for item in result.evidence)


def test_janime_by_episode_marker():
    result = classify("/media/anime/某作品 第03話.mkv")
    assert result.content_type is ContentType.JANIME


def test_path_prefers_uncensored_over_default_censored():
    """同番号的无码版放在 无码/ 目录下时，类型跟着路径走。"""
    result = classify("/mnt/115/无码/MIDV-123.mp4")
    assert result.number == "MIDV-123"
    assert result.content_type is ContentType.UNCENSORED


def test_path_marks_chinese_when_number_is_weak():
    result = classify("/mnt/115/国产/麻豆/MD-0123.mp4")
    assert result.number == "MD-0123"
    assert result.content_type is ContentType.CHINESE


def test_unknown_when_nothing_matches():
    result = classify("/mnt/115/随手丢的文件.mp4")
    assert result.number is None
    assert result.content_type is ContentType.UNKNOWN


def test_is_video_file():
    assert is_video_file("a.MKV")
    assert is_video_file("a.ts")
    assert not is_video_file("a.srt")
    assert not is_video_file("a.jpg")
