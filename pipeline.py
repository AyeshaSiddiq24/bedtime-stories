"""Orchestrates the agents: classify -> plan -> write -> (judge -> revise)* -> user feedback loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import llm
import prompts
import readability

# Pass/fail is decided in code, not by the model, so the bar can't drift between runs.
MIN_SAFETY = 9        # age_appropriateness must be at least this
MIN_EACH = 7          # every other criterion must be at least this
MIN_AVERAGE = 8.0     # and the average must be at least this


@dataclass
class Draft:
    story: str
    judgment: dict
    round: int

    @property
    def average(self) -> float:
        scores = self.judgment["scores"]
        return sum(scores.values()) / len(scores)


@dataclass
class StoryResult:
    request: str
    brief: dict
    outline: dict
    drafts: list[Draft] = field(default_factory=list)

    @property
    def best(self) -> Draft:
        # Revisions can occasionally regress, so we keep the highest-scoring version rather than the last one.
        passing = [d for d in self.drafts if d.judgment["passed"]]
        pool = passing or self.drafts
        return max(pool, key=lambda d: d.average)


def _noop(_msg: str) -> None:
    pass


def classify(request: str, age_band: str | None) -> dict:
    brief = llm.call_model_json(prompts.classifier_prompt(request, age_band), system=prompts.CLASSIFIER_SYSTEM)
    if brief.get("category") not in prompts.CATEGORY_GUIDES:
        brief["category"] = "adventure"
    if age_band:  # an explicit choice from the user always wins
        brief["age_band"] = age_band
    if brief.get("age_band") not in prompts.AGE_BANDS:
        brief["age_band"] = "5-7"
    return brief


def plan(brief: dict) -> dict:
    return llm.call_model_json(prompts.planner_prompt(brief), system=prompts.PLANNER_SYSTEM)


def write(brief: dict, outline: dict) -> str:
    return llm.call_model(
        prompts.storyteller_prompt(brief, outline), system=prompts.STORYTELLER_SYSTEM
    )


def judge(story: str, brief: dict, request: str) -> dict:
    stats = readability.analyze(story)
    result = llm.call_model_json(
        prompts.judge_prompt(story, brief, request, stats), system=prompts.JUDGE_SYSTEM
    )

    # Normalize scores so a malformed reply can't crash the loop or sneak a pass through.
    scores = {}
    for criterion in prompts.JUDGE_CRITERIA:
        try:
            scores[criterion] = max(1, min(10, int(result.get("scores", {}).get(criterion, 1))))
        except (TypeError, ValueError):
            scores[criterion] = 1
    result["scores"] = scores
    result.setdefault("fixes", [])
    result["readability"] = stats

    # Hard rule from code: if the text measures above the target grade, that is always a fix.
    max_grade = prompts.AGE_BANDS[brief["age_band"]]["max_grade"]
    too_hard = stats["grade"] > max_grade
    if too_hard:
        result["fixes"].insert(
            0, f"Reading level measures grade {stats['grade']}, above the target of {max_grade}. "
               "Use shorter sentences and simpler words."
        )

    others = [v for k, v in scores.items() if k != "age_appropriateness"]
    result["passed"] = (
        scores["age_appropriateness"] >= MIN_SAFETY
        and min(others) >= MIN_EACH
        and sum(scores.values()) / len(scores) >= MIN_AVERAGE
        and not too_hard
    )
    return result


def revise(story: str, brief: dict, outline: dict, notes: list[str], keep: str, source: str) -> str:
    return llm.call_model(
        prompts.revise_prompt(story, brief, outline, notes, keep, source),
        system=prompts.STORYTELLER_SYSTEM,
    )


def _judge_loop(result: StoryResult, story: str, max_rounds: int, log: Callable[[str], None]) -> StoryResult:
    """Judge the story and revise it until it passes or we run out of rounds."""
    start = len(result.drafts)
    for i in range(max_rounds + 1):
        judgment = judge(story, result.brief, result.request)
        result.drafts.append(Draft(story=story, judgment=judgment, round=start + i))
        log(_scorecard(judgment, start + i))
        if judgment["passed"] or i == max_rounds:
            break
        log("Revising with the judge's notes...")
        story = revise(story, result.brief, result.outline, judgment["fixes"][:3],
                       judgment.get("best_moment", ""), source="judge")
    return result


def generate(request: str, age_band: str | None = None, max_rounds: int = 2,
             log: Callable[[str], None] = _noop) -> StoryResult:
    log("Reading your request...")
    brief = classify(request, age_band)
    log(f"Category: {brief['category']} | Ages {brief['age_band']}")
    if brief.get("safety_notes", "none").lower() != "none":
        log(f"Made kid-friendly: {brief['safety_notes']}")

    log("Planning the story arc...")
    outline = plan(brief)

    log(f"Writing \"{outline.get('title', 'your story')}\"...")
    story = write(brief, outline)

    result = StoryResult(request=request, brief=brief, outline=outline)
    return _judge_loop(result, story, max_rounds, log)


def apply_feedback(result: StoryResult, feedback: str, max_rounds: int = 1,
                   log: Callable[[str], None] = _noop) -> StoryResult:
    """Apply the user's requested changes, then send the new version back through the judge."""
    log("Rewriting with your changes...")
    story = revise(result.best.story, result.brief, result.outline, [feedback], keep="", source="user")
    # Only drafts that reflect the user's change are eligible from here on.
    result.drafts = []
    return _judge_loop(result, story, max_rounds, log)


def _scorecard(judgment: dict, round_num: int) -> str:
    scores = judgment["scores"]
    avg = sum(scores.values()) / len(scores)
    status = "PASS" if judgment["passed"] else "needs work"
    line = "  ".join(f"{k.replace('_', ' ')}: {v}" for k, v in scores.items())
    r = judgment["readability"]
    return (f"[Judge, round {round_num}] {status} (avg {avg:.1f}) | grade level {r['grade']}, {r['words']} words\n"
            f"    {line}")