---
name: nlm-course-slides
description: Run a staged NotebookLM course-slides workflow from a local course JSON or a FIRA tenant/course pair. Use when Codex needs to fetch course JSON, list section resources, upload sections to NotebookLM, create one slide deck per section, monitor/rename decks, download and post-process PPTX files in the local environment, and summarize or retry only failed sections.
---

# NLM Course Slides

## Overview

默认路径不再是“一次性整课批处理”。先按阶段推进，并在关键节点由 Codex 向用户汇报、停顿、确认；脚本只负责产出阶段结果，不在脚本内部做交互式暂停。

本 skill 固定使用 CLI 和本地脚本链执行 NotebookLM 流程。不要在任何阶段询问用户选择 `MCP` 还是 `CLI`。阶段确认只用于决定是否继续，不用于决定执行通道。

默认流程分为 7 个阶段：

1. 阶段 A：获取课程 JSON 到本地
2. 阶段 B：解析 JSON，生成 section 清单
3. 阶段 C：串行上传资源到 NotebookLM
4. 阶段 D：串行发起 slide 创建请求
5. 阶段 E：轮询完成状态并重命名
6. 阶段 F：仅在用户要求时下载到本地
7. 阶段 G：汇总整体状态，并只对失败项继续处理

关键规则：

- 如果用户目标只是“下载课程 JSON”，阶段 A 完成即可结束。
- 如果用户目标还包括上传或生成 slides，阶段 A 完成后直接进入阶段 B，不在 A 后停顿确认。
- 第一次确认点固定在阶段 B 结束后。
- 默认执行通道固定为 CLI。除非用户明确要求别的方式，否则后续阶段一律沿用 CLI 脚本链。
- 用户未提供 `notebook_id` 时，在阶段 C 上传前自动创建 notebook，并把 notebook 标题与 notebook ID 汇报给用户。
- 现有 `generate_course_slides.py` / `generate_section_slides.py` 保留，但仅作为手动高级入口，不是默认推荐路径。

## Prerequisites

- 开始前确认 `nlm` 已安装并已登录；如有需要，运行 `nlm login`。
- 输入优先使用本地 JSON；如果用户给的是 `tenant_id + course_id`，先调用 `fira-insights` 的 `get_course_content_json`，再把返回的 `signed_url` 下载到本地课程目录。
- 课程结构解析继续沿用现有 manifest/FIRA 解析逻辑与 skip 规则；只有叶子 section 会进入后续阶段。
- 如果 manifest 结构不明确，读取 [references/manifest-format.md](references/manifest-format.md)。
- 如果这是共享到另一台电脑/另一个用户的首次使用，在进入阶段 C 之前先确认 3 件事：
  - 是否启用账号池；如果对方只有 1 个 Google 账号，则不要启用账号池，后续创建阶段应传 `--disable-profile-pool` 或固定使用单一 profile。若对方有多个账号，则要求他先确认每个 `nlm` profile 名称，后续用重复参数 `--profile-pool-item <profile>` 或 `--profile-pool-item <profile>:<email>` 显式传入。
  - 上传完成后是否要把 notebook 分享给其他协作者；默认不分享，只有用户明确提供邮箱时才追加 `--share-email`。
  - 下载后处理使用哪张本地图标；默认优先使用 skill 自带的 `assets/logo.png`，如果用户把图标放在别处，则要求用户给出明确文件路径并在阶段 F 传 `--postprocess-image-path`。

## Default Workflow

### 阶段 A：获取课程 JSON

目标：确保课程 JSON 已经位于本地课程目录，并写出 `course-json-report.json`。

处理方式：

- 用户已经提供本地 JSON：直接复用并复制到课程目录。
- 用户提供 `tenant_id + course_id`：先调用 `get_course_content_json`，再下载 `signed_url`。
- 阶段产物：
  - `course-json-report.json`
  - 课程目录内的 JSON 文件

命令：

```bash
python3 scripts/prepare_course_json.py \
  --source-file /path/to/course.json \
  --course-name "课程名称"
```

或：

