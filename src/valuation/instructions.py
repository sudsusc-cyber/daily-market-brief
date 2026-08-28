"""交给 DeepSeek 的逐公司固定公式说明。

模型只负责从最新官方原文提取可审计输入并形成待批准草稿；最终估值、币种换算与
隐含收益率均由 Python 完成。这里的文字与 policy.py 一起版本化，不能由模型改写。
"""

from __future__ import annotations

from dataclasses import dataclass

from src.valuation.models import OfficialDocument
from src.valuation.policy import POLICIES, ValuationPolicy


@dataclass(frozen=True)
class FormulaInstruction:
    projection_rule: str
    fixed_rules: tuple[str, ...]
    forbidden: tuple[str, ...]
    update_cadence: str


FORMULA_INSTRUCTIONS: dict[str, FormulaInstruction] = {
    "MSFT": FormulaInstruction(
        "合并 FCFF：三分部收入分别预测，使用合并营业利润率与合并资本开支；股权价值桥接净现金。",
        ("SBC 不加回且股份固定为最新稀释股数", "资本开支含融资租赁取得的资产", "OpenAI 基准情景取零或经批准账面值"),
        ("分部资本开支", "分部营运资本", "未披露的 AI 基础设施分摊"),
        "股价每日；财务输入随 10-Q/10-K/Item 2.02 的 8-K 更新。",
    ),
    "COST": FormulaInstruction(
        "合并 FCFF：商品销售与会员费共同驱动收入，使用单一合并营业利润率；资本开支由新增门店与维护比例构成。",
        ("会员费只进入合并收入一次", "特别股息不改变经营价值", "会员费调价须有公司公告"),
        ("把会员费单独估值后仍保留合并利润", "猜测会员体系成本比例"),
        "股价每日；官方月度销售可更新收入锚点；会员、门店与资产负债表数据按季。",
    ),
    "AAPL": FormulaInstruction(
        "FCFE：净利润+折旧摊销-资本开支-营运资本增加，净新增借款取零；产品线收入与产品/服务毛利率驱动预测。",
        ("股份固定为最新稀释股数", "不做额外净现金桥接", "回购只作历史核对"),
        ("iPhone 销量或平均售价", "预测回购减少未来股数", "FCFE 后再加净现金"),
        "股价每日；分产品收入、毛利率与股份数按季。",
    ),
    "NVDA": FormulaInstruction(
        "基准情景 FCFF：按已披露市场平台收入驱动五年显性期，再线性衰减至固定永续利润率。",
        ("邮件内在价值与隐含收益率均采用基准情景", "拆股历史口径统一", "SBC 不加回且股份固定"),
        ("AI 加速器出货量", "平均售价", "把当前周期高点利润率直接永久化"),
        "股价每日；平台收入、利润率、净现金与股份数按季；云厂资本开支只触发复审。",
    ),
    "TSM": FormulaInstruction(
        "新台币中周期 FCFF：收入增长、固定中周期毛利率和资本开支/收入比；最后按即期汇率及 1 ADR=5 普通股换成美元。",
        ("海外厂成本只通过已批准中周期毛利率体现一次", "季度实际毛利率只作核对", "所有经营计算先以新台币完成"),
        ("产能利用率", "先进封装独立收入", "中途混用美元财报口径", "把 ADR 市场溢价加入内在价值"),
        "股价和 TWD/USD 每日；官方月营收按月；财务底稿按季/20-F/6-K。",
    ),
    "MCO": FormulaInstruction(
        "合并 FCFF：MIS 经常性收入按趋势增长，交易性收入采用固定七年中周期窗口；MA 由 ARR 驱动。",
        ("两个分部使用同一固定折现率", "交易性收入均值窗口固定为七年"),
        ("自行推导有效收费率", "以单年评级发行量永久外推"),
        "股价每日；分部收入、ARR、利润率、净现金与股份数按季。",
    ),
    "GOOG": FormulaInstruction(
        "合并 FCFF：Services 与 Cloud 收入/利润驱动，资本开支只用合并口径；EV 后加净现金及税后权益投资。",
        ("Other Bets 基准估值为零时加回其营业亏损", "Alphabet 级费用保留", "SBC 不加回且股份固定"),
        ("分部资本开支", "YouTube 独立利润", "权益投资收益进入 EBIT", "投资资产重复计价"),
        "股价每日；分部收入利润、资本开支、投资公允价值、净现金与股份数按季。",
    ),
    "BRK.B": FormulaInstruction(
        "固定倍数 SOTP：税后上市股票+现金国债+正常化承保利润、BNSF、BHE、制造服务零售各自价值+权益法投资-母公司债务。",
        ("承保利润使用固定十年均值", "只减母公司债务", "每股按 A 类×1500+B 类等价股数"),
        ("加入浮存金资产", "上市股票税折与递延税负重复扣除", "经营利润包含保险投资收益", "再次扣 BNSF/BHE 债务"),
        "主要上市投资市价每日；13F、板块利润、现金国债与股份数按季；以固定五年退出 SOTP 计算 5Y SOTP IRR。",
    ),
    "KO": FormulaInstruction(
        "两阶段 FCFE：收入由有机增长（销量+价格组合）驱动，净新增借款按批准目标杠杆规则。",
        ("股份固定为最新稀释股数", "汇率影响只按公司指引进入季度底稿", "IRS 或有负债只在批准情景扣减"),
        ("预测回购减少未来股数", "把经营汇率影响当成每日币种换算"),
        "股价每日；有机增长、利润率、股份数和或有事项按季。",
    ),
    "AXP": FormulaInstruction(
        "剩余收益：当前每股有形账面价值+各期(ROTE-固定股权成本)×期初有形账面价值的现值及终值。",
        ("ROTE 显性期后线性衰减至固定永续值", "信贷损失采用固定正常化窗口", "全程每股口径"),
        ("使用 FCFF", "把超额资本在 ROTE 之外再次加入", "以单季信贷损失率代表完整周期"),
        "股价每日；每股有形账面价值、ROTE 和信贷损失率按季。",
    ),
    "0700.HK": FormulaInstruction(
        "人民币合并 FCFF：分部收入×分部毛利率汇总毛利，按批准费用率得到营业利润；EV 后加折价投资与净现金，再换港元。",
        ("只从营业利润出发", "上市与非上市投资采用各自固定折让", "递延收入只在营运资本中体现一次"),
        ("分部营业利润", "分部资本开支", "广告加载率/单价", "从净利润出发后再次加投资"),
        "股价、CNY/HKD 和主要上市投资市价每日；财务输入按 HKEX 业绩公告。",
    ),
    "9992.HK": FormulaInstruction(
        "基准情景人民币 FCFF：渠道与地区收入、海外增长路径、头部 IP 衰减率、毛利率和固定存货减值；最后换港元。",
        ("邮件只采用基准情景", "情景概率及高速增长年限冻结", "门店收入用平均门店数×披露数据倒算单店销售"),
        ("量化新 IP 成功率", "无官方更新时凭新闻改写现金流", "头部 IP 永久不衰减"),
        "股价和 CNY/HKD 每日；门店、渠道地区收入、IP、存货、净现金与股份数按半年。",
    ),
    "MA": FormulaInstruction(
        "FCFE：以已扣客户激励的净收入为起点，GDV 与跨境交易额按固定权重驱动增长，诉讼费用正常化。",
        ("股份固定为最新稀释股数", "跨境权重与正常化诉讼额冻结", "客户激励只作分析变量"),
        ("从净收入再次扣客户激励", "预测回购减少未来股数", "猜测跨境有效费率"),
        "股价每日；GDV、跨境额、净收入、利润率与股份数按季。",
    ),
    "LIN": FormulaInstruction(
        "合并 FCFF：既有业务价格/销量增长与积压订单固定转化分开，资本开支用总额，工程业务并入合并口径。",
        ("积压订单转化比例固定", "永续资本开支/折旧比与 ROIC 固定", "EV 后桥接净债务及少数股东权益"),
        ("历史合并增速与积压订单再次叠加", "猜测维护性/增长性资本开支拆分", "单独估值工程业务"),
        "股价每日；积压、收入、利润率、资本开支、股份数与净债务按季。",
    ),
}


