"""Turns a finished story into an illustrated, read-aloud HTML picture book.

Flow: split into pages -> art director (one shared style) -> illustrators (one per page, in parallel)
      -> sanitize SVG in code -> picture judge -> redraw flagged pages -> render HTML
"""

from __future__ import annotations

import html
import os
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import llm
import prompts

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")

MAX_PAGES = 6
ALLOWED_TAGS = {"svg", "g", "rect", "circle", "ellipse", "path", "polygon", "polyline", "line",
                "defs", "linearGradient", "radialGradient", "stop", "title"}

# Shown if a drawing fails twice, so the book is never missing a page.
FALLBACK_SVG = (
    f'<svg xmlns="{SVG_NS}" viewBox="0 0 400 300"><rect width="400" height="300" fill="#3a3570"/>'
    '<circle cx="310" cy="80" r="38" fill="#f6d77a"/><circle cx="326" cy="70" r="34" fill="#3a3570"/>'
    '<circle cx="80" cy="60" r="3" fill="#fff"/><circle cx="150" cy="110" r="2" fill="#fff"/>'
    '<circle cx="220" cy="50" r="2.5" fill="#fff"/><circle cx="60" cy="170" r="2" fill="#fff"/>'
    '<ellipse cx="200" cy="320" rx="260" ry="90" fill="#5b5a9e"/></svg>'
)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
def split_story(story: str) -> tuple[str, list[str]]:
    """Return (title, pages). Paragraphs are grouped evenly into at most MAX_PAGES pages."""
    lines = story.strip().splitlines()
    title = lines[0].strip().strip("#* ") if lines else "A Bedtime Story"
    body = "\n".join(lines[1:])
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    if not paragraphs:
        return title, [body.strip()]
    n = min(MAX_PAGES, len(paragraphs))
    size, extra = divmod(len(paragraphs), n)
    pages, i = [], 0
    for k in range(n):
        step = size + (1 if k < extra else 0)
        pages.append("\n\n".join(paragraphs[i : i + step]))
        i += step
    return title, pages


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------
def sanitize_svg(raw: str) -> str | None:
    """Parse model-written SVG and strip anything that isn't a plain shape. Returns None if unusable."""
    match = re.search(r"<svg\b.*?</svg>", raw, re.DOTALL | re.IGNORECASE)
    if not match:
        return None
    try:
        root = ET.fromstring(match.group(0))
    except ET.ParseError:
        return None

    def local(tag: str) -> str:
        return tag.split("}", 1)[-1]

    def clean(el: ET.Element) -> None:
        for child in list(el):
            if local(child.tag) not in ALLOWED_TAGS:
                el.remove(child)
            else:
                clean(child)
        for attr in list(el.attrib):
            name = local(attr).lower()
            value = el.attrib[attr].lower()
            if name.startswith("on") or name == "href" or ("url(" in value and "#" not in value):
                del el.attrib[attr]

    if local(root.tag) != "svg":
        return None
    clean(root)
    root.set("viewBox", root.get("viewBox", "0 0 400 300"))
    for dim in ("width", "height"):
        root.attrib.pop(dim, None)  # let CSS size it
    root.set("role", "img")
    return ET.tostring(root, encoding="unicode")


def draw_page(text: str, art: dict, num: int, total: int, note: str = "") -> dict:
    """Ask the illustrator for one page, retrying once if the SVG is unusable."""
    for _ in range(2):
        raw = llm.call_model(prompts.illustrator_prompt(text, art, num, total, note),
                             system=prompts.ILLUSTRATOR_SYSTEM, max_tokens=4000)
        scene_match = re.search(r"SCENE:\s*(.+)", raw)
        svg = sanitize_svg(raw)
        if svg:
            return {"text": text, "svg": svg,
                    "scene": scene_match.group(1).strip() if scene_match else "an illustration of this page"}
    return {"text": text, "svg": FALLBACK_SVG, "scene": "a quiet night sky with a crescent moon"}


