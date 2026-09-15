# nlm-course-slides

## Install

1. Copy this `nlm-course-slides` folder into your local Codex `skills` directory.
2. Keep the logo file at `assets/logo.png`.
3. Ensure `nlm` is installed and that you can run `nlm login`.
4. Stage F also writes one Markdown transcript per downloaded section deck, using the same final filename stem as the final PPTX.

## First Use

Before running the skill, tell Codex:

- whether you have only 1 Google account or want to use a profile pool
- whether the NotebookLM notebook should be shared with collaborators
- whether the default logo `assets/logo.png` should be used for PPT post-processing

## Recommended First Message

```text
Please use the nlm-course-slides skill for this course.

Before running, use these settings:
1. I only have 1 Google account, so do not enable a profile pool.
2. Do not share the notebook with any collaborators.
3. Use the bundled logo at assets/logo.png for post-processing.
```

## Multi-account Example

```text
Please use the nlm-course-slides skill for this course.

Before running, use these settings:
1. Enable a profile pool. My nlm profiles are: default, worker_a, worker_b.
2. Do not share the notebook unless I explicitly provide email addresses.
3. Use the bundled logo at assets/logo.png for post-processing.
```

## Download Outputs

- Each downloaded section produces a final PPTX and a same-stem Markdown transcript sidecar.
- Example: `01 Intro_水印版.pptx` also generates `01 Intro_水印版.md`.
