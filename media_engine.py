import math
import os
import re
import json
import struct
import time
import wave
import base64
import html
import io
import tempfile
import subprocess
import unicodedata
from concurrent.futures import ThreadPoolExecutor
import urllib.request
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
from opencc import OpenCC
from playwright.sync_api import sync_playwright

import presentation
import script_writer
import broll_engine


# ── 常數定義 ──────────────────────────────────────────

MAIN_VIDEO_WIDTH = 1920
MAIN_VIDEO_HEIGHT = 1080
MAIN_DESIGN_WIDTH = 1920
MAIN_DESIGN_HEIGHT = 1080
MAIN_OUTPUT_SCALE = 1
VIDEO_FPS = 30
VIDEO_CRF = "18"

SHORT_VIDEO_WIDTH = 1080
SHORT_VIDEO_HEIGHT = 1920
SHORT_DESIGN_WIDTH = 720
SHORT_DESIGN_HEIGHT = 1280
SHORT_OUTPUT_SCALE = 1.5
SHORT_SUBTITLE_MAX_WIDTH = 12.5
SHORT_SUBTITLE_SHRINK_LIMIT = 15.5
SHORT_SUBTITLE_SHRINK_PERCENT = 88
SHORT_SUBTITLE_SENTENCE_END = re.compile(r"[。！？!?；;\n]+")
SHORT_SUBTITLE_BREAKS = re.compile(r"[，、,]")
SHORT_SUBTITLE_CONNECTORS = re.compile(
    r"但是|不過|然而|因此|所以|同時|而且|此外|另外|接著|反而|因為|如果|"
    r"雖然|並且|可是|但|而|且|或|則"
)
SHORT_SUBTITLE_EDGE_PUNCTUATION = re.compile(
    r"^[，、,。！？!?；;：:]+|[，、,。！？!?；;：:]+$"
)

kw_pat = r'(\$[\d,.]+|\b\d+(?:,\d+)*(?:\.\d+)?%?\b|比特幣|以太幣|以太坊|BTC|ETH|SOL|XRP|ADA|ZEC|RSI|均線|恐慌與貪婪指數|超賣|超買|支撐|壓力|風險提醒|投資建議)'
punct_pat = r'[，。！？、；：「」『』（）【】《》—…!?;:\(\)\[\]]+|(?<!\d)[,\.](?!\d)'

def get_system_font_path():
    font_paths = (
        r"C:\Windows\Fonts\msjh.ttc",
        r"C:\Windows\Fonts\mingliu.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    )
    font_path = next((path for path in font_paths if os.path.isfile(path)), None)
    if font_path is None:
        raise RuntimeError("No supported font found.")
    return font_path


# ── 投影片時間與對話提取 ──────────────────────────────────────────

def extract_dialogues_and_slide_anchors(script, slide_count):
    dialogues = []
    first_anchor_dialogue_indexes = {}
    slide_anchor_pattern = re.compile(r"\[SLIDE_(\d+)\]")
    for line in script.splitlines():
        match = re.match(r"^(男|女)[：:]\s*(.+)", line.strip())
        if not match:
            continue
        raw_dialogue_text = match.group(2).strip()
        marker_matches = list(slide_anchor_pattern.finditer(raw_dialogue_text))
        dialogue_text = slide_anchor_pattern.sub("", raw_dialogue_text).strip()
        dialogue_text = script_writer.normalize_long_term_terms(dialogue_text)
        if not dialogue_text:
            continue

        for marker_match in marker_matches:
            slide_number = int(marker_match.group(1))
            if 1 <= slide_number <= slide_count:
                first_anchor_dialogue_indexes.setdefault(
                    slide_number, len(dialogues)
                )
                break
        dialogues.append((match.group(1), dialogue_text))

    ordered_anchor_points = []
    last_dialogue_index = -1
    for slide_number in range(1, slide_count + 1):
        dialogue_index = first_anchor_dialogue_indexes.get(slide_number)
        if dialogue_index is not None and dialogue_index > last_dialogue_index:
            ordered_anchor_points.append((slide_number, float(dialogue_index)))
            last_dialogue_index = dialogue_index

    if not dialogues:
        return dialogues, {}

    anchor_points = {1: 0.0, slide_count: float(len(dialogues))}
    anchor_points.update(ordered_anchor_points)
    known_slides = sorted(anchor_points)
    positions = {}
    for slide_number in range(1, slide_count + 1):
        if slide_number in anchor_points:
            positions[slide_number] = anchor_points[slide_number]
            continue

        previous_slide = max(
            known for known in known_slides if known < slide_number
        )
        next_slide = min(
            known for known in known_slides if known > slide_number
        )
        previous_index = anchor_points[previous_slide]
        next_index = anchor_points[next_slide]
        fraction = (slide_number - previous_slide) / (next_slide - previous_slide)
        positions[slide_number] = previous_index + fraction * (
            next_index - previous_index
        )

    min_gap = len(dialogues) / (slide_count * 1000)
    ordered_positions = [
        min(max(positions[slide_number], 0.0), float(len(dialogues)) - min_gap)
        for slide_number in range(1, slide_count + 1)
    ]
    for index in range(1, slide_count):
        ordered_positions[index] = max(
            ordered_positions[index], ordered_positions[index - 1] + min_gap
        )
    for index in range(slide_count - 2, -1, -1):
        ordered_positions[index] = min(
            ordered_positions[index], ordered_positions[index + 1] - min_gap
        )

    slide_anchor_positions = {
        slide_number: ordered_positions[slide_number - 1]
        for slide_number in range(1, slide_count + 1)
    }
    return dialogues, slide_anchor_positions


def calculate_slide_durations(slide_start_times, speech_duration, video_intro_duration, slide_count, outro_pause):
    expected_slides = set(range(1, slide_count + 1))
    if set(slide_start_times) != expected_slides:
        raise RuntimeError(
            "Slide timing requires one start time for every slide; received "
            f"{sorted(slide_start_times)}."
        )

    durations = []
    for slide_number in range(1, slide_count + 1):
        start_time = slide_start_times[slide_number]
        end_time = (
            slide_start_times[slide_number + 1]
            if slide_number < slide_count
            else video_intro_duration + speech_duration + outro_pause
        )
        duration = end_time - start_time
        if duration <= 0:
            raise RuntimeError(
                f"Slide {slide_number} has invalid anchor timing: "
                f"{start_time:.3f}s to {end_time:.3f}s."
            )
        durations.append(duration)
    return durations


def compute_slide_reveal_timestamps(results, slide_anchor_positions, slide_count, visual_slides=None):
    """以每個簡報短句所屬的 TTS 句索引與句內位置計算揭示時間，不再跨整頁抽樣。"""
    ordered = sorted(results, key=lambda item: item[0])
    durations = {idx: float(dur) for idx, _fn, _line, dur in ordered}
    starts = {}
    cursor = 0.0
    for idx, _fn, _line, dur in ordered:
        starts[idx] = cursor
        cursor += float(dur)

    reveal_map = {}
    visual_slides = visual_slides or []
    for slide_number in range(1, slide_count + 1):
        anchor = slide_anchor_positions.get(slide_number)
        if anchor is None:
            reveal_map[slide_number] = []
            continue

        anchor = max(0.0, float(anchor))
        anchor_index = int(anchor)
        anchor_fraction = anchor - anchor_index
        page_start = starts.get(anchor_index, cursor)
        if anchor_index in durations:
            page_start += durations[anchor_index] * anchor_fraction

        slide = visual_slides[slide_number - 1] if slide_number <= len(visual_slides) else {}
        timeline = slide.get("_body_timeline") or []
        stamps = []
        for item in timeline:
            try:
                dialogue_index = int(item["dialogue_index"])
                fragment_index = int(item.get("fragment_index", 0))
                fragment_count = max(1, int(item.get("fragment_count", 1)))
                duration = durations[dialogue_index]
                dialogue_start = starts[dialogue_index]
            except (KeyError, TypeError, ValueError):
                continue
            fraction = min(0.95, max(0.0, fragment_index / fragment_count))
            absolute_time = dialogue_start + duration * fraction
            stamps.append(max(0.0, absolute_time - page_start))

        # 第一個對應口播的重點在頁面切入時即顯示，避免片頭後出現空白簡報。
        if stamps:
            stamps[0] = 0.0
            stamps = [round(value, 3) for value in stamps]
            for index in range(1, len(stamps)):
                stamps[index] = max(stamps[index], stamps[index - 1] + 0.05)
        reveal_map[slide_number] = stamps

    return reveal_map


# ── Google TTS 語音合成 ──────────────────────────────────────────

TTS_SSML_BYTE_LIMIT = 4500


def _ssml_document(text):
    return f"<speak>{presentation.build_ssml_inner(text)}</speak>"


def _split_tts_text(text):
    """Google TTS 的 SSML 上限是 5000 bytes。超長口播先按標點切開。"""
    raw = str(text or "").strip()
    if not raw:
        return [raw]

    def fits(chunk):
        return len(_ssml_document(chunk).encode("utf-8")) <= TTS_SSML_BYTE_LIMIT

    if fits(raw):
        return [raw]

    pieces = [part for part in re.split(r"(?<=[，。！？、；：…!?;\n])", raw) if part]
    chunks = []
    buffer = ""
    for piece in pieces or [raw]:
        candidate = f"{buffer}{piece}"
        if buffer and not fits(candidate):
            chunks.append(buffer)
            buffer = piece
        else:
            buffer = candidate
    if buffer:
        chunks.append(buffer)

    fitted = []
    for chunk in chunks:
        if fits(chunk):
            fitted.append(chunk)
            continue
        start = 0
        while start < len(chunk):
            low, high, best = 1, len(chunk) - start, 1
            while low <= high:
                mid = (low + high) // 2
                if fits(chunk[start:start + mid]):
                    best = mid
                    low = mid + 1
                else:
                    high = mid - 1
            fitted.append(chunk[start:start + best])
            start += best
    return [part.strip() for part in fitted if part.strip()] or [raw]


def synth_audio_segment(item, file_prefix):
    tts_host = "".join(["https", "://", "texttospeech.", "googleapis.", "com"])
    tts_url = f"{tts_host}/v1/text:synthesize?key={os.environ['GOOGLE_TTS_KEY'].strip()}"

    idx, (role, txt) = item
    txt = script_writer.normalize_long_term_terms(txt)
    display_line = presentation.display_text(txt)
    # 雙軌分離：字幕／簡報保留原始格式，僅 TTS 發音用臨時副本做數字轉換。
    tts_speak_text = presentation.pronounce_for_tts(txt)
    v_name = "cmn-TW-Wavenet-B" if role == "男" else "cmn-TW-Wavenet-A"
    fallback_voice = "cmn-TW-Standard-B" if role == "男" else "cmn-TW-Standard-A"
    pitch = -1.5 if role == "男" else 1.5
    chunks = _split_tts_text(tts_speak_text)
    if len(chunks) > 1:
        print(f"口播第 {idx + 1} 句超過 TTS 5000 bytes，已切成 {len(chunks)} 段。")

    def request_tts(voice_name, spoken_text):
        payload_tts = json.dumps({
            "input": {"ssml": _ssml_document(spoken_text)},
            "voice": {"languageCode": "cmn-TW", "name": voice_name},
            "audioConfig": {"audioEncoding": "MP3", "pitch": pitch, "speakingRate": 1.25},
        }).encode("utf-8")
        req = urllib.request.Request(
            tts_url,
            data=payload_tts,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return json.loads(urllib.request.urlopen(req, timeout=30).read().decode("utf-8"))

    def synthesize_chunk(spoken_text):
        try:
            return request_tts(v_name, spoken_text)
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            try:
                error_message = json.loads(error_body).get("error", {}).get("message", error_body)
            except json.JSONDecodeError:
                error_message = error_body
            error_text = str(error_message).lower()
            unsupported_voice = e.code in (400, 404) and "voice" in error_text and any(
                phrase in error_text
                for phrase in ("not supported", "unsupported", "not available", "unavailable", "does not exist", "not valid", "invalid")
            )
            if not unsupported_voice:
                print(f"Google TTS API error ({e.code}) for voice '{v_name}': {error_message}")
                raise
            print(f"Google TTS voice '{v_name}' is unavailable; retrying with '{fallback_voice}'.")
            return request_tts(fallback_voice, spoken_text)

    fn = f"{file_prefix}_{idx:04d}.mp3"
    part_names = []
    for part_index, chunk in enumerate(chunks):
        part_name = fn if len(chunks) == 1 else f"{file_prefix}_{idx:04d}_p{part_index:02d}.mp3"
        response = synthesize_chunk(chunk)
        with open(part_name, "wb") as handle:
            handle.write(base64.b64decode(response["audioContent"]))
        part_names.append(part_name)
    if len(part_names) > 1:
        list_name = f"{file_prefix}_{idx:04d}_parts.txt"
        with open(list_name, "w", encoding="utf-8") as handle:
            for part_name in part_names:
                handle.write(f"file '{part_name}'\n")
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_name, "-c", "copy", fn],
            check=True,
        )
    dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", fn]).decode().strip())
    return idx, fn, display_line, dur


def create_outro_audio(outro_duration, outro_pause):
    outro_source = next(
        (path for path in ("outro.mp3", "info.mp3") if os.path.exists(path)),
        None,
    )
    if outro_source:
        audio_filter = (
            f"volume=0.35,apad=whole_dur={outro_duration:g},"
            f"afade=t=in:st=0:d=1,"
            f"afade=t=out:st={outro_duration - 2.5:g}:d=2.5"
        )
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", outro_source, "-t", f"{outro_duration:g}",
                "-af", audio_filter, "-ar", "24000", "-ac", "1",
                "-c:a", "libmp3lame", "-b:a", "64k", "outro_music.mp3",
            ],
            check=True,
        )
    else:
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                "-t", f"{outro_duration:g}", "-ar", "24000", "-ac", "1",
                "-c:a", "libmp3lame", "-b:a", "64k", "outro_music.mp3",
            ],
            check=True,
        )
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
            "-t", f"{outro_pause:g}", "-ar", "24000", "-ac", "1",
            "-c:a", "libmp3lame", "-b:a", "64k", "outro_pause.mp3",
        ],
        check=True,
    )


# ── ASS 字幕格式與斷句 ──────────────────────────────────────────

def fmt_ass_t(sec):
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def short_subtitle_visual_width(text):
    return sum(
        1.0
        if unicodedata.east_asian_width(char) in ("F", "W")
        else 0.5
        for char in text
        if char not in "\r\n"
    )


def split_short_subtitle_text(text, max_width=None, shrink_limit=None):
    width_limit = SHORT_SUBTITLE_MAX_WIDTH if max_width is None else max_width
    shrink_at = SHORT_SUBTITLE_SHRINK_LIMIT if shrink_limit is None else shrink_limit
    def trim_edges(value):
        return SHORT_SUBTITLE_EDGE_PUNCTUATION.sub("", value).strip()

    def split_at_midpoint(value):
        candidates = []
        for match in SHORT_SUBTITLE_BREAKS.finditer(value):
            left = trim_edges(value[:match.start()])
            right = trim_edges(value[match.end():])
            if left and right:
                candidates.append(
                    (
                        abs(
                            short_subtitle_visual_width(left)
                            - short_subtitle_visual_width(right)
                        ),
                        0,
                        left,
                        right,
                    )
                )

        for match in SHORT_SUBTITLE_CONNECTORS.finditer(value):
            for boundary in (match.start(), match.end()):
                left = trim_edges(value[:boundary])
                right = trim_edges(value[boundary:])
                if left and right:
                    candidates.append(
                        (
                            abs(
                                short_subtitle_visual_width(left)
                                - short_subtitle_visual_width(right)
                            ),
                            1,
                            left,
                            right,
                        )
                    )

        if candidates:
            _, _, left, right = min(candidates, key=lambda item: item[:2])
            return left, right

        number_spans = [
            (match.start(), match.end())
            for match in re.finditer(
                r"\$?\d{1,3}(?:,\d{3})+(?:\.\d+)?%?|\$?\d+(?:\.\d+)?%?",
                value,
            )
        ]
        boundaries = []
        for boundary in range(1, len(value)):
            if any(start < boundary < end for start, end in number_spans):
                continue
            left = trim_edges(value[:boundary])
            right = trim_edges(value[boundary:])
            if left and right:
                boundaries.append(
                    (
                        abs(
                            short_subtitle_visual_width(left)
                            - short_subtitle_visual_width(right)
                        ),
                        left,
                        right,
                    )
                )
        if not boundaries:
            return value, ""
        _, left, right = min(boundaries, key=lambda item: item[0])
        return left, right

    def split_clause(clause):
        clause = re.sub(r"\s+", " ", trim_edges(clause))
        width = short_subtitle_visual_width(clause)
        if not clause:
            return []
        if width <= shrink_at:
            scale = (
                SHORT_SUBTITLE_SHRINK_PERCENT
                if width > width_limit
                else 100
            )
            return [(clause, scale)]

        left, right = split_at_midpoint(clause)
        if not right:
            return [(clause, SHORT_SUBTITLE_SHRINK_PERCENT)]
        return split_clause(left) + split_clause(right)

    chunks = []
    for sentence in SHORT_SUBTITLE_SENTENCE_END.split(text):
        chunks.extend(split_clause(sentence))
    return chunks


def wrap_by_width(draw, text, font, max_width):
    wrapped_lines = []
    for paragraph in text.split("\n"):
        line = ""
        for ch in paragraph:
            candidate = line + ch
            bbox = draw.textbbox((0, 0), candidate, font=font)
            if line and bbox[2] - bbox[0] > max_width:
                wrapped_lines.append(line)
                line = ch
            else:
                line = candidate
        wrapped_lines.append(line)
    return wrapped_lines


def highlight_key_numbers(text, state=None):
    escaped_text = html.escape(str(text))
    highlight_state = state if state is not None else {"remaining": 3}
    # 以完整語意片語為標示單位，避免只把「200」或「70」染黃，
    # 卻讓「週均線」或「RSI」留在黃色區塊外面。
    important_number_pattern = re.compile(
        r"(?P<money>[-+]?(?:NT\$|US\$|USD\$|\$)\s?[-+]?\d+(?:,\d{3})*(?:\.\d+)?(?:\s?(?:美元|USD))?)"
        r"|(?P<metric_group>(?:恐慌(?:與貪婪)?指數|市場情緒指數|RSI|MVRV(?:\s*Z-Score)?|Z-Score|SOPR|NUPL)\s*[:：]?\s*[+-]?\d+(?:\.\d+)?(?:\s*/\s*100)?%?)"
        r"|(?P<technical_group>(?:[+-]?\d+(?:,\d{3})*(?:\.\d+)?\s*(?:週|日|月|年)?\s*/\s*[+-]?\d+(?:,\d{3})*(?:\.\d+)?\s*(?:週|日|月|年)?|[+-]?\d+(?:,\d{3})*(?:\.\d+)?\s*(?:週|日|月|年)?)(?:均線|線|高點|低點|支撐|壓力))"
        r"|(?P<percent>(?:(?:上漲|下跌|漲幅|跌幅|回撤|報酬率|市占率|增幅|降幅)\s*)?[-+]?\d+(?:,\d{3})*(?:\.\d+)?\s?%)"
        r"|(?P<unit_group>[-+]?\d+(?:,\d{3})*(?:\.\d+)?\s*(?:萬美元|億美元|美元|萬枚|億枚|枚|顆|倍|筆|家|人|次|年|月|週|日))"
    )

    def highlight(match):
        if highlight_state.get("remaining", 0) <= 0:
            return match.group(0)
        # 將完整匹配片語一次包進同一個黃色標籤，不拆開名稱、數值與單位。
        highlight_state["remaining"] -= 1
        return f'<span class="hl">{match.group(0)}</span>'

    return important_number_pattern.sub(highlight, escaped_text)


# ── Playwright 簡報渲染 ──────────────────────────────────────────

def _resolve_reveal_timestamps(reveal_timestamps, node_count, window_sec):
    """將重點揭示點對齊整張投影片的真實口播時長，而非壓縮到短錄影窗口。"""
    if node_count <= 0:
        return []

    window = max(0.0, float(window_sec))
    values = []
    if isinstance(reveal_timestamps, (list, tuple)):
        for item in reveal_timestamps:
            try:
                value = float(item)
            except (TypeError, ValueError):
                continue
            if value >= 0:
                values.append(min(value, window))

    # TTS 時間點包含本頁所有口播句。節點數通常少於句數，因此均勻抽樣
    # 整段口播的時間軸，避免所有重點只在頁面前段出現、後半段長時間靜止。
    if len(values) >= node_count:
        if node_count == 1:
            selected = [values[0]]
        else:
            selected = [
                values[round(index * (len(values) - 1) / (node_count - 1))]
                for index in range(node_count)
            ]
    else:
        # 標記或口播句數不足時，退回整頁均勻揭示；不混接不連續的真實時間點。
        start = min(window * 0.10, 0.5)
        end = max(start, window * 0.88)
        step = (end - start) / (node_count - 1) if node_count > 1 else 0.0
        selected = [start + step * index for index in range(node_count)]

    ordered = []
    last = -1.0
    min_gap = min(0.05, window / max(node_count * 2, 1))
    for value in selected:
        value = min(window, max(float(value), last + min_gap))
        ordered.append(round(value, 3))
        last = value
    return ordered


