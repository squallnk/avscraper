r"""查询名清洗。

这组用例**全部来自部署后的真实文件**。起因是：分类判对了 janime，但刮削全部 not_found ——
因为查询串直接用了 \`path.stem\`，把日期、制作组、集数、副标题、语言后缀一起丢给了站点。
"""

import pytest

from server.cleaner import clean_query_name


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        # 日期 + 制作组 + 集数 + 末尾作者方括号
        (
            "[251114][Queen Bee]牝を狩る村 前編[しのぎ鋭介].chs.mp4",
            "牝を狩る村",
        ),
        # 集数后面还有很长的副标题，全部丢掉
        (
            "[251128][魔人]勇者姫ミリア 第四話 砂漠の町のオークション！ ○○堕ちとメイドご奉仕.chs.mp4",
            "勇者姫ミリア",
        ),
        # 只有制作组，没有动画关键词
        ("[251128][nur]ドSなペット ～初めての躾け～.chs.mp4", "ドSなペット"),
        # OVA 标记要拿掉
        (
            "[251128][ばにぃうぉ～か～]OVA おしかけ！爆乳ギャルハーレム性活 ＃1.chs.mp4",
            "おしかけ！爆乳ギャルハーレム性活",
        ),
        # THE ANIMATION 与 Vol.N
        (
            "[251128][ピンクパイナップル]ながちち永井さん"
            " THE ANIMATION Vol.1 むちむちダイエット奮戦記.chs.mp4",
            "ながちち永井さん",
        ),
        # 集数标记之后的文本全丢
        (
            "[251128][魔人]危険な森 おにごっこ 第二話 「早くお家に帰らなくちゃ」.chs.mp4",
            "危険な森 おにごっこ",
        ),
        # 异体字「其の弍」与「其の二」必须同结果（见下面的一致性用例）
        (
            "[251128][Queen Bee]好色の忠義くノ一ぼたん 其の弍[田辺京].chs.mp4",
            "好色の忠義くノ一ぼたん",
        ),
    ],
)
def test_cleans_real_filenames(filename, expected):
    assert clean_query_name(filename) == expected


@pytest.mark.parametrize(
    "marker",
    ["其の二", "其の弍", "其の弐", "其の貳", "其ノ弐", "其の２", "其の3"],
)
def test_variant_episode_markers_collapse_to_one_query(marker):
    """同一个语义、不同写法，查询词必须完全一样。

    「其の二」被截断而「其の弍」不被截断，会让同一部作品的两个文件
    产出两个不同的查询词 —— 一个能搜到，一个搜不到。
    """
    name = f"[251128][Queen Bee]好色の忠義くノ一ぼたん {marker}[田辺京].chs.mp4"
    assert clean_query_name(name) == "好色の忠義くノ一ぼたん"


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        # 异体字只在"标记位置"参与匹配，标题本身一个字都不能改
        ("[251128][魔人]参上！勇者 第1話.chs.mp4", "参上！勇者"),
        ("[251128][魔人]漆黒の陸 前編.chs.mp4", "漆黒の陸"),
        ("[251128][魔人]拾われた少女 第2巻.chs.mp4", "拾われた少女"),
    ],
)
def test_variant_characters_in_titles_are_not_rewritten(filename, expected):
    """归一化只用来**定位**标记，不能改写标题。

    整篇归一化会把「参上！」变成「三上！」「漆黒の陸」变成「漆黒の六」——
    查询词直接废掉，而且是静默的。
    """
    assert clean_query_name(filename) == expected


@pytest.mark.parametrize(
    ("filename", "episode"),
    [
        ("[251128][Group]作品名 其の二[作者].chs.mp4", 2),
        ("[251128][Group]作品名 其の弍[作者].chs.mp4", 2),
        ("[251128][Group]作品名 其ノ弐[作者].chs.mp4", 2),
        ("[251128][Group]作品名 第2巻.chs.mp4", 2),
        ("[251128][Group]作品名 前編.chs.mp4", 1),
    ],
)
def test_cleaner_and_episode_parser_agree(filename, episode):
    """两个模块必须对同一批标记给出**互补**的结果：

    清洗器把标记剪掉 → 查询词是干净的作品名；
    解析器把同一个标记读成数字 → 集号。
    两边只要有一边不认，就会出现"查询词里留着标记"或"集号丢了"。
    """
    from server.episode import parse_episode

    cleaned = clean_query_name(filename)
    assert cleaned == "作品名", f"查询词没剪干净: {cleaned!r}"
    info = parse_episode(filename)
    assert info.episode == episode, f"集号解析错: {info}"


@pytest.mark.parametrize("lang", [".chs", ".cht", ".jpn", ".eng", ".sc"])
def test_language_suffix_removed(lang):
    assert clean_query_name(f"[251128][魔人]作品名 第1話{lang}.mp4") == "作品名"


def test_brackets_only_yields_empty():
    """清洗不出东西时返回空串，由调用方决定兜底。"""
    assert clean_query_name("[251128][魔人].chs.mp4") == ""
    assert clean_query_name("") == ""


def test_plain_name_survives():
    assert clean_query_name("勇者姫ミリア 第2話.mp4") == "勇者姫ミリア"


def test_date_only_prefix_is_not_treated_as_studio():
    """没有日期前缀时，开头的方括号仍按制作组处理。"""
    assert clean_query_name("[Group]作品名 第1話.mkv") == "作品名"


def test_pipeline_uses_cleaned_query():
    """管线里必须用清洗后的名字，而不是原始 stem。"""
    import inspect

    from server import pipeline

    source = inspect.getsource(pipeline.scrape_one)
    assert "clean_query_name" in source
    assert "query = path.stem if not match.number else None" not in source
