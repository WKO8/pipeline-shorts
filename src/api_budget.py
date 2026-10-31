"""Conservative per-video API reservations, shared across provider fallbacks."""
from __future__ import annotations

from dataclasses import dataclass, field


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class ApiBudget:
    limit_usd: float
    reserved_usd: float = 0.0
    calls: list = field(default_factory=list)

    def reserve(self, model, text, max_output_tokens, input_rate, output_rate):
        if self.limit_usd <= 0 or input_rate <= 0 or output_rate <= 0:
            raise BudgetExceeded('Paid calls disabled or model pricing not configured')
        # UTF-8 bytes deliberately overestimate normal text token counts.
        input_bound = len(text.encode('utf-8')) + 1024
        estimate = (input_bound * input_rate + max_output_tokens * output_rate) / 1_000_000
        if self.reserved_usd + estimate > self.limit_usd:
            raise BudgetExceeded(f'API budget would exceed US$ {self.limit_usd:.4f}; use local curation')
        self.reserved_usd += estimate
        record = dict(model=model, input_token_bound=input_bound,
                      output_token_limit=max_output_tokens, reserved_usd=estimate)
        self.calls.append(record)
        # Keep reservations on errors/timeouts; the provider may have billed them.
        return record
