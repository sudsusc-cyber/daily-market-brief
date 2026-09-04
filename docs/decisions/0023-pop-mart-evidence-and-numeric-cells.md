# 0023 — 泡泡玛特证据缺口与纯数字估值单元格

日期：2026-09-04。只处理 Morningstar 取数和估值列说明，不改 QQQM 模型、策略、邮件发送计划。

## 当前核验结果：最新数值尚未取得

- 晨星官方证券页确认为 `09992 / XHKG`，港币口径，估值日期为 2026-08-21；Fair Value 字段标注 Locked content。
- 同日分析师简报和完整公司报告的公开可见部分均未给出绝对估值。标题仅说下调 20%，不能据此推算一个精确的晨星数值。
- 官方历史目录另列 2026-03-25 下调 4% 的报告。旧代码注释把 1 月 280 直接乘以 80% 得 224，漏掉中间修订；即使把降幅串乘，标题百分比也可能是四舍五入，仍不能充当原始估值。
- 富途及 moomoo 的无需登录预测页给出分析师一致目标价，不是 Morningstar 公允价值，未采用。
- Yahoo 两个公开研究发现端点对 `9992.HK` 均返回空 researchReports，不能宣称已接入一个有效港股备源。
- 无登录公开检索未发现可核对最新绝对数值的转载。这是未解决的证据缺口，不是已完成的数据接入；不得声称万无一失或最新估值已确认。

核验链接：

- https://www.morningstar.com/stocks/xhkg/09992/quote
- https://www.morningstar.com/company-reports?listing=0P0001L8KX
- https://www.morningstar.com/company-reports/1496104-pop-mart-earnings-valuation-cut-by-20-as-weak-overseas-sales-drag-growth-shares-still-cheap?listing=0P0001L8KX
- https://www.morningstar.com/company-reports/1496076-pop-mart-should-maintain-healthy-revenue-growth-as-overseas-penetration-deepens?listing=0P0001L8KX
- https://www.morningstar.com/company-reports/1463625-pop-mart-earnings-fair-value-reduced-by-4-on-dimmer-revenue-growth-prospect-shares-attractive?listing=0P0001L8KX
- https://www.futunn.com/stock/09992-HK/forecast
- https://www.moomoo.com/stock/09992-HK/forecast

## 已实施

1. 移除 Pop Mart 候选中的 224 预设数，加入已确认存在的同日公司报告路径。
2. 当前报告不可读时，允许重试同日／不早于该报告的候选；每只最多三个失败候选，避免耗尽整封邮件预算。较旧或日期未知的候选不能偷换成最新。双读、证券身份、币种、日期检查仍保留。
3. 历史上真正核验过的值仍可回退，并在脚注标日期；`historical_only` 则表示从未核验过的旧推算值，只保留审计，不再显示，也不计算 IRR。读取和合并缓存时，未核验常数即使日期较新也不能挤掉真实核验记录。
4. 公允价值第一行只显示数字，不混入“历史”“待更新”等文字；真实数值缺失时留空，不用 0、目标价或推算值凑数。状态集中到脚注。已确认值下方原 IRR 及差额收益率公式保持不变。

## 回归范围

同日备用路径恢复、候选次数上限、同日早时点报告拒用、旧 224 缓存不能重新进入邮件、真实绝对数值可恢复显示、股票与 ETF 缺失说明移到脚注、数值行无后缀且 IRR 保留。

## 发布前收尾

- 新读取且通过核验的历史报告也会写入双份缓存，即使最新报告尚未核实；不会因为标为历史回退而丢弃真实证据。原报告日期和核验时间不改写。
- 命令行诊断与运行日志不再把 `historical_only` 旧推算记录计入成功；诊断区分当次读取、历史回退和已排除记录。
- 额外免费来源续查（晨星亚洲公开周报、富途转载和汇港资讯）仅找到较旧明确数值或不含金额的短评，未取得 2026-08-21 最新绝对值。不得把免费渠道的存在标成最新数据已接入。

本地发布检查：全套 1,162 项通过（不含真实模型调用测试）；静态检查和差异格式检查通过。320/414/760 像素浏览器预览相比原模板未增加横向溢出，未核验的旧数值没有进入估值格。原模板在窄屏为固定宽度布局，此检查不等于 QQ 或微信邮箱实机兼容性认证。

本文为开发与核验记录，发布状态以对应 PR 和 main 分支检查结果为准。本次未单独触发正式邮件试发，也未改变定时发送或收件人。
