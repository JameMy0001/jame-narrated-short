"""Thai Edge-TTS, PCM assembly, subtitles, and media verification."""
import asyncio
import json
import subprocess
import wave

import aiohttp
import edge_tts
from pythainlp.tokenize import word_tokenize

from storage import FONT

VOICE = "th-TH-NiwatNeural"
WIDTH, HEIGHT = 1080, 1920
FONT_SIZE = 58
MARGIN_LEFT, MARGIN_RIGHT, MARGIN_BOTTOM = 72, 176, 445
TEXT_WIDTH = WIDTH - MARGIN_LEFT - MARGIN_RIGHT - 16
SAMPLE_RATE = 24_000
END_PAUSE_MS = 350

def wrap_phrase(phrase, font, width=TEXT_WIDTH):
    if font.getlength(phrase) <= width:
        return [phrase]
    words = word_tokenize(phrase, engine="newmm")
    # Prefer a written pause, then balance the widths of the two lines.
    choices = []
    for index in range(1, len(words)):
        left = "".join(words[:index]).strip()
        right = "".join(words[index:]).strip()
        if left and right:
            widths = [font.getlength(left), font.getlength(right)]
            if max(widths) <= width:
                word_break = not (words[index - 1].isspace() or words[index].isspace())
                choices.append((word_break, abs(widths[0] - widths[1]), [left, right]))
    if not choices:
        raise ValueError("Use a shorter spoken phrase so its subtitle fits two lines.")
    return min(choices)[2]


async def synthesize_phrase(phrase, index, semaphore, voice=VOICE, attempts=3):
    async with semaphore:
        for attempt in range(attempts):
            audio = bytearray()
            events = []
            try:
                speech = edge_tts.Communicate(
                    phrase, voice, boundary="SentenceBoundary", receive_timeout=25
                )
                async for event in speech.stream():
                    if event["type"] == "audio":
                        audio.extend(event["data"])
                    elif event["type"] == "SentenceBoundary":
                        events.append(event)
                if audio and events:
                    return audio, events
                raise RuntimeError("Edge-TTS returned no audio or sentence timestamps.")
            except (aiohttp.ClientError, OSError, RuntimeError, edge_tts.exceptions.EdgeTTSException) as error:
                if attempt == attempts - 1:
                    raise RuntimeError(f"Edge-TTS failed for phrase {index}: {error}") from error
                await asyncio.sleep(min(5, 1 + attempt))


async def synthesize_story(phrases, voice=VOICE):
    semaphore = asyncio.Semaphore(3)
    return await asyncio.gather(*(
        synthesize_phrase(phrase, index, semaphore, voice=voice)
        for index, phrase in enumerate(phrases, 1)
    ))


def assemble_audio(segments, captions, output, pause_ms=END_PAUSE_MS):
    cues = []
    samples_written = 0
    with wave.open(str(output), "wb") as narration:
        narration.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
        for (audio, events), lines in zip(segments, captions, strict=True):
            pcm = subprocess.run(
                [
                    "ffmpeg", "-v", "error", "-xerror", "-f", "mp3", "-i", "pipe:0",
                    "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1", "pipe:1",
                ],
                input=audio, stdout=subprocess.PIPE, check=True,
            ).stdout
            samples = len(pcm) // 2
            offset_ms = samples_written * 1000 / SAMPLE_RATE
            start_ms = min(event["offset"] for event in events) / 10_000
            end_ms = min(
                max(event["offset"] + event["duration"] for event in events) / 10_000,
                samples * 1000 / SAMPLE_RATE,
            )
            start, end = round(offset_ms + start_ms), round(offset_ms + end_ms)
            if not 0 <= start < end:
                raise ValueError(f"Invalid speech timestamps for phrase {len(cues) + 1}.")
            cues.append((start, end, lines))
            narration.writeframes(pcm)
            samples_written += samples
        narration.writeframes(bytes(2 * SAMPLE_RATE * pause_ms // 1000))
    return cues, samples_written / SAMPLE_RATE + pause_ms / 1000


def timestamp(ticks, *, ass=False):
    per_second = 100 if ass else 1000
    seconds, fraction = divmod(ticks, per_second)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if ass:
        return f"{hours}:{minutes:02d}:{seconds:02d}.{fraction:02d}"
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{fraction:03d}"


def write_captions(cues, srt, ass, *, shape=(WIDTH, HEIGHT), font_size=FONT_SIZE,
                   margins=(MARGIN_LEFT, MARGIN_RIGHT, MARGIN_BOTTOM), centered=True):
    width, height = shape
    left, right, bottom = margins
    alignment = 2 if centered else 1
    srt_events = []
    ass_events = []
    for index, (start, end, lines) in enumerate(cues, 1):
        times = f"{timestamp(start)} --> {timestamp(end)}"
        srt_events.append("\n".join([str(index), times, *lines]))
        start_tick = round(start / 10)
        end_tick = max(start_tick + 1, round(end / 10))
        text = r"\N".join(lines)
        ass_events.append(
            f"Dialogue: 0,{timestamp(start_tick, ass=True)},{timestamp(end_tick, ass=True)},"
            f"Default,,0,0,0,,{text}"
        )
    srt.write_text("\n\n".join(srt_events) + "\n", encoding="utf-8")
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Kanit,{font_size},&H00FFFFFF,&H00FFFFFF,&H00111111,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,{alignment},{left},{right},{bottom},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ass.write_text(header + "\n".join(ass_events) + "\n", encoding="utf-8")


def check_duration(duration, limits, label):
    minimum, maximum = limits
    if not minimum <= duration <= maximum:
        raise ValueError(
            f"{label} lasts {duration:.2f}s, outside {minimum}–{maximum}s. "
            "Revise the narration and render again."
        )


def verify_video(path, limits, shape=(WIDTH, HEIGHT)):
    probe = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path),
    ])
    info = json.loads(probe)
    video = next((stream for stream in info["streams"] if stream["codec_type"] == "video"), {})
    audio = next((stream for stream in info["streams"] if stream["codec_type"] == "audio"), {})
    if (
        "mp4" not in info["format"]["format_name"].split(",")
        or (video.get("width"), video.get("height")) != shape
        or video.get("codec_name") != "h264"
        or video.get("pix_fmt") != "yuv420p"
        or video.get("avg_frame_rate") != "30/1"
        or audio.get("codec_name") != "aac"
    ):
        raise ValueError("Rendered video must have H.264/yuv420p at 30 fps with the planned dimensions and AAC audio.")
    duration = float(info["format"]["duration"])
    check_duration(duration, limits, "MP4")
    audio_duration, video_duration = float(audio["duration"]), float(video["duration"])
    if abs(audio_duration - video_duration) > 0.1:
        raise ValueError(f"Audio ({audio_duration:.3f}s) and video ({video_duration:.3f}s) lengths differ.")
    return duration