```bash
python3 scripts/prepare_course_json.py \
  --signed-url "<signed_url>" \
  --course-name "课程名称" \
  --filename "课程内容.json"
```

执行规则：

- 如果用户的目标只是下载课程文件，到这里结束。
- 如果用户还要上传或生成 slides，不要在 A 后停顿，直接进入阶段 B。

### 阶段 B：生成 section 清单

目标：把后续会上传的 section 清晰列出来，并在这里进行第一次人工确认；同时提前展示计划用于 NotebookLM `slides create` 的提示词。

命令：

```bash
python3 scripts/list_course_sections.py \
  /path/to/course.json
```

阶段产物：

- `section-list.json`

向用户汇报时至少展示：

- `section_id`
- `section_title`
- `resource_title`
- extracted content preview for at least 1 non-intro section. Prefer sampling `1.2` when it exists.
- if the preview looks like placeholder interaction copy such as “下面是围绕所学知识点的训战互动……”, stop and repair the content-extraction rule before Stage C.
- the full NotebookLM prompt text currently planned for Stage D slide creation
- whether the prompt is recommended to stay as-is or be revised before continuing
- if revision is recommended, provide concrete suggested edits, including which audience wording, source-document framing, terminology constraints, or generation requirements should change and why

执行规则：

- A 和 B 可以连续执行。
- B 结束后必须先汇报 section 清单、计划使用的完整提示词、以及是否建议修改提示词，再询问用户是否继续进入阶段 C。
- 默认确认话术应等价于：`阶段 A/B 已完成。我已经整理好 section 清单和计划用于生成 slides 的提示词，并给出是否建议修改的判断。是否按当前版本继续进入阶段 C 上传？如果你要改提示词，我会先按你的修改更新后再继续。`
- 不允许追加“如果继续，请告诉我用 MCP 还是 CLI”之类的工具选择问题。
- 未确认前不要进入上传阶段。
- 如果用户在这里修改了提示词措辞、目标受众、来源文档说明、术语约束或生成要求，把该修改视为当前运行的权威版本，并在后续 Stage D 之前沿用更新后的提示词。

### 阶段 C：资源上传

目标：按 section 串行上传文本资源，并写出 `upload-report.json`。

命令：

```bash
python3 scripts/upload_course_sections.py \
  /path/to/course.json \
  --section-list /path/to/section-list.json \
  --notebook-id <existing-notebook-id>
```

如果没有 notebook ID，可省略 `--notebook-id`，脚本会创建 notebook：

```bash
python3 scripts/upload_course_sections.py \
  /path/to/course.json \
  --section-list /path/to/section-list.json
```

执行规则：

- 上传必须串行。
- 每节之间固定延迟 `5` 秒。
- 不并发上传。
- 本阶段固定沿用 CLI 脚本链执行，不重新选择工具方式。
- 如果脚本创建了 notebook，先把 notebook 标题和 notebook ID 汇报给用户，再继续说明上传结果。
- 如果用户提供了 notebook ID，也要先明确向用户汇报“将复用该 notebook”。
- 阶段结束后汇报成功项/失败项。
- 如果有失败项，询问用户是否只重试失败项。

阶段产物：

- `upload-report.json`

关键字段：

- `section_id`
- `section_title`
- `resource_title`
- `status`
- `source_id`
- `error`

仅重试失败项时使用：

```bash
python3 scripts/upload_course_sections.py \
  /path/to/course.json \
  --retry-failed-from-report /path/to/upload-report.json \
  --notebook-id <notebook-id>
```

### 阶段 D：发起 slide 创建

目标：只对已有 `source_id` 的 section 串行发起 `slides create` 请求，并写出 `create-report.json`。

命令：

```bash
python3 scripts/create_slides_from_sources.py \
  /path/to/course.json \
  --upload-report /path/to/upload-report.json
```

执行规则：

- `slides create` 必须串行调用。
- 每次 create 之间固定延迟 `10` 秒。
- 允许 notebook 内同时存在多个远端生成中的 slide。
- 本阶段固定沿用 CLI 脚本链执行，不重新选择工具方式。
- 如果某个 create 失败，先记录，不要在脚本内暂停。
- 阶段结束后汇报成功项/失败项。
- 如果有失败项，停下来询问用户是否只重试失败项。

