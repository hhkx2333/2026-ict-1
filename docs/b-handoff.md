# B 任务交接：评分核心、校准与评测

更新：2026-11-10。实现位于 `codex/b-grading-mvp` 开发分支，不合并 main。本轮继承已有单题评分模块，新增案例检索、校准草案、风险复核、练习筛选、基线与数据评测。

## 1. 已经能做什么

- 单题逐项建议分、精确原文引文、规则编号、程序汇总总分；非法分档/重复或缺失分项/伪引文会触发修复或人工复核。
- 已审核案例按题号、规则版本、案例集版本过滤；中文字符二元组词面相似度排序，最多检索3条（可设0到5），排除当前答案与去空白同文。**这是轻量检索，不是向量检索或训练模型。**
- 教师修正生成 `DRAFT` 草案，区分单份答案与通用规则；草案不自动变成评分规则。发布权限、审核者身份与历史由 C 实现。
- 一次模型调用后若标记疑难，可追加一次针对性复核；结构修复与复核共用最多2次调用预算。两次分项分数不同保留人工复核标记；相同也不意味着正确。
- 生成教师确认结果的错点反馈，推荐审核题库中存在的题号，排除已做题；不诊断长期掌握率。
- 五种可运行路径：直接评分、带分样例直接评分、结构化评分、已审核案例校准评分、细分检查项评分。真实比较仍需固定相同模型、输入和教师口径。
- JSONL批处理、逐条落盘、失败状态、运行耗时；严格拒绝带 `score` 的待评输入，不覆盖既有运行结果。
- 评测：MAE、RMSE、偏高/偏低、完全一致、容差一致率、较大分差、建议覆盖率、转人工与失败；全体建议与低风险建议分开报告。

所有输出是建议，`teacher_confirmed=false`。B 模块不会确认或发布正式成绩。

## 2. 本地运行

Python 3.10+，标准库，无需安装依赖。在项目根目录：

```sh
python3 -m unittest discover -s tests -v
python3 -m examples.demo
python3 -m examples.calibration_demo
python3 -m examples.verify_repository_data
```

前两个演示中涉及模型输出的部分均为人工编写的合成回放；不能作为模型准确性、教师真实审核或校准有效性的证据。`verify_repository_data` 读取 `work/source-snapshot` 的固定数据快照，复算同学已经做过的实验，不发模型请求。原始数据不复制进开发分支；新克隆请先执行 `git fetch origin 测试数据`，脚本会用 `git show` 从固定提交提取所需文件到本地work目录，也可用 `--source-root` 指定已有快照。

## 3. 和 C 的模块契约

```python
from backend.grading import grade_answer, ChatCompletionsProvider

result = grade_answer(question, published_rubric, answer, approved_cases,
                      provider=ChatCompletionsProvider.from_env(), max_attempts=2)
```

`question`：`question_id/text/max_score`，可含参考答案。`answer`：`question_id/answer_id/text`，待评分数不会发送模型。`rubric`：`question_id/version/status/criteria`；正式使用必须 `PUBLISHED`。分项字段见 `examples/synthetic.json`。

`cases` 是列表，各项为：

```text
case_id / case_set_version / question_id / rubric_version
teacher_approved: true（C 根据真实审核记录提供，不能让模型填写）
answer: {question_id, answer_id, text}
assessment: {items: [...], review_reasons: [...]}
```

`assessment` 使用与模型逐项输出相同的完整结构，经引文与分档校验；全案必须绑定同一案例集版本。不得将原数据只有总分的记录虚构成“教师逐项审核案例”。总分样例可用于 fewshot 基线；真正的逐项案例需要补充审核。

输出延续原有 `schema_version=1` 字段：`question_id/answer_id/rubric_version/case_set_version/items/suggested_total/status/review_reasons/teacher_confirmed/metadata`。metadata 新增 `selected_case_ids/experimental_draft`，记录输入摘要与实际调用次数。

| 状态 | 含义 | C 的处理 |
| --- | --- | --- |
| SUGGESTED | 合法、没有当前流程发现的风险 | 显示“建议”，仍需教师确认和抽查 |
| REVIEW_REQUIRED | 疑难、规则草案、空答案或输出修复失败 | 进入复核队列；总分可能为 null |
| FAILED | 模型调用失败 | 保留失败、允许任务层显式重试，不写0分 |
| 抛 ValidationError | 输入/规则/案例配置错误 | 修正配置，勿盲目重试模型 |

