# 第 4 课：接入 Ollama，建立可追溯的模型基线

本课对应 M04。你将把已有模型接入 Python，让它将中文业务请求转换成固定 JSON，然后用 16 道合成题检查：是否能调用、输出格式是否合格、业务判断是否正确、一次请求花了多久。最终交付的是一份可以追踪到模型、代码、提示词和题集的实验记录。

**执行位置：本文所有命令均在 Ubuntu 24.04 服务器终端执行。** 复用 M01 的 Python 环境和现有 Ollama，不在 Windows 安装或运行平台。本课不训练模型、不连接数据库、不执行模型生成的 SQL，也不需要启动 M01 的数据库容器。

**版本前提：取得课程版本 0.1.2.0 或更新版本，并核对本课文件存在。** 文中的输出结构和题数是程序约定，不是你的模型实测成绩。现有标签 `qwen3.8:27b` 必须以服务器清单核实；仅凭这个名称，无法确认官方模型系列、架构、权重来源或微调兼容性。

命令按顺序逐条执行；除文中专门解释的评估失败外，任一步非 0 都先停止并排查，不用后续命令的成功掩盖前一步错误。本文的目录变量和相对路径都以项目根目录为基础。

## 1. 本课解决什么问题

在调用 Agent 框架前，先把一次模型调用拆开：

```mermaid
flowchart LR
    A[合成中文请求] --> B[固定提示词与输出契约]
    B --> C[服务器本机 Ollama]
    C --> D[原始响应与调用耗时]
    D --> E[JSON 与字段校验]
    E --> F[对照预期答案评分]
    F --> G[逐题记录与汇总]
```

例如“请查看客户 1 的订单概况”，本课希望得到：

```json
{"intent":"customer_summary","customer_id":1,"product_id":null,"action":"query"}
```

它只表示“识别为客户概况请求，提取到客户编号 1”。它没有证明客户存在，没有读取订单，更没有证明当前用户有权查看这个客户。后续 Agent 需要从可信登录会话获取用户和租户身份，经权限校验后调用确定性的数据库工具。模型输出 `query` 只是候选动作，不能直接授予数据库权限。

本课分三层验收：

| 层次 | 要回答的问题 | 证据 |
|---|---|---|
| 调用层 | 请求是否完成？是不是所选模型？ | 清单、HTTP/协议错误、原始响应 |
| 输出层 | JSON 能否解析？四个字段类型是否正确？ | JSON 合格率、schema 合格率 |
| 业务层 | 意图、编号、动作是否与规则一致？ | 字段准确率、整题准确率、失败案例 |

格式合格不等于业务正确。例如把客户 1 识别成客户 17，仍可能完全满足 JSON schema；这样的答案要在业务评分中扣分。

## 2. 先分清模型标签、权重和聊天模板

| 名称 | 本课如何使用 | 后续需要注意什么 |
|---|---|---|
| Ollama 模型标签 | 指定请求目标，例如本机清单中的完整 `name` | 标签可以指向不同内容，必须同时记录 digest |
| GGUF | Ollama 清单可能显示的推理权重格式 | 不把本机量化推理文件直接当成本课程 LLaMA-Factory 训练输入 |
| Hugging Face 模型目录 | 本课暂不下载 | 后续核对官方仓库、revision、tokenizer、配置、权重及许可 |
| 聊天模板 | 把 system/user 等消息组织成模型认识的序列 | 模板不匹配可能影响输出；训练与推理时都要核对 |

