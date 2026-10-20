# M07 同底座评估、合并与模型服务设计

## 已知需求与设计选择

用户要求完成M06后规划并实现M07；Windows只保存代码和文档、运行已有标准库测试，Ubuntu安装与GPU实验在独立会话进行。继续使用M06固定的Python3.11、LF v0.9.4、Qwen3-4B-Instruct-2507和核心依赖，不增加vLLM等第二套运行栈。

主代理选择固定LF ChatModel作为真实生成后端，分别在独立进程执行base和adapter评估，退出进程释放显存。另设adapter-bf16与merged两种非量化配置检查导出回归。固定LF在非量化LoRA推理时会自动merge_and_unload，因此此组实际比较“BF16运行时合并”与“CPU导出重载”，不是独立未合并PEFT等价性证明。不把4bit与BF16差异归因为合并或LoRA收益。CPU导出使用未量化底座合并adapter，不修改原底座，不用4bit训练加载状态直接导出。

既有Ollama27B仅为独立系统参考。M05合成测试区不是现实企业泛化证据；模型输出不能充当身份认证、数据库权限或真实业务查询。

## 模块和冻结接口

### model_delivery.py：冻结交付输入（仅标准库）

`prepare_release(training_run: Path, data_dir: Path, adapter_dir: Path, output_dir: Path, baseline_path: Path) -> dict`。

- verify M06的sft run（拒绝smoke），捕获完整M05 bundle到私有临时快照再validate，来源manifest摘要必须与M06相同。
- adapter必须在该run的adapter目录或其checkpoint子目录，至少包含非空adapter_model.safetensors和合法adapter_config.json（LoRA、匹配底座）。只验证结构与绑定，不加载权重。
- 基础模型文件和adapter文件流式计算SHA256，保存相对文件名/大小/摘要；基础模型包含config、tokenizer、tokenizer_config、权重索引及全部分片，并记录存在的generation_config/vocab/merges/special_tokens_map。拒绝路径越界。哈希是本地版本指纹，不是官方来源认证。
- 创建新release目录，状态preparing→prepared。只导出`cases-validation.jsonl`（96）和`cases-test.jsonl`（48），格式沿用M04。验证数据用于开发/选checkpoint，test在方案冻结后执行。
- 生成合法JSON语法YAML：`base.yaml`、`adapter.yaml`、`adapter-bf16.yaml`、`merged.yaml`、`export.yaml`。model路径和adapter路径均绝对，merged目标固定为release/merged-model（准备时不创建）。base与adapter相同4bitNF4；adapter-bf16与merged相同BF16且无quantization字段。
- manifest字段：schema_version=m07-v1/status、source_training_run_manifest_sha256/source_bundle_sha256、assets={base:{path,files},adapter:{path,files}}、merged_path、generation、counts、artifacts（相对名→SHA256）。files为相对名→{sha256,size}。
- `verify_release(release_dir: Path, *, check_assets=True) -> dict`；`read_artifact(release_dir, manifest, name) -> bytes`校验实际返回的单次读取字节；`fingerprint_model(path) -> dict`、`fingerprint_adapter(path) -> dict`返回files。验证不因评估报告或merged输出出现就拒绝。
- CLI：prepare --training-run --data --adapter-dir --output [--baseline-cases]；verify --release。

### model_eval.py：生成证据与比较（重依赖仅服务器延迟导入）

`evaluate_release(release_dir, variant, split, output_dir, limit=None, backend_factory=None) -> dict`。variant为base/adapter/adapter-bf16/merged；split为validation/test。backend_factory接收LF配置dict，返回有`chat(user_text, system_prompt)->dict`与`runtime`属性的对象。chat返回{text,finish_reason,prompt_tokens,output_tokens}；runtime记录Python/core包/Factory来源。默认后端延迟加载固定LF ChatModel，正式CLI禁止假后端开关。

- 执行前校验release与资产。输入题集/config使用read_artifact的同一快照；prompt复用M04 SYSTEM_PROMPT，评分复用score_response。
- 固定greedy、max_new_tokens=256、单响应、seed42；不给JSON受限解码，避免把协议约束当微调效果。prompt和schema摘要、生成参数、代码版本及runtime都入记录。上下文预算2048，真实模板编码检查prompt+256不超过预算，不静默截断。
- 新输出目录：manifest.json（running→completed或failed）、cases.json、records.jsonl、summary.json。记录每个选中样本，包括请求错误；第一次生成异常后剩余样本标记未执行，分母不缩小，不自动重试。初始化错误保存失败状态。
- 记录原始回答、结束原因、token数、耗时、严格JSON/schema/四字段/完全匹配、类别和拒答误放行计数。长度截断不给业务正确分；LF结束原因限制在教程解释，并核对本模板EOS语义。
- 运行后再次核对基础模型/adapter指纹，merged模式另记录合并模型前后指纹。文件变化、错误、子集都不能进入正式比较；未达目标准确率不算程序错误。
- `compare_runs(left_dir,right_dir,output_file)->dict`：校验报告artifacts、running/failed状态、完整性、唯一题号、题集/期望答案/prompt/schema/生成参数/release身份/runtime/源资产。只允许base→adapter和adapter-bf16→merged。重新从原始回答计算指标，不盲信summary。输出百分点变化、改善/退步/不变题号、生成文本一致率；合并比较独立标注，不能和4bit收益混算。输出拒绝覆盖。
- CLI evaluate --release --variant --split --output [--limit]；compare --before --after --output。错误或不具备正式比较资格非零，子集仅smoke。

### model_api.py：回环API健康与契约探针（标准库）

复用既有回环地址校验原则：仅HTTP127.0.0.1/localhost/::1，不使用代理、重定向或自动重试；socket timeout、4MiB响应上限、严格JSON。API_KEY仅从环境读取，不保存值或Authorization头，不在命令参数传凭证。

CLI `probe --base-url --model --output [--timeout]`，请求/v1/models及单条固定假期意图/v1/chat/completions，检查HTTP/响应结构/model字段/非流式assistant文本/finish_reason，并按M04契约评分。失败报告非零；服务可用不等于模型质量通过，API模型名是显示名称，不是权重身份认证。

## 教程和运行手册

第7课覆盖：指标/错误分母、同底座对照、验证集选型与测试集冻结、两题试跑→完整评估、失败分析、CPU合并、新目录和前后回归、API回环监听/环境密钥、健康调用、显存互斥、切回已验证配置。运行手册包含版本/资产/测试结果/服务配置/启动日志/回滚证据模板。

不搭建新Web应用，不把模型服务声称为企业认证API。FastAPI业务层、认证/审批/审计属于M12；此处使用固定LF原生API。

## 验收边界

本地：标准库测试、假生成后端、伪权重结构、localhost假HTTP服务（不是平台服务）、教程语法/链接、独立review。按用户限制，不启动真实模型服务。Ubuntu：真实ChatModel、GPU内存、导出加载、API鉴权、完整评估与回滚。未实测不填写理想分数或宣称训练改善。
