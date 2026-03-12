# text_preprocessing.py
# Pre-processes text before TTS synthesis to reduce generation artifacts.
# Handles punctuation normalization, short-sentence merging, and other cleanups.

import re
import logging

logger = logging.getLogger("flask_app.text_preprocessing")


def ensure_punctuation(text: str) -> str:
    """
    Ensures every sentence in the text ends with proper punctuation.
    Splits on newlines and common sentence boundaries, then appends
    a period to any sentence that doesn't end with . ! ? : ; or …
    """
    if not text or not text.strip():
        return text

    lines = text.split("\n")
    result_lines = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            result_lines.append(line)
            continue

        # Check if the line ends with punctuation (possibly followed by quotes/brackets)
        if re.search(r'[.!?;:…\u2026]["\'»\]\)]*\s*$', stripped):
            result_lines.append(line)
        else:
            # Add a period at the end
            result_lines.append(line.rstrip() + ".")
            logger.debug(f"Added period to: '{stripped[:50]}...'")

    return "\n".join(result_lines)


def merge_short_sentences(text: str, min_words: int = 3) -> str:
    """
    Merges very short sentences (fewer than min_words) with the next sentence.
    Short orphan sentences tend to produce glitchy TTS output.

    Args:
        text: Input text.
        min_words: Minimum number of words for a sentence to stand alone.

    Returns:
        Text with short sentences merged into their neighbours.
    """
    if not text or not text.strip() or min_words < 1:
        return text

    # Split into sentences preserving the delimiter
    # Match sentence-ending punctuation followed by whitespace
    parts = re.split(r'(?<=[.!?…\u2026])\s+', text.strip())

    if len(parts) <= 1:
        return text

    merged = []
    carry = ""

    for part in parts:
        if carry:
            # Merge the carried short sentence with the current one
            merged_text = carry + " " + part
            carry = ""
            # Check if the merged result is still short
            word_count = len(merged_text.split())
            if word_count < min_words and merged:
                # Append to previous sentence instead
                merged[-1] = merged[-1] + " " + merged_text
            else:
                merged.append(merged_text)
        else:
            word_count = len(part.split())
            if word_count < min_words:
                if merged:
                    # Try to append to the previous sentence
                    merged[-1] = merged[-1] + " " + part
                else:
                    # Nothing to merge with yet, carry forward
                    carry = part
            else:
                merged.append(part)

    # Handle any remaining carry
    if carry:
        if merged:
            merged[-1] = merged[-1] + " " + carry
        else:
            merged.append(carry)

    result = " ".join(merged)
    if result != text.strip():
        logger.debug(f"Merged short sentences. Original parts: {len(parts)}, After merge: {len(merged)}")

    return result


def normalize_text_for_tts(text: str, min_sentence_words: int = 3) -> str:
    """
    Orchestrator that applies all text pre-processing steps in order:
    1. Ensure punctuation at sentence ends
    2. Merge short orphan sentences

    Args:
        text: Raw input text.
        min_sentence_words: Minimum words for a standalone sentence.

    Returns:
        Cleaned and normalized text ready for TTS.
    """
    if not text or not text.strip():
        return text

    result = text

    # Step 1: Ensure punctuation
    result = ensure_punctuation(result)

    # Step 2: Merge short sentences
    result = merge_short_sentences(result, min_words=min_sentence_words)

    if result != text:
        logger.info(f"Text pre-processed for TTS (length: {len(text)} -> {len(result)})")

    return result
