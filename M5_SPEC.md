# PLAN.md 增补:M5 详细规格(v2 — 四季策展版)

> **使用说明**:
> 这份内容**替换** PLAN.md 原第 7 节里的"M5 — HTML 精美化 + 兼容性"那一整段。
> 其他章节(M1-M4、M6、第 0-6 节、第 8-14 节、附录 A/B)全部保留不动。
> 第 5 节"邮件样板示意"中的伪示例,顶部要加上"刊头图区块"(本文档第十一部分给出新版伪示例)。
>
> **v2 变更**:策展规模从 60-100 张扩大到 **200 张分四季**,每季 50 张。引入按月份切换图池的逻辑,增强季节呼应。

---

## M5 — HTML 精美化 + 每日刊头图 + 邮件兼容性

**目标**:把邮件从 M4 完成后的"功能正确但简陋"升级到 PLAN 第 2 节描述的设计标准,并新增**每日刊头图**功能。最终结果必须满足:在 QQ 邮箱、Gmail 网页版、Apple Mail 中显示效果一致;头图模块**永不阻塞邮件发送**。

### M5.1 邮件视觉重写(对应 PLAN 第 2 节)

CC 任务:

1. 重写 `src/renderer/templates/email.html.j2`,严格遵守 PLAN 第 2 节"邮件视觉方向"中所有规格(背景色、字体、强调色、涨跌色、留白、emoji 零容忍)。
2. 使用 **table-based layout**(邮件 HTML 强制要求),inline CSS,不用 `<style>` 块,不用 flexbox / grid,不用 JavaScript。
3. 字体 fallback 链处理中英混排。具体规则:
   - 标题字体栈:`'Noto Serif SC', 'Source Han Serif SC', 'Songti SC', 'STSong', 'Newsreader', 'Source Serif Pro', Georgia, serif`
   - 正文字体栈:`'Noto Serif SC', 'Source Han Serif SC', 'Songti SC', 'STSong', 'Charter', 'Georgia', serif`
   - 数字优先用等宽衬线:`'Source Serif Pro', 'Charter', Georgia, serif`,确保表格对齐
4. 邮件最大宽度 **640px**,水平居中。在窄屏(< 640px)等比缩放。
5. 处理夜间模式适配:深色模式下不要一团黑,加 `<meta name="color-scheme" content="light">`(部分客户端识别)。
6. 加入"打印友好"样式,`@media print` 中保持米色背景但去掉所有装饰。
7. **延续 M4 修复阶段已完成的工作**(字号 16px、行高 1.9、letter-spacing 字距、thin space 中英隔离、脚注角标 oxblood 无下划线等),不要回退这些细节。

**禁止**:emoji、icon、`<style>` 块、flexbox / grid、JavaScript、Inter / Roboto / Arial。

---

### M5.2 每日刊头图(新增功能)

#### 设计意图

每日邮件**最顶部**新增一条 16:5 比例的窄横幅图,下方仍是米色背景的纯文字内容。视觉效果接近《纽约时报》周末版头版上方的全景照——刊头有图,正文克制。**不破坏 Berkshire 致股东信的内容氛围**,但给每天的邮件一点季节感和呼吸感。

参考意象:翻开《Monocle》《Kinfolk》《纽约客》的当期封面,而不是 iPhone 锁屏。

#### 视觉规格(必须严格遵守)

| 项 | 规格 |
|---|---|
| 显示宽度 | 640px(与邮件正文同宽) |
| 显示高度 | 200px(16:5 比例) |
| 源图尺寸 | 1280×400(2x retina) |
| 文件大小 | **必须 ≤ 80KB**,超出则用更小宽度或更低 quality 压缩 |
| 格式 | JPEG (`quality=80`),不用 PNG / WebP / AVIF(邮件兼容性) |
| 圆角 | 无 |
| 阴影 | 无 |

#### 视觉处理细节