证据位置是Python Unicode码点下标，end不包含；前端可用 `Array.from(text).slice(start,end).join('')` 定位。引用存在不代表语义判断正确。

`correction_draft` 接收教师修正后的完整分项结果，返回草案和稳定草案编号；`scope=answer_only` 不改变其他答案；`scope=general_rule` 必须另附规则建议文字。C 审核后生成新规则版本，重新关联案例并固定批次，不能静默覆盖旧分。

`confirmed_feedback` 与 `practice_candidates` 只接受教师确认结果，使用 `confirmed_items`（criterion_id/score/max_score/teacher_reason/evidence）而非模型的 `items`。练习题需有唯一 `practice_id/question_id/criterion_ids/teacher_approved`。

同步接口应在 C 的线程池或任务 Worker 运行。身份、课程访问权限、数据库、规则发布、任务恢复、教师确认与正式成绩导出由 C 负责，B 不冒充完成了这些功能。

## 4. 真实模型接入

本机已通过被Git忽略的 `.env` 配置 DeepSeek 并运行同一批10份公开匿名开发答案的三轮试验；没有教师校准有效性的结论。密钥不会随代码包交付。结果、失败及最新协议的待验证项见 [改进记录](2026-11-10-improvements.md)，配置说明见 [本地接入](deepseek-local-setup.md)。适配层支持显式 HTTPS Chat Completions JSON 协议，拒绝跳转；华为服务端点、模型、区域与鉴权须按团队账号实际验证。密钥仅放服务端环境。

示例先跑1条，确认响应兼容与额度：

```sh
python3 -m backend.grading.cli run --mode structured --live --allow-draft --question work/q011/runtime/question.json --rubric examples/q011_rubric_draft.json --answers work/q011/runtime/remaining_unscored.jsonl --limit 1 --output work/runs/structured-001.jsonl
```

`--allow-draft` 仅用于开发，所有结果强制人工复核。不要把草案改成 PUBLISHED 冒充审核。教师确认后新增真实规则版本再运行正式方案。

直接评分路径 `--mode direct` 不需 rubric；`--mode fewshot` 另加 `--examples work/q011/runtime/calibration_examples.jsonl`；`--mode calibrated` 需 `--rubric` 和非空 `--cases` 审核案例JSON。比较时使用相同目标答案与底层模型，但按实际输入差异解释结果。

评测另行运行，标签只交给该步骤：

```sh
python3 -m backend.grading.cli evaluate --predictions work/q011/evaluator/historical_predictions.jsonl --truth work/q011/evaluator/current_labels.jsonl --output work/runs/historical-metrics.json
```

运行新方案的一个子集时，也应提供同一子集的标签；评测器会列出缺失编号与覆盖率，不能把失败/漏评样本悄悄当作正确或从分母隐藏。

## 5. 第011题数据检查

来源：`测试数据` 分支，固定提交 `4650e5eb65e44ee0bf084667efde51b1a307a900`。原始来源、许可、改动记录随本地快照保留；本轮未向GitHub再分发。

- 共107条、11条学习样本、去空白同文98组，有1条空白答案。
- `011-000005` 与学习样本 `011-000031` 同文，不能仅排除学习编号就算未见答案；准备脚本额外排除了005。
- 因而剩余开发候选为95条，包含那1条空白答案；不是96条独立新文本。分成两个开发子集时按同文组分割，未做语义近重复检测。
- 第011题的107条已被原报告查看，用作开发资料，**不称最终封存测试**。
- 同学历史结果复算：MAE 1.719626、RMSE 2.279531、平均偏差+1.065421；完全同分24/107，误差不超过1分55/107，差至少3分27/107。与报告吻合。
- 当前基准包含复核与未复核标签；这些是分数一致性指标，不等于教师裁定后的模型准确率。

## 6. 仍需要 A / C / 用户提供的条件

1. A组织教师确认第011题配分、替代算法、笔误与代码冲突口径；草案列出了待确认项。
2. 教师提供少量逐项修正和依据，才能构建真实审核案例。原总分样例不会自动升级为可靠逐项标注。
3. A另保留不参与调试的数据，并组织盲评、证据支持性和人工总工时验证。
4. C提供正式业务数据结构与审核接口，配置实际模型服务，接入网站与数据库。
5. 真实使用CodeArts并保留可对应的成果。本轮是在GPT本地完成，不能事后写成已使用CodeArts。

