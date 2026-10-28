"""Deterministic readability check.

LLMs are bad at judging their own reading level and at counting words. This module gives the judge an
objective number to anchor on, and gives the pipeline a hard rule it can enforce in code.
"""

import re


def _count_syllables(word: str) -> int:
    word = word.lower()
    groups = re.findall(r"[aeiouy]+", word)
    count = len(groups)
    if word.endswith("e") and not word.endswith(("le", "ee")) and count > 1:
        count -= 1  # silent e: "cake", "home"
    return max(count, 1)


def analyze(text: str) -> dict:
    sentences = [s for s in re.split(r"[.!?]+", text) if s.strip()]
    words = re.findall(r"[A-Za-z']+", text)
    if not sentences or not words:
        return {"words": 0, "avg_sentence_len": 0.0, "grade": 0.0}
    syllables = sum(_count_syllables(w) for w in words)
    words_per_sentence = len(words) / len(sentences)
    syllables_per_word = syllables / len(words)
    # Flesch-Kincaid grade level formula
    grade = 0.39 * words_per_sentence + 11.8 * syllables_per_word - 15.59
    return {
        "words": len(words),
        "avg_sentence_len": round(words_per_sentence, 1),
        "grade": round(max(grade, 0.0), 1),
    }