1. 图上方加 **1px 极细深色线**(色 `#1A1A1A`,opacity `0.15`),与邮件最顶部边距分隔。
2. 图下方紧跟当日日期(中文衬线小字,中灰 `#6B6B6B`),例:`二〇二六年五月一日 · 周五`。日期下再放主标题"每日晨报"。
3. 图本身**不加任何文字、不加 logo、不加角标**。图就是图。
4. 不要给图加 hover 效果(邮件 HTML 不支持也不应支持)。

#### 选图"克制"标准(关键约束)

策展候选图时严格遵守:

✅ **要**:雾气山脉、清晨水面、北欧极简海岸、日式枯山水、大片留白的天空、单色调建筑(混凝土 / 石料 / 砖墙)、铁路尽头、单棵树、积雪原野、远处的城市天际线、薄云、湖面倒影、苔藓、岩石纹理、雪原。

✅ **色调**:低饱和、莫兰迪色系、灰调、冷调或暖灰。RGB 任意通道均值 < 200(避免过曝),饱和度低于 50%。

❌ **不要**:鲜艳花海、彩虹、夕阳橙红、热带海滩、鲜艳花田、HDR 重度处理、动物特写、人物、城市霓虹、抽象数码艺术、滤镜痕迹明显的"网红风"、明显的 AI 生成图。

❌ **绝对不要**:励志风(山顶剪影、跑步背影、晨曦中举手、"梦想"字样)、商业图库感强的素材、明显的演员摆拍。

---

### M5.3 四季策展架构(关键变更 v2)

#### 总体结构

候选图按四季分组,每季 50 张,共 200 张。`config/curated_images.json` 的 schema:

```json
{
  "version": 2,
  "updated_at": "2026-04-30",
  "policy": "northern_hemisphere_meteorological",
  "seasons": {
    "spring": [
      {
        "id": "1505765050516-f72dcac9c60e",
        "credit": "Photo by 摄影师名 on Unsplash",
        "tags": ["fog", "mountain", "morning"],
        "palette": "cool_grey"
      }
    ],
    "summer": [...],
    "autumn": [...],
    "winter": [...]
  }
}
```

#### 四季月份定义(北半球)

| 季节 | 月份 | 视觉主题 | 色调倾向 |
|---|---|---|---|
| **春**(spring) | 3、4、5 月 | 雾、新绿、薄雨、湿气、嫩芽、雪水初融、薄云 | 冷灰、青灰、淡绿 |
| **夏**(summer) | 6、7、8 月 | 浓雾、海面、白云、深绿、雷雨、苔藓、林间光斑 | 深绿、青蓝、铅灰 |
| **秋**(autumn) | 9、10、11 月 | 干燥、米色、黄叶、枯草、远山、薄霜、稻田 | 米色、土黄、暖灰 |
| **冬**(winter) | 12、1、2 月 | 雪、灰、白、北欧、极简、单色、寒山、冰湖 | 极简白、冷灰、铅蓝 |

#### 选图算法

```python
def pick_season(today: date) -> str:
    m = today.month
    if m in (3, 4, 5):
        return "spring"
    if m in (6, 7, 8):
        return "summer"
    if m in (9, 10, 11):
        return "autumn"
    return "winter"  # 12, 1, 2

def pick_image(today: date, library: dict) -> dict:
    season = pick_season(today)
    pool = library["seasons"][season]
    # 用 toordinal 做种子,同一天结果一致便于排错
    index = today.toordinal() % len(pool)
    return pool[index]
```

每季 50 张,**每张图约 36 天才轮一次**,完全无重复感。

#### 跨季过渡处理

跨季那一天(比如 5 月 31 日 → 6 月 1 日)切换图池是 hard cut。这是有意为之的——你应该在 6 月 1 日早晨打开邮件时**感觉到"今天换季了"**,而不是无感渐变。

---

#### 三层降级架构(永不出错)

##### 主源:Unsplash 策展白名单(走 `curated_images.json`)

- **不用** Unsplash 的 random / search 接口(返回不可控,经常翻车)。
- 改用预先策展的图 ID 白名单 + 当季图池 + ordinal 索引。
- **首次实现时**:CC 先策展 200 张候选图,提交到 `config/curated_images.json`,**等用户审阅通过后再继续集成**。
  - 策展方法见下方"M5.4 策展工作流"。
  - **必须留出"用户审阅 + 删减不喜欢的图"的环节**,这是用户保留审美控制权的关键。
