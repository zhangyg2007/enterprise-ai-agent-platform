# M04 模型调用与基线设计

承接已确认的平台设计与 M04 授权。目标：学习者能在 Ubuntu 上识别已有 Ollama 模型，执行可追溯的小规模评估，为 M05–M07 的数据与模型对照提供基础。

## 范围与约束

- Windows 仅保存文件、执行已有 Python 标准库测试；不安装依赖，不启动模型、数据库或其他服务。
- Ubuntu 24.04、128GB RAM、4090D 24GB；模型标签按实际 inventory 核实，不能由 `qwen3.8:27b` 推断官方架构或训练权重。
- Python 3.11–3.13，零新增依赖。模型参数采用 CLI，不修改 M01 的数据库 `.env` 契约。
- 本模块只分类和提取字段，不查数据库、不执行 SQL、不提供认证、不训练。模型拒答不是访问控制。
- 16 道公开合成题是教学开发集，不是独立测试集；后续训练不得直接复用本题集作泛化结论。

## 实现选择（实施方决定）

选择标准库 HTTP 适配器而非提前引入 Agent 框架；HTTP 仅接受回环地址，不跟随重定向、不读取代理，不重试。超时是 socket 操作超时，不是完整运行的硬截止时间。限制响应为 4 MiB。

`OllamaClient(base_url='http://127.0.0.1:11434', timeout_seconds=120)`：

- `inventory(model: str) -> dict`：读取 version、tags、show；精确匹配标签，要求非空 digest，拒绝已知远程模型标志。返回 `model`、`digest`、`server_version`、`details`、`capabilities`、`template_sha256`、`show`。没有 metadata 的字段如实保留缺失，不猜来源。
- `chat(model, messages, *, format_schema, options, think=None) -> dict`：非流式原始响应；显式 `stream=false`、`keep_alive='5m'`。不执行 tool_calls。检查 assistant/content/done；输出截断由评估器判为质量失败，保留 raw。
- `ModelError(code, message, *, status=None, raw=None)`：安全错误说明和结构化原因。测试注入/替换连接对象，禁止真实网络。

`baseline.py` 负责固定契约、样本校验、评分与 CLI，`evaluation.py` 负责运行记录。输出对象严格四字段：`intent`（customer_summary/leave_balance/inventory/unsupported）、`customer_id`、`product_id`（正整数或 null）、`action`（query/clarify/refuse）。不做字符串或 bool 到整数的转换。分类规则：本人假期可 query；他人假期 refuse；不支持的隐私/指令执行 refuse；多项业务请求 unsupported/clarify；缺 ID clarify。

CLI：`python -m enterprise_ai.baseline inventory --model NAME --output FILE`；`evaluate --model NAME --cases FILE --output DIR [--limit N] [--think auto|off|on] [--timeout 120] [--num-ctx 4096] [--num-predict 256]`，两者可指定 `--base-url`。temperature=0、seed=42。目录/文件已存在时在调用模型前拒绝覆盖。limit=2 用于冒烟，完整默认为16题。

每次评估保存源题集与选题 SHA256、prompt/schema 摘要、实际配置、代码修订与脏状态、Python/platform、UTC、模型前后清单、原始响应/错误、耗时、逐题分数、summary。manifest 初始 running，完成后 completed；中断保留现场，不续跑覆盖。逐题 JSONL 刷盘；最终 manifest 原子替换。前后 digest/server_version/template 变化则不可比较。

所有被选题都进入分母；超时、截断、无效 JSON 均不能获得业务字段分。JSON 语法、schema、字段准确率、exact match 分开统计。输出平均/p50/p95 请求墙钟秒数（含失败请求，nearest-rank 分位数），可选 eval_count/eval_duration 吞吐只对有效正值计算，不能当用户端吞吐。模型错误与答错分开：运行完整且无调用错误/身份变化返回0，即使准确率低；调用错误/身份变化返回2，输入/文件错误返回1。

## 教学验收

教程须包含概念图、环境检查、原始请求、两题冒烟、16题基线、手工核算分母、参数解释、逐题失败分析、恢复、练习答案与官方链接。明确 Ollama 模板和 HF tokenizer 模板的职责，以及 GGUF 推理权重不能直接作为本课程微调输入。未真实运行模型，不填写虚构模型得分。

离线测试覆盖安全地址、HTTP/协议错误、严格 JSON、全失败分母、截断、身份变化、输出不覆盖、部分运行、样本泄漏边界。独立审查与作者验证分开执行；服务器真实兼容性另列待验收。
