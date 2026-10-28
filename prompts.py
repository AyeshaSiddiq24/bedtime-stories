"""All prompts live here, separate from control flow, so they can be read and tuned on their own.

Each agent has a narrow job and its own persona:
  classifier  -> understands the request and makes it safe
  planner     -> designs the story arc
  storyteller -> writes the prose
  judge       -> critiques against a rubric (a different persona, so it doesn't grade its own homework)
  reviser     -> applies the judge's or the user's notes
"""

from __future__ import annotations

import json

# ---------------------------------------------------------------------------
# Age bands: a 5-year-old and a 10-year-old need very different stories.
# ---------------------------------------------------------------------------
AGE_BANDS = {
    "5-7": {
        "words": "350-500",
        "sentences": "short sentences, mostly under 12 words",
        "vocabulary": "simple everyday words; at most one or two fun new words, explained by context",
        "max_grade": 3.0,  # Flesch-Kincaid grade ceiling, checked in code
    },
    "8-10": {
        "words": "550-800",
        "sentences": "varied sentences, mostly under 18 words",
        "vocabulary": "rich but familiar words; a few vivid new words are welcome",
        "max_grade": 5.0,
    },
}

# ---------------------------------------------------------------------------
# Category-specific strategies: each kind of story has its own craft rules.
# ---------------------------------------------------------------------------
CATEGORY_GUIDES = {
    "adventure": "A journey with a clear goal. Small, solvable obstacles (a wobbly bridge, a riddle), never real danger. "
                 "The hero succeeds through cleverness or kindness, not force.",
    "friendship": "Center the feelings between characters. Include a small misunderstanding or difference that gets "
                  "resolved through listening, sharing, or apologizing.",
    "animal": "Give animals distinct, consistent personalities and sounds. Weave in one gentle, true fact about the animal.",
    "fantasy": "One clear magical rule that stays consistent. Magic should feel wondrous, not scary. "
               "Creatures that sound fearsome turn out to be friendly or misunderstood.",
    "funny": "Silly escalation, playful repetition, and a running gag kids can anticipate. Humor never comes from meanness.",
    "calming": "Slow pacing, soft sensory details (warm blankets, quiet rain), and very little conflict. "
               "The whole story should feel like a deep breath.",
    "learning": "Curiosity drives the plot. Weave one real, simple fact (about space, nature, how things work) into the "
                "action so it is discovered, not lectured.",
}

# ---------------------------------------------------------------------------
# 1. Classifier
# ---------------------------------------------------------------------------
CLASSIFIER_SYSTEM = (
    "You are the intake editor at a children's publisher. You turn a parent's or child's story request into a clear, "
    "safe creative brief for stories told to children aged 5 to 10."
)


def classifier_prompt(request: str, age_band: str | None) -> str:
    categories = ", ".join(CATEGORY_GUIDES)
    age_line = f'The listener is in the "{age_band}" age band.' if age_band else (
        'Infer the age band ("5-7" or "8-10") from the request; if there are no clues, use "5-7".'
    )
    return f"""Analyze this bedtime story request.

<request>
{request}
</request>

{age_line}

Rules:
- Pick the single best category from: {categories}.
- Keep every specific detail the requester asked for (names, animals, places, objects).
- If anything in the request is unsuitable for young children (violence, death, real-world fear, romance, brand
  characters), do NOT refuse. Gently reframe it into a kid-friendly version that keeps the spirit of the request,
  and explain what you changed in "safety_notes". Example: "a dragon who eats a village" becomes
  "a hungry dragon who learns to cook soup with the villagers".
- If the request is vague (e.g. "a story"), invent fun, specific details.

Reply with ONLY this JSON:
{{
  "category": "<one category>",
  "age_band": "5-7" or "8-10",
  "characters": [{{"name": "...", "description": "..."}}],
  "setting": "...",
  "theme": "<the gentle lesson or feeling, e.g. 'being brave can be small'>",
  "must_include": ["<specific details from the request>"],
  "safety_notes": "<what you reframed and why, or 'none'>",
  "brief": "<one or two sentences describing the story to write>"
}}"""


# ---------------------------------------------------------------------------
# 2. Planner (story arc)
# ---------------------------------------------------------------------------
PLANNER_SYSTEM = (
    "You are a children's story architect. You design story arcs that are simple enough for a child to follow and "
    "satisfying enough that they ask to hear them again."
)