def render_animated_slide(idx, slide_data, dur_sec, out_mp4, browser, reveal_timestamps=None):
    if browser is None:
        raise RuntimeError("Playwright Chromium must be started before rendering slides.")
    if dur_sec <= 0:
        raise ValueError(f"Slide {idx} duration must be positive.")

    highlights = slide_data.get("highlights", [])
    if isinstance(highlights, str):
        highlights = highlights.splitlines()
    # 強制保底：任何原因導致重點為空時，從標題／內文切出至少 3 張卡，
    # 確保畫面永不出現「只有箭頭／VS 章、卡片全隱形」的空白頁。
    highlights = [str(item).strip() for item in (highlights or []) if str(item).strip()]
    if not highlights:
        fallback_title = script_writer.clean_slide_title(slide_data.get("title", "")).strip()
        fallback_body = script_writer.clean_slide_text(slide_data.get("body", "")).strip()
        for source in (fallback_title, fallback_body):
            for line in str(source).splitlines():
                line = line.strip()
                if line and line not in highlights:
                    highlights.append(line)
        highlights = highlights[:3] or [fallback_title or "先看結構再談價格"]
    reveal_count = 0
    highlight_state = {"remaining": 3}
    title_markup = highlight_key_numbers(
        script_writer.clean_slide_title(slide_data.get("title", "")), highlight_state
    )
    chart_markup = ""
    chart_path = slide_data.get("chart_path")
    if chart_path:
        with open(chart_path, "rb") as chart_file:
            chart_data = base64.b64encode(chart_file.read()).decode("ascii")
        chart_markup = (
            '<div class="visual chart"><img alt="" '
            f'src="data:image/png;base64,{chart_data}"></div>'
        )
    elif slide_data.get("chart_type") == "gauge":
        raw_gauge_value = slide_data.get("gauge_value")
        if raw_gauge_value is None:
            chart_markup = (
                '<div class="visual gauge">'
                '<div class="gauge-title">市場情緒指數</div>'
                '</div>'
            )
        else:
            gauge_value = min(100, max(0, int(raw_gauge_value)))
            chart_markup = (
                '<div class="visual gauge">'
                '<div class="gauge-title">市場情緒指數</div>'
                '<div class="gauge-track">'
                '<span class="fear"></span><span class="caution"></span>'
                '<span class="neutral"></span><span class="greed"></span>'
                '<span class="extreme-greed"></span></div>'
                f'<div class="gauge-marker" style="left:{gauge_value}%"></div>'
                f'<div class="gauge-value">{gauge_value}<small> / 100</small></div>'
                '</div>'
            )

    raw_layout = slide_data.get("layout")
    layout_alias = {
        "compare": "versus_clash",
        "chart": "chart_spotlight",
        "hero": "impact_quote",
        "summary": "impact_quote",
    }
    layout = layout_alias.get(raw_layout, raw_layout)
    if layout not in {"impact_quote", "versus_clash", "chart_spotlight", "timeline_steps"}:
        layout = "chart_spotlight" if chart_markup else "impact_quote"
    if layout == "chart_spotlight" and not chart_markup:
        layout = "impact_quote"
    if layout == "chart_spotlight":
        highlights = highlights[:5]

    list_markup = ""
    if layout == "chart_spotlight":
        spotlight_items = [item for item in highlights if str(item).strip()]
        reveal_count = len(spotlight_items)
        list_markup = "".join(
            f'<li class="reveal-node">{highlight_key_numbers(script_writer.clean_slide_text(item), highlight_state)}</li>'
            for item in spotlight_items
        )
    slide_caption = script_writer.clean_slide_text(str(slide_data.get("caption", ""))).strip()
    chart_caption_markup = (
        f'<li class="chart-caption">{html.escape(slide_caption)}</li>'
        if slide_caption and layout == "chart_spotlight"
        else ""
    )

    if layout == "versus_clash":
        pos_raw = slide_data.get("compare_positive") or (highlights[0] if highlights else slide_data.get("title", ""))
        risk_raw = slide_data.get("compare_risk") or (highlights[1] if len(highlights) > 1 else "短線量能未確認前，波動與回撤風險仍在。")
        positive_markup = highlight_key_numbers(
            script_writer.clean_slide_text(pos_raw), highlight_state
        )
        risk_markup = highlight_key_numbers(
            script_writer.clean_slide_text(risk_raw), highlight_state
        )
        content_markup = (
            '<section class="content versus-clash-layout">'
            '<article class="versus-panel bear reveal-node">'
            '<header class="versus-badge bear-badge">空方論點 ／ 籌碼風險</header>'
            f'<div class="versus-body"><ul><li>{risk_markup}</li></ul></div></article>'
            '<div class="vs-emblem" aria-hidden="true"><span>VS</span></div>'
            '<article class="versus-panel bull reveal-node">'
            '<header class="versus-badge bull-badge">多方反擊 ／ 支撐依據</header>'
            f'<div class="versus-body"><ul><li>{positive_markup}</li></ul></div></article>'
            '</section>'
        )
        reveal_count = 2
    elif layout == "chart_spotlight":
        content_markup = (
            '<section class="content chart-spotlight-layout">'
            f'<div class="spotlight-visual">{chart_markup}</div>'
            '<aside class="spotlight-card">'
            '<div class="spotlight-badge"><i class="pulse-dot"></i>關鍵價位與結構結論</div>'
            f'<ul>{list_markup}{chart_caption_markup}</ul>'
            '</aside></section>'
        )
    elif layout == "timeline_steps":
        default_step_tags = ["情境一：放量突破", "情境二：縮量回測", "情境三：跌破防守線"]
        step_tones = ["step-bull", "step-neutral", "step-bear"]
        cards_html = []
        step_items = [str(item).strip() for item in highlights if str(item).strip()][:3]
        if not step_items:
            step_items = [str(slide_data.get("title") or "觀察關鍵價位與量能變化")]
        for s_idx, raw_step in enumerate(step_items):
            tag_label = default_step_tags[s_idx % len(default_step_tags)]
            body_text = raw_step
            for sep in ("｜", "："):
                if sep in raw_step:
                    parts = raw_step.split(sep, 1)
                    if 2 <= len(parts[0].strip()) <= 16 and parts[1].strip():
                        tag_label = parts[0].strip()
                        body_text = parts[1].strip()
                        break
            tone_cls = step_tones[s_idx % len(step_tones)]
            body_markup = highlight_key_numbers(script_writer.clean_slide_text(body_text), highlight_state)
            if s_idx > 0:
                cards_html.append('<div class="timeline-arrow" aria-hidden="true">➔</div>')
            cards_html.append(
                f'<article class="timeline-card {tone_cls} reveal-node" style="--step-idx:{s_idx}">'
                f'<div class="step-pill">{html.escape(tag_label)}</div>'
                f'<p class="step-text">{body_markup}</p>'
                '</article>'
            )
        reveal_count = len(step_items)
        content_markup = (
            '<section class="content timeline-steps-layout">'
            + "".join(cards_html)
            + '</section>'
        )
    else:
        clean_items = [str(item).strip() for item in highlights if str(item).strip()]
        core_quote = clean_items[0] if clean_items else str(slide_data.get("title") or "")
        sub_lines = clean_items[1:3] if len(clean_items) > 1 else []
        quote_markup = highlight_key_numbers(script_writer.clean_slide_text(core_quote), highlight_state)
        sub_markup = "".join(
            f'<p class="impact-sub reveal-node">{highlight_key_numbers(script_writer.clean_slide_text(sub), highlight_state)}</p>'
            for sub in sub_lines
        )
        reveal_count = 1 + len(sub_lines)
        content_markup = (
            '<section class="content impact-quote-layout">'
            '<div class="impact-quote-box">'
            '<span class="impact-tag">CORE INSIGHT // 核心洞察</span>'
            f'<blockquote class="impact-quote-text reveal-node">{quote_markup}</blockquote>'
            '<div class="impact-divider"></div>'
            f'{sub_markup}'
            '</div></section>'
        )

    frame_class = f"frame layout-{layout}" + (" compact" if slide_data.get("compact") else "")
    label_markup = html.escape(str(slide_data.get("label", "")))
    accent = html.escape(str(slide_data.get("accent", "#fbbf24")))
    ticker_markup = "".join(
        '<div class="ticker-item">'
        f'<span class="ticker-label">{html.escape(str(item.get("label", "")))}</span>'
        f'<strong class="{html.escape(str(item.get("tone", "neutral")))}">'
        f'{html.escape(str(item.get("value", "")))}</strong>'
        f'<span class="ticker-detail {html.escape(str(item.get("tone", "neutral")))}">'
        f'{html.escape(str(item.get("detail", "")))}</span></div>'
        for item in slide_data.get("ticker_items", [])
    )
    # 讓瀏覽器動畫與 TTS 使用同一段真實時間軸。
    # 舊版只錄 6 秒再以 tpad 凍結最後一格，造成長頁面大部分時間完全靜止。
    # 海報前導後多錄 0.25 秒緩衝，並以 clone padding 保證投影片不短於口播時間軸。
    capture_preroll = 0.75
    tail_record_seconds = max(0.1, float(dur_sec) - capture_preroll + 0.25)
    pace = max(0.22, min(0.55, float(dur_sec) / 16.0))
    if reveal_timestamps is None:
        reveal_timestamps = slide_data.get("reveal_timestamps")
    # 錄影最初 0.75 秒由已載入的海報畫面補上；揭示時間同步加上錄影預熱偏移，
    # 讓輸出影片的 0 秒仍對應口播時間軸的 0 秒。
    reveal_list = [
        round(value + capture_preroll, 3)
        for value in _resolve_reveal_timestamps(reveal_timestamps, reveal_count, float(dur_sec))
    ]
    reveal_ts_json = json.dumps(reveal_list)

    # B-Roll 實景動態底圖：有 broll_path 時於 slide-stage 底層循環播放，
    # 並疊加 55% 半透明深色暗角遮罩，確保前景字卡高對比可讀。
    broll_path = slide_data.get("broll_path")
    broll_markup = ""
    if broll_path and os.path.exists(broll_path):
        try:
            with open(broll_path, "rb") as broll_file:
                broll_data = base64.b64encode(broll_file.read()).decode("ascii")
            broll_markup = (
                '<video class="broll-bg" autoplay muted loop playsinline '
                f'src="data:video/mp4;base64,{broll_data}"></video>'
                '<div class="broll-veil"></div>'
            )
        except OSError:
            broll_markup = ""

    insights = slide_data.get("insights") or {}
    insight_labels = slide_data.get("insight_labels") or {}
    mvrv_pct = int(insights.get("mvrv_pct") or 40)
    mvrv_markup = ""
    if insights.get("mvrv_available"):
        mvrv_markup = (
            f"<article><small>{html.escape(str(insight_labels.get('mvrv', 'MVRV Z-Score')))}</small>"
            f"<strong>{html.escape(str(insights.get('mvrv', '')))}</strong>"
            f"<em>{html.escape(str(insights.get('zone', '')))}</em>"
            "<div class=\"mvrv-bar\"><i></i></div></article>"
        )
    insight_markup = (
        mvrv_markup
        + f"<article><small>{html.escape(str(insight_labels.get('level', '支撐 / 壓力')))}</small>"
        f"<strong class=\"pulse\">{html.escape(str(insights.get('support', '—')))}</strong>"
        f"<em>{html.escape(str(insights.get('resistance', '—')))}</em></article>"
        f"<article><small>{html.escape(str(insight_labels.get('indicator', 'RSI / 均線')))}</small>"
        f"<strong>{html.escape(str(insights.get('rsi', '—')))}</strong>"
        f"<em>{html.escape(str(insights.get('sma', '')))}</em></article>"
        f"<article><small>{html.escape(str(insight_labels.get('conclusion', '今日結論')))}</small>"
        f"<strong>{html.escape(str(insights.get('headline', '紀律觀察')))}</strong>"
        f"<em>{html.escape(str(insights.get('detail', '')))}</em></article>"
    )
    html_doc = f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width={MAIN_VIDEO_WIDTH}, initial-scale=1">