- **URL 拼接**:`https://images.unsplash.com/photo-{id}?w=1280&h=400&fit=crop&q=80&auto=format`

##### 降级 1:Bing 每日壁纸

- **触发条件**:主源 timeout(> 5s)、HTTP 4xx / 5xx、或 Content-Length > 80KB 拉不下来。
- **API**:`https://www.bing.com/HPImageArchive.aspx?format=js&idx=0&n=1&mkt=en-US`
- 解析 JSON,取 `images[0].url`,前缀 `https://www.bing.com`。
- 拼参数 `&w=1280&h=400` 由 Bing CDN 处理。
- 优点:Bing 每日图经过编辑筛选,质量稳定;**但风格不可控**——节日装饰、动物特写偶尔会出,这是已知妥协。

##### 降级 2:本地 fallback 静态图

- 仓库内 `assets/fallback_header.jpg` 永久兜底图。
- 选一张**永远安全**的图:雾山 / 留白海面 / 单色天空,JPEG ≤ 80KB,1280×400。
- 这张图必须随仓库 git commit。
- 启动时校验 `assets/fallback_header.jpg` 存在且可读,不存在则启动失败并告警。

---

### M5.4 策展工作流(CC 第一步必做)

**这是 M5 的第一个交付物,完成后必须停下等用户审阅。**

#### 步骤

1. **CC 通过 web 搜索(或调用 Unsplash API)寻找符合美学标准的图。**
   - 推荐搜索关键词:
     - 春:`spring fog`、`misty mountain morning`、`cherry blossom monochrome`、`new green minimal`、`spring rain landscape`
     - 夏:`summer fog ocean`、`deep forest light`、`overcast sea`、`mossy rocks`、`misty lake summer`
     - 秋:`autumn mist field`、`dry grass minimal`、`golden light landscape`、`hazy mountain autumn`、`harvested field`
     - 冬:`snow minimal landscape`、`nordic winter`、`fog snow forest`、`monochrome winter`、`frozen lake`
   - 排除关键词:`vibrant`、`colorful`、`sunset`、`neon`、`cityscape night`、`woman`、`man`、`portrait`、`wedding`、`luxury`

2. **每张候选图必须验证 URL 200 OK**(`requests.head`),不能凭空编造 photo_id。

3. **每张图必须人工评估是否符合"克制标准"**——CC 自己先按上述美学规则过一遍,明显不合的(夕阳、人物、动物)直接淘汰,不要交给用户审。

4. **填充 `config/curated_images.json`**:每季严格 50 张,合计 200 张。

5. **生成一份审阅文档** `docs/curated_images_review.md`,格式:

   ```markdown
   # 候选图审阅清单

   ## 春(50 张)

   | # | Photo ID | 摄影师 | 主题标签 | 预览链接 | 决定 |
   |---|---|---|---|---|---|
   | 1 | 1505765050516-f72dcac9c60e | John Doe | fog, mountain | [预览](https://unsplash.com/photos/1505765050516-f72dcac9c60e) | |
   | 2 | ... | ... | ... | ... | |

   ## 夏 / 秋 / 冬 同上
   ```

   每张图给一个 **Unsplash 详情页链接**,用户点开就能看图。

6. **CC 提交后停下,通知用户审阅**,等用户回复"OK 进入下一步"或"删除第 X 张"等指令后才能继续集成。

#### 用户审阅流程(写在 docs 里供以后参考)

用户拿到 `curated_images_review.md` 后:

- 在"预览链接"栏点开每张图
- 不喜欢的标记 `❌`(在表格末尾"决定"那一列)
- 想换的留 `🔄` + 写需求(例如"想要更多积雪森林,少点海面")
- 全部审完后,告诉 CC 哪些删除、哪些替换、缺多少补多少

CC 收到反馈后:

- 删除标 `❌` 的
- 按 `🔄` 需求重新搜索补足
- 重新提交审阅文档,再次等用户确认

