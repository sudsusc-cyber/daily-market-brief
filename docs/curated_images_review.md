# 候选图审阅清单 — M5.4 第一交付物

- 版本:2
- 更新日期:2026-04-30
- 图源:**pexels**(原 SPEC 写 Unsplash,因主站反爬不可达切到 Pexels,详 ADR-0009)
- 季节策略:northern_hemisphere_meteorological(春 3-5 / 夏 6-8 / 秋 9-11 / 冬 12-2)
- 总计:**200 张** = 4 季 × 50 张(严格)
- CDN URL 模板:`https://images.pexels.com/photos/{id}/pexels-photo-{id}.jpeg?auto=compress&cs=tinysrgb&w=1280&h=400&fit=crop`
- 预览页模板:`https://www.pexels.com/photo/{slug}-{id}/`

## 工作流说明

1. **CC** 用 WebSearch 搜 `site:pexels.com/photo` 共 46 组关键词,收集 283 张原始候选 ID
2. **CC** 元数据粗筛(BLOCKLIST 含 sunset/sunrise/woman/man/colorful/golden/neon 等 50+ 词)→ 230 张通过
3. **CC** 并发 HEAD 验证 CDN URL 200 OK + Content-Length → 212 张可达(18 张真 404)
4. **审美 Agent**(模拟 frontend-design 风格审美)按 SPEC M5.2 克制标准最后过滤 → 砍 12 张 → **200 张**

## 你要做的事

- 在每张表格的 **预览** 列点链接打开,扫一眼图
- **不喜欢**的:在 **决定** 列填 `❌`
- **想换**(同主题但要求别的):填 `🔄 + 描述`(例:`🔄 想要更冷调的、不要这种近景`)
- 全部审完发我:`已删除第 X、Y、Z 张,补 N 张同类`,我重新搜 + 验证 + 重新提交此文档
- **直到你说"全部 OK,可以集成"**,我才开始 M5.5(模板改 / collector 写 / fallback_header.jpg 选)

## 字段说明

- **Photo ID**:Pexels 数字 ID,即将填入 `config/curated_images.json` `seasons.{season}[].id`
- **主题 / Slug**:Pexels 自动生成的英文 slug(由 alt 文字派生),所以"主题准确度"以你点开预览为准 — slug 不一定描述准确
- **调色板**:CC 基于 slug 推断的 palette label(`cool_grey` / `cool_white` / `warm_grey` / `cool_blue` / `muted_green` / `dark_grey` / `monochrome` / `neutral_grey`),仅供你筛选时按色调浏览参考
- **预览**:点开看图本身(`unsplash.com/photos/...` 等价的 Pexels 详情页)


---

## 春(3-5 月,50 张)

