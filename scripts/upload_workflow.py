"""Private Studio submission, read-only reconciliation, and verified retention."""
import os
import subprocess

from package_video import validate
from production import art_records, continuity, load, narration, text
from originality import story_record
from storage import (CHANNEL, STUDIO, VIDEO_ID, Problem, check_history, directory, read,
                     remove_work, sync_dir, utc, write)
import studio


def login(workspace):
    profile = directory(workspace.profile, create=True)
    subprocess.run(["open", "-na", "Google Chrome", "--args",
                    f"--user-data-dir={profile}", f"--app={STUDIO}"], check=True)
    return {"status": "login_opened", "next": "Select your channel and quit the dedicated Chrome with Cmd+Q."}


def setup(workspace, channel):
    if not CHANNEL.fullmatch(channel):
        raise Problem("Provide the intended YouTube channel ID beginning with UC.")
    with studio.studio(workspace) as page:
        identifier = studio.check_channel(page, channel)
    write(workspace.channel_file, {"channel_id": identifier})
    return {"status": "channel_bound", "channel_id": identifier}


def status(episode):
    if history := episode.history():
        row = history
    else:
        receipt = episode.receipt()
        row = receipt or {"status": "not_submitted"}
    return {
        "episode": episode.id, **{key: row[key] for key in (
            "status", "stage", "channel_id", "video_id", "studio_url", "updated_at", "verified_at", "error"
        ) if key in row},
        "history": str(episode.history_path) if history else None,
        "cleanup_pending": bool(history and (episode.work.exists() or episode.work.is_symlink())),
    }


def save_verified(episode, video_id, url):
    if not VIDEO_ID.fullmatch(video_id) or url != f"{STUDIO}/video/{video_id}/edit":
        raise Problem("Studio did not return the expected video edit URL.")
    recorded = episode.receipt().get("video_id")
    if recorded and recorded != video_id:
        raise Problem("The verified video differs from the recorded transfer ID.")
    episode.change(status="saved_private", stage="verified", visibility="private",
                   video_id=video_id, studio_url=url, verified_at=utc(), error=None)
    return finalize(episode)


def submit(episode):
    if episode.history():
        return finalize(episode)
    old = episode.receipt()
    if old.get("status") == "saved_private":
        return finalize(episode)
    if old and (old.get("status") not in ("queued", "retryable") or old.get("video_id")):
        raise Problem("An upload may exist in Studio. Reopen it with verify using the existing video ID.")
    target, plan = validate(episode)
    channel = episode.workspace.channel()
    if old and (old.get("plan") != plan or old.get("channel_id") != channel):
        raise Problem("The package or channel changed after submission.")
    episode.change(status="queued", stage="opening_studio", plan=plan, channel_id=channel, error=None)
    try:
        with studio.studio(episode.workspace) as page:
            identifier, url = studio.upload(page, episode, target, plan, channel)
    except (Exception, KeyboardInterrupt) as error:
        latest = episode.receipt()
        outcome = "retryable" if latest["status"] == "queued" and not latest.get("video_id") else "needs_review"
        reason = str(error).splitlines()
        episode.change(status=outcome, error=(reason[0][:450] if reason else type(error).__name__))
        raise Problem(f"Studio attempt is {outcome}. Use status for the recorded reason.") from error
    return save_verified(episode, identifier, url)


def verify(episode, identifier=None):
    if episode.history():
        return finalize(episode)
    receipt = episode.receipt()
    if receipt.get("status") == "saved_private":
        return finalize(episode)
    if receipt.get("status") not in {"attaching", "uploading", "saving", "needs_review", "manual_pending"}:
        raise Problem("There is no possibly attached upload to verify. For manual use, reserve with submit --manual.")
    if identifier and receipt.get("video_id") and receipt["video_id"] != identifier:
        raise Problem("The supplied video ID differs from the recorded upload.")
    identifier = identifier or receipt.get("video_id", "")
    if not VIDEO_ID.fullmatch(identifier):
        raise Problem("Supply the existing Studio video ID with --video-id.")
    _, plan = validate(episode)
    channel = episode.workspace.channel()
    if plan != receipt.get("plan") or channel != receipt.get("channel_id"):
        raise Problem("The current upload package or bound channel differs from the attempt.")
    with studio.studio(episode.workspace) as page:
        studio.check_channel(page, channel)
        url = studio.verify_saved(page, identifier, plan)
    return save_verified(episode, identifier, url)


