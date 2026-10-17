"""Natural-language scenario assistant: a language model *proposes*, code *validates*, SUMO *measures*.

    "Simulate a rush-hour accident near the central intersection and test whether emergency-priority signaling helps."
        -> Proposal (structured JSON from Gemini, or from the offline rule parser when there is no API key)
        -> validate_proposal(): scenario, parameters, controllers, location and run budget checked against the real
           scenario catalog and network; unsupported or ambiguous requests come back as questions / errors
        -> ExecutionPlan: rebuilt by code from the *validated* fields only (nothing the model wrote is executed)
        -> run_plan(): paired SUMO experiments through the benchmark runner (worker processes)
        -> summarize(): a short explanation; every number in it must match a measured value, otherwise a deterministic
           summary built from the measurements replaces it. The measurements are always returned next to the text.

The model is never given a tool, a shell or a file path, and its output is parsed as data (``json.loads`` + a strict pydantic
model). Gemini access needs ``GEMINI_API_KEY``; without it the rule parser demonstrates the same pipeline (``source: "rules"``).
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .recorder import CONTROLLERS, controller_params

MAX_SEEDS = 5
MAX_DURATION_MIN = 15
MAX_RUNS = 12
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")  # model ids change; override with GEMINI_MODEL
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"


# --------------------------------------------------------------------------- what the model may produce
class Proposal(BaseModel):
    """The only shape the language model is allowed to return. Unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")

    intent: Literal["compare_controllers", "run_single", "clarify", "unsupported"]
    scenario: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    location: str | None = Field(None, description="a place name from the supplied list, or null")
    baseline_controller: str = "fixed"
    candidate_controllers: list[str] = Field(default_factory=list)
    seeds: list[int] = Field(default_factory=lambda: [1, 2, 3])
    duration_min: int | None = None
    explanation: str = ""
    assumptions: list[str] = Field(default_factory=list)
    clarification_questions: list[str] = Field(default_factory=list)


def proposal_json_schema() -> dict[str, Any]:
    """The JSON Schema sent to the model (Gemini structured output); validation is repeated locally either way."""
    return {
        "type": "object",
        "properties": {
            "intent": {"type": "string", "enum": ["compare_controllers", "run_single", "clarify", "unsupported"]},
            "scenario": {"type": "string", "nullable": True},
            "parameters": {"type": "object"},
            "location": {"type": "string", "nullable": True},
            "baseline_controller": {"type": "string"},
            "candidate_controllers": {"type": "array", "items": {"type": "string"}},
            "seeds": {"type": "array", "items": {"type": "integer"}},
            "duration_min": {"type": "integer", "nullable": True},
            "explanation": {"type": "string"},
            "assumptions": {"type": "array", "items": {"type": "string"}},
            "clarification_questions": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["intent", "explanation"],
    }


class LLM(Protocol):
    def generate_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]: ...
    def generate_text(self, system: str, user: str) -> str: ...


# --------------------------------------------------------------------------- Gemini transport
class GeminiError(RuntimeError):
    pass


