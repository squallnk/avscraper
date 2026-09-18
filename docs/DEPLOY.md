# 部署到 Unraid

## 目标环境

```
光猫
 └─ PVE (工控机 J6412)
     ├─ 爱快 192.168.0.253:8000   主路由：拨号 / DHCP
     └─ OpenWrt 192.168.0.252     旁路由：科学上网
          ↑
        Unraid 192.168.0.254       网关指向 OpenWrt
```

**关键结论：本项目不需要配代理。**

Unraid 的默认网关是 OpenWrt 旁路由，Docker 默认 `bridge` 网络是 NAT 到宿主，
容器继承宿主的默认路由，于是自动走科学上网。所以 `proxy` 保持空字符串即可。

### 三个要注意的地方

1. **Docker 网络模式**。默认 `bridge` 会继承宿主路由。
   如果用了 `macvlan` 或自定义 bridge 并手工指定了网关，就要单独确认网关指向 OpenWrt。
   `network_mode: host` 也继承宿主，同样可以。

2. **DNS**。容器 DNS 继承宿主 `/etc/resolv.conf`。
   如果宿主用的是爱快（`192.168.0.253`）而它转发到国内上游，javbus 这类域名的解析可能被污染，
   表现为 `netcheck` 里 "解析失败" 或连到错误 IP。这时在 compose 里显式指定：

   ```yaml
   dns:
     - 192.168.0.252
   ```

3. **端口**。Unraid 上 `9300/9301/9302` 实测空闲，本项目默认用 `9300`。
   已占用的是 3000（MoviePilot）、5000（Kavita）、8080（Adminer）、
   4081、8091、8095、8096、8097、9208、18000、19798。

## compose

```yaml
services:
  avscraper:
    build: .
    image: avscraper:local
    container_name: avscraper
    restart: unless-stopped
    ports:
      - "9300:9300"
    environment:
      - AVS_HOST=0.0.0.0
      - AVS_PORT=9300
      - AVS_DATA_DIR=/app/data
      - AVS_ALLOWED_ROOTS=/media/115test
    volumes:
      - /mnt/user/appdata/avscraper:/app/data
      - /mnt/user/File_Shared2/115/Test:/media/115test
    # 若出现域名解析异常再加这一行
    # dns:
    #   - 192.168.0.252
```

> 注意 `AVS_ALLOWED_ROOTS` 与 compose 里的 `cd2_mappings` 用的都是**容器内路径**
> （`/media/115test`），不是宿主机路径。

## 首次实跑顺序

**每一步都确认无误再进下一步。**

### 1. 自检（不联网、不写盘）

```bash
docker exec -it avscraper python -m server.cli selftest
```

看三件事：允许根目录对不对、`dry_run` 是不是 True、`organize_enabled` 是不是 False。

### 2. 出网探测（联网、不写盘）

```bash
docker exec -it avscraper python -m server.cli netcheck
```

四个源应该都是 `OK`。这是**验证容器能出去**的关键一步 ——
它同时验证了路由继承、DNS 解析、以及站点是否可达。

失败时的排查方向脚本会直接打印出来。

### 3. 分类核对（不联网）

```bash
docker exec -it avscraper python -m server.cli classify "/media/115test/某个文件.mp4"
```

确认番号、内容类型、季集解析符合预期。里番的集数来源会标出来（`标记` / `分集兜底`）。

### 4. 扫描（联网、只写数据库）

WebUI「任务」页提交，或者：

```bash
docker exec -it avscraper python -m server.cli scan --root /media/115test --limit 3
```

**先 `--limit 3`**。然后看「刮削记录」页：
字段来源列会显示每个字段是哪个站给的，一眼能看出哪个源没工作。

### 5. 打开写入

前四步都对了，才在「设置」里关掉 `dry_run`、打开 `organize_enabled`，
并按需打开图片下载（剧照默认关闭）。

**注意**：`organize_enabled` 只影响本项目自己的写操作（NFO、图片、以及显式调用整理）。
文件归档仍然由 Symedia 负责 —— 不要两边都开整理。
