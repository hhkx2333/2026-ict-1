# B 组单题文本评分 MVP（历史说明）

> 以下保留初版记录。2026-11-08新增校准、检索与评测后，以 [b-handoff.md](b-handoff.md) 为准；下文“尚未实现”不再描述当前版本。

目标仓库：`hhkx2333/2026-ict-1`。本模块从 `main` 的 `542c7fd` 建立独立分支开发。核对时 main 仅有 README 与比赛指南，“选题和思路”分支为方案文档，尚无 C 的业务代码或既有接口；因此本版给出可直接接入的 Python 契约，待 C 实现业务层后联调。现有比赛指南和方案分支保持原样。

## 快速运行

Python 3.10+，只使用标准库，无需安装依赖。在仓库根目录执行：

```sh
python3 -m unittest discover -s tests -v
python3 -m examples.demo
```

离线演示输出四个分项、总分 3/8、`SUGGESTED`。它回放人工编写的模拟模型输出，不会识别答案语义，不代表模型准确率。所有样例都是合成数据，规则未经过教师审核，`PUBLISHED` 仅用于模拟已发布版本的接口状态。

重新生成完全一致的模拟输入与输出：

```sh
python3 -m examples.generate_fixture
```

## C 的 Python 接口

```python
from backend.grading import grade_answer, ChatCompletionsProvider, ValidationError

provider = ChatCompletionsProvider.from_env()
try:
    result = grade_answer(question, rubric, answer, [], provider=provider)
except ValidationError as exc:
    # 调用方输入错误：在业务 API 层映射成参数错误，勿重试模型。
    ...
```

`question/rubric/answer` 是可序列化 JSON 对象，完整字段实例见 `examples/synthetic.json`。question 包含 `question_id/text/max_score`；answer 包含 `question_id/answer_id/text`；rubric 包含 `question_id/version/status/criteria`。每项 criterion 包含 `criterion_id/description/max_score/allowed_scores/rules`；rules 是 `rule_id -> 教师规则文字` 的映射。题号必须匹配，细则必须标记 `PUBLISHED`，各项最高分之和等于题目总分，允许分档必须覆盖 0 和最高分。

`cases` 保留前期约定的位置，仅接受 `None` 或 `[]`；非空会明确报错，避免误以为校准已生效。provider 为必需的关键字参数，绝不默认使用模拟服务。provider 实现 `complete(system: str, payload: dict) -> str`，返回纯 JSON 字符串。若团队已有模型调用封装，直接实现此小接口即可。

输出含 `schema_version/question_id/answer_id/rubric_version/case_set_version/items/suggested_total/review_reasons/status/teacher_confirmed/metadata`。各 item 含分项编号、建议分、引文与起止位置、rule_id、理由和风险标记。返回顺序固定为规则顺序。

| 情况 | status | suggested_total / items | C 应如何处理 |
| --- | --- | --- | --- |
| 全部校验通过且无风险标记 | SUGGESTED | 建议总分 / 全部分项 | 展示为待教师确认的建议 |
| 输出合法但有风险标记 | REVIEW_REQUIRED | 建议总分 / 全部分项 | 进入人工复核队列 |
| 空答案或校验修复耗尽 | REVIEW_REQUIRED | null / [] | 展示原因，人工处理，不补零分 |
| 模型超时、限流、网络或适配层异常 | FAILED | null / [] | 由任务层决定稍后重试 |
| 调用参数或评分细则非法 | 抛 ValidationError | 无结果 | 修复数据或返回参数错误 |

模型输出最多调用两次（首次 + 一次带校验错误反馈的修复）；`max_attempts=1` 可禁用修复。传输异常立即失败，业务任务重试由 C 负责，避免隐藏的调用倍增。模型输出不得自报总分；本模块按合法分档使用十进制汇总。校验失败不会保留部分分数。

引文位置基于**原始字符串的 Python Unicode 码点索引**，从 0 开始，end 不包含在引用内；不做空白、换行或 Unicode 归一化。前端 JavaScript 使用 UTF-16，含 emoji 时不能直接 `text.slice(start,end)`，可使用 `Array.from(text).slice(start,end).join('')`。正分必须有引文；零分允许无引文但必须有说明。引文匹配与规则编号校验仅保证结构和溯源合法，**不能证明语义判分正确或引文足以支持判定**。

所有结果 `teacher_confirmed=false`。C 应独立保存教师最终分，不把 `SUGGESTED` 当正式成绩；需要在有分数时显式检查 `suggested_total is not None`，合法 0 分不能当作失败。本接口同步阻塞，FastAPI 集成应放线程池或任务 Worker。数据保存、课程权限、规则发布、版本历史和正式导出均由 C 实现。

metadata 记录输入 SHA-256、提示词版本、模型名称、调用次数和校验失败原因。不返回密钥或原始服务异常。业务侧保存输入快照与结果可供追溯；生产日志和访问控制由 C 管理。离线回放可精确复现，真实模型即便 temperature=0 也不保证逐字一致。

## 真实模型接入（需团队配置并验证）

适配层提供显式 HTTPS Chat Completions 协议：Bearer 鉴权、JSON 输出模式、temperature=0、max_tokens=4096、默认单次超时 30 秒。没有预设华为 MaaS 的端点、区域、模型名或鉴权方式。服务不兼容此协议时，应替换 provider，不能声称已完成华为云连通。

在服务端环境设置以下变量；不要把真实值写入仓库或前端：

```text
GRADING_ENDPOINT=完整的HTTPS chat/completions接口地址
GRADING_MODEL=已验证可调用的模型标识
GRADING_API_KEY=服务端密钥
```

然后运行 `python3 -m examples.demo --live`。该命令会向配置的服务发送合成题目和答案。本轮没有真实模型密钥，未执行线上调用；HTTP 适配层通过模拟响应测试。

## 验证与后续

自动测试覆盖：成功路径、完整性与重复分项、非法档位与非有限数、虚构引用与错误位置、空答案、Unicode、分项排序、小数汇总、复核标记、修复上限、失败无默认分数、异常脱敏、输入关联、HTTP 请求与超时配置。

下一步依赖：

1. C 审阅本接口契约，在业务分支接入；通过代码审查合并 B 的独立分支。
2. 对齐 C 的真实数据模型和错误映射，用单条任务完成业务集成测试。
3. 配置并验证实际模型服务；由 A 组织教师审核规则与开发样本，人工检查引文支持性和语义评分效果。
4. 待单题评分验收后，再做教师校准、RAG、评测基线与批量风险分流。

本轮未实现校准、RAG、数据库、Web API、部署、教师确认或自动练习推荐；未给出真实模型效果数字。
