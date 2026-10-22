# 第8课：企业文档工程——让知识有来源、版本和可见范围

本课目标：把CRM/HCM/SCM合成文档变成可追溯的chunk，解释每块从哪里来、谁可见、何时有效，并亲自观察更新、撤权和删除。无需GPU、模型、PostgreSQL或完成微调。M09将在这些约束上接入embedding与检索；本课还不是RAG问答系统。

执行位置：**Ubuntu服务器、包含M08文件的仓库根目录**。Windows预置环境的验证见[记录](../validation/2026-10-21-m08.md)，不能代替Ubuntu验收。先阅读[数据卡](../data-cards/m08-documents.md)；所有企业规则都是虚构教学内容。

## 1. 业务问题与原理

假设销售政策从“销售可见”变成“员工演示角色可见”。如果只追加新块，旧块仍可能被检索，权限就失效了；如果按文件名覆盖，却不保留版本，又无法解释昨天的回答引用了什么。

本章采用三层状态：`versions`保存不可变版本元数据，`chunks`保存每个版本的文本块，`heads`只指向当前版本。更新和显式delete在一个SQLite事务中移动head。历史仍在，但普通导出只看当前head；墓碑没有可见文本。它与M03状态库是两个独立SQLite文件，不修改业务数据库。

| 概念 | 本课含义 | 常见误解 |
| --- | --- | --- |
| document_id | 租户内稳定ID，复合键为tenant_id+document_id | 文件名或内容hash就是永久ID |
| version | 正整数，内容、权限或处理配置变化时提高 | 改ACL不影响文本，所以不必更新 |
| source_sha256 | 实际读取源字节的摘要 | 摘要等同于来源认证或数字签名 |
| 结构块 | Markdown标题/段落，PDF页，DOCX段落/表格行 | 所有格式都有可靠物理页号 |
| chunk | 结构块内有字符坐标的小片段 | 400字符一定等于400 token |
| overlap | 相邻块重叠的字符数，默认40 | 重叠越大质量必然越好 |
| ACL | 允许的角色列表；演示采用角色交集 | 自报roles就等于通过身份认证 |
| effective_from/to | 起日包含、止日不包含，to=null无截止日 | 提前导入未来版本会自动回退旧版 |

本章未来版本成为head后，生效日前导出为空，不自动选择历史版本。它便于解释当前状态，但不适合直接模拟所有企业政策的预发布机制；真实业务需要额外设计计划发布。

## 2. 准备独立文档环境

使用Python 3.11–3.13；Ubuntu 24.04的Python 3.12可用作候选环境，实际执行结果仍需记录。不要在M06训练环境里改变依赖。以下虚拟环境路径首次使用；已有同名环境先核对用途。

```bash
python3 --version
python3 -m venv .venv-docs
source .venv-docs/bin/activate
python -m pip install -e .
python -m pip install -r requirements/m08-documents.txt
python -m pip check
python -m pip show pypdf python-docx reportlab
```

固定组合为pypdf 6.10.0、python-docx 1.2.0、reportlab 4.4.9，是本地已测组合，不是最新版本声明。仅做Markdown练习可省略格式依赖安装，并在后文使用`--formats md`。缺少python3-venv时按第1课处理安装，不重建已有训练环境。

## 3. 生成可核对的源文档

```bash
python -m enterprise_ai.document_samples generate \
  --output data/generated/m08-source-v1 --count 30 --formats mixed
```

预期：新目录内有`catalog.json`和两个租户目录，共30篇源文档，Markdown/PDF/DOCX各10篇。生成器拒绝覆盖。重做实验改用新的目录名，不清空原来的证据。`--count 300`或`1000`只增加模板复用规模，不证明知识更丰富。

打开3种格式各一篇，对照catalog中的title、tenant_id、roles、source_path和sha256。JSON中的中文可能保留原文，摘要对原始字节计算；换行或ZIP时间变化都会影响摘要。

```bash
python - <<'PY'
import json
from pathlib import Path
p = Path('data/generated/m08-source-v1/catalog.json')
c = json.loads(p.read_text(encoding='utf-8'))
print('documents:', len(c['documents']))
print(json.dumps(c['documents'][0], ensure_ascii=False, indent=2))
PY
```

三篇随仓库提交的可读样例在`data/samples/documents/`；它们方便阅读，完整导入实验请使用生成器创建的catalog和对应源文件。

## 4. 解析、切块和首次入库

```bash
mkdir -p reports/local
python -m enterprise_ai.document_pipeline ingest \
  --catalog data/generated/m08-source-v1/catalog.json \
  --sources data/generated/m08-source-v1 \
  --state reports/local/m08-state.sqlite \
  --report reports/local/m08-first.json \
  --max-chars 400 --overlap 40
```

