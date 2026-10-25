#!/usr/bin/env python3
"""
Автомонтаж разговорного вертикального видео (Reels / Shorts / TikTok).

Шаги (как в гайде):
  prepare    — рабочая копия: H.264, постоянная частота кадров
  transcribe — faster-whisper, время начала/конца каждого слова
  plan       — паузы (по громкости), слова-паразиты, авто-акценты -> out/plan.json
  render     — склейка, наезды, субтитры, акценты, звук -14 LUFS, музыка -20 дБ
  check      — кадры с каждой склейки и надписи + отчёт out/report.md
  auto       — всё подряд

Между plan и render нейросеть (или ты) правит out/plan.json:
исправляет слова, добавляет вырезки дублей ("reason": "duplicate") и акценты.

Пример:
  python montage.py auto ~/videos/reel1/video.mp4
  # правим out/plan.json
  python montage.py render --out ~/videos/reel1/out
  python montage.py check  --out ~/videos/reel1/out
"""
import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".m4v", ".webm", ".avi"}

# Безопасная зона Instagram Reels: сверху 12 %, снизу 20 %, справа 12 %
SAFE_TOP, SAFE_BOTTOM, SAFE_RIGHT, SAFE_LEFT = 0.12, 0.20, 0.12, 0.05

FILLERS = {
    "э", "ээ", "эээ", "эм", "эмм", "ээм", "м", "мм", "ммм", "хм", "а-а", "э-э",
    "uh", "um", "umm", "uhm", "hmm", "erm",
}

DEFAULT_STYLE = {
    "font": None,               # None -> подберётся автоматически
    "uppercase": True,
    "sub_size": 0.075,          # доля ширины кадра
    "sub_max_words": 3,
    "sub_max_chars": 16,
    "sub_y": 0.70,              # низ строки субтитров (доля высоты), выше нижних 20 %
    "text_color": "#FFFFFF",
    "highlight_color": "#FFD400",
    "box_color": "#000000",
    "box_opacity": 0.75,
    "accent_size": 0.13,
    "accent_y": 0.44,
    "accent_color": "#FFD400",
    "punch_zoom": 0.08,         # приближение на склейке (7–10 %)
    "push_zoom": 0.06,          # плавный наезд
    "push_min_gap": 6.0,        # наезд не чаще раза в 6 секунд
}


# ---------------------------------------------------------------- утилиты

def run(cmd, cwd=None, capture=False):
    printable = " ".join(str(c) for c in cmd)
    print("  $ " + (printable if len(printable) < 220 else printable[:220] + " ..."))
    res = subprocess.run([str(c) for c in cmd], cwd=cwd, text=True,
                         capture_output=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        sys.stderr.write(res.stderr[-4000:])
        raise SystemExit(f"Команда упала: {cmd[0]} (код {res.returncode})")
    return res.stderr if capture else None


def need(tool):
    if not shutil.which(tool):
        raise SystemExit(f"Не найден {tool}. Запусти установку: video-editor/setup/install.sh "
                         f"(или install.ps1 на Windows)")


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
        capture_output=True, text=True, check=True).stdout
    info = json.loads(out)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    fps = None
    if v:
        num, den = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "30/1").split("/")
        fps = float(num) / float(den) if float(den) else 30.0
        rot = 0
        for sd in v.get("side_data_list", []) or []:
            rot = int(sd.get("rotation", 0) or 0)
        rot = rot or int((v.get("tags") or {}).get("rotate", 0) or 0)
        w, h = int(v["width"]), int(v["height"])
        if abs(rot) in (90, 270):
            w, h = h, w
    return {
        "duration": float(info["format"]["duration"]),
        "width": w if v else None, "height": h if v else None,
        "fps": fps, "has_audio": a is not None,
    }


def ts(t):
    m, s = divmod(max(t, 0), 60)
    return f"{int(m)}:{s:05.2f}"


