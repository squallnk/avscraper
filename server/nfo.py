"""Emby / Jellyfin 兼容的 NFO 生成。

只写**确定有值**的字段：Emby 对空标签的处理不如缺标签，宁缺勿滥。
"""

from __future__ import annotations

from xml.etree import ElementTree as ET

from server.models import AggregatedMetadata

_INDENT = "  "


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _add(parent: ET.Element, tag: str, value: object | None) -> None:
    if value is None:
        return
    text = str(value).strip()
    if not text:
        return
    node = ET.SubElement(parent, tag)
    node.text = text


def build_movie_nfo(aggregated: AggregatedMetadata) -> str:
    """生成影片级 NFO。`uniqueid` 用番号，Emby 用它去重。"""
    meta = aggregated.metadata
    root = ET.Element("movie")

    _add(root, "title", meta.title)
    _add(root, "originaltitle", meta.original_title)
    _add(root, "sorttitle", meta.number or meta.title)
    _add(root, "plot", meta.plot)
    _add(root, "outline", meta.plot)
    _add(root, "premiered", meta.release_date)
    _add(root, "releasedate", meta.release_date)
    _add(root, "year", meta.year)
    _add(root, "runtime", meta.runtime)
    _add(root, "rating", meta.score)
    _add(root, "studio", meta.studio)
    _add(root, "director", meta.director)
    _add(root, "mpaa", "JP-18+" if meta.number else None)
    _add(root, "website", meta.website)

    for genre in meta.tags:
        _add(root, "genre", genre)
    for tag in meta.tags:
        _add(root, "tag", tag)
    for actor in meta.actors:
        node = ET.SubElement(root, "actor")
        _add(node, "name", actor)

    number = meta.number or aggregated.number
    if number:
        uid = ET.SubElement(root, "uniqueid", {"type": "avscraper", "default": "true"})
        uid.text = number

    return _prettify(root)


def build_tvshow_nfo(aggregated: AggregatedMetadata) -> str:
    """里番这类分集内容用剧集结构更合适。"""
    meta = aggregated.metadata
    root = ET.Element("tvshow")
    _add(root, "title", meta.title)
    _add(root, "originaltitle", meta.original_title)
    _add(root, "plot", meta.plot)
    _add(root, "premiered", meta.release_date)
    _add(root, "year", meta.year)
    _add(root, "studio", meta.studio)
    _add(root, "rating", meta.score)
    for genre in meta.tags:
        _add(root, "genre", genre)
    for actor in meta.actors:
        node = ET.SubElement(root, "actor")
        _add(node, "name", actor)
    _add(root, "season", "-1")
    _add(root, "episode", "-1")
    return _prettify(root)


def build_episode_nfo(aggregated: AggregatedMetadata, *, season: int, episode: int) -> str:
    meta = aggregated.metadata
    root = ET.Element("episodedetails")
    _add(root, "title", meta.title)
    _add(root, "plot", meta.plot)
    _add(root, "season", season)
    _add(root, "episode", episode)
    _add(root, "premiered", meta.release_date)
    _add(root, "rating", meta.score)
    return _prettify(root)


def _prettify(root: ET.Element) -> str:
    """`ET.indent` 之后转字符串，末尾补换行。"""
    ET.indent(root, space=_INDENT)
    body = ET.tostring(root, encoding="unicode")
    body = body.replace("&amp;", "&amp;")
    return f'<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n{body}\n'
