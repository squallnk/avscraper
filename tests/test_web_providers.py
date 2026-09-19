r"""WebUI 用到的 naive-ui provider hook，必须在 App.vue 挂上对应的 provider。

**起因是一次真实的线上白屏**：给「刮削记录」加删除确认时用了 ``useDialog()``，
但 App.vue 里只有 ``n-message-provider``。naive-ui 的 ``useDialog()`` 在注入不到
provider 时**直接抛异常**，而它是在组件 setup 阶段调用的 —— 整个 RecordsView
渲染失败，页面一片空白。用户看到的是"刮削记录里什么都不显示"。

``npm run build`` 抓不到这个：provider 是运行时的依赖注入，
类型检查与打包都不会碰它。所以在这里做一次静态一致性检查。
"""

from __future__ import annotations

import re
from pathlib import Path

WEB_SRC = Path(__file__).resolve().parents[1] / "web" / "src"

# hook -> App.vue 里必须出现的 provider 组件
PROVIDERS = {
    "useMessage": "n-message-provider",
    "useDialog": "n-dialog-provider",
    "useNotification": "n-notification-provider",
    "useLoadingBar": "n-loading-bar-provider",
}


def _app_source() -> str:
    return (WEB_SRC / "App.vue").read_text(encoding="utf-8")


def _hooks_in_use() -> set[str]:
    used: set[str] = set()
    for path in WEB_SRC.rglob("*.vue"):
        text = path.read_text(encoding="utf-8")
        for hook in PROVIDERS:
            if re.search(rf"\b{hook}\s*\(", text):
                used.add(hook)
    return used


def test_every_hook_in_use_has_its_provider():
    used = _hooks_in_use()
    assert used, f"没扫到任何 provider hook，扫描路径对不对？{WEB_SRC}"

    missing = {hook: PROVIDERS[hook] for hook in sorted(used) if PROVIDERS[hook] not in _app_source()}
    assert not missing, f"这些 hook 没有对应的 provider，页面会白屏: {missing}"


def test_providers_wrap_the_router_view():
    """光挂上还不够，必须在 router-view **外面** —— 挂到里面等于没挂。"""
    app = _app_source()
    router_view = app.index("<router-view")
    for hook in sorted(_hooks_in_use()):
        provider = PROVIDERS[hook]
        assert app.index(f"<{provider}") < router_view, f"{provider} 必须在 router-view 外层"


def test_the_regression_that_caused_the_blank_page():
    """把那次白屏钉住：RecordsView 用了 dialog，就必须有 n-dialog-provider。"""
    records = (WEB_SRC / "views" / "RecordsView.vue").read_text(encoding="utf-8")
    assert "useDialog()" in records
    assert "<n-dialog-provider>" in _app_source()
