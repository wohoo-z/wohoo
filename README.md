# wohoo

This repository is used to share the Codex skill `nlm-course-slides` with the team.

## Included

- `shareable-skill/nlm-course-slides/`

## What This Skill Does

`nlm-course-slides` runs a staged NotebookLM workflow for course slide generation:

1. Prepare a local course JSON.
2. Extract the section list.
3. Upload section sources to NotebookLM.
4. Create slide decks section by section.
5. Poll status and rename completed artifacts.
6. Download decks on demand and post-process local PPTX files.
7. Summarize final status and retry only failed items when needed.

## Install

1. Copy `shareable-skill/nlm-course-slides` into your local Codex `skills` directory.
2. Keep `assets/logo.png` in place if you want the default PPT post-processing logo.
3. Ensure `nlm` is installed and that `nlm login` works on your machine.

## Team Defaults

- Safe default is a single `default` profile.
- Only enable a profile pool after explicitly providing your own `nlm` profile names.
- Notebook sharing is off by default unless email addresses are explicitly provided.

See `shareable-skill/nlm-course-slides/README.md` for first-use instructions and example prompts.
