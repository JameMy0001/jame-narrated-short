"""Playwright Studio controls adapted from the local Private uploader."""
import contextlib
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import parse_qs, urlsplit

from storage import CHANNEL, STUDIO, VIDEO_ID, Problem, directory, stamp

@contextlib.contextmanager
def studio(workspace):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise Problem("Install playwright and Pillow in the package Python environment.") from None
    profile = directory(workspace.profile, create=True)
    with sync_playwright() as p:
        try:
            ctx = p.chromium.launch_persistent_context(
                str(profile), channel="chrome", headless=True, locale="en-US", timeout=20000,
                # The sign-in window uses Chrome's normal keychain. A mock
                # keychain cannot read the session saved by that window.
                ignore_default_args=["--use-mock-keychain", "--password-store=basic"],
            )
        except Exception as launch_err:
            raise Problem(f"Could not start Chrome with the upload profile: {launch_err}. Close the dedicated Chrome window and retry.") from None
        try:
            page = ctx.new_page()
            page.set_default_timeout(18000)
            yield page
        finally:
            ctx.close()


def check_channel(page, expected=None):
    url = f"{STUDIO}/channel/{expected}" if expected else STUDIO
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    current = urlsplit(page.url)
    if current.hostname != "studio.youtube.com":
        raise Problem("YouTube Studio needs sign-in. Run login with the dedicated Chrome profile.")
    # Dismiss unsupported browser interstitial if present
    skip_button = page.get_by_text(re.compile(r"^(?:SKIP TO YOUTUBE STUDIO|ข้ามไปยัง YOUTUBE STUDIO)$", re.IGNORECASE))
    if skip_button.count():
        with contextlib.suppress(Exception):
            skip_button.first.click(timeout=3000)
            page.wait_for_timeout(2000)
    try:
        page.wait_for_url(re.compile(r"^https://studio\.youtube\.com/channel/UC[-_A-Za-z0-9]{22}(?:[/?]|$)"),
                          timeout=12000)
    except Exception:
        if skip_button.count():
            with contextlib.suppress(Exception):
                skip_button.first.click(timeout=3000)
                page.wait_for_timeout(2000)
        try:
            page.wait_for_url(re.compile(r"^https://studio\.youtube\.com/channel/UC[-_A-Za-z0-9]{22}(?:[/?]|$)"),
                              timeout=8000)
        except Exception:
            raise Problem("Could not verify the signed-in Studio channel. Select the channel you intend to bind and try again.") from None
    current = urlsplit(page.url)
    parts = current.path.split("/")
    if current.hostname != "studio.youtube.com" or len(parts) < 3 or parts[1] != "channel":
        raise Problem("Studio left the channel dashboard during verification.")
    identifier = parts[2]
    if not CHANNEL.fullmatch(identifier) or (expected and identifier != expected):
        raise Problem("The active Studio channel does not match the bound channel.")
    return identifier


# Match semantic controls and current Studio component IDs. Keep all upload actions
# scoped to the dialog opened by this worker in a fresh tab.
UPLOAD_DIALOG = "ytcp-uploads-dialog:visible, ytcp-upload-dialog:visible, ytcp-uploads-dialog tp-yt-paper-dialog:visible, tp-yt-paper-dialog#dialog:visible"
CREATE = ("ytcp-button#create-icon:visible, button#create-icon:visible, #create-button:visible, "
          "ytcp-icon-button#upload-button:visible")
TITLE = "#title-textarea #textbox:visible"
DESC = "#description-textarea #textbox:visible"
KIDS = (
    "[name='VIDEO_MADE_FOR_KIDS_NOT_MFK']",
    r"^(?:No,? it['’]s not made for kids|ไม่ใช่[ ,]*วิดีโอนี้ไม่ได้สร้างมาเพื่อเด็ก)$",
)
AI_TITLE = re.compile(r"(?:Altered content|Altered or synthetic|AI use|AI usage|"
                      r"เนื้อหาที่มีการดัดแปลง|เนื้อหาดัดแปลง|การใช้ AI|การใช้งาน AI)", re.I)
