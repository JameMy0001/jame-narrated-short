"""Render the selected format and bind the upload plan to the exact reviewed files."""
import asyncio
import math
from pathlib import Path
import shutil
import subprocess
import tempfile

from PIL import Image, ImageFont

import media
from originality import story_record
from production import art_records, gate, load, narration, require_batch
from storage import (FONT, MODE, Problem, directory, file_hash, key_hash, read, stamp, utc, write)

SHAPE = (1920, 1080) if MODE == "audiobook" else (1080, 1920)
VIDEO = "master.mp4" if MODE == "audiobook" else "short.mp4"
THUMBNAIL = "cover_master.png" if MODE == "audiobook" else None
MAX_BYTES = 256_000_000_000


def caption_style(plan):
    if MODE == "audiobook":
        from compose_cover import read_layouts
        _, (left, _, right, bottom), _, centered = read_layouts()[plan["layout"]]
        return {"shape": SHAPE, "font_size": 50, "margins": (left, 1920 - right, 1080 - bottom),
                "centered": centered}
    return {}


def audio_segments(plan, workspace, cache, preview=False):
    """Use actual decoded samples to construct subtitle and chapter offsets."""
    style = caption_style(plan)
    left, right, _ = style.get("margins", (media.MARGIN_LEFT, media.MARGIN_RIGHT, media.MARGIN_BOTTOM))
    font = ImageFont.truetype(str(FONT), style.get("font_size", media.FONT_SIZE), layout_engine=ImageFont.Layout.RAQM)
    groups = plan["chapters"] if MODE == "audiobook" else [{"name": "", "narration": plan["narration"]}]
    elapsed = 0
    cues, chapters, files = [], [], []
    for index, group in enumerate(groups):
        chapters.append([group["name"], round(elapsed * 1000 / media.SAMPLE_RATE)])
        all_phrases = group["narration"]
        for offset in range(0, len(all_phrases), 32):
            lines = all_phrases[offset:offset + 32]
            captions = [media.wrap_phrase(line, font, SHAPE[0] - left - right - 16) for line in lines]
            pause = media.END_PAUSE_MS if offset + len(lines) == len(all_phrases) else 0
            group_id = key_hash([index, offset, lines, plan["voice"], pause])
            folder = cache / group_id
            if not folder.exists():
                print(f"[INFO] Thai dubbing chapter {index + 1}, phrases {offset + 1}–{offset + len(lines)}.", flush=True)
                with tempfile.TemporaryDirectory(prefix=".dub-", dir=cache) as temp:
                    entry = Path(temp) / group_id
                    entry.mkdir()
                    voice = asyncio.run(media.synthesize_story(lines, plan["voice"]))
                    local, length = media.assemble_audio(voice, captions, entry / "audio.wav", pause_ms=pause)
                    write(entry / "cues.json", {
                        "times": [[start, end] for start, end, _ in local],
                        "samples": round(length * media.SAMPLE_RATE), "audio": file_hash(entry / "audio.wav"),
                    })
                    entry.rename(folder)
            directory(folder)
            result = read(folder / "cues.json")
            wav = folder / "audio.wav"
            if result["audio"] != file_hash(wav):
                raise Problem("A cached narration segment changed.")
            files.append(wav)
            elapsed_ms = round(elapsed * 1000 / media.SAMPLE_RATE)
            for (start, end), caption in zip(result["times"], captions, strict=True):
                cues.append((elapsed_ms + start, elapsed_ms + end, caption))
            elapsed += result["samples"]
            if preview and elapsed >= 75 * media.SAMPLE_RATE:
                break
        if preview and elapsed >= 75 * media.SAMPLE_RATE:
            break
    media.write_captions(cues, workspace / "subtitles.srt", workspace / "captions.ass", **style)
    # Relative paths avoid quoting caller-owned paths in FFmpeg's concat syntax.
    links = workspace / "audio-segments"
    links.mkdir()
    for number, path in enumerate(files):
        (links / f"{number}.wav").hardlink_to(path)
    (workspace / "audio.ffconcat").write_text("ffconcat version 1.0\n" + "".join(
        f"file audio-segments/{number}.wav\n" for number in range(len(files))
    ), encoding="utf-8")
    return elapsed / media.SAMPLE_RATE, chapters