完成工程检查不等于完成算法效果验收。最终仍需按教师口径进行真实模型复测、教师校准、独立对照评测和C的集成运行。

## 7. 新增细分检查项接口

```python
from backend.grading import grade_checked_answer, ChatCompletionsProvider
result = grade_checked_answer(question, checklist_rubric, answer, approved_cases,
                              provider=ChatCompletionsProvider.from_env(),
                              max_attempts=2, allow_draft=True)
```

正式规则仍须PUBLISHED；上面的 `allow_draft=True` 仅供开发。本地命令使用 `--mode checklist --rubric examples/q011_checklist_draft.json`，其余参数与structured相同。规则中每个criterion须有checks（check_id/description/max_score/allowed_scores），子项最大分之和等于该分项最大分；子项所有可能合计须属于分项合法分档。每个criterion目前只支持一条聚合policy，多个规则须先明确合并，防止随意关联规则编号。

模型只返回子项分、理由、风险以及证据行编号。程序从原始答案生成带编号的非空行，再把选中的编号还原为精确引文和位置；分项分与总分由程序求和。原文不改空白、标点和栈名；重复行用不同编号区分。未知编号、缺项、非法分档会触发有界修复，最终失败保留null，不变成0分。兼容旧的逐字引文，但不接受模糊匹配或编造引文。

对外仍返回原items/evidence结构，新增metadata.check_results（子项结果）、risk_codes、prompt_version及evidence_protocol。行级证据可能比某个词更长，C可直接高亮整行；证据存在只证明引用来自原文，不证明判分正确。review_reasons中的流程原因与risk_codes的语义风险应分别展示；risk_codes为空不代表模型判断可靠。所有草案结果仍进入教师复核。

A优先确认：漏写非空条件但已写从S2出栈，是否按独立子项给操作分；替代流程/含糊搬移/代码笔误如何处理。B不会把开发澄清冒充教师决策。

## 8. 2026-11-11：同步组内新口径

当前Q011机器规则为011-operation-context-draft-v4，依据测试数据分支fa2866341b6c91d843c977a4ed90dcd8c3c4c5b3的通用口径，关联标签版本2026-11-10-user-approved-v1；整套机器映射仍是DRAFT。JSON规则不含046/069等答案ID与对应参考总分，不能用仓库整份题目MD替代它：MD末尾有个案标签。

新增analysis_policy=operation-context-v1时，模型须先给出algorithm_analysis：method（STANDARD/ALTERNATIVE/UNCLEAR）、invariant，以及enqueue/dequeue/empty三项operations（operation/assessment/reason/evidence）。assessment为SUPPORTED/INCOMPLETE/UNCLEAR。证据仍是原文编号，经程序还原。任一操作UNCLEAR会强制进入语义复核，不能仅因模型没输出risk码而被静默忽略。

metadata.algorithm_analysis返回这份分析；metadata.rubric_source_commit及rubric_label_version记录来源。已知不完整的入队不能同时获完整入队满分，否则触发输出修复。P3要求出队证据：当原文有清楚标题而引用只位于入队段，会被拦下。标题识别仅为保守提示，不是C/C++解析器；未分段、一行混合操作或复杂调用仍依赖语义分析，不宣称全面识别跨分支错误。

正确替代算法按equivalence_policy映射P2/P3出队功能，允许使用入队维护的不变量解释直接出队；不机械要求延迟倒栈条件。该路径的算法正确性仍由模型判断，程序不构成形式化证明。入队笔误/缺失只保留明确局部得分；FIFO概念分可独立给，不据此补齐代码。

本轮55项离线测试通过，并用上次069保存的原证据确认：入队循环借作出队搬移会被新范围检查拒绝。此检查没有重跑模型、不证明新分数正确。用户明确授权后已复测原10份，9份有效，MAE1.111；026格式失败经提示修复后单独复测成功，不能混成同版本10份结果。当前交付提示3.1版只完成该单条真实复测；不能把旧模型输出按新标签重算的MAE1.0称为本版效果。详见2026-11-11-update.md。
