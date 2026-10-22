# 第0–10课主线发布记录

2026-10-22：用户明确确认依次合并PR #4–#8，并修正首页及服务器起步入口。仓库默认分支为main，无GitHub Actions/自动部署；本批只发布教程和代码，未运行Ubuntu服务、数据库迁移或模型训练。

## 实际合并

| PR | 模块 | main合并提交 | UTC时间 |
| -- | -- | -- | -- |
| #4 | M06 | `e01e4a53038ccb248905ecae4260fc3fe510f3ff` | 2026-10-22T04:38:29Z |
| #5 | M07 | `876028133a17ac51ba0c6e43535b7fb5e8ccac49` | 2026-10-22T04:41:28Z |
| #6 | M08 | `b47b21bbb97571416982595135f6ff80048e1235` | 2026-10-22T04:41:43Z |
| #7 | M09 | `c0415d9cb93e32a0e65e77bec4ccd6a174c06541` | 2026-10-22T04:42:58Z |
| #8 | M10 | `2b825524de3262409ddb40dd479696a6e9d71848` | 2026-10-22T04:43:11Z |

五个PR均由GitHub普通merge commit合并到main，章节分支保留。M07的Filelist add/add冲突仅合并导航，M09的plan add/add冲突将原路线表保存在[原计划快照](2026-10-22-original-plan.md)，保留当前完整总计划。两次文档修订都核对src/tests/requirements/configs/infra/pyproject/compose无差异。

合并后的main 2b825524与已验证M10 b6f521c相比，仅增加Filelist导航及原计划快照，业务源码和依赖完全相同。重复的原main导航在入口修订中保留一份，模块明细完整保留。

## 验证与入口修订

- 合并前在精确M10版本运行完整回归：227项通过，85.127秒，无skip；此前各模块独立review及对应验证记录保留。没有配置远端CI，不能声称GitHub CI通过。
- 首页、ROADMAP、总计划、CHANGELOG、服务器起步、第10课和M10交接手册统一到main与课程批次0.1.7.0；Filelist随批更新。
- 入口静态检查：53个Python源文件、57个Shell代码块、7个Python示例、115个本地链接、3份配置通过。
- 原样执行起步手册的命令，在本地Git镜像完成首次clone、已有main更新、旧M09 single-branch补fetch映射并切main三种检查；第0–10课齐备，忽略的.env和实验文件保持原内容。该镜像使用真实已合并提交，但不是GitHub网络或Ubuntu验收。
- 实际GitHub新clone两次失败：首次Recv failure/Connection was aborted，HTTP/1.1重试后连接443端口失败；失败日志保留。Git fetch/push与GitHub API已成功，不把本地镜像检查冒充公网clone通过。
- 独立文档review完成：无Critical/Important/Minor问题，Ready。核对main/版本/章节状态、原计划完整保留、Filelist导航及失败停止说明，并确认公网clone失败与镜像通过分开报告。未将该review冒充历史业务代码复审或完整套件重跑。

原始日志和远端合并回执保存于被忽略的reports/local/main-landing-*；根目录原有未跟踪Filelist/plan先做SHA256一致的本地备份，再更新本地main，备份不上传。

## 后续

main当前只交付第0–10课。M11的T1合成SQLite事务实验在[草稿PR #9](https://github.com/zhangyg2007/enterprise-ai-agent-platform/pull/9)，独立分支5cdba7f，237项完整回归及独立review已通过；T2持久图、T3正式PostgreSQL和完整第11课尚待实施，之后为M12/M13。课程范围没有新增必修模块。

Ubuntu安装、真实模型/训练、BGE与PostgreSQL/RLS/HNSW仍需按各章验收表执行。用户环境版本、绝对路径、私人记忆、实验数据库和原始日志不上传GitHub。[服务器起步](../runbooks/server-start.md)提供保留修改与快进更新步骤。
