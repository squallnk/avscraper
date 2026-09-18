# 怎么抓真实固件

真实固件是这个项目里最重要的一环。**合成 HTML 只能证明"代码没写错"，
证明不了"能对上真实页面"**。已经抓到的问题全都来自真实页面：

- javdb 的评分在「評分」面板格里，不在 `.score`（后者本站根本没有）
- javdb 的演员链接混着男演员，必须优先取 `.actor-female`
- javdb 的剧照列表混着封面，不排掉封面会重复展示
- javbus 的标题必须按番号前缀剥掉，按第一个连字符截断会把番号切碎
- getchu 的搜索结果页夹着推荐位，且和真结果同样是 `soft.phtml?id=` 形态
- freejavbt 的分类页和详情页在同一层路径下（`/zh/censored` vs `/zh/MIDV-123`）

## 开发机连不上这些站点时的做法

本项目开发机对 javbus / javdb / freejavbt / github.com 都不通，
所以用「在能访问的机器上抓、再拷回开发机」的方式。

以 Unraid 为例（该机器能访问这些站点，且共享目录对开发机可见）：

```bash
mkdir -p /mnt/user/appdata/avscraper-capture
cd /mnt/user/appdata/avscraper-capture

UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'
fetch() { curl -sSL -m 30 -A "$UA" -H 'Accept-Language: zh-CN,zh;q=0.9' "$1" -o "$2" -w "$2  -> HTTP %{http_code}  %{size_download} bytes\n"; }

fetch "https://www.javbus.com/MIDV-123"           javbus_detail.html
fetch "https://javdb.com/search?q=MIDV-123&f=all" javdb_search.html
fetch "https://www.freejavbt.com/zh/search?wd=MIDV-123" freejavbt_search.html

# javdb 详情页要两步：先从搜索页取链接
HREF=$(grep -o 'href="/v/[A-Za-z0-9]*"' javdb_search.html | head -1 | sed 's/href="//;s/"//')
[ -n "$HREF" ] && fetch "https://javdb.com$HREF" javdb_detail.html

ls -la
```

然后在开发机上从共享目录拷进仓库：

```bash
cp "//192.168.0.254/appdata/avscraper-capture/"*.html tests/fixtures/
```

## 判定抓到的页面可用

| 现象 | 结论 |
|---|---|
| HTTP 200 + 几十~几百 KB | 可用 |
| HTTP 403 / 000 | 被拦或网络不通，需要 `-x <proxy>` |
| HTTP 200 但只有几 KB | 多半是 Cloudflare / 年龄确认拦截页，**不能用**，不要放进固件 |

放进去之前先确认页面里能找到该站的标志性结构（例如 javbus 的 `a.bigImage`、
javdb 的 `.movie-panel-info`），否则等于把拦截页当成正常页面写进测试。

## 对应的测试

一个源一份测试文件，断言写**结构事实**（片商名、日期、时长、条数、URL 前缀），
不要断言简介正文之类会变的文本。参考 `tests/test_source_parse.py` 与 `tests/test_getchu.py`。