Ollama 的 `/api/chat` 接收消息列表，服务端使用模型模板。你只传 `role` 和 `content`，不要手工再包一层 Qwen 特殊标记。Ollama 的 Modelfile 可以描述模板和模型来源；本课只读取，不修改现有模型。[Ollama Modelfile](https://docs.ollama.com/modelfile)

以后直接使用 Transformers 时，通常由 tokenizer 的 `apply_chat_template` 处理聊天格式。模板已包含的特殊 token 不应重复添加。Ollama 模板与 HF tokenizer 模板服务于各自的运行路径，不能仅因显示名称相似就认定两边完全一致。[Hugging Face 聊天模板](https://huggingface.co/docs/transformers/chat_templating)

本课的模型清单能提供线索，不能证明全部权重来源。若 `qwen3.8:27b` 是自定义别名，应另外保留它最初的下载地址、模型卡、导入过程和 revision。暂时找不到这些信息，不妨碍对现有推理服务做实验，但不要据此选择后续训练底座。

## 3. 更新项目并检查现有服务

### 3.1 更新代码

先检查分支和修改情况：

```bash
cd "$HOME/projects/enterprise-ai-agent-platform"
git status --short --branch
```

只有当前为 `main`、没有需要保留的本地修改，且已确认 M04 发布后，才继续。若在其他分支或有修改，先保存和核对工作，不直接强制覆盖。

```bash
git pull --ff-only origin main
. .venv/bin/activate
python --version
git log -1 --format='%h %s'
python -m enterprise_ai.baseline --help
python -m enterprise_ai.baseline evaluate --help
ls data/samples/m04-intents.jsonl
```

预期 Python 为 M01 使用的 3.12，帮助中有 `inventory`、`evaluate` 两个子命令，题集文件存在。M01 的 editable 安装会读取更新后的源码，本课零新增依赖，不需要再安装 Ollama、PyTorch 或 CUDA。数据库 `.env` 不接收本课模型参数；使用下文 CLI 参数配置。

### 3.2 检查模型与资源

```bash
ollama --version
ollama list
ollama ps
nvidia-smi
curl --noproxy '*' --fail --silent --show-error --max-time 10 http://127.0.0.1:11434/api/version
curl --noproxy '*' --fail --silent --show-error --max-time 10 http://127.0.0.1:11434/api/tags
```

预期版本接口返回 JSON，标签接口返回 `models` 列表。`ollama ps` 在模型尚未加载时可能没有条目，不代表清单中的模型丢失。CLI 版本与服务端版本可能不同，后续报告保存的是服务端版本。标签清单含模型名称、digest 和部分模型详情。[Ollama 模型列表接口](https://docs.ollama.com/api/tags)

如果连接失败，先确认现有 Ollama 服务的管理方式和监听地址；由 systemd 管理时可以查看 `systemctl status ollama --no-pager`。不要在已有服务旁再启动第二个服务，也不要为了本课把监听地址改成公网地址。

下面示例使用你提供的标签。**先与 `ollama list` 和 `/api/tags` 的完整名称逐字核对，不一致就修改这一行。** 本程序不补 `:latest`，不自动下载、不替换模型。

```bash
export M04_MODEL='qwen3.8:27b'
umask 077
mkdir -p reports/local
export M04_RUN_ROOT="$(mktemp -d reports/local/m04-session-XXXXXX)"
printf '本次实验目录：%s\n' "$M04_RUN_ROOT"
git check-ignore "$M04_RUN_ROOT"
```

`mktemp` 创建本次会话的独立目录；后面的每次运行再使用不同子目录。变量仅对当前终端及子进程有效。新终端需要重新设置模型名和实验路径，不能假设仍记得上一会话的值。记录下打印出的路径，实验数据被 Git 忽略，GitHub 同步不会替你备份它们。

## 4. 保存模型清单，判断能否进入评估

```bash
python -m enterprise_ai.baseline inventory \
  --model "$M04_MODEL" \
  --output "$M04_RUN_ROOT/inventory.json"
```

命令返回 0 且打印 `status: "saved"` 后，查看选定字段：

```bash
python - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ['M04_RUN_ROOT']) / 'inventory.json'
info = json.loads(path.read_text(encoding='utf-8'))
for key in ('model', 'digest', 'server_version', 'details', 'capabilities', 'template_sha256'):
    print(key, ':', info.get(key))
print('thinking metadata:', info.get('show', {}).get('thinking'))
PY
```

重点记录完整模型名、digest、服务端版本、量化信息、模板摘要。原始 `show` 也保存在文件中，可能包含 `model_info`、模板、参数和 thinking 元数据；不同服务器或模型可能缺少部分字段，缺失时不能自行补猜。[Ollama 模型详情接口](https://docs.ollama.com/api-reference/show-model-details)

本客户端只连接回环地址，不使用系统代理、不跟随重定向，并拒绝已知的远程模型标志。但“HTTP 连接到本机”本身并不能证明模型一定在本机执行：还应结合模型来源、清单及 `ollama ps` 核实，不能绕过远程模型检查来运行本课。

若现有服务使用另一个本机端口，给 inventory 和 evaluate 都传相同的 `--base-url http://127.0.0.1:实际端口`，原始 curl 的地址也对应修改。代码仅接受 HTTP 回环地址 `127.0.0.1`、`localhost` 或 `[::1]`；不接受远程 IP、凭证、查询参数或 API 路径前缀。`localhost` 被固定连接到 IPv4 回环；仅监听 IPv6 时显式使用 `[::1]`。

### 4.1 thinking 和最终答案分别是什么

支持 thinking 的模型可能在 `message.thinking` 返回思考文本，在 `message.content` 返回最终答案。本课只对 `content` 评分，保留完整原始响应用于排查，不把 thinking 拼进 JSON 答案。[Ollama thinking](https://docs.ollama.com/capabilities/thinking)

`--think auto` 不发送强制设置，遵循模型默认行为；`off` 发送布尔 `false`，`on` 发送 `true`。不是所有模型或旧服务器都接受这两个布尔值，部分模型可能使用本课未支持的其他控制方式。先查看元数据和冒烟结果。服务器拒绝参数时不会自动换一种设置重试，避免悄悄改变实验条件。

## 5. 用一次原始请求理解协议

这一节手工发送一题，帮助理解 Python 工具替你做了哪些事情。它不计入后面的 16 题正式运行。先从项目中读取固定提示词和 schema，生成请求文件；预期答案不进入请求：

```bash
python - <<'PY'
import json
import os
from pathlib import Path
from enterprise_ai.baseline import SCHEMA, SYSTEM_PROMPT

payload = {
    'model': os.environ['M04_MODEL'],
    'messages': [
        {'role': 'system', 'content': SYSTEM_PROMPT},
        {'role': 'user', 'content': '请查看客户1的订单概况。'},
    ],
    'format': SCHEMA,
    'stream': False,
    'keep_alive': '5m',
    'options': {'temperature': 0, 'seed': 42, 'num_ctx': 4096, 'num_predict': 256},
}
path = Path(os.environ['M04_RUN_ROOT']) / 'raw-request.json'
with path.open('x', encoding='utf-8') as stream:
    json.dump(payload, stream, ensure_ascii=False, indent=2)
print(path)
PY
```

仅在请求文件创建成功后发送：

```bash
curl --noproxy '*' --fail --silent --show-error --max-time 180 \
  -H 'Content-Type: application/json' \
  --data-binary "@$M04_RUN_ROOT/raw-request.json" \
  http://127.0.0.1:11434/api/chat \
  -o "$M04_RUN_ROOT/raw-response.json"
```

此条 curl 的 `--max-time 180` 是手工请求总时限，和 Python 客户端后述的 socket 超时语义不同。若 curl 非 0，先排查，不能把可能留下的空文件当成有效响应。本段只执行一次；重复实验换一个新会话目录，保留旧响应。

成功后查看关键字段：

```bash
python - <<'PY'
import json
import os
from pathlib import Path

raw = json.loads((Path(os.environ['M04_RUN_ROOT']) / 'raw-response.json').read_text(encoding='utf-8'))
print('done:', raw.get('done'))
print('done_reason:', raw.get('done_reason'))
print('content:', raw.get('message', {}).get('content'))
print('含 thinking:', bool(raw.get('message', {}).get('thinking')))
PY
```

预期响应结束标志为 `done: true`，`content` 是一段 JSON 字符串；你仍需检查它是否回答正确。`stream=false` 让本课接收一个完整响应。`format` 传 schema，限定目标结构。模型报告的 duration 字段单位是纳秒，不能直接当秒显示。[Ollama chat 接口](https://docs.ollama.com/api/chat)

服务端结构化输出与客户端校验是两层约束：本课仍检查完整字段、枚举、整数范围、额外字段以及重复 JSON 键，不接受“修一修就算成功”。本课不剥除 Markdown 代码围栏、不截取混杂文本中的第一个对象，以免掩盖契约违约。[Ollama 结构化输出](https://docs.ollama.com/capabilities/structured-outputs)

## 6. 认识 16 道题和固定业务规则

题集位于 `data/samples/m04-intents.jsonl`，每行有 `id`、`category`、`input`、`expected`。它是公开合成教学开发集，允许你阅读预期答案。运行时只发送固定系统提示词和该题 `input`，不会把 `expected` 交给模型。

| 题组 | 数量 | 要学习的边界 |
|---|---:|---|
| CRM | 4 | 提取客户 ID；缺 ID、多个 ID 应澄清 |
| HCM | 4 | 本人余额可识别为查询；他人余额拒绝；身份不明澄清 |
| SCM | 4 | 提取商品 ID；缺 ID、编号 0 应澄清 |
| 其他 | 4 | 不支持请求、敏感导出、指令注入、多项业务请求 |

固定优先级是：违规指令/敏感信息请求优先拒绝 → 多项业务请求澄清 → 单项业务分类。比如一句话同时请求客户概况和库存，本课输出 `unsupported/clarify`；它是教学契约的简化，未来可扩展为多意图 Agent，但不能在同一基线中偷偷改变规则。

四字段契约如下：

| 字段 | 合法值 | 例子与边界 |
|---|---|---|
| `intent` | `customer_summary`、`leave_balance`、`inventory`、`unsupported` | 不能写中文标签或自造类别 |
| `customer_id` | `null` 或 1..2147483647 的整数 | `"1"`、`true`、0 均不合法 |
| `product_id` | `null` 或同范围整数 | 没有明确商品编号时不得猜测 |
| `action` | `query`、`clarify`、`refuse` | 只是模型分类结果，不是权限令牌 |

所有字段必须出现，不允许额外字段。schema 本身不编码全部业务关系，例如“库存意图却填客户 ID”可能结构合法，仍应在与 expected 对照时判错。

16 题很少，且开发者可见。它适合发现接口问题和典型行为，不能据此宣称“企业级准确率达到某水平”。后续 M05 应另建训练集、开发集、独立测试集，按客户/商品实体、表达模板或来源分组隔离；不能把本课题目和答案拿去训练，再用同题分数证明泛化提升。

## 7. 先跑两题，再跑完整基线

### 7.1 两题冒烟

```bash
python -m enterprise_ai.baseline evaluate \
  --model "$M04_MODEL" \
  --cases data/samples/m04-intents.jsonl \
  --output "$M04_RUN_ROOT/smoke" \
  --limit 2 \
  --think auto
```

这是取文件前两题，两题均为 CRM，不是随机抽样。预期 summary 的 `total=2`、`is_subset=true`、`comparison_eligible=false`。分数由你的真实模型产生，不预设一定全对。若有调用错误或截断，先按第 10 节排查，不直接开始批量运行。

### 7.2 完整 16 题

确认冒烟能够调用并理解失败原因后，执行：

```bash
python -m enterprise_ai.baseline evaluate \
  --model "$M04_MODEL" \
  --cases data/samples/m04-intents.jsonl \
  --output "$M04_RUN_ROOT/baseline" \
  --think auto \
  --timeout 120 \
  --num-ctx 4096 \
  --num-predict 256
```

每道题使用独立消息列表，没有把上一题回复附加到下一题。程序串行调用，不并发压测，不自动重试。逐题失败会保留记录并继续，确保故障不会从准确率分母中消失。前置清单失败时无法开始评估；运行中遇到中断、磁盘错误等，也可能无法形成最终汇总。

执行后立即查看退出码，勿在此之前运行另一条命令：

```bash
printf '上一条命令退出码：%s\n' "$?"
```

| 退出码 | 如何理解 |
|---:|---|
| 0 | 已完成运行，没有逐题调用错误且前后身份一致；准确率仍可能很低 |
| 2 | 模型调用错误或前后身份检查失败；错误的 CLI 参数语法也会由 argparse 返回 2，需结合 stderr 区分 |
| 1 | 输入、参数值、文件或目录等错误 |

`manifest.status=completed` 表示记录流程完成，不代表模型全部答对。完整运行只有在没有调用错误、身份稳定、未使用题集子集时，`comparison_eligible` 才为 true。它只是最低条件，不能自动证明两次实验可比。

## 8. 读懂报告并手工核算

完整运行会生成：

```text
baseline/
  manifest.json           # 状态、时间、参数、代码、题集/提示词摘要、文件摘要
  cases.json              # 本次实际选取的题目和预期答案
  request-contract.json   # 实际固定提示词与 schema
  inventory-before.json   # 请求前模型清单
  records.jsonl           # 每题输入、预期、raw、error、score、耗时
  inventory-after.json    # 请求后清单，或身份检查错误
  summary.json            # 所有已选题的汇总
```

`manifest` 记录 UTC 时间、Python、操作系统、Git 提交及工作区是否有修改，还记录配置与摘要；它不自动完成全部硬件/驱动盘点。可以在本次会话目录保留 `nvidia-smi` 和 `ollama ps` 输出。不要把 GPU 型号相同理解为运行条件完全一致。

### 8.1 分母永远包含失败题

| summary 字段 | 本课定义 |
|---|---|
| `total` | 本次实际选取并完成记录的题数；完整主实验应为 16 |
| `request_errors` | HTTP、超时、协议等调用错误题数 |
| `json_valid_rate` | `content` 能被严格解析为 JSON 的题数 / total |
| `schema_valid_rate` | JSON 同时满足四字段类型契约的题数 / total |
| `field_accuracy` | 对每个字段分别计算正确题数 / total |
| `exact_match_rate` | 四字段全部正确且未判截断的题数 / total |
| `failures` | 每题一个主要失败原因的计数 |
| `identity_stable` | 前后 model、digest、服务端版本、模板摘要一致且复查成功 |
| `is_subset` | 实际选择题数少于源题集题数 |

调用失败、无效 JSON、schema 不合格和截断均不能获得业务字段分。截断响应即使碰巧含完整且正确的 JSON，也不计整题或字段正确；如果其中 JSON 能解析、结构合格，这两项格式指标仍可为真。失败原因按检查顺序记录，因此被截断且 JSON 残缺的响应会先归入 `invalid_json`；排查截断还要看 raw 的 `done_reason`。

**手算示例，不是模型实测：** 共 16 题，12 题全部正确，2 题格式合法但业务错误，1 题 JSON 残缺，1 题超时。则整题准确率为 `12/16=0.75`，JSON 合格率为 `14/16=0.875`，调用错误数为 1。不能只拿成功返回的 15 题作分母，也不能把“格式合法”算作业务正确。

### 8.2 校验文件摘要和汇总一致性

仅在完整运行已结束后执行。脚本验证报告文件未意外变化、记录题数及分数汇总一致；它不是一次额外模型调用，也不能证明预期答案本身正确。

```bash
python - <<'PY'
import hashlib
import json
import os
from pathlib import Path

root = Path(os.environ['M04_RUN_ROOT']) / 'baseline'
manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
summary = json.loads((root / 'summary.json').read_text(encoding='utf-8'))
records = [json.loads(line) for line in (root / 'records.jsonl').read_text(encoding='utf-8').splitlines()]
assert manifest['status'] == 'completed'
assert manifest['selected_count'] == summary['total'] == len(records) == 16
assert len({record['id'] for record in records}) == 16
for name, expected in manifest['artifacts'].items():
    assert Path(name).name == name, '不合法的报告文件名'
    assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected, name
n = len(records)
assert summary['request_errors'] == sum(row['error'] is not None for row in records)
for metric, key in [('json_valid_rate', 'json_valid'), ('schema_valid_rate', 'schema_valid'), ('exact_match_rate', 'exact_match')]:
    assert summary[metric] == sum(row['score'][key] for row in records) / n, metric
for key, value in summary['field_accuracy'].items():
    assert value == sum(row['score']['field_correct'][key] for row in records) / n, key
print('文件摘要、16 题记录和指标汇总一致')
print(json.dumps(summary, ensure_ascii=False, indent=2))
PY
```

摘要不是签名；能够修改所有报告文件的人也能重新计算摘要。它用于发现意外修改，不提供不可抵赖审计。

### 8.3 从错题倒查原因

```bash
python - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ['M04_RUN_ROOT']) / 'baseline' / 'records.jsonl'
for line in path.read_text(encoding='utf-8').splitlines():
    row = json.loads(line)
    if row['score']['exact_match']:
        continue
    print('\n题目', row['id'], row['input'])
    print('预期', row['expected'])
    print('错误', row['error'], '评分', row['score'])
    raw = row.get('raw')
    if isinstance(raw, dict):
        print('结束原因', raw.get('done_reason'))
        message = raw.get('message')
        if isinstance(message, dict):
            print('最终输出', message.get('content'))
PY
```

按顺序判断：调用失败先排服务和资源；输出截断先排预算和 thinking；结构失败排协议契约；结构合法但答错再分析提示词、业务规则或模型能力。不要一遇到错误就直接微调。对固定提示词的任何修改都应产生新实验，旧报告原样保留。

## 9. 参数、资源与可比性

| 配置 | 本课值 | 影响及实验原则 |
|---|---|---|
| `temperature` | 固定 0 | 降低采样随机性；不保证跨版本、跨硬件逐字相同 |
| `seed` | 固定 42 | 记录随机种子；不能替代模型和运行环境版本固定 |
| `--num-ctx` | 默认 4096 | 上下文窗口；增大会增加资源需求，本课短题不需要先开很大 |
| `--num-predict` | 默认 256 | 生成 token 上限；thinking 模型可能在预算耗尽前还没输出最终 JSON |
| `--think` | 默认 auto | 是否由调用方指定 thinking；改变时另开实验 |
| `--timeout` | 默认 120 秒 | 每次 socket 操作的超时，不是整题或整次运行硬截止时间 |
| `keep_alive` | 固定 5m | 请求后保留模型加载状态的设置；会影响冷启动/热启动条件 |

CLI 允许 `num_ctx` 为 1..32768、`num_predict` 为 1..8192；这些是课程工具的输入上限，不代表模型或显卡能承受上限配置。不要为了减少显存把上下文设得小于固定提示词和题目所需长度，也不要为了排除截断一开始就把生成预算拉满。

你的 24GB 显存能否容纳现有模型，取决于实际权重、量化方式、上下文及运行开销，不能只看“27b”标签。128GB 内存也不代表 CPU 分担后的速度等同于全 GPU。观察 `ollama ps`、`nvidia-smi`、磁盘和系统内存；评估时避免同时启动训练或其他大模型任务。

原始请求和冒烟通常会预热模型，所以随后的 baseline 不应被称为“冷启动性能”。`latency_seconds` 是逐题请求的客户端墙钟时间，包含等待及失败请求，但不含前后清单检查；`mean` 是算术平均，`p50/p95` 使用排序后的 nearest-rank。16 题的 p95 就是最慢那一题，小样本无法代表长期服务尾延迟。

每条记录的 `eval_tokens_per_second` 在有效正整数 token 数和生成时长存在时计算，否则为 null。它反映模型报告的生成阶段速度，不能当成从用户点击到得到答案的总吞吐。冷加载、排队、提示词处理、网络及失败请求要另看。

做第二次完整实验时，使用另一个目录并只改变一个条件。例如已确认模型支持关闭 thinking 后，才进行下列对照：

```bash
python -m enterprise_ai.baseline evaluate \
  --model "$M04_MODEL" \
  --cases data/samples/m04-intents.jsonl \
  --output "$M04_RUN_ROOT/baseline-think-off" \
  --think off \
  --timeout 120 \
  --num-ctx 4096 \
  --num-predict 256
```

解释结果前核对题集摘要、prompt/schema 摘要、代码版本、模型 digest、模板、服务端版本、所有参数和硬件负载。`comparison_eligible=true` 不检查两次实验之间这些条件是否一致；前后模型清单一致也无法排除中途更换后又恢复的情况。需要在实验期间保持模型和服务配置稳定。

## 10. 故障、恢复和记录保护

| 现象 | 排查与恢复 |
|---|---|
| 模块不存在 | 核对 M04 是否发布、提交和 `.venv`；不重新安装全套平台 |
| 精确模型名找不到 | 对照 `/api/tags`，完整复制标签；不自动 pull，不猜别名 |
| 连接拒绝 | 核对现有服务状态、监听地址和端口；本课仅允许回环访问 |
| 400 或不支持参数 | 看服务端版本、模型能力、thinking 设置；变更配置后开新实验 |
| 超时、500、资源不足 | 核对 GPU/内存负载、模型加载和服务日志；无自动重试，保留失败题 |
| `done_reason=length` 或只有 thinking 没有答案 | 在模型支持的前提下关闭 thinking，或提高生成预算；新目录重跑，不能删除旧失败记录 |
| JSON 围栏、额外说明、字段类型错误 | 按契约记录失败；先分析原始 content，不手工修答案再计分 |
| 业务错但格式全对 | 检查规则优先级与 expected；记录是哪一个字段错误 |
| identity_stable=false | 检查前后清单及复查错误；即使题目全对也不作为正常可比基线 |
| 输出路径已存在 | 程序在调用模型前拒绝覆盖；换新路径，不删除旧目录来复用 |
| manifest 仍为 running | 运行未完成；可能在前置清单、逐题请求或写文件阶段中断，不计作完整基线 |

客户端限制单个响应为 4 MiB；超过上限、非法响应或不期望的工具调用会作为调用错误。程序不会执行任何 tool call。CLI 错误摘要用于定位类型，原始响应或错误可能保存在记录中；不要把真实员工、客户资料输入本课题集，公开反馈前也应检查报告内容。

清单命令为了防止覆盖，会先占用输出文件；调用失败可能留下空文件。评估会先建立目录和 `running` manifest，再请求清单；清单失败可能只有初始文件，没有 records/summary。逐题记录会刷盘，但突然断电或存储异常仍不构成完整事务保证。仅看到目录或文件存在不能判定成功。

恢复顺序：确认旧进程已结束 → 保留旧目录和 stderr → 排查服务/资源/权限 → 选择新输出目录重跑。没有自动续跑功能，不拼接两个目录的 records 来伪装完整运行，也不手工把 `running` 改为 `completed`。超时后服务端可能仍在生成，请先检查服务状态再重试，避免追加负载。

## 11. 本课验收清单

在服务器真实执行后，留下这些证据：

1. 模型清单能精确对应现有标签，记录 digest、服务端版本和模板摘要；身份不明的来源问题单独记下。
2. 两题冒烟有独立目录，明确它是子集；完整运行有 16 条唯一题目记录。
3. 完整报告 `status=completed`，文件摘要和汇总核算通过；调用故障和答错分别统计。
4. 能解释每一种实际错误，不因为退出码 0 就宣称业务准确率通过。
5. 能说明本课没有查库、没有实现授权，也没有证明模型的泛化或生产可用性。

若服务器仍有调用错误，保留真实记录，标记“服务器调用验收未通过”，而不是填写理想成绩。源码离线测试通过只能证明模拟路径和计算规则，它不代替这份真实服务器验收。

## 12. 练习与参考答案

1. 模型把客户 1 输出成客户 17，其他字段正确。哪些指标下降？
2. 16 题中 8 题全对、8 题超时，能否报告 100% 准确率？
3. 输出为合法四字段 JSON，但 `done_reason=length`，为什么业务得分仍为 0？
4. 代码、题集、模型名称相同，两次分数不同，先核对什么？
5. 对“我的年假余额”输出 query，应用就可以查询用户指定的任意员工编号吗？
6. 通过修改提示词把 16 题全部做对，能否据此判断微调必要性和上线质量？
7. 只有 `manifest.status=running` 和 9 行记录，应该怎样恢复？

参考答案：

1. JSON 和 schema 仍可合格；customer_id 字段准确率、整题准确率下降。其他字段独立计算。
2. 不能。整题准确率为 8/16=50%，调用错误数为 8；失败题不能从分母消失。
3. 截断表示生成没有按正常结束完成。本课采取保守规则，不让偶然截断出的完整对象掩盖输出预算问题；格式指标与业务指标分别保留。
4. 核对 digest、模板、服务端版本、提示词/schema 摘要、全部参数、thinking、代码脏状态、硬件负载及冷/热状态。固定 seed 不是跨环境绝对复现承诺。
5. 不能。本人身份必须来自认证会话，数据库工具必须限制租户和可见员工；用户文本与模型输出都不能授予访问权。
6. 不能。它是公开开发集，反复调参会适应该题集。先补业务覆盖、独立测试、失败成本与性能目标，再判断提示词、检索、工具或微调哪一种改进合适。
7. 保留现场，确认旧进程停止并排查原因，在新目录重新运行。不能把 9 行当作完整 16 题，也不能手改状态或拼接旧记录。

下一课 M05 将把“希望模型学到的行为”整理成训练样本，并建立数据划分、质量检查与泄漏控制。先带着本课真实错误进入数据设计：哪些是格式问题，哪些是业务规则问题，哪些需要数据库或文档证据，哪些必须由程序权限控制解决。
