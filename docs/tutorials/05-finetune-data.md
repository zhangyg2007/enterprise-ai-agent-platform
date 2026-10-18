# 第 5 课：制作可追溯的微调数据，先验收再训练

本课对应 M05。你将生成 480 条中文业务意图样本，完成标注规则检查、分组切分、Alpaca/ShareGPT 转换、泄漏校验和人工抽样。交付物是一个可复现的数据包、一份人工复核记录和数据卡；本课没有模型训练成绩。

**执行位置：本文所有命令均在 Ubuntu 24.04 服务器终端执行。** 复用 M01 的项目目录与 Python 环境，只使用标准库；本课不安装 LLaMA-Factory、PyTorch、CUDA，不下载权重，不启动数据库或 Ollama。M06 才核对训练底座、固定工具版本、加载 tokenizer 并进行 GPU 训练实验。

**版本前提：先取得包含 M04 和 M05 的项目代码。** 本文存在不等于相关提交已发布。先核对发布状态和代码版本，不能在旧仓库里直接运行本课命令。命令逐条执行；除第 9 节故意制造的失败外，非零退出就停止排查。

## 1. 微调学什么，业务事实放在哪里

本课训练目标是“把请求转换成固定四字段 JSON”，不是记住企业数据库。例如：

```json
{"intent":"inventory","customer_id":null,"product_id":2001,"action":"query"}
```

这表示请求涉及商品 2001 的库存。它没有给出库存量，没有证明商品存在，也没有批准一次数据库查询。库存、订单金额、假期余额会变化，应由后续应用经过认证和权限检查后实时查询。把这些值写进训练答案，容易产生过期回答，也会混淆模型与业务系统的职责。

本课所有编号都是新生成的合成标识，不是 M02 数据库中的真实记录。不能把生成数据里的客户 1001 或商品 2001 直接拿去 M02 查询，并据查询为空认定标注错误。

```mermaid
flowchart LR
    A[规则与合成场景组] --> B[先分配场景组到三个分区]
    B --> C[各分区专用表达模板]
    C --> D[480 条 canonical 样本]
    D --> E[结构与泄漏校验]
    E --> F[两种等价格式和固定验证集]
    F --> G[人工复核与数据卡]
    G --> H[M06 tokenizer 与训练预览]
```

## 2. 检查代码与目录

先查看当前状态：

```bash
cd "$HOME/projects/enterprise-ai-agent-platform"
git status --short --branch
```

只有确认本批代码已发布到 `main`、当前分支为 `main` 且没有需要保留的本地修改时，才执行下面的更新。若课程代码由其他已确认的提交交付，先按交付说明取得该提交，不擅自切换或覆盖本地工作。

```bash
git pull --ff-only origin main
. .venv/bin/activate
python --version
git log -1 --format='%h %s'
python -m enterprise_ai.baseline --help
python -m enterprise_ai.finetune_data --help
python -m enterprise_ai.finetune_data generate --help
python -m enterprise_ai.finetune_data validate --help
ls data/samples/m04-intents.jsonl
```

预期 Python 为 M01 使用的 3.12，M04 有 `evaluate`，M05 有 `generate` 和 `validate`，保护题集存在。若提示 `No module named enterprise_ai.finetune_data`，先检查是否进入正确项目、激活正确环境、取得正确代码；不要通过安装同名第三方包来补救。M01 的 editable 安装会使用更新后的项目源码。

## 3. 先写清楚标注规范

沿用 M04 的 `SYSTEM_PROMPT` 和四字段契约，所有答案必须恰好包含 `intent`、`customer_id`、`product_id`、`action`。两个编号字段只能是 `null` 或 1..2147483647 的整数；字符串 `"1001"`、布尔值 `true`、0 都不是合法编号。

| 请求类型 | intent | customer_id | product_id | action |
|---|---|---|---|---|
| 明确一个客户编号的概况查询 | customer_summary | 提取的编号 | null | query |
| 客户概况但缺编号 | customer_summary | null | null | clarify |
| 本人的假期余额 | leave_balance | null | null | query |
| 他人的假期余额 | leave_balance | null | null | refuse |
| 明确一个商品编号的库存查询 | inventory | null | 提取的编号 | query |
| 库存请求但缺商品编号 | inventory | null | null | clarify |
| 敏感信息导出或违规指令 | unsupported | null | null | refuse |
| 同时请求两项业务 | unsupported | null | null | clarify |