def image_movie(episode, plan, staging, preview):
    original = episode.work / "art/cover/image.png"
    if THUMBNAIL:
        from compose_cover import compose
        still = staging / THUMBNAIL
        compose(original, plan["layout"], plan["headline"], plan.get("subhead"),
                plan.get("accent", "ivory"), still)
        if preview:
            with Image.open(still) as master:
                master.resize((320, 180), Image.Resampling.LANCZOS).save(staging / "cover-review-320.png")
    else:
        still = original
    cache = directory(episode.work / "voice", create=True)
    duration, chapters = audio_segments(plan, staging, cache, preview=preview)
    if preview:
        duration = min(75, duration)
    else:
        media.check_duration(duration, plan["duration_seconds"], "Narration")
    (staging / "fonts").mkdir()
    shutil.copyfile(FONT, staging / "fonts" / "Kanit-Bold.ttf")
    out = "preview.mp4" if preview else VIDEO
    vf = f"scale={SHAPE[0]}:{SHAPE[1]}:flags=lanczos,setsar=1,ass=captions.ass:fontsdir=fonts:shaping=complex"
    subprocess.run([
        "ffmpeg", "-v", "error", "-xerror", "-loop", "1", "-framerate", "30", "-i", str(still),
        "-f", "concat", "-safe", "1", "-i", "audio.ffconcat", "-map", "0:v:0", "-map", "1:a:0",
        "-vf", vf, "-pix_fmt", "yuv420p", "-c:v", "libx264", "-threads:v", "4", "-preset", "fast",
        "-crf", "19", "-t", f"{duration:.6f}", "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart", out,
    ], cwd=staging, check=True)
    return duration, chapters


