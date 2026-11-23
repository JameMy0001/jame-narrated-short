"""Adapted story and perceptual artwork checks for standalone episode receipts."""
from difflib import SequenceMatcher
import hashlib
from PIL import Image, ImageOps
from storage import Problem, file_hash, key_hash
import unicodedata
import re


def normalized(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    return "".join(char for char in text if unicodedata.category(char)[0] in "LMN")


def story_record(plan, phrases):
    text = normalized("".join(phrases))
    # Keep a compact sample for near matches and a hash of the complete script.
    sample = text if len(text) <= 1600 else text[:650] + text[len(text)//2:len(text)//2+300] + text[-650:]
    return {
        "title": normalized(plan.get("story_title") or plan["title"]),
        "episode_title": normalized(plan["title"]),
        "series": normalized(plan.get("series", "")),
        "summary": normalized(plan["premise"]),
        "narration": sample,
        "narration_hash": key_hash(text),
        "plot": {k: normalized(v) for k, v in plan["plot"].items()},
        "visual": normalized(" ".join(plan["visual"].values())),
    }


def difference(left, right):
    return SequenceMatcher(None, left, right, autojunk=False).ratio() if left and right else 0


def story_match(new, old):
    same_series = new["series"] and new["series"] == old["series"]
    if new["episode_title"] == old.get("episode_title") or (new["title"] == old["title"] and not same_series):
        return "same story title"
    if new["narration_hash"] == old["narration_hash"]:
        return "same narration"
    for field, threshold in (("narration", .78), ("summary", .86)):
        if min(len(new[field]), len(old[field])) >= 50 and difference(new[field], old[field]) >= threshold:
            return f"similar {field}"
    if sum(difference(value, old["plot"].get(key, "")) >= .75 for key, value in new["plot"].items()) >= 3:
        return "similar plot beats"
    if difference(new["visual"], old["visual"]) >= .86:
        return "similar shot and visual brief"
    return None


def image_hash(image):
    gray = image.convert("L")
    pixels = gray.resize((8, 8), Image.Resampling.LANCZOS).tobytes()
    average = sum(pixels) / len(pixels)
    row = gray.resize((9, 8), Image.Resampling.LANCZOS).tobytes()
    col = gray.resize((8, 9), Image.Resampling.LANCZOS).tobytes()
    bits = [p > average for p in pixels]
    bits += [row[y * 9 + x] > row[y * 9 + x + 1] for y in range(8) for x in range(8)]
    bits += [col[y * 8 + x] > col[(y + 1) * 8 + x] for y in range(8) for x in range(8)]
    return f"{int(''.join('1' if bit else '0' for bit in bits), 2):048x}"


def art_fingerprint(image):
    return fileless_hash(image), image_hash(image)


def framed_artwork(image):
    """Flag solid white or black bands with a sharp edge on two opposite sides."""
    preview = ImageOps.grayscale(image)
    preview.thumbnail((160, 160), Image.Resampling.LANCZOS)
    for canvas in (preview, preview.transpose(Image.Transpose.TRANSPOSE)):
        width, height = canvas.size
        pixels = canvas.load()
        rows = range(height // 8, height * 7 // 8)
        if not rows:
            continue
        for is_band in (lambda value: value >= 238, lambda value: value <= 18):
            def fraction(x):
                return sum(is_band(pixels[x, y]) for y in rows) / len(rows)

            depths = []
            for direction in (1, -1):
                start = 0 if direction == 1 else width - 1
                for offset in range(width // 8):
                    if fraction(start + direction * offset) < 0.94:
                        if offset >= 3 and fraction(start + direction * (offset + 2)) < 0.68:
                            depths.append(offset)
                        break
            if len(depths) == 2 and max(depths) <= 2 * min(depths) + 2:
                return True
    return False


def fileless_hash(image):
    return hashlib.sha256(image.tobytes()).hexdigest()



def png_record(path, size):
    with Image.open(path) as raw:
        if raw.format != "PNG" or getattr(raw, "is_animated", False):
            raise Problem("The working artwork must be a single PNG.")
        if raw.width * size[1] != raw.height * size[0] or min(raw.size) < min(size):
            raise Problem(f"Need native {size[0]}:{size[1]} art, at least {size[0]}×{size[1]}.")
        if raw.convert("RGBA").getchannel("A").getextrema() != (255, 255):
            raise Problem("Need opaque, full-bleed artwork.")
        image = raw.convert("RGB")
    if framed_artwork(image):
        raise Problem("Artwork has visible solid borders.")
    pixels, view = art_fingerprint(image.resize(size, Image.Resampling.LANCZOS))
    views = {view, image_hash(ImageOps.mirror(image))}
    for zoom in (.88, .75):
        margin_x, margin_y = round(image.width * (1 - zoom)), round(image.height * (1 - zoom))
        for x, y in ((0, 0), (margin_x//2, margin_y//2), (margin_x, margin_y)):
            crop = image.crop((x, y, x + image.width - margin_x, y + image.height - margin_y))
            views.update((image_hash(crop), image_hash(ImageOps.mirror(crop))))
    return {"png_sha256": file_hash(path), "pixels": pixels, "views": sorted(views), "view": view}


def art_match(new, old):
    if (new["source_sha256"] == old["source_sha256"] or new["pixels"] == old["pixels"]
            or new["generation_ref"] == old["generation_ref"]):
        return "same artwork or generation reference"
    for a, b in ((new, old), (old, new)):
        if any((int(a["view"], 16) ^ int(view, 16)).bit_count() <= 23 for view in b["views"]):
            return "similar artwork or crop"
    return None