def ass_time(t):
    t = max(t, 0)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    cs = int(round((s - int(s)) * 100))
    s = int(s)
    if cs == 100:
        s, cs = s + 1, 0
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_color(hex_rgb, opacity=1.0):
    hex_rgb = hex_rgb.lstrip("#")
    r, g, b = hex_rgb[0:2], hex_rgb[2:4], hex_rgb[4:6]
    alpha = int(round((1 - opacity) * 255))
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def ass_escape(text):
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


def norm_word(w):
    return re.sub(r"[^\w-]", "", w.lower()).strip("-")


def pick_font():
    candidates = ["Montserrat ExtraBold", "Montserrat Black", "Montserrat", "Inter ExtraBold",
                  "Inter Display Black", "Inter", "Arial Black", "Arial", "DejaVu Sans"]
    if shutil.which("fc-list"):
        families = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True).stdout
        names = {n.strip() for line in families.splitlines() for n in line.split(",")}
        for c in candidates:
            if c in names:
                return c
    return "Arial Black"  # есть на Windows и macOS, поддерживает кириллицу


def load_plan(out):
    p = Path(out) / "plan.json"
    if not p.exists():
        raise SystemExit(f"Нет {p}. Сначала: python montage.py plan --out {out}")
    return json.loads(p.read_text(encoding="utf-8"))


def save_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- 1. prepare