AI_YES = re.compile(r"(?:^Yes\b|ใช่)", re.I)
AI_NO = re.compile(r"(?:^No\b|ไม่|ไม่ใช่)", re.I)
RADIOS = "tp-yt-paper-radio-button:visible, ytcp-radio-button:visible, [role='radio']:visible, input[type='radio']:visible"
PRIVATE = re.compile(r"^(?:Private|ส่วนตัว|แบบส่วนตัว)$", re.I)
TAGS_INPUT = ("ytcp-free-text-chip-bar input:visible, ytcp-chip-bar input#text-input:visible, "
              "#tags-container input:visible, input[aria-label='Tags']:visible, input[aria-label='แท็ก']:visible")
CHIPS = "ytcp-chip #chip-text:visible, ytcp-chip span.chip-text:visible"
SAVE = re.compile(r"^(?:Save|บันทึก)$", re.I)
TRANSFER_DONE = re.compile(
    r"(?:Upload complete|Finished uploading|Upload finished|Video uploaded|"
    r"Processing (?:will begin|started|complete|HD|SD|[0-9]+%)|"
    r"Checking [0-9]+%|Checks complete|"
    r"อัปโหลดเสร็จ|อัปโหลดสมบูรณ์|อัปโหลดเรียบร้อย|กำลังประมวลผล|จะเริ่มประมวลผล)", re.I)
TRANSFER_FAILED = re.compile(
    r"(?:Upload failed|Upload abandoned|Processing abandoned|Daily upload limit|upload limit reached|"
    r"อัปโหลดไม่สำเร็จ|ถึงขีดจำกัด|เกินขีดจำกัด|ยกเลิกการประมวลผล)", re.I)
DAILY_LIMIT = re.compile(
    r"(?:Daily upload limit|upload limit reached|ถึงขีดจำกัด|เกินขีดจำกัด)", re.I)


def exact(scope, pattern):
    return scope.get_by_text(pattern, exact=True).filter(visible=True)


def control(scope, css, label, fallback_role="button"):
    pattern = re.compile(rf"^(?:{label})$", re.I)
    hit = scope.locator(css).or_(scope.get_by_role(fallback_role, name=pattern)).first
    try:
        hit.scroll_into_view_if_needed(timeout=2000)
    except Exception:
        pass
    hit.wait_for(timeout=30000)
    return hit


def labeled_radios(scope, label):
    radios = scope.locator(RADIOS)
    return radios.filter(has_text=label).or_(radios.and_(scope.get_by_role("radio", name=label)))


def choose(scope, selector, label, edit):
    by_text = labeled_radios(scope, label)
    radio = (scope.locator(selector).or_(by_text) if selector else by_text).first
    try:
        radio.scroll_into_view_if_needed(timeout=2000)
    except Exception:
        pass
    radio.wait_for(timeout=15000)
    if edit and not checked(radio):
        try:
            radio.click(timeout=3000)
        except Exception:
            try:
                radio.click(force=True, timeout=3000)
            except Exception:
                radio.evaluate("el => { el.click(); el.dispatchEvent(new Event('change', {bubbles: true})); }")
    if edit and not checked(radio):
        radio.evaluate("el => { el.click(); el.dispatchEvent(new Event('change', {bubbles: true})); }")
    if not checked(radio):
        raise Problem("Studio did not confirm the required radio selection.")
    return radio


def checked(radio):
    return radio.evaluate("""el => el.checked === true || el.getAttribute('aria-checked') === 'true' ||
        el.getAttribute('checked') === '' || el.getAttribute('checked') === 'true' ||
        el.getAttribute('selected') === '' || el.getAttribute('selected') === 'true' ||
        el.shadowRoot?.querySelector('input:checked,[aria-checked=true]') != null""") is True


def check_upload_errors(dialog, page):
    text = ""
    with contextlib.suppress(Exception):
        if dialog.is_visible():
            text = dialog.inner_text()
        else:
            text = page.locator("body").inner_text()
    if DAILY_LIMIT.search(text):
        raise Problem("Daily upload limit reached: YouTube daily quota exhausted for this 24-hour window.")
    if exact(dialog, TRANSFER_FAILED).count():
        raise Problem("Studio reported an upload error or closed the wizard.")


