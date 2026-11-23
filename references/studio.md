# Save and verify as Private

Use Google Chrome with the CLI's dedicated local profile. Run these commands with the same `--project` as production. Sign in yourself, select the intended channel and quit the dedicated Chrome with Cmd+Q. The channel ID starts with UC and appears in the Studio dashboard URL.

```sh
python scripts/produce.py --project production login
python scripts/produce.py --project production setup --channel-id UC_CHANNEL_ID
python scripts/produce.py --project production preflight new-episode
python scripts/produce.py --project production submit new-episode
python scripts/produce.py --project production status new-episode
```

`submit` attaches one approved file in the foreground, sets title, description, tags, audience and AI disclosure, and clicks Save after transfer with Private selected. It reopens the exact Studio video and checks saved visibility and metadata under the bound channel. For landscape masters, it attaches the same cover, making an episode-local temporary PNG upload copy if Studio's size limit requires it. For both Shorts, select a frame in the YouTube app if required. The default audience is not made for kids and the default AI disclosure is Yes.

A failure before any file attachment can be retried using the same approved package. After attachment begins, any interruption, draft or ambiguity requires reconciliation. Open Studio Content under the bound channel and compare the unique Episode ID in the description. If the exact entry exists, finish saving it as Private in Studio if needed. Run the read-only check below. A pending transfer, failed readback, uncertain channel, unmatched metadata or wrong video ID preserves all files. The CLI blocks a second attachment after an uncertain attempt.

```sh
python scripts/produce.py --project production verify new-episode --video-id EXISTING_ID
```

For manual Studio attachment, use `submit new-episode --manual` after preflight and attach the reserved package once. Copy the title, full description including its unique Episode ID, tags, audience and AI setting from `youtube.json`. Open the saved Private video and run `verify`. The reservation blocks another CLI attachment. The operator must avoid a second attachment in Studio.

A verified saved Private receipt triggers the episode README write and validation, then removes the episode-owned `work` tree. The retained README has the title, distinguishing story beats, Studio receipt and fingerprints for later duplicate checks. If interrupted after verification, run `finalize new-episode` to resume. `status` still works after cleanup. The project root, channel profile, other episodes and caller-owned sources are separate from episode cleanup.