def build_snapshot_draft_prompt(
    *,
    policy: ValuationPolicy,
    document: OfficialDocument,
) -> str:
    """生成待批准底稿的强约束提示；不让模型输出最终估值。"""
    instruction = FORMULA_INSTRUCTIONS[policy.ticker]
    fixed = "\n".join(f"- {rule}" for rule in instruction.fixed_rules)
    forbidden = "\n".join(f"- {rule}" for rule in instruction.forbidden)
    return f"""\
为 {policy.ticker} 从下列已下载并完成哈希的官方文件生成估值输入草稿。
文件编号：{document.document_id}
文件类型：{document.document_type}
报告期间：{document.report_period or '未标明'}
官方 URL：{document.source_url}
SHA-256：{document.content_hash or 'MISSING'}

固定公式：{instruction.projection_rule}
固定规则：
{fixed}
禁止使用或猜测：
{forbidden}
更新节奏：{instruction.update_cadence}

折现率固定为 {policy.hurdle_rate:.1%}，永续增长率固定为 {policy.terminal_growth if policy.terminal_growth is not None else '不适用'}，公式 ID={policy.formula_id}，模型版本={policy.model_version}，情景={policy.scenario}。不得修改这些字段。

只返回 JSON 草稿。每一个财务输入必须附官方原文中的字段名、报告期间、页码或章节和原文短句；没有官方披露就填 null 并将 status 设为 needs_review，严禁用新闻、分析师数据、搜索摘要或常识补数。不得输出内在价值、隐含收益率或安全边际；这些由 Python 复算。新财报出现后不得沿用旧底稿。\
"""


def validate_instruction_coverage() -> None:
    if set(FORMULA_INSTRUCTIONS) != set(POLICIES):
        raise RuntimeError("逐公司估值说明与固定政策覆盖不一致")


validate_instruction_coverage()