按 M04 优先级判定：敏感或违规请求优先拒绝，多业务请求其次澄清，再判断单项业务。员工编号、工单编号可以出现在输入里，但不是本课输出字段。不要给答案添加 `employee_id`、余额、姓名、解释或 Markdown 围栏。

需要区分两种“不确定”：

- **请求缺少业务信息**：例如“帮我查客户情况”，意图明确、编号缺失，应标 `customer_summary/clarify`，不能猜一个 ID；“假期余额还有多少”无法判断本人或他人时应标 `leave_balance/clarify`。
- **标注员无法确定规则或语义**：例如含糊的缩写、冲突指令或未定义业务，应在人工复核记录中标记 `needs_review`、写明争议，由规则负责人裁定。不要把标注员的犹豫一律变成模型的 `clarify` 标签。

身份由可信应用会话提供。“我的假期”对应的 `query` 只是分类标签；文本中自称管理员不能授予权限。真实数据进入后续项目时，必须有来源授权、脱敏和复核。本课不把薪资、密码或个人信息作为训练答案；敏感请求的目标答案只有拒绝 JSON。

这 8 类只是教学覆盖范围，并不覆盖 M04 全部边界或真实业务长尾。例如多客户编号、无效编号、身份不明和多种攻击表达还需要后续专门评估，不能由“480 条”推导为“覆盖充分”。

## 4. 为什么先分组，再生成不同表达

每个场景组绑定客户、商品、员工和工单四种合成实体。30 组，每组 8 类请求，每类 2 个表达，共 `30 × 8 × 2 = 480` 条。

| 分区 | 场景组数 | 样本数 | 每类样本数 | 用途 |
|---|---:|---:|---:|---|
| train | 21 | 336 | 42 | 后续更新模型参数 |
| validation | 6 | 96 | 12 | 后续调参、选 checkpoint、发现开发问题 |
| test | 3 | 48 | 6 | 冻结方案后的独立检查 |

生成器按 seed 与 group_id 的 SHA256 排序分配组。然后为各分区使用分别编写的表达模板，相关实体不会跨分区。不能把同一客户的两种改写逐行随机切开，也不能仅换编号就声称使用了独立测试表达。

校验器检查组、实体、规范化文本和去数字后的模板指纹边界，并检查与 M04 保护题集的输入和实体重合。M04 文件只用于保护与排除，不作为 M05 的训练来源。固定 seed 和相同代码、保护题集应得到相同内容；换 seed 不代表获得了新的真实业务来源。

这些分区仍共享业务语义和合成规则。文本指纹只能发现某些重合，不能自动证明所有近义表达、业务来源或语义都独立。因此，M05 test 是这套合成实验中的保留集，不能被宣传为真实企业泛化证明。

## 5. 生成数据包并验收

输出目录必须不存在，不要预先创建 `m05-v1`。生成器拒绝覆盖已有目录。

```bash
umask 077
python -m enterprise_ai.finetune_data generate \
  --output data/generated/m05-v1 \
  --seed 42 \
  --baseline-cases data/samples/m04-intents.jsonl
```

仅在生成成功后执行：

```bash
python -m enterprise_ai.finetune_data validate \
  data/generated/m05-v1 \
  --baseline-cases data/samples/m04-intents.jsonl
```

预期两条命令均返回 0，数量为 train 336、validation 96、test 48。查看实际输出，不把此处的约定数字当作运行证据。再次生成时使用新目录，例如 `m05-v2`；不要清空旧数据来掩盖失败。

文件布局：

```text
data/generated/m05-v1/
  samples.jsonl                 # canonical：480 条，含来源追踪用的组/实体/模板字段
  train-alpaca.json              # 336 条
  validation-alpaca.json         # 96 条
  test-alpaca.json               # 48 条，封存，不注册训练
  train-sharegpt.json            # 同一批 336 条的另一种表示
  validation-sharegpt.json       # 同一批 96 条的另一种表示
  test-sharegpt.json             # 同一批 48 条的另一种表示，封存
  dataset_info.json              # 只注册 train/validation 的两种表示，共 4 项
  test-cases.jsonl               # 48 题，兼容 M04 评估器
  manual-review-template.csv     # 8 类 × 3 分区，各抽 1 条，共 24 条
  manifest.json                 # 状态、版本、seed、来源和文件摘要
```

