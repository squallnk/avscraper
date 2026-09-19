# 站点访问墙与 Cookie

## 现状：当前所有源都不需要 Cookie

| 源 | 访问墙 | 处理方式 |
|---|---|---|
| **getchu** | 年龄确认页 | **`gc=gc` 参数绕过，不需要 Cookie** |
| **bangumi** | 无 | **公开 API，不需要 Cookie 也不需要代理** |
| javbus / javdb / freejavbt | 可能有地域/反爬拦截 | 走 `proxy` 配置 |
| ~~其它~~ | — | — |

猜错的代价很隐蔽：**"被墙了"和"这片没有"在日志里必须长得不一样**，
否则你会以为某个番号真的没有元数据。所以实现上两者分开：
墙 -> `SourceBlocked`（`blocked`），查不到 -> 返回 `None`（`not_found`）。

## getchu 的年龄墙是怎么过的

排查过程（留档，避免以后重新踩）：

1. `/php/nsearch.phtml` → **403**，带 Referer、带 `age=normal`、带 Cookie 都是 403。
2. `/php/search.phtml` → **302** 到 `/php/attestation.html?aurl=...`。
3. 年龄确认页的可读文本是：
   > 年齢認証ページ / これから先のページには年齢制限のある【R-18コンテンツ】が含まれています。
   > **【すすむ】 【もどる】**
4. 抓「【すすむ】」链接的原始 HTML：
   ```html
   <a href="https://www.getchu.com/php/search.phtml?aurl=...&genre=anime&gc=gc">【すすむ】</a>
   ```
5. 即：**这个按钮就是把 `gc=gc` 参数带回去**。直接请求
   `/php/search.phtml?search_keyword=<关键词>&gc=gc` 就能拿到搜索结果页（200）。

结论：**不需要 Cookie，也不需要浏览器**。`server/sources/getchu.py` 里的
`AGE_ACK_PARAM = "gc=gc"` 就是这件事，并且有一条测试专门断言它不会被"顺手清理"掉。

## 仍然存在的坑：搜索结果页夹着推荐位

搜索结果页里除了真正的结果，还有推荐位，**同样是 `soft.phtml?id=...` 形态**。
关键词查不到东西时（页面上写「該当する作品はありませんでした」），
如果只看 id 就会把推荐位当成匹配结果，产出一部完全不相干的片子的元数据。

处理方式：
- 先判 `該当する作品はありませんでした` 与 `ul.display li` 是否存在，没有命中直接返回 `None`；
- 取 id 只在结果列表 `ul.display` 里取，不扫全页。

这两条都有真实固件（有命中 / 无命中各一份）守着。

## Cookie 通路（留给将来需要的站点）

代码里的 Cookie 机制是完整可用的，只是当前没有源需要它：

```bash
python -m server.cli cookie list
python -m server.cli cookie set <source_id> --file cookie.txt
python -m server.cli cookie clear <source_id>
```

也可以走 WebUI「设置 → 站点 Cookie」（只列出 `needs_cookie=True` 的源）
或 `PUT /api/sources/{id}/cookie`。

约定：

- Cookie 存在本地 SQLite，**没有额外加密**，依赖文件系统权限。
- 读接口一律只回掩码 `***` 或长度，不回传明文。
- 前端把掩码原样传回来 = "不要改"，不会被覆盖成字面量 `***`。这条有专项测试。