def planner_prompt(brief: dict) -> str:
    return f"""Design the outline for this bedtime story.

<brief>
{json.dumps(brief, indent=2)}
</brief>

Category strategy: {CATEGORY_GUIDES.get(brief["category"], "")}

Use this six-beat bedtime arc:
1. cozy_opening  - meet the hero in their world, with one detail that makes them lovable
2. spark         - something happens that sets a goal or problem
3. try_and_stumble - the hero tries, and it doesn't work at first
4. turning_point - an insight, a friend's help, or a brave small choice
5. resolution    - the problem is solved in a warm, earned way
6. wind_down     - the world gets quiet and the hero settles toward sleep

Keep each beat to one or two short sentences.
Also invent a short "refrain": a catchy line (under 10 words) that repeats 2-3 times so a child can say it along.

Reply with ONLY this JSON:
{{
  "title": "...",
  "refrain": "...",
  "beats": {{
    "cozy_opening": "...",
    "spark": "...",
    "try_and_stumble": "...",
    "turning_point": "...",
    "resolution": "...",
    "wind_down": "..."
  }}
}}"""


# ---------------------------------------------------------------------------
# 3. Storyteller
# ---------------------------------------------------------------------------
STORYTELLER_SYSTEM = (
    "You are a beloved bedtime storyteller. Your stories are written to be read aloud by a parent: they have rhythm, "
    "warm humor, dialogue with distinct voices, and gentle sensory detail. They always leave a child feeling safe, "
    "happy, and sleepy."
)


def storyteller_prompt(brief: dict, outline: dict) -> str:
    band = AGE_BANDS[brief["age_band"]]
    return f"""Write the bedtime story from this outline.

<brief>
{json.dumps(brief, indent=2)}
</brief>

<outline>
{json.dumps(outline, indent=2)}
</outline>

Writing rules for ages {brief["age_band"]}:
- Length: {band["words"]} words.
- Sentences: {band["sentences"]}.
- Vocabulary: {band["vocabulary"]}.
- Follow the six beats in order, but never label them. It should read as one flowing story.
- Use the refrain "{outline["refrain"]}" 2-3 times.
- Show feelings through actions and dialogue rather than just naming them.
- Include every item in "must_include".
- Nothing frightening, violent, or sad without comfort right after it.
- The final paragraph should slow down and end on a quiet, sleepy image.

Category strategy: {CATEGORY_GUIDES.get(brief["category"], "")}

Reply with the title on the first line, then a blank line, then the story. No other commentary."""


# ---------------------------------------------------------------------------
# 4. Judge
# ---------------------------------------------------------------------------
JUDGE_CRITERIA = [
    "age_appropriateness",
    "story_arc",
    "engagement",
    "language_level",
    "request_fidelity",
    "bedtime_fit",
]

JUDGE_SYSTEM = (
    "You are a strict children's librarian with a background in child development. You review bedtime stories "
    "before they are read to children aged 5 to 10. You are hard to impress: a score of 10 is rare, "
    "and you always find something concrete to improve. You did not write the story you are reviewing."
)


def judge_prompt(story: str, brief: dict, original_request: str, readability: dict) -> str:
    band = AGE_BANDS[brief["age_band"]]
    return f"""Review this bedtime story.

<original_request>
{original_request}
</original_request>

<brief>
{json.dumps(brief, indent=2)}
</brief>

<story>
{story}
</story>

<measured_readability>
Word count: {readability["words"]} (target {band["words"]})
Average sentence length: {readability["avg_sentence_len"]} words
Flesch-Kincaid grade level: {readability["grade"]} (target at or below {band["max_grade"]})
</measured_readability>

Score each criterion from 1 to 10:
- age_appropriateness: safe, gentle, and emotionally suitable for ages {brief["age_band"]}
- story_arc: clear beginning, problem, effort, turning point, and satisfying resolution
- engagement: vivid, fun to read aloud, distinct character voices, a child would want to hear it again
- language_level: vocabulary and sentence length fit ages {brief["age_band"]} (use the measured readability)
- request_fidelity: honors what was asked for, including every "must_include" detail
- bedtime_fit: calming wind-down ending that helps a child drift toward sleep

Think through the story's weaknesses in "reasoning" BEFORE assigning scores.
Each fix must be a specific editing instruction (e.g. "Shorten the sentences in the bridge scene" rather than
"improve pacing").

Reply with ONLY this JSON:
{{
  "reasoning": "<your critical analysis, 3-5 sentences>",
  "scores": {{{", ".join(f'"{c}": <1-10>' for c in JUDGE_CRITERIA)}}},
  "best_moment": "<the strongest part of the story, so a revision keeps it>",
  "fixes": ["<up to 3 specific, actionable edits, most important first>"]
}}"""