`manifest.status` 从 `building` 变为 `complete`。`complete` 表示生成过程完成；仍应执行独立的 `validate` 并人工复核。中断留下的 `building` 目录保留用于排查，不用于训练。manifest 记录生成器版本、seed、M04 题集字节摘要、系统提示词摘要及产物摘要，不加入实时生成时间，以免破坏相同输入的字节复现。另行在实验记录中记录运行日期和 Git 提交。

摘要用于发现意外修改，不是数字签名；能同时改动文件和摘要的人仍可伪造一套材料。自动验收还会核对 schema、类别与标签、两格式等价、注册映射以及测试集未被注册，不能只看某个哈希值相等。

## 6. 查看 canonical 和两种格式

canonical 每行包含 `sample_id`、`group_id`、`split`、`category`、`template_id`、`entities`、`prompt`、`answer`。其中 `answer` 是 JSON 对象；导出格式中的答案是 JSON **字符串**，因为它是模型需要生成的文本。来源说明在 manifest 和[数据卡](../data-cards/m05-intents.md)中。

只查看训练区的一条示例，避免为了调试不断翻阅 test：

```bash
python - <<'PY'
import json
from pathlib import Path

root = Path('data/generated/m05-v1')
samples = [json.loads(line) for line in (root / 'samples.jsonl').read_text(encoding='utf-8').splitlines()]
row = next(item for item in samples if item['split'] == 'train')
print(json.dumps(row, ensure_ascii=False, indent=2))
for name in ('train-alpaca.json', 'train-sharegpt.json'):
    records = json.loads((root / name).read_text(encoding='utf-8'))
    print(name, '条数:', len(records))
    print(json.dumps(records[0], ensure_ascii=False, indent=2))
PY
```

Alpaca 的 `instruction` 保存原问题，`input` 为空字符串，`output` 保存四字段 JSON 字符串，`system` 保存 M04 完整固定提示词。ShareGPT 的 `system` 相同，`conversations` 只有两条消息：`from=human` 的 `value` 是原问题，`from=gpt` 的 `value` 是相同答案文本。这是一轮问答、两条消息，没有 history、工具结果、CoT 或多模态数据。

不要把两种格式同时加入训练：它们是同一数据的替代表示，同时使用会重复加权，样本数看起来翻倍，信息却没有增加。也不要把期望答案塞进用户问题，否则模型会学到“复制已提供的答案”。

## 7. 理解 LLaMA-Factory 注册，不在本课启动训练