def show_more(scope):
    if scope.locator(TAGS_INPUT).count() and scope.get_by_text(AI_TITLE).count():
        return
    text = ""
    with contextlib.suppress(Exception):
        text = scope.inner_text()
    if DAILY_LIMIT.search(text):
        raise Problem("Daily upload limit reached: YouTube daily quota exhausted for this 24-hour window.")
    toggle = control(scope, "#toggle-button, #more-button",
                     "Show more|Show less|แสดงเพิ่มเติม|แสดงน้อยลง|ดูเพิ่มเติม|ดูน้อยลง")
    try:
        toggle.evaluate("el => el.scrollIntoView({block: 'center', inline: 'center'})")
    except Exception:
        with contextlib.suppress(Exception):
            toggle.scroll_into_view_if_needed(timeout=2000)
    if re.search(r"less|น้อยลง", toggle.inner_text(), re.I) or toggle.get_attribute("aria-expanded") == "true":
        return
    try:
        toggle.click(timeout=3000)
    except Exception:
        try:
            toggle.click(force=True, timeout=3000)
        except Exception:
            toggle.evaluate("el => el.click()")
    with contextlib.suppress(Exception):
        scope.page.wait_for_timeout(1500)


def ai_group(scope):
    heading = scope.get_by_text(AI_TITLE).first
    try:
        heading.evaluate("el => el.scrollIntoView({block: 'center', inline: 'center'})")
    except Exception:
        with contextlib.suppress(Exception):
            heading.scroll_into_view_if_needed(timeout=2000)
    heading.wait_for(timeout=10000)
    group = heading
    for _ in range(7):
        group = group.locator("..")
        yes = labeled_radios(group, AI_YES)
        no = labeled_radios(group, AI_NO)
        if yes.count() >= 1 and no.count() >= 1:
            return group
    raise Problem("Could not identify the AI use / Altered content Yes/No field.")


def normalized(text):
    if not text:
        return ""
    return re.sub(r'\s+', ' ', text.replace("\u00a0", " ")).strip()


def textbox(scope, selector, value, edit):
    field = scope.locator(selector).first
    field.wait_for()
    if edit:
        field.fill(value)
        field.press("Tab")
    actual = field.evaluate("el => 'value' in el ? el.value : el.innerText")
    if normalized(actual) != normalized(value):
        raise Problem(f"Studio text field does not match the requested metadata: expected {len(normalized(value))} chars, got {len(normalized(actual))} chars.")


def tag_chips(scope):
    chips = scope.locator(CHIPS).all_text_contents()
    if not chips:
        chips = scope.locator("ytcp-chip:visible").all_inner_texts()
    return {normalized(chip) for chip in chips if normalized(chip)}


def set_tags(scope, tags, edit):
    tag_input = scope.locator(TAGS_INPUT).first
    try:
        tag_input.evaluate("el => el.scrollIntoView({block: 'center', inline: 'center'})")
    except Exception:
        with contextlib.suppress(Exception):
            tag_input.scroll_into_view_if_needed(timeout=2000)
    tag_input.wait_for(timeout=10000)
    if edit:
        for old in scope.locator("ytcp-chip:visible").all()[::-1]:
            button = old.locator("#remove-button, #delete-button, [aria-label^='Remove'], [aria-label^='ลบ']").first
            if not button.count():
                raise Problem("Cannot clear existing default tags.")
            button.click()
        for tag in tags:
            tag_input.fill(tag)
            tag_input.press("Enter")
        tag_input.press("Tab")
    if tag_chips(scope) != set(tags):
        raise Problem("Studio tags do not match the requested list.")


def details(scope, plan, edit):
    textbox(scope, TITLE, plan["title"], edit)
    textbox(scope, DESC, plan["description"], edit)
    choose(scope, KIDS[0], re.compile(KIDS[1], re.I), edit)
    show_more(scope)
    answer = AI_YES if plan["ai_disclosure"] else AI_NO
    choose(ai_group(scope), None, answer, edit)
    set_tags(scope, plan["tags"], edit)


