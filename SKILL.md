---
name: jame-narrated-short
description: "Produce a Thai narrated novel YouTube Short with one fresh portrait still. Use for คลิปช็อต, คลิปสั้น, นิยายแนวตั้ง, and Short adaptations or teasers from an existing story."
---

# Thai narrated novel Short

## Choose the image route

Honor the user's explicit choice for this workflow or their saved preference. If neither exists, ask which route they want and wait for their choice before selecting an image model or generating artwork. You can write the script, plan and briefs while waiting.

1. **Draw Things Local / Z-Image Turbo 6-bit.** Use this package's default local model if selected. Run the read-only `python scripts/produce.py doctor` before any installation or download. Reuse the installed CLI and weights offline. If the CLI, weights or a prerequisite is missing, name it and wait for explicit approval before installing or downloading it. If the user declines, continue with the script, plan and briefs and pause artwork until suitable assets are available.
2. **Continue without the bundled workflow's local model.** Continue the script, plan and briefs without downloading or using Z-Image Turbo. Use `import-art` for fresh user-supplied images or fresh output from a compatible already-available generator the user explicitly selects. Pause artwork until such inputs are available.

Use only the route selected by the user. Automatic installations, downloads and cloud fallback are prohibited.

Thai Edge-TTS is the online narration service. It supplies SentenceBoundary timing for Kanit Bold subtitles shaped with libass/HarfBuzz.

Example request

> ทำช็อตนิยายแนวตั้ง 3 คลิปจากคนละฉากของเรื่องที่ให้มา

Follow [setup and commands](README.md) and the [plan format](references/plan.md). Run this package's CLI with the caller's production directory. Write new episode-owned drafts inside the `work` directory reported by `begin`. Keep caller-supplied manuscripts, model and font files, and reusable original rig inputs outside that directory.

- Default to one 45–60 second episode. Start with the incident, use one focused conflict and a turn, and finish the scene. An adaptation keeps the supplied world and characters and uses a newly written incident or an unused scene. Record the original story or manuscript name in the description.
- Register the whole requested set of episode IDs before generating. Prepare every script and brief, generate one native portrait PNG per episode, open all images, then pass `check-batch` for the exact requested set before any Edge-TTS call or rendering. If a requested image fails, keep the rest of the batch and repair that slot.
- Render the 1080×1920 MP4 and open the beginning, middle and end with sound. Confirm spoken Thai, two-line subtitle readability, timing, mobile safe area, and the intended ending before approval.
- Include a related YouTube video link when the requested teaser has a published full story. Set Studio's Related video or select a thumbnail frame in the YouTube app when needed. The Shorts upload has no separate cover attachment.

Use only newly generated full-bleed scene art. Review with [artwork acceptance criteria](references/artwork.md), then bind the approval to the rendered version with `approve`.

After final review, run local `preflight`. For an authorized final Private upload use [Studio and retention](references/studio.md). For local-only, artwork-only, or preview work, stop at the requested local artifact. After a saved Private verification the CLI retains the episode README and removes episode-owned media and state.
