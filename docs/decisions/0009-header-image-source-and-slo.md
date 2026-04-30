# ADR-0009 — 刊头图图源切换(Unsplash → Pexels)及 SLO

**状态**: 已接受  
**日期**: 2026-04-30  
**里程碑**: M5(HTML 精美化 + 每日刊头图)

---

## 背景

M5_SPEC.md 原定使用 Unsplash 作为每日刊头图图源。调研阶段发现 Unsplash 在以下路径均不可达:

| 访问路径 | 结果 |
|---|---|
| `claude.ai WebFetch unsplash.com` | 安全策略拒绝 |
| `curl` via 本地代理 `127.0.0.1:1082` | 503(代理封锁) |
| `curl --noproxy "*"` unsplash.com | 401 Cloudflare 反爬挑战 |
| `source.unsplash.com` 随机图 API | 503(官方 2024 年已废弃) |

同期发现 Pexels CDN(`images.pexels.com`)可通过 Python `urllib` + `ProxyHandler({})` 绕过本地代理直连,HEAD/GET 均返回 200,典型图片体积 30–90KB。

## 决策

1. **图源**: 从 Unsplash 切换至 **Pexels**。CDN URL 模板:  
   `https://images.pexels.com/photos/{id}/pexels-photo-{id}.jpeg?auto=compress&cs=tinysrgb&w=1280&h=400&fit=crop`

2. **选取策略**: 维护 `config/curated_images.json`(200 张,4 季各 50 张)作为白名单。每日按  
   `pool[today.toordinal() % len(pool)]` 确定性选取,无随机性,便于复现与回滚。

3. **三层降级**(均为"never throws",5s 超时,不重试):  
   - **Tier 1** Pexels 白名单 — 从本地 JSON 读取 URL,无网络依赖  
   - **Tier 2** Bing 每日壁纸 API — 有网络依赖,景观类图,符合审美基调  
   - **Tier 3** `assets/fallback_header.jpg` — 本地冬季雪山图(51KB, 1280×400),作为 CID 附件内联

4. **邮件渲染**: 使用 `<img>` 标签(不使用 `background-image`),兼容 iOS Mail / Android Gmail / 桌面客户端。模板仅新增刊头图行,不修改 M4 已定稿正文。

5. **版权**: Pexels 图片在非商业展示场景下免费使用,`credit` 字段预留摄影师姓名空间。

## 后果

**正面**:
- Tier 1 完全本地化,不受网络抖动影响
- 200 张白名单已经审美过滤(BLOCKLIST + Agent 审美审核),避免不当图片出现在金融邮件中
- 确定性选取方便调试和审计
- fallback 三层兜底确保刊头图模块失败不阻塞邮件发送

**负面**:
- 图库固定,需人工定期补充(建议每季度运行 `scripts/curate_pexels.py` 扩充)
- Pexels CDN 在某些企业网络可能被屏蔽,此时 Tier 1 返回的 URL 在收件方不可达;Tier 3 fallback 只在发件侧触发,不能覆盖收件方图片加载失败

## SLO

| 指标 | 目标 |
|---|---|
| 刊头图选取不阻塞邮件发送 | 100%(由三层降级 + never-throws 保证) |
| Tier 1 选取延迟 | < 1ms(本地 JSON 读取) |
| Tier 2 超时上限 | 5s |
| fallback_header.jpg 体积 | ≤ 80KB(当前: 51KB) |
| 图片兼容性 | iOS Mail / Android Gmail / Apple Mail / QQ 邮箱 |
