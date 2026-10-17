# M04 模型调用与基线 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 交付可解释、可复现的 Ollama 教学评估工具与 Ubuntu 操作教程。

**Architecture:** 标准库客户端负责 HTTP；纯函数负责题集与评分；运行器保存证据。教程与代码共享明确命令契约。

**Tech Stack:** Python 3.11–3.13、unittest、http.client、JSON/JSONL、Ollama REST。

**Spec:** [M04 设计](../specs/2026-10-17-m04-model-baseline-design.md)

## Global Constraints

- 零新增依赖；Windows 不安装平台或启动服务；仅 mock HTTP 传输。
- CLI 使用模型配置，不修改数据库 .env。
- 所有样本为合成公开教学开发集；本模块不执行 SQL 或业务工具。
- 独立 reviewer 与 verification 分开；未运行服务器不声称模型通过。

## Review Focus

1. 超时与 HTTP 错误是否从准确率分母消失：T2 全失败测试。
2. bool/额外字段/重复 JSON key 被宽松接受：T1/T2 严格解析测试。
3. 标签变更使两次评估失去可比性：T2 前后 inventory 漂移测试。
4. 重用输出路径造成证据覆盖或无谓模型调用：T2 已存在目录测试。
5. 中断留下看似完成的报告：T2 中断保留 running 与已写记录测试。

### T1：Ollama 客户端

Files: `src/enterprise_ai/ollama.py`, `tests/test_ollama.py`。
接口严格见 spec 的 OllamaClient/ModelError。适配器独立实现，不修改评分器和根配置。

- [ ] 写地址、请求载荷、HTTP 错误、严格 JSON、done、标签和 digest 校验测试；先观察缺失功能失败。
- [ ] 实现客户端；响应上限 4 MiB，不重试/不跟随跳转，不把服务端错误原文直接打印。
- [ ] 定向运行 `python -m unittest tests.test_ollama -v`，全部通过。

### T2：样本、评分与记录

Files: `src/enterprise_ai/baseline.py`, `src/enterprise_ai/evaluation.py`, `tests/test_baseline.py`, `data/samples/m04-intents.jsonl`。
消费 T1 接口。输出 `load_cases(path)`、`score_response(raw, expected)`、`run_evaluation(client, *, model, cases_path, output_dir, limit=None, think=None, options=None)`。

- [ ] 先写失败测试：正确答案 exact=true；超时+正确各一题 exact_rate=0.5；截断 exact=false；bool ID schema=false；不覆盖/身份漂移/中断。
- [ ] 实现16题严格校验、固定提示、评分、持久化和 CLI；错误不得静默丢失。
- [ ] 运行 `python -m unittest tests.test_baseline -v`，检查真实临时文件证据与手算指标一致。

### T3：教程与发布前验证

Files: `docs/tutorials/04-model-baseline.md`, `README.md`, `docs/ROADMAP.md`, `docs/validation/2026-10-17-m04.md`。

- [ ] 编写从原理到操作、预期、故障、恢复、练习答案的完整教程；真实效果留空待测。
- [ ] 全套 `python -m unittest discover -v`；检查所有 CLI help、Python AST、教程 Bash 语法与相对链接。
- [ ] 独立审查代码与教程，修复实质问题并执行回归；保存真实验证证据。
- [ ] 更新导航与版本范围，提交可审阅结果；不把服务器验收标为完成。

执行说明：依据用户已确认的分模块方案直接实施。T1 和 T2 的文件所有权独立，接口按 spec 固定；主代理串行维护共享导航与配置。已有隔离工作目录复用，无环境安装步骤。