| # | Photo ID | 主题 / Slug | 标签 | 调色板 | 预览 | 决定 |
|---|---|---|---|---|---|---|
| 1 | `30882618` | misty-forest-landscape-with-dense-fog | misty, forest, dense, fog | `cool_grey` | [预览](https://www.pexels.com/photo/misty-forest-landscape-with-dense-fog-30882618/) |  |
| 2 | `35001488` | misty-forest-tall-trees-in-atmospheric-fog | misty, forest, tall, trees, atmospheric | `cool_grey` | [预览](https://www.pexels.com/photo/misty-forest-tall-trees-in-atmospheric-fog-35001488/) |  |
| 3 | `36646592` | moody-misty-forest-landscape-with-evergreen-trees | moody, misty, forest, evergreen, trees | `dark_grey` | [预览](https://www.pexels.com/photo/moody-misty-forest-landscape-with-evergreen-trees-36646592/) |  |
| 4 | `24536427` | fog-over-lake-under-hill | fog, lake, hill | `cool_grey` | [预览](https://www.pexels.com/photo/fog-over-lake-under-hill-24536427/) |  |
| 5 | `28966860` | misty-morning-landscape-in-tranquil-countryside | misty, morning, tranquil, countryside | `cool_grey` | [预览](https://www.pexels.com/photo/misty-morning-landscape-in-tranquil-countryside-28966860/) |  |
| 6 | `59688` | mountains-foggy-misty-fog | mountains, foggy, misty, fog | `cool_grey` | [预览](https://www.pexels.com/photo/mountains-foggy-misty-fog-59688/) |  |
| 7 | `1287083` | trees-surrounded-by-fogs-in-the-forest | trees, surrounded, fogs, forest | `cool_grey` | [预览](https://www.pexels.com/photo/trees-surrounded-by-fogs-in-the-forest-1287083/) |  |
| 8 | `29070548` | serene-frosty-morning-in-a-misty-bog-landscape | serene, frosty, morning, misty, bog | `cool_grey` | [预览](https://www.pexels.com/photo/serene-frosty-morning-in-a-misty-bog-landscape-29070548/) |  |
| 9 | `33312427` | aerial-view-of-misty-mountain-range-landscape | aerial, misty, mountain, range | `cool_grey` | [预览](https://www.pexels.com/photo/aerial-view-of-misty-mountain-range-landscape-33312427/) |  |
| 10 | `1183099` | photography-of-mountains-under-cloudy-sky | mountains, cloudy, sky | `cool_grey` | [预览](https://www.pexels.com/photo/photography-of-mountains-under-cloudy-sky-1183099/) |  |
| 11 | `1770809` | green-grass-near-trees | green, grass, trees | `neutral_grey` | [预览](https://www.pexels.com/photo/green-grass-near-trees-1770809/) |  |
| 12 | `20523396` | green-coniferous-forest | green, coniferous, forest | `muted_green` | [预览](https://www.pexels.com/photo/green-coniferous-forest-20523396/) |  |
| 13 | `17203268` | green-trees-in-forest | green, trees, forest | `neutral_grey` | [预览](https://www.pexels.com/photo/green-trees-in-forest-17203268/) |  |
| 14 | `17005430` | river-flowing-in-green-nature-landscape | river, flowing, green, nature | `neutral_grey` | [预览](https://www.pexels.com/photo/river-flowing-in-green-nature-landscape-17005430/) |  |
| 15 | `21753035` | rain-clouds-over-mountains | rain, clouds, mountains | `neutral_grey` | [预览](https://www.pexels.com/photo/rain-clouds-over-mountains-21753035/) |  |
| 16 | `31947949` | moody-dense-forest-under-cloudy-sky | moody, dense, forest, cloudy, sky | `dark_grey` | [预览](https://www.pexels.com/photo/moody-dense-forest-under-cloudy-sky-31947949/) |  |
| 17 | `6172330` | wetlands-in-spring | wetlands, spring | `neutral_grey` | [预览](https://www.pexels.com/photo/wetlands-in-spring-6172330/) |  |
| 18 | `29834890` | moody-black-and-white-landscape-in-bolu-turkey | moody, black, white, bolu, turkey | `monochrome` | [预览](https://www.pexels.com/photo/moody-black-and-white-landscape-in-bolu-turkey-29834890/) |  |
| 19 | `1363873` | scenic-view-of-mountain-under-cloudy-sky | scenic, mountain, cloudy, sky | `cool_grey` | [预览](https://www.pexels.com/photo/scenic-view-of-mountain-under-cloudy-sky-1363873/) |  |
| 20 | `7789192` | foggy-morning-in-countryside-field | foggy, morning, countryside, field | `cool_grey` | [预览](https://www.pexels.com/photo/foggy-morning-in-countryside-field-7789192/) |  |
| 21 | `8708732` | grass-field-under-the-cloudy-sky | grass, field, cloudy, sky | `cool_grey` | [预览](https://www.pexels.com/photo/grass-field-under-the-cloudy-sky-8708732/) |  |
| 22 | `4488071` | grass-field-near-forest-in-overcast-weather | grass, field, forest, overcast, weather | `cool_grey` | [预览](https://www.pexels.com/photo/grass-field-near-forest-in-overcast-weather-4488071/) |  |
| 23 | `32176280` | serene-misty-morning-in-grassy-meadow | serene, misty, morning, grassy, meadow | `cool_grey` | [预览](https://www.pexels.com/photo/serene-misty-morning-in-grassy-meadow-32176280/) |  |
| 24 | `102160` | landscape-nature-sky-clouds | nature, sky, clouds | `neutral_grey` | [预览](https://www.pexels.com/photo/landscape-nature-sky-clouds-102160/) |  |
| 25 | `34555740` | misty-morning-landscape-in-poland-countryside | misty, morning, poland, countryside | `cool_grey` | [预览](https://www.pexels.com/photo/misty-morning-landscape-in-poland-countryside-34555740/) |  |
| 26 | `12488674` | dark-image-of-a-mountain-landscape-in-a-fog-and-winding-road | dark, mountain, fog, winding, road | `dark_grey` | [预览](https://www.pexels.com/photo/dark-image-of-a-mountain-landscape-in-a-fog-and-winding-road-12488674/) |  |
| 27 | `55766` | wheat-field-under-gray-sky | wheat, field, gray, sky | `warm_grey` | [预览](https://www.pexels.com/photo/wheat-field-under-gray-sky-55766/) |  |
| 28 | `30982497` | misty-forest-pathway-in-serene-morning-light | misty, forest, pathway, serene, morning | `cool_grey` | [预览](https://www.pexels.com/photo/misty-forest-pathway-in-serene-morning-light-30982497/) |  |
| 29 | `345043` | body-of-water | body, water | `neutral_grey` | [预览](https://www.pexels.com/photo/body-of-water-345043/) |  |
| 30 | `9501329` | a-reflection-of-trees-on-a-calm-lake | reflection, trees, calm, lake | `neutral_grey` | [预览](https://www.pexels.com/photo/a-reflection-of-trees-on-a-calm-lake-9501329/) |  |
| 31 | `9188430` | reflection-of-the-sky-on-lake | reflection, sky, lake | `neutral_grey` | [预览](https://www.pexels.com/photo/reflection-of-the-sky-on-lake-9188430/) |  |
| 32 | `30725259` | serene-lake-with-reflective-trees-under-cloudy-sky | serene, lake, reflective, trees, cloudy | `cool_grey` | [预览](https://www.pexels.com/photo/serene-lake-with-reflective-trees-under-cloudy-sky-30725259/) |  |
| 33 | `5370215` | green-grassy-hills-in-countryside-on-overcast-day | green, grassy, hills, countryside, overcast | `cool_grey` | [预览](https://www.pexels.com/photo/green-grassy-hills-in-countryside-on-overcast-day-5370215/) |  |
| 34 | `30654271` | misty-rural-hillside-with-overcast-skies | misty, rural, hillside, overcast, skies | `cool_grey` | [预览](https://www.pexels.com/photo/misty-rural-hillside-with-overcast-skies-30654271/) |  |
| 35 | `9268741` | dark-landscape-with-river-in-a-valley-and-mountain-peaks-in-fog | dark, river, valley, mountain, peaks | `dark_grey` | [预览](https://www.pexels.com/photo/dark-landscape-with-river-in-a-valley-and-mountain-peaks-in-fog-9268741/) |  |
| 36 | `16065845` | green-hillside-under-cloudy-sky | green, hillside, cloudy, sky | `cool_grey` | [预览](https://www.pexels.com/photo/green-hillside-under-cloudy-sky-16065845/) |  |
| 37 | `12797727` | fog-and-overcast-sky-above-mountains | fog, overcast, sky, above, mountains | `cool_grey` | [预览](https://www.pexels.com/photo/fog-and-overcast-sky-above-mountains-12797727/) |  |
| 38 | `14808128` | clouds-over-forest-on-hillside | clouds, forest, hillside | `neutral_grey` | [预览](https://www.pexels.com/photo/clouds-over-forest-on-hillside-14808128/) |  |
| 39 | `16181969` | rain-clouds-over-green-hills | rain, clouds, green, hills | `neutral_grey` | [预览](https://www.pexels.com/photo/rain-clouds-over-green-hills-16181969/) |  |
| 40 | `29223951` | serene-countryside-morning-with-mist-and-fog | serene, countryside, morning, mist, fog | `cool_grey` | [预览](https://www.pexels.com/photo/serene-countryside-morning-with-mist-and-fog-29223951/) |  |
| 41 | `4114237` | weeping-willow-tree-near-body-of-water | weeping, willow, tree, body, water | `neutral_grey` | [预览](https://www.pexels.com/photo/weeping-willow-tree-near-body-of-water-4114237/) |  |
| 42 | `5407567` | fog-over-green-forest-in-mountains | fog, green, forest, mountains | `muted_green` | [预览](https://www.pexels.com/photo/fog-over-green-forest-in-mountains-5407567/) |  |
| 43 | `32176277` | misty-morning-by-a-quiet-riverside-landscape | misty, morning, quiet, riverside | `cool_grey` | [预览](https://www.pexels.com/photo/misty-morning-by-a-quiet-riverside-landscape-32176277/) |  |
| 44 | `35854904` | misty-pine-forest-in-serene-morning-light | misty, pine, forest, serene, morning | `cool_grey` | [预览](https://www.pexels.com/photo/misty-pine-forest-in-serene-morning-light-35854904/) |  |
| 45 | `11575077` | calm-sea-in-mountains-landscape | calm, sea, mountains | `cool_blue` | [预览](https://www.pexels.com/photo/calm-sea-in-mountains-landscape-11575077/) |  |
| 46 | `10622712` | calm-mountain-landscape-with-fog-black-and-white | calm, mountain, fog, black, white | `monochrome` | [预览](https://www.pexels.com/photo/calm-mountain-landscape-with-fog-black-and-white-10622712/) |  |
| 47 | `4406333` | fog-over-valley-with-high-trees | fog, valley, high, trees | `cool_grey` | [预览](https://www.pexels.com/photo/fog-over-valley-with-high-trees-4406333/) |  |
| 48 | `19497735` | mountain-valley-in-fog | mountain, valley, fog | `cool_grey` | [预览](https://www.pexels.com/photo/mountain-valley-in-fog-19497735/) |  |
| 49 | `14691271` | hills-in-a-mountain-valley-covered-with-fog | hills, mountain, valley, covered, fog | `cool_grey` | [预览](https://www.pexels.com/photo/hills-in-a-mountain-valley-covered-with-fog-14691271/) |  |
| 50 | `28617613` | misty-hills-and-foggy-forest-in-munnar-india | misty, hills, foggy, forest, munnar | `cool_grey` | [预览](https://www.pexels.com/photo/misty-hills-and-foggy-forest-in-munnar-india-28617613/) |  |

---

## 夏(6-8 月,50 张)

| # | Photo ID | 主题 / Slug | 标签 | 调色板 | 预览 | 决定 |
|---|---|---|---|---|---|---|
| 1 | `1463668` | view-of-ocean-covered-with-fog | ocean, covered, fog | `cool_blue` | [预览](https://www.pexels.com/photo/view-of-ocean-covered-with-fog-1463668/) |  |
| 2 | `18050899` | scenic-view-of-sea-and-coastline-under-overcast-sky | scenic, sea, coastline, overcast, sky | `cool_blue` | [预览](https://www.pexels.com/photo/scenic-view-of-sea-and-coastline-under-overcast-sky-18050899/) |  |
| 3 | `10278716` | fog-over-ocean | fog, ocean | `cool_blue` | [预览](https://www.pexels.com/photo/fog-over-ocean-10278716/) |  |
| 4 | `4640990` | fog-over-sea-near-shore | fog, sea, shore | `cool_blue` | [预览](https://www.pexels.com/photo/fog-over-sea-near-shore-4640990/) |  |
| 5 | `448748` | blue-body-of-water-with-fog | blue, body, water, fog | `cool_blue` | [预览](https://www.pexels.com/photo/blue-body-of-water-with-fog-448748/) |  |
| 6 | `28770440` | foggy-cliffside-overlooking-calm-ocean-waters | foggy, cliffside, overlooking, calm, ocean | `cool_blue` | [预览](https://www.pexels.com/photo/foggy-cliffside-overlooking-calm-ocean-waters-28770440/) |  |
| 7 | `29673131` | lush-green-moss-in-brazilian-forest-light | lush, green, moss, brazilian, forest | `muted_green` | [预览](https://www.pexels.com/photo/lush-green-moss-in-brazilian-forest-light-29673131/) |  |
| 8 | `18983204` | fog-over-beach-and-cliff-on-sea-shore | fog, beach, cliff, sea, shore | `cool_blue` | [预览](https://www.pexels.com/photo/fog-over-beach-and-cliff-on-sea-shore-18983204/) |  |
| 9 | `13728503` | ocean-view-under-gray-sky | ocean, gray, sky | `cool_blue` | [预览](https://www.pexels.com/photo/ocean-view-under-gray-sky-13728503/) |  |
| 10 | `11932114` | moss-in-dark-forest | moss, dark, forest | `muted_green` | [预览](https://www.pexels.com/photo/moss-in-dark-forest-11932114/) |  |
| 11 | `5273591` | overcast-over-sea-shore-and-mountains | overcast, sea, shore, mountains | `cool_blue` | [预览](https://www.pexels.com/photo/overcast-over-sea-shore-and-mountains-5273591/) |  |
| 12 | `4775483` | rock-on-sea-in-overcast-weather-in-evening | rock, sea, overcast, weather, evening | `cool_blue` | [预览](https://www.pexels.com/photo/rock-on-sea-in-overcast-weather-in-evening-4775483/) |  |
| 13 | `5331812` | empty-beach-of-ocean-in-overcast-weather | empty, beach, ocean, overcast, weather | `cool_blue` | [预览](https://www.pexels.com/photo/empty-beach-of-ocean-in-overcast-weather-5331812/) |  |
| 14 | `34053957` | scenic-rock-jetty-extending-into-calm-blue-sea | scenic, rock, jetty, extending, into | `cool_blue` | [预览](https://www.pexels.com/photo/scenic-rock-jetty-extending-into-calm-blue-sea-34053957/) |  |
| 15 | `5098158` | rocky-cliff-above-sea-in-overcast | rocky, cliff, above, sea, overcast | `cool_blue` | [预览](https://www.pexels.com/photo/rocky-cliff-above-sea-in-overcast-5098158/) |  |
| 16 | `9578029` | brown-rocky-mountain-beside-the-beach | brown, rocky, mountain, beside, beach | `warm_grey` | [预览](https://www.pexels.com/photo/brown-rocky-mountain-beside-the-beach-9578029/) |  |
| 17 | `235637` | grey-rocks-on-river-landscape-photography | grey, rocks, river | `neutral_grey` | [预览](https://www.pexels.com/photo/grey-rocks-on-river-landscape-photography-235637/) |  |
| 18 | `7520331` | water-flowing-on-a-shallow-rocky-river | water, flowing, shallow, rocky, river | `neutral_grey` | [预览](https://www.pexels.com/photo/water-flowing-on-a-shallow-rocky-river-7520331/) |  |
| 19 | `3800074` | time-lapse-photo-of-river-between-mossy-rocks | time, lapse, river, mossy, rocks | `muted_green` | [预览](https://www.pexels.com/photo/time-lapse-photo-of-river-between-mossy-rocks-3800074/) |  |
| 20 | `8048466` | waterfalls-cascading-on-mossy-rocks-in-the-forest | waterfalls, cascading, mossy, rocks, forest | `muted_green` | [预览](https://www.pexels.com/photo/waterfalls-cascading-on-mossy-rocks-in-the-forest-8048466/) |  |
| 21 | `216757` | body-of-water-between-black-rock-artwork | body, water, black, rock, artwork | `neutral_grey` | [预览](https://www.pexels.com/photo/body-of-water-between-black-rock-artwork-216757/) |  |
| 22 | `5777919` | waterfalls-in-the-forest | waterfalls, forest | `neutral_grey` | [预览](https://www.pexels.com/photo/waterfalls-in-the-forest-5777919/) |  |
| 23 | `18703061` | view-of-a-rocky-stream-in-the-forest | rocky, stream, forest | `neutral_grey` | [预览](https://www.pexels.com/photo/view-of-a-rocky-stream-in-the-forest-18703061/) |  |
| 24 | `1271620` | creek-in-a-forest | creek, forest | `neutral_grey` | [预览](https://www.pexels.com/photo/creek-in-a-forest-1271620/) |  |
| 25 | `34253909` | mountain-stream-flowing-over-mossy-rocks | mountain, stream, flowing, mossy, rocks | `muted_green` | [预览](https://www.pexels.com/photo/mountain-stream-flowing-over-mossy-rocks-34253909/) |  |
| 26 | `14471495` | black-and-white-landscape-of-mountains-and-water-in-fog | black, white, mountains, water, fog | `monochrome` | [预览](https://www.pexels.com/photo/black-and-white-landscape-of-mountains-and-water-in-fog-14471495/) |  |
| 27 | `12871045` | mountain-with-cliffs-in-a-fog | mountain, cliffs, fog | `cool_grey` | [预览](https://www.pexels.com/photo/mountain-with-cliffs-in-a-fog-12871045/) |  |
| 28 | `3011846` | foggy-cliff | foggy, cliff | `cool_grey` | [预览](https://www.pexels.com/photo/foggy-cliff-3011846/) |  |
| 29 | `1367192` | photo-of-foggy-forest | foggy, forest | `cool_grey` | [预览](https://www.pexels.com/photo/photo-of-foggy-forest-1367192/) |  |
| 30 | `9156056` | scenic-view-of-a-coastal-cliff | scenic, coastal, cliff | `cool_blue` | [预览](https://www.pexels.com/photo/scenic-view-of-a-coastal-cliff-9156056/) |  |
| 31 | `1366921` | photo-of-forest-covered-by-fog | forest, covered, fog | `cool_grey` | [预览](https://www.pexels.com/photo/photo-of-forest-covered-by-fog-1366921/) |  |
| 32 | `18591164` | a-foggy-landscape-with-trees-and-fog | foggy, trees, fog | `cool_grey` | [预览](https://www.pexels.com/photo/a-foggy-landscape-with-trees-and-fog-18591164/) |  |
| 33 | `167699` | green-pine-trees-covered-with-fogs-under-white-sky-during-daytime | green, pine, trees, covered, fogs | `muted_green` | [预览](https://www.pexels.com/photo/green-pine-trees-covered-with-fogs-under-white-sky-during-daytime-167699/) |  |
| 34 | `670782` | early-morning-fog-forest-haze | early, morning, fog, forest, haze | `cool_grey` | [预览](https://www.pexels.com/photo/early-morning-fog-forest-haze-670782/) |  |
| 35 | `12264406` | reflection-of-the-sky-in-a-still-lake | reflection, sky, still, lake | `neutral_grey` | [预览](https://www.pexels.com/photo/reflection-of-the-sky-in-a-still-lake-12264406/) |  |
| 36 | `3975366` | green-pine-trees-covered-with-fog | green, pine, trees, covered, fog | `muted_green` | [预览](https://www.pexels.com/photo/green-pine-trees-covered-with-fog-3975366/) |  |
| 37 | `13364090` | reflection-of-cloudy-sky-on-lake-surface | reflection, cloudy, sky, lake, surface | `cool_grey` | [预览](https://www.pexels.com/photo/reflection-of-cloudy-sky-on-lake-surface-13364090/) |  |
| 38 | `17180777` | mountains-with-reflection-in-lake | mountains, reflection, lake | `neutral_grey` | [预览](https://www.pexels.com/photo/mountains-with-reflection-in-lake-17180777/) |  |
| 39 | `1557652` | tree-with-reflection-on-body-of-water | tree, reflection, body, water | `neutral_grey` | [预览](https://www.pexels.com/photo/tree-with-reflection-on-body-of-water-1557652/) |  |
| 40 | `15745269` | reflection-of-trees-and-mountains-in-the-lake | reflection, trees, mountains, lake | `neutral_grey` | [预览](https://www.pexels.com/photo/reflection-of-trees-and-mountains-in-the-lake-15745269/) |  |
| 41 | `19036832` | mountain-reflection-in-lake | mountain, reflection, lake | `neutral_grey` | [预览](https://www.pexels.com/photo/mountain-reflection-in-lake-19036832/) |  |
| 42 | `5097949` | reflection-of-a-cloudy-sky-on-a-lake | reflection, cloudy, sky, lake | `cool_grey` | [预览](https://www.pexels.com/photo/reflection-of-a-cloudy-sky-on-a-lake-5097949/) |  |
| 43 | `8389188` | trees-reflection-on-a-lake | trees, reflection, lake | `neutral_grey` | [预览](https://www.pexels.com/photo/trees-reflection-on-a-lake-8389188/) |  |
| 44 | `28481987` | serene-foggy-lake-with-tree-reflection | serene, foggy, lake, tree, reflection | `cool_grey` | [预览](https://www.pexels.com/photo/serene-foggy-lake-with-tree-reflection-28481987/) |  |
| 45 | `15211444` | a-storm-cloud-above-a-field | storm, cloud, above, field | `dark_grey` | [预览](https://www.pexels.com/photo/a-storm-cloud-above-a-field-15211444/) |  |
| 46 | `8903157` | dark-heavy-clouds-over-the-grass-land | dark, heavy, clouds, grass, land | `dark_grey` | [预览](https://www.pexels.com/photo/dark-heavy-clouds-over-the-grass-land-8903157/) |  |
| 47 | `355441` | grass-field-below-clouds | grass, field, below, clouds | `neutral_grey` | [预览](https://www.pexels.com/photo/grass-field-below-clouds-355441/) |  |
| 48 | `16880145` | storm-clouds-over-a-countryside | storm, clouds, countryside | `dark_grey` | [预览](https://www.pexels.com/photo/storm-clouds-over-a-countryside-16880145/) |  |
| 49 | `16277399` | view-of-dark-storm-clouds | dark, storm, clouds | `dark_grey` | [预览](https://www.pexels.com/photo/view-of-dark-storm-clouds-16277399/) |  |
| 50 | `13258137` | heavy-dark-clouds-over-mountains | heavy, dark, clouds, mountains | `dark_grey` | [预览](https://www.pexels.com/photo/heavy-dark-clouds-over-mountains-13258137/) |  |

---

## 秋(9-11 月,50 张)

| # | Photo ID | 主题 / Slug | 标签 | 调色板 | 预览 | 决定 |
|---|---|---|---|---|---|---|
| 1 | `17242182` | misty-countryside-landscape-with-grass-field-covered-with-morning-dew | misty, countryside, grass, field, covered | `cool_grey` | [预览](https://www.pexels.com/photo/misty-countryside-landscape-with-grass-field-covered-with-morning-dew-17242182/) |  |
| 2 | `16297902` | close-up-of-dry-grass-on-a-field | close, dry, grass, field | `warm_grey` | [预览](https://www.pexels.com/photo/close-up-of-dry-grass-on-a-field-16297902/) |  |
| 3 | `9892165` | trees-in-the-middle-of-dry-fields-and-hills | trees, middle, dry, fields, hills | `neutral_grey` | [预览](https://www.pexels.com/photo/trees-in-the-middle-of-dry-fields-and-hills-9892165/) |  |
| 4 | `10111157` | fallen-leaves-on-green-grass | fallen, leaves, green, grass | `neutral_grey` | [预览](https://www.pexels.com/photo/fallen-leaves-on-green-grass-10111157/) |  |
| 5 | `174614` | autumn-grass-background-grass | autumn, grass, grass | `warm_grey` | [预览](https://www.pexels.com/photo/autumn-grass-background-grass-174614/) |  |
| 6 | `5889428` | clear-river-flowing-among-dry-grass-and-trees | clear, river, flowing, among, dry | `warm_grey` | [预览](https://www.pexels.com/photo/clear-river-flowing-among-dry-grass-and-trees-5889428/) |  |
| 7 | `5662136` | bright-autumn-leaves-on-grass-lawn-in-park | bright, autumn, leaves, grass, lawn | `warm_grey` | [预览](https://www.pexels.com/photo/bright-autumn-leaves-on-grass-lawn-in-park-5662136/) |  |
| 8 | `13739181` | dry-landscape-with-grass-and-mountains | dry, grass, mountains | `neutral_grey` | [预览](https://www.pexels.com/photo/dry-landscape-with-grass-and-mountains-13739181/) |  |
| 9 | `380012` | background-dry-grass-landscape-yellow-grass | dry, grass, yellow, grass | `warm_grey` | [预览](https://www.pexels.com/photo/background-dry-grass-landscape-yellow-grass-380012/) |  |
| 10 | `28352672` | an-aerial-view-of-a-field-with-crops | aerial, field, crops | `neutral_grey` | [预览](https://www.pexels.com/photo/an-aerial-view-of-a-field-with-crops-28352672/) |  |
| 11 | `2816057` | photo-of-grass-field-during-daytime | grass, field, daytime | `neutral_grey` | [预览](https://www.pexels.com/photo/photo-of-grass-field-during-daytime-2816057/) |  |
| 12 | `5876603` | dry-wheat-growing-on-countryside-field | dry, wheat, growing, countryside, field | `warm_grey` | [预览](https://www.pexels.com/photo/dry-wheat-growing-on-countryside-field-5876603/) |  |
| 13 | `13957215` | big-brown-tree-on-brown-grass-field-under-the-white-clouds | big, brown, tree, brown, grass | `warm_grey` | [预览](https://www.pexels.com/photo/big-brown-tree-on-brown-grass-field-under-the-white-clouds-13957215/) |  |
| 14 | `7626143` | a-field-with-wheat-crops | field, wheat, crops | `warm_grey` | [预览](https://www.pexels.com/photo/a-field-with-wheat-crops-7626143/) |  |
| 15 | `1227513` | photo-of-grass-field | grass, field | `neutral_grey` | [预览](https://www.pexels.com/photo/photo-of-grass-field-1227513/) |  |
| 16 | `1048039` | green-grass-field-under-white-clouds | green, grass, field, white, clouds | `neutral_grey` | [预览](https://www.pexels.com/photo/green-grass-field-under-white-clouds-1048039/) |  |
| 17 | `1102908` | foggy-path | foggy, path | `cool_grey` | [预览](https://www.pexels.com/photo/foggy-path-1102908/) |  |
| 18 | `10817836` | forest-path-in-fog | forest, path, fog | `cool_grey` | [预览](https://www.pexels.com/photo/forest-path-in-fog-10817836/) |  |
| 19 | `10239298` | a-foggy-forest-during-autumn | foggy, forest, autumn | `warm_grey` | [预览](https://www.pexels.com/photo/a-foggy-forest-during-autumn-10239298/) |  |
| 20 | `34889618` | foggy-road-through-a-dark-forest-in-autumn | foggy, road, through, dark, forest | `warm_grey` | [预览](https://www.pexels.com/photo/foggy-road-through-a-dark-forest-in-autumn-34889618/) |  |
| 21 | `5868172` | fog-over-dark-autumn-forest | fog, dark, autumn, forest | `warm_grey` | [预览](https://www.pexels.com/photo/fog-over-dark-autumn-forest-5868172/) |  |
| 22 | `15576547` | foggy-road-between-trees-in-autumn | foggy, road, trees, autumn | `warm_grey` | [预览](https://www.pexels.com/photo/foggy-road-between-trees-in-autumn-15576547/) |  |
| 23 | `5837865` | narrow-pathway-between-bright-autumn-trees-in-fog | narrow, pathway, bright, autumn, trees | `warm_grey` | [预览](https://www.pexels.com/photo/narrow-pathway-between-bright-autumn-trees-in-fog-5837865/) |  |
| 24 | `1655901` | landscape-photo-of-forest | forest | `neutral_grey` | [预览](https://www.pexels.com/photo/landscape-photo-of-forest-1655901/) |  |
| 25 | `29136939` | misty-forest-with-birch-and-pine-trees | misty, forest, birch, pine, trees | `warm_grey` | [预览](https://www.pexels.com/photo/misty-forest-with-birch-and-pine-trees-29136939/) |  |
| 26 | `29443875` | autumn-birch-tree-in-misty-forest-scene | autumn, birch, tree, misty, forest | `warm_grey` | [预览](https://www.pexels.com/photo/autumn-birch-tree-in-misty-forest-scene-29443875/) |  |
| 27 | `28471869` | peaceful-birch-tree-pathway-in-autumn-forest | peaceful, birch, tree, pathway, autumn | `warm_grey` | [预览](https://www.pexels.com/photo/peaceful-birch-tree-pathway-in-autumn-forest-28471869/) |  |
| 28 | `15846861` | birch-forest-in-autumn | birch, forest, autumn | `warm_grey` | [预览](https://www.pexels.com/photo/birch-forest-in-autumn-15846861/) |  |
| 29 | `29120792` | foggy-birch-tree-in-a-misty-autumn-landscape | foggy, birch, tree, misty, autumn | `warm_grey` | [预览](https://www.pexels.com/photo/foggy-birch-tree-in-a-misty-autumn-landscape-29120792/) |  |
| 30 | `19957279` | birch-tree-in-autumn-foliage | birch, tree, autumn, foliage | `warm_grey` | [预览](https://www.pexels.com/photo/birch-tree-in-autumn-foliage-19957279/) |  |
| 31 | `16512172` | birch-trees-in-autumn-with-yellow-leaves | birch, trees, autumn, yellow, leaves | `warm_grey` | [预览](https://www.pexels.com/photo/birch-trees-in-autumn-with-yellow-leaves-16512172/) |  |
| 32 | `29173938` | rustic-autumn-birch-tree-by-a-tranquil-lake | rustic, autumn, birch, tree, tranquil | `warm_grey` | [预览](https://www.pexels.com/photo/rustic-autumn-birch-tree-by-a-tranquil-lake-29173938/) |  |
| 33 | `29715252` | misty-forest-in-autumn-with-bare-trees | misty, forest, autumn, bare, trees | `warm_grey` | [预览](https://www.pexels.com/photo/misty-forest-in-autumn-with-bare-trees-29715252/) |  |
| 34 | `13622968` | grass-field-and-trees-covered-in-fog | grass, field, trees, covered, fog | `cool_grey` | [预览](https://www.pexels.com/photo/grass-field-and-trees-covered-in-fog-13622968/) |  |
| 35 | `1640882` | dried-leaves-under-a-tree-on-park-with-fogs-landscape-photography | dried, leaves, tree, park, fogs | `warm_grey` | [预览](https://www.pexels.com/photo/dried-leaves-under-a-tree-on-park-with-fogs-landscape-photography-1640882/) |  |
| 36 | `12527037` | lake-surrounded-by-trees-covered-with-fog | lake, surrounded, trees, covered, fog | `cool_grey` | [预览](https://www.pexels.com/photo/lake-surrounded-by-trees-covered-with-fog-12527037/) |  |
| 37 | `34675744` | serene-autumn-reflection-on-calm-lake-waters | serene, autumn, reflection, calm, lake | `warm_grey` | [预览](https://www.pexels.com/photo/serene-autumn-reflection-on-calm-lake-waters-34675744/) |  |
| 38 | `35004410` | tranquil-autumn-landscape-with-hillside-and-lake | tranquil, autumn, hillside, lake | `warm_grey` | [预览](https://www.pexels.com/photo/tranquil-autumn-landscape-with-hillside-and-lake-35004410/) |  |
| 39 | `29479873` | serene-autumn-lake-with-fall-foliage-reflection | serene, autumn, lake, fall, foliage | `warm_grey` | [预览](https://www.pexels.com/photo/serene-autumn-lake-with-fall-foliage-reflection-29479873/) |  |
| 40 | `89403` | lake-with-fog-under-dark-blue-sky-photography | lake, fog, dark, blue, sky | `dark_grey` | [预览](https://www.pexels.com/photo/lake-with-fog-under-dark-blue-sky-photography-89403/) |  |
| 41 | `28961234` | misty-autumn-morning-in-a-forest-clearing | misty, autumn, morning, forest, clearing | `warm_grey` | [预览](https://www.pexels.com/photo/misty-autumn-morning-in-a-forest-clearing-28961234/) |  |
| 42 | `28811877` | misty-pine-forest-in-autumn-colors | misty, pine, forest, autumn, colors | `warm_grey` | [预览](https://www.pexels.com/photo/misty-pine-forest-in-autumn-colors-28811877/) |  |
| 43 | `29579812` | misty-forest-pathway-in-early-morning-light | misty, forest, pathway, early, morning | `cool_grey` | [预览](https://www.pexels.com/photo/misty-forest-pathway-in-early-morning-light-29579812/) |  |
| 44 | `34482318` | moody-scottish-highland-landscape-in-autumn | moody, scottish, highland, autumn | `warm_grey` | [预览](https://www.pexels.com/photo/moody-scottish-highland-landscape-in-autumn-34482318/) |  |
| 45 | `28551402` | misty-landscape-with-trees-and-fog-rolling-hills | misty, trees, fog, rolling, hills | `cool_grey` | [预览](https://www.pexels.com/photo/misty-landscape-with-trees-and-fog-rolling-hills-28551402/) |  |
| 46 | `18842494` | a-broken-tree-on-a-meadow-in-the-forest-in-autumn | broken, tree, meadow, forest, autumn | `warm_grey` | [预览](https://www.pexels.com/photo/a-broken-tree-on-a-meadow-in-the-forest-in-autumn-18842494/) |  |
| 47 | `10012495` | photo-of-a-forest-with-mist | forest, mist | `cool_grey` | [预览](https://www.pexels.com/photo/photo-of-a-forest-with-mist-10012495/) |  |
| 48 | `34472001` | majestic-autumn-landscape-with-foggy-mountain-view | majestic, autumn, foggy, mountain | `warm_grey` | [预览](https://www.pexels.com/photo/majestic-autumn-landscape-with-foggy-mountain-view-34472001/) |  |
| 49 | `29562214` | misty-autumn-forest-in-vermont | misty, autumn, forest, vermont | `warm_grey` | [预览](https://www.pexels.com/photo/misty-autumn-forest-in-vermont-29562214/) |  |
| 50 | `18897224` | autumn-mountain-landscape-in-fog | autumn, mountain, fog | `warm_grey` | [预览](https://www.pexels.com/photo/autumn-mountain-landscape-in-fog-18897224/) |  |

---

## 冬(12-2 月,50 张)

| # | Photo ID | 主题 / Slug | 标签 | 调色板 | 预览 | 决定 |
|---|---|---|---|---|---|---|
| 1 | `691668` | landscape-photography-of-mountains-covered-in-snow | mountains, covered, snow | `cool_white` | [预览](https://www.pexels.com/photo/landscape-photography-of-mountains-covered-in-snow-691668/) |  |
| 2 | `1366919` | landscape-photography-of-snowy-mountain | snowy, mountain | `cool_white` | [预览](https://www.pexels.com/photo/landscape-photography-of-snowy-mountain-1366919/) |  |
| 3 | `34348785` | scenic-winter-view-of-norwegian-fjord-landscape | scenic, winter, norwegian, fjord | `cool_blue` | [预览](https://www.pexels.com/photo/scenic-winter-view-of-norwegian-fjord-landscape-34348785/) |  |
| 4 | `6530841` | winter-landscape | winter | `neutral_grey` | [预览](https://www.pexels.com/photo/winter-landscape-6530841/) |  |
| 5 | `29823042` | scenic-winter-road-through-snowy-norwegian-landscape | scenic, winter, road, through, snowy | `cool_white` | [预览](https://www.pexels.com/photo/scenic-winter-road-through-snowy-norwegian-landscape-29823042/) |  |
| 6 | `35375041` | minimalist-winter-scene-with-leaf-and-snow | minimalist, winter, leaf, snow | `cool_white` | [预览](https://www.pexels.com/photo/minimalist-winter-scene-with-leaf-and-snow-35375041/) |  |
| 7 | `18542025` | winter-forest-in-the-fog | winter, forest, fog | `cool_grey` | [预览](https://www.pexels.com/photo/winter-forest-in-the-fog-18542025/) |  |
| 8 | `4913511` | snowy-forest-with-high-trees-on-foggy-day | snowy, forest, high, trees, foggy | `cool_white` | [预览](https://www.pexels.com/photo/snowy-forest-with-high-trees-on-foggy-day-4913511/) |  |
| 9 | `10408415` | aerial-shot-of-a-foggy-forest | aerial, shot, foggy, forest | `cool_grey` | [预览](https://www.pexels.com/photo/aerial-shot-of-a-foggy-forest-10408415/) |  |
| 10 | `20142761` | dark-mountains-with-snow-and-fog | dark, mountains, snow, fog | `cool_white` | [预览](https://www.pexels.com/photo/dark-mountains-with-snow-and-fog-20142761/) |  |
| 11 | `4406183` | cloudy-sky-over-snowy-mountains-and-lush-forest | cloudy, sky, snowy, mountains, lush | `cool_white` | [预览](https://www.pexels.com/photo/cloudy-sky-over-snowy-mountains-and-lush-forest-4406183/) |  |
| 12 | `6397400` | cloudy-sky-over-lush-coniferous-forest-covered-with-snow-in-winter | cloudy, sky, lush, coniferous, forest | `cool_white` | [预览](https://www.pexels.com/photo/cloudy-sky-over-lush-coniferous-forest-covered-with-snow-in-winter-6397400/) |  |
| 13 | `355770` | mountain-covered-with-snow-digital-wallpaper | mountain, covered, snow, digital, wallpaper | `cool_white` | [预览](https://www.pexels.com/photo/mountain-covered-with-snow-digital-wallpaper-355770/) |  |
| 14 | `1446713` | monochrome-photography-of-mountain | monochrome, mountain | `monochrome` | [预览](https://www.pexels.com/photo/monochrome-photography-of-mountain-1446713/) |  |
| 15 | `1367188` | monochrome-photography-of-mountain-covered-by-clouds | monochrome, mountain, covered, clouds | `monochrome` | [预览](https://www.pexels.com/photo/monochrome-photography-of-mountain-covered-by-clouds-1367188/) |  |
| 16 | `1840101` | snow-covered-rocky-mountain | snow, covered, rocky, mountain | `cool_white` | [预览](https://www.pexels.com/photo/snow-covered-rocky-mountain-1840101/) |  |
| 17 | `6752125` | snow-covered-road-near-the-trees | snow, covered, road, trees | `cool_white` | [预览](https://www.pexels.com/photo/snow-covered-road-near-the-trees-6752125/) |  |
| 18 | `14461667` | grayscale-photo-of-mountain-peak-covered-with-snow | grayscale, mountain, peak, covered, snow | `monochrome` | [预览](https://www.pexels.com/photo/grayscale-photo-of-mountain-peak-covered-with-snow-14461667/) |  |
| 19 | `5570439` | black-and-white-photo-of-the-snowy-mountains | black, white, snowy, mountains | `monochrome` | [预览](https://www.pexels.com/photo/black-and-white-photo-of-the-snowy-mountains-5570439/) |  |
| 20 | `2683746` | monochrome-photo-of-mountains | monochrome, mountains | `monochrome` | [预览](https://www.pexels.com/photo/monochrome-photo-of-mountains-2683746/) |  |
| 21 | `371649` | mountains-covered-with-snow | mountains, covered, snow | `cool_white` | [预览](https://www.pexels.com/photo/mountains-covered-with-snow-371649/) |  |
| 22 | `2479026` | monochrome-photo-of-mountain | monochrome, mountain | `monochrome` | [预览](https://www.pexels.com/photo/monochrome-photo-of-mountain-2479026/) |  |
| 23 | `17475755` | white-snow-and-cloud-in-mountains | white, snow, cloud, mountains | `cool_white` | [预览](https://www.pexels.com/photo/white-snow-and-cloud-in-mountains-17475755/) |  |
| 24 | `4318217` | frozen-lake-surrounded-with-wooded-mountains-in-snowy-winter | frozen, lake, surrounded, wooded, mountains | `cool_white` | [预览](https://www.pexels.com/photo/frozen-lake-surrounded-with-wooded-mountains-in-snowy-winter-4318217/) |  |
| 25 | `20306445` | frozen-lake-among-evergreen-trees | frozen, lake, among, evergreen, trees | `cool_white` | [预览](https://www.pexels.com/photo/frozen-lake-among-evergreen-trees-20306445/) |  |
| 26 | `5892621` | the-frozen-lake-louise-in-alberta-canada-during-winter | frozen, lake, louise, alberta, canada | `cool_white` | [预览](https://www.pexels.com/photo/the-frozen-lake-louise-in-alberta-canada-during-winter-5892621/) |  |
| 27 | `2004390` | leafless-tree | leafless, tree | `neutral_grey` | [预览](https://www.pexels.com/photo/leafless-tree-2004390/) |  |
| 28 | `3509418` | white-snow-field | white, snow, field | `cool_white` | [预览](https://www.pexels.com/photo/white-snow-field-3509418/) |  |
| 29 | `327434` | calm-waters-clouds-cold-country | calm, waters, clouds, cold, country | `neutral_grey` | [预览](https://www.pexels.com/photo/calm-waters-clouds-cold-country-327434/) |  |
| 30 | `29900182` | snow-covered-forest-and-frozen-lake-scenic-view | snow, covered, forest, frozen, lake | `cool_white` | [预览](https://www.pexels.com/photo/snow-covered-forest-and-frozen-lake-scenic-view-29900182/) |  |
| 31 | `2004388` | body-of-water-across-white-mountain | body, water, across, white, mountain | `neutral_grey` | [预览](https://www.pexels.com/photo/body-of-water-across-white-mountain-2004388/) |  |
| 32 | `15150340` | bare-trees-on-snow-covered-ground | bare, trees, snow, covered, ground | `cool_white` | [预览](https://www.pexels.com/photo/bare-trees-on-snow-covered-ground-15150340/) |  |
| 33 | `35617820` | aerial-view-of-snow-covered-farmland-in-winter | aerial, snow, covered, farmland, winter | `cool_white` | [预览](https://www.pexels.com/photo/aerial-view-of-snow-covered-farmland-in-winter-35617820/) |  |
| 34 | `10819629` | a-snow-covered-field-near-the-beach | snow, covered, field, beach | `cool_white` | [预览](https://www.pexels.com/photo/a-snow-covered-field-near-the-beach-10819629/) |  |
| 35 | `11180715` | a-bare-tree-on-a-snow-covered-field | bare, tree, snow, covered, field | `cool_white` | [预览](https://www.pexels.com/photo/a-bare-tree-on-a-snow-covered-field-11180715/) |  |
| 36 | `11104472` | trees-surrounding-a-snow-covered-field | trees, surrounding, snow, covered, field | `cool_white` | [预览](https://www.pexels.com/photo/trees-surrounding-a-snow-covered-field-11104472/) |  |
| 37 | `688660` | landscape-photography-of-snow-pathway-between-trees-during-winter | snow, pathway, trees, winter | `cool_white` | [预览](https://www.pexels.com/photo/landscape-photography-of-snow-pathway-between-trees-during-winter-688660/) |  |
| 38 | `19296658` | beautiful-snow-covered-pine-trees-in-a-winter-forest | beautiful, snow, covered, pine, trees | `cool_white` | [预览](https://www.pexels.com/photo/beautiful-snow-covered-pine-trees-in-a-winter-forest-19296658/) |  |
| 39 | `801787` | pine-trees-covered-with-snow | pine, trees, covered, snow | `cool_white` | [预览](https://www.pexels.com/photo/pine-trees-covered-with-snow-801787/) |  |
| 40 | `30663486` | scenic-forest-pathway-with-pine-trees-in-winter | scenic, forest, pathway, pine, trees | `neutral_grey` | [预览](https://www.pexels.com/photo/scenic-forest-pathway-with-pine-trees-in-winter-30663486/) |  |
| 41 | `4946941` | coniferous-trees-covered-with-snow-in-sunny-winter-day | coniferous, trees, covered, snow, sunny | `cool_white` | [预览](https://www.pexels.com/photo/coniferous-trees-covered-with-snow-in-sunny-winter-day-4946941/) |  |
| 42 | `5097652` | a-snow-covered-pathway-between-pine-trees | snow, covered, pathway, pine, trees | `cool_white` | [预览](https://www.pexels.com/photo/a-snow-covered-pathway-between-pine-trees-5097652/) |  |
| 43 | `904382` | snow-covered-pine-trees-under-cloudy-sky | snow, covered, pine, trees, cloudy | `cool_white` | [预览](https://www.pexels.com/photo/snow-covered-pine-trees-under-cloudy-sky-904382/) |  |
| 44 | `18416001` | scenic-panorama-of-a-mountain-with-peak-hidden-in-fog | scenic, panorama, mountain, peak, hidden | `cool_grey` | [预览](https://www.pexels.com/photo/scenic-panorama-of-a-mountain-with-peak-hidden-in-fog-18416001/) |  |
| 45 | `21348382` | coniferous-trees-covered-with-snow | coniferous, trees, covered, snow | `cool_white` | [预览](https://www.pexels.com/photo/coniferous-trees-covered-with-snow-21348382/) |  |
| 46 | `10346402` | gray-and-black-mountain-in-fog | gray, black, mountain, fog | `cool_grey` | [预览](https://www.pexels.com/photo/gray-and-black-mountain-in-fog-10346402/) |  |
| 47 | `2365457` | snow-covered-mountain | snow, covered, mountain | `cool_white` | [预览](https://www.pexels.com/photo/snow-covered-mountain-2365457/) |  |
| 48 | `18145514` | mountain-covered-in-fog | mountain, covered, fog | `cool_grey` | [预览](https://www.pexels.com/photo/mountain-covered-in-fog-18145514/) |  |
| 49 | `3732527` | leafless-tree-under-gray-sky | leafless, tree, gray, sky | `neutral_grey` | [预览](https://www.pexels.com/photo/leafless-tree-under-gray-sky-3732527/) |  |
| 50 | `1888403` | bare-tree-on-snow | bare, tree, snow | `cool_white` | [预览](https://www.pexels.com/photo/bare-tree-on-snow-1888403/) |  |

---

## 已被审美 Agent 淘汰的 12 张(供你交叉校验)

| Photo ID | 季节 | Slug | 淘汰理由 |
|---|---|---|---|
| `10770234` | 春 | silhouette-of-mountains-under-the-blue-sky | 剪影+蓝天,接近励志风 |
| `4497588` | 春 | quiet-green-forest-on-sunny-spring-day | 晴朗鲜绿,饱和偏高 |
| `795622` | 春 | green-forest | 过于平淡通用,缺记忆点 |
| `149521` | 春 | white-and-green-flower-plant-in-leafless-tress | 花卉特写,偏装饰性 |
| `1226302` | 春 | close-up-photo-of-green-fern-leaf | 特写微距,不适合横幅 |
| `240040` | 夏 | forest | 标题极泛,通用图库感 |
| `1822996` | 夏 | electric-lines-over-cloudy-sky | 电线杂乱,破坏克制感 |
| `17399019` | 秋 | landscape-of-a-grass-field-and-hills-under-blue-sky | 鲜蓝天,色调不符 |
| `34063300` | 秋 | golden-wheat-field-under-clear-blue-sky | 金黄+鲜蓝天,饱和过高 |
| `6435268` | 秋 | gyeongbokgung-palace-in-the-middle-of-the-lake-surrounded-with-autumn-trees | 宫殿景点,商业图库感 |
| `4500037` | 春 | forest-in-foggy-morning-in-summer | 季节归属错,夏景 |
| `4468715` | 春 | summer-morning-in-calm-forest | 季节归属错,夏景 |

> 如果你认为其中任何一张应该**保留**,告诉我,我把它放回对应季节,然后从该季淘汰另一张(由你指定)。


---

## 调色板分布(供你"按色挑"参考)

- **春**:cool_grey=28, neutral_grey=12, dark_grey=4, muted_green=2, monochrome=2, warm_grey=1, cool_blue=1
- **夏**:cool_blue=14, neutral_grey=13, cool_grey=9, muted_green=7, dark_grey=5, warm_grey=1, monochrome=1
- **秋**:warm_grey=33, cool_grey=8, neutral_grey=8, dark_grey=1
- **冬**:cool_white=32, neutral_grey=6, monochrome=6, cool_grey=5, cool_blue=1


---

## 下一步(用户审阅通过后)

1. CC 从用户审阅通过的春 / 冬池里挑 1 张最稳妥的(雾山 / 雪原 / 留白海面),压缩到 1280×400 + JPEG q=80 + ≤80KB,commit 为 `assets/fallback_header.jpg`
2. CC 实现 `src/collectors/header_image.py`(三层降级:Pexels 白名单 → Bing 每日 → 本地 fallback),5s 超时,不重试,永不抛异常
3. CC 改 `src/main.py` 调用 + `src/renderer/templates/email.html.j2` 顶部加刊头图区(**铁律:不动 M4 已定稿正文**)
4. CC 写 `tests/test_header_image.py`(覆盖 200 / 主源失败 / Bing 失败 / 文件丢失等场景)
5. CC 写 ADR `docs/decisions/0009-email-rendering-slo.md`(SPEC M5.9 要求,编号顺延)
6. CC 跨客户端兼容性测试(QQ webmail / QQ mac / Gmail / Apple Mail / Outlook)