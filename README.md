# wohoo

This repository is used to share Codex skills with the team.

## Included

- `shareable-skill/nlm-course-slides/`
- `shareable-skill/ppt-to-imagesgallery/`

## What These Skills Do

`nlm-course-slides` runs a staged NotebookLM workflow for course slide generation:

1. Prepare a local course JSON.
2. Extract the section list.
3. Upload section sources to NotebookLM.
4. Create slide decks section by section.
5. Poll status and rename completed artifacts.
6. Download decks on demand and post-process local PPTX files.
7. Summarize final status and retry only failed items when needed.

`ppt-to-imagesgallery` converts a PPT plus aligned manuscript into a publishable `imagesgallery` package:

1. Render slide images.
2. Split and align the full manuscript page by page.
3. Validate continuity and page boundaries.
4. Synthesize audio and timestamps.
5. Prepare outputs for downstream Studio publishing.

## Install

1. Copy the skill folder you need from `shareable-skill/` into your local Codex `skills` directory.
2. For `nlm-course-slides`, keep `assets/logo.png` in place if you want the default PPT post-processing logo.
3. For `nlm-course-slides`, ensure `nlm` is installed and that `nlm login` works on your machine.
4. For `ppt-to-imagesgallery`, also install the paired `studio-imagegallery-publish` skill if you plan to publish to Studio.

## Team Defaults

- Safe default is a single `default` profile.
- Only enable a profile pool after explicitly providing your own `nlm` profile names.
- Notebook sharing is off by default unless email addresses are explicitly provided.

See each skill's local `README.md` for first-use instructions and example prompts.
