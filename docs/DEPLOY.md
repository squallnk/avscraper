# 部署到 Unraid

## 目标环境

一个常见的旁路由组网（**下面是示例地址，换成你自己的**）：

```
光猫
 └─ PVE / 物理机
     ├─ 主路由 192.168.1.253        拨号 / DHCP
     └─ 旁路由 192.168.1.252        科学上网
          ↑
        Unraid 192.168.1.254        网关指向旁路由
```

**关键结论：本项目通常不需要配代理。**

宿主机的默认网关指向旁路由时，Docker 默认 `bridge` 网络是 NAT 到宿主，
容器继承宿主的默认路由，于是自动走代理。所以 `proxy` 保持空字符串即可。

### 三个要注意的地方

1. **Docker 网络模式**。默认 `bridge` 会继承宿主路由。
   如果用了 `macvlan` 或自定义 bridge 并手工指定了网关，就要单独确认网关指向旁路由。
   `network_mode: host` 也继承宿主，同样可以。

2. **DNS**。容器 DNS 继承宿主 `/etc/resolv.conf`。
   如果宿主用的是主路由而它转发到国内上游，javbus 这类域名的解析可能被污染，
   表现为 `netcheck` 里 "解析失败" 或连到错误 IP。这时在 compose 里显式指定旁路由：

   ```yaml
   dns:
     - 192.168.1.252     # 换成你旁路由的地址
   ```

3. **端口**。默认用 `9300`。选一个没被别的容器占用的即可。

## compose

```yaml
services:
  avscraper:
    image: ghcr.io/<你的用户名>/avscraper:latest
    container_name: avscraper
    restart: unless-stopped
    ports:
      - "9300:9300"
    environment:
      - AVS_HOST=0.0.0.0
      - AVS_PORT=9300
      - AVS_DATA_DIR=/app/data
      - AVS_ALLOWED_ROOTS=/media/media
    volumes:
      - /mnt/user/appdata/avscraper:/app/data
      # 要刮削的媒体目录；建议先只挂一个测试目录
      - /mnt/user/clouddrive/115/Test:/media/media
    # 若出现域名解析异常再加这一行
    # dns:
    #   - 192.168.1.252
```

> 注意 `AVS_ALLOWED_ROOTS` 与 `cd2_mappings` 用的都是**容器内路径**
> （`/media/media`），不是宿主机路径。

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
docker exec -it avscraper python -m server.cli classify "/media/media/某个文件.mp4"
```

确认番号、内容类型、季集解析符合预期。里番的集数来源会标出来（`标记` / `分集兜底`）。

### 4. 扫描（联网、只写数据库）

WebUI「任务」页提交，或者：

```bash
docker exec -it avscraper python -m server.cli scan --root /media/media --limit 3
```

**先 `--limit 3`**。然后看「刮削记录」页：
字段来源列会显示每个字段是哪个站给的，一眼能看出哪个源没工作。

### 5. 打开写入

前四步都对了，才在「设置」里关掉 `dry_run`、打开 `organize_enabled`，
并按需打开图片下载（剧照默认关闭）。

**注意**：`organize_enabled` 只影响本项目自己的写操作（NFO、图片、以及显式调用整理）。
文件归档仍然由 Symedia 负责 —— 不要两边都开整理。