def cmd_prepare(src, out, vertical=None):
    need("ffmpeg")
    src, out = Path(src).resolve(), Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    info = probe(src)
    if not info["has_audio"]:
        raise SystemExit("В видео нет звуковой дорожки — монтировать по речи нечего.")
    fps = info["fps"] or 30
    fps = round(fps) if abs(fps - round(fps)) < 0.1 else 30
    w, h = info["width"], info["height"]
    is_vertical = h > w
    if vertical is None:
        vertical = is_vertical
    if vertical:
        # 9:16, 1080×1920: горизонтальное видео обрезаем по центру
        vf = "scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,crop=1080:1920,setsar=1"
        w, h = 1080, 1920
    else:
        w, h = w // 2 * 2, h // 2 * 2
        vf = f"scale={w}:{h}:flags=lanczos,setsar=1"
    work = out / "work.mp4"
    print(f"[prepare] {src.name}: {info['width']}×{info['height']} {info['fps']:.2f} fps, "
          f"{ts(info['duration'])} -> {w}×{h} {fps} fps CFR")
    run(["ffmpeg", "-y", "-v", "error", "-i", src, "-vf", vf, "-r", fps, "-fps_mode", "cfr",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-ac", "2", work])
    meta = {"original": str(src), "source": "work.mp4", "width": w, "height": h, "fps": fps,
            "duration": probe(work)["duration"], "original_duration": info["duration"]}
    save_json(out / "meta.json", meta)
    return meta


# ---------------------------------------------------------------- 2. transcribe

def cmd_transcribe(out, model="small", lang=None, prompt=None):
    out = Path(out).resolve()
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise SystemExit("Не установлен faster-whisper: pip install faster-whisper")
    print(f"[transcribe] модель {model} (первый запуск скачивает её с huggingface.co)")
    try:
        wm = WhisperModel(model, device="auto", compute_type="int8")
    except Exception as e:
        raise SystemExit(f"Не удалось загрузить модель Whisper «{model}»: {e}\n"
                         "Нужен доступ к huggingface.co или путь к скачанной модели: --model /путь/к/модели")
    segments, info = wm.transcribe(str(out / "work.mp4"), language=lang, word_timestamps=True,
                                   initial_prompt=prompt, beam_size=5, vad_filter=False,
                                   condition_on_previous_text=False)
    words, lines = [], []
    for seg in segments:
        lines.append(f"[{ts(seg.start)} – {ts(seg.end)}] {seg.text.strip()}")
        for w in seg.words or []:
            if w.word.strip():
                words.append({"word": w.word.strip(), "start": round(w.start, 3),
                              "end": round(w.end, 3), "prob": round(w.probability, 2)})
        print("  " + lines[-1])
    save_json(out / "words.json", words)
    (out / "transcript.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[transcribe] язык: {info.language}, слов: {len(words)} -> words.json, transcript.txt")
    return words


# ---------------------------------------------------------------- 3. plan

def detect_silences(path, noise_db=-35, min_dur=0.4):
    err = run(["ffmpeg", "-v", "info", "-nostats", "-i", path, "-vn", "-af",
               f"silencedetect=noise={noise_db}dB:d={min_dur}", "-f", "null", "-"], capture=True)
    sil, start = [], None
    for line in err.splitlines():
        m = re.search(r"silence_start: (-?[\d.]+)", line)
        if m:
            start = max(float(m.group(1)), 0.0)
        m = re.search(r"silence_end: ([\d.]+)", line)
        if m and start is not None:
            sil.append((start, float(m.group(1))))
            start = None
    return sil, start  # start != None -> тишина до самого конца


def merge(intervals):
    res = []
    for a, b, *rest in sorted(intervals, key=lambda x: x[0]):
        if res and a <= res[-1][1] + 1e-3:
            res[-1][1] = max(res[-1][1], b)
        else:
            res.append([a, b] + rest)
    return res


def cmd_plan(out, noise_db=-35, min_pause=0.4, pre=0.10, post=0.15, music=None):
    out = Path(out).resolve()
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    dur = meta["duration"]
    words_path = out / "words.json"
    words = json.loads(words_path.read_text(encoding="utf-8")) if words_path.exists() else []
    print(f"[plan] ищу паузы > {min_pause} с по громкости (порог {noise_db} dB)")
    sil, tail = detect_silences(out / meta["source"], noise_db, min_pause)
    cuts = []
    for a, b in sil:
        ca = 0.0 if a <= 0.01 else a + post
        cb = b - pre
        if cb - ca >= 0.05:
            cuts.append({"start": round(ca, 3), "end": round(cb, 3),
                         "reason": "start" if ca == 0 else "pause"})
    if tail is not None and dur - (tail + post) > 0.05:
        cuts.append({"start": round(tail + post, 3), "end": round(dur, 3), "reason": "end"})

    for k, w in enumerate(words):
        if norm_word(w["word"]) in FILLERS:
            lo = words[k - 1]["end"] if k > 0 else 0.0              # не задеваем соседние слова
            hi = words[k + 1]["start"] if k + 1 < len(words) else dur
            cuts.append({"start": round(max(w["start"] - 0.02, lo), 3), "end": round(min(w["end"] + 0.02, hi), 3),
                         "reason": "filler", "text": w["word"]})
    speech = [w for w in words if norm_word(w["word"]) not in FILLERS]
    if speech:  # ролик начинается сразу со слова
        if speech[0]["start"] - pre > 0.05:
            cuts.append({"start": 0.0, "end": round(speech[0]["start"] - pre, 3), "reason": "start"})
        if dur - (speech[-1]["end"] + post) > 0.05:
            cuts.append({"start": round(speech[-1]["end"] + post, 3), "end": round(dur, 3), "reason": "end"})

    # авто-акценты: числа из речи (нейросеть потом выбирает 3–5 главных)
    accents = []
    for w in speech:
        if re.search(r"\d", w["word"]) and (not accents or w["start"] - accents[-1]["start"] > 4):
            accents.append({"start": w["start"], "end": round(w["start"] + 1.5, 3),
                            "text": re.sub(r"[^\w%$€₽+.,×x-]", "", w["word"]).strip(".,"), "auto": True})

    if music is None:
        src_dir = Path(meta["original"]).parent
        found = sorted(p for p in src_dir.iterdir() if p.suffix.lower() in AUDIO_EXT)
        music = str(found[0]) if found else None

    plan = {
        "_help": ("cuts — что вырезать (секунды исходника). Добавь {start,end,reason:'duplicate'} "
                  "для дублей и оговорок. words — исправь текст, если Whisper ошибся. "
                  "accents — 3–5 крупных надписей {start,end,text}. После правок: montage.py render"),
        "source": meta["source"], "duration": dur, "width": meta["width"], "height": meta["height"],
        "fps": meta["fps"], "music": music, "style": dict(DEFAULT_STYLE, font=pick_font()),
        "cuts": sorted(cuts, key=lambda c: c["start"]), "words": words, "accents": accents[:5],
    }
    save_json(out / "plan.json", plan)
    keeps = keep_segments(plan)
    kept = sum(b - a for a, b in keeps)
    print(f"[plan] вырезок: {len(cuts)}, останется {ts(kept)} из {ts(dur)}; "
          f"акцентов-кандидатов: {len(plan['accents'])}; музыка: {music or 'нет'} -> plan.json")
    return plan


def keep_segments(plan):
    fps, dur = plan["fps"], plan["duration"]
    cuts = merge([[c["start"], c["end"]] for c in plan["cuts"]])
    keeps, t = [], 0.0
    for a, b in cuts:
        if a > t:
            keeps.append([t, a])
        t = max(t, b)
    if t < dur:
        keeps.append([t, dur])
    snapped = []
    for a, b in keeps:  # по сетке кадров, чтобы склейка была точной
        a, b = round(a * fps) / fps, min(round(b * fps) / fps, math.floor(dur * fps) / fps)
        if b - a >= 3 / fps:
            snapped.append((a, b))
    return snapped


# ---------------------------------------------------------------- 4. render

class Timeline:
    def __init__(self, keeps):
        self.items, off = [], 0.0
        for a, b in keeps:
            self.items.append((a, b, off))
            off += b - a
        self.duration = off

    def seg_of(self, t):
        for i, (a, b, _) in enumerate(self.items):
            if a - 1e-6 <= t <= b + 1e-6:
                return i
        return None

    def map(self, t, i=None):
        i = self.seg_of(t) if i is None else i
        if i is None:
            return None
        a, b, off = self.items[i]
        return off + min(max(t, a), b) - a


def subtitle_groups(words, tl, st):
    kept = []
    for w in words:
        if norm_word(w["word"]) in FILLERS:
            continue
        i = tl.seg_of((w["start"] + w["end"]) / 2)
        if i is None:
            continue
        kept.append(dict(w, seg=i, os=tl.map(w["start"], i), oe=tl.map(w["end"], i)))
    groups, cur = [], []
    for w in kept:
        text = " ".join(x["word"] for x in cur + [w])
        if cur and (len(cur) >= st["sub_max_words"] or len(text) > st["sub_max_chars"]
                    or w["seg"] != cur[-1]["seg"]                      # не через склейку
                    or re.search(r"[.!?…,:;]$", cur[-1]["word"])
                    or w["os"] - cur[-1]["oe"] > 0.5):
            groups.append(cur)
            cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    out = []
    for k, g in enumerate(groups):
        seg_end = tl.items[g[-1]["seg"]][2] + tl.items[g[-1]["seg"]][1] - tl.items[g[-1]["seg"]][0]
        end = g[-1]["oe"] + 0.15
        if k + 1 < len(groups):
            nxt = groups[k + 1][0]["os"]
            end = nxt if nxt - g[-1]["oe"] < 0.4 else end
            end = min(end, nxt)
        out.append({"start": g[0]["os"], "end": min(end, seg_end), "words": [w["word"] for w in g]})
    return out


def main_word_index(words):
    clean = [re.sub(r"[^\w%]", "", w) for w in words]
    for i, w in enumerate(clean):
        if re.search(r"\d", w):
            return i
    best = max(range(len(clean)), key=lambda i: len(clean[i]))
    return best if len(clean[best]) >= 5 else None


def build_ass(plan, tl, path):
    st, W, H = plan["style"], plan["width"], plan["height"]
    safe_w = W * (1 - SAFE_LEFT - SAFE_RIGHT)
    font = st.get("font") or pick_font()
    sub_fs = round(W * st["sub_size"])
    acc_fs = round(W * st["accent_size"])
    box = ass_color(st["box_color"], st["box_opacity"])
    pad = round(sub_fs * 0.18)
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,{font},{sub_fs},{ass_color(st['text_color'])},&H000000FF,{box},{box},-1,0,0,0,100,100,0,0,3,{pad},0,2,0,0,0,1
Style: Accent,{font},{acc_fs},{ass_color(st['accent_color'])},&H000000FF,{box},{box},-1,0,0,0,100,100,0,0,3,{round(acc_fs*0.2)},0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    cx = W * (SAFE_LEFT + (1 - SAFE_LEFT - SAFE_RIGHT) / 2)
    hl = ass_color(st["highlight_color"])
    events, report = [], []
    for g in subtitle_groups(plan["words"], tl, st):
        words = [re.sub(r"[.,;:…]+$", "", w) or w for w in g["words"]]   # точки и запятые на экране не нужны
        words = [w.upper() if st["uppercase"] else w for w in words]
        mi = main_word_index(words)
        text = " ".join(words)
        # подгоняем размер, чтобы строка точно влезла в безопасную ширину
        fs = min(sub_fs, int(safe_w * 0.92 / (0.62 * max(len(text), 1))))
        parts = [f"{{\\c{hl}}}{ass_escape(w)}{{\\r}}" if i == mi else ass_escape(w) for i, w in enumerate(words)]
        tag = (f"{{\\an2\\pos({cx:.0f},{H * st['sub_y']:.0f})\\fs{fs}"
               f"\\fscx88\\fscy88\\t(0,90,\\fscx100\\fscy100)}}")
        events.append(f"Dialogue: 1,{ass_time(g['start'])},{ass_time(g['end'])},Sub,,0,0,0,,{tag}{' '.join(parts)}")
    for acc in plan.get("accents", []):
        i = tl.seg_of(acc["start"])
        if i is None:
            report.append(f"акцент «{acc['text']}» попал в вырезанный кусок — пропущен")
            continue
        s = tl.map(acc["start"], i)
        e = s + max(acc["end"] - acc["start"], 0.8)
        text = acc["text"].upper() if st["uppercase"] else acc["text"]
        fs = min(acc_fs, int(safe_w * 0.92 / (0.62 * max(len(text), 1))))
        tag = (f"{{\\an5\\pos({cx:.0f},{H * st['accent_y']:.0f})\\fs{fs}\\fscx60\\fscy60"
               f"\\t(0,120,\\fscx108\\fscy108)\\t(120,200,\\fscx100\\fscy100)\\fad(0,120)}}")
        events.append(f"Dialogue: 2,{ass_time(s)},{ass_time(e)},Accent,,0,0,0,,{tag}{ass_escape(text)}")
        acc["out_start"] = round(s, 3)
    Path(path).write_text(head + "\n".join(events) + "\n", encoding="utf-8")
    return report


def zoom_plan(plan, tl):
    st = plan["style"]
    accent_out = sorted(tl.map(a["start"]) for a in plan.get("accents", []) if tl.seg_of(a["start"]) is not None)
    zooms, last_push = [], -1e9
    for i, (a, b, off) in enumerate(tl.items):
        d = b - a
        base = 1.0 + (st["punch_zoom"] if i % 2 == 1 else 0.0)   # джамп-кат прикрываем приближением
        push = 0.0
        has_accent = any(off <= t < off + d for t in accent_out)
        if (i == 0 or has_accent) and off - last_push >= st["push_min_gap"] and d >= 1.0:
            push, last_push = st["push_zoom"], off               # плавный наезд (и движение в первые 3 с)
        zooms.append((base, push, d))
    return zooms


def zoom_filter(base, push, d, W, H):
    if push <= 0 and base == 1.0:
        return "null"
    if push <= 0:
        z = f"{base}"
        return (f"scale=w='trunc(iw*{z}/2)*2':h='trunc(ih*{z}/2)*2':flags=lanczos,crop={W}:{H}")
    ramp = min(d, 4.0)
    z = f"({base}+{push}*min(t/{ramp:.3f},1))"
    return (f"scale=w='trunc(iw*{z}/2)*2':h='trunc(ih*{z}/2)*2':eval=frame:flags=bicubic,"
            f"crop={W}:{H}")


def measure_loudness(path, flt=""):
    err = run(["ffmpeg", "-v", "info", "-nostats", "-i", path, "-af",
               (flt + "," if flt else "") + "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json",
               "-f", "null", "-"], capture=True)
    return json.loads(err[err.rindex("{"):err.rindex("}") + 1])


def cmd_render(out):
    need("ffmpeg")
    out = Path(out).resolve()
    plan = load_plan(out)
    W, H, fps = plan["width"], plan["height"], plan["fps"]
    tl = Timeline(keep_segments(plan))
    if not tl.items:
        raise SystemExit("После вырезок ничего не осталось — проверь cuts в plan.json")
    tmp = out / "tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    src = out / plan["source"]
    zooms = zoom_plan(plan, tl)
    print(f"[render] кусков: {len(tl.items)}, итог {ts(tl.duration)}")
    vlist, alist = [], []
    for i, ((a, b, _), (base, push, d)) in enumerate(zip(tl.items, zooms)):
        frames = round(d * fps)
        v, au = tmp / f"v{i:04d}.mp4", tmp / f"a{i:04d}.wav"
        run(["ffmpeg", "-y", "-v", "error", "-ss", f"{a:.4f}", "-i", src, "-t", f"{d:.4f}",
             "-vf", zoom_filter(base, push, d, W, H), "-frames:v", frames, "-r", fps, "-an",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "15", "-pix_fmt", "yuv420p", v])
        fade = min(0.012, d / 4)
        run(["ffmpeg", "-y", "-v", "error", "-ss", f"{a:.4f}", "-i", src, "-t", f"{frames / fps:.4f}", "-vn",
             "-af", f"afade=t=in:d={fade},afade=t=out:st={frames / fps - fade:.4f}:d={fade}",
             "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", au])
        vlist.append(f"file '{v.name}'")
        alist.append(f"file '{au.name}'")
    (tmp / "v.txt").write_text("\n".join(vlist) + "\n", encoding="utf-8")
    (tmp / "a.txt").write_text("\n".join(alist) + "\n", encoding="utf-8")
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", "v.txt", "-c", "copy", "video.mp4"], cwd=tmp)
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", "a.txt", "-c", "copy", "voice.wav"], cwd=tmp)

    notes = build_ass(plan, tl, out / "subs.ass")

    # Звук: голос -14 LUFS (два прохода), музыка на 20 дБ тише
    m = measure_loudness(tmp / "voice.wav")
    voice_f = (f"loudnorm=I=-14:TP=-1.5:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
               f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:"
               f"linear=true,aresample=48000")
    music = plan.get("music")
    inputs = ["-i", tmp / "video.mp4", "-i", tmp / "voice.wav"]
    if music and Path(music).exists():
        inputs += ["-stream_loop", "-1", "-i", music]
        fo = max(tl.duration - 1.5, 0)
        gain = -34 - float(measure_loudness(music)["input_i"])   # голос -14, музыка -34 LUFS
        afilter = (f"[1:a]{voice_f}[vo];[2:a]volume={gain:.2f}dB,aresample=48000,"
                   f"atrim=0:{tl.duration:.3f},afade=t=out:st={fo:.3f}:d=1.5[mu];"
                   f"[vo][mu]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.89:level=0[a]")
    else:
        afilter = f"[1:a]{voice_f}[a]"
        music = None
    run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex",
         f"[0:v]subtitles=subs.ass[v];{afilter}", "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", fps,
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "final.mp4"], cwd=out)
    final_lufs = measure_loudness(out / "final.mp4")["input_i"]
    shutil.rmtree(tmp, ignore_errors=True)

    render_info = {"keeps": [[round(a, 3), round(b, 3), round(o, 3)] for a, b, o in tl.items],
                   "zooms": zooms, "duration": tl.duration, "lufs": final_lufs, "music": music,
                   "notes": notes, "accents": plan.get("accents", [])}
    save_json(out / "render.json", render_info)
    print(f"[render] готово: {out / 'final.mp4'} ({ts(tl.duration)}, {final_lufs} LUFS)")
    return render_info