**直到用户明确说"全部 OK,可以集成"**,CC 才能开始集成头图模块。

---

### M5.5 嵌入 HTML 的技术规范

**绝对不要用 `background-image`** —— Outlook、QQ 网页版渲染不稳。

**必须用 `<img>` 标签**:

```html
<!-- 整体用 div 套一层米色底,作为图加载失败的兜底 -->
<div style="background-color:#EFE9DD; max-width:640px; margin:0 auto;">
  <img src="https://images.unsplash.com/photo-xxxx?w=1280&h=400&fit=crop&q=80&auto=format"
       width="640"
       height="200"
       alt=""
       style="display:block; width:100%; max-width:640px; height:auto; border:0; outline:none; text-decoration:none; -ms-interpolation-mode:bicubic;">
</div>
```

**关键约束**(每一条都有"踩过坑"的理由):

1. `display:block` —— 消除 `<img>` 下方诡异 4-6px 空隙
2. `border:0` —— IE / 旧 Outlook 默认 img 有蓝色边框
3. `outline:none; text-decoration:none` —— 部分客户端给图加链接装饰
4. `-ms-interpolation-mode:bicubic` —— Outlook 对缩放图的优化指令
5. **HTML 属性 `width="640"` `height="200"` + style 都要写** —— 部分 Outlook 版本只看 HTML 属性
6. `alt=""` —— 空 alt,因为图是装饰性的
7. **外层 div 用 `background-color:#EFE9DD`** —— 默认不加载远程图时,看到的是温和米色横条而非裂图标

---

### M5.6 模块代码结构

新增文件 `src/collectors/header_image.py`,接口:

```python
from datetime import date

def get_today_header_image(target_date: date) -> dict:
    """
    获取今日刊头图。三层降级,永不抛异常。

    返回:
        {
          "url": "https://...",
          "source": "unsplash" | "bing" | "fallback",
          "season": "spring" | "summer" | "autumn" | "winter" | None,
          "credit": "Photo by X on Unsplash" 或 None,
          "width": 640,
          "height": 200
        }

    任何错误都内部消化,最差情况返回 fallback 本地图(season=None)。
    """
```

实现要点:

1. **网络请求超时**:5 秒
2. **HEAD 验证**:把 URL 放进邮件之前用 `requests.head()` 验证 200 OK + Content-Length 合理;HEAD 失败立刻走降级
3. **不重试**:每层只尝试一次,失败立刻走下一层(总耗时上限 ~10 秒)
4. **日志**:每次执行记录最终用了哪一层 + 季节,方便监控降级率
5. **`assets/fallback_header.jpg` 路径**用相对路径,启动时校验存在
6. **`config/curated_images.json` 加载失败**(文件丢失 / JSON 解析错):直接走 Bing 降级,不要崩溃

---

### M5.7 集成到主流程

`src/main.py` 中:

```python
header = get_today_header_image(today)
context["header_image"] = header
```

模板中:

```jinja
<div style="background-color:#EFE9DD; max-width:640px; margin:0 auto;">
  <img src="{{ header.url }}"
       width="{{ header.width }}"
       height="{{ header.height }}"
       alt=""
       style="display:block; width:100%; max-width:640px; height:auto; border:0; outline:none; text-decoration:none;">
</div>
```

页脚 credit:

```jinja
{% if header.credit %}
<p style="font-size:11px; color:#999; text-align:center; margin-top:24px;">
  封面图 · {{ header.credit }}
</p>
{% endif %}
```

理由:Unsplash 协议要求显示摄影师署名,放在页脚极小字。Bing / fallback 没有 credit 就不显示。

---

### M5.8 多客户端兼容性测试

CC 任务:

1. **测试矩阵**:
   - QQ 邮箱网页版(主要,用户实际使用)
   - QQ 邮箱 macOS 客户端
   - Gmail 网页版(用户可能转发查看)
   - Apple Mail(macOS 系统邮件)
   - Outlook 网页版