def chapter_lines(chapters, length):
    seconds = [offset // 1000 for _, offset in chapters]
    if len(seconds) < 3 or any(b - a < 10 for a, b in zip(seconds, [*seconds[1:], length])):
        return []
    def label(start):
        clock = media.timestamp(start * 1000).split(",")[0]
        return clock[3:] if start < 3600 else clock
    return [f"{label(start)} {name}" for (name, _), start in zip(chapters, seconds)]


def metadata(episode, plan, duration, chapters):
    parts = [plan["description"]]
    if THUMBNAIL:
        if lines := chapter_lines(chapters, math.floor(duration)):
            parts.append("\n".join(lines))
    elif "#shorts" not in plan["description"].casefold().split():
        parts.append("#Shorts")
    if related := plan.get("related_video"):
        parts.append(f"Related video {related}")
    # Durable unique label for resolving an interrupted transfer by its Studio metadata.
    parts.append(f"Episode ID {episode.id} / {episode.job()['job']}")
    description = "\n\n".join(parts)
    if len(description) > 5000:
        raise Problem("Shorten the description to leave room for chapters and the episode ID.")
    return dict(title=plan["title"], description=description, tags=plan["tags"], ai_disclosure=plan["ai_disclosure"],
                made_for_kids=False, visibility="private", video=VIDEO, thumbnail=THUMBNAIL)


def output_dir(episode, digest, preview=False):
    root = directory(episode.work / ("previews" if preview else "renders"), create=True)
    return root / digest


def render(episode, preview=False):
    episode.active()
    if episode.receipt():
        raise Problem("An upload attempt already identifies this version.")
    if preview and MODE != "audiobook":
        preview = False
    signature = require_batch(episode)
    plan, _ = load(episode)
    target = output_dir(episode, signature, preview)
    if target.exists():
        directory(target)
        if preview:
            file_hash(target / "preview.mp4")
        else:
            validate(episode, reviewed=False)
        return target
    with tempfile.TemporaryDirectory(prefix=".render-", dir=target.parent) as temp:
        staging = Path(temp) / "output"
        if MODE == "cartoon-short":
            from render_animation import render as animate
            duration = animate(episode.work / "plan/plan.json", staging, episode.work)
            chapters = []
        else:
            staging.mkdir()
            duration, chapters = image_movie(episode, plan, staging, preview)
            for name in ("fonts", "audio-segments"):
                shutil.rmtree(staging / name)
            (staging / "audio.ffconcat").unlink()
            (staging / "captions.ass").unlink()
        if preview:
            media.verify_video(staging / "preview.mp4", (duration - .06, duration + .06), SHAPE)
        else:
            duration = media.verify_video(staging / VIDEO, plan["duration_seconds"], SHAPE)
            write(staging / "youtube.json", metadata(episode, plan, duration, chapters))
            if chapters:
                write(staging / "chapters.json", chapters)
            names = [VIDEO, "youtube.json", "subtitles.srt", *([THUMBNAIL] if THUMBNAIL else [])]
            write(staging / "package.json", {
                "episode": episode.id, "gate": signature, "duration_seconds": duration,
                "hashes": {name: file_hash(staging / name) for name in names},
            })
        if gate(episode, review_originality=True) != signature:
            raise Problem("Production inputs changed during rendering.")
        staging.rename(target)
    return target


def validate(episode, *, reviewed=True):
    episode.active()
    signature = require_batch(episode)
    if gate(episode, review_originality=True) != signature:
        raise Problem("Artwork or script changed since check-batch.")
    plan, rigs = load(episode)
    target = directory(output_dir(episode, signature))
    proof = read(target / "package.json")
    expected_names = {VIDEO, "youtube.json", "subtitles.srt", *([THUMBNAIL] if THUMBNAIL else [])}
    if proof["episode"] != episode.id or proof["gate"] != signature or set(proof["hashes"]) != expected_names:
        raise Problem("The production package does not match the gated episode.")
    stamps = {name: stamp(target / name) for name in expected_names}
    if stamps[VIDEO][2] > MAX_BYTES:
        raise Problem("Video exceeds the YouTube 256 GB upload limit.")
    if any(file_hash(target / name) != digest for name, digest in proof["hashes"].items()):
        raise Problem("A packaged asset changed after rendering.")
    if reviewed and read(episode.work / "final-review.json")["proof"] != key_hash(proof):
        raise Problem("Run approve after reviewing this rendered version.")
    if THUMBNAIL:
        with Image.open(target / THUMBNAIL) as cover:
            if cover.format != "PNG" or cover.size != SHAPE:
                raise Problem("The final cover must be a 1920×1080 PNG.")
            cover.verify()
    duration = media.verify_video(target / VIDEO, plan["duration_seconds"], SHAPE)
    chapters = read(target / "chapters.json") if MODE == "audiobook" else []
    fields = read(target / "youtube.json")
    if (duration != proof["duration_seconds"] or fields != metadata(episode, plan, duration, chapters)
            or any(stamp(target / name) != info for name, info in stamps.items())):
        raise Problem("Package changed or metadata disagrees with the verified production data.")
    return target, {
        **fields, "episode": episode.id, "mode": MODE, "job": episode.job()["job"], "gate": signature,
        "duration_seconds": duration, "assets": stamps, "mp4_sha256": proof["hashes"][VIDEO],
        "metadata_sha256": proof["hashes"]["youtube.json"],
    }


def approve(episode, notes):
    from production import text
    if episode.receipt():
        raise Problem("An upload attempt already identifies this version.")
    target, _ = validate(episode, reviewed=False)
    write(episode.work / "final-review.json", {
        "proof": key_hash(read(target / "package.json")), "reviewed_at": utc(),
        "notes": text(notes, "final review notes", 800, multiline=True),
    })
    return target
