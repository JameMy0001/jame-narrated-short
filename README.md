# Jame Thai narrated novel Short

Install this whole folder as one selectable skill. For Codex, copy the directory into `~/.agents/skills/jame-narrated-short` and open a new chat. Invoke `$jame-narrated-short` or ask for the production format in [SKILL.md](SKILL.md). The CLI also works independently from a terminal.

## Choose the image route before setup

Use the choice the user already made for this workflow or their saved preference. If neither exists, ask which route they want and wait for an answer before selecting a model or generating artwork. Script, plan and image brief work can continue while waiting.

1. **Draw Things Local / Z-Image Turbo 6-bit.** This is the package's default local model when the user selects the Draw Things route. It runs offline with eight steps and CFG 1. Run the read-only check below before installing or downloading anything. Use existing CLI and model weights. If a component is missing, explain which one and wait for explicit approval before installing or downloading it.
2. **Continue without Z-Image Turbo.** Keep working on the script, plan and briefs. Use `import-art` with fresh user-supplied artwork or fresh output from an already-available compatible generator explicitly selected by the user. Pause artwork if suitable inputs are unavailable. Follow this same path if the user declines a missing Draw Things component.

Automatic installation, downloads and cloud fallback are prohibited.

For the Draw Things route, run this from the skill directory in your current Python environment or an existing virtual environment.

```sh
python scripts/produce.py doctor
```

If `draw_things` reports the CLI path and `z_image_turbo_6_bit` is `true`, reuse them. If they are already installed elsewhere, set `ROCK_DRAW_THINGS_CLI` or `ROCK_DRAW_THINGS_MODELS` and check again. If a missing Python import prevents `doctor` from starting, identify that prerequisite and wait for approval before installing it.

Thai narration uses Edge-TTS, which requires internet. It is a separate step from offline image generation.

## Install missing requirements after approval

Use Python 3.11 or newer, FFmpeg and ffprobe with libass, HarfBuzz and libx264, and the Kanit Bold TTF in `~/Library/Fonts/Kanit-Bold.ttf`. Set `ROCK_KANIT_FONT` for an alternate path to the same font. Pillow needs RAQM. Google Chrome and your YouTube Studio sign-in are needed for Private upload. For missing dependencies, run only the approved setup commands.

```sh
brew install ffmpeg
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

With approval to install Draw Things, use the official CLI. Run the following command only with approval to download the model and its companion files.

```sh
draw-things-cli models ensure \
  --models-dir "$HOME/.local/share/rock-spoiler/draw-things/models" \
  --model z_image_turbo_1.0_q6p.ckpt
```

Rerun `python scripts/produce.py doctor` after any approved setup.

## Produce locally

Run from this skill folder with an activated environment. Use a production directory you control. The CLI creates an isolated workspace under `production/.rock-spoiler-work/narrated-short/`. Add `.rock-spoiler-work/` to that project's ignore file if it is tracked separately.

```sh
mkdir -p production
python scripts/produce.py --project production begin new-episode
# Author plan.json under the reported work path. See references/plan.md.
python scripts/produce.py --project production prepare new-episode production/.rock-spoiler-work/narrated-short/episodes/new-episode/work/plan-draft.json
python scripts/produce.py --project production enqueue new-episode
# If the user selected Draw Things, generate the queued artwork.
python scripts/produce.py --project production generate new-episode
# For the other route, use import-art as described below instead.
python scripts/produce.py --project production queue-status new-episode
# Open each artwork at its returned path before marking it reviewed.
python scripts/produce.py --project production review-art new-episode --notes 'Checked the scene, full bleed and mobile caption space'
python scripts/produce.py --project production check-batch new-episode
python scripts/produce.py --project production render new-episode --preview
python scripts/produce.py --project production render new-episode
# Open the MP4 with audio and inspect it before approving.
python scripts/produce.py --project production approve new-episode --notes 'Checked playback, pacing and Thai caption timing'
python scripts/produce.py --project production preflight new-episode
```

The serial `generate` command processes queued artwork in the foreground. For a batch, pass every ID to `enqueue` and `check-batch`. An existing slot is reused for the same episode. `queue-status` shows local state. Use `reject-art ID SLOT --reason 'specific visible defect'`, make the shot change in the plan, and run `prepare` before requesting a replacement. On generation failure, resolve the cause before resuming the slot. Ask for approval if the fix requires an installation or download. If the generator already wrote its result, the next run picks up that result. Keep `work` for episode-owned files only.

For fresh user-supplied artwork or results from a compatible already-available generator that the user explicitly selects, use `import-art ID SLOT PATH --generator 'Tool / model' --ref 'actual output identifier'`. Slots are `cover`. The result must be generated for this job. The package preserves the original file hash and format and converts JPEG or WebP into a working PNG when needed. Caller-owned input files remain with the caller.

See [Studio setup and recovery](references/studio.md) for Private submission and the README-only completion state. See [artwork review](references/artwork.md) before approving an image.