阶段产物：

- `create-report.json`

关键字段：

- `section_id`
- `section_title`
- `source_id`
- `artifact_id`
- `status`
- `error`

仅重试失败项时使用：

```bash
python3 scripts/create_slides_from_sources.py \
  /path/to/course.json \
  --upload-report /path/to/upload-report.json \
  --retry-failed-from-report /path/to/create-report.json
```

### 阶段 E：轮询与重命名

目标：只针对已成功发起的 artifact 轮询远端状态；完成后立即重命名，并写出 `finalize-report.json`。

命令：

```bash
python3 scripts/monitor_and_finalize_slides.py \
  /path/to/course.json \
  --create-report /path/to/create-report.json
```

执行规则：

- 只针对已有 `artifact_id` 的 section。
- 状态轮询默认每 `60` 秒一次。
- 完成后立即重命名远端 slide。
- 本阶段固定沿用 CLI 脚本链执行，不重新选择工具方式。
- 阶段结束后汇报完成项、仍在运行项、失败项。

阶段产物：

- `finalize-report.json`

关键字段：

- `section_id`
- `artifact_id`
- `artifact_status`
- `renamed`
- `downloaded`
- `output_path`
- `error`

### 阶段 F：按需下载

目标：只有用户明确要求下载时，才把已完成的 slide deck 拉到本地。

命令：

```bash
python3 scripts/monitor_and_finalize_slides.py \
  /path/to/course.json \
  --create-report /path/to/create-report.json \
  --download
```

执行规则：

- 默认不下载。
- 只有用户明确要求“下载到本地”时才执行。
- 下载失败自动重试 `3` 次。
- 本阶段固定沿用 CLI 脚本链执行，不重新选择工具方式。
- 阶段结束后汇报下载结果。

### 阶段 G：总结与失败项再处理

目标：聚合各阶段 report，给出整体状态摘要，并指导下一步。

命令：

```bash
python3 scripts/summarize_course_slide_status.py \
  /path/to/course.json
```

阶段产物：

- `course-status-summary.json`

建议汇报内容：

- 每个 section 的当前综合状态
- 失败项列表
- 可重试项列表

结束时引导用户决定：

- 是否只对失败项重试
- 是否对已完成项执行下载
- 是否结束本轮

## Report Files

默认报告文件：

- `course-json-report.json`
- `section-list.json`
- `upload-report.json`
- `create-report.json`
- `finalize-report.json`
- `course-status-summary.json`

统一状态建议：

- `pending`
- `uploaded`
- `failed_upload`
- `create_requested`
- `failed_create`
- `running`
- `completed_remote`
- `renamed`
- `downloaded_local`
- `failed_download`

## Operational Rules

- 上传阶段：固定串行，固定 `5` 秒节流，不并发。
- 创建阶段：固定串行，固定 `10` 秒节流；允许多个远端 artifact 同时生成中。
- 轮询阶段：单独使用 `60` 秒轮询节奏。
- 下载阶段：默认关闭，仅用户明确要求时执行，失败自动重试 `3` 次。
- 失败项优先通过对应阶段 report 进行定向重试，不要默认整课重跑。
- 不要自动删除 notebook、source 或 artifact。

## Manual Advanced Entry

以下脚本仍然保留，适合用户明确要求“一次跑完”或做高级恢复操作时使用。它们仍然是 CLI 入口，不应触发额外的 MCP/CLI 选择确认：

- `scripts/generate_course_slides.py`
- `scripts/generate_section_slides.py`
- `scripts/generate_recovery_report.py`
- `scripts/verify_course_slides.py`

这些脚本不是默认推荐路径。默认应优先使用阶段 A-G。

## References

- [references/manifest-format.md](references/manifest-format.md)
- For `华为流程框架基础-人力资源管理流程` and similar Huawei HR flow-framework courses in this local environment, read [references/huawei-hr-prompt-template.md](references/huawei-hr-prompt-template.md) before proposing prompt changes or confirming the final NotebookLM prompt.

## Local Defaults

