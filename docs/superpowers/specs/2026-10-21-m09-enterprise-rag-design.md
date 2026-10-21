# M09 企业知识库与RAG设计

沿用已确认课程：M08文档→本地embedding→pgvector精确/HNSW与中文关键词→RRF融合→可选重排→带引用的证据回答→检索评估。本章是可信教学CLI，不建立HTTP认证或生产服务。用户已要求按路线继续与及时同步，以下可逆实现选择由执行者作出，不重复请求启动批准。Ubuntu、真实模型与数据库实测仍由服务器执行，本地不安装服务/下载大模型。

## 输入、快照与失效

不能把某个roles/as_of过滤后的旧JSONL当全量知识源。`rag_index.snapshot(state,tenant)`从M08 SQLite只读事务读取指定租户全部当前heads（含墓碑和未来生效项），并读取upsert当前chunks；验证每块schema、SHA、chunk_id与版本元数据一致。返回heads、chunks及规范JSON摘要source_digest。仅接受M08标记数据库，源路径拒绝链接。

`build_index(state,tenant,generation,encoder,output)`创建新JSON包（schema m09-index-v1）；generation为操作者提供的递增正整数；记录tenant、source_digest、模型身份/权重文件摘要、维度、归一化、token上限、算法版本、chunks与vectors、整体digest。空租户/全部删除可形成空包，用于撤销旧索引。只有当前upsert块参与embedding，未来块保留并在查询时过滤日期。构建前后核对M08快照相同；失败不输出成功包。

查询和导入都必须提供当前M08 state，前后核对source_digest；旧快照或源更新后拒绝继续查询，提示重建。不自动清空、回退、缓存。该检查限定本地可信文件，不承诺跨服务原子撤权；部署并发更新需上层停写/发布屏障。source_digest不是数字签名。

## 模型适配

`rag_models.FixtureEncoder`：标准库中文字符bigram/英文数字token的确定性哈希1024维归一化；identity明确synthetic-fixture，不得用其成绩宣称真实语义能力。

`LocalEncoder(path,revision)`：BAAI/bge-m3固定revision `5617a9f61b028005a4858fdac845db406aefb181`，Transformers AutoModel/AutoTokenizer，CLS pooling，L2归一化，1024维，CPU默认，batch=4，max_tokens=1024；超长输入拒绝，禁止静默截断。local_files_only、trust_remote_code=False、weights_only=True；固定路径中权重/配置/tokenizer相关文件摘要绑定identity并在操作后复验。包内不暴露密钥。模型下载步骤仅写在Ubuntu教程。BGE-M3仓库该revision使用pytorch_model.bin，需要受信官方来源及torch>=2.6的weights_only路径。

`LocalReranker(path,revision)`使用本地BAAI/bge-reranker-v2-m3的AutoModelForSequenceClassification，固定revision由官方元数据查询后写入配置；sigmoid(logit)，输入对超长拒绝。权重不下载到本机。可选rerank仅对已授权候选执行；默认无重排，真实报告必须记录reranker身份。

向量严格校验维度、数值有限、非零、归一化。不同模型/权重/token策略不能混用。依赖复用M06候选torch2.8.0/transformers4.57.1，单独requirements/m09-rag.txt（含psycopg3.2.10、sentencepiece0.2.1），候选环境不是GPU兼容认证。

## 检索与回答

`rag_search.tokens(text)`：NFC/lower，中文逐字bigram（单字也保留）、ASCII单词/数字；显式字符分词基线，不称语言学分词。关键词采用TF-IDF加权重合，固定算法；pgvector路径先在SQL按权限/日期过滤后取至多10000块候选，再用同一函数关键词排序，适合30–1000篇教学语料，超上限明确拒绝。

`search(index,state,query,roles,as_of,mode='hybrid',k=5,candidates=20,encoder,reranker=None)`：权限/date先过滤，dense精确cosine、keyword或RRF(k0=60)融合；稳定tie按chunk_id；重排只接受授权候选的有限同长度分数。query非空<=500字符、k1..20、candidate k..100。top-k不自动意味着相关；没有关键词匹配或无授权证据时明确无证据（语义模式可返回低相似候选，不能声称可回答）。每个result给出chunk来源元数据与检索分数。

`evidence_answer(result)`是确定性摘录式RAG教学基线，返回status=evidence/insufficient、证据原文及文档ID/版本/页码或章节引用；不假装LLM生成，也不执行文档指令。不含未经引用的业务结论。M10再把证据交给模型与工具编排。

## pgvector与隔离

新增`infra/postgres/migrations/020-m09-rag.sql`，由Ubuntu管理员在已有M01库显式执行，事务化、仅新建rag_lab schema，不修改已有业务表；第二次执行拒绝已存在schema，不能隐藏部分迁移。vector扩展须已存在。

collections保存collection_id、tenant、generation、digest、model identity；chunks保存payload、roles、有效期及vector(1024)，级联外键。不用用户输入拼SQL标识符。导入用参数化SQL与事务锁，拒绝同generation不同digest/更低generation；新generation全量替换该collection内部派生块，原M08历史保留。建HNSW cosine索引。真实导入拒绝fixture包。

RLS：现有lab_a_reader/lab_b_reader通过auth.current_tenant()绑定租户；新reader_roles表与current_roles()固定sales演示，管理员可显式调整；reader不能改映射，不能读其他角色chunks。禁止使用自报session变量授权。collection可见性也按租户。查询额外校验传入tenant/roles不超出reader身份；effective日期SQL显式过滤。导入只用管理员，不作为查询账号。

`rag_pg.load(settings,collection,index,state)`与`retrieve(settings,reader,collection,index,state,query,roles,as_of,method,encoder)`：核对真实模型identity及source freshness。数据库generation/digest必须等于受检包。exact事务SET LOCAL enable_indexscan=off/enable_bitmapscan=off；HNSW用strict_order与ef_search=100，返回不足不静默冒充exact；返回diagnostics标记requested_method并记录EXPLAIN JSON实际计划，不凭SET宣称用了索引。过滤在SQL候选阶段生效。M09真实验收工具需可用两reader比较、核对向量结果与Python精确基线、更新后旧包拒绝；未运行时标注未验证。

## 评估与交付

固定公开中文开发集data/samples/m09-retrieval.json：至少12题，覆盖CRM/HCM/SCM、跨租户相似规则、错误角色、无答案；相关性以tenant/doc/version固定文档级标签；拒绝题expected_documents为空。评估去重文档排名、Recall@k/MRR、拒绝题泄露率（拒绝题检索到候选不等同语义正确）；错误题保留分母与原因。报告模式、代码、index/source/model身份、检索/重排配置、逐题hits/引用；不能把fixture和真实报告直接比较。

新增离线demo：生成30篇M08 MD→index→关键词/向量/融合→带引用摘录→ACL更新+delete→旧包拒绝→新generation重建→旧块不可见。保存阶段文件和质量报告，指标标记fixture。教程包含Ubuntu安装/固定模型下载/迁移/真实检索/EXPLAIN与HNSW召回比较/手工引用核对/异常恢复。更新Filelist、README、ROADMAP、CHANGELOG、VERSION=0.1.6.0及ledger；按T1/T2/T3同步独立分支，依赖M08草稿PR。

证据：先RED→GREEN；格式/来源/向量异常、隔离/日期/删除失效、参数化SQL契约、排名评估与引用测试；全量离线回归；独立review和失败复现修复。pgvector SQL与真实BGE是否运行必须分开报告。
