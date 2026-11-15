# B模块接入与校准契约

更新：2026-11-15。本文供C接入；B不实现登录、数据库、任务恢复、规则发布和正式成绩导出。接口为同步Python函数，C应放入自己的Worker或线程池。

## 1. 最短接入路径

```python
from backend.grading import grade_submission, ChatCompletionsProvider

# request中的规则、案例、身份标记须由C从服务端固定版本记录加载。
# provider从服务端环境读取，密钥不交给前端。
result = grade_submission(request, provider=ChatCompletionsProvider.from_env())
```

完整可执行字典见`examples/integration_contract.json`中的grading_request。无付费演示：`python3 -m examples.integration_flow`。演示的教师、案例和模型输出均为合成回放，不是真实审核或校准效果。

request字段：question/rubric/answer必填；mode为structured或checklist，建议C明确填写；cases默认为空；max_attempts为1或2；allow_draft默认false。answer严格只能包含question_id/answer_id/text，附带score等标签会在调用模型前拒绝。原有grade_answer、grade_checked_answer接口保留。

真实规则须PUBLISHED，实验可显式allow_draft=true。即使规则草案通过结构检查，输出仍强制REVIEW_REQUIRED，teacher_confirmed始终false。不要通过改状态冒充教师发布；C按真实审核生成新版本。

## 2. C应保存的结果

原有schema_version=1和items/evidence结构不变。suggested_total为建议分；null不等于0分。FAILED表示调用失败，REVIEW_REQUIRED表示需复核或输出校验失败，SUGGESTED也仍是未确认建议。

metadata含：prompt_version、input_sha256、selected_case_ids、attempts、validation_errors、assessment_history。通过grade_submission新增model_calls、usage_summary和elapsed_seconds；通过checklist新增check_results、algorithm_analysis、risk_codes及规则来源。

assessment_history保留每次通过校验的评分。若第二次复核失败，第一份可供教师查看，但主结果不伪装成成功。两次评分不同会标RECHECK_SCORE_DISAGREEMENT。每份评分最多2次模型请求，结构修复和复核共用预算。

model_calls只记录请求摘要、耗时、服务返回的token用量及安全响应信息，不写密钥或异常正文。usage_reported_calls不足model_calls时用量不完整；不能把缺失用量当0费用。服务返回模型别名不等于锁定底层权重版本。HTTP错误仅记录安全状态码/代码，C可根据业务需要明确重试，不在B中无限重试。

引文位置为Python Unicode码点，end不含；JavaScript用Array.from(text).slice(start,end).join('')取原文。引用存在与教师认定支持判分是两种检查，不能混称“证据准确”。

## 3. 教师修正到可复用案例

1. C保存教师改分、逐项理由及真实原文证据，调用correction_draft生成草案。answer_only仅修正此份；general_rule另附proposed_rule，不能自动影响其他答案。
2. C完成真实身份、课程权限及审核校验，保存confirmed记录。教师确认成绩与批准复用案例是两次明确决定，不合并。
3. 规则发布后，调用case_from_confirmed(question,published_rubric,answer,confirmed,case_id=...,case_set_version=...)。
4. C持久化返回案例，并固定整个case_set_version；下一份评分提供该集合。

confirmed须包含匹配的question_id/answer_id/rubric_version、teacher_confirmed=true、完整confirmed_items；每项criterion_id/score/max_score/teacher_reason/evidence，若分项有多条规则还须rule_id。正分必须有真实原文证据。

case_from_confirmed还要求approved_for_reuse=true，以及review_id/reviewer_id/confirmed_at。B校验字段和版本，不鉴别真实用户身份；C不能直接信任浏览器上传的teacher_confirmed=true。

案例检索只接受teacher_approved=true、题号和规则版本一致、单一案例集版本；排除当前答案编号和去空白同文，最多3条。词面相似度为0的不检索。它是轻量词面检索，不是向量检索或模型训练。

仓库user_approved_applied的总分修订、teacher_score=null记录，不能直接转换为teacher_approved案例。仅总分不够构建逐项校准案例。教师在一份答案上的判断也不能未经审核升格为通用规则。

## 4. 确认后反馈与练习

```python
from backend.grading import feedback_and_practice
learning = feedback_and_practice(question, published_rubric, answer,
                                confirmed, approved_catalog, attempted_practice_ids)
```

该入口先检查确认结果与规则/答案匹配，再生成本次失分点反馈，最多推荐3道已有审核练习，排除已做题。没有实际题库就返回空，不现场编造题目或把未审核题标为通过。

题库字段：practice_id、question_id（练习题题号）、source_question_id（原评分题号）、criterion_ids、teacher_approved；题目正文/答案/难度等由A提供、C保存。兼容旧数据中question_id表示原题的格式，但新题库建议明确source_question_id。推荐只返回存在的practice_id与匹配得分点，C据此查询题目内容。

反馈只描述本次教师确认作答，不推断长期能力，不声称推荐已带来学习提升。

## 5. 联调验收

C先用合成演示核对：导入→评分→保存建议→教师逐项修正→复用批准→下一份检索→反馈练习。再提供真实已审核记录验证，所有身份与正式发布操作由C服务端完成。

必须另外验证：失败不显示完成、null不导出为0、草案不进入正式成绩、跨版本案例被拒、当前/同文答案不检索自己、修改规则后旧结果提示重审、已做练习不再推荐。B可验证函数层面，数据库/网页/正式导出和云运行必须由C完成实际联调。
