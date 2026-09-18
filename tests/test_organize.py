import pytest

from server.models import MediaMetadata
from server.organize import TemplateError, build_template_data, render


@pytest.mark.parametrize(
    ("template", "data", "expected"),
    [
        ("{number}", {"number": "MIDV-123"}, "MIDV-123"),
        ("{number}[-CD{cd?}]", {"number": "MIDV-123", "cd": "2"}, "MIDV-123-CD2"),
        ("{number}[-CD{cd?}]", {"number": "MIDV-123"}, "MIDV-123"),
        ("{number}[[{def?}]]", {"number": "MIDV-123", "def": "4K"}, "MIDV-123[4K]"),
        ("{number}[[{def?}]]", {"number": "MIDV-123"}, "MIDV-123"),
        ("{mosaic?|censored=有码,uncensored=无码}", {"mosaic": "uncensored"}, "无码"),
        ("{mosaic?|censored=有码}", {"mosaic": "other"}, "other"),
        ("{a?|=缺省}", {"a": ""}, "缺省"),
        ("{studio}/{number}", {"studio": "S1", "number": "MIDV-123"}, "S1/MIDV-123"),
    ],
)
def test_render(template, data, expected):
    assert render(template, data) == expected


def test_render_rejects_unbalanced_brackets():
    with pytest.raises(TemplateError):
        render("{number}[-CD{cd?}", {"number": "X"})


def test_render_never_leaves_dangling_separator():
    """可选组为空时不能残留分隔符，这是最常见的路径 Bug。"""
    assert render("{number}[-{sub?}]", {"number": "A-1"}) == "A-1"
    assert not render("{number}[-{sub?}]", {"number": "A-1"}).endswith("-")


def test_build_template_data_splits_prefix():
    data = build_template_data(MediaMetadata(number="MKY-HS-001", title="T"))
    assert data["prefix"] == "MKY"
    assert data["suffix"] == "HS-001"


def test_build_template_data_handles_missing_number():
    data = build_template_data(MediaMetadata(title="里番作品"))
    assert data["number"] == ""
    assert data["prefix"] == ""


def test_build_template_data_actor_defaults_empty():
    data = build_template_data(MediaMetadata(number="A-1", actors=[]))
    assert data["actor"] == ""
