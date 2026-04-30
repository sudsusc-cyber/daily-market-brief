# ADR-0003 — 公司 Logo 内嵌:CID 方案 + Google S2 Favicon

- 日期:2026-04-30
- 状态:已采纳
- 影响范围:M2(已实施)/ M3(配色微调时复核 logo 与背景对比度)/ M5
- 触发:用户在 M2 验收期间追加要求"每家公司股票前面加 logo,iOS 与安卓都能完美显示"

## 决策

1. **图片传输方式:CID 内嵌附件(multipart/related)**
   - PLAN 第 7 节 M5 仅禁止 `<style>` / flexbox / grid / JS;未禁止 `<img>`
   - 邮件标准 RFC 2392 的 `cid:` URL 是所有主流客户端**默认显示**(无需用户点"加载远程图片")的唯一方式
   - 远程 `<img src="https://...">` / Base64 data URI / CSS background 全部因兼容性失败,详细对比见 commit `3f445a5` 后的提交说明 / 用户回复
2. **Logo 数据源:三段优先级(Google S2 → 各 CDN override)**

   - **第一层(默认)**:Google S2 favicon — `https://www.google.com/s2/favicons?domain=<domain>&sz=128`
     - 大公司 favicon 通常 ≥ 1KB,28×28 显示尺寸下视觉清晰
     - 即使经过本机科学上网代理也稳定可达,返回 RGBA PNG

   - **第二层(单独 override)**:对 favicon 过小或形状不合适的 ticker,在 `scripts/fetch_logos.py::LOGO_OVERRIDES` 单独指定 URL,优先级高于 Google S2。当前两类源:
     - **Wikimedia Commons** (`commons.wikimedia.org/wiki/Special:FilePath/<File>?width=512`):自动 302 到 SVG 渲染的 PNG,适合官方有 SVG logo 的公司
     - **Financial Modeling Prep CDN** (`financialmodelingprep.com/image-stock/<TICKER>.png`):公开 ticker logo CDN,免费、统一 128×128 方形 PNG,适合 Wikimedia 上 logo 是横长条形或者根本无 logo 文件的情况(如 BRK)

   - **当前 override 列表**(M2 验收期间用户手动指出"糊"或"看不到"的案例):
     | Ticker | 源 | 原因 |
     |---|---|---|
     | `TSM` | Wikimedia: `Tsmc-text.svg` | Google S2 仅 263 bytes,糊 |
     | `COST` | FMP: `COST.png` | Wikimedia 上 Costco logo 是 960×344 长条形,压成 28×28 方形会糊 |
     | `BRK.B` | FMP: `BRK.B.png` | Berkshire 官网无 favicon,Wikipedia 主条目用大楼照片,FMP 有 8.6KB 方形 logo |

   - **`GOOG` 的特殊处理**:不进 override,改走 `google.com` 而非 `abc.xyz`(后者 favicon 504 bytes 糊;前者 2.3KB 清晰)。修改在 `src/config.py::HOLDINGS`

   - **Wikimedia UA 要求**:必须带联系方式 `daily-market-brief/0.1 (sudsusc@gmail.com)`,否则返回 400(实测过)

   - **被替代方案**:
     - Clearbit Logo API(原首选,2024 起停服,SSL 失败)
     - Brandfetch CDN(免费档需 token)
     - `<company>.com/apple-touch-icon.png`(测过 Costco 的,本机网络偶发超时,改用更稳定的 FMP CDN)
3. **缺 logo 的文字 fallback(目前未触发,但保留机制)**
   - Berkshire 主体官网没有 favicon,初版 Google S2 / Wikimedia 都拿不到。最初决定不做 hack,在模板里加文字 fallback:深墨圆角 + 白字 ticker 前 3 字符
   - **后来加上 Financial Modeling Prep CDN 后,BRK.B 拿到了 8.6KB 方形 logo,fallback 不再触发**
   - 文字 fallback 机制保留在模板里(`{% if has_logo %}` 分支),万一未来某只 ticker 三个数据源都拿不到,UI 不会破
