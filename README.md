# avscraper

番号 / 里番元数据刮削器 —— 为 **Unraid + CloudDrive2 + Symedia + Emby** 这套体系设计。

> 定位：**只做刮削**。文件归档整理交给 Symedia。默认**只读**，写入必须显式开启。

## 设计约束（来自实际部署环境）

1. **不直连 115**。进程内不出现任何 115 SDK / Cookie。所有对 115 的访问都经由 CloudDrive2
   暴露的挂载路径或虚拟路径映射，115 侧的风控由 CD2 承担。
2. **默认只读**。`organize_enabled=false` 且 `dry_run=true` 时不会对任何文件做移动/改名/删除，
   有单测守着这条。
3. **防风控**。写操作走串行队列 + 令牌桶限速，失败指数退避，连续失败按源熔断。
4. **安全边界**。任何路径先解析真实路径并校验落在允许根目录内，**符号链接逃逸也会被拒绝**。
5. **不碰现有数据**。首次使用只指向独立测试目录。

## 数据源

| 源 | 覆盖 | 备注 |
|---|---|---|
| **getchu** | 里番 | 里番第一源；EUC-JP 编码；年龄确认墙用 `gc=gc` 参数绕过，无需 Cookie |
| **javbus** | 有码 / 无码 | 详情的标题、片商、导演、日期、时长 |
| **javdb** | 有码 / 无码 / FC2 / 欧美 / 动漫 | 评分与中文标题；动漫分区可作里番兜底 |
| **freejavbt** | 全类型兜底 | 覆盖最宽；结构化信息取自 meta description |

四个源**都用真实抓取的页面固件回归过**（固件不入库，见 `docs/FIXTURES.md`），
并且已对真实站点跑通完整链路。

## Unraid 安装

### 方式一：用模板（可在 Docker 界面里编辑、升级）

1. Docker 标签页 → **Add Container**
2. 在 **Template** 输入框里粘贴：
   ```
   https://raw.githubusercontent.com/squallnk/avscraper/main/unraid/avscraper.xml
   ```
3. 表单会自动填好端口、路径与环境变量，按需修改后 Apply

### 方式二：手动 compose

见 `docs/DEPLOY.md`，里面有完整拓扑与 compose 示例。

**镜像**：`ghcr.io/squallnk/avscraper:latest`
（首次构建后需在仓库的 Packages 页面把该包设为 **Public**，否则 Unraid 拉不到）

## 首次使用顺序

**每一步都确认无误再进下一步。**

```bash
# 1. 只读自检：不联网、不写盘
docker exec -it avscraper python -m server.cli selftest

# 2. 出网探测：联网、不写盘。验证容器能出去（路由继承 / DNS / 站点可达）
docker exec -it avscraper python -m server.cli netcheck

# 3. 分类核对：不联网。确认番号、内容类型、季集解析符合预期
docker exec -it avscraper python -m server.cli classify "/media/media/某个文件.mp4"

# 4. 小范围扫描：联网，只写数据库。先 --limit 3
docker exec -it avscraper python -m server.cli scan --root /media/media --limit 3
```

前四步都对，才在「设置」里关掉 `dry_run`、打开 `organize_enabled`。

## 本地开发

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows
pip install -r requirements.txt pytest pytest-asyncio ruff

python -m server.cli selftest
python -m server.main           # http://127.0.0.1:9300

cd web && npm install && npm run dev    # 前端开发服务器，代理 /api 到 9300
```

前端构建产物存在时后端会直接托管，所以生产只出一个端口。

## 目录结构

```
server/
  config.py        配置（环境变量 + DB 运行期设置）
  db.py            SQLite(WAL) + 显式 schema 版本迁移
  models.py        数据模型
  ratelimit.py     令牌桶 / 串行队列 / 冷却退避 / 熔断
  classify.py      番号解析、内容类型判定、里番路径识别
  episode.py       季集解析（第N話 / 其の弐 / 前編 / SxxEyy …）
  storage.py       本地根目录 + CD2 虚拟路径映射，含越界校验
  images.py        图片下载（四类可分别开关，候选逐个回退）
  aggregate.py     字段级多源聚合（内容类型路由 + 字段优先级）
  nfo.py           Emby/Jellyfin 兼容 NFO 生成
  webhook.py       CloudDrive2 文件变更 webhook
  pipeline.py      扫描 -> 分类 -> 刮削 -> 落盘
  sources/         刮削源插件（一源一文件）
  api/             FastAPI 路由
web/               Vue 3 + Naive UI
unraid/            Unraid Docker 模板与图标
tests/             pytest（真实页面固件不入库，缺失时自动跳过）
docs/              接入说明 / 部署 / 固件抓取 / 路线图
```

## 文档

| 文档 | 内容 |
|---|---|
| `docs/DEPLOY.md` | Unraid 部署：网络拓扑、compose、五步实跑顺序 |
| `docs/INTEGRATION.md` | 与 CloudDrive2 / Symedia / Emby / nextemby 的职责边界，webhook 配置 |
| `docs/FIXTURES.md` | 怎么抓真实页面固件（以及为什么必须抓） |
| `docs/COOKIE.md` | 站点访问墙与 Cookie 机制 |
| `docs/ROADMAP.md` | 已验证 / 未验证清单与下一步 |

## 明确不做

- **不直连 115**，不持有任何 115 凭证
- **不做转存 / 秒传**，那是 nextemby 的职责
- **不做文件归档**，整理交给 Symedia
- **不做任务图 / 链式依赖**，当前规模用不上

## 免责声明

本项目只从公开站点抓取**元数据**（标题、演员、日期等），不提供任何影片下载线索，
不含任何影片内容。使用者需自行确认当地法律法规并承担使用后果。

## License

MIT
