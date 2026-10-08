# Household Energy Optimizer and Usage Simulator

CSS142-P / AM2, by Benitez & Vinoya. You enter a monthly electricity budget (PHP), your appliances and their priorities. The tool returns the maximum hours each appliance can run as a daily schedule, using Meralco's September 2026 residential rates. The bill is guaranteed to stay within the budget.

The full write-up is in [report/Research_Report.md](report/Research_Report.md).

## Setup

    pip install -r requirements.txt

## Get a schedule

**Desktop window (easiest):**

    python gui.py                         # or: python main.py --gui

Pick a household, set the budget and press **Calculate**. The tabs show the schedule, the appliance list (add, edit, delete, reorder priorities), the Meralco bill, charts and a budget simulator.

**Command line:**

    python main.py --household median --budget 2000
    python main.py --household high --budget 4000 --csv my_plan.csv
    python main.py --household custom --budget 3000 --mode weighted
    python main.py --household households/my_home.json --budget 2500
    python main.py --interactive          # build your own household step by step

Options:

| Option | Meaning |
|---|---|
| `--mode strict` | Default. The lowest priority is always sacrificed first. |
| `--mode weighted` | Maximizes priority-weighted comfort. |
| `--days` | Days in the billing month (default 30). |
| `--step` | Rounding step in hours (default 0.25; 0 = no rounding). |
| `--lifeline`, `--senior`, `--lft` | Lifeline customer, senior-citizen discount (both only apply up to 100 kWh a month), and local franchise tax in PHP/kWh. |

Household JSON format: see [households/custom_example.json](households/custom_example.json) and Appendix B of the report.

## Reproduce the research results

| Command | Methodology step | Output |
|---|---|---|
| `python hecs_data.py` | 2: Collect data | `data/`, `households/` from the HECS 2011 files in `Datasets/` |
| `python -m unittest discover -s tests -t .` | 5: Verify | 27 automated tests |
| `python validate.py` | 5: Validate | `results/validation_report.md` |
| `python scenarios.py` | 6: Analyze | `results/scenarios.csv`, `scenario_summary.md`, `fig*.png` |

## Files

| File | Purpose |
|---|---|
| `meralco_rates.py` | Meralco residential tariff: bill for a given kWh, and the kWh cap for a budget |
| `appliances.py` | Appliance and household inputs, HECS catalogue lookup |
| `optimizer.py` | The optimization model: a linear program (see its docstring) |
| `schedule.py` | Turns the optimal hours into clock-time blocks |
| `main.py` | Command-line tool |
| `gui.py` | Desktop window (Tkinter + matplotlib) |
| `hecs_data.py` | Builds the appliance catalogue and sample households from HECS 2011 |
| `lp_reference.py` | scipy LP solver, used only to verify optimality |
| `validate.py`, `scenarios.py`, `tests/` | Validation and analysis |
