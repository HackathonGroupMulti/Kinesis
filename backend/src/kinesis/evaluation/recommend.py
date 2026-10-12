"""The recommendation shown to the animator (advisory; the human decides).

- Gated candidates are never recommended.
- A candidate that leaves the planted slip at or above ``ACCEPTANCE_SLIP_CM`` is recommended
  only when no ungated candidate gets below it (2026-10-09 benchmark: smoothness and the visual
  review preferred B in 5/5 scenes, including ones where B left 1.3-2.3 cm and A left 0).
- When every ungated candidate has a visual judgement (evaluator OK), candidates are ranked
  by ``0.7 * objective_score + 0.3 * visual_score``, where ``visual_score`` is the mean of
  the five 1-5 scores mapped onto [0, 1]. Otherwise the objective ranking alone decides.
- The reason says which view decided, and flags when the model's favourite differs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from kinesis.evaluation.metrics import ACCEPTANCE_SLIP_CM, SCORING_WEIGHTS
from kinesis.schemas import CandidateLabel, ObjectiveRank, VisualEvaluation

OBJECTIVE_WEIGHT = 0.7
VISUAL_WEIGHT = 0.3


def visual_score(v: VisualEvaluation) -> float:
    scores = (
        v.contact_stability,
        v.naturalness,
        v.artifacts_visible,
        v.performance_preservation,
        v.instruction_adherence,
    )
    return (sum(scores) / len(scores) - 1) / 4


@dataclass(frozen=True)
class Recommendation:
    label: CandidateLabel | None
    reason: str
    weights: dict[str, float]


def recommend(
    ranking: Sequence[ObjectiveRank],
    visual: Mapping[str, VisualEvaluation],
    *,
    visual_ok: bool,
    residual_cm: Mapping[str, float] | None = None,
) -> Recommendation:
    """``residual_cm`` maps candidate id -> planted slip after the repair (cm)."""
    weights = dict(SCORING_WEIGHTS)
    ungated = [r for r in ranking if not r.gated and r.score is not None]
    if not ungated:
        return Recommendation(
            None, "Every candidate is gated by a preservation check; review carefully.", weights
        )
    note = ""
    if residual_cm is not None:
        passing = [r for r in ungated if residual_cm[r.candidate_id] < ACCEPTANCE_SLIP_CM]
        if passing and len(passing) < len(ungated):
            note = " " + " ".join(
                f"Candidate {r.label.value} leaves {residual_cm[r.candidate_id]:.1f} cm of slip, "
                f"above the {ACCEPTANCE_SLIP_CM:g} cm acceptance line."
                for r in ungated
                if r not in passing
            )
            ungated = passing
    objective_best = ungated[0]
    use_visual = visual_ok and all(r.candidate_id in visual for r in ungated)
    if not use_visual:
        reason = (
            f"Candidate {objective_best.label.value} has the highest objective score "
            f"({objective_best.score:.2f})."
        )
        return Recommendation(objective_best.label, reason + note, weights)

    weights |= {"objective": OBJECTIVE_WEIGHT, "visual": VISUAL_WEIGHT}

    def combined(r: ObjectiveRank) -> float:
        assert r.score is not None
        return OBJECTIVE_WEIGHT * r.score + VISUAL_WEIGHT * visual_score(visual[r.candidate_id])

    best = max(ungated, key=lambda r: (combined(r), -ungated.index(r)))
    top = max(visual_score(visual[r.candidate_id]) for r in ungated)
    leaders = [r for r in ungated if visual_score(visual[r.candidate_id]) == top]
    visual_best = leaders[0] if len(leaders) == 1 else None  # a tie favours no one
    reason = (
        f"Candidate {best.label.value} scores highest overall ({combined(best):.2f}: objective "
        f"{best.score:.2f}, visual {visual_score(visual[best.candidate_id]):.2f})."
    )
    if visual_best is not None and visual_best.label is not objective_best.label:
        reason += (
            f" The measurements favour {objective_best.label.value}; the visual review favours "
            f"{visual_best.label.value}."
        )
    return Recommendation(best.label, reason + note, weights)


__all__ = ["OBJECTIVE_WEIGHT", "VISUAL_WEIGHT", "Recommendation", "recommend", "visual_score"]