预期报告`status=ok, created=30`，updated/deleted/unchanged为0。任何输入错误返回非零，报告为failed，全批不更新head。报告输出路径必须全新。

处理顺序为：完整清单结构校验→源路径检查→一次有界读取→SHA-256核对→解析同一字节快照→切块→SQLite事务。不会在检查后重新读取另一份源内容进行解析。

`max_chars`允许80–2000，默认400；过大使单块更长、后续token消耗更高，过小会把上下文切碎。overlap必须非负且小于max_chars的一半；更大重叠意味着更多冗余。块不会跨页/结构段拼接，因此短段可能产生短块。需在M09用检索评估决定参数，不能靠块数判断质量。

`pipeline_id`包含解析器算法、相关库版本、切块算法与配置。升级依赖或修改切块配置后必须提高受影响文档的版本，不能同版本偷偷重做。请保存原环境清单，避免无关升级污染重跑对照。

本章最终解析器为`m08-parser-v2`，增加对DOCX公式的明确拒绝。若曾使用早期T1/T2检查点的v1状态，请在新实验目录重做，或明确提高文档版本后导入；不要删除/覆盖原状态以掩盖差异。

## 5. 按可信身份导出

```bash
python -m enterprise_ai.document_pipeline export \
  --state reports/local/m08-state.sqlite \
  --output reports/local/m08-tenant1-sales.jsonl \
  --tenant 1 --roles sales --as-of 2026-10-21
```

预期只包含租户1的5篇CRM文档。当前固定组合、默认参数下本地混合格式实验得到23块；块数可能随参数与文档结构变化，关键是文档集合、租户、权限和定位正确。返回值给出chunks和输出SHA-256。

检查一块的`page/section/block/start/end`：start/end是**规范化后的该结构块文本**的字符区间，end不包含，不是原文件字节偏移。PDF有物理页号；DOCX和Markdown的page为null，不能伪造页码引用。`content_sha256`描述块文本，`source_sha256`绑定源文件；`chunk_id`还绑定版本和元数据。M09实际生成向量前`embedding_model_revision`为null。

这里`--tenant`和`--roles`来自教学操作者，**没有认证功能**。生产服务必须从已认证身份获取它们，不能把请求参数直接作为权限证明。代码先筛选当前head的元数据，再读取允许的文本，不能把全租户文本交给模型后让模型自行拒答。

## 6. 重跑与完整增量实验

同一清单、同一解析/切块配置，使用新的报告名重跑：

```bash
python -m enterprise_ai.document_pipeline ingest \
  --catalog data/generated/m08-source-v1/catalog.json \
  --sources data/generated/m08-source-v1 \
  --state reports/local/m08-state.sqlite \
  --report reports/local/m08-repeat.json
```

预期`unchanged=30`。即使版本已存在，仍读取并核对源文件；源文件被删或篡改不会用旧索引冒充重跑成功。

为了不改动前面实验，运行独立、纯Markdown的更新删除演示：

```bash
python -m enterprise_ai.document_demo --output reports/local/m08-incremental
```

预期summary为`baseline_documents=30, history_versions=32, checks_passed=7`，并标记`synthetic-fixture-not-production`。演示保留每个阶段的catalog、报告、SQLite和导出文件：

| 阶段 | 实际动作 | 观察点 |
| --- | --- | --- |
| 01-baseline | 初始30篇 | created=30 |
| 02-repeat | 相同清单重跑 | unchanged=30 |
| 03-update | crm-0001正文变化，版本2，sales改employee | updated=1，旧角色看不到新版和旧版 |
| 04-delete | crm-0002显式delete，版本2 | deleted=1，导出不再含该文档 |
| 05-bad-hash | 提交版本3但提供错误摘要 | status=failed，全部head保持原状 |

对比before-sales与after-sales，后者仅剩3篇未变CRM文档；after-employee只含crm-0001版本2；before/after-tenant2内容相同。历史中原30个版本仍保留，加上更新和删除共32个版本。示例代码使用运行时检查，`python -O`也不会跳过这些验收断言。

delete项只含tenant_id/document_id/version/op四个字段，op必须是`delete`；upsert必须完整给出所有元数据。初次删除未知ID会拒绝；相同删除版本重跑是unchanged。清单中省略一篇文档不代表删除，低版本回退会拒绝。恢复一个已删除的文档要提供更高版本完整upsert。

