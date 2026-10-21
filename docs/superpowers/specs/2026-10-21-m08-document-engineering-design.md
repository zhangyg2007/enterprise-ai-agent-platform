# M08 企业文档工程设计

## 目标与范围

延续已确认课程路线：让有 Python/Linux 基础的学习者理解文档进入知识库之前的处理，能解释每个 chunk 的来源、版本、有效期和可见范围，并验证更新/删除不会留下当前可见旧块。提供30篇CRM/HCM/SCM合成文档，可扩展到300或1000篇；这不是知识覆盖率或真实制度的证明。

支持 UTF-8 Markdown、文本 PDF、DOCX 正文段落与简单表格。扫描件/OCR、复杂布局恢复、嵌入附件、检索、embedding、pgvector、网页和企业身份认证均不在本章；M09消费本章元数据。HCM规则全部为虚构教学业务规则。

技术选择由实现者作出：标准库处理manifest/切块/SQLite状态；PDF用pypdf 6.10.0，DOCX用python-docx 1.2.0，生成PDF用reportlab 4.4.9。这是本地预置库可实测的固定组合，不是“最新版本”声明。仅在已有Codex运行库执行格式测试；不安装平台环境、不运行数据库服务/GPU。Ubuntu安装通过单独requirements/m08-documents.txt说明。

## 组成与接口

### document_samples.py

`generate_corpus(output: Path, count=30, formats='mixed') -> dict`：新目录独占，默认30篇，支持30/300/1000；两个租户1/2，CRM/HCM/SCM三个领域，每领域5类主题（共15种模板，每租户一组）。mixed轮换md/pdf/docx；md用于纯标准库练习。语义文本确定，PDF invariant模式，DOCX规范化ZIP时间与核心属性，保证同版本依赖下可重复。发布30篇生成器与3篇Markdown可读样例，不把上千生成文件提交仓库。

输出catalog.json含schema_version=m08-catalog-v1与documents；每项字段严格固定：tenant_id正整数、document_id小写ASCII标识、version正整数、op=upsert、source_path安全相对路径、title、domain（crm/hcm/scm）、roles（employee/sales/hr/procurement非空去重列表）、effective_from、effective_to（ISO日期或null）、sha256。同租户ID复合键唯一。禁止absolute、..、反斜杠、链接逃逸。delete项仅有tenant_id/document_id/version/op。manifest明确删除，缺文件和清单缺项都不推断删除。

### document_parse.py

`parse_document(raw: bytes, suffix: str) -> list[dict]`：只消费已读并验证摘要的字节快照；返回text、section、page（PDF从1计，其他null）、block（从1计）。Markdown按标题/空行保留原顺序，围栏内容当作数据不执行；PDF逐页提取，空页拒绝并要求人工检查/OCR；DOCX按正文顺序交替段落/表格，表格按行保留单元格分隔，不声称物理页码。检测修订/绘图/嵌套表格等不支持内容时明确报错，不静默遗漏。

文件上限10MiB、50页、提取文本500000字符；DOCX ZIP展开总量20MiB、最多2000项、单项压缩比上限200、拒绝重复名/宏/加密包。文本NFC与换行标准化，拒绝NUL。外部链接不获取，文档指令从不执行。教学解析针对可核对的可信合成文件；这些限制不构成恶意文件解析沙箱，真实不可信文档需隔离工作进程/资源限制。

### document_pipeline.py

`chunk_segments(metadata, segments, *, max_chars=400, overlap=40) -> list[dict]`。字符计数不是token；80<=max_chars<=2000，0<=overlap<max_chars/2。同一页/结构块内按段落/标点优先切分，长段有界切片；不跨源块拼接。chunk_id由来源/版本/定位/切块配置/文本摘要确定，配置相同重跑稳定。字段：schema_version=m08-chunk-v1、tenant_id、document_id、version、title、domain、roles、effective_from/to、source_path/source_sha256、page/section/block/start/end、text/content_sha256、pipeline_id、embedding_model_revision=null。后者在M09实际生成embedding时补齐。

`ingest(catalog_path, source_root, state_path, report_path, *, max_chars=400, overlap=40) -> dict`：报告新路径独占。先对完整清单验证元数据/去重，再读取每个upsert的安全源字节、校验摘要并解析切块。任何项目失败：报告failed，整个批次不改变当前索引，源文件保留。不得将失败输出发布给下游；涉及撤权的失败批次应暂停下游服务直至处理成功，不以旧ACL继续服务。

SQLite单写者事务BEGIN IMMEDIATE、参数化SQL；versions保存不可变历史，chunks按版本保存，heads保存每个租户文档的当前版本。相同版本与相同内容/元数据/解析器/切块配置为unchanged；相同版本内容或ACL变化为冲突；低版本拒绝回滚；高版本upsert原子替换head，高版本delete将head移到无块墓碑。首次delete未知ID拒绝（防写错ID），重复同版本delete幂等。导入批次中的任一冲突回滚全批。报告记录created/updated/deleted/unchanged和失败项，不写“全部成功”掩盖异常。

报告提交失败仍保留数据库已提交事实：重跑同一清单使用新报告路径可幂等恢复。SQLite状态不容许多个schema混用；明确标记应用版本。解析器名称及已安装版本与切块算法构成pipeline_id，不同版本/配置不能伪装为相同版本的幂等重跑。

`export_chunks(state_path, output_path, *, tenant_id, roles, as_of) -> dict`：仅导出head为upsert且有效期覆盖as_of、指定租户且roles交集非空的chunks，确定顺序，输出JSONL与摘要返回值；输出独占。参数是教学演示可信身份上下文，不是用户认证。不得先把所有租户文本交给调用方再做筛选。只展示当前head，未来生效的新head在生效前不暴露，旧版本不自动回退。历史查询留后续，不把旧文档当当前事实。

CLI：samples generate --output --count --formats；pipeline ingest --catalog --sources --state --report [--max-chars/--overlap]；pipeline export --state --output --tenant --roles --as-of。失败非零，无静默重试/覆盖。

### document_demo.py

`run_demo(output)`生成30篇Markdown基线，在全新目录按顺序执行：初始导入、重跑不变、提高1篇版本并变更内容与权限、删除另1篇、错误摘要批次拒绝。分别导出租户/角色/as_of快照，断言旧块不可见、跨租户不可见、更新后旧角色不可见、历史仍留存、失败批次不修改head。生成带明确fixture标签的summary.json，不能作为生产性能或真实文档质量报告。

## 教学材料与验证

第8课解释文档/版本/chunk/页码/字符与token/ACL/生效时间/幂等/墓碑/原子事务；给Ubuntu环境、生成30篇、导入、导出、增量演示、异常排查、人工抽样与M09交接步骤。数据卡说明合成来源和覆盖限制，交付模板记录质量指标与问题。Filelist逐项解释新增文件，版本升为0.1.5.0，M08独立分支依赖M07。

验证：标准库核心单测；预置库真实PDF/DOCX生成与解析回读；PDF样例渲染检查、DOCX渲染工具可用性检查；30篇三格式集成；完整增量演示；权限/时间/版本/事务/失败路径；全量旧测试回归；独立只读代码审核与修复。所有运行证据注明本地预置环境，Ubuntu待验收。

本次复用既有课程目标，由实现者选择上述可逆技术细节；用户已要求按路线继续并及时同步GitHub，不再重复请求启动批准。
