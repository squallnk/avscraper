# 目录里冒出 `<uuid>.nfo` / `<uuid>.jpg`

媒体目录里偶尔会出现**以 GUID 命名**的 sidecar（例：`168b62ed-cf66-4658-b25f-549b299d132f.nfo`、
`c601a126-b9ba-4d24-b2ba-b904d00e3987.nfo`、`2402b80a-5589-4617-927e-509bd1f13bc0.jpg`、
`aafe9e63-9584-4bd8-a44d-45d5da161e95.jpg`），同时**对应的视频少了 `.nfo` / `-poster.jpg` / `-fanart.jpg`**。
看上去像"刮削器把文件名写错了"，实际不是。

## 结论

**这些文件不是 avscraper 写的。** 同一个目录里另有一个进程在对我们的 sidecar 做
**「改名成临时 GUID → 写回正式名」的读改写**，窗口期很短；正常情况下它会自己恢复，
中断/撞车时就留下 GUID 残骸，视频那头就空着。

## 证据

1. **`avscraper` 全仓没有任何 UUID 生成**：`grep -rn uuid --include=*.py --include=*.ts --include=*.vue .`
   零命中。写文件名的地方只有一处：`stem = video_path.stem or metadata.number`（`server/pipeline.py`）。
2. **那个 4 KB 的 NFO 里有"第二个写入器"的指纹**：它等于我们 `build_movie_nfo()` 的输出
   （3758 B，逐字节相同）**再加**一段写在 `</movie>` **之后**的内容：

   ```xml
   </movie>
     <genre>眼鏡</genre><genre>打手槍</genre><genre>口交</genre><genre>高清</genre><genre>凌辱</genre>
     <tag>アダルトアニメ</tag>…<actor><name>茅ヶ崎ありす</name></actor>
   </movie>
   ```

   这些字段**不在该库 61 条记录的任何一条里**（逐条搜过）。而且我们的写入器是整篇重写，
   不可能产出"根节点已关闭、后面还有元素"的文档 —— 这是**追加/合并**留下的。
3. **数据库里没有这些 GUID 路径的记录**，`app_logs` 里也只出现过正常文件名。
4. **现场抓到过窗口期**：手动重刮 `レイカ…第1巻`（写入 3 个文件）之后，`1LDK…第5話` 的
   `-poster.jpg` / `-fanart.jpg` 短暂消失，目录里冒出 `503e2cf4-….jpg`、`93ee6559-….jpg`；
   **约两分钟后 GUID 文件消失、正式名恢复、内容完好**。同时段还有一个我们**根本没碰**的视频
   （`寝取られた爆乳妻たち 前編`）的 fanart 被临时搬走 —— 说明它在按自己的节奏扫整个目录。

## 已排除

| 服务 | 证据 |
| --- | --- |
| **Symedia** | `config/config.yaml` 的 sync_list（14 项：`115/CS`、`115/Venuseo/*`、`115/Media Library/*`、`115/测试`…）**没有 `115/Test`**；`autosymlink.log` / `symedia.log` / `debug.log` 里从来没有 `/115/Test`；它当时正在忙 `115/Venuseo/国产传媒/…` |
| **MDC-NG** | `mdcng/config.json` 的 `target_folder` 是 `/media/115/整理待入库/AV` |
| **avscraper** | 见上面第 1、2、3 条 |

## 剩下最可能的（**未坐实**）

Emby：`EmbyServer/plugins` 里有 `NfoMetadata.dll`（内置 NFO 保存器）和 `Avdb.MagicTools.Plugin`。
「读已有 NFO → 合并自己的标签/演员 → 写回」正是 Emby 元数据保存器的行为，而追加块里的
标签/演员（眼鏡、打手槍、凌辱、`茅ヶ崎ありす`）是 **AV 库**字段 —— 与 AVDB 插件同源。
Emby 保存器写文件常走"临时名 + 改名"，在 CloudDrive2/115 挂载上改名失败就会留下 GUID 残骸。

**但没能坐实**：`EmbyServer/logs/embyserver.txt` 最近 3 MB 里搜不到 `115/Test` 和这些片名
（只覆盖最近几分钟，且该日志巨大，全量 grep 不现实）。

## 怎么验证（下次照做）

1. **隔离法**：临时停 Emby（或关掉该媒体库的 NFO 保存器 / 把测试目录移出媒体库）→ 重刮一个文件 →
   看目录里还出不出现 GUID 残骸。
2. **现场抓**：脚本同时做三件事 —— 触发一次重刮、每 2~3 秒列一次目录、一旦出现 GUID 就立刻
   比对哪个服务的日志文件在变长（本次就是靠"日志文件 mtime/size 谁在变"这一步缩小范围的）。
3. **判别归属**（最快的判据）：
   - GUID 文件内容与本仓库的 NFO 模板输出**逐字节相同** → 那是"我们的文件被搬走了"；
   - 出现 `</movie>` **之后**还有元素 → **一定有第二个写入器**（我们只整篇重写）。

## 处置

- 删掉 GUID 残骸，然后对受影响的视频逐个 **重刮**（重刮会强制覆盖图片）。
  本仓库这条路径现在会写运行日志：`手动重刮 <文件名>：<状态>` + `重刮写入 N 个文件（…）`。
- **根治**：一个目录只保留一个元数据写入者。要么让 avscraper 写、Emby 只读，
  要么关掉该库的 NFO 保存器；两个写入器抢同一个 `.nfo`，谁赢都不奇怪。

## 附：本机排查用的操作事实

- **本机（Windows 笔记本）直连日站是 HTTP 000**，浏览器能开是因为系统代理 `127.0.0.1:10808`。
  命令行要显式带上：`curl -x http://127.0.0.1:10808 ...`（该端口 HTTP 与 SOCKS5 都通）。
  抓 getchu 复核时必须这样走，否则会误判成"站点挂了"。
- **确认容器里跑的是哪个版本**：`GET http://<host>:9300/api/health` 的 `build_sha`；
  对照 GHCR 上 `latest` 的 OCI 标签 `org.opencontainers.image.revision`
  （匿名三步：`https://ghcr.io/token?...` → `manifests/latest` → amd64 manifest → config blob）。
  侧边栏的「检查更新」做的也是这件事（见 `GET /api/version/check`）。
- **Unraid 必须 force update**（`docker pull` 后再重建），否则镜像标签是 `latest` 但内容还是旧的。
- **SMB 上 grep 极慢**（十几 MB 就能跑到超时），要先把文件 `cp` 到本地再 grep；
  而 `ls` / `stat` 很快 —— 排查时优先用目录列举，不要用远程 grep。