class GeminiClient:
    """Minimal REST client. Structured output uses ``generateContent`` with ``responseMimeType`` + ``responseSchema``
    (``GEMINI_API_MODE=interactions`` selects the newer ``interactions`` endpoint with ``response_format``). The key is
    sent in the ``x-goog-api-key`` header only and never logged or returned."""

    def __init__(self, api_key: str | None = None, model: str | None = None, *, base_url: str = GEMINI_BASE, timeout: float = 60.0,
                 transport: httpx.BaseTransport | None = None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        self.model = model or DEFAULT_MODEL
        self.base_url = base_url.rstrip("/")
        self.mode = os.environ.get("GEMINI_API_MODE", "generateContent")
        self._client = httpx.Client(timeout=timeout, transport=transport)

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _post(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise GeminiError("GEMINI_API_KEY is not set")
        r = self._client.post(url, json=body, headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"})
        if r.status_code != 200:
            try:
                msg = r.json().get("error", {}).get("message", "")
            except ValueError:
                msg = r.text[:200]
            raise GeminiError(f"Gemini API returned {r.status_code}: {msg[:300]}")
        return r.json()

    def generate_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        if self.mode == "interactions":
            data = self._post(f"{self.base_url}/interactions", {"model": self.model, "system_instruction": system, "input": user,
                                                                "response_format": {"type": "text", "mime_type": "application/json", "schema": schema}})
            text = _interaction_text(data)
        else:
            data = self._post(f"{self.base_url}/models/{self.model}:generateContent", {
                "systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"temperature": 0, "responseMimeType": "application/json", "responseSchema": schema}})
            text = _candidate_text(data)
        try:
            return json.loads(text)
        except ValueError as exc:
            raise GeminiError(f"the model did not return JSON: {text[:120]!r}") from exc

    def generate_text(self, system: str, user: str) -> str:
        if self.mode == "interactions":
            return _interaction_text(self._post(f"{self.base_url}/interactions", {"model": self.model, "system_instruction": system, "input": user}))
        return _candidate_text(self._post(f"{self.base_url}/models/{self.model}:generateContent", {
            "systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": user}]}], "generationConfig": {"temperature": 0.2}}))


def _candidate_text(data: dict[str, Any]) -> str:
    try:
        return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError, TypeError) as exc:
        raise GeminiError(f"unexpected Gemini response shape: {list(data)[:5]}") from exc


def _interaction_text(data: dict[str, Any]) -> str:
    for key in ("output_text", "text"):
        if isinstance(data.get(key), str):
            return data[key]
    outs = data.get("outputs") or data.get("output") or []
    if isinstance(outs, list):
        return "".join(o.get("text", "") for o in outs if isinstance(o, dict))
    raise GeminiError(f"unexpected Gemini interactions response shape: {list(data)[:5]}")


# --------------------------------------------------------------------------- the world the assistant may act in
@dataclass
class World:
    """Supported scenarios, controllers and places, taken from the running code (never from the model)."""

    scenarios: dict[str, dict[str, Any]]
    places: dict[str, dict[str, Any]]  # name -> {"node": id, "edges": [edge ids, nearest first]}
    central: str

    def catalog_text(self) -> str:
        lines = []
        for sid, s in self.scenarios.items():
            ps = []
            for p in s["params"]:
                if p["key"] in ("duration", "signals"):
                    continue
                desc = p["key"] + ":" + p["type"]
                if p.get("options"):
                    desc += "=" + "|".join(str(o["value"]) for o in p["options"])
                elif p.get("min") is not None:
                    desc += f"[{p['min']:g}..{p['max']:g}]"
                if p["key"] == "location":
                    desc += "(use the 'location' field with a place name)"
                ps.append(desc)
            lines.append(f"- {sid}: {s['tagline']}. parameters: {', '.join(ps)}")
        return "\n".join(lines)


def load_world(network: str = "district") -> World:
    from ..api.service import NETWORKS_DIR
    from ..network.info import load_net_info
    from ..scenarios.catalog import list_specs

    net = load_net_info(str(NETWORKS_DIR / f"{network}.net.xml"), network)
    specs = {s["id"]: s for s in list_specs()}
    places: dict[str, dict[str, Any]] = {}
    for node, name in net.junction_names.items():
        x, y = net.node_pos.get(node, (0.0, 0.0))
        near = [e for e in net.block_edges(1) if net.edges[e].to == node or net.edges[e].frm == node]
        near.sort(key=lambda e: (math.dist(net.node_pos[net.edges[e].to], (x, y)), e))
        if near:
            places[name] = {"node": node, "edges": near}
    focus = net.city.get("focus") or [300, 240]
    central = min(places, key=lambda n: math.dist(net.node_pos[places[n]["node"]], focus))
    return World(specs, places, central)


