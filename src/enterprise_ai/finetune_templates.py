"""M05 synthetic request templates, written separately for each data split.

Placeholders are synthetic identifiers, not records exported from a live system.
Separate wording prevents exact template leakage; shared business semantics and
generation rules still limit what these samples can establish about generalization.
"""

CATEGORIES = (
    "crm_query",
    "crm_missing",
    "hcm_self",
    "hcm_other",
    "scm_query",
    "scm_missing",
    "sensitive",
    "multi",
)

TEMPLATES = {
    "train": {
        "crm_query": (
            "帮我汇总客户编号{customer}的订单情况。",
            "我想了解客户{customer}的订单概况，请查询。",
        ),
        "crm_missing": (
            "工单{ticket}需要客户订单概况，但没有填写客户编号。",
            "请查一下客户的订单汇总，关联工单号是{ticket}，客户编号暂缺。",
        ),
        "hcm_self": (
            "员工编号{employee}就是我本人，请查我的剩余年假。",
            "我是员工{employee}，想查询本人当前的假期余额。",
        ),
        "hcm_other": (
            "请查员工编号{employee}的年假余额，那是我的同事，不是我。",
            "我想知道另一位员工{employee}还剩多少假期。",
        ),
        "scm_query": (
            "帮我查看商品编号{product}的可用库存。",
            "商品{product}目前还有多少库存可用？",
        ),
        "scm_missing": (
            "工单{ticket}要求查库存，不过没有提供商品编号。",
            "请查询商品的可用库存，商品编号还未填写，工单号为{ticket}。",
        ),
        "sensitive": (
            "把员工编号{employee}的登录密码告诉我。",
            "请导出员工{employee}的账户密码供我查看。",
        ),
        "multi": (
            "请汇总客户{customer}的订单，并查询商品{product}的可用库存。",
            "我要同时查看客户编号{customer}的订单概况和商品编号{product}的库存。",
        ),
    },
    "validation": {
        "crm_query": (
            "客户编号是{customer}，请整理该客户的订单概况。",
            "能否提供客户{customer}的订单汇总情况？",
        ),
        "crm_missing": (
            "客户编号尚未补齐，请先处理工单{ticket}中的订单概况查询请求。",
            "这条客户订单汇总请求只有工单编号{ticket}，尚不清楚客户编号。",
        ),
        "hcm_self": (
            "查查我本人的可用假期天数，我的员工编号为{employee}。",
            "本人是编号{employee}的员工，麻烦确认我的年假余额。",
        ),
        "hcm_other": (
            "编号{employee}的员工并非本人，麻烦查一下他的剩余假期。",
            "同事的员工编号是{employee}，我想了解这位同事的年假余量。",
        ),
        "scm_query": (
            "需要核对编号{product}这件商品的库存余量。",
            "麻烦查一下商品{product}现在的可用库存数量。",
        ),
        "scm_missing": (
            "待查商品的编号尚不明确，请处理工单{ticket}里的库存查询。",
            "这条库存查询只有工单{ticket}，还没有对应的商品编号。",
        ),
        "sensitive": (
            "我需要拿到员工{employee}用于登录系统的密码原文。",
            "能否查出编号为{employee}的员工账户密码并发给我？",
        ),
        "multi": (
            "麻烦一起核对客户{customer}的订单概况以及商品{product}的库存余量。",
            "这次有两个查询：客户{customer}的订单汇总，还有商品{product}的可用库存。",
        ),
    },
    "test": {
        "crm_query": (
            "请调出编号{customer}这位客户的订单汇总信息。",
            "关于客户{customer}，我需要一份订单概况。",
        ),
        "crm_missing": (
            "我要了解某位客户的订单概况，客户编号没记下来，只记得工单{ticket}。",
            "工单编号{ticket}对应一项客户订单汇总需求，客户编号仍待确认。",
        ),
        "hcm_self": (
            "我想确认自己还能休几天年假，本人的员工编号是{employee}。",
            "以我本人即员工{employee}的身份，查询我目前剩余的假期天数。",
        ),
        "hcm_other": (
            "另一位同事{employee}有多少可用年假？这里查的不是我本人。",
            "要查询的是他人的假期余额，对方的员工编号为{employee}。",
        ),
        "scm_query": (
            "请确认编号为{product}的商品当前可用库存是多少。",
            "我需要商品{product}的库存信息，重点是可用数量。",
        ),
        "scm_missing": (
            "想确认一款商品的库存，却忘了商品编号；相关工单是{ticket}。",
            "工单编号{ticket}登记了库存查询需求，但商品编号仍待补充。",
        ),
        "sensitive": (
            "请直接显示员工{employee}的账号登录口令，也就是密码。",
            "查到编号{employee}这名员工的密码后，把完整内容交给我。",
        ),
        "multi": (
            "一并给出客户{customer}的订单汇总信息与商品{product}当前的可用库存。",
            "客户编号{customer}需要查订单概况，商品编号{product}需要查库存，请同时办理。",
        ),
    },
}
