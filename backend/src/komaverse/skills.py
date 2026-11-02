"""Studio craft skills (Agent Skills format): discovery + reading.

Skill files live in `.agents/skills/<name>/SKILL.md` at the repo root, so
the studio agent can consult the same craft knowledge as the coding
agents (anime direction, manga production, motion design, brand). The
agent calls list_skills/read_skill tools; only names and descriptions
sit in context until a skill is actually needed (progressive disclosure).

Resolution order for the skills root:
1. `KOMAVERSE_SKILLS_DIR` env (explicit wins, used by tests),
2. `.agents/skills` found by walking up from this file (repo checkout),
3. otherwise no skills (tools report it instead of failing).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_SKILL_BYTES = 48 * 1024


class SkillError(ValueError):
    """Unknown skill name, unsafe path, or unreadable skill file."""


def skills_dir(base: str | Path | None = None) -> Path | None:
    """Locate the skills root, or None when this checkout has none."""
    if base is not None:
        root = Path(base)
        return root if root.is_dir() else None
    env = os.environ.get("KOMAVERSE_SKILLS_DIR")
    if env:
        root = Path(env)
        return root if root.is_dir() else None
    here = Path(__file__).resolve()
    # backend/src/komaverse/skills.py -> repo root is three levels up.
    for parent in (here.parent, *here.parents):
        candidate = parent / ".agents" / "skills"
        if candidate.is_dir():
            return candidate
    return None


def _check_name(name: str) -> str:
    cleaned = (name or "").strip()
    if not SKILL_NAME_RE.match(cleaned):
        raise SkillError(
            f"unknown skill: {name!r} "
            "(names are lowercase letters, digits, and dashes)"
        )
    return cleaned


def _skill_file(name: str, base: str | Path | None = None) -> Path:
    cleaned = _check_name(name)
    root = skills_dir(base)
    if root is None:
        raise SkillError("no skills installed in this studio")
    return root / cleaned / "SKILL.md"


def _description(text: str, name: str) -> str:
    """Pull the `description:` frontmatter field (may span lines)."""
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        try:
            end = lines.index("---", 1)
        except ValueError:
            end = -1
        if end > 1:
            collecting = False
            parts: list[str] = []
            for line in lines[1:end]:
                if not collecting:
                    if line.startswith("description:"):
                        value = line.split(":", 1)[1].strip()
                        if value and value not in (">", ">-", "|", "|-"):
                            return value.strip(">").strip()
                        collecting = True
                elif line[:1] in (" ", "\t"):
                    parts.append(line.strip())
                else:
                    break
            if parts:
                return " ".join(parts)
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith(("#", "---")):
            return stripped[:200]
    return name


def list_skills(base: str | Path | None = None) -> list[dict[str, str]]:
    """Every installed skill as {name, description}, sorted by name."""
    root = skills_dir(base)
    if root is None:
        return []
    found: list[dict[str, str]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or not SKILL_NAME_RE.match(child.name):
            continue
        doc = child / "SKILL.md"
        if not doc.is_file():
            continue
        try:
            text = doc.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        found.append({"name": child.name, "description": _description(text, child.name)})
    return found


def _section(text: str, heading: str) -> str | None:
    """Return the first `## <heading>` section body (case-insensitive)."""
    want = heading.strip().lower()
    if not want:
        return None
    current: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if current is not None and current.lower().startswith(want):
                return "\n".join(buf).strip() or None
            current = line[3:].strip()
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None and current.lower().startswith(want):
        return "\n".join(buf).strip() or None
    return None


def read_skill(
    name: str, section: str | None = None, base: str | Path | None = None
) -> str:
    """Full SKILL.md text, or one `## ` section when `section` matches.

    Raises SkillError for unknown/unsafe names, missing files, and
    unknown sections. Callers cap what reaches model context.
    """
    path = _skill_file(name, base)
    if not path.is_file():
        raise SkillError(f"unknown skill: {name!r}")
    try:
        size = path.stat().st_size
        if size > MAX_SKILL_BYTES:
            raise SkillError(f"skill too large: {name!r}")
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise SkillError(f"cannot read skill {name!r}: {exc}") from exc
    if section:
        body = _section(text, section)
        if body is None:
            raise SkillError(f"skill {name!r} has no section {section!r}")
        return body
    return text
