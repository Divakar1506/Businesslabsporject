"""Parse a transcript into speaker turns and split long meetings into overlapping sections.

Line format: [HH:MM:SS] Name (Client|Consultant): text      ('#' lines are header comments)
Lines that do not match are appended to the previous turn, so wrapped text is kept.
The original transcript is never changed; every extracted item points back to a turn.
"""
import re

from shared.llm_gateway import tokens

from .models import Turn

LINE = re.compile(r"^\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*([^:(]+?)\s*(?:\(([^)]+)\))?\s*:\s*(.*)$")


def parse(text: str) -> list[Turn]:
    turns: list[Turn] = []
    for i, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = LINE.match(line)
        if m:
            ts, name, role, body = m.groups()
            role_l = (role or "").lower()
            kind = "client" if "client" in role_l else "consultant" if ("consult" in role_l or "dev" in role_l) else "other"
            turns.append(Turn(ts=ts, speaker=name.strip(), role=kind, text=body.strip(), line=i))
        elif turns:
            turns[-1].text += " " + line
    return turns


def sections(turns: list[Turn], max_tokens: int = 500, overlap: int = 2) -> list[list[Turn]]:
    """Consecutive turns up to max_tokens each, with `overlap` turns repeated for context.

    Short meetings give one section; long ones are processed section by section and merged.
    """
    out, cur, size = [], [], 0
    for t in turns:
        n = tokens.estimate(t.text)
        if cur and size + n > max_tokens:
            out.append(cur)
            cur = cur[-overlap:] if overlap else []
            size = sum(tokens.estimate(x.text) for x in cur)
        cur.append(t)
        size += n
    if cur:
        out.append(cur)
    return out


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
