# Huawei HR Prompt Template

This reference applies to `华为流程框架基础-人力资源管理流程` and nearby Huawei HR flow-framework courses in this local environment.

Use it when:

- confirming the planned NotebookLM prompt with the user
- recommending prompt edits after a sample slide review
- rebuilding a manifest prompt after the user has corrected the wording in-thread

## What We Learned

The main prompt failures in this course family were not caused by lack of detail. They were caused by adding too many cover-page design instructions.

Observed failure modes:

- cover pages injected unwanted subtitle-like text
- cover pages injected `目标受众` / `适合谁` / `面向谁` style audience text
- cover pages injected course intro, background, value, or collaboration text
- overly strong cover instructions such as `黑色 + 加粗 + 大字号` pushed NotebookLM into giant, crowded poster-style titles
- adding too many layout-process constraints made the output less stable than the earlier default style

The preferred strategy for this course family is:

- preserve the earlier default visual style when it is already close to acceptable
- patch only the recurring unwanted cover content
- keep cover instructions short and mostly negative-constraint based
- treat visual/base/color requirements as fixed unless the user explicitly changes them

## Fixed vs Editable

When showing prompt confirmation or recommending edits, present the prompt in two buckets.

### Fixed Items

These should be treated as fixed unless the user explicitly asks to change them:

- the white-base requirement block
- the visual-effect requirement block
- the brand-color requirement block
- the overall course language rule: Chinese first, allow only source-present English acronyms/terms
- the `取消结尾页` requirement
- the rule that content must stay aligned with the source document and current section

### Editable Items

These can be revised based on the course, source content, or the user's feedback:

- target audience wording
- source-document framing
- generation requirements about focus, depth, and teaching emphasis
- cover-page restrictions beyond the fixed visual/base/color blocks
- wording that controls whether process acronyms should be allowed or suppressed in a given section

## Cover Prompt Rule

For this course family, default to the simplest stable cover patch.

Use this style:

```text
封面页要求：\
- 封面页只保留主标题，不要副标题。\
- 封面页不要出现“目标受众”“适合谁”“面向谁”等对象描述。\
- 封面页不要出现课程简介、课程背景、价值说明、协同说明等补充文字。\
- 封面页需要有与主题相关的现代企业扁平线性矢量插图或主视觉，与标题关系协调，美观。\
```

Avoid adding these unless the user explicitly requests them:

- `黑色、加粗、大字号`
- `字号适中`
- `标题断行自然`
- `图文平衡构图`
- other design-process micromanagement

Reason: these instructions look reasonable, but in practice they made NotebookLM overfit the cover layout and produce crowded or awkward titles.

## Approved Full Template

This is the approved full prompt template for the current Huawei HR course family baseline:

```text
文稿题目：<对应 section 标题，去掉编号前缀>

目标受众：

- 企业管理者
- 人力资源负责人
- HRBP与人力资源专业人员
- 各部门业务负责人
- 参与组织变革、流程优化与数字化建设的管理者

这是《华为流程框架基础-人力资源管理流程》的培训内容。\
课程围绕华为人力资源管理流程的核心逻辑展开，讲解如何把企业战略转化为组织责任、岗位要求、人才供应、干部建设、绩效激励、员工关系、共享服务与数字化基础，并说明人力资源流程如何与DSTE、IPD、ISC、ITR、IFS等关键流程协同。\
请根据来源文档生成演示文稿，演示文稿要和来源文档的逻辑与核心内容严格对应，帮助学员理解华为人力资源管理流程中的价值链、关键机制、管理动作、协同关系与落地方法。\
开篇尽量简洁，直入主题。\
必须遵循来源文档中的专业用语和定义。\
页数要求：10-16页，用适当的文字讲解主要内容。\
不要浪费页面讲口号，要讲清楚关键机制、职责分工、业务场景、管理判断、协同接口、治理动作与实践启发。\
演示文稿以中文为主，但允许保留来源文档中已经出现的英文缩写或英文术语，例如 AI、DSTE、IPD、ISC、ITR、IFS、HRBP。不要额外引入来源文档里没有出现的英文单词、英文句子或纯英文标题。\
取消结尾页。\

封面页要求：\
- 封面页只保留主标题，不要副标题。\
- 封面页不要出现“目标受众”“适合谁”“面向谁”等对象描述。\
- 封面页不要出现课程简介、课程背景、价值说明、协同说明等补充文字。\
- 封面页需要有与主题相关的现代企业扁平线性矢量插图或主视觉，与标题关系协调，美观。\

正文要求：\
- 不要把“目标受众”直接写进正文页面。\
- 不要引入来源文档里没有明确出现的流程名、缩写、英文术语、协同关系或背景概念。\
- 如果某个流程名、缩写或术语在当前 section 来源文档中没有出现，就不要为了补充背景主动写进去。\
- 严格围绕当前这一节来源文档生成，不要把整门课程的背景平均分摊到每一页。\

视觉效果要求: 遵循极简的商务视觉原则，以现代企业扁平线性矢量插图为主，构图上合理留白，营造舒适的视觉呼吸感。全文稿任何地方都严禁放任何徽标（Logo）。\
底版强制要求：全部演示文稿的底版必须统一为纯白色（#FFFFFF），底版区域禁止出现任何底纹、网格线、辅助线、水印、杂色及各类装饰性背景元素，保证底版干净无杂质。\
配图、图标等须使用品牌色，即绿色（RGB 0-176-80），橙色（RGB 255-153-0），蓝色（RGB 51-153-255），可适当加入浅绿色（RGB 97-209-116）、浅橙色（RGB 255-194-102）、浅蓝色（RGB 153-204-255），严禁使用粉色系颜色。
```

## Suggestion Format

When recommending prompt changes for this course family, use this decision pattern:

1. say which part is fixed and should not be touched
2. say which part is editable for the current run
3. prefer the smallest prompt patch that solves the observed issue
4. avoid turning one bad sample into a universal hard rule unless the user explicitly wants that

Recommended phrasing pattern:

- `固定项：...`
- `可改项：...`
- `这次建议只改：...`
- `不建议动：...`