# ---------------------------------------------------------------------------
# 5. Reviser (used for both judge notes and user feedback)
# ---------------------------------------------------------------------------
def revise_prompt(story: str, brief: dict, outline: dict, notes: list[str], keep: str, source: str) -> str:
    band = AGE_BANDS[brief["age_band"]]
    who = "the child or parent listening to this story" if source == "user" else "a children's librarian"
    priority = (
        "The listener's request takes priority. Make the change fully, even if it means changing the plot, "
        "but keep the story safe and age-appropriate."
        if source == "user"
        else "Apply every note while keeping what already works."
    )
    notes_text = "\n".join(f"- {n}" for n in notes)
    return f"""Revise this bedtime story based on feedback from {who}.

<story>
{story}
</story>

<feedback>
{notes_text}
</feedback>

<keep>
{keep or "the story's warmth, characters, and refrain"}
</keep>

{priority}

Still follow these rules: ages {brief["age_band"]}, {band["words"]} words, {band["sentences"]},
refrain "{outline["refrain"]}" used 2-3 times, and a quiet, sleepy final paragraph.

Reply with the title on the first line, then a blank line, then the full revised story. No other commentary."""


# ---------------------------------------------------------------------------
# 6. Storybook: art director, illustrator, picture judge
# ---------------------------------------------------------------------------
ART_DIRECTOR_SYSTEM = (
    "You are the art director for a children's picture book. You define a simple, consistent visual style so that "
    "several illustrators working at the same time produce pictures that look like one book."
)


def art_director_prompt(brief: dict, title: str) -> str:
    return f"""Create the art direction for the picture book "{title}".

<brief>
{json.dumps(brief, indent=2)}
</brief>

The pictures will be simple flat vector drawings (SVG) made of soft rounded shapes, like a gentle board book.
Describe every character so it can be drawn the same way on every page using only basic shapes.

Reply with ONLY this JSON:
{{
  "palette": ["<6 soft hex colors that suit the story, including one night-sky color>"],
  "characters": [{{"name": "...", "look": "<shapes, colors and one signature detail, e.g. 'round orange body, triangle ears, green scarf'>"}}],
  "world": "<the setting's recurring visual elements>"
}}"""


ILLUSTRATOR_SYSTEM = (
    "You are a picture-book illustrator who draws in clean SVG code. Your style is flat, soft and friendly: "
    "rounded shapes, gentle colors, happy faces, no outlines thicker than 3px. You never draw anything scary."
)


def illustrator_prompt(page_text: str, art: dict, page_num: int, total: int, note: str = "") -> str:
    last = page_num == total
    mood = "This is the last page: make it quiet and sleepy, with a night sky, moon or stars." if last else ""
    fix = f"\nA reviewer asked for this change to your last attempt: {note}\n" if note else ""
    return f"""Draw the illustration for page {page_num} of {total}.

<page_text>
{page_text}
</page_text>

<art_direction>
{json.dumps(art, indent=2)}
</art_direction>
{fix}
Rules:
- Show the single most important moment on this page. {mood}
- Draw characters exactly as described in the art direction, using only colors from the palette (plus white and
  a dark color for eyes).
- Use viewBox="0 0 400 300". Fill the whole background first.
- Use only basic SVG shapes: rect, circle, ellipse, path, polygon, line, g. No text, no images, no scripts,
  no filters, no external links.
- Keep it under 80 elements.

Reply in exactly this format and nothing else:
SCENE: <one sentence describing what you drew>
<svg ...>...</svg>"""


PICTURE_JUDGE_SYSTEM = (
    "You review picture-book illustrations for children aged 5 to 10. You check that each picture matches its page "
    "and is gentle and friendly. You are reviewing the illustrator's description of each picture."
)


def picture_judge_prompt(pages: list[dict]) -> str:
    items = "\n\n".join(
        f'<page number="{i}">\n<text>{p["text"]}</text>\n<picture>{p["scene"]}</picture>\n</page>'
        for i, p in enumerate(pages, 1)
    )
    return f"""Check each page's picture against its text.

{items}

Flag a page only if its picture shows the wrong moment, the wrong characters, or anything that could frighten a
young child. For each flagged page, give one specific instruction for the redraw.

Reply with ONLY this JSON (an empty list if everything is fine):
{{"redraw": [{{"page": <number>, "note": "<what to change>"}}]}}"""