- In a shared/new-machine setup, Codex must confirm before Stage C whether the user wants notebook collaborator sharing. The default is no sharing. Only add collaborators when the user explicitly provides the email addresses.
- The generated reports now include `notebook_url` and `share_result`, so Codex can return the notebook link directly in follow-up messages.
- In this local environment, once Stage E finishes and there are no remaining failed or still-running items, Codex should proceed directly to Stage F download without asking the user for an extra confirmation.
- In this local environment, when Stage F downloads PPTX files to the local course directory, Codex should immediately post-process each downloaded deck before presenting it to the user.
- In this local environment, when Stage F downloads a section deck, Codex must also write a Markdown transcript beside the final PPTX, using the same final filename stem and the `.md` extension.
- In a shared/new-machine setup, Codex must confirm before Stage D whether the user wants a profile pool. If the user only has 1 Google account, do not enable the pool. If the user has multiple accounts, collect their `nlm` profile names first and pass them via repeated `--profile-pool-item`.
- In this local environment, when the user confirms that a profile pool is available, Stage D slide creation should use the local profile pool by default when `--profile` is not explicitly forced:
  - `worker_wly` -> `wlydsydmhmdsyd@gmail.com`
  - `worker_daba` -> `dababyturnsintoaconvertible@gmail.com`
- In this local environment, Stage C notebook creation and source upload must default to the owner account `default` -> `wuzhijian1999@gmail.com`, so the notebook owner is always the main account unless the user explicitly overrides it.
- Before Stage C or Stage D in this local environment, Codex must show the planned NotebookLM prompt wording for confirmation. At minimum, confirm the target audience, source-document framing, and any course-family-specific wording before creating new slides.
- In this local environment, Codex must not blindly reuse the previous course family's prompt wording. It must adapt the prompt to the current course family based on the fetched course title and the extracted teaching content before any new create requests.
- When showing the planned NotebookLM prompt for confirmation in this local environment, Codex must include both:
  - the full prompt text that will be used for the next create requests
  - a short summary of what changed from the default template and why those changes fit the current course
- For `华为流程框架基础-人力资源管理流程` and similar Huawei HR flow-framework courses in this local environment, Codex must separate prompt guidance into `固定项` and `可改项` when confirming or suggesting edits. Do not mix them together in a way that implies every sentence is equally negotiable.
- For that Huawei HR course family, treat the visual-effect block, white-base block, and brand-color block as fixed unless the user explicitly asks to change them. Do not propose wording changes to those blocks as part of ordinary prompt refinement.
- For that Huawei HR course family, cover-page prompting should default to the minimal negative-constraint style documented in [references/huawei-hr-prompt-template.md](references/huawei-hr-prompt-template.md): remove subtitle, audience/object text, and extra descriptive text, but do not add extra layout micromanagement such as forcing oversized titles, black-bold-large combinations, line-break rules, or other design-process instructions unless the user explicitly asks for them.
- For that Huawei HR course family, when recommending prompt edits, prefer preserving the user's previous satisfactory default style and only patch the specific recurring errors. Do not escalate from a small cover issue into a broad redesign of the prompt.
- If the user corrects the NotebookLM prompt wording in the thread, treat that correction as the source of truth for the current run and update the prompt before any new create requests.
- In this local environment, the default language wording must keep the slides primarily in Chinese, but allow English acronyms or English terms that already appear in the source lecture script, such as `AI`, `LTC`, `IPD`, `CRM`, and `ERP`. Do not introduce extra English words, English sentences, or pure-English titles that are not already present in the source material.
- For enterprise process-intelligence courses in this local environment, do not default the audience to `中国出海企业员工`. The default audience is:
  - `流程与智能化中的企业领导者`
  - `企业各个部门业务负责人`
  - `企业流程与IT部门员工`
  - `人工智能变革项目管理者`
- For enterprise process-intelligence courses in this local environment, do not describe the source document as “给中国出海企业员工的课程内容”. The default source framing is:
  - `这是企业流程智能化培训解决方案。`
  - `本解决方案旨在企业流程框架（参见企业流程框架基础解决方案）的基础上，讲解人工智能对企业流程的设计、实施、运营所产生的深刻影响。`
  - `课程为流程与智能化中的管理者和参与数智变革的人员提供数智化基础理论、实操建议和案例研究。`
