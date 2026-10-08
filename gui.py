"""
Desktop Graphical User Interface (GUI) for Household Energy Optimizer & Usage Simulator.
Built with Tkinter, ttk, and Matplotlib.

Run with:
    python gui.py
  or
    python main.py --gui
"""

import json
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from appliances import (FIXED, VARIABLE, from_dict, household_from_dict,
                        load_catalogue, to_dict)
from meralco_rates import BillOptions
from optimizer import STRICT, WEIGHTED, optimize
from schedule import daily_schedule, hourly_load_kw

ROOT = Path(__file__).resolve().parent
HOUSEHOLDS_DIR = ROOT / "households"
# The HECS profiles are grouped by electricity consumption, not income.
DEFAULT_PRESET = "Typical Metro Manila (Median)"
PRESETS = {
    DEFAULT_PRESET: "median_household.json",
    "Low-consumption Household (HECS P25)": "low_household.json",
    "High-consumption Household (HECS P90, aircon)": "high_household.json",
    "Present-day Example (hand-entered)": "custom_example.json",
}


def normalized(data):
    """Validated copy of a household dict with every field filled in (wattage,
    hours and days from the HECS catalogue where missing), listed as fixed
    appliances first, then variable ones in priority order. The inventory
    table and the priority buttons rely on this order. Raises ValueError for
    an invalid household, before it replaces the current one."""
    hh = household_from_dict(data)
    out = {k: v for k, v in data.items() if k != "appliances"}
    out["appliances"] = [to_dict(a) for a in hh.fixed + hh.variable]
    return out


def unique_name(name, taken):
    base, k = name, 2
    while name in taken:
        name, k = f"{base} #{k}", k + 1
    return name


class EnergyOptimizerGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Household Energy Optimizer & Usage Simulator")
        self.root.geometry("1180x820")
        self.root.minsize(1000, 700)

        # Style configuration
        self.style = ttk.Style()
        try:
            self.style.theme_use("vista")  # Clean Windows theme
        except tk.TclError:
            pass

        self._configure_styles()

        # Application state
        self.catalogue = load_catalogue()
        self.current_household_data = self._load_preset_json(PRESETS[DEFAULT_PRESET])
        self.current_plan = None

        # Build UI layout
        self._build_header()
        self._build_main_layout()

        # Initial calculation
        self.run_optimization()

    def _configure_styles(self):
        self.style.configure(".", font=("Segoe UI", 9))
        self.style.configure("Header.TLabel", font=("Segoe UI", 14, "bold"), foreground="#0f172a")
        self.style.configure("SubHeader.TLabel", font=("Segoe UI", 9), foreground="#64748b")
        self.style.configure("KpiVal.TLabel", font=("Segoe UI", 14, "bold"))
        self.style.configure("KpiSub.TLabel", font=("Segoe UI", 8), foreground="#64748b")
        self.style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))
        self.style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))

    def _load_preset_json(self, filename):
        fpath = HOUSEHOLDS_DIR / filename
        if fpath.exists():
            with open(fpath, "r", encoding="utf-8") as f:
                return normalized(json.load(f))
        return {"name": "Default Household", "appliances": []}

    # ---------------------------------------------------------------------------
    # UI Layout Construction
    # ---------------------------------------------------------------------------
    def _build_header(self):
        header_frame = ttk.Frame(self.root, padding=(15, 10, 15, 5))
        header_frame.pack(fill=tk.X)

        title_lbl = ttk.Label(header_frame, text="⚡ Household Energy Optimizer", style="Header.TLabel")
        title_lbl.pack(anchor=tk.W)

        sub_lbl = ttk.Label(
            header_frame,
            text="Set your electricity budget and household appliances to compute an optimal daily usage plan.",
            style="SubHeader.TLabel",
        )
        sub_lbl.pack(anchor=tk.W)

        separator = ttk.Separator(self.root, orient=tk.HORIZONTAL)
        separator.pack(fill=tk.X, padx=15, pady=5)

    def _build_main_layout(self):
        paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=15, pady=5)

        # Left Control Sidebar
        sidebar = ttk.Frame(paned, padding=10, width=320)
        paned.add(sidebar, weight=0)

        # Right Main Content
        main_content = ttk.Frame(paned, padding=5)
        paned.add(main_content, weight=1)

        self._build_sidebar_controls(sidebar)
        self._build_dashboard_content(main_content)

    def _build_sidebar_controls(self, parent):
        # Step 1: Profile Frame
        prof_frame = ttk.LabelFrame(parent, text="Step 1: Household Profile", padding=10)
        prof_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(prof_frame, text="Select Household Profile:").pack(anchor=tk.W, pady=(0, 2))
        self.preset_var = tk.StringVar(value=DEFAULT_PRESET)
        preset_cb = ttk.Combobox(
            prof_frame,
            textvariable=self.preset_var,
            values=list(PRESETS.keys()),
            state="readonly",
        )
        preset_cb.pack(fill=tk.X, pady=(0, 8))
        preset_cb.bind("<<ComboboxSelected>>", self._on_preset_change)

        btn_box = ttk.Frame(prof_frame)
        btn_box.pack(fill=tk.X)
        ttk.Button(btn_box, text="Import Profile", command=self._import_json).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 2))
        ttk.Button(btn_box, text="Save Profile", command=self._export_json).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(2, 0))

        # Step 2: Budget Frame
        budget_frame = ttk.LabelFrame(parent, text="Step 2: Monthly Electricity Budget", padding=10)
        budget_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(budget_frame, text="Monthly Budget (PHP ₱):").pack(anchor=tk.W, pady=(0, 2))
        self.budget_var = tk.DoubleVar(value=3000.0)
        budget_spin = ttk.Spinbox(budget_frame, from_=0, to=100000, increment=100, textvariable=self.budget_var)
        budget_spin.pack(fill=tk.X, pady=(0, 6))

        # Quick budget chips
        quick_b_frame = ttk.Frame(budget_frame)
        quick_b_frame.pack(fill=tk.X, pady=(0, 8))
        for b_val in (1500, 3000, 4500, 6000):
            btn = ttk.Button(quick_b_frame, text=f"₱{b_val}", width=6,
                             command=lambda v=b_val: self._set_budget(v))
            btn.pack(side=tk.LEFT, expand=True, padx=1)

        # Advanced Settings Collapsible LabelFrame
        adv_frame = ttk.LabelFrame(parent, text="⚙ Advanced Tariff Options", padding=10)
        adv_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(adv_frame, text="Optimization Mode:").pack(anchor=tk.W, pady=(0, 2))
        self.mode_var = tk.StringVar(value=STRICT)
        mode_cb = ttk.Combobox(
            adv_frame,
            textvariable=self.mode_var,
            values=[STRICT, WEIGHTED],
            state="readonly",
        )
        mode_cb.pack(fill=tk.X, pady=(0, 6))

        ttk.Label(adv_frame, text="Billing Days in Month:").pack(anchor=tk.W, pady=(0, 2))
        self.days_var = tk.IntVar(value=30)
        ttk.Spinbox(adv_frame, from_=1, to=31, textvariable=self.days_var).pack(fill=tk.X, pady=(0, 6))

        self.lifeline_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(adv_frame, text="Lifeline Customer (up to 100 kWh)", variable=self.lifeline_var).pack(anchor=tk.W, pady=1)

        self.senior_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(adv_frame, text="Senior Citizen 5% (up to 100 kWh)", variable=self.senior_var).pack(anchor=tk.W, pady=1)

        ttk.Label(adv_frame, text="Local Franchise Tax (₱/kWh):").pack(anchor=tk.W, pady=(4, 2))
        self.lft_var = tk.DoubleVar(value=0.0)
        ttk.Entry(adv_frame, textvariable=self.lft_var).pack(fill=tk.X, pady=(0, 4))

        # Main Action Button
        run_btn = ttk.Button(parent, text="▶ CALCULATE OPTIMAL SCHEDULE", style="Accent.TButton", command=self.run_optimization)
        run_btn.pack(fill=tk.X, ipady=8, pady=(5, 0))

    def _build_dashboard_content(self, parent):
        # KPI Metric Cards Row
        kpi_frame = ttk.Frame(parent)
        kpi_frame.pack(fill=tk.X, pady=(0, 10))

        # KPI 1: Feasibility
        self.card_feas = ttk.LabelFrame(kpi_frame, text="Plan Status", padding=8)
        self.card_feas.pack(side=tk.LEFT, expand=True, fill=tk.BOTH, padx=(0, 4))
        self.lbl_feas_val = ttk.Label(self.card_feas, text="FEASIBLE", style="KpiVal.TLabel", foreground="#10b981")
        self.lbl_feas_val.pack()
        self.lbl_feas_sub = ttk.Label(self.card_feas, text="Budget covers energy", style="KpiSub.TLabel")
        self.lbl_feas_sub.pack()

        # KPI 2: Expected Bill
        self.card_bill = ttk.LabelFrame(kpi_frame, text="Expected Bill", padding=8)
        self.card_bill.pack(side=tk.LEFT, expand=True, fill=tk.BOTH, padx=4)
        self.lbl_bill_val = ttk.Label(self.card_bill, text="₱0.00", style="KpiVal.TLabel", foreground="#059669")
        self.lbl_bill_val.pack()
        self.lbl_bill_sub = ttk.Label(self.card_bill, text="Unused: ₱0.00", style="KpiSub.TLabel")
        self.lbl_bill_sub.pack()

        # KPI 3: Energy kWh
        self.card_kwh = ttk.LabelFrame(kpi_frame, text="Recommended Usage", padding=8)
        self.card_kwh.pack(side=tk.LEFT, expand=True, fill=tk.BOTH, padx=4)
        self.lbl_kwh_val = ttk.Label(self.card_kwh, text="0.0 kWh", style="KpiVal.TLabel")
        self.lbl_kwh_val.pack()
        self.lbl_kwh_sub = ttk.Label(self.card_kwh, text="0.00 kWh/day", style="KpiSub.TLabel")
        self.lbl_kwh_sub.pack()

        # KPI 4: Comfort Score
        self.card_comf = ttk.LabelFrame(kpi_frame, text="Comfort Delivered", padding=8)
        self.card_comf.pack(side=tk.LEFT, expand=True, fill=tk.BOTH, padx=(4, 0))
        self.lbl_comf_val = ttk.Label(self.card_comf, text="100%", style="KpiVal.TLabel", foreground="#2563eb")
        self.lbl_comf_val.pack()
        self.progress_comf = ttk.Progressbar(self.card_comf, length=100, mode="determinate")
        self.progress_comf.pack(fill=tk.X, pady=(2, 0))

        # Advice & Message Box
        self.msg_box = tk.Text(parent, height=5, wrap=tk.WORD, font=("Segoe UI", 9), bg="#f8fafc", fg="#334155", relief=tk.SOLID, bd=1)
        self.msg_box.pack(fill=tk.X, pady=(0, 10))

        # Tabs Notebook
        self.notebook = ttk.Notebook(parent)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        # Tab 1: Recommended Schedule
        self.tab_schedule = ttk.Frame(self.notebook, padding=5)
        self.notebook.add(self.tab_schedule, text="  📅 Recommended Schedule  ")

        # Tab 2: Appliance Inventory
        self.tab_inventory = ttk.Frame(self.notebook, padding=5)
        self.notebook.add(self.tab_inventory, text="  🔌 Appliance Inventory  ")

        # Tab 3: Bill Breakdown
        self.tab_bill = ttk.Frame(self.notebook, padding=5)
        self.notebook.add(self.tab_bill, text="  📄 Meralco Bill Details  ")

        # Tab 4: Hourly Load & Share Charts
        self.tab_charts = ttk.Frame(self.notebook, padding=5)
        self.notebook.add(self.tab_charts, text="  📊 Power Charts  ")

        # Tab 5: Budget Sensitivity Simulator
        self.tab_sim = ttk.Frame(self.notebook, padding=5)
        self.notebook.add(self.tab_sim, text="  📈 Budget Simulator  ")

        self._build_schedule_tab()
        self._build_inventory_tab()
        self._build_bill_tab()
        self._build_charts_tab()
        self._build_sim_tab()

    # ---------------------------------------------------------------------------
    # Notebook Tabs Implementation
    # ---------------------------------------------------------------------------
    def _build_schedule_tab(self):
        toolbar = ttk.Frame(self.tab_schedule)
        toolbar.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(toolbar, text="💡 Priority 1 is sacrificed last when budget is limited.", font=("Segoe UI", 8, "italic"), foreground="#64748b").pack(side=tk.LEFT)
        ttk.Button(toolbar, text="Export Schedule (CSV)", command=self._export_csv).pack(side=tk.RIGHT)

        cols = ("pri", "appliance", "type", "when", "hours_day", "monthly_kwh", "blocks", "status")
        self.sched_tree = ttk.Treeview(self.tab_schedule, columns=cols, show="headings", selectmode="browse")

        self.sched_tree.heading("pri", text="Pri")
        self.sched_tree.heading("appliance", text="Appliance Name")
        self.sched_tree.heading("type", text="Type")
        self.sched_tree.heading("when", text="Frequency")
        self.sched_tree.heading("hours_day", text="Hours / Day")
        self.sched_tree.heading("monthly_kwh", text="Monthly kWh")
        self.sched_tree.heading("blocks", text="Recommended Clock Times")
        self.sched_tree.heading("status", text="Status")

        self.sched_tree.column("pri", width=40, anchor=tk.CENTER)
        self.sched_tree.column("appliance", width=220)
        self.sched_tree.column("type", width=80, anchor=tk.CENTER)
        self.sched_tree.column("when", width=90, anchor=tk.CENTER)
        self.sched_tree.column("hours_day", width=90, anchor=tk.E)
        self.sched_tree.column("monthly_kwh", width=90, anchor=tk.E)
        self.sched_tree.column("blocks", width=180)
        self.sched_tree.column("status", width=110, anchor=tk.CENTER)

        vsb = ttk.Scrollbar(self.tab_schedule, orient=tk.VERTICAL, command=self.sched_tree.yview)
        self.sched_tree.configure(yscrollcommand=vsb.set)

        self.sched_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

    def _build_inventory_tab(self):
        toolbar = ttk.Frame(self.tab_inventory)
        toolbar.pack(fill=tk.X, pady=(0, 5))

        ttk.Button(toolbar, text="+ Add from Catalogue", command=self._open_catalogue_picker).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(toolbar, text="+ Add Custom Item", command=self._add_custom_appliance).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="Edit Selected", command=self._edit_selected_appliance).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="Priority Up ▲", command=self._move_pri_up).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="Priority Down ▼", command=self._move_pri_down).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="Delete Selected", command=self._delete_selected_appliance).pack(side=tk.RIGHT)

        cols = ("pri", "name", "type", "watts", "qty", "max_h", "min_h", "days")
        self.inv_tree = ttk.Treeview(self.tab_inventory, columns=cols, show="headings", selectmode="browse")

        self.inv_tree.heading("pri", text="Pri")
        self.inv_tree.heading("name", text="Appliance Name")
        self.inv_tree.heading("type", text="Type")
        self.inv_tree.heading("watts", text="Watts (W)")
        self.inv_tree.heading("qty", text="Qty")
        self.inv_tree.heading("max_h", text="Desired (h/d)")
        self.inv_tree.heading("min_h", text="Min (h/d)")
        self.inv_tree.heading("days", text="Days / Wk")

        self.inv_tree.column("pri", width=40, anchor=tk.CENTER)
        self.inv_tree.column("name", width=250)
        self.inv_tree.column("type", width=80, anchor=tk.CENTER)
        self.inv_tree.column("watts", width=90, anchor=tk.E)
        self.inv_tree.column("qty", width=60, anchor=tk.CENTER)
        self.inv_tree.column("max_h", width=100, anchor=tk.E)
        self.inv_tree.column("min_h", width=90, anchor=tk.E)
        self.inv_tree.column("days", width=90, anchor=tk.CENTER)

        vsb = ttk.Scrollbar(self.tab_inventory, orient=tk.VERTICAL, command=self.inv_tree.yview)
        self.inv_tree.configure(yscrollcommand=vsb.set)

        self.inv_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

    def _build_bill_tab(self):
        cols = ("component", "amount")
        self.bill_tree = ttk.Treeview(self.tab_bill, columns=cols, show="headings", selectmode="browse")

        self.bill_tree.heading("component", text="Meralco Tariff Rate Component Schedule (VAT-Inclusive)")
        self.bill_tree.heading("amount", text="Amount (PHP ₱)")

        self.bill_tree.column("component", width=450)
        self.bill_tree.column("amount", width=180, anchor=tk.E)

        vsb = ttk.Scrollbar(self.tab_bill, orient=tk.VERTICAL, command=self.bill_tree.yview)
        self.bill_tree.configure(yscrollcommand=vsb.set)

        self.bill_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

    def _build_charts_tab(self):
        self.fig_charts = Figure(figsize=(8, 5), dpi=100)
        self.ax_load = self.fig_charts.add_subplot(121)
        self.ax_share = self.fig_charts.add_subplot(122)

        self.canvas_charts = FigureCanvasTkAgg(self.fig_charts, master=self.tab_charts)
        self.canvas_charts.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    def _build_sim_tab(self):
        top_bar = ttk.Frame(self.tab_sim)
        top_bar.pack(fill=tk.X, pady=5)

        ttk.Label(top_bar, text="Test how bill & comfort change across monthly budgets (₱1k to ₱10k)").pack(side=tk.LEFT, padx=5)
        ttk.Button(top_bar, text="Generate Trade-Off Curve", command=self._run_simulation_chart).pack(side=tk.LEFT, padx=10)

        self.fig_sim = Figure(figsize=(8, 5), dpi=100)
        self.ax_sim_bill = self.fig_sim.add_subplot(111)
        # Created once and cleared on each run; calling twinx() on every
        # click would stack a new axis on top of the old ones.
        self.ax_sim_comf = self.ax_sim_bill.twinx()

        self.canvas_sim = FigureCanvasTkAgg(self.fig_sim, master=self.tab_sim)
        self.canvas_sim.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    # ---------------------------------------------------------------------------
    # Optimization & UI Update Logic
    # ---------------------------------------------------------------------------
    def run_optimization(self):
        try:
            budget = float(self.budget_var.get())
            mode = self.mode_var.get()
            days = int(self.days_var.get())
            opts = BillOptions(
                lifeline=self.lifeline_var.get(),
                senior_citizen=self.senior_var.get(),
                lft_per_kwh=float(self.lft_var.get()),
            )

            hh = household_from_dict(self.current_household_data)
            self.current_plan = optimize(hh, budget, mode=mode, options=opts, days_in_month=days, step=0.25)
            self._update_dashboard()
        except Exception as e:
            messagebox.showerror("Optimization Error", str(e))

    def _update_dashboard(self):
        plan = self.current_plan
        if not plan:
            return

        # 1. Update KPIs
        if plan.feasible:
            self.lbl_feas_val.config(text="FEASIBLE", foreground="#10b981")
            self.lbl_feas_sub.config(text="Budget covers energy")
        else:
            self.lbl_feas_val.config(text="INFEASIBLE", foreground="#ef4444")
            self.lbl_feas_sub.config(text="Budget below always-on cost")

        self.lbl_bill_val.config(text=f"₱{plan.cost:,.2f}")
        if plan.feasible:
            self.lbl_bill_sub.config(text=f"Unused Budget: ₱{plan.unused_budget:,.2f}")
        else:
            self.lbl_bill_sub.config(text=f"Always-on load alone, ₱{-plan.unused_budget:,.2f} over budget")

        self.lbl_kwh_val.config(text=f"{plan.total_kwh:,.1f} kWh")
        self.lbl_kwh_sub.config(text=f"{plan.total_kwh / plan.days_in_month:,.2f} kWh/day (Cap: {plan.kwh_cap:,.1f} kWh)")

        comfort = plan.comfort_score()
        self.lbl_comf_val.config(text=f"{comfort:.1f}%")
        self.progress_comf["value"] = comfort

        # 2. Update Messages
        self.msg_box.config(state=tk.NORMAL)
        self.msg_box.delete("1.0", tk.END)
        if plan.messages:
            for msg in plan.messages:
                self.msg_box.insert(tk.END, f"• {msg}\n")
        else:
            self.msg_box.insert(tk.END, "• Plan generated successfully with zero warnings.")
        self.msg_box.config(state=tk.DISABLED)

        # 3. Update Schedule Table
        for row in self.sched_tree.get_children():
            self.sched_tree.delete(row)
        for r in daily_schedule(plan):
            blocks = ", ".join(r["time_blocks"]) if r["time_blocks"] else "-"
            self.sched_tree.insert("", tk.END, values=(
                r["priority"],
                r["appliance"],
                r["type"],
                r["when"],
                f"{r['hours_per_use_day']:.2f} h",
                f"{r['monthly_kwh']:.2f}",
                blocks,
                r["status"],
            ))

        # 4. Update Inventory Table
        self._refresh_inventory_tree()

        # 5. Update Bill Table
        for row in self.bill_tree.get_children():
            self.bill_tree.delete(row)
        for k, v in plan.bill.lines.items():
            self.bill_tree.insert("", tk.END, values=(k, f"₱{v:,.2f}"))
        self.bill_tree.insert("", tk.END, values=("---------------------------------------", "------------"))
        self.bill_tree.insert("", tk.END, values=("TOTAL MONTHLY BILL", f"₱{plan.cost:,.2f}"))
        if plan.total_kwh > 0:
            self.bill_tree.insert("", tk.END, values=("Effective Tariff Rate", f"₱{plan.cost / plan.total_kwh:,.4f} / kWh"))

        # 6. Update Charts
        self._draw_charts(plan)

    def _refresh_inventory_tree(self):
        for row in self.inv_tree.get_children():
            self.inv_tree.delete(row)

        # The data is normalized, so every field is present and the rows are
        # in the same order as the list (fixed first, then by priority).
        for a in self.current_household_data.get("appliances", []):
            is_fixed = a["type"] == FIXED
            self.inv_tree.insert("", tk.END, values=(
                "-" if is_fixed else a["priority"],
                a["name"],
                a["type"],
                f"{a['watts']:g}",
                a["quantity"],
                24 if is_fixed else f"{a['max_hours']:g}",
                24 if is_fixed else f"{a['min_hours']:g}",
                7 if is_fixed else f"{a['days_per_week']:g}",
            ))

    def _draw_charts(self, plan):
        # 1. Hourly Load Curve
        self.ax_load.clear()
        load = hourly_load_kw(plan)
        hours = list(range(24))
        self.ax_load.plot(hours, load, color="#0284c7", linewidth=2, marker="o", markersize=3)
        self.ax_load.fill_between(hours, load, color="#0284c7", alpha=0.15)
        self.ax_load.set_title("Hourly Load Profile (kW)", fontsize=10, fontweight="bold")
        self.ax_load.set_xlabel("Clock Hour", fontsize=8)
        self.ax_load.set_ylabel("Power Demand (kW)", fontsize=8)
        self.ax_load.set_xticks(range(0, 24, 3))
        self.ax_load.grid(True, linestyle="--", alpha=0.5)

        # 2. Energy Share Donut Chart
        self.ax_share.clear()
        sched = [r for r in daily_schedule(plan) if r["monthly_kwh"] > 0]
        total = sum(r["monthly_kwh"] for r in sched)
        # Group slices under 3% so their labels do not overlap.
        big = [r for r in sched if r["monthly_kwh"] >= 0.03 * total]
        small = sum(r["monthly_kwh"] for r in sched if r["monthly_kwh"] < 0.03 * total)
        labels = [r["appliance"][:15] for r in big] + (["Others (<3% each)"] if small else [])
        kwhs = [r["monthly_kwh"] for r in big] + ([small] if small else [])

        if kwhs:
            self.ax_share.pie(kwhs, labels=labels, autopct="%1.0f%%", startangle=140, textprops={"fontsize": 7})
            self.ax_share.set_title("Monthly kWh Share", fontsize=10, fontweight="bold")
        else:
            self.ax_share.text(0.5, 0.5, "Zero Usage", horizontalalignment="center", verticalalignment="center")

        self.fig_charts.tight_layout()
        self.canvas_charts.draw()

    def _run_simulation_chart(self):
        try:
            hh = household_from_dict(self.current_household_data)
            mode = self.mode_var.get()
            days = int(self.days_var.get())
            opts = BillOptions(
                lifeline=self.lifeline_var.get(),
                senior_citizen=self.senior_var.get(),
                lft_per_kwh=float(self.lft_var.get()),
            )

            budgets = list(range(1000, 10500, 500))
            bills, comforts = [], []
            for b in budgets:
                p = optimize(hh, b, mode=mode, options=opts, days_in_month=days, step=0.25)
                # Infeasible budgets have no plan: leave a gap in the lines.
                bills.append(p.cost if p.feasible else float("nan"))
                comforts.append(p.comfort_score() if p.feasible else float("nan"))

            self.ax_sim_bill.clear()
            ax_sim_comf = self.ax_sim_comf
            ax_sim_comf.clear()
            ax_sim_comf.yaxis.set_label_position("right")
            ax_sim_comf.yaxis.tick_right()

            color1 = "#059669"
            color2 = "#2563eb"

            self.ax_sim_bill.set_xlabel("Monthly Budget (PHP ₱)", fontsize=9)
            self.ax_sim_bill.set_ylabel("Expected Bill (PHP ₱)", color=color1, fontsize=9)
            self.ax_sim_bill.plot(budgets, bills, color=color1, linewidth=2.5, marker="s")
            self.ax_sim_bill.tick_params(axis="y", labelcolor=color1)
            self.ax_sim_bill.grid(True, linestyle="--", alpha=0.5)

            ax_sim_comf.set_ylabel("Comfort Score (%)", color=color2, fontsize=9)
            ax_sim_comf.plot(budgets, comforts, color=color2, linewidth=2, linestyle="--", marker="o")
            ax_sim_comf.tick_params(axis="y", labelcolor=color2)
            ax_sim_comf.set_ylim(0, 105)

            self.fig_sim.tight_layout()
            self.canvas_sim.draw()
        except Exception as e:
            messagebox.showerror("Simulation Error", str(e))

    # ---------------------------------------------------------------------------
    # User Actions & Event Handlers
    # ---------------------------------------------------------------------------
    def _on_preset_change(self, event=None):
        name = self.preset_var.get()
        if name in PRESETS:
            self.current_household_data = self._load_preset_json(PRESETS[name])
            self.run_optimization()

    def _set_budget(self, val):
        self.budget_var.set(val)
        self.run_optimization()

    def _import_json(self):
        path = filedialog.askopenfilename(filetypes=[("JSON files", "*.json")])
        if path:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = normalized(json.load(f))
            except Exception as e:
                messagebox.showerror("File Error", f"Failed to load household: {e}")
                return
            self.current_household_data = data
            self.run_optimization()

    def _export_json(self):
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON files", "*.json")])
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(self.current_household_data, f, indent=2)
                messagebox.showinfo("Saved", f"Profile saved to {path}")
            except Exception as e:
                messagebox.showerror("File Error", f"Failed to save JSON: {e}")

    def _export_csv(self):
        if not self.current_plan:
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV files", "*.csv")])
        if path:
            try:
                from main import write_csv
                write_csv(self.current_plan, path)
                messagebox.showinfo("Saved", f"Schedule CSV saved to {path}")
            except Exception as e:
                messagebox.showerror("Export Error", str(e))

    def _names(self, skip=None):
        return {a["name"] for i, a in enumerate(self.current_household_data["appliances"])
                if i != skip}

    def _commit(self, apps):
        """Renumber variable priorities in list order, validate, and rerun.
        On an invalid household the previous one is kept."""
        p = 1
        for a in apps:
            if a.get("type") == FIXED:
                a["priority"] = None
            else:
                a["priority"], p = p, p + 1
        try:
            data = normalized({**self.current_household_data, "appliances": apps})
        except (ValueError, KeyError, TypeError) as e:
            messagebox.showerror("Invalid Appliance", str(e))
            return False
        self.current_household_data = data
        self.run_optimization()
        return True

    def _selected_index(self):
        sel = self.inv_tree.selection()
        if not sel:
            messagebox.showwarning("Select Item", "Please select an appliance from the table first.")
            return None
        return self.inv_tree.index(sel[0])

    def _reselect(self, idx):
        rows = self.inv_tree.get_children()
        if 0 <= idx < len(rows):
            self.inv_tree.selection_set(rows[idx])
            self.inv_tree.see(rows[idx])

    def _open_catalogue_picker(self):
        dlg = CataloguePickerDialog(self.root, self.catalogue)
        self.root.wait_window(dlg.top)
        if dlg.selected_item:
            ref = dlg.selected_item
            new_app = {
                "name": unique_name(ref["name"], self._names()),
                "catalogue": ref["name"],
                "type": FIXED if ref.get("category") == FIXED else VARIABLE,
                "watts": float(ref["median_watts"]),
                "quantity": 1,
                "max_hours": float(ref["median_hours_per_day"]),
                "min_hours": 0.0,
                "days_per_week": float(ref["median_days_per_week"]),
            }
            self._commit(self.current_household_data["appliances"] + [new_app])

    def _add_custom_appliance(self):
        apps = self.current_household_data["appliances"]
        new_app = {
            "name": unique_name("Custom Appliance", self._names()),
            "type": VARIABLE,
            "watts": 100.0,
            "quantity": 1,
            "max_hours": 4.0,
            "min_hours": 0.0,
            "days_per_week": 7.0,
        }
        dlg = ApplianceEditDialog(self.root, new_app, self._names())
        self.root.wait_window(dlg.top)
        if dlg.result:
            self._commit(apps + [dlg.result])

    def _edit_selected_appliance(self):
        idx = self._selected_index()
        apps = list(self.current_household_data["appliances"])
        if idx is None or not 0 <= idx < len(apps):
            return
        dlg = ApplianceEditDialog(self.root, apps[idx], self._names(skip=idx))
        self.root.wait_window(dlg.top)
        if dlg.result:
            apps[idx] = dlg.result
            self._commit(apps)

    def _delete_selected_appliance(self):
        idx = self._selected_index()
        apps = list(self.current_household_data["appliances"])
        if idx is not None and 0 <= idx < len(apps):
            del apps[idx]
            self._commit(apps)

    def _move(self, delta):
        """Swap the selected variable appliance with its neighbour. Fixed
        appliances have no priority, so they cannot be moved."""
        idx = self._selected_index()
        if idx is None:
            return
        apps = list(self.current_household_data["appliances"])
        j = idx + delta
        if not (0 <= j < len(apps)) or FIXED in (apps[idx]["type"], apps[j]["type"]):
            return
        apps[idx], apps[j] = apps[j], apps[idx]
        if self._commit(apps):
            self._reselect(j)

    def _move_pri_up(self):
        self._move(-1)

    def _move_pri_down(self):
        self._move(+1)