def build_pages(title: str, page_texts: list[str], brief: dict, log: Callable[[str], None]) -> list[dict]:
    log("Choosing the art style...")
    art = llm.call_model_json(prompts.art_director_prompt(brief, title),
                              system=prompts.ART_DIRECTOR_SYSTEM)

    total = len(page_texts)
    log(f"Drawing {total} pictures...")
    with ThreadPoolExecutor(max_workers=total) as pool:  # pages are independent, so draw them at once
        futures = [pool.submit(draw_page, text, art, num, total) for num, text in enumerate(page_texts, 1)]
        pages = [f.result() for f in futures]

    log("Checking the pictures...")
    try:
        review = llm.call_model_json(prompts.picture_judge_prompt(pages), system=prompts.PICTURE_JUDGE_SYSTEM)
        flagged = [r for r in review.get("redraw", []) if 1 <= int(r.get("page", 0)) <= total]
    except (RuntimeError, ValueError, TypeError):
        flagged = []  # a failed review shouldn't block the book
    for item in flagged:
        n = int(item["page"])
        log(f"Redrawing page {n}: {item.get('note', '')}")
        pages[n - 1] = draw_page(page_texts[n - 1], art, n, total, note=item.get("note", ""))
    return pages


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------
def render_html(title: str, pages: list[dict]) -> str:
    def para_html(text: str) -> str:
        return "".join(f"<p>{html.escape(p)}</p>" for p in text.split("\n\n"))

    sections = [
        f'<section class="page cover" data-speak="{html.escape(title)}">'
        f'<div class="art">{pages[0]["svg"]}</div><h1>{html.escape(title)}</h1>'
        f'<p class="hint">A bedtime story</p></section>'
    ]
    for i, p in enumerate(pages, 1):
        sections.append(
            f'<section class="page" hidden data-speak="{html.escape(p["text"])}">'
            f'<div class="art" aria-label="{html.escape(p["scene"])}">{p["svg"]}</div>'
            f'<div class="words">{para_html(p["text"])}</div></section>'
        )
    sections.append(
        '<section class="page end" hidden data-speak="The end. Goodnight.">'
        f'<div class="art">{pages[-1]["svg"]}</div><h2>The end</h2><p class="hint">Goodnight.</p></section>'
    )
    return (HTML_TEMPLATE.replace("{{TITLE}}", html.escape(title))
            .replace("{{PAGES}}", "\n".join(sections))
            .replace("{{SCRIPT}}", READER_JS))


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{TITLE}}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Andika:wght@400;700&family=Fredoka:wght@500;600&display=swap" rel="stylesheet">
<style>
  :root {
    --sky: #26214a;      /* the room with the lights off */
    --sky-2: #352e66;
    --paper: #f7f1ff;    /* pale lilac page */
    --ink: #2e2850;
    --moon: #f2b544;
    --soft: #8d86c4;
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; margin: 0; }
  body {
    background: radial-gradient(ellipse at 50% 0%, var(--sky-2), var(--sky) 70%);
    color: var(--ink);
    font-family: "Andika", "Trebuchet MS", system-ui, sans-serif;  /* Andika is designed for new readers */
    display: grid; place-items: center; padding: 16px;
  }
  .book {
    width: min(720px, 100%); background: var(--paper); border-radius: 28px 28px 28px 8px;
    padding: clamp(16px, 4vw, 32px); box-shadow: 0 30px 60px -20px #0008;
  }
  .page { animation: turn .35s ease; }
  .art svg { display: block; width: 100%; height: auto; border-radius: 18px; }
  h1, h2 { font-family: "Fredoka", "Trebuchet MS", sans-serif; font-weight: 600; text-align: center;
           font-size: clamp(1.8rem, 5vw, 2.6rem); line-height: 1.15; margin: 24px 0 4px; }
  .hint { text-align: center; color: var(--soft); font-size: 1.1rem; margin: 0 0 8px; }
  .words { max-width: 60ch; margin: 20px auto 4px; }
  .words p { font-size: clamp(1.15rem, 2.6vw, 1.4rem); line-height: 1.75; margin: 0 0 .8em; }
  nav { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 20px; }
  button {
    font: 600 1.05rem "Fredoka", sans-serif; border: 0; border-radius: 999px; padding: 12px 22px;
    background: var(--sky); color: var(--paper); cursor: pointer;
  }
  button.read { background: var(--moon); color: var(--ink); }
  button:disabled { opacity: .3; cursor: default; }
  button:focus-visible { outline: 3px solid var(--moon); outline-offset: 3px; }
  .dots { display: flex; gap: 6px; }
  .dots span { width: 9px; height: 9px; border-radius: 50%; background: #d9d2f0; }
  .dots span.on { background: var(--moon); }
  @keyframes turn { from { opacity: 0; transform: translateX(14px); } to { opacity: 1; transform: none; } }
  @media (prefers-reduced-motion: reduce) { .page { animation: none; } }
</style>
</head>
<body>
<main class="book">
{{PAGES}}
<nav>
  <button class="prev" aria-label="Previous page">Back</button>
  <div class="dots" aria-hidden="true"></div>
  <button class="read">Read to me</button>
  <button class="next" aria-label="Next page">Next</button>
</nav>
</main>
{{SCRIPT}}
</body>
</html>
"""


READER_JS = r"""<script>
  // ---- Page turning ----
  const pages = [...document.querySelectorAll(".page")];
  const prev = document.querySelector(".prev"), next = document.querySelector(".next");
  const read = document.querySelector(".read"), dots = document.querySelector(".dots");
  const canSpeak = "speechSynthesis" in window;
  let at = 0, reading = false, run = 0;
  pages.forEach(() => dots.appendChild(document.createElement("span")));

  function show(i) {
    at = Math.max(0, Math.min(pages.length - 1, i));
    pages.forEach((p, k) => p.hidden = k !== at);
    [...dots.children].forEach((d, k) => d.classList.toggle("on", k === at));
    prev.disabled = at === 0; next.disabled = at === pages.length - 1;
    if (reading) speakPage();
  }

  // ---- Choosing a friendly voice ----
  // Novelty and robotic voices that sound strange (or scary) reading to a child.
  const AVOID = /albert|bad news|bahh|bells|boing|bubbles|cellos|good news|jester|organ|superstar|trinoids|whisper|wobble|zarvox|fred|junior|ralph|kathy|compact/i;
  const PREFERRED = ["Ava", "Zoe", "Samantha", "Allison", "Susan", "Karen", "Moira", "Tessa", "Serena",
                     "Google US English", "Google UK English Female", "Microsoft Aria", "Microsoft Jenny"];
  const picker = document.createElement("select");
  picker.setAttribute("aria-label", "Reading voice");
  picker.style.cssText = "font:500 .95rem Fredoka,sans-serif;border-radius:999px;padding:10px 12px;" +
                         "border:2px solid #d9d2f0;background:#fff;color:#2e2850;max-width:150px";
  let voices = [], voice = null;

  function score(v) {
    let s = 0;
    const i = PREFERRED.findIndex(n => v.name.startsWith(n));
    if (i >= 0) s += 100 - i;
    if (/premium|enhanced|natural|neural/i.test(v.name)) s += 50;   // higher-quality versions of a voice
    if (/^en[-_]US/i.test(v.lang)) s += 5;
    return s;
  }
  function loadVoices() {
    voices = speechSynthesis.getVoices().filter(v => /^en/i.test(v.lang) && !AVOID.test(v.name))
                                        .sort((a, b) => score(b) - score(a));
    if (!voices.length) return;
    let saved = null;
    try { saved = localStorage.getItem("bedtime-voice"); } catch (e) {}
    voice = voices.find(v => v.name === saved) || voices[0];
    picker.innerHTML = voices.map(v => `<option${v === voice ? " selected" : ""}>${v.name}</option>`).join("");
  }
  picker.onchange = () => {
    voice = voices.find(v => v.name === picker.value);
    try { localStorage.setItem("bedtime-voice", voice.name); } catch (e) {}
    if (reading) speakPage();
  };

  // ---- Reading with expression ----
  // Each sentence is spoken separately so its tone can match: excited, curious, a character talking, or sleepy.
  function sentences(text) {
    return text.replace(/\s+/g, " ").match(/[^.!?]+[.!?]+["\u201d\u2019']*|[^.!?]+$/g) || [text];
  }
  function toneFor(sentence, sleepy) {
    if (sleepy) return { rate: 0.78, pitch: 1.0 };                       // calm, slow wind-down
    const t = { rate: 0.95, pitch: 1.15 };                               // warm, friendly base
    if (/!["\u201d\u2019']*$/.test(sentence)) { t.rate = 1.05; t.pitch = 1.4; }  // excitement
    else if (/\?["\u201d\u2019']*$/.test(sentence)) { t.pitch = 1.3; }   // curiosity
    if (/["\u201c\u201d]/.test(sentence)) t.pitch += 0.1;                // a character is talking
    return t;
  }
  function speakPage() {
    speechSynthesis.cancel();
    const mine = ++run;                       // ignore callbacks from speech we cancelled
    const sleepy = at >= pages.length - 2;
    const parts = sentences(pages[at].dataset.speak);
    parts.forEach((sentence, k) => {
      const u = new SpeechSynthesisUtterance(sentence.trim());
      const t = toneFor(sentence, sleepy);
      u.rate = t.rate; u.pitch = t.pitch;
      if (voice) u.voice = voice;
      if (k === parts.length - 1) u.onend = () => {
        if (mine !== run || !reading) return;
        if (at < pages.length - 1) setTimeout(() => { if (mine === run && reading) show(at + 1); }, 900);
        else stop();
      };
      speechSynthesis.speak(u);
    });
  }
  function stop() { reading = false; run++; speechSynthesis.cancel(); read.textContent = "Read to me"; }

  if (canSpeak) {
    read.parentNode.insertBefore(picker, read);
    read.parentNode.style.flexWrap = "wrap";
    loadVoices();
    speechSynthesis.onvoiceschanged = loadVoices;   // Chrome loads voices a moment after the page
  } else {
    read.hidden = true;
  }
  read.onclick = () => { if (reading) return stop(); reading = true; read.textContent = "Stop reading"; speakPage(); };
  prev.onclick = () => show(at - 1);
  next.onclick = () => show(at + 1);
  document.addEventListener("keydown", e => {
    if (e.target === picker) return;
    if (e.key === "ArrowRight") show(at + 1);
    if (e.key === "ArrowLeft") show(at - 1);
  });
  show(0);
</script>"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def build_storybook(story: str, brief: dict, out_dir: str = "stories",
                    log: Callable[[str], None] = lambda _m: None) -> str:
    title, page_texts = split_story(story)
    pages = build_pages(title, page_texts, brief, log)
    os.makedirs(out_dir, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "storybook"
    path = os.path.join(out_dir, f"{slug}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_html(title, pages))
    return path


def refresh(path: str) -> None:
    """Update the reader (voice and controls) in an already-made book, without any API calls."""
    with open(path, encoding="utf-8") as f:
        page = f.read()
    page = re.sub(r"<script>.*?</script>", lambda _m: READER_JS, page, count=1, flags=re.DOTALL)
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)


if __name__ == "__main__":
    # Usage: python3 storybook.py stories/<book>.html   (refreshes the reader in existing books)
    import sys
    for book in sys.argv[1:]:
        refresh(book)
        print(f"Updated {book}")