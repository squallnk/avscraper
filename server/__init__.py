"""avscraper —— 番号 / 里番元数据刮削器。"""

import os

__version__ = "0.1.0"

# 构建时由 Dockerfile 注入（见 .github/workflows/docker.yml）。
# 本地源码运行时是 "dev"。
BUILD_SHA = os.getenv("AVS_BUILD_SHA", "dev")[:12]
BUILD_TIME = os.getenv("AVS_BUILD_TIME", "")


def build_info() -> dict[str, str]:
    """给 /api/health 与 selftest 用的版本信息。

    没有这个之前，判断"容器里跑的是哪个版本"只能靠行为反推 ——
    排查时白绕了好几圈。
    """
    return {"version": __version__, "build_sha": BUILD_SHA, "build_time": BUILD_TIME}
