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


def test_janime_by_release_studio():
    """真实放的里番常常只有制作组、没有 \"アニメ\" 这类关键词。

    这是部署后才发现的：\\`[251128][nur]...\\` 这种文件在没有制作组名单时被判成 unknown。
    名单匹配有前提 —— 必须出现在 \\`[日期][制作组]\\` 结构里。
    """
    for name in (
        "[251128][nur]ドSなペット ～初めての躾け～.chs.mp4",
        "[251114][Queen Bee]牝を狩る村 前編[しのぎ鋭介].chs.mp4",
        "[251128][魔人]危険な森 おにごっこ 第二話 「早くお家に帰らなくちゃ」.chs.mp4",
    ):
        assert classify(f"/media/media/{name}").content_type is ContentType.JANIME, name


def test_janime_english_animation_marker():
    r"""ピンクパイナップル 的命名习惯是 \`XXX THE ANIMATION\`，只认片假名会漏掉。"""
    result = classify("/media/media/[251128][ピンクパイナップル]作品名 THE ANIMATION 第2巻.chs.mp4")
    assert result.content_type is ContentType.JANIME
    assert result.episode == 2


def test_studio_word_alone_does_not_trigger_janime():
    r"""制作组名单里有 Seven / Milky / Lune 这类通用词。

    只在 \`[日期][制作组]\` 结构里认，避免普通影片误判成里番。
    """
    for name in ("Seven Samurai 1954.mkv", "Milky Way 2015.mp4", "Lune de Miel 2019.mkv"):
        assert classify(f"/media/media/{name}").content_type is not ContentType.JANIME, name


def test_unknown_bracket_group_is_not_janime():
    """方括号里有东西不代表就是里番制作组。

    注意这里用的是 **8 位**日期（`[20240101]`）—— 那是另一类命名（欧美/压制组的
    常见写法）。结构兜底只认 6 位 `yymmdd` 的里番发布形态，所以这条仍然不判里番。
    """
    result = classify("/media/media/[20240101][Some Random Group]Movie Name.mp4")
    assert result.content_type is not ContentType.JANIME


def test_six_digit_release_prefix_falls_back_to_janime():
    r"""`[yymmdd][制作组] 标题` —— 按月归档的里番库就是这个命名形态。

    制作组名单是一个永远补不完的清单：实跑里 `AnimeFesta` 不在名单上，
    那个文件被判成 unknown → skipped，**悄无声息地没被刮**（用户在记录页
    只能看到一条"跳过"，看不出是名单漏了）。所以留一条结构兜底。
    """
    result = classify("/media/media/[250711][谁也没听说过的字幕组]某个作品.mp4")
    assert result.content_type is ContentType.JANIME
    # 比制作组名单（0.7）弱 —— 它只是"形态像"，不是"认得这个组"
    assert result.confidence < 0.7


def test_animefesta_is_a_known_studio():
    """实跑踩到的：AnimeFesta 不在名单上，文件被整批跳过。"""
    result = classify("/media/media/[250704][AnimeFesta]彼女がセパレートをまとう理由を見る.chs.mp4")
    assert result.content_type is ContentType.JANIME
    assert any("AnimeFesta" in e for e in result.evidence)


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