# --------------------------------------------------------------------------- validation
@dataclass
class ExecutionPlan:
    scenario: str
    params: dict[str, Any]
    controllers: list[str]
    baseline: str
    seeds: list[int]
    location_edge: str | None = None
    location_name: str | None = None

    def to_json(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class Validation:
    status: Literal["ok", "needs_clarification", "invalid", "unsupported"]
    plan: ExecutionPlan | None = None
    errors: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def validate_proposal(p: Proposal, world: World) -> Validation:
    """Everything the run depends on is checked here; a model cannot add an option that is not checked."""
    from ..scenarios.base import coerce

    if p.intent == "unsupported":
        return Validation("unsupported", errors=[p.explanation or "this request is not something FlowState can simulate"])
    if p.intent == "clarify" or not p.scenario:
        return Validation("needs_clarification", questions=p.clarification_questions or ["Which situation should be simulated (accident, rush hour, ambulance call, ...)?"])
    if p.scenario not in world.scenarios:
        return Validation("invalid", errors=[f"unknown scenario '{p.scenario}'; supported: {sorted(world.scenarios)}"])
    spec = {x["key"]: x for x in world.scenarios[p.scenario]["params"]}
    errors, notes, questions = [], [], list(p.clarification_questions)
    params: dict[str, Any] = {}
    for k, v in p.parameters.items():
        if k not in spec:
            errors.append(f"'{k}' is not a parameter of '{p.scenario}' (valid: {sorted(spec)})")
            continue
        if k in ("signals", "preemption", "tsp"):
            errors.append(f"'{k}' is set by the controller, not as a parameter")
            continue
        if k == "location":
            errors.append("give the place in 'location', not in parameters")
            continue
        try:
            from ..scenarios.base import ParamSpec

            params[k] = coerce(ParamSpec(**spec[k]), v)
        except (ValueError, TypeError) as exc:
            errors.append(f"{k}: {exc}")
    loc_edge = loc_name = None
    if "location" in spec:
        name = p.location
        if name:
            hit = _match_place(name, world)
            if hit is None:
                errors.append(f"unknown place '{name}'; known intersections: {sorted(world.places)[:12]}")
            else:
                loc_name = hit
                loc_edge = world.places[hit]["edges"][0]
                notes.append(f"'{name}' resolved to {hit}; the {p.scenario} is placed on the nearest street segment {loc_edge}")
                params["location"] = loc_edge
        else:
            notes.append("no location given: the scenario draws one from the seed")
    elif p.location:
        notes.append(f"scenario '{p.scenario}' has no location option; '{p.location}' ignored")
    controllers = [c for c in dict.fromkeys([p.baseline_controller, *p.candidate_controllers]) if c]
    for c in controllers:
        if c not in CONTROLLERS:
            errors.append(f"unknown controller '{c}' (supported: {sorted(CONTROLLERS)})")
    if p.intent == "compare_controllers" and len(controllers) < 2:
        questions.append("Which controller should be compared against the baseline (e.g. emergency pre-emption, transit priority, actuated)?")
    seeds = list(dict.fromkeys(p.seeds)) or [1]
    if not all(isinstance(s, int) and 0 <= s <= 99999 for s in seeds) or len(seeds) > MAX_SEEDS:
        errors.append(f"between 1 and {MAX_SEEDS} seeds, each 0..99999")
    duration = p.duration_min or int(spec.get("duration", {}).get("default") or 5)
    allowed = [o["value"] for o in (spec.get("duration", {}).get("options") or [])]
    if duration > MAX_DURATION_MIN or (allowed and duration not in allowed):
        errors.append(f"run length must be one of {[a for a in allowed if a <= MAX_DURATION_MIN]} minutes")
    params["duration"] = duration
    if len(controllers) * max(1, len(seeds)) > MAX_RUNS:
        errors.append(f"at most {MAX_RUNS} runs (controllers x seeds)")
    if not errors:
        from ..scenarios.catalog import create

        for c in controllers:  # a controller the scenario cannot express, or a value the scenario rejects, is refused here
            try:
                create(p.scenario, controller_params(p.scenario, c, params))
            except ValueError as exc:
                errors.append(str(exc))
    if errors:
        return Validation("invalid", errors=errors, notes=notes)
    if questions:
        return Validation("needs_clarification", questions=questions, notes=notes)
    return Validation("ok", ExecutionPlan(p.scenario, params, controllers or ["fixed"], p.baseline_controller if p.baseline_controller in controllers else (controllers or ["fixed"])[0],
                                          seeds, loc_edge, loc_name), notes=notes)


def _match_place(name: str, world: World) -> str | None:
    n = re.sub(r"[^a-z0-9& ]", " ", name.lower()).strip()
    if n in ("central intersection", "the central intersection", "centre", "center", "city centre", "downtown", "central"):
        return world.central
    for k in world.places:
        if n == k.lower():
            return k
    words = {w for w in re.split(r"[ &]+", n) if w and w not in ("street", "st", "avenue", "ave", "and", "the")}
    best = max(world.places, key=lambda k: len(words & {w for w in re.split(r"[ &]+", k.lower()) if w not in ("street", "avenue", "and")}), default=None)
    if best and len(words) >= 2 and words <= {w for w in re.split(r"[ &]+", best.lower())}:
        return best
    return None


# --------------------------------------------------------------------------- proposing
SYSTEM = """You turn a traffic-planning request into a structured FlowState experiment proposal.
You may ONLY choose from the scenarios, parameters, controllers and places listed below. You cannot run anything; your JSON is
checked by code and only validated options are executed. If the request is ambiguous, set intent=clarify and ask short questions.
If it cannot be simulated at all, set intent=unsupported and say why. Never invent results or numbers.

Scenarios:
{catalog}

Controllers (signal control): {controllers}. 'fixed' is the baseline. Emergency-priority signalling is 'fixed+preempt' (only scenarios that
have a 'preemption' parameter); transit signal priority is 'fixed+tsp' (transit scenario).
Places (intersections; the central intersection is "{central}"): {places}
Rules: baseline_controller is the comparison reference; candidate_controllers are the ones being tested; use 3 seeds unless told otherwise;
duration_min must be one of 5, 10 or 15. A "rush hour" accident has no dedicated scenario: use the accident scenario with demand around 2400-2800
and say so in assumptions. Put the place in 'location', never in parameters. Use only the listed parameter names."""


def propose_with_llm(text: str, world: World, llm: LLM) -> Proposal:
    system = SYSTEM.format(catalog=world.catalog_text(), controllers=", ".join(sorted(CONTROLLERS)), central=world.central, places=", ".join(sorted(world.places)))
    raw = llm.generate_json(system, text, proposal_json_schema())
    return Proposal.model_validate(raw)  # raises ValidationError if the model added or malformed anything


def propose_with_rules(text: str, world: World) -> Proposal:
    """Offline fallback: keyword rules. Narrower than the model, but exercises the same validation and execution path."""
    t = text.lower()
    scenario = next((s for s, kw in (("accident", ("accident", "crash", "collision")), ("ambulance", ("ambulance", "paramedic", "ems")), ("transit", ("bus", "transit", "streetcar")),
                                     ("stadium", ("stadium", "game", "concert")), ("school", ("school",)), ("rush_hour", ("rush", "commut", "peak")),
                                     ("weather", ("snow", "rain", "fog", "storm", "weather")), ("construction", ("construction", "road work", "roadwork")),
                                     ("bike_lanes", ("bike lane", "cycling lane", "bicycle lane"))) if any(k in t for k in kw)), None)
    if scenario is None:
        return Proposal(intent="clarify", clarification_questions=["Which situation should be simulated: an accident, rush hour, an ambulance call, a stadium event, ...?"],
                        explanation="I could not tell which scenario you mean.")
    params: dict[str, Any] = {}
    assumptions: list[str] = []
    if "rush" in t and scenario == "accident":
        params["demand"] = 2600
        assumptions.append("there is no combined rush-hour accident scenario: modelled as an accident with rush-hour-level background demand (2600 trips/h)")
    cands: list[str] = []
    if any(k in t for k in ("emergency", "pre-empt", "preempt", "priority")) and "transit" not in t and "bus" not in t:
        cands.append("fixed+preempt")
    if any(k in t for k in ("transit priority", "bus priority", "tsp")):
        cands.append("fixed+tsp")
    if "actuated" in t or "adaptive" in t:
        cands.append("actuated")
    location = None
    if any(k in t for k in ("central", "downtown", "center", "centre")):
        location = "central intersection"
    else:
        for name in world.places:
            if name.lower() in t:
                location = name
                break
    if not cands:
        return Proposal(intent="clarify", scenario=scenario, parameters=params, location=location, assumptions=assumptions,
                        clarification_questions=["Which control strategy should be tested against fixed-time signals: emergency pre-emption, transit priority or actuated control?"],
                        explanation="The scenario is clear but no control strategy to compare was named.")
    return Proposal(intent="compare_controllers", scenario=scenario, parameters=params, location=location, baseline_controller="fixed", candidate_controllers=cands,
                    seeds=[1, 2, 3], duration_min=10 if scenario in ("ambulance", "accident") else 5, assumptions=assumptions,
                    explanation=f"Compare {', '.join(cands)} with fixed-time signals on the {scenario} scenario (rule-based interpretation).")


@dataclass
class PlanResult:
    source: Literal["gemini", "rules"]
    proposal: Proposal | None
    validation: Validation
    llm_error: str | None = None

    def to_json(self) -> dict[str, Any]:
        v = self.validation
        return {"source": self.source, "status": v.status, "proposal": self.proposal.model_dump() if self.proposal else None, "plan": v.plan.to_json() if v.plan else None,
                "errors": v.errors, "questions": v.questions, "notes": v.notes, "llmError": self.llm_error}


def plan_request(text: str, world: World | None = None, llm: LLM | None = None) -> PlanResult:
    world = world or load_world()
    text = (text or "").strip()[:2000]
    if not text:
        return PlanResult("rules", None, Validation("needs_clarification", questions=["What would you like to simulate?"]))
    llm = llm if llm is not None else (GeminiClient() if (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")) else None)
    err = None
    if llm is not None and getattr(llm, "available", True):
        try:
            prop = propose_with_llm(text, world, llm)
            return PlanResult("gemini", prop, validate_proposal(prop, world))
        except (GeminiError, ValidationError, httpx.HTTPError) as exc:
            err = f"{type(exc).__name__}: {str(exc).splitlines()[0][:200] if str(exc) else ''}"  # no key material is ever in these messages
    prop = propose_with_rules(text, world)
    return PlanResult("rules", prop, validate_proposal(prop, world), llm_error=err)


# --------------------------------------------------------------------------- running and explaining
def run_plan(plan: ExecutionPlan, suite: str, *, workers: int = 4, warmup: float = 90.0) -> dict[str, Any]:
    """Run the validated plan as paired SUMO experiments and return the comparison document."""
    from .benchmark import run_benchmark

    manifest = run_benchmark(suite, plan.scenario, plan.controllers, plan.seeds, plan.params, baseline=plan.baseline, warmup=warmup, workers=workers)
    out = json.loads((_bench_dir(suite) / "comparisons.json").read_text(encoding="utf-8"))
    return {"manifest": {k: manifest[k] for k in ("suite", "scenario", "controllers", "seeds", "wallSeconds", "reproduce")}, "comparisons": out}


def _bench_dir(suite: str):
    from .benchmark import BENCH_DIR

    return BENCH_DIR / suite


KEY_METRICS = ["mean_travel_s", "mean_time_loss_s", "mean_wait_s", "throughput_per_h", "co2_total_g", "idle_co2_g", "collisions", "teleports", "peak_queue"]


def measured_facts(comparisons: dict[str, Any]) -> dict[str, Any]:
    """The numbers a summary may quote, flattened from the measured comparison (means over seeds, paired deltas)."""
    facts: dict[str, Any] = {"baseline": comparisons.get("baseline"), "controllers": {}}
    for ctrl, e in comparisons.get("controllers", {}).items():
        item = {"runs": e["n"], "seeds": e["seeds"], "mean": {m: e["metrics"][m]["mean"] for m in KEY_METRICS if e["metrics"].get(m) and e["metrics"][m]["mean"] is not None}}
        if "deltas" in e:
            item["pairedDelta"] = {m: {"meanDelta": d["meanDelta"], "pctOfBaseline": d["pctOfBaseline"], "n": d["n"], "improvedInEverySeed": d["improvedInEverySeed"]}
                                   for m, d in e["deltas"].items() if m in KEY_METRICS}
        activity = {k.split(".", 1)[1]: v["mean"] for k, v in (e.get("scenarioMetrics") or {}).items() if k.startswith("signals.") and v.get("mean") is not None}
        if activity:
            item["signalActivity"] = activity  # what the controller actually did (pre-emptions granted, priority extensions, ...)
        sm = {k: v["mean"] for k, v in (e.get("scenarioMetrics") or {}).items() if not k.startswith("signals.") and v.get("mean") is not None}
        if sm:
            item["scenarioResults"] = sm
        facts["controllers"][ctrl] = item
    scenario_extra = comparisons.get("scenarioResults")
    if scenario_extra:
        facts["scenarioResults"] = scenario_extra
    return facts


def _numbers_in(obj: Any) -> list[float]:
    out: list[float] = []
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        out.append(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            out += _numbers_in(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out += _numbers_in(v)
    return out


def numbers_are_grounded(text: str, facts: dict[str, Any]) -> tuple[bool, list[str]]:
    """Every number in ``text`` must equal a measured value (to the precision it is quoted at, or as a percentage / absolute value of one)."""
    allowed = _numbers_in(facts)
    allowed += [abs(x) for x in allowed]
    bad = []
    for m in re.finditer(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(%?)", re.sub(r"(?<=\d),(?=\d{3})", "", text)):
        val = float(m.group(1))
        dec = len(m.group(1).split(".")[1]) if "." in m.group(1) else 0
        tol = 0.5 * 10 ** (-dec) * (1.0001)
        if dec == 0 and not m.group(2) and 1 <= val <= MAX_SEEDS:
            continue  # small counts ("3 seeds", "2 controllers") are not measurements
        if not any(abs(a - val) <= tol for a in allowed):
            bad.append(m.group(0).strip())
    return (not bad), bad


def deterministic_summary(plan: ExecutionPlan, facts: dict[str, Any]) -> str:
    base = facts["baseline"]
    lines = [f"{plan.scenario}: {', '.join(c for c in plan.controllers if c != base)} against {base}, {len(plan.seeds)} seed(s) each (same situation per seed)."]
    for ctrl, e in facts["controllers"].items():
        if ctrl == base or "pairedDelta" not in e:
            continue
        parts = []
        for m, label, unit in (("mean_travel_s", "mean travel time", " s"), ("co2_total_g", "simulated CO2", " g"), ("collisions", "collisions", "")):
            d = e["pairedDelta"].get(m)
            if d:
                pct = f" ({d['pctOfBaseline']:+.1f}%)" if d["pctOfBaseline"] is not None else ""
                parts.append(f"{label} {d['meanDelta']:+.1f}{unit}{pct}")
        lines.append(f"{ctrl}: " + "; ".join(parts) + f" (mean paired difference over {e.get('runs')} seed(s)).")
        act = e.get("signalActivity") or {}
        for key, strategy in (("preemptions", "emergency pre-emption"), ("tspExtensions", "transit signal priority")):
            if strategy.split()[0] in ctrl.replace("preempt", "emergency").replace("tsp", "transit") and key in act and act[key] == 0:
                lines.append(f"{strategy.capitalize()} was never triggered in these runs ({key} = 0), so no difference attributable to it is expected; try a longer run or a scenario that dispatches emergency vehicles.")
    return " ".join(lines)


def summarize(plan: ExecutionPlan, comparisons: dict[str, Any], llm: LLM | None = None) -> dict[str, Any]:
    """Narrative for the result. The model may only rephrase ``facts``; a summary with an unmeasured number is discarded."""
    facts = measured_facts(comparisons)
    text, source, rejected = deterministic_summary(plan, facts), "measurements", []
    if llm is not None and getattr(llm, "available", True):
        try:
            draft = llm.generate_text("Summarise this traffic experiment for a city planner in 3-5 sentences. Use ONLY numbers that appear in the JSON, quoted exactly or as the given percentage. "
                                      "State clearly that CO2 values are simulated estimates and that differences are paired, same-seed comparisons. Do not speculate beyond the data.",
                                      json.dumps(facts, default=str))
            ok, bad = numbers_are_grounded(draft, facts)
            if ok and draft.strip():
                text, source = draft.strip(), "gemini"
            else:
                rejected = bad
        except (GeminiError, httpx.HTTPError):
            pass
    return {"summary": text, "summarySource": source, "rejectedNumbers": rejected, "measured": facts,
            "caveat": "Simulated results on FlowState's synthetic district; emissions are HBEFA3 model estimates, not measurements of real emissions."}