def thumbnail_file(path, plan, tmp):
    if not plan["thumbnail"]:
        return None
    source = path / plan["thumbnail"]
    if source.stat().st_size <= 2_000_000:
        return source
    from PIL import Image
    target = Path(tmp) / "thumbnail.png"
    with Image.open(source) as cover:
        cover.convert("RGB").quantize(colors=256).save(target, "PNG", optimize=True)
    if target.stat().st_size > 2_000_000:
        raise Problem("Could not prepare a PNG upload copy under 2 MB from the master.")
    return target


def studio_id(scope):
    candidates = set()
    for link in scope.locator("a[href*='youtu.be/'], a[href*='/video/'], a[href*='watch?v='], a[href*='/shorts/']").all():
        href = urlsplit(link.get_attribute("href") or "")
        if href.hostname == "youtu.be" or href.path.startswith(("/video/", "/shorts/")):
            identifier = href.path.split("/")[1 if href.hostname == "youtu.be" else 2]
        else:
            identifier = parse_qs(href.query).get("v", [""])[0]
        if VIDEO_ID.fullmatch(identifier):
            candidates.add(identifier)
    if len(candidates) > 1:
        raise Problem("Studio returned more than one possible video ID.")
    return next(iter(candidates), None)


def record_id(episode, identifier):
    if identifier and identifier != episode.receipt().get("video_id"):
        episode.change(**{"video_id": identifier, "studio_url": f"{STUDIO}/video/{identifier}/edit"})


def wait_transfer(page, dialog, episode):
    deadline = time.monotonic() + 6 * 3600
    while time.monotonic() < deadline:
        if dialog.is_visible():
            d_text = ""
            with contextlib.suppress(Exception):
                d_text = dialog.inner_text()
            if DAILY_LIMIT.search(d_text) or exact(dialog, DAILY_LIMIT).count():
                raise Problem("Daily upload limit reached: YouTube daily quota exhausted for this 24-hour window.")
        if not dialog.is_visible() or exact(dialog, TRANSFER_FAILED).count():
            if dialog.is_visible():
                with contextlib.suppress(Exception):
                    d_text = dialog.inner_text()
                    if DAILY_LIMIT.search(d_text):
                        raise Problem("Daily upload limit reached: YouTube daily quota exhausted for this 24-hour window.")
            raise Problem("Studio reported an upload error or closed the wizard.")
        identifier = studio_id(dialog)
        progress = dialog.locator(
            "ytcp-video-upload-progress:visible, ytcp-video-upload-progress-text:visible, "
            "#upload-progress:visible, #progress-label:visible"
        )
        record_id(episode, identifier)
        if identifier and (progress.filter(has_text=TRANSFER_DONE).count() or exact(dialog, TRANSFER_DONE).count()):
            return identifier
        page.wait_for_timeout(1500)
    raise Problem("Timed out waiting for the upload transfer. Check Studio before doing anything else.")


def verify_saved(page, identifier, plan):
    url = f"{STUDIO}/video/{identifier}/edit"
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    skip = page.get_by_text(re.compile(r"^(?:SKIP TO YOUTUBE STUDIO|ข้ามไปยัง YOUTUBE STUDIO)$", re.IGNORECASE))
    if skip.count():
        with contextlib.suppress(Exception):
            skip.first.click(timeout=3000)
            page.wait_for_timeout(2000)
    parsed = urlsplit(page.url)
    if parsed.hostname != "studio.youtube.com" or parsed.path.rstrip("/") != f"/video/{identifier}/edit":
        raise Problem("Studio did not open the expected saved video.")
    panel = page.locator(
        "ytcp-video-metadata-visibility, ytcp-video-visibility-select, #visibility-select, #visibility-container,"
        "ytcp-video-visibility, #visibility"
    ).first
    with contextlib.suppress(Exception):
        panel.scroll_into_view_if_needed(timeout=5000)
    for _ in range(20):
        if panel.count() and panel.inner_text().strip():
            break
        page.wait_for_timeout(1000)
    trigger = panel.locator("#trigger, ytcp-dropdown-trigger, [role='button'], .selected-item").first
    trigger_text = trigger.inner_text().strip() if (trigger.count() and trigger.inner_text().strip()) else panel.inner_text().strip()
    if not PRIVATE.fullmatch(normalized(trigger_text)):
        raise Problem("The reopened Studio video does not show unambiguous saved Private visibility.")
    draft = re.compile(r"^(?:Edit draft|Finish draft|Resume upload|แก้ไขฉบับร่าง)$", re.I)
    if page.locator(UPLOAD_DIALOG).count() or exact(page, draft).count():
        raise Problem("Studio reopened an upload draft instead of a saved video.")
    details(page, plan, edit=False)
    return url


