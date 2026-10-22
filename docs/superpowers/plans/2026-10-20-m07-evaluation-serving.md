# M07 Evaluation and Serving Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Follow TDD and independent review.

**Goal:** 把M06 adapter转为可比较、可回归、可启动和可回滚的教学交付包。

**Architecture:** delivery冻结数据/配置/资产；eval通过固定LF生成并保留证据；api只做回环探针。互相通过设计中的明确接口集成，不改M04/M05契约。

**Tech Stack:** 标准库用于本地代码；Ubuntu复用M06 LF0.9.4环境。

**Spec:** [M07设计](../specs/2026-10-20-m07-evaluation-serving-design.md)

## Global Constraints

- 不在Windows安装依赖、下载模型或执行GPU/真实API服务。
- 保留M06固定底座与依赖；同4bit比较微调，同BF16比较导出。
- 所有输出独占，失败保留记录；测试不能被当作选型集。
- 安装测试另会话；所有真实成绩待服务器执行。

## Review Focus

- 题集或模型在校验后变动：用受检字节快照并核对运行前后资产。
- 不同模型/生成参数/子集伪装成可比较：比较器拒绝。
- 错误行被删除或summary被改：重新验证题号/计数及重算指标。
- 量化底座错误合并与输出覆盖：CPU非量化export配置及新目录guard。
- API代理/重定向/鉴权错误、密钥写报告：回环直连与脱敏失败测试。

### T1 交付包准备器
- [x] tests/test_model_delivery.py先RED；model_delivery.py实现设计接口。
- [x] 检查M05来源、M06sft、adapter归属、流式指纹、路径越界和中断状态。
- [x] 验证两组推理精度、CPU导出、新目录保护与源文件变化。

### T2 模型评估与比较
- [x] tests/test_model_eval.py先RED；model_eval.py实现假后端可测的评估与对照。
- [x] 核对固定ChatModel API；测试错误分母、截断、资产变化、子集/混合配置拒绝、篡改报告。
- [x] CLI生成与比较输出可由教程逐项解释。

### T3 API探针与教程
- [x] tests/test_model_api.py先RED；model_api.py实现回环HTTP与严格响应校验。
- [x] 编写docs/tutorials/07-evaluation-serving.md、docs/runbooks/m07-model-delivery.md。
- [x] 编写验证记录，更新课程导航与CHANGELOG；全量验证和独立复审。
- [x] 将审核后的M06与M07分别提交、同步GitHub，明确PR/main状态与服务器待验收项。

2026-10-21 本地交付门禁：173项离线测试通过，静态检查和独立审阅修复已完成。详见 `docs/validation/2026-10-20-m07.md` 与执行 ledger；真实 Ubuntu 验收仍待执行，PR 合并状态以 GitHub 为准。
