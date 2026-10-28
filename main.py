import argparse
import os
import re
import webbrowser
from datetime import datetime

import pipeline
import storybook

"""
Before submitting the assignment, describe here in a few sentences what you would have built next if you spent 2 more hours on this project:

I would build a small evaluation set of about 20 varied requests (vague, oddly specific, and some that need a safety
reframe), then compare first drafts with judged drafts using a second, independent judge prompt. That would show
whether the revision loop actually improves stories or just optimizes for one judge's taste. I would also add an
"interactive" mode where the story pauses at the turning point and lets the child choose what the hero does next,
and a real image model behind the storybook so the pictures look hand-painted rather than vector shapes.
"""

# Handy for a quick demo: python main.py --example
example_requests = "A story about a girl named Alice and her best friend Bob, who happens to be a cat."


def _load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader so the key never has to be hard-coded (avoids an extra dependency)."""
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _save(result: pipeline.StoryResult) -> str:
    os.makedirs("stories", exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", result.outline.get("title", "story").lower()).strip("-")[:40]
    path = os.path.join("stories", f"{datetime.now():%Y%m%d-%H%M%S}-{slug}.md")
    best = result.best
    with open(path, "w") as f:
        f.write(best.story + "\n\n---\n")
        f.write(f"Request: {result.request}\n\n")
        f.write(f"Category: {result.brief['category']} | Ages {result.brief['age_band']} | "
                f"Judge average {best.average:.1f}\n")
    return path


def main():
    parser = argparse.ArgumentParser(description="Bedtime stories for ages 5-10, reviewed by an LLM judge.")
    parser.add_argument("--age", choices=["5-7", "8-10"], help="Listener's age band (inferred if omitted)")
    parser.add_argument("--rounds", type=int, default=2, help="Max judge-driven revision rounds (default 2)")
    parser.add_argument("--quiet", action="store_true", help="Hide the pipeline's progress and judge scores")
    parser.add_argument("--example", action="store_true", help="Use the built-in example request")
    parser.add_argument("--storybook", action="store_true",
                        help="Also make an illustrated, read-aloud picture book (HTML) and open it")
    args = parser.parse_args()

    _load_dotenv()
    log = (lambda _m: None) if args.quiet else (lambda m: print(f"  {m}"))

    if args.example:
        user_input = example_requests
        print(f"Request: {user_input}")
    else:
        user_input = input("What kind of story do you want to hear? ").strip() or example_requests

    result = pipeline.generate(user_input, age_band=args.age, max_rounds=args.rounds, log=log)
    print("\n" + result.best.story + "\n")

    while True:
        feedback = input("Want any changes? Describe them, or press Enter to finish: ").strip()
        if not feedback:
            break
        result = pipeline.apply_feedback(result, feedback, log=log)
        print("\n" + result.best.story + "\n")

    print(f"Saved to {_save(result)}.")

    if args.storybook:
        path = storybook.build_storybook(result.best.story, result.brief, log=log)
        print(f"Picture book ready: {path}")
        webbrowser.open("file://" + os.path.abspath(path))

    print("Sweet dreams!")


if __name__ == "__main__":
    main()