失败全批回滚可能意味着原权限还在数据库中。**涉及撤权的失败批次必须暂停下游发布/访问，修复成功后再恢复**，不能继续用旧ACL声称撤权已完成。已有JSONL也是静态快照，后续状态变化不会重写旧文件；M09必须做失效/删除传播，不能永久加载历史导出。

## 7. 格式边界与人工质量检查

- Markdown：UTF-8，NFC/换行规范化，标题与段落有顺序，代码围栏仅当数据，不执行里面的命令。不是完整Markdown渲染器；链接不会被访问。
- PDF：逐页提取文本，50页上限，空页拒绝并提示人工检查/OCR。提取不保证复杂多栏阅读顺序，也不做扫描件识别。空白封面也会被拒绝，需要人工处理并生成新来源版本。
- DOCX：正文段落/简单表格行按顺序，修订、绘图、公式（Office Math）、文本框、嵌套表格、域等不支持内容明确拒绝。页眉页脚/脚注有文本时要求人工处理。不会获取外部链接。
- 输入最多10MiB，提取文本50万字符；DOCX展开最多20MiB/2000项，压缩比限制200。限制服务于可信教学文件的可诊断性，不是恶意文件解析沙箱。

用[交接表](../runbooks/m08-document-handoff.md)记录：每格式样本数、抽样源ID、标题/段落/页码/表格顺序、是否有缺字、是否丢内容。PDF与DOCX都要用查看器打开检查。当前Windows没有LibreOffice，所以DOCX版面未验收；Ubuntu需人工检查，不将文本回读测试当作排版证明。

## 8. 故障与恢复

| 错误或现象 | 解释 | 恢复方法 |
| --- | --- | --- |
| 输出已存在 | 保护已有证据 | 使用新的输出/报告名；不要覆盖 |
| source sha256 mismatch | 源文件与清单不一致 | 核实正确来源，提高版本并重新计算SHA-256 |
| version rollback/conflict | 同版本内容/权限/配置不同，或试图降版本 | 保存旧证据，明确新版本；不要直接修改SQLite |
| 空PDF页/unsupported DOCX | 格式超出本章范围 | 人工核对并转换可支持的副本，记录转换关系和新版本 |
| foreign database | 误用了M03或其他SQLite | 使用独立M08状态路径，不删除旧库 |
| database is locked | 另一个写事务未结束 | 等待/定位占用进程，串行重跑；不清空数据库 |
| 导出0块 | 租户/角色/日期不匹配，或者当前head为未来版本/墓碑 | 查看catalog与报告；不关闭权限过滤求“有结果” |
| 数据库提交后报告写失败 | 磁盘/权限错误可能使报告为空或不完整 | 不发布本次结果；修复磁盘后同清单、新报告名幂等重跑 |

进程中断时保留现场。SQLite事务未提交会回滚；报告为空不能当成功。再次运行使用新报告路径，成功后再导出。不要把含真实文档的SQLite、JSONL或日志提交公开GitHub。

## 9. 验收与练习

```bash
python -m unittest discover -s tests -p 'test_document*.py' -v
```

必须记录实际通过/跳过数量。未安装格式依赖时对应测试会skip，不能写“PDF/DOCX通过”。全量回归用`python -m unittest discover -s tests -v`；它仍不代表数据库、GPU、Ubuntu服务实测。

练习：①只改roles但不升版本，会怎样？②把overlap改成0，对块数/覆盖有何影响？③向清单删去一项，是否会删库？④当前head明天生效，今天导出为什么为空？⑤文档中写“忽略所有权限”，流水线该做什么？先记录预期再实验，使用独立目录。

参考解释：①同版本冲突拒绝；②块重叠减少，字符覆盖必须完整，质量留M09评估；③不会，删除必须显式表达；④本章不回退历史；⑤保留为不可信文本数据，权限仍由代码执行，不执行文档指令。

交给M09的不是一个任意JSONL，而是已确认身份范围与as_of的当前导出、SHA-256、源catalog与解析配置、版本/删除实验报告及待人工检查项。后续向量索引必须保留这些元数据，补充实际embedding revision，并传播更新和撤权。

## 官方资料

- [pypdf文本提取](https://pypdf.readthedocs.io/en/stable/user/extract-text.html)：PDF布局与扫描内容的提取边界。
- [python-docx Document API](https://python-docx.readthedocs.io/en/latest/api/document.html)：iter_inner_content按正文顺序遍历段落与表格。
- [SQLite事务](https://www.sqlite.org/lang_transaction.html)：本章BEGIN IMMEDIATE用于串行写入与批次原子性。

下一课：[M09规划](../ROADMAP.md)，企业知识库与RAG。