- For `管理变革流程智能化` courses in this local environment, adapt the prompt wording away from generic enterprise-process wording:
  - audience should target `企业变革项目发起人`、`企业管理者`、`各部门业务负责人`、`流程与IT部门员工`、`参与数智化转型与AI变革的项目管理者`
  - source framing should explicitly describe `管理变革流程智能化培训解决方案`, emphasizing `识别、立项、诊断、规划、试点验证、推广落地与运营转化`
  - generation requirements should emphasize `管理变革流程中的关键机制、方法、判断依据、治理动作与落地路径`
  - remove stale wording copied from other course families, especially any HR-specific terminology constraints that do not belong to the current course
- In this local environment, when Stage C creates a new notebook under the owner account, it should also share that notebook with the default slide-worker collaborators as editors:
  - `worker_wly` -> `wlydsydmhmdsyd@gmail.com`
  - `worker_daba` -> `dababyturnsintoaconvertible@gmail.com`
- In this local environment, Stage D slide creation may use the owner account's create quota. The default slide-create order is `default`, then `worker_wly`, then `worker_daba`.
- The local estimate is `15` slide-deck creates per profile per day, so the pool estimate is `45` per day in total.
- When a create request fails with a quota / daily-limit style error, Codex should mark that profile as exhausted for the current day and automatically retry the same section with the next profile in the pool.
- The local per-profile usage tracker is only a success counter, not NotebookLM's official quota source of truth. A profile can still show large local remaining capacity while NotebookLM temporarily returns `RESOURCE_EXHAUSTED` for create requests.
- In this local environment, distinguish `temporary service-side create throttling` from `local estimated remaining capacity`. When reporting capacity to the user, explicitly label it as a local estimate rather than a guaranteed NotebookLM remaining quota.
- In this local environment, Codex should hold the profile-pool usage / remaining-capacity summary until the final user-facing wrap-up after download and post-processing finish; do not interrupt mid-run just to report remaining quota.
- In the final user-facing wrap-up, Codex should summarize from `create-report.json` / `course-status-summary.json` which profile created each section and the estimated same-day remaining capacity for each profile plus the pooled total remaining estimate.
- The local post-processing defaults are:
  - Download the raw PPTX to a temporary path first, not as the user-facing final file.
  - Cover the fixed lower-right NotebookLM watermark area on every slide with a same-color rectangle. Rectangle size: `3.39 cm x 0.64 cm`. Position from the slide's top-left corner: `x=41.63 cm`, `y=24.55 cm`.
  - Add the local logo image from the bundled skill asset `assets/logo.png` by default. If the user explicitly gives another image path, use that path instead. Image frame size: `4.10 cm x 1.19 cm`. Position from the slide's top-left corner: `x=40.26 cm`, `y=23.36 cm`. Keep `lockAspectRatio=true`, keep it relative to the image's original size, and use `6%` for both width scaling and height scaling.
  - Save the final user-facing PPTX with the suffix `_水印版.pptx`.
  - Do not keep `_遮挡水印` or `_加图` as the final user-facing suffixes in subsequent runs.
  - If any slide samples a non-white or mixed background color while covering the watermark area, report those slide numbers and sampled colors back to the user for quick manual verification.

## Local Reliability Notes