def upload(page, episode, path, plan, channel):
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    episode.change(**{"stage": "check_channel"})
    check_channel(page, channel)
    episode.change(**{"stage": "open_upload"})
    dialog = page.locator(UPLOAD_DIALOG).first
    dashboard_upload = page.locator("#dashboard-actions #upload-icon:visible")
    try:
        dashboard_upload.wait_for(timeout=4000)
    except PlaywrightTimeout:
        control(page, CREATE, "Create|สร้าง").click()
        upload_menu = exact(page, re.compile(r"^(?:Upload videos|อัปโหลดวิดีโอ)$", re.I)).first
        upload_menu.click()
    else:
        dashboard_upload.click()
    dialog.wait_for()
    upload_input = dialog.locator("input[type='file']")
    upload_input.wait_for(state="attached")
    with tempfile.TemporaryDirectory(prefix=".thumbnail-", dir=episode.work) as tmp:
        cover = thumbnail_file(path, plan, tmp)
        if any(stamp(path / name) != value for name, value in plan["assets"].items()):
            raise Problem("An asset changed after validation.")
        episode.change(**{"status": "attaching", "stage": "attach_file"})
        upload_input.set_input_files(str(path / plan["video"]))
        page.wait_for_timeout(1000)
        check_upload_errors(dialog, page)
        episode.change(**{"status": "uploading", "stage": "fill_details"})
        details(dialog, plan, edit=True)
        check_upload_errors(dialog, page)
        if cover:
            thumb = dialog.locator("ytcp-thumbnails-compact-editor input[type=file], "
                                   "ytcp-thumbnails-compact-editor-uploader input[type=file], "
                                   "input[type=file][accept*='image']").first
            thumb.set_input_files(str(cover))
        episode.change(**{"stage": "select_private"})
        for _ in range(6):
            check_upload_errors(dialog, page)
            if dialog.locator("[name='PRIVATE']:visible").count() or dialog.locator("#done-button:visible").count():
                break
            nb = dialog.locator("#next-button:visible, [name='NEXT']:visible")
            if nb.count() and nb.first.is_visible():
                try:
                    nb.first.click(timeout=3000)
                except Exception:
                    nb.first.evaluate("el => el.click()")
            page.wait_for_timeout(1200)
        private = choose(dialog, "[name='PRIVATE']", PRIVATE, True)
        done = control(dialog, "#done-button:visible", "Save|บันทึก")
        episode.change(**{"stage": "wait_for_transfer"})
        identifier = wait_transfer(page, dialog, episode)
        private_label = private.get_attribute("aria-label") or private.inner_text()
        if (not checked(private) or not PRIVATE.fullmatch(normalized(private_label)) or
                not SAVE.fullmatch(normalized(done.inner_text()))):
            raise Problem("The final dialog is not confirmed as Private / Save.")
        episode.change(**{"status": "saving", "stage": "click_save"})
        done.click()
        close_btn = page.locator("#close-button:visible, [aria-label='Close']:visible, [aria-label='ปิด']:visible").or_(page.get_by_text(re.compile(r"^(?:Close|ปิด)$", re.I)))
        for _ in range(30):
            if not dialog.is_visible():
                break
            if close_btn.count():
                with contextlib.suppress(Exception):
                    close_btn.first.click(timeout=1000)
            page.wait_for_timeout(1000)
        with contextlib.suppress(Exception):
            dialog.wait_for(state="hidden", timeout=10000)
        episode.change(**{"stage": "verify_in_studio"})
        if dialog.is_visible():
            raise Problem("Studio still displays the upload dialog after Save.")
        check_channel(page, channel)
        return identifier, verify_saved(page, identifier, plan)