def reserve_manual(episode):
    if episode.history():
        return finalize(episode)
    old = episode.receipt()
    if old.get("status") == "saved_private":
        return finalize(episode)
    if old:
        if old["status"] == "manual_pending":
            return status(episode)
        raise Problem("This episode already has an upload attempt.")
    target, plan = validate(episode)
    channel = episode.workspace.channel()
    episode.change(status="manual_pending", stage="reserved_before_attachment", plan=plan, channel_id=channel)
    return {**status(episode), "package": str(target), "metadata": str(target / "youtube.json")}


def finalize(episode):
    history = episode.history()
    found = {path.name for path in episode.path.iterdir()}
    if found - {"README.md", "work"}:
        raise Problem("Unexpected caller-owned files beside work. Move them yourself before finalizing.")
    if not history:
        receipt = episode.receipt()
        if (receipt.get("status") != "saved_private" or receipt.get("stage") != "verified"
                or receipt.get("visibility") != "private"):
            raise Problem("Cleanup requires a verified saved Private Studio receipt.")
        target, plan = validate(episode)
        channel = episode.workspace.channel()
        if receipt.get("channel_id") != channel or receipt.get("plan") != plan:
            raise Problem("Saved Private receipt identifies a different channel or production package.")
        prepared, rigs = load(episode)
        info = episode.work.stat()
        history = check_history({
            **{key: receipt.get(key) for key in (
                "status", "stage", "visibility", "channel_id", "video_id", "studio_url", "verified_at", "updated_at"
            )},
            **{key: plan[key] for key in ("episode", "mode", "job", "gate", "title", "mp4_sha256", "duration_seconds")},
            "series": prepared.get("series", ""), "premise": prepared["premise"], "plot": prepared["plot"],
            "story": story_record(prepared, narration(prepared)), "art": art_records(episode, prepared),
            "cohort": read(episode.work / "cohort.json"), "continuity": continuity(prepared, rigs),
            "work_identity": [info.st_dev, info.st_ino],
        }, episode.id)
        import json
        lines = [
            f"# {text(history['title'], 'title', 100)}", "",
            f"- Episode `{episode.id}`",
            f"- Series {history['series'] or 'Standalone'}",
            f"- YouTube channel `{channel}`",
            f"- Video `{history['video_id']}`, saved as Private",
            f"- Verified {history['verified_at']}",
            f"- Duration {history['duration_seconds']:.2f} seconds",
            f"- [Open the verified video in YouTube Studio]({history['studio_url']})",
            "", history["premise"], "",
            "## Plot", "",
            *[f"- {key.title()} — {history['plot'][key]}" for key in ("hook", "conflict", "turn", "ending")],
            "", "## Verification and originality record", "", "~~~json",
            json.dumps(history, ensure_ascii=False, indent=2).replace("~~~", "\\u007e\\u007e\\u007e"), "~~~", "",
        ]
        journal = episode.work / ".retained-readme"
        write(journal, "\n".join(lines))
        # Commit the durable journal before removing anything from the work tree.
        if episode.history_path.exists() or episode.history_path.is_symlink():
            raise Problem("Episode README appeared during finalization.")
        os.replace(journal, episode.history_path)
        sync_dir(episode.path)
        sync_dir(episode.work)
        if episode.history() != history:
            raise Problem("Could not validate the saved episode README.")
    remove_work(episode, history["work_identity"])
    return status(episode)
