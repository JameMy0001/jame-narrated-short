#!/usr/bin/env python3
"""Produce an original Thai Jame episode in an independent local workspace."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

from PIL import ImageFont, features

import package_video as video
import production as art
from storage import FONT, MODE, Problem, Workspace
import upload_workflow as upload


def doctor():
    checks = {"python": sys.version.split()[0], "ffmpeg": shutil.which("ffmpeg"),
              "ffprobe": shutil.which("ffprobe"), "draw_things": art.artwork_cli(),
              "z_image_turbo_6_bit": (art.MODELS / art.MODEL).is_file(),
              "kanit_bold": FONT.is_file(), "pillow_raqm": features.check_feature("raqm"),
              "playwright": importlib.util.find_spec("playwright") is not None,
              "google_chrome": Path("/Applications/Google Chrome.app").is_dir(),
              "symlink_safe_cleanup": shutil.rmtree.avoids_symlink_attacks}
    if checks["ffmpeg"]:
        proc = subprocess.run(["ffmpeg", "-hide_banner", "-buildconf"], check=True,
                              capture_output=True, text=True)
        config = proc.stdout + proc.stderr
        for flag in ("libass", "libharfbuzz", "libx264"):
            checks[flag] = f"--enable-{flag}" in config
    if checks["kanit_bold"] and checks["pillow_raqm"]:
        identity = ImageFont.truetype(str(FONT), 40, layout_engine=ImageFont.Layout.RAQM).getname()
        checks["kanit_font_identity"] = identity == ("Kanit", "Bold")
    if MODE == "cartoon-short":
        worker = Path(__file__).parent / "animation"
        checks["node"] = shutil.which("node")
        checks["resvg"] = False
        if checks["node"]:
            checks["resvg"] = subprocess.run(
                ["node", "-e", "require('@resvg/resvg-js').Resvg"], cwd=worker, capture_output=True,
            ).returncode == 0
    return checks


def parser_for_cli():
    parser = argparse.ArgumentParser(description=f"{__doc__} Workflow: {MODE}.")
    parser.add_argument("--project", type=Path, default=Path.cwd(), help="Caller-selected production directory")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("doctor", help="Check installed local dependencies without opening Studio or starting a job")
    sub.add_parser("login", help="Open your dedicated Chrome sign-in profile")
    setup = sub.add_parser("setup", help="Bind the intentionally selected Studio channel")
    setup.add_argument("--channel-id", required=True)
    for name in ("enqueue", "generate", "check-batch", "queue-status"):
        cmd = sub.add_parser(name)
        cmd.add_argument("episodes", nargs="+")
    for name in ("begin", "prepare", "import-art", "reject-art", "review-art", "render",
                 "approve", "validate", "preflight", "submit", "status", "verify", "finalize"):
        cmd = sub.add_parser(name)
        cmd.add_argument("episode")
        if name == "prepare":
            cmd.add_argument("plan", type=Path)
        elif name == "import-art":
            cmd.add_argument("slot")
            cmd.add_argument("path", type=Path)
            cmd.add_argument("--generator", required=True)
            cmd.add_argument("--ref", required=True)
        elif name == "reject-art":
            cmd.add_argument("slot")
            cmd.add_argument("--reason", required=True)
        elif name in ("review-art", "approve"):
            cmd.add_argument("--notes", required=True)
        elif name == "render":
            cmd.add_argument("--preview", action="store_true", help="75-second sample for audiobook, full draft for Shorts")
        elif name == "submit":
            cmd.add_argument("--manual", action="store_true", help="Reserve one exact approved package for manual Studio attachment")
        elif name == "verify":
            cmd.add_argument("--video-id")
    return parser


def queue_status(episode):
    if episode.history():
        return upload.status(episode)
    plan, _ = art.load(episode)
    states = {}
    for slot in art.briefs(plan):
        root = episode.work / "art" / slot
        states[slot] = (
            "reviewed" if (root / "review.json").is_file() else
            "needs_art_review" if (root / "image.json").is_file() else
            "generation_started_or_interrupted" if (root / "request.json").is_file() else
            "queued" if (episode.work / "cohort.json").is_file() else "prepared"
        )
    return {"episode": episode.id, "art": states, "work": str(episode.work)}


def dispatch(workspace, args):
    action = args.action
    if action == "login":
        return upload.login(workspace)
    if action == "setup":
        return upload.setup(workspace, args.channel_id)
    if action == "enqueue":
        return art.enqueue(workspace, args.episodes)
    if action == "check-batch":
        return art.check_batch(workspace, args.episodes)
    if action == "generate":
        if len(set(args.episodes)) != len(args.episodes):
            raise Problem("List each episode once.")
        output = {}
        for identifier in args.episodes:
            episode = workspace.episode(identifier)
            plan, _ = art.load(episode)
            output[identifier] = [
                {"slot": slot, "png": str(art.slot_path(episode, slot, create=True) / "image.png"),
                 "generation_ref": art.generate_one(episode, slot)["generation_ref"]}
                for slot in art.briefs(plan)
            ]
        return output
    episode = workspace.episode(args.episode)
    if action == "begin":
        return {**art.begin(episode), "work": str(episode.work)}
    if action == "prepare":
        return art.prepare(episode, args.plan)
    if action == "import-art":
        return art.import_art(episode, args.slot, args.path, args.generator, args.ref)
    if action == "reject-art":
        art.reject_art(episode, args.slot, args.reason)
        return {"rejected": args.slot}
    if action == "review-art":
        return art.review_art(episode, args.notes)
    if action == "render":
        return {"package": str(video.render(episode, preview=args.preview))}
    if action == "approve":
        return {"package": str(video.approve(episode, args.notes)), "approved": True}
    if action in ("validate", "preflight"):
        if episode.history():
            return upload.status(episode)
        target, plan = video.validate(episode, reviewed=action == "preflight")
        return {"package": str(target), **plan}
    if action == "submit":
        return (upload.reserve_manual if args.manual else upload.submit)(episode)
    if action == "verify":
        return upload.verify(episode, args.video_id)
    if action == "finalize":
        return upload.finalize(episode)
    raise Problem("Unknown command.")


def main():
    args = parser_for_cli().parse_args()
    if args.action == "doctor":
        result = doctor()
    else:
        workspace = Workspace(args.project)
        if args.action == "status":
            result = upload.status(workspace.episode(args.episode))
        elif args.action == "queue-status":
            result = [queue_status(workspace.episode(identifier)) for identifier in args.episodes]
        else:
            with workspace.lock():
                result = dispatch(workspace, args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if args.action == "doctor" and not all(result.values()) else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, RuntimeError, KeyError, subprocess.SubprocessError) as error:
        message = str(error).splitlines()
        print(f"[FAIL] {message[0] if message else type(error).__name__}", file=sys.stderr)
        raise SystemExit(1) from None
