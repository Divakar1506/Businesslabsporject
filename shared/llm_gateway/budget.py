"""Budget policy: picks the model for a call and blocks calls that would break a hard limit.

Rules, checked for the task's own limits and the global limits:
- spend + this call's estimate above a limit  -> BudgetExceeded (callers return HTTP 429)
- spend at or above soft_pct of a limit       -> downgrade to the cheapest model
- "cheapest_only" switch on                    -> cheapest model for every call
Limits left empty (None) are not enforced.

On top of the dashboard limits, HARD_MONTHLY_CAP_USD (env, default $0) caps paid
Claude spend for the month across all tasks. At $0 no paid call can run. The dashboard cannot raise it; only
the environment can. Free calls (offline / Ollama) skip the dollar checks.
"""
import os
from dataclasses import dataclass

from . import pricing
from .store import UsageStore, day_start, month_start

KNOWN_TASKS = ("knowledge", "inbox", "scope")
LIMIT_KEYS = ("daily_usd", "monthly_usd", "daily_tokens", "monthly_tokens")

DEFAULT_SETTINGS = {
    "global": {"daily_usd": None, "monthly_usd": None, "daily_tokens": None, "monthly_tokens": None,
               "soft_pct": 80, "cheapest_only": False, "default_model": None},
    "tasks": {t: {"daily_usd": None, "monthly_usd": None, "daily_tokens": None, "monthly_tokens": None,
                  "model": None} for t in KNOWN_TASKS},
}

# A preset the user can apply from the dashboard: cheap models for routine work.
COST_OPTIMIZED_ROUTING = {"knowledge": "claude-haiku-5-5", "inbox": "claude-haiku-5-5", "scope": "claude-sonnet-5-5"}


def hard_cap_usd() -> float:
    return float(os.environ.get("HARD_MONTHLY_CAP_USD", "0"))


class BudgetExceeded(Exception):
    def __init__(self, task: str, message: str):
        super().__init__(message)
        self.task = task


@dataclass
class Decision:
    model: str
    reason: str = ""


def load_settings(store: UsageStore) -> dict:
    saved = store.get_setting("budget", {}) or {}
    merged = {"global": {**DEFAULT_SETTINGS["global"], **saved.get("global", {})}, "tasks": {}}
    for t in set(DEFAULT_SETTINGS["tasks"]) | set(saved.get("tasks", {})):
        merged["tasks"][t] = {**DEFAULT_SETTINGS["tasks"].get(t, DEFAULT_SETTINGS["tasks"]["knowledge"]),
                              **saved.get("tasks", {}).get(t, {})}
    return merged


def validate_settings(settings: dict) -> dict:
    """Normalise settings sent by the dashboard. Raises ValueError on bad input."""
    out = {"global": {}, "tasks": {}}
    g = settings.get("global", {})
    for k in LIMIT_KEYS:
        out["global"][k] = _limit(g.get(k), k)
    soft = g.get("soft_pct", 80)
    if not isinstance(soft, (int, float)) or not 1 <= soft <= 100:
        raise ValueError("soft_pct must be between 1 and 100")
    out["global"]["soft_pct"] = soft
    out["global"]["cheapest_only"] = bool(g.get("cheapest_only", False))
    out["global"]["default_model"] = _model(g.get("default_model"))
    for task, cfg in settings.get("tasks", {}).items():
        out["tasks"][task] = {k: _limit(cfg.get(k), k) for k in LIMIT_KEYS}
        out["tasks"][task]["model"] = _model(cfg.get("model"))
    return out


def _limit(v, name):
    if v in (None, ""):
        return None
    if not isinstance(v, (int, float)) or v < 0:
        raise ValueError(f"{name} must be a non-negative number or empty")
    return v


def _model(m):
    if m in (None, ""):
        return None
    if not pricing.known(m):
        raise ValueError(f"unknown model {m}")
    return m


class BudgetPolicy:
    def __init__(self, store: UsageStore, default_model: str):
        self.store = store
        self.default_model = default_model

    def _scopes(self, task: str, settings: dict):
        yield task, settings["tasks"].get(task, {})
        yield None, settings["global"]

    def status(self, task: str | None, limits: dict) -> list[dict]:
        """Spend vs. each set limit for a task (None = all tasks)."""
        now_day, now_month = day_start(), month_start()
        d_usd, d_tok = self.store.spend(task, now_day)
        m_usd, m_tok = self.store.spend(task, now_month)
        spent = {"daily_usd": d_usd, "monthly_usd": m_usd, "daily_tokens": d_tok, "monthly_tokens": m_tok}
        return [{"key": k, "limit": limits.get(k), "spent": spent[k],
                 "pct": round(100 * spent[k] / limits[k], 1) if limits.get(k) else None} for k in LIMIT_KEYS]

    def decide(self, task: str, requested_model: str | None, est_input_tokens: int, max_tokens: int,
               paid: bool = True) -> Decision:
        settings = load_settings(self.store)
        g = settings["global"]
        model = requested_model or settings["tasks"].get(task, {}).get("model") or g["default_model"] or self.default_model
        if g["cheapest_only"]:
            model = pricing.CHEAPEST_MODEL
        # Worst case for the hard checks: the full max_tokens is generated.
        est_usd = pricing.call_cost(model, input_tokens=est_input_tokens, output_tokens=max_tokens) if paid else 0.0
        est_tok = est_input_tokens + max_tokens
        if paid:
            spent, _ = self.store.spend(None, month_start())
            if spent + est_usd > hard_cap_usd():
                raise BudgetExceeded(task, f"hard monthly cap ${hard_cap_usd():.2f} would be exceeded "
                                           f"(spent ${spent:.4f}, this call up to ${est_usd:.4f})")
        if g["cheapest_only"]:
            return Decision(model, "cheapest-only switch is on")
        downgrade = ""
        for scope, limits in self._scopes(task, settings):
            label = task if scope else "all tasks"
            for row in self.status(scope, limits):
                if row["limit"] is None:
                    continue
                if row["key"].endswith("usd") and not paid:
                    continue
                add = est_usd if row["key"].endswith("usd") else est_tok
                if row["spent"] + add > row["limit"]:
                    raise BudgetExceeded(task, f"{label}: {row['key']} limit {row['limit']} would be exceeded "
                                               f"(spent {row['spent']:.4g}, this call up to {add:.4g})")
                if row["pct"] is not None and row["pct"] >= g["soft_pct"]:
                    downgrade = f"{label}: {row['key']} at {row['pct']}% of limit"
        if downgrade and paid and model != pricing.CHEAPEST_MODEL:
            return Decision(pricing.CHEAPEST_MODEL, f"downgraded ({downgrade})")
        return Decision(model)

    def after_call(self, task: str) -> None:
        """Raise dashboard alerts when a limit crosses the soft threshold or 100%."""
        settings = load_settings(self.store)
        soft = settings["global"]["soft_pct"]
        for scope, limits in self._scopes(task, settings):
            for row in self.status(scope, limits):
                if row["pct"] is None:
                    continue
                kind = "usd" if row["key"].endswith("usd") else "tokens"
                name = scope or "all tasks"
                if row["pct"] >= 100:
                    self.store.add_alert(name, "critical", kind, f"{name}: {row['key']} reached {row['pct']}% of limit")
                elif row["pct"] >= soft:
                    self.store.add_alert(name, "warning", kind, f"{name}: {row['key']} at {row['pct']}% of limit")
