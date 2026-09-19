import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_fixture(name: str, encoding: str = "utf-8") -> str:
    r"""读取测试固件。缺失时跳过该用例。

    真实页面固件**默认不入库**：它们抓自成站点，页面里含成人内容的标题文本，
    放进公开仓库有风险。抓取方法见 docs/FIXTURES.md。
    本机要跑全量用例，把那几个 .html 放进 tests/fixtures/ 即可。

    **编码自动兜底**：固件是直接抓下来的原始字节，站点编码各不相同 ——
    getchu 是 EUC-JP，其余是 UTF-8。以前写死 ``errors="replace"`` 读 UTF-8，
    于是 EUC-JP 的固件被解成满篇替换字符，**断言日本字全部失真却照样"通过"**
    （只对 ASCII 部分生效）。先按指定编码严格解，失败再逐个试，
    这样哪种编码的固件都能拿到真正的文本。
    """
    path = FIXTURES / name
    if not path.is_file():
        pytest.skip(f"固件 {name} 未提供（真实页面固件默认不入库，见 docs/FIXTURES.md）")

    raw = path.read_bytes()
    for candidate in dict.fromkeys((encoding, "utf-8", "euc-jp")):
        try:
            return raw.decode(candidate)
        except UnicodeDecodeError:
            continue
    return raw.decode(encoding, errors="replace")
