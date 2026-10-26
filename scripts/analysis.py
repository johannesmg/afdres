"""Correlate AfD Bundestag vote shares with employment growth across German
NUTS-3 regions (Kreise / kreisfreie Städte, 2021 boundaries, n = 400).

Inputs (see download_data.sh), all from GERDA harmonised to 2021 boundaries:
  federal_cty_harm.csv   second-vote (Zweitstimme) shares by county, 1990-2025
  cty_area_pop_emp.csv   employees subject to social insurance (thousands), 1997-2020
  cty_inkar_harm2021.csv INKAR socio-economic indicators, 1995-2021

Outputs go to results/.
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "results"
FIG = OUT / "figures"

EAST_STATES = {"12", "13", "14", "15", "16"}  # Berlin (11) is coded West
WEST_COLOR, EAST_COLOR = "#2a78d6", "#eb6834"

# Employment growth windows (start, end). SV employment data end in 2020.
WINDOWS = {
    "2015-2020": (2015, 2020),
    "2014-2019": (2014, 2019),  # pre-COVID
    "2010-2020": (2010, 2020),
    "2000-2020": (2000, 2020),
}


def ann_growth(emp, start, end):
    """Annualised log growth in percent."""
    return 100 * (np.log(emp[end]) - np.log(emp[start])) / (end - start)


def load():
    el = pd.read_csv(RAW / "federal_cty_harm.csv", dtype={"county_code": str, "state": str})
    afd = el.pivot(index="county_code", columns="election_year", values="afd") * 100
    afd = afd[[2013, 2017, 2021, 2025]].add_prefix("afd_")
    state = el.groupby("county_code").state.first()

    cov = pd.read_csv(RAW / "cty_area_pop_emp.csv", dtype={"county_code_21": str})
    names = cov.groupby("county_code_21").county_name_21.first()
    emp = cov.pivot(index="county_code_21", columns="year", values="employees_cty")
    pop = cov.pivot(index="county_code_21", columns="year", values="population_cty")
    dens = cov.pivot(index="county_code_21", columns="year", values="pop_density_cty")

    ink = pd.read_csv(RAW / "cty_inkar_harm2021.csv")
    ink["county"] = ink.county.astype(str).str.zfill(5)
    ink19 = ink[ink.year == 2019].set_index("county")

    df = afd.join(state.rename("state"))
    df["name"] = names
    df["east"] = df.state.isin(EAST_STATES).astype(int)
    for label, (a, b) in WINDOWS.items():
        df[f"g_{label}"] = ann_growth(emp, a, b)
        df[f"popg_{label}"] = ann_growth(pop, a, b)
    df["emp_2020"] = emp[2020]
    df["pop_2020"] = pop[2020]
    df["emp_rate_2020"] = emp[2020] / pop[2020] * 100  # workplace jobs per 100 residents
    df["log_density"] = np.log(dens[2020])
    df["log_gdp_pc"] = np.log(ink19["Bruttoinlandsprodukt_je_Einwohner_inkar"])
    df["foreign_share"] = ink19["Ausländeranteil_inkar"]
    df["median_income"] = ink19["Medianeinkommen_inkar"]
    assert len(df) == 400 and df.filter(like="g_").notna().all().all()
    return df, emp, afd


def corr_table(df):
    rows = []
    for outcome in ["afd_2017", "afd_2021", "afd_2025"]:
        for label in WINDOWS:
            x, y = df[f"g_{label}"], df[outcome]
            r, p = stats.pearsonr(x, y)
            rho, prho = stats.spearmanr(x, y)
            w = df.pop_2020
            c = np.cov(x, y, aweights=w)
            wr = c[0, 1] / np.sqrt(c[0, 0] * c[1, 1])
            row = dict(outcome=outcome, growth_window=label, n=len(df),
                       pearson_r=r, pearson_p=p, spearman_rho=rho, spearman_p=prho,
                       pop_weighted_r=wr)
            for grp, sub in [("west", df[df.east == 0]), ("east", df[df.east == 1])]:
                rr, pp = stats.pearsonr(sub[f"g_{label}"], sub[outcome])
                row[f"r_{grp}"], row[f"p_{grp}"], row[f"n_{grp}"] = rr, pp, len(sub)
            rows.append(row)
    return pd.DataFrame(rows)


def regressions(df):
    """OLS of AfD 2025 share on standardised employment growth.

    Coefficient = percentage-point change in AfD share per 1 SD of growth.
    """
    specs = {
        "1 bivariate": "afd ~ gz",
        "2 + East dummy": "afd ~ gz + east",
        "3 + state FE": "afd ~ gz + C(state)",
        "4 + state FE + controls": "afd ~ gz + C(state) + log_density + log_gdp_pc"
                                   " + foreign_share + emp_rate_2020",
        "5 + pop. growth": "afd ~ gz + C(state) + log_density + log_gdp_pc"
                           " + foreign_share + emp_rate_2020 + popg",
    }
    rows = []
    for outcome in ["afd_2025", "afd_2021"]:
        for label in WINDOWS:
            d = df.assign(afd=df[outcome],
                          gz=(df[f"g_{label}"] - df[f"g_{label}"].mean()) / df[f"g_{label}"].std(),
                          popg=df[f"popg_{label}"])
            for name, f in specs.items():
                m = smf.ols(f, d).fit(cov_type="HC1")
                lo, hi = m.conf_int().loc["gz"]
                rows.append(dict(outcome=outcome, growth_window=label, spec=name,
                                 coef_pp_per_sd=m.params["gz"], se=m.bse["gz"],
                                 ci_low=lo, ci_high=hi, p=m.pvalues["gz"],
                                 r2=m.rsquared, n=int(m.nobs),
                                 sd_growth=df[f"g_{label}"].std()))
    return pd.DataFrame(rows)


def panel(df, emp, afd):
    """County + election fixed effects: does faster job growth in the run-up to
    an election go with a lower AfD share *within* the same county?

    Run-up windows: 2013 <- 2009-13, 2017 <- 2013-17, 2021 <- 2017-20.
    """
    windows = {2013: (2009, 2013), 2017: (2013, 2017), 2021: (2017, 2020)}
    long = []
    for yr, (a, b) in windows.items():
        long.append(pd.DataFrame({"county": df.index, "year": yr,
                                  "afd": afd[f"afd_{yr}"].values,
                                  "g": ann_growth(emp, a, b).values,
                                  "east": df.east.values}))
    long = pd.concat(long, ignore_index=True)
    out = {}
    for name, sub in [("all", long), ("west", long[long.east == 0]), ("east", long[long.east == 1])]:
        m = smf.ols("afd ~ g + C(county) + C(year)", sub).fit(
            cov_type="cluster", cov_kwds={"groups": pd.factorize(sub.county)[0]})
        out[name] = dict(coef_pp_per_1pct_growth=m.params["g"], se=m.bse["g"],
                         p=m.pvalues["g"], n=int(m.nobs))
    # also with east-specific year effects (AfD surge in the East 2017+)
    long["east17"] = long.east * (long.year == 2017)
    long["east21"] = long.east * (long.year == 2021)
    m = smf.ols("afd ~ g + C(county) + C(year) + east17 + east21", long).fit(
        cov_type="cluster", cov_kwds={"groups": pd.factorize(long.county)[0]})
    out["all, East x year FE"] = dict(coef_pp_per_1pct_growth=m.params["g"], se=m.bse["g"],
                                      p=m.pvalues["g"], n=int(m.nobs))
    return pd.DataFrame(out).T


def scatter(df, label="2010-2020", outcome="afd_2025"):
    fig, ax = plt.subplots(figsize=(8, 5.5), dpi=150)
    for flag, color, name in [(0, WEST_COLOR, "West (incl. Berlin)"), (1, EAST_COLOR, "East")]:
        sub = df[df.east == flag]
        x, y = sub[f"g_{label}"], sub[outcome]
        ax.scatter(x, y, s=np.clip(sub.pop_2020 / 8, 10, 200), color=color, alpha=0.55,
                   edgecolor="white", linewidth=0.6, label=None)
        b, a = np.polyfit(x, y, 1)
        xs = np.linspace(x.min(), x.max(), 50)
        r = stats.pearsonr(x, y)[0]
        ax.plot(xs, a + b * xs, color=color, lw=2,
                label=f"{name}: r = {r:+.2f}, slope = {b:+.1f} pp per 1 %/yr (n={len(sub)})")
    r_all = stats.pearsonr(df[f"g_{label}"], df[outcome])[0]
    ax.set_xlabel(f"Employment growth {label}, % per year (SV-pflichtig Beschäftigte, workplace)")
    ax.set_ylabel(f"AfD second-vote share {outcome[-4:]}, %")
    ax.set_title(f"AfD {outcome[-4:]} vs. job growth {label}, 400 NUTS-3 regions"
                 f"  (pooled r = {r_all:+.2f})", loc="left", fontsize=11)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.grid(color="#e5e4e0", lw=0.6)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, fontsize=8.5, loc="upper right")
    ax.text(0, -0.13, "Marker area ∝ population. Data: GERDA / Bundeswahlleiterin / BBSR / INKAR.",
            transform=ax.transAxes, fontsize=7.5, color="#52514e")
    fig.tight_layout()
    fig.savefig(FIG / f"scatter_{outcome}_growth_{label}.png")
    plt.close(fig)


def coef_plot(reg):
    sub = reg[reg.outcome == "afd_2025"]
    fig, ax = plt.subplots(figsize=(8, 5), dpi=150)
    specs = sub.spec.unique()
    colors = ["#2a78d6", "#eb6834", "#1baf7a", "#6250d6"]
    for i, (label, color) in enumerate(zip(WINDOWS, colors)):
        s = sub[sub.growth_window == label].set_index("spec").loc[specs]
        y = np.arange(len(specs)) + (i - 1.5) * 0.17
        ax.errorbar(s.coef_pp_per_sd, y, xerr=[s.coef_pp_per_sd - s.ci_low, s.ci_high - s.coef_pp_per_sd],
                    fmt="o", ms=5, color=color, lw=2, capsize=0, label=f"growth {label}")
    ax.axvline(0, color="#52514e", lw=1)
    ax.set_yticks(np.arange(len(specs)), specs)
    ax.invert_yaxis()
    ax.set_xlabel("Change in AfD 2025 share (pp) per +1 SD employment growth, 95% CI (HC1)")
    ax.set_title("Job growth → AfD 2025: coefficient across specifications", loc="left", fontsize=11)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.grid(axis="x", color="#e5e4e0", lw=0.6)
    ax.legend(frameon=False, fontsize=8.5)
    fig.tight_layout()
    fig.savefig(FIG / "coefficients_afd_2025.png")
    plt.close(fig)


def main():
    FIG.mkdir(parents=True, exist_ok=True)
    df, emp, afd = load()
    df.round(4).to_csv(OUT / "nuts3_afd_employment.csv", index_label="county_code")

    ct = corr_table(df)
    ct.round(4).to_csv(OUT / "correlations.csv", index=False)
    reg = regressions(df)
    reg.round(4).to_csv(OUT / "regressions.csv", index=False)
    pan = panel(df, emp, afd)
    pan.round(4).to_csv(OUT / "panel_fe.csv", index_label="sample")

    for label in ["2010-2020", "2015-2020", "2000-2020"]:
        scatter(df, label)
    coef_plot(reg)

    pd.set_option("display.width", 200)
    print(ct[["outcome", "growth_window", "pearson_r", "pearson_p", "spearman_rho",
              "pop_weighted_r", "r_west", "p_west", "r_east", "p_east"]].round(3).to_string(index=False))
    print()
    print(reg[reg.outcome == "afd_2025"][["growth_window", "spec", "coef_pp_per_sd", "ci_low",
                                         "ci_high", "p", "r2"]].round(3).to_string(index=False))
    print()
    print(pan.round(3))
    print()
    print(df.groupby("east")[[f"g_{w}" for w in WINDOWS] + ["afd_2025"]].mean().round(2))


if __name__ == "__main__":
    main()
