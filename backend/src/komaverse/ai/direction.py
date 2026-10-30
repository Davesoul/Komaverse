"""Shared direction vocabulary for AI generation: style presets, camera
moves, and entity kinds. IDs are stable API values; labels are UI-only."""

from __future__ import annotations

ENTITY_KINDS = ("character", "field", "prop")

STYLE_PRESETS: dict[str, dict[str, str]] = {
    "none": {"label": "No style", "suffix": ""},
    "manga-ink": {
        "label": "Manga ink",
        "suffix": "bold black ink linework on clean white, screentone shading, manga panel art",
    },
    "cel-anime": {
        "label": "Cel anime",
        "suffix": "cel-shaded anime still, clean color fills, studio-quality TV anime frame",
    },
    "watercolor": {
        "label": "Watercolor",
        "suffix": "soft watercolor washes, visible paper grain, gentle edges",
    },
    "ukiyoe": {
        "label": "Ukiyo-e",
        "suffix": "ukiyo-e woodblock print, flat color planes, fine contour lines",
    },
    "noir": {
        "label": "Noir",
        "suffix": "high-contrast black-and-white noir, deep shadows, film grain",
    },
    "pencil": {
        "label": "Pencil sketch",
        "suffix": "detailed pencil sketch, construction lines, graphite shading",
    },
}

CAMERA_MOVES: dict[str, dict[str, str]] = {
    "none": {"label": "Static", "sentence": ""},
    "dolly-in": {
        "label": "Slow dolly-in",
        "sentence": "The camera pushes in slowly toward the subject.",
    },
    "dolly-out": {
        "label": "Slow dolly-out",
        "sentence": "The camera pulls back slowly to reveal the scene.",
    },
    "pan-left": {
        "label": "Pan left",
        "sentence": "The camera pans smoothly to the left.",
    },
    "pan-right": {
        "label": "Pan right",
        "sentence": "The camera pans smoothly to the right.",
    },
    "crane-up": {
        "label": "Crane up",
        "sentence": "The camera rises smoothly like on a crane.",
    },
    "orbit": {
        "label": "Slow orbit",
        "sentence": "The camera orbits slowly around the subject.",
    },
    "handheld": {
        "label": "Handheld drift",
        "sentence": "Subtle handheld drift, documentary feel.",
    },
    "zoom-snap": {
        "label": "Snap zoom",
        "sentence": "A quick punch-in zoom onto the subject.",
    },
}


def style_suffix(style: str) -> str:
    try:
        return STYLE_PRESETS[style]["suffix"]
    except KeyError:
        raise ValueError(f"unknown style: {style}")


def camera_sentence(move: str) -> str:
    try:
        return CAMERA_MOVES[move]["sentence"]
    except KeyError:
        raise ValueError(f"unknown camera move: {move}")


def build_final_prompt(
    *,
    prompt: str,
    entities: list[dict[str, str]],
    style: str = "none",
    camera_move: str = "none",
    kind: str = "image",
    negative_prompt: str = "",
) -> str:
    """Assemble the provider prompt: base + entity anchors + style + camera.

    Raises ValueError on unknown style/camera ids (surfaced as 400).
    Negative prompts fold into the text for providers whose dedicated API
    has no negative field (OpenRouter /images, /videos).
    """
    parts = [prompt.strip()]
    for ent in entities:
        anchor = (ent.get("anchor") or "").strip()
        label = f'{ent.get("name", "entity")} ({ent.get("kind", "prop")})'
        parts.append(f"{label}: {anchor}" if anchor else label)
    suffix = style_suffix(style)
    if suffix:
        parts.append(suffix)
    if kind == "video":
        sentence = camera_sentence(camera_move)
        if sentence:
            parts.append(sentence)
    negative = (negative_prompt or "").strip()
    if negative:
        parts.append(f"Avoid: {negative}")
    return "\n".join(p for p in parts if p)
