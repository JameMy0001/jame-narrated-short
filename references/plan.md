# Production plan

Provide one UTF-8 JSON object per episode. Run `begin` first, then author the source and run `prepare ID PATH`. Keep generated scripts and art inside the episode's `work` directory. A passed `check-batch` binds the exact plan and reviewed art, so rerun the gate after a revision.

Common fields

| Field | Value |
| --- | --- |
| `title` | YouTube title, one line, up to 100 characters |
| `story_title` | Optional canonical story title for comparison when it differs from the SEO title |
| `series` | Optional recurring series or shared story name. Use a distinct title and incident per episode |
| `premise` | Distinguishing premise, up to 360 characters, retained in local history |
| `description` | Thai YouTube description. Leave space for generated metadata |
| `tags` | List of individual tags, subject to YouTube's 500-character total |
| `ai_disclosure` | Boolean, optional, defaults to true |
| `plot` | Object with specific `hook`, `conflict`, `turn`, and `ending` strings |
| `visual` | Object with specific `subject`, `action`, `setting`, `shot`, and `palette` strings |
| `duration_seconds` | Optional `[minimum, maximum]` measured in seconds |

Keep subtitle phrases short enough to fit one or two lines. The examples show field structure and use abbreviated dialogue.

Supply `art_brief` for the native portrait shot and `narration` as ordered spoken phrases. Default voice is `th-TH-NiwatNeural`. The package also supports `th-TH-PremwadeeNeural`. The default duration is 45–60 seconds with a 180-second ceiling. For a requested adaptation, include the source title in the description. Use optional `related_video` for a published YouTube watch URL.

Additional fields

```json
{
  "duration_seconds": [45, 60],
  "voice": "th-TH-NiwatNeural",
  "art_brief": "Native 9:16, a young gardener lifts a mirror at a courtyard gate, dusk, open quiet area beneath hands",
  "narration": ["ประตูสวนเปิดได้เฉพาะตอนที่เห็นเงาตัวเอง", "แต่ในกระจก ใบหน้าที่ขยับกลับไม่ใช่ของเธอ"]
}
```