2. **测试方法**:
   - CC 在每个里程碑发一封测试邮件(用 `scripts/send_test_email.py`)
   - 用户在每个客户端打开后,主观评估是否符合"严肃刊物感"

3. **必须验收的渲染特性**:
   - 头图加载正常 + 不加载时降级显示米色横条
   - 中文衬线字体正确显示(在没装思源宋体的 Mac 上回退到系统宋体)
   - 数字表格对齐
   - 涨跌色非鲜艳红绿
   - 强调色(oxblood / navy / gold 三选一)只在标题和关键数字出现

---

### M5.9 运行可靠性 SLO

写进 `docs/decisions/0005-email-rendering-slo.md`,作为长期约束:

1. **头图模块永远不能阻塞邮件发送** —— 任何失败都走降级,绝不 raise。
2. **三层降级路径中,本地兜底必须 100% 可用** —— `assets/fallback_header.jpg` 启动时校验,缺失则启动失败 + 告警邮件。
3. **`curated_images.json` 加载失败不能崩主流程** —— 直接走 Bing 降级。
4. **新增单元测试** `tests/test_header_image.py`,覆盖场景:
   - 主源 200 OK → 用 Unsplash(春夏秋冬各一张图测试)
   - 主源超时 → fallback Bing
   - Bing 也超时 → fallback 本地
   - `curated_images.json` 文件丢失 → fallback Bing
   - 本地图也丢了 → 邮件不显示头图,但**正文照常发出**

---

### M5.10 长期维护:每季补图

写进 `docs/runbook.md` 的"维护"章节:

> **每 6 个月补图一次**(每年 4 月、10 月各一次):
>
> 1. 用户回顾过去 6 个月最满意的图(在 Apple Mail 邮件里挑出 5-10 张特别喜欢的)
> 2. CC 按"风格相近"原则补 30-50 张同类图,加进对应季节
> 3. 用户审阅,通过后 commit
>
> **如果某张图突然 404**(Unsplash 偶尔会删图):自动降级到 Bing 当天该不影响用户。事后批量检查 `curated_images.json` 中失效的 ID,从清单中删除并补新图。
>
> **每年 1 月**:全量回顾 200 张图,删除一年没轮上 / 用户没标记过喜欢的,扩张到 220 张做实验。

---

### M5 验收标准

完成 M5 必须满足以下**每一条**:

1. ✅ 用户在 QQ 邮箱中看到的视觉效果与第 5 节(更新版)伪示例的"严肃刊物感"一致
2. ✅ 头图正确显示;关闭网络模拟时降级到米色横条不崩
3. ✅ 同时在 Gmail 网页版、Apple Mail 打开,布局不崩
4. ✅ 中文衬线字体正确
5. ✅ 单元测试 `test_header_image.py` 全部通过
6. ✅ `config/curated_images.json` 含**严格 200 张** = 4 季 × 50 张,全部用户审阅过
7. ✅ ADR `0005-email-rendering-slo.md` 已提交
8. ✅ `assets/fallback_header.jpg` ≤ 80KB,可读
9. ✅ 用户主观评价:"这封邮件我愿意每天读"

---

### M5 禁止做的

- ❌ 改动 M1-M4 已经验收通过的 collector / processor 逻辑(若发现 bug 单独处理,不混进 M5)
- ❌ **改动 M4 修复阶段已经定稿的邮件正文内容、措辞、区块结构、栏目标题、脚注样式、字距设置**(M5 只新增刊头图区,不动正文)
- ❌ **以"美化"或"统一风格"为名,把 M4 已通过的正文部分按 M5_SPEC 或 PLAN 第 5 节伪示例去"重构"**——若觉得有不一致的地方,先停下来问用户
- ❌ 跳过"用户审阅策展白名单"环节,自己随机抓图
- ❌ 用 `background-image` 实现头图
- ❌ 用 base64 嵌入图(邮件 size 暴涨)
- ❌ 用 emoji / icon 装饰任何区块(包括头图区域)
- ❌ 提交超过 80KB 的 fallback 图
- ❌ 让 LLM(DeepSeek)动态选图——这是个被拒绝的方案,LLM 看不见图,会翻车
- ❌ 引入 random,选图必须是确定性的 `today.toordinal() % len(pool)`

