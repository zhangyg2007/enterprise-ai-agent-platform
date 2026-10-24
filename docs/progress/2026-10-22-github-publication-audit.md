# GitHub M06之后不可见：排查记录

2026-10-23状态更新：PR #4–#8及入口PR #10已合并main，最终记录见[主线发布](2026-10-22-main-publication.md)。以下保留合并前的排查快照；其中“等待确认”不再代表当前状态。M11仍是独立草稿PR #9。

2026-10-22核查：默认main只有第0–5课；M06–M10已推送到下表的远端分支，但PR依赖链尚未合并。并非本地文件丢失或上传后被删除。逐项读取GitHub文件blob SHA，与对应本地提交中的教程文件SHA相同。

| 模块 | 远端分支 | 已核实提交 | PR及其base |
| -- | -- | -- | -- |
| M06 | codex/m06-qwen-training | 88d9799 | [#4](https://github.com/zhangyg2007/enterprise-ai-agent-platform/pull/4)，main |
| M07 | codex/m07-evaluation-serving | 003633f | [#5](https://github.com/zhangyg2007/enterprise-ai-agent-platform/pull/5)，M06分支 |
| M08 | codex/m08-document-engineering | 6e9abe0 | [#6](https://github.com/zhangyg2007/enterprise-ai-agent-platform/pull/6)，M07分支 |
| M09 | codex/m09-enterprise-rag | b4fd4ea | [#7](https://github.com/zhangyg2007/enterprise-ai-agent-platform/pull/7)，M08分支 |
| M10 | codex/m10-langgraph-basics | b6f521c | [#8](https://github.com/zhangyg2007/enterprise-ai-agent-platform/pull/8)，M09分支 |

五个PR当时均OPEN/MERGEABLE，#5、#6仍为draft；未配置GitHub必需CI。不能把“分支已同步”称作“默认主线已发布”。原main aa7cfd5与M10分支有分叉，不能强推main覆盖。

修复方案已经准备：依次复核并合并#4→#8，每一步将下一个PR改base为main、必要时取消draft，保留分支及提交历史；最后核对main确实包含教程06–10、源码、plan与Filelist，并修正首页及服务器起步入口。无需启动服务或执行服务器数据库迁移。

按用户本会话AGENTS要求，合并main需要明确确认；截至本记录仍在等待，未擅自合并。确认后更新本记录的实际合并SHA和验证结果。确认前可在GitHub分支选择器查看[codex/m10-langgraph-basics](https://github.com/zhangyg2007/enterprise-ai-agent-platform/tree/codex/m10-langgraph-basics)，它包含第0–10课。服务器取得方式见[第10课](../tutorials/10-langgraph-basics.md)，保持本地修改和实验数据。

合并前就绪检查：完整回归227项通过（72.492秒）；之前一次Windows临时文件清理WinError32已保留日志，针对性4项与重新运行完整套件通过，不隐瞒该历史失败。未把这些本地结果当作Ubuntu、真实模型或PostgreSQL服务验收。

M11已另建codex/m11-enterprise-workflows，从b6f521c继续，T1合成业务账本、T2持久图、T3正式数据库分阶段交付。见[M11执行记录](2026-10-22-m11-execution.md)。环境私人记忆和原始日志保存在被忽略的本地目录，不上传GitHub。

2026-10-24现状覆盖上方历史待确认状态：用户已确认合并；PR #4–#8及入口PR #10均已合并，main为c68c0a2，默认页可取得第0–10课。M11分支以3e39064接入该main，T1/T2继续更新草稿PR #9并以main为目标，不代表已合并M11。T2修订后完整257项及独立环境30项通过，三案例实操命令通过；T3正式PostgreSQL与完整第11课尚待实施。后续以M11 T2执行/验证记录为准。
