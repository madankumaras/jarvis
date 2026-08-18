"""Load and validate workflows.yaml.

Kept separate from the engine so a malformed file is a loading error with a
readable message, not a mid-conversation crash.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_PATH = REPO_ROOT / "workflows.yaml"

VALID_KINDS = {"agent", "worker"}
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


@dataclass(frozen=True)
class Step:
    kind: str
    request: str
    say: str = ""
    offer: str = ""
    needs: tuple[str, ...] = ()

    @property
    def placeholders(self) -> set[str]:
        found: set[str] = set()
        for text in (self.request, self.say, self.offer):
            found.update(_PLACEHOLDER.findall(text or ""))
        return found


@dataclass(frozen=True)
class Workflow:
    name: str
    triggers: tuple[str, ...]
    steps: tuple[Step, ...]


@dataclass
class Catalogue:
    workflows: list[Workflow] = field(default_factory=list)

    def match(self, text: str) -> Workflow | None:
        """Find a workflow whose trigger appears in the utterance.

        Longest trigger wins, so "new build deployed" is not beaten by a
        shorter phrase that happens to be a substring of it.
        """
        said = " ".join((text or "").lower().split())
        if not said:
            return None
        best: tuple[int, Workflow] | None = None
        for wf in self.workflows:
            for trigger in wf.triggers:
                # A trigger with a placeholder matches on its literal prefix.
                literal = _PLACEHOLDER.sub("", trigger).strip().lower()
                if literal and literal in said:
                    if best is None or len(literal) > best[0]:
                        best = (len(literal), wf)
        return best[1] if best else None

    def by_name(self, name: str) -> Workflow | None:
        return next((w for w in self.workflows if w.name == name), None)


def load(path: str | Path | None = None) -> Catalogue:
    """Read the catalogue. A bad file raises here, with the reason."""
    p = Path(path or DEFAULT_PATH)
    if not p.is_file():
        return Catalogue()

    raw = yaml.safe_load(p.read_text()) or {}
    entries = raw.get("workflows") or []
    workflows: list[Workflow] = []

    for i, entry in enumerate(entries):
        name = (entry or {}).get("name")
        if not name:
            raise ValueError(f"workflow {i} has no name")
        triggers = tuple(entry.get("triggers") or ())
        if not triggers:
            raise ValueError(f"workflow {name!r} has no triggers")

        steps: list[Step] = []
        for j, s in enumerate(entry.get("steps") or []):
            kind = (s or {}).get("kind", "agent")
            if kind not in VALID_KINDS:
                raise ValueError(f"{name!r} step {j}: kind must be one of {sorted(VALID_KINDS)}")
            request = (s.get("request") or "").strip()
            if not request:
                raise ValueError(f"{name!r} step {j} has no request")
            steps.append(Step(
                kind=kind,
                request=" ".join(request.split()),
                say=" ".join((s.get("say") or "").split()),
                offer=" ".join((s.get("offer") or "").split()),
                needs=tuple(s.get("needs") or ()),
            ))

        if not steps:
            raise ValueError(f"workflow {name!r} has no steps")
        workflows.append(Workflow(name=name, triggers=triggers, steps=tuple(steps)))

    return Catalogue(workflows=workflows)