`dataset_info.json` 用显式列映射连接格式与文件。Alpaca 将 prompt/query/response/system 映射到 instruction/input/output/system；ShareGPT 将 messages/system 映射到 conversations/system，并将 role/content/user/assistant 标签映射到 from/value/human/gpt。本课遵循官方数据格式，未实际运行 LLaMA-Factory 加载器。[官方数据格式说明](https://github.com/hiyouga/LLaMA-Factory/blob/main/data/README.md)

检查实际注册表：

```bash
python - <<'PY'
import json
from pathlib import Path

path = Path('data/generated/m05-v1/dataset_info.json')
info = json.loads(path.read_text(encoding='utf-8'))
expected = {'m05_train_alpaca', 'm05_validation_alpaca',
            'm05_train_sharegpt', 'm05_validation_sharegpt'}
assert set(info) == expected
print(json.dumps(info, ensure_ascii=False, indent=2))
PY
```

未来选 Alpaca 时，数据参数片段如下。它只是参数含义示例，不是可直接启动训练的完整配置：

```yaml
dataset_dir: /absolute/path/to/data/generated/m05-v1
dataset: m05_train_alpaca
eval_dataset: m05_validation_alpaca
val_size: 0
```

选 ShareGPT 时将两个名称换成 `m05_train_sharegpt`、`m05_validation_sharegpt`，不要把两套名称拼在一起。`dataset_dir` 需要换成服务器实际绝对路径。

使用固定 `eval_dataset`，不要再设置正数 `val_size` 随机切出验证集；当前上游也明确拒绝两者同时启用。M06 应在固定 revision 后重新检查参数兼容性。[官方 DataArguments](https://github.com/hiyouga/LLaMA-Factory/blob/main/src/llamafactory/hparams/data_args.py)

不要在本地 validation 文件的注册项中另加 `split: validation`：这个字段选择底层加载器分区，不是声明训练用途。本地文件沿用默认分区，再由 `eval_dataset` 指定验证用途。更换数据后不要复用旧的 `tokenized_path`，否则缓存可能使新数据配置不生效；M06 会检查实际加载样本数和缓存来源。[官方数据加载代码](https://github.com/hiyouga/LLaMA-Factory/blob/main/src/llamafactory/data/loader.py)

`test-cases.jsonl` 保持 M04 的 `id/category/input/expected` 结构，供冻结模型方案后用已有 `baseline evaluate --cases` 接口检查；本课不执行模型评估，不用这 48 题挑选提示词或 checkpoint。M04 的 16 题与 M05 的 48 题不是同一题集，不能直接把两个准确率的差当成微调收益。微调前后需要同一冻结题集和可比较的运行条件。

## 8. 人工抽样与字符长度检查

自动程序可以发现结构错误，不能证明每条自然语言与标注规则一致。模板作者也可能把错误复制 30 遍。生成器提供按类别和分区抽出的 24 条 CSV 模板；它是待填写表，不是已完成的审核报告。

将模板复制到独立报告目录，再填写副本：

```bash
mkdir -p reports/local
export M05_REVIEW_DIR="$(mktemp -d reports/local/m05-review-XXXXXX)"
cp data/generated/m05-v1/manual-review-template.csv "$M05_REVIEW_DIR/manual-review.csv"
git log -1 --format='%H' > "$M05_REVIEW_DIR/code-revision.txt"
printf '复核目录：%s\n' "$M05_REVIEW_DIR"
```

用文本编辑器或能保留 UTF-8/CSV 引号的表格软件打开副本。逐行填写 `reviewer`、`decision`、`note`；建议 decision 使用 `accept`、`reject` 或 `needs_review`。核对输入语义、编号、优先级、四字段答案及拒绝边界。发现问题时写明 sample_id、理由和裁定方式；不要修改生成包中的模板，否则摘要会变化。

24 条全部可接受仅表示完成这次抽样。发现任何系统性问题，应修正规则或模板、扩大受影响类别的复核，再以新版本重新生成整包。不能只改一个导出文件，因为 canonical、两种格式、test-cases 和摘要必须一致。报告应注明复核范围、未决问题和最终结论；保留集的标注审查可以做，但不要据此反复针对其表达调模型。

以下只统计训练区字符数，不接触 tokenizer：

```bash
python - <<'PY'
import json
from pathlib import Path

rows = json.loads(Path('data/generated/m05-v1/train-alpaca.json').read_text(encoding='utf-8'))
sizes = [len(row['system']) + len(row['instruction']) + len(row['input']) + len(row['output']) for row in rows]
print('训练样本数:', len(sizes))
print('总文本字符数 min/max:', min(sizes), max(sizes))
PY
```

字符数不等于 token 数，中文、数字、标点和特殊标记的编码长度都受 tokenizer 影响。M06 要用目标权重配套 tokenizer 和聊天模板统计真实 token 长度，检查 system、用户输入、答案和结束标记是否被截断，再决定 `cutoff_len`。本课不能根据字符上限宣称“4096 token 一定足够”。

## 9. 做一次失败实验：复制样本不等于独立测试

只在独立副本中操作。下面把训练题的输入复制到一条测试题，保留原始包用于恢复和对照；它故意制造文本重合及导出不一致。

```bash
export M05_BAD_PARENT="$(mktemp -d reports/local/m05-leakage-demo-XXXXXX)"
cp -R data/generated/m05-v1 "$M05_BAD_PARENT/bundle"
python - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ['M05_BAD_PARENT']) / 'bundle' / 'samples.jsonl'
rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
train = next(row for row in rows if row['split'] == 'train')
test = next(row for row in rows if row['split'] == 'test')
test['prompt'] = train['prompt']
path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')
print('仅修改演示副本:', path)
PY
python -m enterprise_ai.finetune_data validate \
  "$M05_BAD_PARENT/bundle" \
  --baseline-cases data/samples/m04-intents.jsonl
```

立即记录上一条命令退出码：

```bash
printf '失败实验退出码：%s\n' "$?"
```

预期非零，记录实际错误消息。校验可能先发现文件摘要不符、文本重合或样本关系错误，取决于检查顺序；此实验只证明被修改的包遭到拒绝，不能仅据这次错误证明每一道泄漏检查都已独立触发。不要重写摘要去“修复”这份坏数据，更不要使用它训练。

重新验证原始包，预期仍可通过：

```bash
python -m enterprise_ai.finetune_data validate \
  data/generated/m05-v1 \
  --baseline-cases data/samples/m04-intents.jsonl
```

## 10. 验收记录与常见问题

将生成及校验的实际终端结果、Git 提交、manifest、人工报告位置和未决问题放入自己的实验记录。`data/generated/` 与 `reports/local/` 被 Git 忽略；提交教程不会替你同步这些本地文件，应另行备份。

| 现象 | 检查或处理 |
|---|---|
| 输出目录已存在 | 查看旧目录用途，换一个未存在的新目录；不覆盖 |
| manifest 仍为 building | 保留失败现场，修正原因后用新目录生成 |
| M04 摘要不匹配 | 确认使用生成时同一份保护题集；不改摘要绕过 |
| entity/group/template/text 跨分区 | 修正生成规则或分组；单改 split 名称不能解决关联关系 |
| JSON 或标签错误 | 按 M04 四字段和优先级核对；不自动补字段掩盖错误 |
| 手改导出后校验失败 | 从修正后的 canonical/生成规则重新生成完整新版本 |
| 自动验收通过但语义有误 | 人工复核拒绝并追溯模板；自动通过不能替代裁定 |

本课验收条件是：480 条及 336/96/48 切分正确；两格式等价；四项注册没有 test；原始包 validate 成功；副本失败实验被拒绝；人工抽样表已填写、未决问题被明确记录；能够解释数据来源和限制。未完成的步骤如实注明，尤其不要把“生成了抽样表”写成“已人工审核”。

此校验器只验证本课程生成器的数据包，不是任意企业数据的通用导入器。直接改标签、模板或注册表，即使重新计算摘要也会因不符合生成规则而被拒绝。需要新规则时应更新生成器版本、测试和数据卡，输出到新目录。`human_review_complete` 固定为 false：工具不读取人工报告，也不会替人宣告审核完成；实际人工结论记录在独立报告中。

## 11. 练习与参考答案

1. 将“查客户 1001”放进 train，把“客户 1001 的情况呢”放进 test，是否足够独立？
2. 将两种格式的训练文件一起用于训练，会得到 672 条独立知识吗？
3. “请给我同事 3001 的假期余额”应该如何标注？
4. 本课固定 validation 后，能否再设置 `val_size: 0.1`？
5. 自动校验通过，24 条抽样没有填结论，能否写“数据审核完成”？
6. 某训练样本只有 800 字符，能否断言不会被 1024 token 的配置截断？

参考答案：

1. 不足。实体相同、可能来源同一场景；应先按相关实体组切分，并检查模板/文本边界。即使这些检查通过，也不自动证明语义泛化。
2. 不能。它们是 336 条相同样本的两种表示，合并会重复加权。选择其中一种。
3. `{"intent":"leave_balance","customer_id":null,"product_id":null,"action":"refuse"}`。员工 ID 不写进客户或商品字段。
4. 不能。保留 `eval_dataset` 和 `val_size: 0`，使用已经隔离好的验证集。
5. 不能。自动验收与人工标注审核是两个检查，必须填报告并处理或明确记录未决项。
6. 不能。必须使用真实 tokenizer 和聊天模板，连同 system、答案及特殊标记统计完整序列。

继续 M06 前，保留数据卡、数据版本、摘要和人工审核结论。后续训练首先需要确认目标 HF 权重的来源、revision、许可、tokenizer 与模板，不能仅凭 Ollama 标签名判断兼容性。
