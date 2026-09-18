"""进程配置与运行期设置。

分两层：
- `Settings`：进程启动期配置，来自环境变量（前缀 `AVS_`），启动后只读。
- `RuntimeConfig`：运行期可改的设置，存 DB 的 `config` 表，由 WebUI 修改。

安全默认值：只读（`organize_enabled=False` 且 `dry_run=True`）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """启动期配置。"""

    model_config = SettingsConfigDict(env_prefix="AVS_", env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    host: str = "127.0.0.1"
    port: int = 9300
    log_level: str = "INFO"

    # 访问令牌。为空 = 本机免鉴权（仍只允许回环地址访问 API）。
    api_token: str = ""

    # 允许被本进程读写/移动的根目录白名单。空列表 = 不允许任何文件写入。
    # NoDecode：环境变量里写 `D:/a;D:/b` 这种分隔串，不让 pydantic 当 JSON 解析。
    allowed_roots: Annotated[list[Path], NoDecode] = Field(default_factory=list)

    # CloudDrive2。仅用于读取云盘元数据，不做 115 直连。
    cd2_grpc_target: str = ""
    cd2_grpc_token: str = ""

    db_path: Path | None = None

    @property
    def database_path(self) -> Path:
        return self.db_path or (self.data_dir / "avscraper.db")

    @field_validator("allowed_roots", mode="before")
    @classmethod
    def _split_roots(cls, value: Any) -> Any:
        """支持用 `;` 或 `,` 分隔的字符串写环境变量。"""
        if isinstance(value, str):
            if not value.strip():
                return []
            parts = [p.strip() for p in value.replace(";", ",").split(",")]
            return [Path(p) for p in parts if p]
        return value


SECRET_MASK = "***"


class ImageDownloadConfig(BaseModel):
    """图片下载选择。

    剧照（extrafanart）体积大、张数多，默认关闭 —— 它们只是详情页的观感增强，
    对 Emby 的识别没有任何影响。海报与缩略图默认开启，因为 Emby 靠它们展示条目。
    """

    poster: bool = True
    thumb: bool = True
    fanart: bool = False
    extrafanart: bool = False
    extrafanart_limit: int = Field(default=5, ge=1, le=50)

    overwrite: bool = False
    """默认不覆盖已存在的图片。"""

    concurrency: int = Field(default=3, ge=1, le=8)
    timeout: float = Field(default=30.0, ge=5, le=180)


class RuntimeConfig(BaseModel):
    """运行期设置，存 DB。

    含密钥的字段（`webhook_token`、`source_cookies`）在回给前端时一律用 `SECRET_MASK` 掩码，
    见 `redacted()` / `with_patch()`。存储是本地 SQLite，没有额外加密 —— 依赖文件系统权限。
    """

    # ---- 安全 ----
    dry_run: bool = True
    """True 时一切落盘操作只记录不执行。"""

    organize_enabled: bool = False
    """True 才允许移动/改名/删除。默认关闭。"""

    # ---- 防风控 ----
    file_op_rate: float = 0.5
    """文件写操作速率（次/秒）。0.5 = 每 2 秒一次。"""

    file_op_burst: int = 3
    """文件写操作突发容量。"""

    http_rate: float = 1.0
    """单站点 HTTP 速率（次/秒）。"""

    http_burst: int = 3

    cooldown_seconds: float = 60.0
    """连续失败后进入冷却的基础时长。"""

    failure_threshold: int = 3
    """连续失败达到该值时熔断。"""

    max_retries: int = 2
    """单次请求最大重试次数。"""

    request_timeout: float = 20.0

    proxy: str = ""
    """如 http://127.0.0.1:7890 或 socks5://... 。"""

    user_agent: str = ""
    """自定义 User-Agent。留空用内置默认值。

    有些站点的 Cloudflare 会做 `cf_clearance` Cookie 与 UA 的绑定校验 ——
    从浏览器复制出来的 Cookie 只有配上**同一个 UA** 才有效。
    所以填 Cookie 时通常要同时把浏览器的 UA 一起填进来。
    """

    # ---- CD2 / Webhook ----
    cd2_mappings: list[list[str]] = Field(default_factory=list)
    """CD2 虚拟路径前缀 -> 本地挂载点，最长前缀优先。

    例：[["/115open/Test", "/mnt/user/clouddrive/115/Test"]]
    """

    webhook_enabled: bool = False
    webhook_token: str = ""
    """CD2 侧 Authorization: Bearer <token>。为空表示不校验（仅建议本机调试用）。"""

    webhook_debounce_seconds: float = 20.0
    """目录事件防抖：目录 create 后等这么久，让 CD2 有时间把子树列出来。"""

    webhook_max_subtree_files: int = 2000
    """单个目录事件的子树扫描上限，防止误配大目录导致一次扫爆。"""

    webhook_auto_scrape: bool = False
    """收到新文件事件后自动刮削。默认关，先只登记。"""

    # ---- 刮削 ----
    enabled_sources: list[str] = Field(default_factory=list)
    """启用的源 id。空 = 全部已注册源。"""

    source_cookies: dict[str, str] = Field(default_factory=dict)
    """站点特例：源 id -> Cookie 串。getchu 的搜索接口有年龄确认墙，需要这个。"""

    metadata_dir: str = ""
    """NFO/图片写到哪里。留空表示写在视频同目录的 .metadata 下。"""

    images: ImageDownloadConfig = Field(default_factory=ImageDownloadConfig)
    """图片下载开关。"""

    field_priority: dict[str, list[str]] = Field(default_factory=dict)
    """字段 -> 源 id 优先顺序。未配置的字段用内容类型路由。"""

    route_override: dict[str, list[str]] = Field(default_factory=dict)
    """内容类型 -> 源 id 顺序覆盖。"""

    # ---- 整理（默认关闭） ----
    directory_template: str = "{category}/{number}"
    filename_template: str = "{number}"
    image_template: str = "{number}-poster"
    nfo_template: str = "{number}"

    def redacted(self) -> dict[str, Any]:
        """给前端看的版本：密钥只回显"已设置"，不回传明文。"""
        data = self.model_dump(mode="json")
        data["webhook_token"] = SECRET_MASK if self.webhook_token else ""
        data["source_cookies"] = {key: SECRET_MASK for key in self.source_cookies}
        return data

    def with_patch(self, patch: dict[str, Any]) -> RuntimeConfig:
        """应用部分更新。

        密钥字段的语义：
        - 值为掩码 -> 保留原值（前端拿到的是掩码，原样传回来不代表要改成掩码）；
        - 值为空串 -> 清除；
        - 其它值 -> 覆盖。
        """
        data = self.model_dump()
        for key, value in patch.items():
            if key == "webhook_token":
                if value == SECRET_MASK:
                    continue
                data[key] = value or ""
                continue
            if key == "source_cookies":
                if not isinstance(value, dict):
                    continue
                merged = dict(self.source_cookies)
                for source_id, cookie in value.items():
                    if cookie == SECRET_MASK:
                        continue
                    if not cookie:
                        merged.pop(source_id, None)
                    else:
                        merged[source_id] = cookie
                data["source_cookies"] = merged
                continue
            data[key] = value
        return RuntimeConfig.model_validate(data)

    def to_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, raw: str | None) -> RuntimeConfig:
        if not raw:
            return cls()
        try:
            return cls.model_validate(json.loads(raw))
        except (ValueError, TypeError):
            return cls()
