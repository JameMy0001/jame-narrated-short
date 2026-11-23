"""Episode intake, serial artwork queue, review, and persistent originality."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import uuid

from PIL import Image, ImageOps

from originality import art_match, normalized, png_record, story_match, story_record
from storage import (MODE, Problem, directory, file_hash, key_hash, read, slug, sync_dir, utc, write)

MODEL = "z_image_turbo_1.0_q6p.ckpt"
GENERATOR = "Draw Things Local / Z-Image Turbo 6-bit"
MODELS = Path(os.environ.get("ROCK_DRAW_THINGS_MODELS",
                            Path.home() / ".local/share/rock-spoiler/draw-things/models")).expanduser()
SIZE = (1024, 576) if MODE == "audiobook" else (576, 1024)
PLOT = {"hook", "conflict", "turn", "ending"}
VISUAL = {"subject", "action", "setting", "shot", "palette"}
VOICES = ("th-TH-NiwatNeural", "th-TH-PremwadeeNeural")


def artwork_cli():
    return os.environ.get("ROCK_DRAW_THINGS_CLI") or shutil.which("draw-things-cli")


def begin(episode):
    if episode.path.exists() or episode.path.is_symlink():
        episode.active()
        return episode.job()
    with tempfile.TemporaryDirectory(prefix=".new-", dir=episode.workspace.episodes) as staging:
        root = Path(staging) / "episode"
        root.mkdir(mode=0o700)
        (root / "work").mkdir(mode=0o700)
        job = {"episode": episode.id, "mode": MODE, "job": uuid.uuid4().hex, "started_ns": time.time_ns()}
        write(root / "work" / "job.json", job)
        sync_dir(root)
        root.rename(episode.path)
        sync_dir(episode.path.parent)
    return job


def text(value, label, limit, *, multiline=False):
    if (not isinstance(value, str) or not 0 < len(value.strip()) <= limit
            or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]" if multiline else r"[\x00-\x1f]", value)):
        raise Problem(f"{label} needs 1–{limit} characters of plain text.")
    return value.strip()


def phrases(value, label):
    if not isinstance(value, list) or not value:
        raise Problem(f"{label} needs a list of short spoken phrases.")
    result = [text(phrase, label, 260) for phrase in value]
    if any(re.search(r"[{}\\]", phrase) for phrase in result):
        raise Problem("Spoken phrases cannot include ASS control characters.")
    return result


def plan_data(data):
    if not isinstance(data, dict):
        raise Problem("The production plan must be a JSON object.")
    for key, maximum in (("title", 100), ("premise", 360), ("description", 5000)):
        data[key] = text(data.get(key), key, maximum, multiline=key == "description")
    for key in ("series", "story_title"):
        if key in data:
            data[key] = text(data[key], key, 100)
    for label, fields in (("plot", PLOT), ("visual", VISUAL)):
        info = data.get(label)
        if not isinstance(info, dict) or not fields <= info.keys():
            raise Problem(f"{label} requires {', '.join(sorted(fields))}.")
        data[label] = {key: text(info[key], key, 250) for key in sorted(fields)}
        if any(len(normalized(v)) < 6 for v in data[label].values()):
            raise Problem(f"{label} fields need specific descriptions.")
    tags = data.setdefault("tags", [])
    if not isinstance(tags, list):
        raise Problem("tags must be a list of strings.")
    data["tags"] = list(dict.fromkeys(text(tag, "tag", 100) for tag in tags))
    if any("," in tag for tag in data["tags"]) or sum(
        len(tag) + 1 + (2 if " " in tag else 0) for tag in data["tags"]
    ) > 500:
        raise Problem("Use individual tags without commas, within YouTube's 500-character total.")
    if type(data.setdefault("ai_disclosure", True)) is not bool:
        raise Problem("ai_disclosure must be true or false.")
    if "related_video" in data:
        url = text(data["related_video"], "related_video", 160)
        if not re.fullmatch(r"https://(?:www\.youtube\.com/watch\?v=|youtu\.be/)[-_A-Za-z0-9]{11}", url):
            raise Problem("Use a YouTube watch URL for related_video.")
    standard = {"audiobook": [10800, 18000], "narrated-short": [45, 60], "cartoon-short": [50, 60]}
    floor, ceiling = (7200, 43200) if MODE == "audiobook" else (2, 180)
    bounds = data.setdefault("duration_seconds", standard[MODE])
    if (not isinstance(bounds, list) or len(bounds) != 2
        or any(type(n) not in (int, float) for n in bounds) or not floor <= bounds[0] <= bounds[1] <= ceiling):
        raise Problem(f"duration_seconds must be an ordered range within {floor}–{ceiling}.")
    if MODE != "cartoon-short" and data.setdefault("voice", VOICES[0]) not in VOICES:
        raise Problem("Use a supported Thai Edge-TTS voice.")
    if MODE == "audiobook":
        data["headline"] = text(data.get("headline"), "headline", 100)
        data["layout"] = text(data.get("layout"), "layout", 100)
        if "subhead" in data:
            data["subhead"] = text(data["subhead"], "subhead", 100)
        if data.get("accent", "ivory") not in ("ivory", "gold", "lavender", "ice"):
            raise Problem("Use a supported cover accent.")
        chapters = data.get("chapters")
        if not isinstance(chapters, list) or not chapters:
            raise Problem("Supply the full audiobook as ordered chapters.")
        for chapter in chapters:
            if not isinstance(chapter, dict):
                raise Problem("Each chapter must be an object.")
            chapter["name"] = text(chapter.get("name"), "chapter name", 100)
            chapter["narration"] = phrases(chapter.get("narration"), "chapter narration")
    elif MODE == "narrated-short":
        data["narration"] = phrases(data.get("narration"), "narration")
    else:
        if not isinstance(data.get("scenes"), list) or not 1 <= len(data["scenes"]) <= 20:
            raise Problem("Supply 1–20 scenes.")
        for scene in data["scenes"]:
            if not isinstance(scene, dict) or not isinstance(scene.get("lines"), list) or not scene["lines"]:
                raise Problem("Every scene needs dialogue lines.")
            phrases([line.get("text") if isinstance(line, dict) else None for line in scene["lines"]], "dialogue")
    briefs(data)
    return data


def briefs(plan):
    if MODE != "cartoon-short":
        return {"cover": text(plan.get("art_brief"), "art_brief", 6000, multiline=True)}
    return {f"s{index:02}": text(scene.get("art_brief"), "scene art_brief", 6000, multiline=True)
            for index, scene in enumerate(plan["scenes"], 1)}


def narration(plan):
    if MODE == "audiobook":
        return [phrase for chapter in plan["chapters"] for phrase in chapter["narration"]]
    if MODE == "cartoon-short":
        return [line["text"] for scene in plan["scenes"] for line in scene["lines"]]
    return plan["narration"]


def plan_dir(episode):
    return directory(episode.work / "plan")


def prepare(episode, source):
    job = episode.active()
    if episode.receipt():
        raise Problem("An upload attempt already identifies this version of the episode.")
    if source.stat().st_mtime_ns < job["started_ns"]:
        raise Problem("Write the episode plan after begin. Use caller-owned source outside the work tree.")
    data = plan_data(read(source))
    if (MODE == "audiobook" and data["layout"] not in cover_layouts()):
        raise Problem("Choose a layout from references/layouts.md.")
    story = story_record(data, narration(data))
    compare(episode, story=story)
    with tempfile.TemporaryDirectory(prefix=".prepare-", dir=episode.work) as temp:
        staged = Path(temp)
        rigs = {}
        if MODE == "cartoon-short":
            from render_animation import load_plan
            chars = data.get("characters")
            if not isinstance(chars, dict) or not 1 <= len(chars) <= 6:
                raise Problem("Specify 1–6 illustrated SVG characters.")
            for name, char in chars.items():
                slug(name)
                if not isinstance(char, dict) or not isinstance(char.get("rig"), str):
                    raise Problem(f"Supply an original SVG rig for {name}.")
                path = Path(char["rig"]).expanduser()
                path = path if path.is_absolute() else source.parent / path
                if path.suffix.lower() != ".svg" or path.is_symlink() or not path.is_file():
                    raise Problem(f"Expected a regular SVG at {path}")
                char["rig"] = f"{name}.svg"
                shutil.copyfile(path, staged / char["rig"])
                rigs[char["rig"]] = file_hash(staged / char["rig"])
            for index, scene in enumerate(data["scenes"], 1):
                scene["background"] = f"../art/s{index:02}/image.png"
            write(staged / "plan.json", data)
            # A staged plan validates structure and rigs before the queue starts.
            load_plan(staged / "plan.json", require_backgrounds=False, workspace_root=episode.work)
        check_continuity(episode, data, rigs)
        target = directory(episode.work / "plan", create=True)
        for name in rigs:
            write(target / name, (staged / name).read_text(encoding="utf-8"))
        write(target / "plan.json", data)
        write(target / "source.json", {"plan": key_hash(data), "rigs": rigs})
    return {"episode": episode.id, "slots": list(briefs(data))}


def cover_layouts():
    from compose_cover import read_layouts
    return read_layouts()


def check_continuity(episode, plan, rigs):
    if not plan.get("series"):
        return
    for other in episode.workspace.existing():
        if other.id == episode.id:
            continue
        history = other.history()
        if history:
            previous = history.get("continuity", {})
        elif (other.work / "plan/source.json").is_file():
            old, sources = load(other)
            previous = continuity(old, sources)
        else:
            continue
        current = continuity(plan, rigs)
        if current.get("series") == previous.get("series"):
            for name in current["cast"].keys() & previous["cast"].keys():
                if current["cast"][name] != previous["cast"][name]:
                    raise Problem(f"Series character {name} changed its rig or voice from {other.id}.")


def continuity(plan, rigs):
    if MODE != "cartoon-short" or not plan.get("series"):
        return {}
    return {"series": normalized(plan["series"]), "cast": {
        name: [char["voice"], rigs[char["rig"]]] for name, char in plan["characters"].items()
    }}


def load(episode):
    episode.job()
    target = plan_dir(episode)
    data = read(target / "plan.json")
    inputs = read(target / "source.json")
    if key_hash(data) != inputs["plan"] or any(
            file_hash(target / name) != digest for name, digest in inputs["rigs"].items()
    ):
        raise Problem("Plan or rig changed. Run prepare with the updated source.")
    return data, inputs["rigs"]


def slot_path(episode, slot, create=False):
    slug(slot)
    root = directory(episode.work / "art", create=create)
    return directory(root / slot, create=create)


def optional(path):
    return read(path) if path.exists() or path.is_symlink() else None


def active_art(episode):
    folder = episode.work / "art"
    if not folder.exists():
        return []
    directory(folder)
    return [row for slot in sorted(folder.iterdir()) if (row := optional(directory(slot) / "image.json"))]


def compare(episode, *, story=None, art=None, slot=None):
    for other in episode.workspace.existing():
        history = other.history()
        if other.id == episode.id:
            arts = [row for row in active_art(other) if row["slot"] != slot] if art else []
            old_story = None
        elif history:
            arts, old_story = history["art"], history["story"]
        else:
            arts, old_story = (active_art(other) if art else []), None
            if story and (other.work / "plan/source.json").is_file():
                old, _ = load(other)
                old_story = story_record(old, narration(old))
        if story and old_story and (reason := story_match(story, old_story)):
            raise Problem(f"{reason} matches {other.id}. Change the episode premise or script.")
        if art:
            for old in arts:
                if reason := art_match(art, old):
                    raise Problem(f"{reason} matches {other.id}/{old['slot']}. Use a different generated shot.")


def enqueue(workspace, ids):
    cohort = sorted(set(ids))
    if len(cohort) != len(ids):
        raise Problem("List each episode ID once.")
    episodes = [workspace.episode(id_) for id_ in cohort]
    for episode in episodes:
        episode.active()
        load(episode)
        previous = optional(episode.work / "cohort.json")
        if previous is not None and previous != cohort:
            raise Problem(f"{episode.id} already belongs to a different batch: {', '.join(previous)}")
    for episode in episodes:
        write(episode.work / "cohort.json", cohort)
    return {"queued": cohort}


def convert_png(source, destination):
    with Image.open(source) as raw:
        original_format = raw.format
        if original_format not in ("PNG", "JPEG", "WEBP") or getattr(raw, "is_animated", False):
            raise Problem("Provide a single original PNG, JPEG or WebP from the generator.")
        if original_format == "PNG":
            if source != destination:
                shutil.copyfile(source, destination)
        else:
            ImageOps.exif_transpose(raw).convert("RGBA").save(destination, format="PNG")
    return original_format


def image_pending(root, art_source, provenance):
    with tempfile.TemporaryDirectory(prefix=".encode-", dir=root) as staging:
        temporary = Path(staging) / "image.png"
        original_format = convert_png(art_source, temporary)
        checks = png_record(temporary, SIZE)
        record = {
            **provenance, **checks, "original_format": original_format,
            "source_sha256": file_hash(art_source),
        }
        temporary.replace(root / "image.png")
        write(root / "image.json", record)
    return record


def generation_ready(episode, slot):
    episode.active()
    if episode.receipt():
        raise Problem("An upload attempt already identifies this version.")
    plan, _ = load(episode)
    brief = briefs(plan).get(slot)
    if brief is None:
        raise Problem("Unknown artwork slot.")
    if not optional(episode.work / "cohort.json"):
        raise Problem("Enqueue the complete planned set first.")
    root = slot_path(episode, slot, create=True)
    past = optional(root / "rejected.json") or []
    if past and key_hash(brief) == past[-1]["brief_hash"]:
        raise Problem("Revise the rejected image brief in the plan, then run prepare.")
    return root, brief


def generate_one(episode, slot):
    root, brief = generation_ready(episode, slot)
    if current := optional(root / "image.json"):
        return current
    pending = root / "generator-output.png"
    request = optional(root / "request.json")
    if request and request["brief_hash"] != key_hash(brief):
        raise Problem("Resolve the pending artwork before changing its brief.")
    if pending.is_symlink():
        raise Problem("Generator output must be a regular file.")
    if not pending.exists():
        cli = artwork_cli()
        if not cli or not (MODELS / MODEL).is_file():
            raise Problem("Draw Things CLI or the local Z-Image Turbo 6-bit model is missing. See README.")
        request = {"slot": slot, "generator": GENERATOR, "brief_hash": key_hash(brief),
                   "generation_ref": f"draw-things-local/{episode.id}/{slot}/{uuid.uuid4().hex}/generator-output.png",
                   "output_name": pending.name, "created_at": utc()}
        write(root / "request.json", request)
        subprocess.run([
            cli, "generate", "--local", "--offline", "--models-dir", str(MODELS),
            "--model", MODEL, "--steps", "8", "--cfg", "1", "--width", str(SIZE[0]),
            "--height", str(SIZE[1]), "--prompt", brief, "--output", str(pending),
        ], check=True)
    if request is None:
        raise Problem("The pending file has no recorded generator request.")
    record = image_pending(root, pending, request)
    pending.unlink()
    (root / "request.json").unlink(missing_ok=True)
    compare(episode, art=record, slot=slot)
    return record


def import_art(episode, slot, source, generator, reference):
    root, brief = generation_ready(episode, slot)
    if any((root / name).exists() for name in ("image.json", "request.json", "generator-output.png")):
        raise Problem("A generation already exists for this slot. Review it before another attempt.")
    if source.stat().st_mtime_ns < episode.job()["started_ns"] or source.is_symlink():
        raise Problem("Provide fresh original generator output for this episode.")
    data = {"slot": slot, "generator": text(generator, "generator", 180),
            "generation_ref": text(reference, "generation reference", 400), "created_at": utc(),
            "brief_hash": key_hash(brief), "output_name": source.name}
    record = image_pending(root, source, data)
    compare(episode, art=record, slot=slot)
    return record


def reject_art(episode, slot, reason):
    episode.active()
    if episode.receipt():
        raise Problem("Cannot replace art after an upload attempt.")
    root = slot_path(episode, slot)
    record = optional(root / "image.json") or optional(root / "request.json")
    if not record:
        raise Problem("There is no generation to reject.")
    rejected = optional(root / "rejected.json") or []
    write(root / "rejected.json", [*rejected, {**record, "reason": text(reason, "reason", 500)}])
    for name in ("image.png", "image.json", "review.json", "request.json", "generator-output.png"):
        (root / name).unlink(missing_ok=True)


def art_records(episode, plan, *, reviewed=True):
    records = []
    for slot, brief in briefs(plan).items():
        root = slot_path(episode, slot)
        record = read(root / "image.json")
        if (record["slot"] != slot or record["brief_hash"] != key_hash(brief)
                or record["png_sha256"] != file_hash(root / "image.png")):
            raise Problem(f"{slot} PNG or brief changed. Resolve the artwork and review again.")
        if reviewed:
            review = read(root / "review.json")
            if review["art"] != key_hash(record):
                raise Problem(f"{slot} review no longer matches the artwork.")
        records.append(record)
    return records


def review_art(episode, notes):
    episode.active()
    plan, _ = load(episode)
    records = art_records(episode, plan, reviewed=False)
    notes = text(notes, "visual review notes", 800, multiline=True)
    for record in records:
        compare(episode, art=record, slot=record["slot"])
    for record in records:
        write(slot_path(episode, record["slot"]) / "review.json",
              {"art": key_hash(record), "notes": notes, "reviewed_at": utc()})
    return {"reviewed": episode.id, "slots": list(briefs(plan))}


def gate(episode, *, review_originality=False):
    history = episode.history()
    if history:
        return history["gate"]
    plan, rigs = load(episode)
    art = art_records(episode, plan)
    if review_originality:
        compare(episode, story=story_record(plan, narration(plan)))
        for record in art:
            compare(episode, art=record, slot=record["slot"])
        if MODE == "cartoon-short":
            check_continuity(episode, plan, rigs)
    return key_hash([plan, rigs, art])


def check_batch(workspace, ids):
    cohort = sorted(set(ids))
    if len(cohort) != len(ids):
        raise Problem("List each episode ID once.")
    episodes = [workspace.episode(id_) for id_ in cohort]
    sig = {}
    for episode in episodes:
        history = episode.history()
        actual = history["cohort"] if history else read(episode.work / "cohort.json")
        if actual != cohort:
            raise Problem(f"Check the full queued batch for {episode.id}: {', '.join(actual)}")
        sig[episode.id] = gate(episode, review_originality=True)
    for episode in episodes:
        if not episode.history():
            write(episode.work / "batch.json", sig)
    return sig


def require_batch(episode):
    cohort = read(episode.work / "cohort.json")
    expected = read(episode.work / "batch.json")
    if set(cohort) != set(expected) or any(
        gate(episode.workspace.episode(id_)) != digest for id_, digest in expected.items()
    ):
        raise Problem("Batch inputs changed. Review and rerun check-batch for the complete set.")
    return expected[episode.id]
