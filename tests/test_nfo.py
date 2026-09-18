from xml.etree import ElementTree as ET

from server.models import AggregatedMetadata, ContentType, MediaMetadata
from server.nfo import build_movie_nfo, build_tvshow_nfo


def _aggregated(**kwargs) -> AggregatedMetadata:
    return AggregatedMetadata(
        number=kwargs.pop("number", "MIDV-123"),
        content_type=kwargs.pop("content_type", ContentType.CENSORED),
        metadata=MediaMetadata(**kwargs),
    )


def test_movie_nfo_is_valid_xml():
    xml = build_movie_nfo(
        _aggregated(title="标题", original_title="タイトル", actors=["A", "B"], tags=["标签"], year=2024)
    )
    root = ET.fromstring(xml)
    assert root.tag == "movie"
    assert root.findtext("title") == "标题"
    assert [a.findtext("name") for a in root.findall("actor")] == ["A", "B"]
    assert root.find("uniqueid").text == "MIDV-123"


def test_movie_nfo_omits_empty_fields():
    """空值不写标签 —— Emby 对空标签的处理不如缺标签。"""
    xml = build_movie_nfo(_aggregated(title="只有标题"))
    root = ET.fromstring(xml)
    assert root.find("plot") is None
    assert root.find("rating") is None
    assert root.find("actor") is None


def test_tvshow_nfo_for_janime():
    xml = build_tvshow_nfo(
        _aggregated(number=None, content_type=ContentType.JANIME, title="里番作品")
    )
    root = ET.fromstring(xml)
    assert root.tag == "tvshow"
    assert root.findtext("season") == "-1"


def test_nfo_escapes_special_characters():
    xml = build_movie_nfo(_aggregated(title="A & B <tag>"))
    root = ET.fromstring(xml)
    assert root.findtext("title") == "A & B <tag>"
