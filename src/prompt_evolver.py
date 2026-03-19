"""ARCANA AI — Prompt Evolver.

A/B tests and optimizes LLM prompts across all ARCANA operations.

The system:
1. Maintains a library of prompt variants for each operation
2. Uses deterministic bucketing to split traffic between variants
3. Tracks outcomes (conversion rate, quality score, engagement)
4. Auto-promotes winning variants and retires losers
5. Generates new variants via mutation of winners

This is how ARCANA gets better at talking — not just what it does, but HOW it says it.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from src.llm import LLM, Tier
from src.memory import Memory

logger = logging.getLogger("arcana.prompt_evolver")


class PromptVariant:
    """A single prompt variant being tested."""

    def __init__(
        self, name: str, operation: str, prompt_template: str,
        parent: str = "original",
    ) -> None:
        self.name = name
        self.operation = operation  # e.g., "lead_reply", "scanner_response", "content_tweet"
        self.prompt_template = prompt_template
        self.parent = parent
        self.impressions = 0
        self.conversions = 0
        self.quality_scores: list[float] = []
        self.created = datetime.now(timezone.utc).isoformat()
        self.status = "testing"  # testing, winner, retired

    @property
    def conversion_rate(self) -> float:
        return self.conversions / self.impressions if self.impressions > 0 else 0.0

    @property
    def avg_quality(self) -> float:
        return sum(self.quality_scores) / len(self.quality_scores) if self.quality_scores else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "operation": self.operation,
            "prompt_template": self.prompt_template,
            "parent": self.parent,
            "impressions": self.impressions,
            "conversions": self.conversions,
            "conversion_rate": self.conversion_rate,
            "quality_scores": self.quality_scores[-50:],
            "avg_quality": self.avg_quality,
            "status": self.status,
            "created": self.created,
        }


class PromptEvolver:
    """Evolve and optimize prompts through A/B testing."""

    def __init__(self, llm: LLM, memory: Memory) -> None:
        self.llm = llm
        self.memory = memory
        self.variants: dict[str, list[PromptVariant]] = {}
        self._load_variants()

    def _load_variants(self) -> None:
        """Load variant data from memory."""
        data = self.memory.get_tacit("prompt-variants")
        if not data:
            return
        try:
            parsed = json.loads(data.split("\n", 2)[-1]) if "\n" in data else {}
            for op, variants in parsed.items():
                self.variants[op] = []
                for v in variants:
                    pv = PromptVariant(
                        v["name"], v["operation"], v["prompt_template"],
                        v.get("parent", "original"),
                    )
                    pv.impressions = v.get("impressions", 0)
                    pv.conversions = v.get("conversions", 0)
                    pv.quality_scores = v.get("quality_scores", [])[-50:]
                    pv.status = v.get("status", "testing")
                    pv.created = v.get("created", "")
                    self.variants[op].append(pv)
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    def _save_variants(self) -> None:
        """Persist variant data."""
        data = {}
        for op, variants in self.variants.items():
            data[op] = [v.to_dict() for v in variants]
        self.memory.save_tacit("prompt-variants", json.dumps(data, indent=2))

    def get_variant(self, operation: str, context_key: str) -> PromptVariant | None:
        """Get the prompt variant to use for an operation.

        Uses deterministic bucketing based on context_key (e.g., lead handle,
        opportunity ID) so the same entity always gets the same variant.
        """
        variants = [v for v in self.variants.get(operation, []) if v.status != "retired"]
        if not variants:
            return None

        # Deterministic bucket
        from src.toolkit import fast_hash
        bucket = int(fast_hash(context_key), 16) % len(variants)
        variant = variants[bucket]
        variant.impressions += 1
        return variant

    def record_outcome(
        self, operation: str, variant_name: str,
        converted: bool = False, quality_score: float | None = None,
    ) -> None:
        """Record the outcome of using a variant."""
        for v in self.variants.get(operation, []):
            if v.name == variant_name:
                if converted:
                    v.conversions += 1
                if quality_score is not None:
                    v.quality_scores.append(quality_score)
                    v.quality_scores = v.quality_scores[-100:]  # Keep last 100
                break
        self._save_variants()

    def register_variant(
        self, operation: str, name: str, prompt_template: str,
        parent: str = "original",
    ) -> None:
        """Register a new prompt variant for testing."""
        if operation not in self.variants:
            self.variants[operation] = []

        # Don't duplicate
        if any(v.name == name for v in self.variants[operation]):
            return

        variant = PromptVariant(name, operation, prompt_template, parent)
        self.variants[operation].append(variant)
        self._save_variants()
        logger.info("Registered prompt variant: %s/%s", operation, name)

    async def mutate_winner(self, operation: str) -> PromptVariant | None:
        """Create a new variant by mutating the winning prompt."""
        variants = self.variants.get(operation, [])
        if not variants:
            return None

        # Find the best performer
        best = max(variants, key=lambda v: v.conversion_rate if v.impressions >= 5 else 0)
        if best.impressions < 5:
            return None  # Not enough data yet

        result = await self.llm.ask_json(
            f"You are ARCANA AI evolving a prompt that works well.\n\n"
            f"Operation: {operation}\n"
            f"Current best prompt (conv rate: {best.conversion_rate:.1%}):\n"
            f"{best.prompt_template}\n\n"
            f"Create a MUTATION — a variation that might perform even better.\n"
            f"Change the tone, structure, specificity, or persuasion technique.\n"
            f"Keep the core intent but try a different angle.\n\n"
            f"Return JSON: {{\n"
            f'  "variant_name": str (descriptive, snake_case),\n'
            f'  "prompt_template": str (the mutated prompt),\n'
            f'  "mutation_type": str (what you changed and why),\n'
            f'  "hypothesis": str (why this might work better)\n'
            f"}}",
            tier=Tier.SONNET,
        )

        name = result.get("variant_name", f"{operation}_mutation_{len(variants)}")
        template = result.get("prompt_template", "")
        if not template:
            return None

        new_variant = PromptVariant(name, operation, template, parent=best.name)
        self.variants[operation].append(new_variant)
        self._save_variants()

        self.memory.log(
            f"Prompt mutation: {operation}/{name}\n"
            f"Parent: {best.name} ({best.conversion_rate:.1%} conv)\n"
            f"Mutation: {result.get('mutation_type', 'N/A')}",
            "Prompt Evolution",
        )

        return new_variant

    async def evaluate_and_promote(self) -> dict[str, Any]:
        """Evaluate all variants, promote winners, retire losers."""
        promotions = []
        retirements = []
        mutations = []

        for operation, variants in self.variants.items():
            active = [v for v in variants if v.status == "testing" and v.impressions >= 10]
            if len(active) < 2:
                continue

            # Find winner and loser
            best = max(active, key=lambda v: v.conversion_rate)
            worst = min(active, key=lambda v: v.conversion_rate)

            # Promote if significantly better (>20% relative improvement)
            if best.conversion_rate > 0 and worst.conversion_rate > 0:
                improvement = (best.conversion_rate - worst.conversion_rate) / worst.conversion_rate
                if improvement > 0.2:
                    best.status = "winner"
                    worst.status = "retired"
                    promotions.append(f"{operation}: {best.name} wins ({best.conversion_rate:.1%})")
                    retirements.append(f"{operation}: {worst.name} retired ({worst.conversion_rate:.1%})")

                    # Mutate the winner to keep evolving
                    try:
                        new = await self.mutate_winner(operation)
                        if new:
                            mutations.append(f"{operation}: {new.name}")
                    except Exception as exc:
                        logger.error("Mutation failed for %s: %s", operation, exc)

        self._save_variants()

        result = {
            "promotions": promotions,
            "retirements": retirements,
            "mutations": mutations,
        }

        if promotions or retirements:
            self.memory.log(
                f"Prompt evolution cycle:\n"
                f"Promoted: {', '.join(promotions) or 'none'}\n"
                f"Retired: {', '.join(retirements) or 'none'}\n"
                f"New mutations: {', '.join(mutations) or 'none'}",
                "Prompt Evolution",
            )

        return result

    def get_evolution_stats(self) -> dict[str, Any]:
        """Get stats on all prompt evolution activity."""
        stats = {}
        for operation, variants in self.variants.items():
            stats[operation] = {
                "total_variants": len(variants),
                "active": len([v for v in variants if v.status == "testing"]),
                "winners": len([v for v in variants if v.status == "winner"]),
                "retired": len([v for v in variants if v.status == "retired"]),
                "best_conversion": max(
                    (v.conversion_rate for v in variants if v.impressions > 0),
                    default=0,
                ),
            }
        return stats
