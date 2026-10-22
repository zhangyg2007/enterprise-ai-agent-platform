# M06 单卡 Qwen 微调课程设计

用户将安装与实机测试放到另一个会话；本会话继续设计、实现、审核和GitHub交付。沿用已确认4B非思考底座路线；不在Windows安装依赖或运行GPU。

## 固定来源与边界

- LLaMA-Factory v0.9.4 / `95ac3f2373b82662c1bd855c284d3379e6a763d3`。
- Qwen/Qwen3-4B-Instruct-2507 / `cdbee75f17c01a7cc42f958dc650907174af0554`；template=`qwen3_nothink`，不需要enable_thinking字段。
- 训练Python3.11，候选torch2.6.0+cu124/torchvision0.21.0/torchaudio2.6.0，transformers4.57.1、peft0.17.1、bitsandbytes0.46.1、accelerate1.11.0、datasets4.0.0、trl0.24.0、torchdata0.11.0。源码约束相容，不冒充GPU实测或完整传递依赖锁。
- 数据使用M05通过validate的bundle。只复制train/validation，绝不使用test选模型。没有自动安装、下载或训练入口；命令由用户在Ubuntu执行。

## 文件与接口

配置 `configs/training/m06-smoke.yaml`、`m06-sft.yaml` 使用JSON语法的合法YAML，便于标准库读取和审阅；`m06-versions.json`记录来源；`requirements/m06-constraints.txt`记录核心约束。

`enterprise_ai.training prepare --data DIR --model-dir DIR --run-dir DIR --profile smoke|sft [--baseline-cases FILE]`：先validate M05、检查本地HF模型config/tokenizer和safetensors索引存在，拒绝GGUF/非qwen3与缺失权重；不加载权重。新run目录独占，复制不可变数据快照和注册表，生成绝对路径train.yaml与manifest。smoke取训练前64、验证前32（保持16条场景包边界），10更新步；sft使用336/96全部数据，3epoch。输出目录为run/adapter，初始不创建。配置禁止覆盖已有adapter，不复用tokenized_path、不设置max_samples。LLaMA-Factory会自动接续已有checkpoint，首次训练命令因此必须先拒绝已存在的adapter目录，不能把overwrite=false描述为禁止续训。

`prepare_run(data_dir:Path, model_dir:Path, run_dir:Path, profile:str, baseline_path:Path)->dict`；`verify_prepared(run_dir:Path)->dict`校验所有准备产物摘要、数据计数与绝对路径，不能把模型目录检查称为官方来源证明。manifest含profile、source_bundle_sha256、model路径/模型config摘要、官方revision期望值、config/data摘要、counts、status prepared。生成途中为preparing；完成后原子替换。验证不因训练额外日志/adapter出现就拒绝，但准备文件不可修改。

`enterprise_ai.training_checks preflight --factory-dir DIR --output FILE`：服务器只读收集实际Python/core包版本、torch CUDA、设备信息，核对固定factory HEAD与核心版本；CUDA/BF16不可用返回非零。执行一个很小的bnb NF4 linear forward/backward检查；明确非4B训练验收。没有GPU或缺依赖时保留失败报告，不堆栈倾倒环境密钥。

`enterprise_ai.training_checks token-lengths --run-dir DIR --output FILE`：先verify_prepared，调用本地AutoTokenizer(local_files_only=True,trust_remote_code=False)，使用固定版本LF `get_template_and_fix_tokenizer` 和 `encode_oneturn`检查train/validation的system+user+assistant真实长度，拒绝任何超过cutoff或没有答案token；不读取test、不加载模型权重。保存计数、min/max、超限样本索引、模板和tokenizer信息；通过只证明长度检查，不代表LF实际训练加载已验证。输出文件都拒绝覆盖。

## 训练策略

QLoRA bnb4bit NF4、double quant、LoRA r8 alpha16 dropout0.05 target all、BF16、梯度检查点、SDPA、packing=false/neat_packing=false、单卡batch1。smoke累积4、max_steps10、eval/save5；sft累积8、epoch3、eval/save42、learning_rate1e-4。显存不足先评估cutoff，当前batch已经为1；新参数必须生成新run并保留原实验。不能自行删除数据或禁用验证。日志report_to=none，save_only_model=false保留恢复材料。WebUI只绑定回环，用于字段理解与预览；CLI使用冻结的train.yaml实际训练，避免界面默认验证切分改变实验。

## 教学质量与验收

教程包括安装候选、下载固定snapshot、WebUI字段映射、冒烟到正式、参数含义、loss与业务准确率区别、OOM/NaN/中断恢复、adapter产物、跨会话诊断交接模板。训练前保存未微调对照的工作划入M07，同一冻结题集评估不能拿Ollama27B与4B分数差当微调收益。

离线仅验证准备器、配置、检查器的失败/计算分支和教程语法。GPU、HF下载、真实tokenizer、LF loader/WebUI/训练均标为服务器待验收。安装测试会话应回传版本、命令、脱敏日志与manifest；不复制.env或token。
