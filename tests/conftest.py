import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_fixture(name: str) -> str:
    """读取测试固件。缺失时跳过该用例。

    真实页面固件**默认不入库**：它们抓自成站点，页面里含成人内容的标题文本，
    放进公开仓库有风险。抓取方法见 docs/FIXTURES.md。
    本机要跑全量用例，把那几个 .html 放进 tests/fixtures/ 即可。
    """
    path = FIXTURES / name
    if not path.is_file():
        pytest.skip(f"固件 {name} 未提供（真实页面固件默认不入库，见 docs/FIXTURES.md）")
    return path.read_text(encoding="utf-8", errors="replace")
