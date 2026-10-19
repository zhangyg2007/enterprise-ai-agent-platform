# M06 Qwen Training Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or subagent-driven-development. Follow TDD and independent final review.

**Goal:** 为24GB单卡提供可审计的微调准备工具与详细课程，不伪造服务器运行结果。
**Architecture:** 配置由主代理串行维护，标准库准备器与服务器检查器接口固定，教程依同一契约编写。
**Tech Stack:** 标准库本地验证；Ubuntu Python3.11/LLaMA-Factory v0.9.4的服务器候选环境。
**Spec:** [M06设计](../specs/2026-10-18-m06-qwen-training-design.md)

## Global Constraints

- Windows不安装依赖、不启动服务/训练；实机测试另一个会话。
- 固定来源、训练数据只含train/validation；禁用自动覆盖/缓存复用。
- 同一run保存配置与数据摘要；本地检查不得称GPU兼容认证。

## Review Focus

- 训练时源M05文件变化：准备器复制验证后的同一快照并记录摘要。
- 输出路径重用或索引路径越界：准备器拒绝并测试。
- smoke误用test或随机切分：测试64/32来源与registry。
- tokenizer只算用户文本遗漏答案：检查器测试system/user/assistant与cutoff。
- 环境或token检查失败却退出0：模拟缺依赖/CUDA/BF16和超限路径测试。

### T1 准备器与配置
- [x] 先写失败测试，再实现 `training.py` / `test_training.py`，按spec接口与状态语义。
- [x] 编写2份配置、来源JSON、核心约束文件；核对固定上游源码合法字段。
- [x] 实际离线生成准备包、校验复制数据/配置/摘要与不覆盖行为。

### T2 服务器检查器
- [x] `training_checks.py` / `test_training_checks.py`：TDD验证纯逻辑与依赖缺失路径，重依赖延迟导入。
- [x] 检查器只在服务器加载tokenizer/GPU，不自动启动训练；记录真实检查状态。

### T3 教程与交付
- [x] 编写M06中文教程、跨会话运行手册和验证记录；含参数解释、练习答案。
- [x] 全量标准库测试、AST/配置/链接/教程语法检查；独立审核后处理发现。
- [x] 更新导航并保存可审阅提交，报告发布与服务器验收边界。
