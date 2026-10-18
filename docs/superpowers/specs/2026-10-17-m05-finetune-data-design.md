# M05 微调数据设计

用户已要求继续完成M05并审核。延续M04四字段意图契约，目标是学会制作和验收训练数据，而不是把动态库存、员工隐私或数据库事实固化进模型。

## 数据与范围

纯标准库，Windows仅生成合成教学文件与测试，不安装LLaMA-Factory或运行模型。提供 `python -m enterprise_ai.finetune_data generate --output DIR --seed 42 --baseline-cases data/samples/m04-intents.jsonl` 和 `validate DIR --baseline-cases FILE`。

30个合成场景包，每包包含一个客户编号1000+n、商品编号2000+n、员工编号3000+n及工单编号4000+n。每包8类请求（客户查询/缺编号、本人假期/他人假期、库存查询/缺编号、敏感数据拒绝、多业务澄清），每类2种表达，共480条。不从M02导出真实或动态字段；ID只是合成标识，不代表M02表里的现存记录。

先按seed和group_id的SHA256排序将30组固定切分为21/6/3组，再为各分区使用分别撰写的表达模板，得到train336/validation96/test48条。相关实体必须同组、同分区；同时校验去数字后的模板指纹和规范化文本不跨分区。训练/验证/测试互不共享实体和完整模板文本，但仍共享业务语义和生成规则，不能宣传为真实业务泛化证明。

生成canonical samples.jsonl（含group/template/entity/provenance），三份Alpaca JSON及三份ShareGPT JSON，dataset_info.json仅注册train/validation的两种格式（4项），test-cases.jsonl可供M04评估器运行48题。test不注册训练项、不被训练/开发循环反复使用。两格式是同一数据的替代表示，不能同时训练造成重复加权。

Alpaca：instruction=原请求、input为空、output=四字段JSON字符串、system=M04固定提示。ShareGPT：system相同、conversations=[human/value请求,gpt/valueJSON答案]。dataset_info显式映射列和role/content标签。未使用history、工具、CoT或多模态。

## 验收与证据

输出新目录专用，不覆盖；manifest从building到complete，记录seed、生成器版本、M04题集字节摘要、prompt摘要、文件SHA256和计数。固定seed输出可重复（不写实时生成时间影响哈希）。生成后运行严格校验：题集schema、类别标签、实体/组/模板/文本跨split、M04输入/实体重合、两格式等价、注册文件路径、test隐藏、摘要完整性。保护用M04题集包含input和expected，仅读取做排除，不用于训练。

可复现的8类×3分区=24条人工抽样表模板，需另存到reports/local填写 reviewer/decision/note；自动校验不替代人工标注复核。数据卡解释来源、用途、数量、限制、版本、清洗标准、切分与许可边界。失败生成目录保留，不拿building目录训练。

接口：`build_samples(seed:int)->list[dict]`；`validate_samples(samples:list[dict], baseline_cases:list[dict])->dict`；`generate_bundle(output:Path,seed:int,baseline_path:Path)->dict`；`validate_bundle(root:Path,baseline_path:Path)->dict`。canonical字段为sample_id/group_id/split/category/template_id/entities/prompt/answer，provenance写在manifest/data card中。实现方可选模块拆分，但CLI和计数不变。

后续M06才安装并固定LLaMA-Factory revision、HF权重和tokenizer，检查真实token长度/截断与训练预览。M05字符上限不能替代token预算。训练参数片段用 `dataset` 指向train、`eval_dataset` 指向validation，不以val_size重新随机切分，不把test注册进去。