4. **Logo 文件管理**
   - 位置:`assets/logos/<slug>.png`,slug 来自 `Holding.slug`(BRK.B → BRK_B,0700.HK → 0700_HK)
   - 入库:**logo 文件提交进 git**。理由:每家公司 logo 极少变,提交比每次 fetch 更稳;文件总大小 9KB 量级,可忽略
   - 重抓:`scripts/fetch_logos.py` 一次性脚本,后续新增持仓或者发现 logo 过时时手动重跑
5. **CID 命名约定**
   - 格式:`logo_<slug>`,例如 `logo_NVDA`、`logo_0700_HK`、`logo_BRK_B`
   - 由 `Holding.logo_cid` 属性统一生成,模板与 sender 都用同一个值

## 与 PLAN 的偏差

| 条款 | 原文 | 偏差 |
|---|---|---|
| 第 2 节"邮件视觉方向" | "emoji / 图标 **零容忍**,一个都不要" | 放入了公司 logo |
| 解读 | "图标"在 PLAN 上下文指**装饰性**图标(checkmark / 星号 / 表情等),区别于**信息性**的公司标识 | 用户主动追加该需求,且公司 logo 用作"视觉辅助识别",不构成花哨干扰 |
| 风险 | 整体克制风格被打破 | 28×28 圆角小尺寸 + 单一行内位置 + 不增加任何颜色/特效,与 oxblood 强调色不冲突 |

## 实施变更摘要

| 文件 | 变更 |
|---|---|
| `src/config.py` | `Holding` 新增 `logo_domain` / `slug` / `logo_cid` 属性;12 只持仓填好 domain;`GOOG` 的 domain 由 `abc.xyz` 改为 `google.com`(更高分辨率 favicon) |
| `scripts/fetch_logos.py` | 新增,默认从 Google S2 favicon 拉 logo;`LOGO_OVERRIDES` 字典对 TSM / COST / BRK.B 走 Wikimedia 或 FMP |
| `assets/logos/*.png` | 新增,12 个文件(总 ~50KB) |
| `src/sender/smtp_sender.py` | 改为 multipart/related;新增 `InlineImage` 数据类、`_build_image_part`、`_detect_image_subtype`(magic bytes 推 MIME,允许 .png 文件实际是 JPEG) |
| `src/renderer/templates/email.html.j2` | 标的单元格内嵌 28×28 logo + 文字 fallback;表头第一栏改嵌套表对齐 ticker 文字列(用户验收要求) |
| `src/renderer/render.py` | `render_email()` 新增 `logo_cids: dict[str, str]` 参数 |
| `src/main.py` | 新增 `_load_logo_assets()` 扫描 `assets/logos/`,组装 `InlineImage` 列表 + cid 映射 |
| `scripts/preview_email.py` | 渲染后把 `cid:` 替换为 base64 data URI,MIME 类型按 magic bytes 推断;mock 数据修正 9992.HK 为正常状态 |
| `scripts/preview_server.py` | 复用 `preview_email.render_preview()`,统一 logo 内联逻辑 |

## 后续注意

- **暗色模式邮件客户端**:有些 logo 是深色 PNG(如 KO 的可口可乐),在深色背景下可能看不清。M5 兼容性测试时需要在 iOS Mail / Gmail 暗模式下复核
- **logo 失效**:某只持仓改名 / 公司被收购导致 favicon 改变后,需重跑 `fetch_logos.py`。M6 加 cron 时考虑在 workflow 里附带每周日重抓一次的步骤
- **图标风格不统一**:Google S2 favicon 来自各公司,色调风格不齐(MSFT 蓝、COST 红、AAPL 黑、KO 黑、AXP 蓝)。这是 brand 客观情况,不强求统一