<style>
{presentation.dark_css(bool(broll_markup))}
* {{ box-sizing: border-box; }}
html, body {{ width: {MAIN_VIDEO_WIDTH}px; height: {MAIN_VIDEO_HEIGHT}px; margin: 0; overflow: hidden; }}
body {{
  position: relative; width: {MAIN_VIDEO_WIDTH}px; height: {MAIN_VIDEO_HEIGHT}px;
  color: #f8fafc; background: #090d16;
  font-family: 'Noto Sans CJK TC', sans-serif;
}}
.slide-stage {{
  position: relative; width: {MAIN_DESIGN_WIDTH}px; height: {MAIN_DESIGN_HEIGHT}px;
  overflow: hidden; background: #090d16;
  transform: scale({MAIN_OUTPUT_SCALE}); transform-origin: top left;
}}
.slide-stage::before {{
  position: absolute; inset: -20%; content: ""; opacity: .46;
  background:
    radial-gradient(ellipse at 18% 22%, rgba(56,189,248,.18), transparent 34%),
    radial-gradient(ellipse at 82% 55%, rgba(167,139,250,.15), transparent 38%),
    linear-gradient(rgba(56,189,248,.045) 1px, transparent 1px),
    linear-gradient(90deg, rgba(56,189,248,.045) 1px, transparent 1px);
  background-size: auto, auto, 48px 48px, 48px 48px;
  animation: gridFlow 18s linear infinite;
}}
@keyframes gridFlow {{
  from {{ transform: translate3d(0, 0, 0); }}
  to {{ transform: translate3d(48px, 24px, 0); }}
}}
/* 四組場景光色輪替，避免每頁都是相同的深藍網格背景。 */
.slide-stage.scene-1::before {{
  background:
    radial-gradient(ellipse at 12% 78%, rgba(244,63,94,.20), transparent 36%),
    radial-gradient(ellipse at 88% 18%, rgba(245,158,11,.17), transparent 38%),
    linear-gradient(rgba(251,191,36,.045) 1px, transparent 1px),
    linear-gradient(90deg, rgba(251,191,36,.045) 1px, transparent 1px);
  background-size: auto, auto, 54px 54px, 54px 54px;
  animation: gridFlow 13s linear infinite;
}}
.slide-stage.scene-2::before {{
  background:
    radial-gradient(ellipse at 18% 18%, rgba(167,139,250,.22), transparent 36%),
    radial-gradient(ellipse at 82% 78%, rgba(52,211,153,.16), transparent 40%),
    linear-gradient(rgba(167,139,250,.045) 1px, transparent 1px),
    linear-gradient(90deg, rgba(52,211,153,.045) 1px, transparent 1px);
  background-size: auto, auto, 44px 44px, 44px 44px;
  animation: gridFlow 21s linear infinite reverse;
}}
.slide-stage.scene-3::before {{
  background:
    radial-gradient(ellipse at 50% 8%, rgba(56,189,248,.22), transparent 38%),
    radial-gradient(ellipse at 50% 92%, rgba(244,114,182,.14), transparent 42%),
    linear-gradient(rgba(56,189,248,.045) 1px, transparent 1px),
    linear-gradient(90deg, rgba(56,189,248,.045) 1px, transparent 1px);
  background-size: auto, auto, 62px 62px, 62px 62px;
  animation: gridFlow 16s linear infinite;
}}
.broll-bg {{
  /* Pixabay 全畫面插播：與簡報輪流出現，不再左右分欄。 */
  position: absolute; inset: 0; z-index: 1; width: 100%; height: 100%;
  object-fit: cover; opacity: 0; background: #050914;
  animation: brollInterlude var(--broll-cycle, 18s) linear infinite;
}}
@keyframes brollInterlude {{
  0%, 68%, 94%, 100% {{ opacity: 0; }}
  72%, 90% {{ opacity: 1; }}
}}
 .slide-stage.has-broll .ticker, .slide-stage.has-broll .frame, .slide-stage.has-broll .metric-rail {{
  animation: slideInterlude var(--broll-cycle, 18s) linear infinite;
}}
@keyframes slideInterlude {{
  0%, 68%, 94%, 100% {{ opacity: 1; }}
  72%, 90% {{ opacity: 0; }}
}}
.broll-veil {{
  display: none;
}}
.ticker {{
  position: absolute; inset: 0 0 auto; z-index: 2; height: 84px;
  display: grid; grid-template-columns: repeat(4, minmax(0, 1fr));
  align-items: center; padding: 0 26px;
  background: rgba(15, 23, 42, .92);
  border-bottom: 1px solid rgba(251, 191, 36, .72);
  box-shadow: 0 2px 14px rgba(251, 191, 36, .12);
  backdrop-filter: blur(12px);
}}
.ticker-item {{
  min-width: 0; display: flex; justify-content: center; align-items: baseline;
  gap: 8px; padding: 0 10px; white-space: nowrap; overflow: hidden;
}}
.ticker-item + .ticker-item {{ border-left: 1px solid rgba(148, 163, 184, .22); }}
.ticker-label {{ color: #cbd5e1; font-size: 18px; font-weight: 700; }}
.ticker-item strong {{ color: #f8fafc; font-size: 28px; font-weight: 800; font-variant-numeric: tabular-nums; }}
.ticker-item strong.positive {{ color: #4ade80; }}
.ticker-item strong.negative {{ color: #fb7185; }}
.ticker-item strong.neutral {{ color: #f8fafc; }}
.ticker-detail {{ font-size: 16px; overflow: hidden; text-overflow: ellipsis; }}
.ticker-detail.positive {{ color: #4ade80; }}
.ticker-detail.negative {{ color: #fb7185; }}
.ticker-detail.neutral {{ color: #cbd5e1; }}
.sr-only {{
  position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
  overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0;
}}
.frame {{
  position: absolute; left: 48px; right: 34%; top: 110px; bottom: 350px;
  z-index: 3;
  padding: 24px 36px; border: 2px solid rgba(251,191,36,.72);
  border-radius: 18px; background: rgba(11,17,32,.9);
  box-shadow: 0 0 36px rgba(56,189,248,.14), inset 0 0 36px rgba(251,191,36,.04);
  display: flex; flex-direction: column; overflow: hidden;
}}
.frame.layout-impact_quote {{
  border-color: rgba(245,158,11,.85);
  background:
    radial-gradient(circle at 50% 45%, rgba(245,158,11,.10), transparent 60%),
    radial-gradient(circle at 80% 20%, rgba(244,63,94,.08), transparent 45%),
    rgba(9,13,24,.94);
  box-shadow: 0 0 46px rgba(245,158,11,.18), inset 0 0 48px rgba(244,63,94,.08);
}}
h1 {{
  position: relative; flex: 0 0 auto; max-width: 100%; margin: 0 0 18px;
  color: #fff7d6; font-size: 42px; line-height: 1.18; font-weight: 800;
  text-shadow: 0 2px 0 #111827, 0 0 18px rgba(245, 158, 11, .45);
  animation: titleIn .8s cubic-bezier(.2,.8,.2,1) both;
}}
@keyframes titleIn {{
  from {{ opacity: 0; transform: translateX(-58px); }}
  to {{ opacity: 1; transform: translateX(0); }}
}}
.content {{
  min-height: 0; flex: 1 1 auto; display: grid;
  grid-template-columns: minmax(0, 1fr); align-items: stretch;
}}
ul {{ list-style: none; margin: 0; padding: 0; min-width: 0; }}
li {{
  position: relative; margin: 0 0 12px; padding-left: 24px;
  color: #e2e8f0; font-size: 24px; line-height: 1.38; overflow-wrap: anywhere;
  animation: fadeInUp calc(var(--pace) * 1.4) ease-out both;
}}
li::before {{
  position: absolute; top: .56em; left: 0; width: 9px; height: 9px;
  border-radius: 50%; background: var(--accent); content: "";
  box-shadow: 0 0 12px var(--accent);
}}
li:nth-child(1) {{ animation-delay: calc(var(--pace) * 0.35); }}
li:nth-child(2) {{ animation-delay: calc(var(--pace) * 1.0); }}
li:nth-child(3) {{ animation-delay: calc(var(--pace) * 1.65); }}
li:nth-child(4) {{ animation-delay: calc(var(--pace) * 2.3); }}
li:nth-child(5) {{ animation-delay: calc(var(--pace) * 2.95); }}
li:nth-child(n+6) {{ animation-delay: calc(var(--pace) * 3.6); }}
.frame.compact h1,
.frame.fit-compact h1,
.frame.fit-tight h1,
.frame.fit-min h1 {{ font-size: 38px; margin-bottom: 10px; }}
.frame.compact li {{ margin-bottom: 6px; padding-left: 20px; font-size: 20px; line-height: 1.24; }}
.frame.compact li::before {{ width: 7px; height: 7px; }}
.frame.fit-compact {{ padding: 16px 24px; }}
.frame.fit-tight {{ padding: 12px 16px; }}
.frame.fit-tight .versus-clash-layout {{ gap: 20px; }}
.frame.fit-tight .versus-panel {{ padding: 14px; }}
.frame.fit-tight .timeline-steps-layout {{ gap: 14px; }}
.frame.fit-tight .timeline-card {{ padding: 14px; }}
.frame.fit-min {{ padding: 8px 12px; }}
.frame.fit-min h1 {{ margin-bottom: 6px; font-size: 34px; }}
.frame.fit-min li {{ margin-bottom: 3px; font-size: 18px; line-height: 1.15; }}
@keyframes fadeInUp {{
  from {{ opacity: 0; transform: translateY(24px); }}
  to {{ opacity: 1; transform: translateY(0); }}
}}
.hl {{ position: relative; z-index: 0; color: #fff7cc; white-space: nowrap; animation: levelPulse calc(var(--pace) * 2) ease-in-out 2; }}
.hl::after {{
  position: absolute; left: 0; bottom: -.08em; z-index: -1; width: 100%; height: .34em;
  background: linear-gradient(90deg, rgba(251,191,36,.82), rgba(251,191,36,.18));
  content: ""; transform: scaleX(0); transform-origin: left center;
  animation: highlightIn .65s ease-out .45s forwards;
}}
@keyframes highlightIn {{ to {{ transform: scaleX(1); }} }}

/* ── 版型一：impact_quote（巨幅新聞頭條金句幕） ── */
.impact-quote-layout {{
  display: flex; align-items: center; justify-content: center;
  padding: 10px 24px;
}}
.impact-quote-box {{
  position: relative; width: 100%; max-width: 1520px;
  padding: 34px 48px; border-radius: 20px; text-align: center;
  border: 1px solid rgba(251, 191, 36, .42);
  background: linear-gradient(160deg, rgba(15, 23, 42, .85), rgba(10, 15, 28, .92));
  box-shadow: 0 0 40px rgba(251, 191, 36, .12), inset 0 0 28px rgba(244, 63, 94, .06);
  animation: heroPop calc(var(--pace) * 1.8) cubic-bezier(.16,1,.3,1) both;
}}
.impact-quote-box::before, .impact-quote-box::after {{
  content: ""; position: absolute; width: 32px; height: 32px;
  border-color: #fbbf24; border-style: solid; opacity: .85;
}}
.impact-quote-box::before {{ top: -2px; left: -2px; border-width: 4px 0 0 4px; border-radius: 12px 0 0 0; }}
.impact-quote-box::after {{ bottom: -2px; right: -2px; border-width: 0 4px 4px 0; border-radius: 0 0 12px 0; }}
.impact-tag {{
  display: inline-block; margin-bottom: 16px; padding: 6px 18px;
  border-radius: 999px; font-size: 16px; font-weight: 800; letter-spacing: 2px;
  color: #fde68a; background: rgba(245, 158, 11, .16);
  border: 1px solid rgba(251, 191, 36, .45);
}}
.impact-quote-text {{
  margin: 0; color: #ffffff; font-size: 56px; font-weight: 900; line-height: 1.26;
  text-shadow: 0 4px 24px rgba(251, 191, 36, .35), 0 0 42px rgba(244, 63, 94, .22);
  animation: quoteGlow 4s ease-in-out infinite;
}}
@keyframes quoteGlow {{
  0%, 100% {{ text-shadow: 0 4px 20px rgba(251, 191, 36, .28), 0 0 32px rgba(244, 63, 94, .18); }}
  50% {{ text-shadow: 0 4px 32px rgba(251, 191, 36, .52), 0 0 48px rgba(244, 63, 94, .34); }}
}}
.impact-divider {{
  width: 140px; height: 4px; margin: 22px auto 18px; border-radius: 99px;
  background: linear-gradient(90deg, #f43f5e, #fbbf24, #38bdf8);
}}
.impact-sub {{
  margin: 6px auto 0; max-width: 88%; color: #cbd5e1;
  font-size: 27px; font-weight: 600; line-height: 1.42;
  animation: fadeInUp calc(var(--pace) * 1.6) ease-out calc(var(--pace) * 0.7) both;
}}

/* ── 版型二：versus_clash（多空硬核對決 VS 分割幕） ── */
.versus-clash-layout {{
  position: relative; grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 34px; align-items: stretch;
}}
.versus-panel {{
  min-width: 0; padding: 26px 28px; border-radius: 18px; border: 2px solid;
  display: flex; flex-direction: column; overflow: hidden;
}}
.versus-panel.bear {{
  border-color: rgba(244, 63, 94, .82);
  background: linear-gradient(150deg, rgba(69, 10, 26, .84), rgba(15, 23, 42, .92));
  box-shadow: 0 0 30px rgba(244, 63, 94, .18), inset 0 0 24px rgba(244, 63, 94, .08);
  animation: compareLeft calc(var(--pace) * 1.6) cubic-bezier(.2,.8,.2,1) .15s both;
}}
.versus-panel.bull {{
  border-color: rgba(52, 211, 153, .82);
  background: linear-gradient(150deg, rgba(6, 78, 59, .76), rgba(15, 23, 42, .92));
  box-shadow: 0 0 30px rgba(52, 211, 153, .18), inset 0 0 24px rgba(52, 211, 153, .08);
  animation: compareRight calc(var(--pace) * 1.6) cubic-bezier(.2,.8,.2,1) .45s both;
}}
.versus-badge {{
  display: inline-block; align-self: flex-start; margin-bottom: 18px; padding: 7px 18px;
  border-radius: 999px; font-size: 21px; font-weight: 800; letter-spacing: 1px;
}}
.bear-badge {{ color: #fecdd3; background: rgba(244, 63, 94, .24); border: 1px solid rgba(244, 63, 94, .65); }}
.bull-badge {{ color: #a7f3d0; background: rgba(16, 185, 129, .24); border: 1px solid rgba(52, 211, 153, .65); }}
.versus-body {{ flex: 1 1 auto; display: flex; align-items: center; }}
.versus-panel li {{ margin: 0; padding-left: 22px; font-size: 27px; line-height: 1.48; color: #f1f5f9; }}
.versus-panel.bear li::before {{ background: #fb7185; box-shadow: 0 0 12px #fb7185; }}
.versus-panel.bull li::before {{ background: #34d399; box-shadow: 0 0 12px #34d399; }}
.vs-emblem {{
  position: absolute; left: 50%; top: 50%; z-index: 8;
  width: 78px; height: 78px; transform: translate(-50%, -50%);
  border-radius: 50%; display: flex; align-items: center; justify-content: center;
  background: radial-gradient(circle, #fde047, #f59e0b 65%, #ea580c);
  color: #090d16; font-size: 32px; font-weight: 900; font-style: italic;
  box-shadow: 0 0 28px rgba(251, 191, 36, .85), 0 0 0 8px rgba(9, 13, 22, .94);
  animation: vsPulse 2.4s ease-in-out infinite;
}}
@keyframes vsPulse {{
  0%, 100% {{ transform: translate(-50%, -50%) scale(1); box-shadow: 0 0 24px rgba(251, 191, 36, .75), 0 0 0 8px rgba(9, 13, 22, .94); }}
  50% {{ transform: translate(-50%, -50%) scale(1.08); box-shadow: 0 0 40px rgba(251, 191, 36, 1), 0 0 0 8px rgba(9, 13, 22, .94); }}
}}
@keyframes compareLeft {{ from {{ opacity: 0; transform: translateX(-36px); }} to {{ opacity: 1; transform: translateX(0); }} }}
@keyframes compareRight {{ from {{ opacity: 0; transform: translateX(36px); }} to {{ opacity: 1; transform: translateX(0); }} }}

/* ── 版型三：chart_spotlight（全屏數據圖表焦點幕 72% / 28%） ── */
.chart-spotlight-layout {{
  grid-template-columns: minmax(0, 72fr) minmax(0, 28fr);
  gap: 24px; align-items: stretch;
}}
.spotlight-visual {{
  min-width: 0; height: 100%; display: flex; align-items: center; justify-content: center;
  padding: 12px; border-radius: 16px;
  border: 1px solid rgba(56, 189, 248, .35);
  background: rgba(8, 13, 24, .78);
  animation: chartIn .8s cubic-bezier(.2,.8,.2,1) .25s both;
}}
.spotlight-visual .visual {{ max-height: 550px; height: 100%; }}
.spotlight-visual .chart img {{ max-height: 520px; width: 100%; height: 100%; object-fit: contain; }}
.spotlight-card {{
  min-width: 0; padding: 20px 22px; border-radius: 16px;
  border: 1px solid rgba(251, 191, 36, .52);
  background: linear-gradient(170deg, rgba(15, 23, 42, .92), rgba(9, 14, 26, .95));
  box-shadow: 0 0 26px rgba(251, 191, 36, .12);
  display: flex; flex-direction: column; justify-content: center; gap: 14px;
}}
.spotlight-badge {{
  display: inline-flex; align-items: center; gap: 8px; align-self: flex-start;
  padding: 6px 14px; border-radius: 999px; font-size: 16px; font-weight: 800;
  color: #fde68a; background: rgba(245, 158, 11, .18);
  border: 1px solid rgba(251, 191, 36, .55);
  box-shadow: 0 0 16px rgba(245, 158, 11, .28);
}}
.pulse-dot {{
  width: 9px; height: 9px; border-radius: 50%; background: #fbbf24;
  box-shadow: 0 0 10px #fbbf24; animation: dotPulse 1.5s ease-in-out infinite;
}}
@keyframes dotPulse {{
  0%, 100% {{ opacity: 1; transform: scale(1); }}
  50% {{ opacity: .45; transform: scale(1.35); }}
}}
.spotlight-card ul {{ display: grid; grid-template-columns: 1fr; gap: 10px; }}
.spotlight-card li {{ margin: 0; padding-left: 18px; font-size: 21px; line-height: 1.4; }}
.spotlight-card li::before {{ top: .52em; width: 7px; height: 7px; }}
.spotlight-card li.chart-caption {{
  margin-top: 4px; padding: 10px 12px; border: 1px solid rgba(251,191,36,.42);
  border-radius: 10px; color: #fde68a; background: rgba(120,83,9,.18);
  font-size: 16px; line-height: 1.35;
}}
.spotlight-card li.chart-caption::before {{ display: none; }}

/* ── 版型四：timeline_steps（因果推演／24小時驗證劇本幕） ── */
.timeline-steps-layout {{
  display: flex; align-items: stretch; justify-content: space-between; gap: 16px;
  padding: 10px 4px;
}}
.timeline-card {{
  flex: 1 1 0; min-width: 0; padding: 24px 22px; border-radius: 18px;
  border: 2px solid; background: rgba(11, 18, 34, .88);
  display: flex; flex-direction: column; gap: 16px;
  animation: heroPop calc(var(--pace) * 1.5) cubic-bezier(.16,1,.3,1) both;
  animation-delay: calc(var(--step-idx) * var(--pace) * 0.9);
}}
.timeline-card.step-bull {{ border-color: rgba(52, 211, 153, .75); box-shadow: 0 0 24px rgba(52, 211, 153, .14); }}
.timeline-card.step-neutral {{ border-color: rgba(251, 191, 36, .75); box-shadow: 0 0 24px rgba(251, 191, 36, .14); }}
.timeline-card.step-bear {{ border-color: rgba(244, 63, 94, .75); box-shadow: 0 0 24px rgba(244, 63, 94, .14); }}
.step-pill {{
  display: inline-block; align-self: flex-start; padding: 7px 16px;
  border-radius: 999px; font-size: 19px; font-weight: 800;
}}
.step-bull .step-pill {{ color: #a7f3d0; background: rgba(16, 185, 129, .24); border: 1px solid rgba(52, 211, 153, .65); }}
.step-neutral .step-pill {{ color: #fde68a; background: rgba(245, 158, 11, .24); border: 1px solid rgba(251, 191, 36, .65); }}
.step-bear .step-pill {{ color: #fecdd3; background: rgba(244, 63, 94, .24); border: 1px solid rgba(244, 63, 94, .65); }}
.step-text {{
  margin: 0; color: #f1f5f9; font-size: 24px; font-weight: 600; line-height: 1.48;
  overflow-wrap: anywhere;
}}
.timeline-arrow {{
  flex: 0 0 auto; align-self: center; font-size: 32px; font-weight: 900;
  color: #fbbf24; text-shadow: 0 0 14px rgba(251, 191, 36, .65);
  animation: fadeInUp calc(var(--pace) * 1.2) ease both;
}}

.visual {{
  position: relative; width: 100%; min-width: 0; height: 100%;
  display: flex; align-items: center; justify-content: center;
  animation: chartIn .9s cubic-bezier(.2,.8,.2,1) .45s both, breathe 5s ease-in-out 1.4s infinite;
}}
.chart img {{
  display: block; width: 100%; height: 100%; object-fit: contain;
  animation: revealRight calc(var(--pace) * 5) ease-out both;
}}
@keyframes revealRight {{
  from {{ clip-path: inset(0 100% 0 0); }}
  to {{ clip-path: inset(0 0 0 0); }}
}}
.metric-rail {{
  position: absolute; left: 290px; right: 290px; bottom: 225px; z-index: 6;
  overflow: visible;
}}
.disclaimer-line {{
  margin: 0 0 4px; text-align: center; color: #94a3b8;
  font-size: 13px; line-height: 1.2; font-weight: 600;
}}
.insight-dock {{
  display: grid; grid-template-columns: 1.15fr 1fr 1.15fr 1.2fr; gap: 12px;
}}
.insight-dock article {{
  border: 1px solid rgba(245, 158, 11, .45); border-radius: 12px; padding: 8px 12px;
  background: rgba(8, 12, 22, .78); animation: fadeInUp calc(var(--pace) * 2) ease both;
}}
.insight-dock article:nth-child(2) {{ animation-delay: calc(var(--pace)); }}
.insight-dock article:nth-child(3) {{ animation-delay: calc(var(--pace) * 2); }}
.insight-dock article:nth-child(4) {{ animation-delay: calc(var(--pace) * 3); }}
.insight-dock small {{ display: block; color: #94a3b8; font-size: 12px; margin-bottom: 2px; }}
.insight-dock strong {{ display: block; color: #f59e0b; font-size: 21px; line-height: 1.2; }}
.insight-dock strong.pulse {{ animation: levelPulse 1.6s ease-in-out infinite; }}
.insight-dock em {{ display: block; margin-top: 2px; color: #e2e8f0; font-style: normal; font-size: 13px; }}
.mvrv-bar {{ height: 12px; margin-top: 8px; border-radius: 99px; background: #1e293b; overflow: hidden; }}
.mvrv-bar i {{
  display: block; height: 100%; width: 0;
  background: linear-gradient(90deg, #34d399, #f59e0b, #f87171);
  animation: barGrow 1.5s ease forwards;
}}
@keyframes barGrow {{ to {{ width: var(--mvrv-pct); }} }}
@keyframes levelPulse {{
  50% {{ text-shadow: 0 0 14px rgba(245, 158, 11, .95); }}
}}
@keyframes chartIn {{
  from {{ opacity: 0; transform: scale(.88); }}
  to {{ opacity: 1; transform: scale(1); }}
}}
@keyframes breathe {{ 0%, 100% {{ filter: drop-shadow(0 0 8px rgba(56,189,248,.16)); }} 50% {{ filter: drop-shadow(0 0 18px rgba(56,189,248,.34)); }} }}
.gauge {{ flex-direction: column; gap: 22px; }}
.gauge-title {{ color: #cbd5e1; font-size: 30px; font-weight: 700; }}
.gauge-track {{ width: 90%; height: 28px; display: flex; overflow: hidden; border-radius: 16px; }}
.gauge-track span {{ flex: 1; }}
.fear {{ background: #ef4444; }} .caution {{ background: #f97316; }}
.neutral {{ background: #eab308; }} .greed {{ background: #84cc16; }}
.extreme-greed {{ background: #22c55e; }}
.gauge-marker {{ position: absolute; top: 43%; width: 5px; height: 42px; background: white; box-shadow: 0 0 12px white; }}
.gauge-value {{ color: #fbbf24; font-size: 64px; font-weight: 800; }}
.gauge-value small {{ color: #94a3b8; font-size: 24px; font-weight: 700; }}
@keyframes heroPop {{ from {{ opacity: 0; transform: scale(.88) translateY(18px); }} to {{ opacity: 1; transform: scale(1) translateY(0); }} }}

/* ── Progressive Kinetic Focus：卡片保留各版型自己的進場動畫，
   語音講到哪張就加上金色發光邊框高亮，絕不隱形。 ── */
.reveal-node {{
  opacity: 1 !important; visibility: visible !important;
  transform: translateY(0) scale(1);
  transition:
    transform 0.35s cubic-bezier(0.22, 1, 0.36, 1),
    box-shadow 0.35s ease, border-color 0.35s ease,
    background 0.35s ease, filter 0.35s ease, color 0.35s ease;
}}
.reveal-node.is-hidden {{ opacity: 0.05 !important; visibility: visible !important; transform: translateY(0) scale(1); }}
.reveal-node.is-active {{
  opacity: 1 !important; visibility: visible !important; transform: translateY(0) scale(1.02);
  border-color: rgba(251, 191, 36, .9) !important;
  box-shadow: 0 0 24px rgba(251, 191, 36, .35), 0 0 0 1px rgba(251, 191, 36, .55) inset;
  animation: activeNodePulse 2.8s ease-in-out infinite !important;
}}
@keyframes activeNodePulse {{
  0%, 100% {{ filter: drop-shadow(0 0 0 rgba(251,191,36,0)); }}
  50% {{ filter: drop-shadow(0 0 10px rgba(251,191,36,.34)); }}
}}
.timeline-card.is-active {{
  animation: timelineFocus 2.8s ease-in-out infinite !important;
}}
@keyframes timelineFocus {{
  0%, 100% {{ box-shadow: 0 0 18px rgba(251,191,36,.12); }}
  50% {{ box-shadow: 0 0 34px rgba(251,191,36,.36); }}
}}
.reveal-node.is-active,
.reveal-node.is-active .step-text,
.reveal-node.is-active li,
.reveal-node.is-active strong {{ color: #FBBF24 !important; }}
.reveal-node.is-dimmed {{
  opacity: 0.55 !important; visibility: visible !important; transform: translateY(0) scale(1);
  border-color: rgba(148, 163, 184, .32) !important;
  box-shadow: none !important; filter: saturate(0.85);
}}
/* ── Episode-level visual systems: distinct composition per show format ── */
.slide-stage.style-debate .ticker { background: linear-gradient(90deg, rgba(127,29,29,.96), rgba(15,23,42,.96) 48%, rgba(8,47,73,.96)); border-bottom: 3px solid #fb7185; }
.slide-stage.style-debate .frame { left: 70px; right: 70px; top: 118px; bottom: 330px; border: 3px solid rgba(251,113,133,.72); border-radius: 8px; background: linear-gradient(135deg, rgba(39,12,25,.94), rgba(8,24,42,.94)); }
.slide-stage.style-debate .versus-clash-layout { gap: 30px; }
.slide-stage.style-debate .versus-panel { border-radius: 8px; }

.slide-stage.style-archive::before { background: radial-gradient(ellipse at 15% 20%, rgba(180,133,75,.24), transparent 38%), radial-gradient(ellipse at 85% 82%, rgba(120,82,45,.18), transparent 40%), linear-gradient(rgba(180,133,75,.055) 1px, transparent 1px), linear-gradient(90deg, rgba(180,133,75,.055) 1px, transparent 1px); background-size: auto, auto, 58px 58px, 58px 58px; }
.slide-stage.style-archive .ticker { height: 68px; background: rgba(38,29,21,.96); border-bottom: 2px solid #c49a62; }
.slide-stage.style-archive .frame { left: 105px; right: 105px; top: 95px; bottom: 325px; border: 1px solid rgba(196,154,98,.8); border-radius: 3px; background: rgba(25,22,19,.94); box-shadow: 12px 12px 0 rgba(90,65,39,.22); }
.slide-stage.style-archive h1 { color: #f4dfb8; font-family: Georgia, "Noto Sans CJK TC", serif; }
.slide-stage.style-archive .insight-dock article { border-color: rgba(196,154,98,.55); border-radius: 3px; background: rgba(38,29,21,.9); }

.slide-stage.style-editorial .ticker { height: 70px; background: rgba(8,19,35,.97); border-bottom: 4px solid #38bdf8; }
.slide-stage.style-editorial .frame { left: 92px; right: 92px; top: 108px; bottom: 330px; border: 0; border-left: 10px solid #38bdf8; border-radius: 0 18px 18px 0; background: linear-gradient(105deg, rgba(8,19,35,.97), rgba(13,28,48,.88)); box-shadow: 18px 0 42px rgba(56,189,248,.10); }
.slide-stage.style-editorial h1 { color: #e0f2fe; text-shadow: none; }

.slide-stage.style-psychology::before { background: radial-gradient(ellipse at 10% 80%, rgba(244,63,94,.25), transparent 40%), radial-gradient(ellipse at 90% 15%, rgba(168,85,247,.22), transparent 40%), linear-gradient(rgba(244,63,94,.035) 1px, transparent 1px), linear-gradient(90deg, rgba(168,85,247,.035) 1px, transparent 1px); background-size: auto, auto, 42px 42px, 42px 42px; }
.slide-stage.style-psychology .ticker { background: rgba(35,13,35,.96); border-bottom: 2px solid #e879f9; }
.slide-stage.style-psychology .frame { left: 45px; right: 45px; top: 122px; bottom: 350px; border: 2px solid rgba(232,121,249,.65); border-radius: 30px 8px 30px 8px; background: rgba(27,13,35,.88); }
.slide-stage.style-psychology .impact-quote-box { border-radius: 28px 8px 28px 8px; }

.slide-stage.style-investigation .ticker { height: 62px; background: rgba(24,29,39,.98); border-bottom: 3px solid #f59e0b; }
.slide-stage.style-investigation .frame { left: 135px; right: 135px; top: 92px; bottom: 318px; border: 1px solid rgba(245,158,11,.8); border-radius: 2px; background: rgba(14,19,28,.96); }
.slide-stage.style-investigation h1::before { content: "CASE FILE  /  "; color: #f59e0b; font-size: .52em; letter-spacing: .08em; }
.slide-stage.style-investigation .metric-rail { left: 210px; right: 210px; }

.slide-stage.style-terminal::before { background: radial-gradient(ellipse at 50% 50%, rgba(16,185,129,.12), transparent 60%), linear-gradient(rgba(16,185,129,.07) 1px, transparent 1px), linear-gradient(90deg, rgba(16,185,129,.07) 1px, transparent 1px); background-size: auto, 32px 32px, 32px 32px; }
.slide-stage.style-terminal .ticker { background: rgba(2,24,22,.97); border-bottom: 2px solid #10b981; }
.slide-stage.style-terminal .frame { left: 34px; right: 34px; top: 108px; bottom: 345px; border: 1px solid rgba(16,185,129,.62); border-radius: 4px; background: rgba(2,18,17,.91); }
.slide-stage.style-terminal h1 { color: #a7f3d0; text-shadow: 0 0 18px rgba(16,185,129,.25); }
.slide-stage.style-terminal .insight-dock article { border-color: rgba(16,185,129,.5); border-radius: 4px; background: rgba(2,24,22,.94); }

.slide-stage.style-scenario .ticker { height: 92px; grid-template-columns: repeat(2, minmax(0, 1fr)); background: linear-gradient(90deg, rgba(23,37,84,.98), rgba(49,46,129,.94)); border-bottom: 3px solid #818cf8; }
.slide-stage.style-scenario .ticker-item { justify-content: flex-start; padding-left: 28px; }
.slide-stage.style-scenario .frame { left: 76px; right: 76px; top: 132px; bottom: 342px; border: 2px solid rgba(129,140,248,.72); border-radius: 26px; background: linear-gradient(145deg, rgba(15,23,58,.95), rgba(24,24,62,.91)); }
.slide-stage.style-scenario .timeline-steps-layout { gap: 26px; }

.slide-stage.style-data .ticker { height: 76px; background: rgba(7,25,39,.97); border-bottom: 3px solid #22d3ee; }
.slide-stage.style-data .frame { left: 55px; right: 55px; top: 112px; bottom: 336px; border: 2px solid rgba(34,211,238,.58); border-radius: 14px; background: rgba(5,21,34,.92); }
.slide-stage.style-data .chart-spotlight-layout { grid-template-columns: minmax(0, 68fr) minmax(0, 32fr); }
.slide-stage.style-data .insight-dock article { border-color: rgba(34,211,238,.52); background: rgba(5,21,34,.92); }

</style>
</head>
<body>
<div class="slide-stage kinetic scene-{(idx - 1) % 4} style-{html.escape(str(slide_data.get("visual_style") or "editorial"))}{' has-broll' if broll_path and os.path.exists(broll_path) else ''}" style="--pace:{pace:.2f}s;--mvrv-pct:{mvrv_pct}%;--broll-cycle:{min(18.0, max(8.0, float(dur_sec))):.3f}s;">
{broll_markup}
<header class="ticker" aria-label="即時行情狀態列">{ticker_markup}</header>
<main class="{frame_class}" style="--accent:{accent}" aria-label="{label_markup}">
  <h1>{title_markup}</h1>
  {content_markup}
</main>
<div class="metric-rail">
  <p class="disclaimer-line">公開資訊整理，不構成投資建議。</p>
  <aside class="insight-dock">{insight_markup}</aside>
</div>
</div>
  <script>
  window.fitSlideLayout = () => {{
    const frame = document.querySelector(".frame");
    const title = document.querySelector("h1");
    const content = document.querySelector(".content");
    const items = [...document.querySelectorAll("li, .impact-quote-text, .impact-sub, .step-text")];
    const overflows = () => {{
      const contentBox = content.getBoundingClientRect();
      return content.scrollHeight > content.clientHeight + 1 ||
        items.some(item => item.scrollWidth > item.clientWidth + 1 ||
          item.getBoundingClientRect().top < contentBox.top - 1 ||
          item.getBoundingClientRect().bottom > contentBox.bottom + 1) ||
        title.scrollWidth > title.clientWidth + 1 ||
        title.getBoundingClientRect().bottom > contentBox.top - 8 ||
        frame.scrollHeight > frame.clientHeight + 1;
    }};
    for (let attempt = 0; attempt < 14 && overflows(); attempt++) {{
      if (attempt === 0) frame.classList.add("fit-compact");
      if (attempt === 3) frame.classList.add("fit-tight");
      if (attempt === 6) frame.classList.add("fit-min");
      const titleSize = parseFloat(getComputedStyle(title).fontSize);
      if (titleSize > 34) title.style.fontSize = `${{Math.max(34, titleSize - 2)}}px`;
      const minBodySize = content.classList.contains("chart-spotlight-layout") ? 18 : 18;
      for (const item of items) {{
        const bodySize = parseFloat(getComputedStyle(item).fontSize);
        if (bodySize > minBodySize) {{
          item.style.fontSize = `${{Math.max(minBodySize, bodySize - 2)}}px`;
        }}
      }}
    }}
    if (!overflows()) return true;

    document.querySelectorAll(".versus-panel, .timeline-card").forEach(panel => {{
      panel.style.overflow = "visible";
    }});
    frame.style.overflow = "visible";
    const frameBox = frame.getBoundingClientRect();
    const contentBox = content.getBoundingClientRect();
    const itemRects = items.map(item => item.getBoundingClientRect());
    const minLeft = Math.min(
      0, ...itemRects.map(rect => (rect.left - frameBox.left) / {MAIN_OUTPUT_SCALE})
    );
    const minTop = Math.min(
      0, ...itemRects.map(rect => (rect.top - frameBox.top) / {MAIN_OUTPUT_SCALE})
    );
    const maxRight = Math.max(
      frame.clientWidth,
      (contentBox.right - frameBox.left) / {MAIN_OUTPUT_SCALE},
      (contentBox.left - frameBox.left) / {MAIN_OUTPUT_SCALE} + content.scrollWidth,
      ...itemRects.map((rect, index) =>
        (rect.right - frameBox.left) / {MAIN_OUTPUT_SCALE} +
          Math.max(0, items[index].scrollWidth - items[index].clientWidth)
      )
    );
    const maxBottom = Math.max(
      frame.clientHeight,
      (contentBox.bottom - frameBox.top) / {MAIN_OUTPUT_SCALE},
      (contentBox.top - frameBox.top) / {MAIN_OUTPUT_SCALE} + content.scrollHeight,
      ...itemRects.map(rect => (rect.bottom - frameBox.top) / {MAIN_OUTPUT_SCALE})
    );
    const scale = Math.min(
      1,
      frame.clientWidth / Math.max(1, maxRight - minLeft),
      frame.clientHeight / Math.max(1, maxBottom - minTop)
    );
    frame.style.transformOrigin = "top left";
    frame.style.transform = `translate(${{-minLeft * scale}}px, ${{-minTop * scale}}px) scale(${{scale}})`;
    return false;
  }};
  </script>
  <script>
  (function () {{
    const nodes = Array.prototype.slice.call(document.querySelectorAll(".reveal-node"));
    if (!nodes.length) return;
    const times = {reveal_ts_json};
    if (!times.length) {{
      nodes.forEach(node => node.classList.remove("is-hidden", "is-active", "is-dimmed"));
      return;
    }}
    const start = performance.now();
    let clockOffset = 0;
    function apply(activeIndex) {{
      for (let i = 0; i < nodes.length; i++) {{
        const node = nodes[i];
        node.classList.remove("is-hidden", "is-active", "is-dimmed");
        if (i < activeIndex) node.classList.add("is-dimmed");
        else if (i === activeIndex) node.classList.add("is-active");
        else node.classList.add("is-hidden");
      }}
    }}
    window.resetSlideRevealClock = () => {{
      clockOffset = (performance.now() - start) / 1000;
      apply(-1);
    }};
    function tick(now) {{
      const t = Math.max(0, (now - start) / 1000 - clockOffset);
      let activeIndex = -1;
      for (let i = 0; i < times.length; i++) {{
        if (t >= times[i]) activeIndex = i;
      }}
      apply(activeIndex);
      if (activeIndex < nodes.length - 1) requestAnimationFrame(tick);
    }}
    apply(times.length ? 0 : -1);
    requestAnimationFrame(tick);
  }})();
  </script>
</body>
</html>"""

    with tempfile.TemporaryDirectory(prefix=f"slide_{idx}_") as record_dir:
        context = browser.new_context(
            viewport={"width": MAIN_VIDEO_WIDTH, "height": MAIN_VIDEO_HEIGHT},
            device_scale_factor=2,
            record_video_dir=record_dir,
            record_video_size={"width": MAIN_VIDEO_WIDTH, "height": MAIN_VIDEO_HEIGHT},
        )
        page = context.new_page()
        recording_started_at = time.monotonic()
        video = page.video
        try:
            page.set_content(html_doc, wait_until="load")
            page.evaluate("() => document.fonts.ready.then(() => true)")
            # 不只確認檔案存在：必須確認 Chromium 真正解碼並播放 Pixabay 影片，
            # 否則禁止把「已下載」誤當成「已顯示」。
            if slide_data.get("broll_path"):
                page.wait_for_function(
                    "() => { const v = document.querySelector('.broll-bg'); "
                    "return !!v && v.readyState >= 2 && v.videoWidth > 0 && v.videoHeight > 0; }",
                    timeout=20000,
                )
                page.evaluate(
                    "() => { const v = document.querySelector('.broll-bg'); "
                    "if (v) { v.muted = true; v.play(); } }"
                )
                page.wait_for_function(
                    "() => { const v = document.querySelector('.broll-bg'); "
                    "return !!v && v.currentTime > 0.15; }",
                    timeout=10000,
                )
                print(
                    f"[Pixabay B-Roll] 第 {idx} 頁已確認影片解碼播放："
                    f"{os.path.basename(slide_data['broll_path'])}"
                )
            if not page.evaluate("() => window.fitSlideLayout()"):
                print(f"Slide {idx} required final auto-fit scaling to stay inside its safe area.")
            page.evaluate("() => window.resetSlideRevealClock && window.resetSlideRevealClock()")
            page.wait_for_timeout(max(1, round(capture_preroll * 1000)))
            capture_offset = time.monotonic() - recording_started_at
            poster_path = os.path.join(record_dir, "poster.png")
            page.screenshot(path=poster_path)
            page.wait_for_timeout(max(1, round(tail_record_seconds * 1000)))
        finally:
            context.close()
        recording_path = video.path()
        tail_duration = max(0.1, float(dur_sec) - capture_preroll)
        poster_filter = (
            f"[0:v]fps={VIDEO_FPS},scale={MAIN_VIDEO_WIDTH}:{MAIN_VIDEO_HEIGHT}:flags=lanczos,"
            f"trim=duration={capture_preroll:.6f},setpts=PTS-STARTPTS,format=yuv420p[poster];"
            f"[1:v]trim=start={capture_offset:.6f},setpts=PTS-STARTPTS,fps={VIDEO_FPS},"
            f"scale={MAIN_VIDEO_WIDTH}:{MAIN_VIDEO_HEIGHT}:flags=lanczos,"
            f"tpad=stop_mode=clone:stop_duration={float(dur_sec):.6f},"
            f"trim=duration={tail_duration:.6f},format=yuv420p[tail];"
            "[poster][tail]concat=n=2:v=1:a=0[v]"
        )
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-loop", "1", "-framerate", str(VIDEO_FPS),
                "-t", f"{capture_preroll:.6f}", "-i", poster_path,
                "-i", recording_path,
                "-filter_complex", poster_filter, "-map", "[v]",
                "-t", f"{dur_sec:.6f}", "-an", "-r", str(VIDEO_FPS),
                "-c:v", "libx264", "-preset", "medium", "-crf", VIDEO_CRF,
                "-pix_fmt", "yuv420p", out_mp4,
            ],
            check=True,
        )


# ── 主題圖片、片頭片尾與頭像 ──────────────────────────────────────────

def generate_topic_icon(topic_summary, coin_code):
    icon_path = "topic_icon.png"
    coin_ids = {
        "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
        "XRP": "ripple", "ADA": "cardano", "ZEC": "zcash",
    }
    coin_code = str(coin_code or "BTC").upper()
    image_prompt = (
        f"Create a polished, cinematic 3D finance illustration inspired by {coin_code} "
        f"and this daily market topic: {topic_summary}. No letters, no words, no logos, "
        "square composition, luminous metallic gold and deep midnight blue, premium "
        "financial editorial art."
    )

    try:
        if script_writer.reuse_script_enabled():
            raise RuntimeError("REUSE_SCRIPT skips Gemini image generation.")
        image_prompt = script_writer.call_gemini(
            "Rewrite this description as one concise English image-generation prompt. "
            "Require a square 3D financial editorial illustration with no text, letters, "
            "or logos. Return only the prompt:\n" + image_prompt
        ).strip()
        request = urllib.request.Request(
            "https://generativelanguage.googleapis.com/v1beta/interactions",
            data=json.dumps({
                "model": "gemini-nano-banana-2.1",
                "input": image_prompt,
            }).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": os.environ["GEMINI_API_KEY"].strip(),
                "Api-Revision": "2026-05-20",
            },
            method="POST",
        )
        response = json.loads(
            urllib.request.urlopen(request, timeout=180).read().decode("utf-8")
        )
        image_bytes = base64.b64decode(response["output_image"]["data"])
        with Image.open(io.BytesIO(image_bytes)) as generated:
            generated.convert("RGB").resize((512, 512), Image.LANCZOS).save(icon_path)
        print("Generated daily topic icon with Gemini image generation.")
        return icon_path
    except Exception as e:
        print(f"Topic image generation failed; trying the official coin icon: {e}")

    try:
        coin_id = coin_ids.get(coin_code, "bitcoin")
        coin_data = json.loads(urllib.request.urlopen(
            f"https://api.coingecko.com/api/v3/coins/{coin_id}"
            "?localization=false&tickers=false&market_data=false&community_data=false"
            "&developer_data=false&sparkline=false",
            timeout=20,
        ).read().decode("utf-8"))
        image_url = coin_data["image"]["large"]
        image_request = urllib.request.Request(
            image_url, headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(image_request, timeout=20) as response:
            with Image.open(io.BytesIO(response.read())) as coin_image:
                coin_image.convert("RGBA").resize(
                    (512, 512), Image.LANCZOS
                ).save(icon_path)
        print(f"Using the official CoinGecko {coin_code} icon.")
        return icon_path
    except Exception as e:
        print(f"CoinGecko icon unavailable; drawing a local coin emblem: {e}")

    fp = get_system_font_path()
    emblem = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    draw = ImageDraw.Draw(emblem)
    for radius, alpha in ((224, 18), (204, 32), (182, 52)):
        draw.ellipse(
            (256 - radius, 256 - radius, 256 + radius, 256 + radius),
            fill=(251, 191, 36, alpha),
        )
    draw.ellipse((70, 70, 442, 442), fill="#102238", outline="#fbbf24", width=16)
    draw.ellipse((92, 92, 420, 420), outline="#fde68a", width=5)
    emblem_font = ImageFont.truetype(fp, 158)
    emblem_draw = ImageDraw.Draw(emblem)
    symbol = coin_code[:4]
    bounds = emblem_draw.textbbox((0, 0), symbol, font=emblem_font)
    emblem_draw.text(
        ((512 - (bounds[2] - bounds[0])) // 2, (512 - (bounds[3] - bounds[1])) // 2 - bounds[1]),
        symbol,
        fill="#fbbf24",
        font=emblem_font,
    )
    emblem.save(icon_path)
    print("Using a locally rendered metallic topic emblem.")
    return icon_path


def create_outro_frame():
    fp = get_system_font_path()
    image = Image.new("RGB", (MAIN_VIDEO_WIDTH, MAIN_VIDEO_HEIGHT), "#090d16")
    draw = ImageDraw.Draw(image)
    draw.rectangle((30, 30, 1890, 1050), outline="#fbbf24", width=6)
    if os.path.exists("avatar.png"):
        with Image.open("avatar.png") as avatar:
            image.paste(
                avatar.convert("RGB").resize((390, 390), Image.LANCZOS),
                (765, 45),
            )

    draw = ImageDraw.Draw(image)
    title = "感謝收聽【幣看趨勢】｜別忘了按讚、訂閱、開啟小鈴鐺！"
    title_font = ImageFont.truetype(fp, 60)
    title_lines = wrap_by_width(draw, title, title_font, 1740)
    for line_index, line in enumerate(title_lines):
        bounds = draw.textbbox((0, 0), line, font=title_font)
        draw.text(
            (
                (MAIN_VIDEO_WIDTH - (bounds[2] - bounds[0])) // 2,
                472 + line_index * 75,
            ),
            line,
            fill="#f8fafc",
            font=title_font,
        )
    subtitle = "留言告訴以太喵與比特汪，下一集你想看哪一顆幣？"
    subtitle_font = ImageFont.truetype(fp, 42)
    subtitle_lines = wrap_by_width(draw, subtitle, subtitle_font, 1740)
    for line_index, line in enumerate(subtitle_lines):
        bounds = draw.textbbox((0, 0), line, font=subtitle_font)
        draw.text(
            (
                (MAIN_VIDEO_WIDTH - (bounds[2] - bounds[0])) // 2,
                630 + line_index * 57,
            ),
            line,
            fill="#fbbf24",
            font=subtitle_font,
        )

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rectangle(
        (90, 765, 900, 1020),
        fill=(5, 8, 18, 185),
        outline=(251, 191, 36, 210),
        width=5,
    )
    overlay_draw.rectangle(
        (1020, 765, 1830, 1020),
        fill=(5, 8, 18, 185),
        outline=(56, 189, 248, 210),
        width=5,
    )
    image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    image.save("outro_frame.png")


def create_speaker_avatars(talk_videos):
    def square_crop(image, top_bias=0.5):
        width, height = image.size
        crop_size = min(width, height)
        left = (width - crop_size) // 2
        top = int((height - crop_size) * top_bias)
        return image.crop((left, top, left + crop_size, top + crop_size))

    def load_portrait(speaker):
        single_image_paths = (
            (f"{speaker}_single.png", f"{speaker}_single.png.png")
        )
        for image_path in single_image_paths:
            if os.path.exists(image_path):
                with Image.open(image_path) as source:
                    return square_crop(source.convert("RGB")).copy()

        video_path = talk_videos.get(speaker)
        if video_path:
            try:
                frame_bytes = subprocess.check_output(
                    [
                        "ffmpeg", "-v", "error", "-i", video_path,
                        "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-",
                    ],
                    stderr=subprocess.PIPE,
                )
                with Image.open(io.BytesIO(frame_bytes)) as source:
                    source.load()
                    return square_crop(source.convert("RGB")).copy()
            except (subprocess.CalledProcessError, OSError) as error:
                details = getattr(error, "stderr", None)
                if isinstance(details, bytes):
                    details = details.decode("utf-8", errors="replace")
                print(
                    f"Could not capture the first frame from {video_path}; "
                    f"trying avatar.png instead. {details or error}"
                )

        if os.path.exists("avatar.png"):
            with Image.open("avatar.png") as source:
                avatar = source.convert("RGB")
                midpoint = avatar.width // 2
                bounds = (
                    (0, 0, midpoint, avatar.height)
                    if speaker == "cat"
                    else (midpoint, 0, avatar.width, avatar.height)
                )
                return square_crop(avatar.crop(bounds), top_bias=0.25).copy()

        raise FileNotFoundError(
            f"No still image, VTuber video frame, or avatar.png is available for {speaker}."
        )

    portraits = {
        speaker: load_portrait(speaker).resize((225, 225), Image.LANCZOS)
        for speaker in ("cat", "dog")
    }
    mask_large = Image.new("L", (1008, 1008), 0)
    ImageDraw.Draw(mask_large).ellipse((3, 3, 1005, 1005), fill=255)
    face_mask = mask_large.resize((225, 225), Image.LANCZOS)
    circle_mask = Image.new("L", (252, 252), 0)
    ImageDraw.Draw(circle_mask).ellipse((0, 0, 251, 251), fill=255)
    circle_mask_path = "speaker_circle_mask.png"
    circle_mask.save(circle_mask_path)

    assets = {"circle_mask": circle_mask_path}
    for speaker, color in (("cat", (129, 140, 248)), ("dog", (251, 191, 36))):
        portrait = portraits[speaker]
        listening_face = ImageEnhance.Brightness(portrait).enhance(0.72).convert("RGBA")
        listening = Image.new("RGBA", (225, 225), (0, 0, 0, 0))
        listening.paste(listening_face, (0, 0), face_mask)
        listening_path = f"{speaker}_avatar.png"
        listening.save(listening_path)
        assets[f"{speaker}_idle"] = listening_path

        ring_glow = Image.new("RGBA", (252, 252), (0, 0, 0, 0))
        ImageDraw.Draw(ring_glow).ellipse(
            (6, 6, 246, 246), outline=(*color, 220), width=15
        )
        ring = ring_glow.filter(ImageFilter.GaussianBlur(14))
        ring_draw = ImageDraw.Draw(ring)
        ring_draw.ellipse((6, 6, 246, 246), outline=(*color, 255), width=9)
        ring_draw.ellipse((15, 15, 237, 237), outline=(255, 255, 255, 180), width=2)
        ring_path = f"{speaker}_avatar_ring.png"
        ring.save(ring_path)
        assets[f"{speaker}_ring"] = ring_path

        speaking_face = ImageEnhance.Brightness(portrait).enhance(1.18).convert("RGBA")
        speaking = Image.new("RGBA", (252, 252), (0, 0, 0, 0))
        speaking.paste(speaking_face, (14, 14), face_mask)
        speaking.alpha_composite(ring)
        speaking_path = f"{speaker}_avatar_speaking.png"
        speaking.save(speaking_path)
        assets[f"{speaker}_speaking"] = speaking_path

    return assets


def create_outro_video(outro_duration):
    subprocess.run(
        [
            "ffmpeg", "-y", "-loop", "1", "-framerate", str(VIDEO_FPS),
            "-i", "outro_frame.png", "-t", f"{outro_duration:g}",
            "-vf", f"scale={MAIN_VIDEO_WIDTH}:{MAIN_VIDEO_HEIGHT}:flags=lanczos,fps={VIDEO_FPS},fade=t=in:st=0:d=1,fade=t=out:st=10:d=2,format=yuv420p",
            "-an", "-r", str(VIDEO_FPS), "-c:v", "libx264",
            "-tune", "stillimage", "-preset", "medium", "-crf", VIDEO_CRF,
            "-pix_fmt", "yuv420p", "outro.mp4",
        ],
        check=True,
    )


def split_thumb_title(text, font, draw, max_width, max_lines=3):
    text = re.sub(r'\s+', '', text).strip()
    if not text:
        return []

    preferred_breaks = ('！', '？', '：', '｜', '，', '、')
    lines = []
    current = ''
    for char in text:
        trial = current + char
        if draw.textbbox((0, 0), trial, font=font)[2] <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = char
        if len(current) >= 11 and char in preferred_breaks:
            lines.append(current)
            current = ''
        elif len(current) >= 12:
            lines.append(current)
            current = ''

    if current:
        lines.append(current)

    return lines


def _write_pcm_wav(path, samples, rate):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        frames = bytearray()
        for sample in samples:
            clipped = max(-1.0, min(1.0, sample))
            frames.extend(struct.pack("<h", int(clipped * 32767)))
        handle.writeframes(frames)


def _ambient_pad_samples():
    rate = 22050
    seconds = 16
    count = rate * seconds
    tones = ((98.0, 0.22), (123.0, 0.14), (147.0, 0.09), (196.0, 0.04))
    samples = []
    for index in range(count):
        moment = index / rate
        tremolo = 0.84 + 0.16 * math.sin(2 * math.pi * 0.125 * moment)
        value = 0.0
        for frequency, gain in tones:
            value += gain * math.sin(2 * math.pi * frequency * moment)
        samples.append(value * tremolo * 0.5)
    return samples, rate


def _chime_samples():
    rate = 44100
    seconds = 0.35
    count = int(rate * seconds)
    samples = []
    for index in range(count):
        moment = index / rate
        first = math.sin(2 * math.pi * 740.0 * moment) * math.exp(-11 * moment)
        second_t = max(0.0, moment - 0.12)
        second = math.sin(2 * math.pi * 988.0 * moment) * math.exp(-10 * second_t) * (1 if moment >= 0.12 else 0)
        samples.append((first * 0.45 + second * 0.28) * 0.7)
    return samples, rate


def ensure_broadcast_audio():
    """缺少內建音檔時用標準庫生成。自訂 mp3 優先。"""
    folder = os.path.join("assets", "audio")
    os.makedirs(folder, exist_ok=True)
    bgm_path = os.path.join(folder, "bgm_ambient.wav")
    sfx_path = os.path.join(folder, "sfx_transition.wav")
    custom_bgm = os.path.join(folder, "custom_bgm.mp3")
    custom_sfx = os.path.join(folder, "custom_sfx.mp3")
    if not os.path.exists(bgm_path):
        samples, rate = _ambient_pad_samples()
        _write_pcm_wav(bgm_path, samples, rate)
        print(f"已生成內建環境墊音 {bgm_path}")
    if not os.path.exists(sfx_path):
        samples, rate = _chime_samples()
        _write_pcm_wav(sfx_path, samples, rate)
        print(f"已生成內建轉場提示音 {sfx_path}")
    return (
        custom_bgm if os.path.exists(custom_bgm) else bgm_path,
        custom_sfx if os.path.exists(custom_sfx) else sfx_path,
    )


def mix_voice_with_bed(speech_path, output_path, transition_times):
    """人聲維持原音量，墊音約 -28dB，轉場提示音約 -18dB。"""
    bgm_path, sfx_path = ensure_broadcast_audio()
    stamps = []
    for raw in transition_times or []:
        try:
            stamp = float(raw)
        except (TypeError, ValueError):
            continue
        if stamp < 0.05 or any(abs(stamp - old) < 0.25 for old in stamps):
            continue
        stamps.append(stamp)
    stamps = stamps[:24]
    command = ["ffmpeg", "-y", "-i", speech_path, "-stream_loop", "-1", "-i", bgm_path]
    for _stamp in stamps:
        command.extend(["-i", sfx_path])
    filters = [
        "[0:a]aformat=sample_rates=44100:channel_layouts=stereo[speech]",
        "[1:a]volume=0.0398,aformat=sample_rates=44100:channel_layouts=stereo[bgm]",
    ]
    labels = ["[speech]", "[bgm]"]
    for index, stamp in enumerate(stamps):
        delay = int(round(stamp * 1000))
        filters.append(
            f"[{index + 2}:a]volume=0.1259,adelay={delay}|{delay},"
            "aformat=sample_rates=44100:channel_layouts=stereo"
            f"[sfx{index}]"
        )
        labels.append(f"[sfx{index}]")
    filters.append(
        "".join(labels)
        + f"amix=inputs={len(labels)}:duration=first:normalize=0:dropout_transition=0[mix]"
    )
    command.extend([
        "-filter_complex", ";".join(filters),
        "-map", "[mix]",
        "-c:a", "libmp3lame", "-b:a", "192k",
        output_path,
    ])
    subprocess.run(command, check=True)
    print(f"已混入墊音與 {len(stamps)} 個轉場提示音：{output_path}")


def _cjk_chars(text):
    return "".join(ch for ch in str(text or "") if "\u4e00" <= ch <= "\u9fff")


def _fit_cjk_hook(text, fallback):
    compact = _cjk_chars(text)
    if 6 <= len(compact) <= 9:
        return compact
    if len(compact) > 9:
        return compact[:9]
    return fallback


def _daily_thumb_hook(mdata, sdata):
    fng = mdata.get("fng_num")
    pct = mdata.get("btc_pct")
    if fng is not None and int(fng) <= 40:
        return f"恐慌指數{int(fng)}守得住"
    if pct is not None and float(pct) <= -3:
        return "比特幣急跌了"
    if pct is not None and float(pct) >= 3:
        return "比特幣急漲了"
    raw = re.sub(r"^【幣看趨勢】|\([^)]+\)$", "", str(sdata.get("video_title") or ""))
    for part in re.split(r"[，。！？、\s｜|]+", raw):
        hook = _fit_cjk_hook(part, "")
        if hook:
            return hook
    return "比特幣守得住"


def _draw_impact_thumb(image, hook, badges, avatar_box):
    draw = ImageDraw.Draw(image)
    width, height = image.size
    draw.rectangle((0, 0, width, height), fill="#070b14")
    draw.rectangle((0, 0, int(width * 0.035), height), fill="#f59e0b")
    font_path = get_system_font_path()
    max_text_width = int(width * 0.62)
    hook_font = ImageFont.truetype(font_path, 72)
    for size in range(int(height * 0.22), 48, -4):
        candidate = ImageFont.truetype(font_path, size)
        if draw.textbbox((0, 0), hook, font=candidate)[2] <= max_text_width:
            hook_font = candidate
            break
    text_x = int(width * 0.07)
    text_y = int(height * 0.22)
    draw.text((text_x + 8, text_y + 8), hook, font=hook_font, fill="#000000")
    draw.text(
        (text_x, text_y), hook, font=hook_font, fill="#fff7ed",
        stroke_width=max(6, hook_font.size // 12), stroke_fill="#000000",
    )
    badge_font = ImageFont.truetype(font_path, max(28, int(height * 0.055)))
    badge_x = int(width * 0.68)
    badge_y = int(height * 0.62)
    for label, fill in badges:
        draw.rounded_rectangle(
            (badge_x, badge_y, width - int(width * 0.04), badge_y + int(height * 0.12)),
            radius=18, fill=fill,
        )
        draw.text(
            (badge_x + 24, badge_y + int(height * 0.025)),
            label, font=badge_font, fill="#ffffff",
        )
        badge_y += int(height * 0.15)
    if avatar_box and os.path.exists("avatar.png"):
        left, top, box_w, box_h = avatar_box
        with Image.open("avatar.png") as avatar:
            avatar = avatar.convert("RGBA").resize((box_w, box_h), Image.LANCZOS)
            image.paste(avatar, (left, top), avatar)
        draw.rounded_rectangle(
            (left - 8, top - 8, left + box_w + 8, top + box_h + 8),
            radius=28, outline="#f59e0b", width=8,
        )


# ── 主片 1080p 影音合成 ──────────────────────────────────────────

def render_main_video(sdata, mdata, dca_data, chart_paths, config):
    is_test = config["is_test"]
    show_dca = config["show_dca"]
    today_str = config["today_str"]
    outro_duration = config["OUTRO_DURATION"]
    outro_pause = config["OUTRO_PAUSE"]
    slide_count = len(sdata.get("visual_slides") or []) or config["SLIDE_COUNT"]
    # 同集 B-Roll 輪換從乾淨狀態開始，避免跨集重複。
    broll_engine.reset_episode_rotation()

    full_script = sdata["full_script"]
    dialogues, slide_anchor_positions = extract_dialogues_and_slide_anchors(full_script, slide_count)

    # 1. 配音合成
    with ThreadPoolExecutor(max_workers=5) as ex:
        results = list(ex.map(lambda item: synth_audio_segment(item, "seg"), enumerate(dialogues)))
    results.sort(key=lambda x: x[0])

    # 2. 片頭片尾音樂
    create_outro_audio(outro_duration, outro_pause)
    has_intro_video = os.path.exists("info.mp4") and os.path.exists("info.mp3")
    intro_video_duration = 15.0 if has_intro_video else 0.0
    intro_dur = 0.0
    if has_intro_video:
        intro_source_duration = float(
            subprocess.check_output(
                [
                    "ffprobe", "-v", "error", "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1", "info.mp3",
                ]
            ).decode().strip()
        )
        fade_duration = min(1.5, intro_source_duration) if intro_source_duration > 0 else 1.5
        fade_start = max(0.0, min(13.5, intro_source_duration - fade_duration))
        intro_audio_filter = (
            f"afade=t=out:st={fade_start:.3f}:d={fade_duration:.3f},"
            "apad=whole_dur=15"
        )
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", "info.mp3", "-t", "15",
                "-af", intro_audio_filter,
                "-ar", "24000", "-ac", "1", "-c:a", "libmp3lame", "-b:a", "64k",
                "intro_cut.mp3",
            ],
            check=True,
        )
        intro_dur = float(
            subprocess.check_output(
                [
                    "ffprobe", "-v", "error", "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1", "intro_cut.mp3",
                ]
            ).decode().strip()
        )
        # 投影片時間軸與字幕共用同一個「真實片頭秒數」，避免片頭 15.0s 銜接處
        # 因音檔實際長度與硬寫 15.0 的微小落差而讓簡報與播報逐漸脫鉤。
        intro_video_duration = intro_dur

    with open("audio_list.txt", "w", encoding="utf-8") as f:
        if intro_dur > 0:
            f.write("file 'intro_cut.mp3'\n")
        for _, fn, _, _ in results:
            f.write(f"file '{fn}'\n")
        f.write("file 'outro_pause.mp3'\n")
        f.write("file 'outro_music.mp3'\n")
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "audio_list.txt", "-c", "copy", "podcast.mp3"], check=True)

    # 3. 建立主片字幕與投影片時間點
    cc = OpenCC("s2twp")
    ass_head = (
        f"[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {MAIN_VIDEO_WIDTH}\nPlayResY: {MAIN_VIDEO_HEIGHT}\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Noto Sans CJK TC,66,&H00FFFFFF,&H000000FF,&H00000000,"
        "&H99000000,0,0,0,0,100,100,0,0,3,3,0,2,315,315,60,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text\n"
    )

    cur_t, evs = intro_dur, []
    speech_elapsed = 0.0
    slide_start_times = {}
    speaker_intervals = {"F": [], "M": []}
    for idx, _, txt, dur in results:
        role = dialogues[idx][0]
        speaker = "F" if role in ("女", "F") else "M"
        speaker_start = cur_t
        speaker_end = speaker_start + dur
        intervals = speaker_intervals[speaker]
        if intervals and abs(intervals[-1][1] - speaker_start) < 1e-6:
            intervals[-1] = (intervals[-1][0], speaker_end)
        else:
            intervals.append((speaker_start, speaker_end))

        for slide_number, anchor_position in slide_anchor_positions.items():
            anchor_dialogue_index = int(anchor_position)
            if idx == anchor_dialogue_index:
                fractional_offset = anchor_position - anchor_dialogue_index
                slide_start_times[slide_number] = (
                    intro_video_duration + speech_elapsed + dur * fractional_offset
                )
        # OpenCC 可能把「比特汪」轉成「位元汪」，轉換後必須再跑角色名正規化。
        tw_txt = presentation.display_text(cc.convert(txt))

        split_txt = [part.strip() for part in re.split(r'[，。！？、；：…!?;\n]+', tw_txt) if part.strip()]
        sub_segments = []
        for segment in split_txt:
            if len(segment) > 24:
                sub_segments.extend(presentation.split_keeping_numbers(segment, 20))
            else:
                sub_segments.append(segment)

        sub_segments = [re.sub(r'\s+', ' ', segment).strip() for segment in sub_segments if segment.strip()]
        # 依實際送入 TTS 的發音文字估算字幕片段權重，避免 0.587 等數字
        # 在字幕中只佔 5 個字元、口播卻需要較長時間而造成逐句偏移。
        segment_weights = [
            max(1, len(presentation.pronounce_for_tts(segment)))
            for segment in sub_segments
        ]
        total_chars = sum(segment_weights)
        if total_chars == 0:
            cur_t += dur
            speech_elapsed += dur
            continue

        segment_cur = cur_t
        for index, segment in enumerate(sub_segments):
            sub_txt = re.sub(r'\s+', ' ', re.sub(punct_pat, ' ', segment)).strip()
            if not sub_txt:
                continue

            if index == len(sub_segments) - 1:
                sub_end = cur_t + dur
            else:
                sub_end = segment_cur + dur * (segment_weights[index] / total_chars)

            hl = re.sub(kw_pat, r'{\\b1\\c&H0024BFFF&}\1{\\b0\\c&H00FFFFFF&}', sub_txt)
            if idx == results[-1][0] and index == len(sub_segments) - 1:
                # 最後一句在進入片尾前淡出，避免結尾字幕殘留在片尾畫面。
                hl = r"{\\fad(0,600)}" + hl
            evs.append(f"Dialogue: 0,{fmt_ass_t(segment_cur)},{fmt_ass_t(sub_end)},Default,,315,315,0,,{hl}")
            segment_cur = sub_end

        cur_t += dur
        speech_elapsed += dur
    speech_end_t = cur_t

    if set(slide_start_times) != set(range(1, slide_count + 1)):
        raise RuntimeError(
            "Not all slide anchors were mapped to synthesized dialogue: "
            f"{sorted(slide_start_times)}."
        )
    with open("subs.ass", "w", encoding="utf-8") as f:
        f.write(ass_head + "\n".join(evs))

    fp = get_system_font_path()
    fh = ImageFont.truetype(fp, 60)
    ft = ImageFont.truetype(fp, 51)
    if has_intro_video:
        intro_frame = Image.new(
            "RGB", (MAIN_VIDEO_WIDTH, MAIN_VIDEO_HEIGHT), "#090d16"
        )
        intro_draw = ImageDraw.Draw(intro_frame)
        intro_draw.rectangle((30, 30, 1890, 1050), outline="#fbbf24", width=6)
        intro_draw.rectangle((606, 74, 1314, 782), outline="#fbbf24", width=6)
        intro_draw.line((60, 60, 270, 60), fill="#38bdf8", width=3)
        intro_draw.line((1650, 1020, 1860, 1020), fill="#38bdf8", width=3)
        intro_draw.text(
            (960, 837),
            "幣看趨勢｜比特幣與加密貨幣情報",
            fill="#f8fafc",
            font=fh,
            anchor="mm",
        )
        intro_draw.text(
            (960, 918),
            f"以太喵 ＆ 比特汪・每日市場深度解析（{today_str}）",
            fill="#fbbf24",
            font=ft,
            anchor="mm",
        )
        intro_frame.save("intro_frame.png")

    # 4. 組裝投影片內容清單
    today_fng = mdata["today_fng"]
    yest_fng = mdata["yest_fng"]
    fng_num = mdata["fng_num"]
    sentiment_slide = ""
    if today_fng:
        if yest_fng:
            fng_yesterday = int(yest_fng["value"])
            fng_change = fng_num - fng_yesterday
            fng_direction = "升高" if fng_change > 0 else "降低" if fng_change < 0 else "持平"
            sentiment_slide = (
                f"今日：{fng_num}（{today_fng['value_classification']}）\n"
                f"昨日：{fng_yesterday}，{fng_direction} {abs(fng_change)} 點"
            )
        else:
            sentiment_slide = (
                f"今日：{fng_num}（{today_fng['value_classification']}）"
            )
    else:
        sentiment_slide = ""

    planned_slides = sdata.get("visual_slides") or []
    if planned_slides:
        slides_cfg = [
            (
                item.get("title") or f"分鏡 {index}",
                item.get("accent") or "#fbbf24",
                item.get("body") or "",
                item.get("chart"),
            )
            for index, item in enumerate(planned_slides, 1)
        ]
    if not planned_slides:
        slide_news_details = sdata["slide_news_details"]
        slide_news = sdata["slide_news"]
        news_slides = [
            (
                presentation.display_text(script_writer.clean_slide_title(slide_news_details[index]["slide_title"])),
                "#fbbf24",
                slide_news[index],
                "compare",
            )
            for index in range(3)
        ]
        condition_slides = [
            (f"未來 24 小時（今日至明晨）觀察條件 {index + 1}", "#f472b6", condition, "focus")
            for index, condition in enumerate(sdata["slide_24h_conditions"])
        ]
        slides_cfg = [
            ("今日核心衝突", "#38bdf8", presentation.opening_conflict_body(mdata), "summary"),
        ]
        if show_dca:
            slides_cfg.append(
                ("BTC 長期定投（DCA）模擬帳本成績單", "#fbbf24", sdata["dca_slide_body"], "dca")
            )
        slides_cfg += [
            ("市場情緒｜恐慌與貪婪指數", "#38bdf8", sentiment_slide, "gauge"),
            (
                "BTC 動態｜均線、RSI 與歷史超賣",
                "#38bdf8",
                f"{mdata['s1']}\n{mdata['btc_oversold_report']}",
                "btc_rsi",
            ),
            ("ETH 動態｜日線均線與 RSI", "#a78bfa", mdata["eth_slide"], "dual"),
        ] + news_slides + [
            ("六大主流幣｜報價與指標總表", "#34d399", cc.convert(mdata["coins_summary"]), None),
            ("六大主流幣｜24H 漲跌幅", "#34d399", "即時報價、漲跌幅與 RSI 對照", "bars"),
            (
                "BTC 價格結構與關鍵攻防",
                "#a78bfa",
                f"{mdata['btc_technical_report']}\n{mdata['eth_technical_report']}",
                "btc_structure",
            ),
        ] + condition_slides + [
            ("24小時策略｜先驗證、再行動", "#fbbf24", "核對價格與均線、量能結構及市場情緒。\n依可驗證訊號控管部位，避免追高殺低。", "focus"),
        ]
        dynamic_titles = script_writer.align_slide_titles(
            sdata.get("slide_titles"),
            mdata,
            show_dca,
            sdata.get("slide_news_details"),
            sdata.get("slide_24h_conditions"),
        )
        slides_cfg = [
            (dynamic_titles[index] if index < len(dynamic_titles) else title, color, body, ctype)
            for index, (title, color, body, ctype) in enumerate(slides_cfg)
        ]

    # 5. Playwright 渲染動畫投影片
    slide_durations = calculate_slide_durations(
        slide_start_times, speech_elapsed, intro_video_duration, slide_count, outro_pause
    )
    # 依真實 TTS 子音檔秒數，算出每頁各重點節點的揭示時間點（音畫對齊 <0.1s）。
    slide_reveal_map = compute_slide_reveal_timestamps(
        results, slide_anchor_positions, slide_count, sdata.get("visual_slides")
    )
    # 簡報節點與 TTS 時間必須一對一；不允許缺少對應時退回整頁平均揭示。
    for slide_number, slide in enumerate(sdata.get("visual_slides") or [], 1):
        timeline = slide.get("_body_timeline") or []
        reveal_times = slide_reveal_map.get(slide_number) or []
        if not timeline or len(timeline) != len(reveal_times):
            raise RuntimeError(
                f"第 {slide_number} 張簡報的文字節點與口播時間軸不匹配："
                f"nodes={len(timeline)}, timestamps={len(reveal_times)}；停止輸出錯配影片。"
            )
    spoken_by_index = {idx: line for idx, _fn, line, _dur in results}
    for slide_number, slide in enumerate(sdata.get("visual_slides") or [], 1):
        for node, reveal_at in zip(slide.get("_body_timeline") or [], slide_reveal_map[slide_number]):
            source_line = spoken_by_index.get(node.get("dialogue_index"), "")
            print(
                f"[Slide Sync] S{slide_number} +{reveal_at:.2f}s｜"
                f"畫面：{node.get('text', '')}｜"
                f"對應口播：{source_line[:100]}"
            )
    print(f"[Slide Sync] {slide_count} 張簡報已完成文字節點／TTS 時間一對一驗證。")
    dca_snapshot = mdata.get("dca_snapshot") or {}
    dca_return_pct = dca_snapshot.get("roi_pct")
    if dca_return_pct is None and show_dca:
        dca_return_pct = (dca_data.get("usd_view") or {}).get("roi_pct")
    fng_tone = (
        "negative" if fng_num <= 40 else "positive" if fng_num >= 60 else "neutral"
    ) if fng_num is not None else "neutral"
    ticker_items = [
        {
            "label": "BTC",
            "value": presentation.format_usd(mdata["cur_p"]),
            "detail": f"{mdata['btc_pct']:+.2f}% 24H",
            "tone": "positive" if mdata["btc_pct"] >= 0 else "negative",
        },
        {
            "label": "ETH",
            "value": presentation.format_usd(mdata["spot_prices"]["ETH"]),
            "detail": f"{mdata['eth_pct']:+.2f}% 24H",
            "tone": "positive" if mdata["eth_pct"] >= 0 else "negative",
        },
    ]
    if fng_num is not None:
        ticker_items.append({
            "label": "恐慌貪婪指數",
            "value": str(fng_num),
            "detail": str(today_fng["value_classification"]) if today_fng else "",
            "tone": fng_tone,
        })
    ticker_items.append({
            "label": "DCA 實戰帳本",
            "value": (
                presentation.format_metric(dca_return_pct, "percent")
                if dca_return_pct is not None else "沿用最近紀錄"
            ),
            "detail": (
                f"美元總值 {presentation.format_metric(dca_snapshot.get('current_value', 0), 'price')}"
                if dca_snapshot else "沒有新成交也沿用上次部位"
            ),
            "tone": (
                "neutral" if dca_return_pct is None
                else "positive" if float(dca_return_pct) >= 0 else "negative"
            ),
        })
    mvrv_info = mdata.get("mvrv") or {}
    market_zone = mvrv_info.get("zone") or ""
    slide_insights = {
        "mvrv_available": bool(mvrv_info.get("mvrv_type") == "z_score" and mvrv_info.get("value") is not None),
        "mvrv": presentation.format_metric(mvrv_info["value"], "mvrv") if mvrv_info.get("value") is not None else "",
        "mvrv_name": mvrv_info.get("display_name", "MVRV Z-Score"),
        "zone": market_zone,
        "mvrv_pct": mvrv_info.get("bar_pct", 40),
        "support": presentation.format_metric(mdata["btc_metrics"]["support"], "price"),
        "resistance": presentation.format_metric(mdata["btc_metrics"]["resistance"], "price"),
        "rsi": f"{mdata['btc_rsi']}",
        "sma": (
            f"100日 {presentation.format_metric(float(mdata['ma100d']), 'price')} / "
            f"200日 {presentation.format_metric(float(mdata['ma200d']), 'price')}"
        ),
        "headline": market_zone if market_zone else "先看價格確認",
        "detail": (
            f"帳本 {presentation.format_metric(dca_return_pct, 'percent')}"
            if dca_return_pct is not None else "部位沿用最近一筆有效紀錄"
        ),
    }

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            for idx, (title, color, body, ctype) in enumerate(slides_cfg, 1):
                title = presentation.display_text(title)
                body = presentation.display_text(body)
                chart_path = (
                    chart_paths.get("dca") if ctype == "dca"
                    else chart_paths.get("bars") if ctype == "bars"
                    else chart_paths.get("dual") if ctype == "dual"
                    else chart_paths.get("price") if ctype == "btc_structure"
                    else chart_paths.get("rsi") if ctype == "btc_rsi"
                    else None
                )
                planned_item = planned_slides[idx - 1] if planned_slides and idx - 1 < len(planned_slides) else {}
                layout = planned_item.get("layout") or presentation.assign_daily_layout(
                    {"title": title, "chart": ctype, "role": planned_item.get("role", "")},
                    idx - 1,
                )
                if layout == "chart_spotlight" and not (chart_path or ctype == "gauge"):
                    layout = "impact_quote"

                # B-Roll：優先根據「本頁實際播報標題＋內文」即時產生搜尋詞，
                # 不再依賴上游可能不存在的 planned_item["broll_query"]。
                broll_query = (
                    presentation.assign_broll_query({
                        "title": title,
                        "body": body,
                        "layout": layout,
                        "role": planned_item.get("role", ""),
                    })
                    or planned_item.get("broll_query", "")
                )
                broll_path = None
                if broll_query:
                    print(f"[Pixabay B-Roll] 第 {idx} 頁依口播內容搜尋：{broll_query}")
                    broll_path = broll_engine.fetch_pixabay_broll(
                        broll_query, orientation=broll_engine.HORIZONTAL
                    )
                    if broll_path:
                        print(
                            f"[Pixabay B-Roll] 第 {idx} 頁已下載素材："
                            f"{os.path.basename(broll_path)}"
                        )
                    else:
                        raise RuntimeError(
                            f"第 {idx} 頁未取得新的唯一 Pixabay 素材；"
                            "為避免成片缺少實景或重複使用舊素材，停止主片製作。"
                        )

                if layout == "versus_clash":
                    if "利多" in body or "風險" in body:
                        compare_positive, compare_risk = script_writer.split_news_summary(body)
                    else:
                        body_lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
                        compare_positive = body_lines[0] if body_lines else title
                        compare_risk = body_lines[1] if len(body_lines) > 1 else "若關鍵均線或量能未能確認，短線波動與回撤風險仍在。"
                else:
                    compare_positive, compare_risk = ("", "")

                slide_data = {
                    "label": f"幣看趨勢｜比特幣與加密貨幣情報　{today_str}",
                    "title": title,
                    "highlights": (
                        [] if layout == "versus_clash"
                        else [line for line in body.splitlines() if line.strip()]
                    ),
                    "body": body,
                    "chart_path": chart_path,
                    "chart_type": ctype,
                    "gauge_value": fng_num,
                    "caption": sdata.get("dca_slide_caption", "") if ctype == "dca" else "",
                    "accent": color,
                    "compact": ctype == "dca",
                    "layout": layout,
                    "visual_style": sdata.get("visual_style") or presentation.visual_style_for_format(sdata.get("format_id")),
                    "compare_positive": compare_positive,
                    "compare_risk": compare_risk,
                    "ticker_items": ticker_items,
                    "insights": slide_insights,
                    "insight_labels": {"mvrv": mvrv_info.get("display_name", "MVRV Z-Score")},
                    "broll_path": broll_path,
                    "reveal_timestamps": slide_reveal_map.get(idx),
                }
                print(
                    f"[Slide Render] {idx}/{slide_count}｜"
                    f"口播對齊時長 {slide_durations[idx - 1]:.2f}s｜"
                    f"Pixabay={'已套用' if broll_path else '無素材'}｜"
                    f"重點揭示 {slide_reveal_map.get(idx, [])}"
                )
                render_animated_slide(
                    idx, slide_data, slide_durations[idx - 1], f"p{idx}.mp4", browser,
                    reveal_timestamps=slide_reveal_map.get(idx),
                )
        finally:
            browser.close()

    # 主片 B-Roll 覆蓋率驗收：有設定 API Key 卻沒有成功素材時，不再靜默輸出全暗底影片。
    broll_stats = broll_engine.get_episode_stats()[broll_engine.HORIZONTAL]
    coverage = (
        broll_stats["success"] / broll_stats["requested"]
        if broll_stats["requested"] else 0.0
    )
    print(
        f"[Pixabay B-Roll] 主片素材統計：成功 {broll_stats['success']}/"
        f"{broll_stats['requested']}｜失敗 {broll_stats['failed']}｜"
        f"缺少金鑰 {broll_stats['missing_key']}｜覆蓋率 {coverage:.0%}"
    )
    if os.environ.get("PIXABAY_API_KEY", "").strip():
        min_coverage = float(os.environ.get("PIXABAY_MIN_COVERAGE", "0.5"))
        if broll_stats["requested"] and coverage < min_coverage:
            raise RuntimeError(
                f"主片 Pixabay B-Roll 覆蓋率 {coverage:.0%}，低於最低要求 "
                f"{min_coverage:.0%}；停止輸出，請檢查 API Key、Pixabay 配額與下載日誌。"
            )
    elif broll_stats["requested"]:
        raise RuntimeError(
            "主片有需要實景底片的頁面，但未設定 PIXABAY_API_KEY；"
            "停止輸出，避免再次產生沒有 Pixabay 畫面的影片。"
        )

    create_outro_frame()
    if has_intro_video:
        frame_count_output = subprocess.check_output(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-count_frames", "-show_entries", "stream=nb_read_frames",
                "-of", "default=noprint_wrappers=1:nokey=1", "info.mp4",
            ]
        ).decode().strip()
        source_frame_count = int(frame_count_output)
        if source_frame_count <= 0:
            raise RuntimeError("info.mp4 does not contain decodable video frames.")
        boomerang_frames = source_frame_count * 2
        intro_filter = (
            "[1:v]setpts=PTS-STARTPTS,"
            "scale=660:660:force_original_aspect_ratio=decrease,"
            "pad=660:660:(ow-iw)/2:(oh-ih)/2,setsar=1,"
            "split=2[forward][backward];"
            "[backward]reverse,setpts=PTS-STARTPTS[reversed];"
            f"[forward][reversed]concat=n=2:v=1:a=0,"
            f"loop=loop=-1:size={boomerang_frames}:start=0,"
            f"fps={VIDEO_FPS},trim=duration=15,setpts=PTS-STARTPTS[animation];"
            f"[0:v]scale={MAIN_VIDEO_WIDTH}:{MAIN_VIDEO_HEIGHT}:flags=lanczos,setsar=1[base];"
            "[base][animation]overlay=630:98:shortest=1,"
            "fade=t=out:st=14:d=1,format=yuv420p[output]"
        )
        subprocess.run(
            [
                "ffmpeg", "-y", "-loop", "1", "-framerate", str(VIDEO_FPS),
                "-i", "intro_frame.png", "-i", "info.mp4",
                "-filter_complex", intro_filter, "-map", "[output]", "-an",
                "-t", "15", "-r", str(VIDEO_FPS), "-c:v", "libx264",
                "-tune", "stillimage", "-preset", "medium", "-crf", VIDEO_CRF,
                "-pix_fmt", "yuv420p", "intro.mp4",
            ],
            check=True,
        )

    create_outro_video(outro_duration)
    with open("list.txt", "w", encoding="utf-8") as f:
        if has_intro_video:
            f.write("file 'intro.mp4'\n")
        for i in range(1, slide_count + 1):
            f.write(f"file 'p{i}.mp4'\n")
        f.write("file 'outro.mp4'\n")

    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "list.txt", "-c", "copy", "base.mp4"], check=True)

    # 6. VTuber 雙人動態與音軌多工合成
    talk_videos = {
        "cat": "cat_vtober.mp4" if os.path.exists("cat_vtober.mp4") else None,
        "dog": "dor_vtober.mp4" if os.path.exists("dor_vtober.mp4") else None,
    }
    speaker_avatar_paths = create_speaker_avatars(talk_videos)
    cat_speaking = "+".join(
        f"between(t,{start:.6f},{end:.6f})"
        for start, end in speaker_intervals["F"]
    ) or "0"
    dog_speaking = "+".join(
        f"between(t,{start:.6f},{end:.6f})"
        for start, end in speaker_intervals["M"]
    ) or "0"
    speech_window = f"between(t,{intro_dur:.6f},{speech_end_t:.6f})"
    mix_voice_with_bed("podcast.mp3", "podcast_bed.mp3", slide_start_times.values())
    mux_command = ["ffmpeg", "-y", "-i", "base.mp4", "-i", "podcast_bed.mp3"]
    input_indexes = {}

    def add_avatar_input(name, path, is_video=False):
        input_index = len(input_indexes) + 2
        if is_video:
            mux_command.extend(["-stream_loop", "-1", "-i", path])
        else:
            mux_command.extend(["-loop", "1", "-framerate", str(VIDEO_FPS), "-i", path])
        input_indexes[name] = input_index
        return input_index

    add_avatar_input("cat_idle", speaker_avatar_paths["cat_idle"])
    add_avatar_input(
        "cat_talk",
        talk_videos["cat"] or speaker_avatar_paths["cat_speaking"],
        is_video=bool(talk_videos["cat"]),
    )
    add_avatar_input("dog_idle", speaker_avatar_paths["dog_idle"])
    add_avatar_input(
        "dog_talk",
        talk_videos["dog"] or speaker_avatar_paths["dog_speaking"],
        is_video=bool(talk_videos["dog"]),
    )
    if any(talk_videos.values()):
        add_avatar_input("circle_mask", speaker_avatar_paths["circle_mask"])
    for speaker in ("cat", "dog"):
        if talk_videos[speaker]:
            add_avatar_input(f"{speaker}_ring", speaker_avatar_paths[f"{speaker}_ring"])

    avatar_filters = ["[0:v]ass=subs.ass[v_sub]"]
    active_speakers = [speaker for speaker in ("cat", "dog") if talk_videos[speaker]]
    if len(active_speakers) == 2:
        avatar_filters.append(
            f"[{input_indexes['circle_mask']}:v]format=gray,split=2"
            "[cat_mask][dog_mask]"
        )
    elif active_speakers:
        speaker = active_speakers[0]
        avatar_filters.append(
            f"[{input_indexes['circle_mask']}:v]format=gray[{speaker}_mask]"
        )
    for speaker in active_speakers:
        input_index = input_indexes[f"{speaker}_talk"]
        avatar_filters.extend(
            [
                f"[{input_index}:v]fps={VIDEO_FPS},"
                "scale=252:252:force_original_aspect_ratio=increase,"
                f"crop=252:252,setsar=1,format=rgba[{speaker}_square]",
                f"[{speaker}_square][{speaker}_mask]alphamerge[{speaker}_talk_circle]",
            ]
        )

    cat_talk_input = (
        "[cat_talk_circle]" if talk_videos["cat"]
        else f"[{input_indexes['cat_talk']}:v]"
    )
    dog_talk_input = (
        "[dog_talk_circle]" if talk_videos["dog"]
        else f"[{input_indexes['dog_talk']}:v]"
    )
    avatar_filters.extend(
        [
            f"[v_sub][{input_indexes['cat_idle']}:v]overlay=x=42:y=817.5:"
            f"enable='{speech_window}*not({cat_speaking})'[v_cat_idle]",
            f"[v_cat_idle]{cat_talk_input}"
            f"overlay=x=42:y='804+6*sin(t*14)':eval=frame:"
            f"enable='{cat_speaking}'[v_cat_talk]",
        ]
    )
    cat_stage = "v_cat_talk"
    if talk_videos["cat"]:
        avatar_filters.append(
            f"[{cat_stage}][{input_indexes['cat_ring']}:v]"
            f"overlay=x=42:y='804+6*sin(t*14)':eval=frame:"
            f"enable='{cat_speaking}'[v_cat_speaking]"
        )
        cat_stage = "v_cat_speaking"
    avatar_filters.append(
        f"[{cat_stage}][{input_indexes['dog_idle']}:v]overlay=x=1653:y=817.5:"
        f"enable='{speech_window}*not({dog_speaking})'[v_dog_idle]"
    )
    avatar_filters.append(
        f"[v_dog_idle]{dog_talk_input}"
        f"overlay=x=1653:y='804+6*sin(t*16)':eval=frame:"
        f"enable='{dog_speaking}'[v_dog_talk]"
    )
    dog_stage = "v_dog_talk"
    if talk_videos["dog"]:
        avatar_filters.append(
            f"[{dog_stage}][{input_indexes['dog_ring']}:v]"
            f"overlay=x=1653:y='804+6*sin(t*16)':eval=frame:"
            f"enable='{dog_speaking}'[v_dog_speaking]"
        )
        dog_stage = "v_dog_speaking"
    avatar_filters.append(f"[{dog_stage}]null[v_out]")
    avatar_filter = ";".join(avatar_filters)
    mux_command.extend(
        [
            "-filter_complex", avatar_filter,
            "-map", "[v_out]", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "medium", "-crf", VIDEO_CRF,
            "-threads", "4", "-r", str(VIDEO_FPS), "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-shortest", "output.mp4",
        ]
    )
    subprocess.run(mux_command, check=True)

    # 7. 計算章節時間軸
    actual_video_duration = intro_video_duration + speech_elapsed + outro_pause + outro_duration
    def fmt_chapter(sec):
        sec = max(0, min(int(round(sec)), int(round(actual_video_duration))))
        minutes, seconds = divmod(sec, 60)
        return f"{minutes:02d}:{seconds:02d}"

    chapter_indexes = [1]
    chapter_entries = [(0.0, "主題動畫片頭")] if has_intro_video else []
    for slide_number in range(2, slide_count + 1):
        if (
            round(slide_start_times[slide_number])
            - round(slide_start_times[chapter_indexes[-1]]) >= 10
        ):
            chapter_indexes.append(slide_number)
    for slide_number in chapter_indexes:
        chapter_entries.append(
            (slide_start_times[slide_number], slides_cfg[slide_number - 1][0])
        )
    chapter_entries.append(
        (intro_video_duration + speech_elapsed + outro_pause, "片尾互動與推薦影片")
    )
    chapter_times = [fmt_chapter(start) for start, _ in chapter_entries]
    chapter_lines = "\n".join(
        f"{chapter_times[position]} {title}"
        for position, (_, title) in enumerate(chapter_entries)
    )

    dca_description_section = (
        f"\n\n📊 BTC 長期定投（DCA）實戰帳本：\n{dca_data.get('dca_report', '')}"
        if show_dca else ""
    )
    v_desc = (
        f"歡迎收聽【幣看趨勢｜比特幣與加密貨幣情報】！本集為您彙整 {today_str} 最新市場指標與網路公開重點議題，"
        f"由「以太喵」與「比特汪」帶你透過多空理性打折視角掌握幣圈脈動！頻道以長期持有與紀律DCA為核心，不鼓勵盲目短線或高槓桿交易。\n\n"
        f"⏱️ 本集簡報章節（依{slide_count}張圖卡時間對齊，含12秒動態片尾）：\n{chapter_lines}\n\n"
        f"📊 BTC 核心數據：\n{mdata['s1']}{dca_description_section}\n\n"
        f"📊 ETH 核心數據：\n{mdata['eth_slide']}\n\n"
        f"⚠️ 免責聲明：\n本頻道所有內容均為網路公開資訊與市場數據整理分享，不構成任何形式之投資建議或買賣邀約。加密貨幣市場波動劇烈，投資前請務必自行研究並審慎評估風險。\n\n"
        f"#比特幣 #加密貨幣 #區塊鏈 #BTC #ETH #SOL #技術分析 #均線 #RSI #恐慌指數 #幣看趨勢 #DCA"
    )

    # 8. 製作封面縮圖 thumb.jpg
    hook = _daily_thumb_hook(mdata, sdata)
    pct_fill = "#16a34a" if mdata["btc_pct"] >= 0 else "#dc2626"
    badges = [(f"BTC {mdata['btc_pct']:+.2f}%", pct_fill)]
    mvrv_info = mdata.get("mvrv") or {}
    if mvrv_info.get("value") is not None:
        mvrv_label = mvrv_info.get("display_name", "MVRV")
        badges.append((f"{mvrv_label} {presentation.format_metric(mvrv_info['value'], 'mvrv')}", "#d97706"))
    elif fng_num is not None:
        badges.append((f"恐慌 {int(fng_num)}", "#0369a1"))
    t_img = Image.new("RGB", (MAIN_VIDEO_WIDTH, MAIN_VIDEO_HEIGHT), "#070b14")
    _draw_impact_thumb(
        t_img, hook, badges,
        (1280, 80, 560, 560) if os.path.exists("avatar.png") else None,
    )
    thumb_path = "thumb.jpg"
    t_img.save(thumb_path, "JPEG", quality=92)

    return {
        "output_path": "output.mp4",
        "thumb_path": thumb_path,
        "v_desc": v_desc,
        "chapter_lines": chapter_lines,
        "video_title": sdata["video_title"],
    }


# ── Shorts 9:16 短片影音合成 ──────────────────────────────────────────

def render_short_video(sdata, mdata, config):
    is_test = config["is_test"]
    today_str = config["today_str"]
    short_target_chars = 45 if is_test else 230
    short_duration_limit = 15.0 if is_test else 60.0

    headline_for_short = (
        script_writer.clean_slide_title(sdata["slide_news_details"][0]["slide_title"])
        if sdata.get("slide_news_details")
        else "今日市場焦點"
    )
    today_fng = mdata["today_fng"]
    fng_num = mdata["fng_num"]
    short_news = (
        f"資料抓取時間：{mdata['data_fetched_at']}\n"
        f"頭條：{headline_for_short}\n"
        f"多空打折摘要：{sdata['slide_news'][0]}\n"
        f"BTC：{presentation.format_usd(mdata['cur_p'])}，24H {'+' if mdata['btc_pct'] >= 0 else ''}{mdata['btc_pct']:.2f}%，"
        f"100日均線 {presentation.format_usd(mdata['ma100d'])}，200日均線 {presentation.format_usd(mdata['ma200d'])}，RSI {mdata['btc_rsi']}\n"
        f"ETH：{presentation.format_usd(mdata['spot_prices']['ETH'])}，24H {'+' if mdata['eth_pct'] >= 0 else ''}{mdata['eth_pct']:.2f}%，"
        f"RSI {mdata['eth_rsi']}\n"
        + (
            f"恐慌與貪婪指數：{today_fng['value']}（{today_fng['value_classification']}）"
            if today_fng else ""
        )
    )

    short_prompt = f"""你是【幣看趨勢】短影音編劇，請使用下方當日頭條與市場數據，寫一段約{short_target_chars}字的繁體中文雙人快節奏短影音逐字稿。
【資料依循最高優先級】只能依據下列本次資料及抓取時間，不可使用預訓練記憶補入價格、事件日期或新聞；缺少的指標直接略過，不猜測或編造。
資料：
{short_news}

規格：
1. 正式模式總長約230字，適合1.25倍速配音約45至50秒；測試模式總長約45字，控制在15秒內。
2. 第一位主持人在開頭3秒內用反直覺問題或數據落差當鉤子。
3. 中間只講資料中的核心新聞、BTC/ETH數據與一個理性風險折價，不可編造資料。
4. 結尾明確引導觀眾點擊頻道觀看完整市場解析，不得預設或宣稱固定片長。
5. 以「男：」與「女：」交替對話，至少各說一次；只輸出逐字稿，不要標題、註解或Markdown。"""

    if script_writer.reuse_script_enabled():
        raw_short_script = (
            f"男：{headline_for_short}，比特幣現在 {presentation.format_usd(mdata['cur_p'])}。\n"
            "女：風險仍在，完整解析請看今天的正片。"
        )
        print("[REUSE_SCRIPT] Shorts 使用快取頭條，不呼叫 Gemini。")
    else:
        raw_short_script = script_writer.call_gemini(short_prompt)
    cc = OpenCC("s2twp")
    short_dialogues = []
    for line in raw_short_script.splitlines():
        match = re.match(r"^(男|女)[：:]\s*(.+)", line.strip())
        if match:
            short_dialogues.append(
                (match.group(1), cc.convert(match.group(2).strip()))
            )
    if not short_dialogues or {role for role, _ in short_dialogues} != {"男", "女"}:
        raise RuntimeError("Gemini did not return valid dialogue for both short-video hosts.")

    with ThreadPoolExecutor(max_workers=5) as executor:
        short_segments = list(
            executor.map(
                lambda item: synth_audio_segment(item, "short_seg"),
                enumerate(short_dialogues),
            )
        )
    short_segments.sort(key=lambda segment: segment[0])
    short_audio_duration = sum(segment[3] for segment in short_segments)
    if short_audio_duration <= 0 or short_audio_duration > short_duration_limit:
        raise RuntimeError(
            f"Short-video speech duration {short_audio_duration:.2f}s exceeds "
            f"the {short_duration_limit:g}s limit; refusing to cut off speech."
        )

    with open("short_audio_list.txt", "w", encoding="utf-8") as audio_list:
        for _, filename, _, _ in short_segments:
            audio_list.write(f"file '{filename}'\n")
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", "short_audio_list.txt", "-c", "copy", "short_audio.mp3",
        ],
        check=True,
    )

    short_ass_header = (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 2\n"
        f"PlayResX: {SHORT_VIDEO_WIDTH}\nPlayResY: {SHORT_VIDEO_HEIGHT}\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Noto Sans CJK TC,68,&H0000D7FF,&H000000FF,&H00101010,"
        "&HAA000000,1,0,0,0,100,100,0,0,1,4,2,2,100,100,90,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text\n"
    )
    short_subtitle_events = []
    subtitle_time = 0.0
    for _, _, spoken_text, segment_duration in short_segments:
        clean_text = re.sub(r"[{}\\]", "", spoken_text).strip()
        subtitle_chunks = split_short_subtitle_text(clean_text)
        total_width = sum(
            short_subtitle_visual_width(chunk)
            for chunk, _ in subtitle_chunks
        )
        segment_time = subtitle_time
        for index, (chunk, scale_percent) in enumerate(subtitle_chunks):
            chunk_width = short_subtitle_visual_width(chunk)
            subtitle_end = (
                subtitle_time + segment_duration
                if index == len(subtitle_chunks) - 1
                else segment_time + segment_duration * chunk_width / total_width
            )
            highlighted = re.sub(
                kw_pat,
                r"{\\b1\\c&H0024BFFF&}\1{\\b0\\c&H0000D7FF&}",
                chunk,
            )
            if scale_percent != 100:
                highlighted = (
                    f"{{\\fscx{scale_percent}\\fscy{scale_percent}}}{highlighted}"
                )
            short_subtitle_events.append(
                f"Dialogue: 0,{fmt_ass_t(segment_time)},{fmt_ass_t(subtitle_end)},"
                f"Default,,60,60,0,,{highlighted}"
            )
            segment_time = subtitle_end
        subtitle_time += segment_duration
    with open("subs_short.ass", "w", encoding="utf-8") as subtitle_file:
        subtitle_file.write(short_ass_header + "\n".join(short_subtitle_events))

    avatar_markup = '<div class="avatar-placeholder">喵・汪</div>'
    if os.path.exists("avatar.png"):
        with Image.open("avatar.png") as avatar:
            avatar_buffer = io.BytesIO()
            avatar.convert("RGBA").resize(
                (240, 240), Image.LANCZOS
            ).save(avatar_buffer, format="PNG")
        avatar_encoded = base64.b64encode(avatar_buffer.getvalue()).decode("ascii")
        avatar_markup = (
            '<img class="avatar" alt="以太喵與比特汪" '
            f'src="data:image/png;base64,{avatar_encoded}">'
        )

    short_highlight_state = {"remaining": 3}
    short_highlights = [
        highlight_key_numbers(headline_for_short[:34], short_highlight_state),
        highlight_key_numbers(script_writer.clean_slide_text(sdata["slide_news"][0])[:42], short_highlight_state),
        "完整市場解析，點擊頻道觀看",
    ]
    highlights_markup = "".join(
        f'<li style="--delay:{index * 0.6:.1f}s">{highlight}</li>'
        for index, highlight in enumerate(short_highlights)
    )
    fear_stat = ""
    if today_fng:
        fear_value = html.escape(str(today_fng["value"]))
        fear_classification = html.escape(str(today_fng["value_classification"]))
        fear_stat = (
            f'<div class="stat"><strong>{fear_value}</strong>'
            f"<small>恐懼貪婪・{fear_classification}</small></div>"
        )

    # Shorts 9:16 全面優先使用直式實景動態底片。
    short_broll_path = broll_engine.fetch_pixabay_broll(
        "cryptocurrency, stock market, abstract technology",
        orientation=broll_engine.VERTICAL,
    )
    if not short_broll_path:
        raise RuntimeError("Main Shorts could not obtain a new unique vertical Pixabay clip.")
    short_broll_markup = ""
    if short_broll_path and os.path.exists(short_broll_path):
        try:
            with open(short_broll_path, "rb") as short_broll_file:
                short_broll_data = base64.b64encode(short_broll_file.read()).decode("ascii")
            short_broll_markup = (
                '<video class="broll-bg" autoplay muted loop playsinline '
                f'src="data:video/mp4;base64,{short_broll_data}"></video>'
                '<div class="broll-veil"></div>'
            )
        except OSError:
            short_broll_markup = ""

    short_html = f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width={SHORT_VIDEO_WIDTH}, initial-scale=1">
<style>
{presentation.dark_css(bool(short_broll_markup))}
* {{ box-sizing: border-box; }}
html, body {{ width:{SHORT_VIDEO_WIDTH}px; height:{SHORT_VIDEO_HEIGHT}px; margin:0; overflow:hidden; }}
body {{
  position:relative; width:{SHORT_VIDEO_WIDTH}px; height:{SHORT_VIDEO_HEIGHT}px;
  color:#f8fafc; background:#090d16;
  font-family:'Noto Sans CJK TC',sans-serif;
}}
.short-stage {{
  position:relative; width:{SHORT_DESIGN_WIDTH}px; height:{SHORT_DESIGN_HEIGHT}px;
  overflow:hidden; background:#090d16;
  transform:scale({SHORT_OUTPUT_SCALE}); transform-origin:top left;
}}
.short-stage::before {{
  position:absolute; inset:-8%; content:""; opacity:.5;
  background:
    radial-gradient(ellipse at 50% 38%,rgba(56,189,248,.2),transparent 40%),
    radial-gradient(ellipse at 20% 75%,rgba(167,139,250,.16),transparent 35%),
    linear-gradient(rgba(56,189,248,.05) 1px,transparent 1px),
    linear-gradient(90deg,rgba(56,189,248,.05) 1px,transparent 1px);
  background-size:auto,auto,42px 42px,42px 42px;
  animation:gridFlow 12s linear infinite;
}}
@keyframes gridFlow {{ to {{ transform:translate3d(42px,21px,0); }} }}
.broll-bg {{
  /* 直式 Pixabay 全螢幕插播，與簡報卡輪流播放。 */
  position:absolute; inset:0; z-index:1; width:100%; height:100%;
  object-fit:cover; opacity:0;
  animation:shortBrollInterlude var(--broll-cycle, 18s) linear infinite;
}}
 .short-stage.has-broll .brand, .short-stage.has-broll .avatar, .short-stage.has-broll .avatar-placeholder, .short-stage.has-broll .panel {{
  animation:shortSlideInterlude var(--broll-cycle, 18s) linear infinite;
}}
@keyframes shortBrollInterlude {{
  0%,68%,94%,100% {{ opacity:0; }}
  72%,90% {{ opacity:1; }}
}}
@keyframes shortSlideInterlude {{
  0%,68%,94%,100% {{ opacity:1; }}
  72%,90% {{ opacity:0; }}
}}
.broll-veil {{ display:none; }}
.brand {{
  position:absolute; top:24px; width:100%; text-align:center; z-index:3;
  color:#fbbf24; font-size:29px; font-weight:800; letter-spacing:2px;
}}
.brand::after {{
  display:block; width:210px; height:3px; margin:10px auto 0;
  background:linear-gradient(90deg,transparent,#38bdf8,transparent); content:"";
}}
.avatar {{
  position:absolute; top:78px; left:240px; width:240px; height:240px; z-index:3;
  object-fit:contain; border:3px solid #fbbf24; border-radius:50%;
  box-shadow:0 0 34px rgba(251,191,36,.38); animation:avatarIn .8s ease-out both;
}}
.avatar-placeholder {{
  position:absolute; top:78px; left:240px; width:240px; height:240px; z-index:3;
  display:grid; place-items:center; border:3px solid #fbbf24; border-radius:50%;
  color:#fbbf24; background:#111827; font-size:52px;
}}
@keyframes avatarIn {{
  from {{ opacity:0; transform:scale(.75); }}
  to {{ opacity:1; transform:scale(1); }}
}}
.panel {{
  position:absolute; top:348px; left:34px; right:34px; bottom:260px; z-index:3;
  padding:28px 26px; border:2px solid rgba(251,191,36,.78); border-radius:24px;
  background:rgba(15,23,42,.9); box-shadow:0 0 35px rgba(56,189,248,.14);
  overflow:hidden; text-align:center;
}}
.eyebrow {{ color:#94a3b8; font-size:22px; letter-spacing:4px; }}
.symbol {{ margin-top:6px; color:#38bdf8; font-size:32px; font-weight:800; }}
.price {{
  margin:2px 0 12px; color:#fbbf24; font-size:68px; font-weight:900;
  text-shadow:0 0 26px rgba(251,191,36,.38);
  animation:numberIn .8s cubic-bezier(.2,.8,.2,1) both,numberPulse 3.5s ease-in-out 1s infinite;
}}
@keyframes numberIn {{ from {{ opacity:0; transform:translateY(25px) scale(.92); }} to {{ opacity:1; transform:translateY(0) scale(1); }} }}
@keyframes numberPulse {{ 0%,100% {{ text-shadow:0 0 18px rgba(251,191,36,.25); }} 50% {{ text-shadow:0 0 34px rgba(251,191,36,.62); }} }}
.stats {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; margin:12px 0 20px; }}
.stat {{
  padding:12px 6px; border:1px solid rgba(56,189,248,.35); border-radius:14px;
  background:rgba(8,15,28,.8); animation:fadeInUp .6s ease-out .55s both;
}}
.stat strong {{ display:block; color:#fbbf24; font-size:29px; }}
.stat small {{ color:#94a3b8; font-size:17px; }}
.points {{ list-style:none; margin:0; padding:0; text-align:left; }}
.points li {{
  position:relative; margin:13px 0; padding:0 4px 0 28px;
  color:#e2e8f0; font-size:22px; line-height:1.35; overflow-wrap:anywhere;
  animation:fadeInUp .6s ease-out var(--delay) both;
}}
.points li::before {{
  position:absolute; top:.48em; left:2px; width:9px; height:9px;
  border-radius:50%; background:#38bdf8; box-shadow:0 0 12px #38bdf8; content:"";
}}
.hl {{ position:relative; z-index:0; color:#fff2b3; }}
.hl::after {{
  position:absolute; z-index:-1; left:0; bottom:-.06em; width:100%; height:.32em;
  background:linear-gradient(90deg,rgba(251,191,36,.8),rgba(251,191,36,.14));
  content:""; transform:scaleX(0); transform-origin:left;
  animation:highlight .65s ease-out .6s forwards;
}}
@keyframes highlight {{ to {{ transform:scaleX(1); }} }}
@keyframes fadeInUp {{ from {{ opacity:0; transform:translateY(20px); }} to {{ opacity:1; transform:translateY(0); }} }}
</style></head>
<body>
<div class="short-stage{' has-broll' if short_broll_markup else ''}" style="--broll-cycle:{min(18.0, max(8.0, float(15.0 if is_test else short_audio_duration))):.3f}s;">
{short_broll_markup}
<header class="brand">【幣看趨勢】｜每日市場快訊</header>
{avatar_markup}
<main class="panel">
  <div class="eyebrow">BTC 即時價格</div>
  <div class="symbol">BITCOIN　BTC</div>
  <div class="price">{presentation.format_usd(mdata['cur_p'])}</div>
  <section class="stats">
    <div class="stat"><strong>{mdata['btc_pct']:+.2f}%</strong><small>BTC 24H</small></div>
    <div class="stat"><strong>{presentation.format_usd(mdata['spot_prices']['ETH'])}</strong><small>ETH 現價</small></div>
    <div class="stat"><strong>{mdata['eth_pct']:+.2f}%</strong><small>ETH 24H</small></div>
    {fear_stat}
  </section>
  <ul class="points">{highlights_markup}</ul>
</main>
</div>
<script>
window.fitShortLayout = () => {{
  const panel = document.querySelector(".panel");
  const points = [...document.querySelectorAll(".points li")];
  for (let attempt = 0; attempt < 12 && panel.scrollHeight > panel.clientHeight + 1; attempt++) {{
    for (const point of points) {{
      const size = parseFloat(getComputedStyle(point).fontSize);
      if (size > 17) point.style.fontSize = `${{size - 1}}px`;
    }}
  }}
  return document.documentElement.scrollWidth <= {SHORT_VIDEO_WIDTH} &&
    document.documentElement.scrollHeight <= {SHORT_VIDEO_HEIGHT} &&
    panel.scrollHeight <= panel.clientHeight + 1;
}};
</script>
</body></html>"""

    render_duration = 15.0 if is_test else short_audio_duration
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            with tempfile.TemporaryDirectory(prefix="short_render_") as video_dir:
                context = browser.new_context(
                    viewport={"width": SHORT_VIDEO_WIDTH, "height": SHORT_VIDEO_HEIGHT},
                    device_scale_factor=2,
                    record_video_dir=video_dir,
                    record_video_size={
                        "width": SHORT_VIDEO_WIDTH,
                        "height": SHORT_VIDEO_HEIGHT,
                    },
                )
                page = context.new_page()
                recording_started_at = time.monotonic()
                video = page.video
                try:
                    page.set_content(short_html, wait_until="load")
                    page.evaluate("() => document.fonts.ready.then(() => true)")
                    if not page.evaluate("() => window.fitShortLayout()"):
                        raise ValueError("Short-video HTML exceeds its safe area.")
                    capture_preroll = 0.75
                    page.wait_for_timeout(round(capture_preroll * 1000))
                    capture_offset = time.monotonic() - recording_started_at
                    poster_path = os.path.join(video_dir, "short_poster.png")
                    page.screenshot(path=poster_path)
                    short_tail_record_seconds = max(0.1, render_duration - capture_preroll + 0.25)
                    page.wait_for_timeout(round(short_tail_record_seconds * 1000))
                finally:
                    context.close()
                recording_path = video.path()
                short_tail_duration = max(0.1, render_duration - capture_preroll)
                short_poster_filter = (
                    f"[0:v]fps={VIDEO_FPS},scale={SHORT_VIDEO_WIDTH}:{SHORT_VIDEO_HEIGHT}:flags=lanczos,"
                    f"trim=duration={capture_preroll:.6f},setpts=PTS-STARTPTS,format=yuv420p[poster];"
                    f"[1:v]trim=start={capture_offset:.6f},setpts=PTS-STARTPTS,fps={VIDEO_FPS},"
                    f"scale={SHORT_VIDEO_WIDTH}:{SHORT_VIDEO_HEIGHT}:flags=lanczos,"
                    f"tpad=stop_mode=clone:stop_duration={render_duration:.6f},"
                    f"trim=duration={short_tail_duration:.6f},format=yuv420p[tail];"
                    "[poster][tail]concat=n=2:v=1:a=0[v]"
                )
                subprocess.run(
                    [
                        "ffmpeg", "-y",
                        "-loop", "1", "-framerate", str(VIDEO_FPS),
                        "-t", f"{capture_preroll:.6f}", "-i", poster_path,
                        "-i", recording_path,
                        "-filter_complex", short_poster_filter, "-map", "[v]",
                        "-t", f"{render_duration:.6f}", "-an", "-r", str(VIDEO_FPS),
                        "-c:v", "libx264", "-preset", "medium", "-crf", VIDEO_CRF,
                        "-pix_fmt", "yuv420p", "short_visual.mp4",
                    ],
                    check=True,
                )
        finally:
            browser.close()

    mux_command = [
        "ffmpeg", "-y", "-i", "short_visual.mp4", "-i", "short_audio.mp3",
        "-vf", "ass=subs_short.ass",
    ]
    if is_test:
        mux_command.extend(["-af", "apad=whole_dur=15", "-t", "15"])
    else:
        mux_command.extend(["-t", f"{short_audio_duration:.6f}"])
    mux_command.extend(
        [
            "-c:v", "libx264", "-preset", "medium", "-crf", VIDEO_CRF,
            "-r", str(VIDEO_FPS), "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-shortest", "short.mp4",
        ]
    )
    subprocess.run(mux_command, check=True)
    return "short.mp4"


def _concat_media(list_name, output_name):
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_name, "-c", "copy", output_name],
        check=True,
    )


def _write_tutorial_thumb(tutorial):
    title = presentation.strip_bracket_tags(
        tutorial.get("title") or tutorial.get("lesson_title") or "成本帶怎麼看"
    )
    hook = _fit_cjk_hook(title, "成本帶怎麼看")
    image = Image.new("RGB", (1280, 720), "#070b14")
    _draw_impact_thumb(
        image, hook,
        [(str(tutorial.get("ep_id") or "深潛"), "#d97706")],
        (860, 36, 360, 360) if os.path.exists("avatar.png") else None,
    )
    image.save("thumb_tutorial.jpg", "JPEG", quality=92)
    return "thumb_tutorial.jpg"


def _tutorial_intro_and_outro(intro_seconds, outro_seconds, outro_pause):
    create_outro_audio(outro_seconds, outro_pause)
    create_outro_frame()
    create_outro_video(outro_seconds)
    has_intro = os.path.exists("info.mp4") and os.path.exists("info.mp3")
    intro_dur = 0.0
    if not has_intro:
        print("找不到 info.mp4 或 info.mp3，教學片頭改為略過。")
        return intro_dur, has_intro
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", "info.mp3", "-t", f"{intro_seconds:g}",
            "-af", "afade=t=out:st=13.5:d=1.5,apad=whole_dur=15",
            "-ar", "24000", "-ac", "1", "-c:a", "libmp3lame", "-b:a", "64k",
            "intro_cut.mp3",
        ],
        check=True,
    )
    intro_dur = float(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", "intro_cut.mp3"]
    ).decode().strip())
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", "info.mp4", "-t", f"{intro_seconds:g}",
            "-vf", f"scale={MAIN_VIDEO_WIDTH}:{MAIN_VIDEO_HEIGHT}:flags=lanczos,fps={VIDEO_FPS},format=yuv420p",
            "-an", "-r", str(VIDEO_FPS), "-c:v", "libx264", "-preset", "medium",
            "-crf", VIDEO_CRF, "-pix_fmt", "yuv420p", "intro.mp4",
        ],
        check=True,
    )
    return intro_dur, has_intro


def _tutorial_card_html(card, chart_markup, broll_path=None):
    layout = card.get("layout") or "impact"
    title = html.escape(str(card.get("title") or ""))
    headline = html.escape(str(card.get("headline") or ""))
    myth = html.escape(str(card.get("myth") or ""))
    truth = html.escape(str(card.get("truth") or ""))
    steps = "".join(
        f'<li><b>{index}</b><span>{html.escape(str(step))}</span></li>'
        for index, step in enumerate(card.get("steps") or [], 1)
    )
    if layout == "compare":
        body = (
            '<div class="split"><article class="bad"><small>迷思 ❌</small>'
            f'<p>{myth}</p></article><article class="good"><small>真相 ✅</small>'
            f'<p>{truth}</p></article></div>'
        )
    elif layout == "steps":
        body = f'<ol class="steps">{steps}</ol>'
    elif layout == "chart" and chart_markup:
        body = f'<div class="chart-row">{chart_markup}<aside><p>{truth}</p></aside></div>'
    else:
        body = f'<p class="giant">{headline}</p><p class="sub">{truth}</p>'
    broll_markup = ""
    if broll_path and os.path.isfile(broll_path):
        with open(broll_path, "rb") as handle:
            encoded = base64.b64encode(handle.read()).decode("ascii")
        broll_markup = (
            '<video autoplay muted loop playsinline '
            f'src="data:video/mp4;base64,{encoded}"></video>'
        )
    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<style>
html,body {{ margin:0; width:1920px; height:1080px; overflow:hidden; background:#070b14; color:#f8fafc;
  font-family:'Noto Sans CJK TC',sans-serif; }}
video {{ position:absolute; right:70px; top:150px; width:30%; height:630px;
  object-fit:cover; border:2px solid rgba(56,189,248,.72); border-radius:18px;
  box-shadow:0 0 34px rgba(56,189,248,.22); }}
.safe {{ position:absolute; left:180px; right:35%; top:70px; bottom:290px; }}
.kicker {{ color:#f59e0b; font-size:34px; font-weight:800; }}
h1 {{ margin:12px 0 24px; font-size:54px; line-height:1.25; }}
.giant {{ margin:20px 0; font-size:92px; font-weight:900; line-height:1.15; color:#fff7ed;
  text-shadow:0 0 28px rgba(245,158,11,.45); animation:glow 2.8s ease-in-out infinite; }}
.sub {{ font-size:36px; color:#cbd5e1; }}
.split {{ display:grid; grid-template-columns:1fr 1fr; gap:28px; height:72%; }}
article {{ border-radius:28px; padding:36px; }}
.bad {{ background:#3f1218; border:4px solid #ef4444; }}
.good {{ background:#052e1a; border:4px solid #22c55e; }}
small {{ font-size:32px; font-weight:800; }}
p {{ font-size:40px; line-height:1.35; }}
.steps {{ list-style:none; margin:0; padding:0; position:relative; }}
.steps::before {{ content:""; position:absolute; left:34px; top:36px; bottom:36px; width:4px; background:#f59e0b; }}
.steps li {{ display:flex; gap:22px; align-items:center; margin:18px 0; font-size:36px;
  animation:pop .45s ease-out both; }}
.steps li:nth-child(2) {{ animation-delay:.15s; }}
.steps li:nth-child(3) {{ animation-delay:.3s; }}
.steps b {{ width:72px; height:72px; border-radius:50%; display:grid; place-items:center;
  background:#f59e0b; color:#111827; }}
.chart-row {{ display:grid; grid-template-columns:1.3fr .7fr; gap:24px; height:100%; }}
.chart-row img {{ width:100%; height:620px; object-fit:contain; }}
aside {{ border:4px solid #38bdf8; border-radius:24px; padding:28px; background:#0f172a; }}
@keyframes glow {{ 50% {{ text-shadow:0 0 42px rgba(245,158,11,.8); }} }}
@keyframes pop {{ from {{ opacity:0; transform:translateY(16px); }} to {{ opacity:1; transform:none; }} }}
</style></head><body>{broll_markup}<div class="safe"><div class="kicker">{title}</div><h1>{headline}</h1>{body}</div></body></html>"""


def _record_tutorial_card(browser, index, card, duration, chart_markup, broll_path=None):
    record_seconds = min(float(duration), 4.0)
    remaining = max(0.0, float(duration) - record_seconds)
    tpad = f",tpad=stop_mode=clone:stop_duration={remaining:.6f}" if remaining else ""
    with tempfile.TemporaryDirectory(prefix=f"tcard_{index}_") as record_dir:
        context = browser.new_context(
            viewport={"width": MAIN_VIDEO_WIDTH, "height": MAIN_VIDEO_HEIGHT},
            record_video_dir=record_dir,
            record_video_size={"width": MAIN_VIDEO_WIDTH, "height": MAIN_VIDEO_HEIGHT},
        )
        page = context.new_page()
        video = page.video
        try:
            page.set_content(_tutorial_card_html(card, chart_markup, broll_path), wait_until="load")
            page.wait_for_timeout(max(1, round(record_seconds * 1000)))
        finally:
            context.close()
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", video.path(),
                "-vf", (
                    f"fps={VIDEO_FPS},scale={MAIN_VIDEO_WIDTH}:{MAIN_VIDEO_HEIGHT}:flags=lanczos,"
                    f"trim=duration={record_seconds:.6f},setpts=PTS-STARTPTS{tpad},format=yuv420p"
                ),
                "-t", f"{duration:.6f}", "-an", "-r", str(VIDEO_FPS),
                "-c:v", "libx264", "-preset", "medium", "-crf", VIDEO_CRF,
                "-pix_fmt", "yuv420p", f"tutorial_p{index}.mp4",
            ],
            check=True,
        )


def _mux_tutorial_avatars(video_path, audio_path, output_path, speaker_intervals, intro_dur, speech_end):
    talk_videos = {
        "cat": "cat_vtober.mp4" if os.path.exists("cat_vtober.mp4") else None,
        "dog": "dor_vtober.mp4" if os.path.exists("dor_vtober.mp4") else None,
    }
    assets = create_speaker_avatars(talk_videos)
    cat_speaking = "+".join(
        f"between(t,{start:.6f},{end:.6f})" for start, end in speaker_intervals["F"]
    ) or "0"
    dog_speaking = "+".join(
        f"between(t,{start:.6f},{end:.6f})" for start, end in speaker_intervals["M"]
    ) or "0"
    speech_window = f"between(t,{intro_dur:.6f},{speech_end:.6f})"
    command = ["ffmpeg", "-y", "-i", video_path, "-i", audio_path]
    indexes = {}

    def add_input(name, path, is_video=False):
        indexes[name] = len(indexes) + 2
        if is_video:
            command.extend(["-stream_loop", "-1", "-i", path])
        else:
            command.extend(["-loop", "1", "-framerate", str(VIDEO_FPS), "-i", path])

    add_input("cat_idle", assets["cat_idle"])
    add_input("cat_talk", talk_videos["cat"] or assets["cat_speaking"], bool(talk_videos["cat"]))
    add_input("dog_idle", assets["dog_idle"])
    add_input("dog_talk", talk_videos["dog"] or assets["dog_speaking"], bool(talk_videos["dog"]))
    if any(talk_videos.values()):
        add_input("circle_mask", assets["circle_mask"])
    for speaker in ("cat", "dog"):
        if talk_videos[speaker]:
            add_input(f"{speaker}_ring", assets[f"{speaker}_ring"])
    filters = ["[0:v]ass=tutorial_main.ass[v_sub]"]
    active = [speaker for speaker in ("cat", "dog") if talk_videos[speaker]]
    if len(active) == 2:
        filters.append(f"[{indexes['circle_mask']}:v]format=gray,split=2[cat_mask][dog_mask]")
    elif active:
        filters.append(f"[{indexes['circle_mask']}:v]format=gray[{active[0]}_mask]")
    for speaker in active:
        filters.append(
            f"[{indexes[f'{speaker}_talk']}:v]fps={VIDEO_FPS},"
            "scale=252:252:force_original_aspect_ratio=increase,crop=252:252,setsar=1,format=rgba"
            f"[{speaker}_square];[{speaker}_square][{speaker}_mask]alphamerge[{speaker}_talk_circle]"
        )
    cat_talk = "[cat_talk_circle]" if talk_videos["cat"] else f"[{indexes['cat_talk']}:v]"
    dog_talk = "[dog_talk_circle]" if talk_videos["dog"] else f"[{indexes['dog_talk']}:v]"
    filters.extend([
        f"[v_sub][{indexes['cat_idle']}:v]overlay=x=42:y=817.5:enable='{speech_window}*not({cat_speaking})'[v_cat_idle]",
        f"[v_cat_idle]{cat_talk}overlay=x=42:y='804+6*sin(t*14)':eval=frame:enable='{cat_speaking}'[v_cat_talk]",
    ])
    stage = "v_cat_talk"
    if talk_videos["cat"]:
        filters.append(
            f"[{stage}][{indexes['cat_ring']}:v]overlay=x=42:y='804+6*sin(t*14)':eval=frame:enable='{cat_speaking}'[v_cat_speaking]"
        )
        stage = "v_cat_speaking"
    filters.extend([
        f"[{stage}][{indexes['dog_idle']}:v]overlay=x=1653:y=817.5:enable='{speech_window}*not({dog_speaking})'[v_dog_idle]",
        f"[v_dog_idle]{dog_talk}overlay=x=1653:y='804+6*sin(t*16)':eval=frame:enable='{dog_speaking}'[v_dog_talk]",
    ])
    stage = "v_dog_talk"
    if talk_videos["dog"]:
        filters.append(
            f"[{stage}][{indexes['dog_ring']}:v]overlay=x=1653:y='804+6*sin(t*16)':eval=frame:enable='{dog_speaking}'[v_dog_speaking]"
        )
        stage = "v_dog_speaking"
    filters.append(f"[{stage}]null[v_out]")
    command.extend([
        "-filter_complex", ";".join(filters),
        "-map", "[v_out]", "-map", "1:a:0",
        "-c:v", "libx264", "-preset", "medium", "-crf", VIDEO_CRF,
        "-r", str(VIDEO_FPS), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-shortest", output_path,
    ])
    subprocess.run(command, check=True)


def _render_tutorial_short(tutorial):
    lines = tutorial.get("short_lines") or []
    if len(lines) < 2:
        raise RuntimeError("Tutorial short script is missing dialogue lines.")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(
            lambda item: synth_audio_segment((item[0], (item[1]["role"], item[1]["text"])), "tshort"),
            list(enumerate(lines)),
        ))
    results.sort(key=lambda item: item[0])
    with open("tutorial_short_audio.txt", "w", encoding="utf-8") as handle:
        for _, filename, _, _ in results:
            handle.write(f"file '{filename}'\n")
    _concat_media("tutorial_short_audio.txt", "tutorial_short.mp3")
    duration = float(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", "tutorial_short.mp3"]
    ).decode().strip())
    events = []
    clock = 0.0
    for _, _, spoken, segment_duration in results:
        clean = presentation.strip_bracket_tags(re.sub(r"[{}\\]", "", spoken))
        chunks = split_short_subtitle_text(clean, max_width=11, shrink_limit=11)
        total_width = sum(short_subtitle_visual_width(chunk) for chunk, _ in chunks) or 1
        segment_time = clock
        for index, (chunk, scale_percent) in enumerate(chunks):
            end = clock + segment_duration if index == len(chunks) - 1 else segment_time + segment_duration * short_subtitle_visual_width(chunk) / total_width
            text = chunk if scale_percent == 100 else f"{{\\fscx{scale_percent}\\fscy{scale_percent}}}{chunk}"
            events.append(
                f"Dialogue: 0,{fmt_ass_t(segment_time)},{fmt_ass_t(end)},Default,,110,110,0,,{text}"
            )
            segment_time = end
        clock += segment_duration
    ass = (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 2\n"
        "PlayResX: 1080\nPlayResY: 1920\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Noto Sans CJK TC,62,&H00FFFFFF,&H000000FF,&H00000000,&H96000000,1,0,0,0,100,100,0,0,1,4,0,2,110,110,420,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        + "\n".join(events)
    )
    with open("tutorial_short.ass", "w", encoding="utf-8") as handle:
        handle.write(ass)
    hook = html.escape(_fit_cjk_hook(tutorial.get("title") or "", "成本帶怎麼看"))
    badge = html.escape(presentation.tutorial_badge(tutorial.get("ep_id", "EP")))
    cards = []
    for index, line in enumerate(lines[:4]):
        phrase = presentation.fit_complete_phrase(line.get("text"), 6, 18, "先看結構")
        cards.append(
            f'<article style="animation-delay:{index * duration / 4:.2f}s">{html.escape(phrase)}</article>'
        )
    def png_data(path):
        with Image.open(path) as image:
            buffer = io.BytesIO()
            image.convert("RGBA").save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode("ascii")

    host_assets = create_speaker_avatars({
        "cat": "cat_vtober.mp4" if os.path.exists("cat_vtober.mp4") else None,
        "dog": "dor_vtober.mp4" if os.path.exists("dor_vtober.mp4") else None,
    })
    hosts = [
        f'<img class="host cat" alt="" src="data:image/png;base64,{png_data(host_assets["cat_idle"])}">',
        f'<img class="host dog" alt="" src="data:image/png;base64,{png_data(host_assets["dog_idle"])}">',
    ]
    talk_sources = {
        "女": png_data(host_assets["cat_speaking"]),
        "男": png_data(host_assets["dog_speaking"]),
    }
    clock = 0.0
    for index, (_, _, _, segment_duration) in enumerate(results):
        role = lines[index].get("role") if index < len(lines) else "男"
        side = "cat" if role == "女" else "dog"
        hosts.append(
            f'<img class="host {side} talk" alt="" src="data:image/png;base64,{talk_sources.get(role, talk_sources["男"])}" '
            f'style="animation-delay:{clock:.3f}s;animation-duration:{max(segment_duration, 0.1):.3f}s">'
        )
        clock += segment_duration
    avatars = "".join(hosts)
    short_broll_path = broll_engine.fetch_pixabay_broll(
        presentation.assign_broll_query({
            "title": tutorial.get("title", ""),
            "body": " ".join(str(line.get("text") or "") for line in lines),
            "layout": "",
        }),
        orientation=broll_engine.VERTICAL,
    )
    if not short_broll_path:
        raise RuntimeError("Tutorial Shorts could not obtain a new unique vertical Pixabay clip.")
    with open(short_broll_path, "rb") as handle:
        short_broll_data = base64.b64encode(handle.read()).decode("ascii")
    short_broll_markup = (
        '<video class="tutorial-broll" autoplay muted loop playsinline '
        f'src="data:video/mp4;base64,{short_broll_data}"></video>'
    )
    short_html = f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><style>
{presentation.GLOBAL_DARK_CSS}
html,body {{ margin:0; width:1080px; height:1920px; overflow:hidden; color:#f8fafc; font-family:'Noto Sans CJK TC',sans-serif; }}
body {{ background:#070b14; }}
body::before {{ content:""; position:absolute; inset:-8%;
  background:radial-gradient(circle at 50% 30%,rgba(56,189,248,.25),transparent 42%),
  linear-gradient(rgba(56,189,248,.08) 1px,transparent 1px),linear-gradient(90deg,rgba(56,189,248,.08) 1px,transparent 1px);
  background-size:auto,48px 48px,48px 48px; animation:flow 14s linear infinite; }}
@keyframes flow {{ to {{ transform:translate3d(48px,24px,0); }} }}
.tutorial-broll {{ position:absolute; right:36px; top:450px; width:390px; height:1120px;
  object-fit:cover; border:2px solid rgba(56,189,248,.72); border-radius:24px; }}
.badge {{ position:absolute; top:70px; left:50px; right:50px; color:#f59e0b; font-size:40px; font-weight:800; }}
h1 {{ position:absolute; top:160px; left:50px; right:480px; font-size:68px; line-height:1.2; }}
.deck {{ position:absolute; left:50px; right:480px; top:460px; height:900px; }}
article {{ position:absolute; inset:0; display:grid; place-items:center; padding:48px; border-radius:36px;
  background:rgba(15,23,42,.92); border:4px solid #f59e0b; font-size:64px; font-weight:800; text-align:center;
  opacity:0; animation:card {duration:.2f}s linear forwards; }}
@keyframes card {{ 4%,22% {{ opacity:1; transform:scale(1); }} 0%,28%,100% {{ opacity:0; transform:scale(.94); }} }}
.host {{ position:absolute; bottom:90px; width:240px; height:240px; border-radius:50%; object-fit:cover; background:#111827; }}
.host.cat {{ left:150px; }}
.host.dog {{ right:150px; }}
.host.talk {{ opacity:0; animation:speak linear 1; }}
@keyframes speak {{ 0%,100% {{ opacity:1; }} }}
</style></head><body>{short_broll_markup}<div class="badge">{badge}</div><h1>{hook}</h1><div class="deck">{''.join(cards)}</div>{avatars}</body></html>"""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            with tempfile.TemporaryDirectory(prefix="tshort_") as video_dir:
                context = browser.new_context(
                    viewport={"width": 1080, "height": 1920},
                    record_video_dir=video_dir,
                    record_video_size={"width": 1080, "height": 1920},
                )
                page = context.new_page()
                video = page.video
                try:
                    page.set_content(short_html, wait_until="load")
                    page.wait_for_timeout(max(1, round(duration * 1000)))
                finally:
                    context.close()
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-i", video.path(),
                        "-vf", f"fps={VIDEO_FPS},scale=1080:1920:flags=lanczos,format=yuv420p",
                        "-t", f"{duration:.6f}", "-an", "-r", str(VIDEO_FPS),
                        "-c:v", "libx264", "-preset", "medium", "-crf", VIDEO_CRF,
                        "-pix_fmt", "yuv420p", "tutorial_short_visual.mp4",
                    ],
                    check=True,
                )
        finally:
            browser.close()
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", "tutorial_short_visual.mp4", "-i", "tutorial_short.mp3",
            "-vf", "ass=tutorial_short.ass",
            "-c:v", "libx264", "-preset", "medium", "-crf", VIDEO_CRF,
            "-r", str(VIDEO_FPS), "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-shortest", "tutorial_short.mp4",
        ],
        check=True,
    )
    return "tutorial_short.mp4", duration


def _write_broadcast_ass(path, timed_lines, start_time):
    """66pt 底部字幕，左右讓開頭像，與每日晨報同一套安全區。"""
    cc = OpenCC("s2twp")
    events = []
    clock = start_time
    for spoken, duration in timed_lines:
        text = presentation.strip_bracket_tags(presentation.display_text(spoken))
        segments = [
            part.strip()
            for part in re.split(r"[，。！？、；：…!?;\n]+", cc.convert(text))
            if part.strip()
        ]
        pieces = []
        for segment in segments:
            if len(segment) > 24:
                pieces.extend(presentation.split_keeping_numbers(segment, 20))
            else:
                pieces.append(segment)
        pieces = [re.sub(r"\s+", " ", piece).strip() for piece in pieces if piece.strip()]
        total_chars = sum(len(piece) for piece in pieces)
        if total_chars == 0 or duration <= 0:
            clock += max(duration, 0)
            continue
        segment_cur = clock
        for index, piece in enumerate(pieces):
            sub_txt = re.sub(r"\s+", " ", re.sub(punct_pat, " ", piece)).strip()
            sub_txt = presentation.strip_bracket_tags(sub_txt)
            if not sub_txt:
                continue
            sub_end = clock + duration if index == len(pieces) - 1 else segment_cur + duration * (len(piece) / total_chars)
            highlighted = re.sub(kw_pat, r"{\\b1\\c&H0024BFFF&}\1{\\b0\\c&H00FFFFFF&}", sub_txt)
            events.append(
                f"Dialogue: 0,{fmt_ass_t(segment_cur)},{fmt_ass_t(sub_end)},Default,,315,315,0,,{highlighted}"
            )
            segment_cur = sub_end
        clock += duration
    header = (
        f"[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {MAIN_VIDEO_WIDTH}\nPlayResY: {MAIN_VIDEO_HEIGHT}\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Noto Sans CJK TC,66,&H00FFFFFF,&H000000FF,&H00000000,"
        "&H99000000,0,0,0,0,100,100,0,0,3,3,0,2,315,315,60,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text\n"
    )
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(header + "\n".join(events))


def render_tutorial_episode(tutorial, chart_path, intro_seconds=15, outro_seconds=12, outro_pause=1.5):
    """教學長片：15 秒片頭、25 到 35 張快切圖卡、雙頭像、1.5 秒停頓與 12 秒片尾。"""
    sections = tutorial.get("sections") or []
    if len(sections) != 5:
        raise RuntimeError("Tutorial render requires 5 sections.")
    cards = tutorial.get("cards") or presentation.build_tutorial_cards(sections, tutorial)
    if not cards:
        raise RuntimeError("Tutorial script has no spoken lines.")
    print(f"教學分鏡 {len(cards)} 張。")
    flat = []
    for card in cards:
        for line in card.get("lines") or []:
            flat.append((line.get("role") or "男", line.get("text") or ""))
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(
            lambda item: synth_audio_segment((item[0], item[1]), "tseg"),
            list(enumerate(flat)),
        ))
    results.sort(key=lambda item: item[0])
    intro_dur, has_intro = _tutorial_intro_and_outro(intro_seconds, outro_seconds, outro_pause)
    cursor = intro_dur
    speaker_intervals = {"F": [], "M": []}
    card_starts = []
    line_cursor = 0
    card_durations = []
    for card in cards:
        card_starts.append(cursor)
        card_duration = 0.0
        for line in card.get("lines") or []:
            duration = results[line_cursor][3]
            speaker = "F" if line.get("role") == "女" else "M"
            speaker_intervals[speaker].append((cursor, cursor + duration))
            cursor += duration
            card_duration += duration
            line_cursor += 1
        card_durations.append(max(1.0, card_duration))
    speech_end = cursor
    with open("tutorial_audio.txt", "w", encoding="utf-8") as handle:
        if has_intro:
            handle.write("file 'intro_cut.mp3'\n")
        for _, filename, _, _ in results:
            handle.write(f"file '{filename}'\n")
        handle.write("file 'outro_pause.mp3'\n")
        handle.write("file 'outro_music.mp3'\n")
    _concat_media("tutorial_audio.txt", "tutorial.mp3")
    mix_voice_with_bed("tutorial.mp3", "tutorial_bed.mp3", card_starts)
    chart_markup = ""
    if chart_path and os.path.exists(chart_path):
        with open(chart_path, "rb") as chart_file:
            encoded = base64.b64encode(chart_file.read()).decode("ascii")
        chart_markup = f'<img alt="" src="data:image/png;base64,{encoded}">'
    broll_engine.reset_episode_rotation()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            if not broll_engine._api_key():
                raise RuntimeError("Tutorial video requires PIXABAY_API_KEY.")
            for index, card in enumerate(cards, 1):
                markup = chart_markup if card.get("layout") == "chart" else ""
                query = presentation.assign_broll_query({
                    "title": card.get("title", ""),
                    "body": " ".join(str(card.get(key) or "") for key in ("headline", "myth", "truth")),
                    "layout": "",
                })
                broll_path = broll_engine.fetch_pixabay_broll(query, broll_engine.HORIZONTAL)
                if not broll_path:
                    raise RuntimeError(
                        f"Tutorial card {index} has no new unique Pixabay footage; stopping to prevent reuse."
                    )
                _record_tutorial_card(
                    browser, index, card, card_durations[index - 1], markup, broll_path
                )
        finally:
            browser.close()
    with open("tutorial_video.txt", "w", encoding="utf-8") as handle:
        if has_intro:
            handle.write("file 'intro.mp4'\n")
        for index in range(1, len(cards) + 1):
            handle.write(f"file 'tutorial_p{index}.mp4'\n")
        handle.write("file 'outro.mp4'\n")
    _concat_media("tutorial_video.txt", "tutorial_visual.mp4")
    _write_broadcast_ass(
        "tutorial_main.ass",
        [(spoken, duration) for _, _, spoken, duration in results],
        intro_dur,
    )
    _mux_tutorial_avatars(
        "tutorial_visual.mp4", "tutorial_bed.mp3", "tutorial_main.mp4",
        speaker_intervals, intro_dur, speech_end,
    )
    short_path, short_duration = _render_tutorial_short(tutorial)
    thumb_path = _write_tutorial_thumb(tutorial)
    chapters = [f"00:00 片頭"] if has_intro else []
    for start, card in zip(card_starts, cards):
        chapters.append(f"{int(start) // 60:02d}:{int(start) % 60:02d} {card['title']}")
    return {
        "output_path": "tutorial_main.mp4",
        "short_path": short_path,
        "thumb_path": thumb_path,
        "chapters": chapters,
        "short_duration": short_duration,
    }

