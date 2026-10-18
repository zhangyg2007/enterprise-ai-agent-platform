# M05 微调数据 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 交付480条可复现合成标注、双格式、分组切分、泄漏验收和教程。

**Architecture:** 模板模块生成30个实体场景组；数据模块校验与导出；教程解释人工标注和LLaMA-Factory映射。零训练服务。

**Tech Stack:** Python3.11–3.13标准库、unittest、JSON/JSONL/CSV。

**Spec:** [M05设计](../specs/2026-10-17-m05-finetune-data-design.md)

## Global Constraints

- 本地不安装平台/依赖，不运行模型；只处理合成样本。
- 30组、8类、每类2表达；固定336/96/48；两格式是替代关系。
- 保护M04题集，test不注册训练项；不承诺自动证明语义无泄漏。

## Review Focus

- 改写同一实体后跨split：校验实体与group边界。
- 同模板替换ID伪装独立测试：校验去数字模板指纹。
- training注册误指test或列名错：校验四项注册和映射。
- 修改标签/导出却仍通过：摘要、结构与双格式一致性校验。
- 输出目录已有或生成中断：拒绝覆盖，complete必须最后发布。

### T1 生成与严格验收

Files: `src/enterprise_ai/finetune_templates.py`, `src/enterprise_ai/finetune_data.py`, `tests/test_finetune_data.py`。

- [x] 先写测试并观察失败：480条/30组，336/96/48，固定seed一致；跨实体、模板、文本或M04重合拒绝；布尔ID、错标签拒绝。
- [x] 实现spec接口、48个表达模板、分组分配、Alpaca/ShareGPT导出与4项dataset_info。
- [x] 测试bundle真实文件往返、篡改检测、不覆盖、失败状态、两格式一一对齐、test-cases可被M04读取。

### T2 教程、数据卡与审核

Files: `docs/tutorials/05-finetune-data.md`, `docs/data-cards/m05-intents.md`, `docs/validation/2026-10-17-m05.md`, `docs/progress/2026-10-17-m01-m05-summary.md`。

- [x] 编写操作、标注规范、参数映射、人工抽样、泄漏反例、练习和答案。
- [x] 全套离线测试、实际生成/validate、CLI help、教程Bash/Python语法及命令核验。
- [x] 独立审核与作者验证分别记录；限额或服务器未测如实说明。
- [x] 更新导航；写用户要求的可打印总结，列已发布/未发布/服务器未验收边界。

完成记录（2026-10-18）：94项全量测试通过；独立36项复跑通过且无剩余P1/P2；教程命令与生成文件已校验。GitHub发布与服务器实际验收不在本次已完成范围。
