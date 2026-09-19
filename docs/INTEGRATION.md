# 与现有体系的接入说明

目标环境：**Unraid + CloudDrive2 + Symedia + Emby + nextemby**。

## 职责边界

| 组件 | 负责 | 不负责 |
|---|---|---|
| CloudDrive2 | 115 的凭证与访问通道、挂载、风控 | 元数据 |
| Symedia | 归档整理、strm 生成、Emby 通知、115 转存 | 番号/里番元数据 |
| **avscraper（本项目）** | **番号/里番元数据刮削，输出 NFO 与图片** | 移动文件、转存、播放 |
| nextemby | 播放时的 302 与秒传 | 元数据 |
| Emby | 展示与播放 | — |

**核心约束：文件移动只能有一套。** Symedia 已经在归档，本项目的整理默认关闭。

## 测试目录（当前配置）

| 项 | 值 |
|---|---|
| 115 网盘目录 | `/Test` |
| Unraid 挂载路径 | `/mnt/user/clouddrive/115/Test` |
| **CD2 虚拟路径** | **`/115/Test`**（来自 CD2 网页 `?page=files&path=%2F115%2FTest`） |

容器里：

```
AVS_ALLOWED_ROOTS=/media/115test
-v /mnt/user/clouddrive/115/Test:/media/115test
```

**CD2 虚拟路径与本地路径是两套坐标**：webhook 推来的是 `/115/Test/...` 这种 VFS 字符串，
不是 `/mnt/user/...`。两者靠 `cd2_mappings` 关联，做最长前缀匹配：

```json
"cd2_mappings": [["/115/Test", "/media/115test"]]
```

右侧必须是**容器内**的路径（即 volume 映射后的 `/media/115test`），不是宿主机路径。
填错的表现是日志里出现"未映射"并跳过事件 —— 不会误写文件，也不会写到别的目录去。

## 不直连 115

进程内不出现任何 115 SDK 或 Cookie。所有 115 内容都通过 CloudDrive2 暴露：

- **首选**：CD2 挂载点，对进程就是普通本地路径。
- **webhook**：CD2 推来虚拟路径，由 `Cd2PathMapper` 做**最长前缀匹配**换成挂载路径，
  再经 `RootGuard` 校验是否落在允许根目录内。映射写错或越界一律拒绝。

## 防风控的七条规则

1. **单一出口**：115 只由 CD2 访问，本项目不持有任何 115 凭证。
2. **事件优先于轮询**：接 CD2 webhook 后不再全盘扫描。
3. **写操作串行**：所有落盘经 `SerialQueue`，同一时刻只有一个在跑。
4. **写操作限速**：`file_op_rate` 默认 0.5 次/秒。
5. **失败退避与熔断**：连续失败到阈值进入冷却，时长指数增长（上限 64 倍）。
6. **本地优先**：NFO、图片、strm 全部写本地盘。
7. **不做转存**：转存/秒传交给 nextemby。

## CD2 webhook 配置

### 1. 本项目侧

「设置」页或 `PUT /api/config`：

```json
{
  "webhook_enabled": true,
  "webhook_token": "<自己生成的随机串>",
  "webhook_debounce_seconds": 20,
  "webhook_auto_scrape": false,
  "cd2_mappings": [["/115open/Test", "/media/115test"]]
}
```

`webhook_auto_scrape=false` 时只登记索引、不联网刮削 —— 先跑这个，确认事件与时序都对了再打开。

### 2. CloudDrive2 侧

在 CD2 网页 **设置 → Webhook → 添加 Webhook**（会员功能），或编辑配置目录里的
`webhook.toml` / `Configuration.toml` 的 `[file_system_watcher]`：

```toml
[file_system_watcher]
url = "http://<unraid-ip>:9300/api/webhooks/clouddrive"
method = "POST"
enabled = true
body = '''
{
  "data": [
    {
      "action": "{action}",
      "is_dir": "{is_dir}",
      "source_file": "{source_file}",
      "destination_file": "{destination_file}"
    }
  ]
}
'''

[file_system_watcher.headers]
Authorization = "Bearer <webhook_token>"
```

### 3. 契约

| 事件 | 本项目的行为 |
|---|---|
| 文件 create | 是视频且未登记过则入库（已存在的跳过） |
| 文件 delete | 按路径删索引 |
| 文件 rename | 改索引路径；源不在索引则按目标登记 |
| 目录 create | **防抖**后只扫该子树，登记其中的视频 |
| 目录 delete | 按路径前缀删索引，**不遍历磁盘** |
| 目录 rename | 前缀改写索引路径 |

要点：
- HTTP **立即返回 204**，处理在后台 —— 否则 FUSE 子树扫描会把 CD2 自己堵住。
- 目录事件可能早于 CD2 把子树列出来，所以防抖后再扫，且扫之前重算路径（库删了就跳过）。
- 幂等靠路径唯一 + "已登记则跳过"，文件级事件与目录展开重叠也不会重复。
- 目录扫描有 `webhook_max_subtree_files` 上限（默认 2000），防止误配大目录一次扫爆。

## 里番：getchu 源

`getchu` 已经实现，两个站点特性都用真实页面确认过：

1. **编码是 EUC-JP**，不是 UTF-8。代码里写死了解码方式。
2. **年龄确认墙用 `gc=gc` 参数绕过** —— 确认页上的「【すすむ】」按钮本质上就是把这个参数带回去。
   **不需要 Cookie，也不需要浏览器。** 排查过程留档在 `docs/COOKIE.md`。

另外：

- **商品详情页**（`/soft.phtml?id=<数字 id>`）本来是公开的，解析已用真实页面验证。
- **搜索结果页夹着推荐位**，且同样是 `soft.phtml?id=...` 形态，所以先判"有没有命中"
  且只在结果列表 `ul.display` 里取 id。有命中/无命中各一份真实固件做回归。
- 也可以用 `getchu:<商品id>` 直接指定。

由于里番没有番号，里番的路由是 `bangumi → getchu → javdb → freejavbt`，
分类靠**路径关键词**（目录名含"里番/アニメ/OVA"或文件名含"第N話"等）。

## 首次使用顺序

1. `python -m server.cli selftest` —— 确认允许根目录、限速、熔断参数。
2. `python -m server.cli classify "<某个文件路径>"` —— 确认番号与类型判定。
3. WebUI「设置」填代理（这些站点普遍需要代理），确认 `dry_run` 仍为开。
4. WebUI「任务」页提交扫描，打开 `confirm`。默认只写数据库记录，不动文件系统。
5. 看「刮削记录」页：番号、类型、**字段来源**、错误原因逐条可查。
6. 分类与刮削都对了，再考虑打开 `organize_enabled`。