---

## 关于 PLAN.md 第 5 节"邮件样板示意"的处理

**⚠️ 极其重要的指令(给 CC 看):**

PLAN.md 第 5 节里那个伪示例,是 v1.0 阶段的初稿,**已经过时**。M4 修复阶段用户已经亲自和 CC 一起把所有正文区块的内容、措辞、结构、用词都重新打磨过了——目前邮件中"情绪温度计 / 持仓信号 / 昨日动态 / 关键发言 / 宏观视野 / 页脚"这些区块的实际呈现,**才是用户认可的正确版本**,不是 PLAN.md 第 5 节的旧示例。

**因此 M5 阶段:**

- ❌ **不要**参考 PLAN.md 第 5 节的伪示例去"按样板改正文区"
- ❌ **不要**根据本 M5_SPEC 文档去重写、重构、"美化"任何已经验收过的正文区块的措辞、结构、字段顺序、栏目标题
- ❌ **不要**改动 M4 修复阶段已确定的:章节标题写法、letter-spacing、thin space、脚注角标样式、"昨日动态"按公司分行结构、"宏观视野"按主题分段结构、页脚 credit 写法
- ✅ **只在邮件最顶部新增一个"刊头图区域"**,这个区域之前不存在,现在加进来。其位置:在原邮件最顶部边距之后、原"日期 + 标题"区块之前
- ✅ 如果觉得新增刊头图后,**仅刊头图本身与下方"日期 + 主标题"之间的间距/分隔线**需要协调,可以微调那一处的 margin 和 hr,但**仅限这一处**

**M5 阶段对邮件模板的改动范围,严格限定为以下 3 类:**

1. **新增**:刊头图区域(顶部 16:5 比例 `<img>` + 米色降级背景 div)
2. **校准**:刊头图与下方"日期 + 主标题"之间的过渡(margin、可能的 hr)
3. **跨客户端兼容性微调**:在 QQ 邮箱 / Gmail / Apple Mail / Outlook 网页版测试发现的渲染 bug 修复(如某客户端不支持某属性,需要加 fallback)

**除上述 3 类之外,任何对 email.html.j2 已有正文部分的改动,都必须先停下来征得用户同意后再做。**

**同步动作**:CC 应在 M5 开工前**先打开当前的 `email.html.j2`,把现状中已经存在的所有区块结构原样保留**,把刊头图作为新增模块插入即可,不要"重构"。

---

## 关于 PLAN.md 第 5 节伪示例本身

PLAN.md 第 5 节的伪示例**也需要更新一下**,但**只更新顶部刊头图部分**,正文示例段保持原样不动(因为 PLAN.md 是规格文档,改动它会让历史决策记录混乱)。

具体做法:

- 在 PLAN.md 第 5 节伪示例的最顶部插入一个"刊头图占位":

  ```
  ┌─────────────────────────────────────────────────┐
  │ ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ │
  │ ░░ [16:5 刊头图,按四季策展自动切换] ░░░░░░ │
  │ ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ │
  │ ──────────────────────────────────────────────  │
  │                                                 │
  ```

- 然后**保留** PLAN.md 第 5 节原有的伪示例剩余部分(日期、标题、各区块)不变。
- 在 PLAN.md 第 5 节末尾加一行**说明**:"上述伪示例中的正文区块结构与措辞**仅供参考方位**,实际呈现以 M4 修复阶段产出的 `email.html.j2` 为准。"

---

## 文档变更记录

- v1.0 (2026-04-30):初始版本
- v1.1 (2026-04-30):新增"每日刊头图",60-100 张策展
- v2.0 (2026-04-30):策展规模扩大到 200 张分四季;增加跨季切换逻辑;增加长期维护章节;增加候选图审阅工作流
- **v2.1 (2026-04-30):删除可能误导 CC 的"邮件样板示意更新版"伪代码段;明确 M5 不动 M4 修复后已定稿的正文内容,只新增刊头图区**