- In this local environment, always clear `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, and the lowercase variants before any `nlm` or NotebookLM API call.
- When the user adds a new slide worker in this local environment, update all of the following in the same turn so future chats inherit the change:
  - `scripts/common.py` -> add the worker email to `DEFAULT_NOTEBOOK_SHARE_EMAILS`
  - `scripts/common.py` -> add the profile-to-email mapping to `EXPECTED_PROFILE_EMAILS`
  - `scripts/create_slides_from_sources.py` -> add the worker to `DEFAULT_CREATE_PROFILE_POOL` in the intended create order
  - `SKILL.md` -> update the local profile pool list, default collaborator list, expected mapping notes, create order, and pooled daily-capacity estimate if the pool size changed
- In this local environment, do not stop after only editing the skill text when adding a worker. The code defaults and the skill instructions must stay aligned.
- When the user replaces one worker with another in this local environment, update the code defaults and skill text in the same turn, but also treat the new worker as operationally incomplete until all 3 checks pass:
  - the new worker's dedicated NotebookLM auth browser has been used for login
  - the saved `nlm` profile has been refreshed from that same profile-bound browser
  - the new worker has been invited to the target notebook and has passed at least one real NotebookLM API or create-path validation relevant to the current task
- Do not trust `nlm login --check` as the source of truth on this machine; it may report `expired` even when real NotebookLM RPC calls still work.
- Do not trust `check_auth(live=True)` as the only source of truth on this machine either; it can still report `expired` while the real NotebookLM API path is usable.
- The preferred auth validation order in this local environment is:
  - first, confirm the saved profile email matches the expected mapping
  - second, validate with a real NotebookLM API call such as `NotebookLMClient(...).list_notebooks()`
  - only use homepage-style auth checks as a secondary signal
- Prefer refreshing credentials from each profile's own saved browser profile.
- Never reuse an unmapped existing CDP browser across profiles; that can contaminate `default`, `worker_wly`, and `worker_daba`.
- Expected local profile mapping:
  - `default` -> `wuzhijian1999@gmail.com`
  - `worker_wly` -> `wlydsydmhmdsyd@gmail.com`
  - `worker_daba` -> `dababyturnsintoaconvertible@gmail.com`
- If a profile refresh resolves to the wrong Google account, treat it as profile contamination and repair it before continuing.
- When a user asks to "just open a login window" in this local environment, prefer the simplest path:
  - for `default`, `worker_wly`, `worker_daba`, or any newly added worker, launch that profile's dedicated NotebookLM auth browser first
  - after the user says the login is done, extract cookies from that same profile-bound CDP browser and save them directly to the matching `nlm` profile
  - avoid repeated generic browser windows or repeated account switching in a shared normal Chrome session
- When repairing NotebookLM slide names, avoid hardcoding Chinese titles or paths in PowerShell literals. Read the course JSON or manifest in UTF-8 inside Python and derive section titles there.
- When validating a freshly fetched FIRA course JSON, do not trust section titles alone. Read at least one extracted non-intro section body and confirm it matches the real teaching script before uploading anything to NotebookLM.
- For enterprise process-intelligence courses, prefer the teaching content under `赋能内容`, especially the `有声幻灯片` block, as the authoritative section body. Treat `在线训战` and similar interaction/training blocks as fallback only.
- When rebuilding authoritative create/finalize state from a live notebook, match artifacts using the first line of `custom_instructions` (`文稿题目：...`) plus `section_id`, not only the current artifact title.
- Long all-in-one finalize/download runs are fragile in this workspace. If a run is interrupted, inspect and stop leftover `monitor_and_finalize_slides.py` and `postprocess_downloaded_pptx.mjs` processes before resuming.
- Resume interrupted finalize/download work in small `--section-id` batches instead of rerunning the whole course.
- For local progress checks, count actual output files in the course directory (`*_水印版.pptx`) instead of trusting stale reports alone.
- In this local environment, NotebookLM may auto-generate English artifact titles even when the intended section title is Chinese. For recovery, rename, and download workflows, prefer the `文稿题目：...` line inside `custom_instructions` over the current artifact title.
- During slide creation in this local environment, treat transient RPC disconnects such as `WinError 10053` as retryable network errors and retry the same profile before marking the section failed.
- A newly added worker can show `share_status` as `editor` and can list notebook sources, yet still return `API error (code 3): INVALID_ARGUMENT` on slide creation. Do not treat visibility alone as proof that the worker is usable for slide generation; require one successful create on the target notebook before counting that worker as reliable capacity.
- In this local environment, after replacing a failing worker with a new worker, prefer forcing the remaining failed sections through the new worker first before resuming mixed profile-pool retries. This makes the worker validation unambiguous and reduces confusion about which account actually cleared the backlog.