# ---------------------------------------------------------------------------
# Dialog Windows
# ---------------------------------------------------------------------------
class CataloguePickerDialog:
    def __init__(self, parent, catalogue):
        self.top = tk.Toplevel(parent)
        self.top.title("Select Appliance from HECS Catalogue")
        self.top.geometry("550x400")
        self.top.transient(parent)
        self.top.grab_set()

        self.catalogue = catalogue
        self.selected_item = None

        ttk.Label(self.top, text="Search Catalogue:").pack(anchor=tk.W, padx=10, pady=(10, 2))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", self._on_search)
        ttk.Entry(self.top, textvariable=self.search_var).pack(fill=tk.X, padx=10, pady=(0, 5))

        self.tree = ttk.Treeview(self.top, columns=("name", "watts", "hours", "days"), show="headings", selectmode="browse")
        self.tree.heading("name", text="Appliance Name")
        self.tree.heading("watts", text="Watts (W)")
        self.tree.heading("hours", text="Typical h/d")
        self.tree.heading("days", text="Days/Wk")

        self.tree.column("name", width=260)
        self.tree.column("watts", width=80, anchor=tk.E)
        self.tree.column("hours", width=80, anchor=tk.E)
        self.tree.column("days", width=70, anchor=tk.CENTER)

        self.tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        self.tree.bind("<Double-1>", lambda e: self._on_select())

        btn_box = ttk.Frame(self.top)
        btn_box.pack(fill=tk.X, padx=10, pady=10)
        ttk.Button(btn_box, text="Add Selected", command=self._on_select).pack(side=tk.RIGHT)
        ttk.Button(btn_box, text="Cancel", command=self.top.destroy).pack(side=tk.RIGHT, padx=5)

        self._populate_tree()

    def _populate_tree(self, filter_text=""):
        for row in self.tree.get_children():
            self.tree.delete(row)

        txt = filter_text.lower()
        for item in self.catalogue.values():
            if txt in item["name"].lower():
                self.tree.insert("", tk.END, values=(
                    item["name"],
                    item["median_watts"],
                    item["median_hours_per_day"],
                    item["median_days_per_week"],
                ))

    def _on_search(self, *args):
        self._populate_tree(self.search_var.get())

    def _on_select(self):
        sel = self.tree.selection()
        if sel:
            name = self.tree.item(sel[0])["values"][0]
            if name in self.catalogue:
                self.selected_item = self.catalogue[name]
                self.top.destroy()


