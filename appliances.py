"""
Household appliance inventory: the model's input data.

An appliance is either
  * "fixed"    - Always On (refrigerator, freezer). Always gets 24 h/day.
  * "variable" - On Demand (aircon, TV, fan, ...). The optimizer decides how
                 many hours per use-day it runs, between min_hours and
                 max_hours, in order of the user's priority (1 = most
                 important, sacrificed last).

Energy model (proposal limitation: average wattage, no dynamic draw):
    monthly kWh = watts x quantity x hours per use-day
                  x (days_per_week / 7) x days_in_month / 1000
"""

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CATALOGUE_CSV = ROOT / "data" / "appliance_catalogue.csv"

FIXED = "fixed"
VARIABLE = "variable"


@dataclass
class Appliance:
    name: str
    type: str = VARIABLE
    watts: float = 0.0
    quantity: int = 1
    priority: int | None = None
    min_hours: float = 0.0
    max_hours: float = 24.0
    days_per_week: float = 7.0
    catalogue: str | None = None

    @property
    def is_fixed(self):
        return self.type == FIXED

    def kwh_per_hour(self, days_in_month=30):
        """Monthly kWh added by running 1 more hour on each use-day."""
        return (self.watts * self.quantity / 1000
                * self.days_per_week / 7 * days_in_month)

    def monthly_kwh(self, hours, days_in_month=30):
        return self.kwh_per_hour(days_in_month) * hours

    def validate(self):
        if self.type not in (FIXED, VARIABLE):
            raise ValueError(f"{self.name}: type must be 'fixed' or 'variable'")
        if self.watts < 0 or self.quantity < 1:
            raise ValueError(f"{self.name}: watts must be >= 0 and quantity >= 1")
        if self.is_fixed:
            self.min_hours = self.max_hours = 24.0
            self.days_per_week = 7.0
            return
        if not 0 < self.days_per_week <= 7:
            raise ValueError(f"{self.name}: days_per_week must be in (0, 7]")
        if not 0 <= self.min_hours <= self.max_hours <= 24:
            raise ValueError(f"{self.name}: need 0 <= min_hours <= max_hours <= 24")


# ---------------------------------------------------------------------------
# Catalogue (median values from HECS 2011, built by hecs_data.py)
# ---------------------------------------------------------------------------
_catalogue_cache = None


def load_catalogue(path=CATALOGUE_CSV):
    global _catalogue_cache
    if _catalogue_cache is None or path != CATALOGUE_CSV:
        with open(path, newline="", encoding="utf-8") as f:
            rows = {r["name"]: r for r in csv.DictReader(f)}
        if path != CATALOGUE_CSV:
            return rows
        _catalogue_cache = rows
    return _catalogue_cache


def find_in_catalogue(name):
    """Exact (case-insensitive) match first, then substring match."""
    cat = load_catalogue()
    key = name.strip().lower()
    for n, row in cat.items():
        if n.lower() == key:
            return row
    matches = [row for n, row in cat.items() if key in n.lower()]
    if matches:
        return max(matches, key=lambda r: int(r["households"]))
    return None


def from_dict(d):
    """Build an Appliance, filling missing watts/hours from the catalogue."""
    name = str(d.get("name") or "").strip()
    if not name:
        raise ValueError("every appliance needs a name")
    d = {**d, "name": name}
    ref = find_in_catalogue(d.get("catalogue") or name)
    kind = d.get("type") or (ref["category"] if ref else VARIABLE)

    def pick(key, ref_key, default):
        if d.get(key) is not None:
            return d[key]
        if ref is not None:
            return float(ref[ref_key])
        return default

    watts = pick("watts", "median_watts", None)
    if watts is None:
        raise ValueError(f"{d['name']}: no wattage given and no catalogue match")
    app = Appliance(
        name=d["name"],
        type=kind,
        watts=float(watts),
        quantity=int(d.get("quantity", 1)),
        priority=d.get("priority"),
        min_hours=float(d.get("min_hours", 0.0)),
        max_hours=float(pick("max_hours", "median_hours_per_day", 24.0)),
        days_per_week=float(pick("days_per_week", "median_days_per_week", 7.0)),
        catalogue=ref["name"] if ref else d.get("catalogue"),
    )
    app.validate()
    return app


def to_dict(app):
    """Inverse of from_dict: every field the optimizer uses, filled in."""
    d = {"name": app.name, "catalogue": app.catalogue, "type": app.type,
         "watts": app.watts, "quantity": app.quantity}
    if not app.is_fixed:
        d.update(priority=app.priority, min_hours=app.min_hours,
                 max_hours=app.max_hours, days_per_week=app.days_per_week)
    return d


def assign_priorities(appliances):
    """Variable appliances without a priority go after those with one, in
    input order. Ties keep input order. Result: priorities 1..N, unique."""
    variables = [a for a in appliances if not a.is_fixed]
    order = sorted(enumerate(variables),
                   key=lambda t: (t[1].priority is None,
                                  t[1].priority if t[1].priority is not None else 0,
                                  t[0]))
    for rank, (_, app) in enumerate(order, start=1):
        app.priority = rank
    for a in appliances:
        if a.is_fixed:
            a.priority = None
    return appliances


@dataclass
class Household:
    name: str
    appliances: list = field(default_factory=list)
    source: str = ""

    @property
    def fixed(self):
        return [a for a in self.appliances if a.is_fixed]

    @property
    def variable(self):
        return sorted((a for a in self.appliances if not a.is_fixed),
                      key=lambda a: a.priority)


def load_household(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return household_from_dict(data)


def household_from_dict(data):
    apps = [from_dict(d) for d in data["appliances"]]
    names = [a.name for a in apps]
    if len(set(names)) != len(names):
        raise ValueError("appliance names must be unique within a household")
    assign_priorities(apps)
    return Household(name=data.get("name", "Household"), appliances=apps,
                     source=data.get("source", ""))