# ---------------------------------------------------------------- 5. check

def cmd_check(out):
    out = Path(out).resolve()
    plan, ri = load_plan(out), json.loads((out / "render.json").read_text(encoding="utf-8"))
    W, H = plan["width"], plan["height"]
    chk = out / "check"
    shutil.rmtree(chk, ignore_errors=True)
    chk.mkdir()
    shots = [(0.5, "start")]
    shots += [(o + 0.12, f"cut{i:02d}") for i, (_, _, o) in enumerate(ri["keeps"]) if i > 0]
    shots += [(a["out_start"] + 0.4, f"accent_{k}") for k, a in enumerate(ri["accents"]) if "out_start" in a]
    shots = [(min(t, ri["duration"] - 0.05), n) for t, n in shots]
    # красным — зоны, куда Instagram кладёт свои кнопки
    zones = (f"drawbox=x=0:y=0:w=iw:h=ih*{SAFE_TOP}:color=red@0.25:t=fill,"
             f"drawbox=x=0:y=ih*{1 - SAFE_BOTTOM}:w=iw:h=ih*{SAFE_BOTTOM}:color=red@0.25:t=fill,"
             f"drawbox=x=iw*{1 - SAFE_RIGHT}:y=0:w=iw*{SAFE_RIGHT}:h=ih:color=red@0.25:t=fill,scale=360:-2")
    for t, name in shots:
        run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", out / "final.mp4", "-frames:v", 1,
             "-vf", zones, chk / f"{t:06.2f}_{name}.jpg"])
    sheet = None
    try:
        from PIL import Image
        imgs = [Image.open(p) for p in sorted(chk.glob("*.jpg"))]
        cols = 6
        w, h = imgs[0].size
        rows = math.ceil(len(imgs) / cols)
        canvas = Image.new("RGB", (cols * w, rows * h), "white")
        for k, im in enumerate(imgs):
            canvas.paste(im, ((k % cols) * w, (k // cols) * h))
        sheet = out / "check_sheet.jpg"
        canvas.save(sheet, quality=85)
    except Exception:
        pass

    by = {}
    for c in plan["cuts"]:
        by.setdefault(c["reason"], []).append(c)
    issues = []
    tl = Timeline([(a, b) for a, b, _ in ri["keeps"]])
    for w in plan["words"]:  # склейка не должна резать слово
        if norm_word(w["word"]) in FILLERS:
            continue
        if w["end"] - w["start"] < 0.15:
            continue
        sa, sb = tl.seg_of(w["start"] + 0.06), tl.seg_of(w["end"] - 0.06)
        if (sa is None) != (sb is None) or (sa is not None and sa != sb):
            issues.append(f"склейка задевает слово «{w['word']}» ({ts(w['start'])})")
    lines = [
        "# Отчёт монтажа", "",
        f"- Длительность: **{ts(plan['duration'])} → {ts(ri['duration'])}** "
        f"(минус {plan['duration'] - ri['duration']:.1f} с)",
        f"- Громкость: {ri['lufs']} LUFS (цель −14)",
        f"- Музыка: {Path(ri['music']).name if ri['music'] else 'нет'}",
        f"- Склеек: {len(ri['keeps']) - 1}", "", "## Что вырезано",
    ]
    names = {"pause": "паузы", "filler": "слова-паразиты", "duplicate": "дубли/оговорки",
             "start": "начало до первого слова", "end": "хвост после последнего слова"}
    for r, cs in by.items():
        total = sum(c["end"] - c["start"] for c in cs)
        extra = ""
        if r in ("filler", "duplicate"):
            extra = ": " + "; ".join(f"{ts(c['start'])} «{c.get('text', '')}»" for c in cs)
        lines.append(f"- {names.get(r, r)} — {len(cs)} шт., {total:.1f} с{extra}")
    lines += ["", "## Акценты"]
    lines += [f"- {ts(a.get('out_start', 0))} «{a['text']}»" for a in ri["accents"]] or ["- нет"]
    lines += ["", "## Что проверить глазами"]
    lines += [f"- ⚠️ {x}" for x in issues + ri.get("notes", [])]
    lines += ["- Кадры склеек и надписей: папка `check/`"
              + (" и `check_sheet.jpg`" if sheet else "")
              + " (красное — зоны кнопок Instagram, текста там быть не должно)",
              "- Не закрывают ли субтитры и надписи лицо", "- Нет ли резких скачков звука на склейках"]
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return issues


# ---------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_out(p):
        p.add_argument("--out", default="out", help="папка результатов (по умолчанию ./out)")

    p = sub.add_parser("auto", help="весь монтаж одной командой")
    p.add_argument("video", nargs="?", help="видео; по умолчанию — первое видео в текущей папке")
    p.add_argument("--out")
    p.add_argument("--model", default="small")
    p.add_argument("--lang", default=None, help="ru, en ... (по умолчанию — автоопределение)")
    p.add_argument("--prompt", default=None, help="подсказка для Whisper: имена, термины")
    p.add_argument("--music", default=None)
    p.add_argument("--horizontal", action="store_true", help="не переводить в 9:16")

    p = sub.add_parser("prepare"); p.add_argument("video"); add_out(p)
    p.add_argument("--horizontal", action="store_true")
    p = sub.add_parser("transcribe"); add_out(p)
    p.add_argument("--model", default="small"); p.add_argument("--lang"); p.add_argument("--prompt")
    p = sub.add_parser("plan"); add_out(p); p.add_argument("--music")
    p.add_argument("--noise", type=float, default=-35, help="порог тишины, dB")
    p = sub.add_parser("render"); add_out(p)
    p = sub.add_parser("check"); add_out(p)

    a = ap.parse_args()
    if a.cmd == "auto":
        video = a.video
        if not video:
            vids = sorted(p for p in Path.cwd().iterdir() if p.suffix.lower() in VIDEO_EXT)
            if not vids:
                raise SystemExit("В папке нет видео")
            video = str(vids[0])
        out = a.out or str(Path(video).resolve().parent / "out")
        cmd_prepare(video, out, vertical=False if a.horizontal else None)
        cmd_transcribe(out, a.model, a.lang, a.prompt)
        cmd_plan(out, music=a.music)
        cmd_render(out)
        cmd_check(out)
    elif a.cmd == "prepare":
        cmd_prepare(a.video, a.out, vertical=False if a.horizontal else None)
    elif a.cmd == "transcribe":
        cmd_transcribe(a.out, a.model, a.lang, a.prompt)
    elif a.cmd == "plan":
        cmd_plan(a.out, noise_db=a.noise, music=a.music)
    elif a.cmd == "render":
        cmd_render(a.out)
    elif a.cmd == "check":
        cmd_check(a.out)


if __name__ == "__main__":
    main()