class ApplianceEditDialog:
    FIELDS = [  # (key, label, kind)
        ("name", "Appliance Name:", str),
        ("type", "Type:", "type"),
        ("watts", "Wattage (W):", float),
        ("quantity", "Quantity:", int),
        ("max_hours", "Desired Hours (h/day):", float),
        ("min_hours", "Minimum Hours (h/day):", float),
        ("days_per_week", "Days per Week:", float),
    ]

    def __init__(self, parent, appliance_dict, taken_names=()):
        self.top = tk.Toplevel(parent)
        self.top.title("Edit Appliance Parameters")
        self.top.geometry("400x330")
        self.top.transient(parent)
        self.top.grab_set()
        self.top.columnconfigure(1, weight=1)

        self.result = None
        self.original = appliance_dict
        self.taken = set(taken_names)
        self.vars = {}
        defaults = {"name": "", "type": VARIABLE, "watts": 100, "quantity": 1,
                    "max_hours": 4.0, "min_hours": 0.0, "days_per_week": 7.0}
        for row, (key, label, kind) in enumerate(self.FIELDS):
            ttk.Label(self.top, text=label).grid(row=row, column=0, sticky=tk.W, padx=10, pady=5)
            value = appliance_dict.get(key)
            # StringVar for every field: a half-typed number must not raise
            # a TclError before the user presses Save.
            var = tk.StringVar(value=str(defaults[key] if value is None else value))
            if kind == "type":
                widget = ttk.Combobox(self.top, textvariable=var, values=[VARIABLE, FIXED], state="readonly")
            else:
                widget = ttk.Entry(self.top, textvariable=var)
            # grid() takes sticky=, not pack()'s fill=.
            widget.grid(row=row, column=1, sticky=tk.EW, padx=10, pady=5)
            self.vars[key] = var

        ttk.Label(self.top, text="Fixed appliances always run 24 h/day, 7 days/week.",
                  style="KpiSub.TLabel").grid(row=len(self.FIELDS), column=0, columnspan=2,
                                              sticky=tk.W, padx=10)
        btn_box = ttk.Frame(self.top)
        btn_box.grid(row=len(self.FIELDS) + 1, column=0, columnspan=2, sticky=tk.EW, padx=10, pady=12)
        ttk.Button(btn_box, text="Save Parameters", command=self._on_save).pack(side=tk.RIGHT)
        ttk.Button(btn_box, text="Cancel", command=self.top.destroy).pack(side=tk.RIGHT, padx=5)

    def _on_save(self):
        raw = {k: v.get().strip() for k, v in self.vars.items()}
        try:
            result = {**self.original}  # keeps "catalogue" (time-of-day profile)
            result.update(
                name=raw["name"],
                type=raw["type"],
                watts=float(raw["watts"]),
                quantity=int(raw["quantity"]),
                max_hours=float(raw["max_hours"]),
                min_hours=float(raw["min_hours"]),
                days_per_week=float(raw["days_per_week"]),
            )
            if result["name"] in self.taken:
                raise ValueError(f"another appliance is already named '{result['name']}'")
            from_dict(result)  # same checks the optimizer applies
        except ValueError as e:
            messagebox.showerror("Input Error", str(e), parent=self.top)
            return
        self.result = result
        self.top.destroy()


def main():
    root = tk.Tk()
    app = EnergyOptimizerGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
