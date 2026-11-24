#!/usr/bin/env python3
"""Builds Tables 1-6 (+7: other RWLock policies) from results/*.json (median of 3 runs)."""
import glob, json, os, statistics as st, sys

R = sys.argv[1] if len(sys.argv) > 1 else "results"
med = st.median


def load(pat):
    return [json.load(open(f, encoding="utf-8")) for f in sorted(glob.glob(os.path.join(R, pat)))]


G, RW = load("run_g_*.json"), load("run_rw_[0-9].json")
X1G, X1RW = load("x1_g_*.json"), load("x1_rw_*.json")
TCS = load("tcs_*.json")
SELF = load("selftest.json")[0]
n, nw, nr = G[0]["n"], G[0]["n_w"], G[0]["n_r"]
D = G[0]["D"]


def m(runs, f):
    return med([f(r) for r in runs])


def role_vals(runs, role):
    g = lambda k: m(runs, lambda r: r[role][k])
    return {"N": g("N"), "X": g("X"), "x": g("x"), "p50": g("p50_us"), "p99": g("p99_us"),
            "h": g("h"), "mw": g("maxwait_ms"), "w": g("w"), "cv": g("cv")}


def allvals(runs):
    v = role_vals(runs, "all")
    return v


tcs_w, tcs_r = m(TCS, lambda r: r["t_cs_w_us"]), m(TCS, lambda r: r["t_cs_r_us"])
tbar = (nw * tcs_w + nr * tcs_r) / n
bound = 1e6 / tbar
X1g, X1rw = m(X1G, lambda r: r["X1"]), m(X1RW, lambda r: r["X1"])
out = []
P = out.append


def fmt(x, d=2):
    return f"{x:,.{d}f}".replace(",", " ")


def row(*c):
    return "| " + " | ".join(str(x) for x in c) + " |"


# ---- Table 1
P("### Таблиця 1. Параметри стенду\n")
P(row("n", "n_w", "n_r", "Мова і версія", "Логічні ядра", "X_1 (G), оп/с", "X_1 (RW), оп/с", "Інваріанти (крок 2)", "Інваріанти (крок 3)"))
P(row(*["---"] * 9))
P(row(n, nw, nr, f"{G[0]['impl']} {G[0]['python']}", G[0]["cores"], fmt(X1g, 0), fmt(X1rw, 0),
      "виконано" if all(r["invariants_ok"] for r in G) else "ПОРУШЕНО",
      "виконано" if all(r["invariants_ok"] for r in RW) else "ПОРУШЕНО"))
P(f"\nПеревірка «читання не змінює полів»: peek не змінив жодного поля = {SELF['peek_does_not_mutate']}; "
  f"контроль (get переставляє запис) = {SELF['get_reorders']}.\n")


# ---- Table 2/3
def main_table(title, runs, with_cs):
    P(title + "\n")
    hdr = ["Роль", "Операцій за D", "X, оп/с", "p50, мкс", "p99, мкс", "max t_wait, мс", "w", "cv"]
    if with_cs:
        hdr.append("t_cs, мкс")
    P(row(*hdr)); P(row(*["---"] * len(hdr)))
    for name, v, cs in (("запис", role_vals(runs, "w"), tcs_w), ("читання", role_vals(runs, "r"), tcs_r),
                        ("разом", allvals(runs), tbar)):
        c = [name, fmt(v["N"], 0), fmt(v["X"], 0), fmt(v["p50"]), fmt(v["p99"]), fmt(v["mw"]), fmt(v["w"], 3), fmt(v["cv"], 3)]
        if with_cs:
            c.append(fmt(cs, 3))
        P(row(*c))
    P("")


main_table("### Таблиця 2. Глобальний замок (медіана з 3 запусків)", G, True)
P(f"Одно потокові виміри: t_cs,w = {fmt(tcs_w,3)} мкс, t_cs,r = {fmt(tcs_r,3)} мкс, "
  f"t̄_cs = {fmt(tbar,3)} мкс, межа 1/t̄_cs = {fmt(bound,0)} оп/с.\n")
main_table("### Таблиця 3. RWLock (медіана з 3 запусків)", RW, False)

# ---- Table 4
P("### Таблиця 4. Операцій на потік за D\n")
P(row("Потік", "Роль", "Глобальний замок", "RWLock")); P(row(*["---"] * 4))
def per_thread(runs):
    pts = []
    for r in runs:
        w, rd = list(r["w"]["per_thread"]), list(r["r"]["per_thread"])
        seq = []
        for i in range(n):
            seq.append(w.pop(0) if (i + 1) % 2 == 1 else rd.pop(0))
        pts.append(seq)
    return [med(p[i] for p in pts) for i in range(n)]
pg, pr = per_thread(G), per_thread(RW)
for i in range(n):
    P(row(i + 1, "запис" if (i + 1) % 2 else "читання", fmt(pg[i], 0), fmt(pr[i], 0)))
P("")

# ---- Table 5
Gv, RWv = allvals(G), allvals(RW)
Gw, Gr, Rw, Rr = role_vals(G, "w"), role_vals(G, "r"), role_vals(RW, "w"), role_vals(RW, "r")
XG, XR = Gv["X"], RWv["X"]
P("### Таблиця 5. Масштабування і насичення\n")
P(row("Замок", "X(n), оп/с", "X_1, оп/с", "S(n)", "E(n)")); P(row(*["---"] * 5))
P(row("глобальний", fmt(XG, 0), fmt(X1g, 0), fmt(XG / X1g, 3), fmt(XG / X1g / n, 3)))
P(row("RWLock", fmt(XR, 0), fmt(X1rw, 0), fmt(XR / X1rw, 3), fmt(XR / X1rw / n, 3)))
P("")
P(row("S_w", "S_r", "S_Σ", "ρ = X(G)·t̄_cs", "X(G)", "1/t̄_cs")); P(row(*["---"] * 6))
P(row(fmt(Rw["X"] / Gw["X"], 3), fmt(Rr["X"] / Gr["X"], 3), fmt(XR / XG, 3), fmt(XG * tbar / 1e6, 3), fmt(XG, 0), fmt(bound, 0)))
P("")

# ---- Table 6
P("### Таблиця 6. Порівняння доступу до даних\n")
P(row("Показник", "G: запис", "G: читання", "RW: запис", "RW: читання", "RW/G запис", "RW/G читання"))
P(row(*["---"] * 7))
phi = lambda runs, k: m(runs, lambda r: r[k])
items = [("X, оп/с", "X", 0), ("x на потік, оп/с", "x", 0), ("p50, мкс", "p50", 2), ("p99, мкс", "p99", 2),
         ("h = p99/p50", "h", 2), ("max t_wait, мс", "mw", 2), ("w", "w", 3), ("cv", "cv", 3)]
def ratio(a, b, d=3):
    return f"{a/b:.{d}f}" if b else f"Δ={a-b:.{d}f}"
for lab, k, d in items:
    P(row(lab, fmt(Gw[k], d), fmt(Gr[k], d), fmt(Rw[k], d), fmt(Rr[k], d), ratio(Rw[k], Gw[k]), ratio(Rr[k], Gr[k])))
pg_w, pg_r, pr_w, pr_r = phi(G, "phi_w"), phi(G, "phi_r"), phi(RW, "phi_w"), phi(RW, "phi_r")
P(row("φ", fmt(pg_w, 3), fmt(pg_r, 3), fmt(pr_w, 3), fmt(pr_r, 3), ratio(pr_w, pg_w), ratio(pr_r, pg_r)))
P("")

# ---- Table 7 (extra policies)
P("### Таблиця 7 (додатково). Політики RWLock\n")
P(row("Політика", "X, оп/с", "X_w", "X_r", "p99 запис, мкс", "p99 читання, мкс", "max t_wait запис, мс", "max t_wait читання, мс", "cv запис", "cv читання", "Інваріанти"))
P(row(*["---"] * 11))
for pol, runs in (("writer (основна)", RW), ("reader", load("run_rw-reader_*.json")), ("fair (FIFO)", load("run_rw-fair_*.json"))):
    if not runs:
        continue
    w, r, a = role_vals(runs, "w"), role_vals(runs, "r"), allvals(runs)
    P(row(pol, fmt(a["X"], 0), fmt(w["X"], 0), fmt(r["X"], 0), fmt(w["p99"]), fmt(r["p99"]), fmt(w["mw"]), fmt(r["mw"]),
          fmt(w["cv"], 3), fmt(r["cv"], 3), "ok" if all(x["invariants_ok"] for x in runs) else "ПОРУШЕНО"))
P("")
open(os.path.join(R, "tables.md"), "w", encoding="utf-8").write("\n".join(out))
print("\n".join(out))

# ---- plot
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    c1, c2 = "#2a6fb0", "#d9822b"
    fig, ax = plt.subplots(1, 2, figsize=(11, 4))
    xs = range(1, n + 1); wdt = 0.4
    ax[0].bar([x - wdt / 2 for x in xs], pg, wdt, label="глобальний замок", color=c1)
    ax[0].bar([x + wdt / 2 for x in xs], pr, wdt, label="RWLock (writer-pref)", color=c2)
    ax[0].set_xticks(list(xs)); ax[0].set_xlabel("потік (непарні — запис, парні — читання)")
    ax[0].set_ylabel(f"операцій за D={D:g} с"); ax[0].set_title("Операції на потік"); ax[0].legend(frameon=False)
    labs = ["p50 зап.", "p99 зап.", "p50 чит.", "p99 чит."]
    gv = [Gw["p50"], Gw["p99"], Gr["p50"], Gr["p99"]]; rv = [Rw["p50"], Rw["p99"], Rr["p50"], Rr["p99"]]
    ax[1].bar([i - wdt / 2 for i in range(4)], gv, wdt, color=c1, label="глобальний замок")
    ax[1].bar([i + wdt / 2 for i in range(4)], rv, wdt, color=c2, label="RWLock")
    ax[1].set_xticks(range(4)); ax[1].set_xticklabels(labs); ax[1].set_yscale("log")
    ax[1].set_ylabel("мкс (лог. шкала)"); ax[1].set_title("Тривалість операції τ")
    for a in ax:
        a.spines[["top", "right"]].set_visible(False)
    fig.tight_layout(); fig.savefig(os.path.join(R, "lab5_overview.png"), dpi=150)
except Exception as e:
    print("plot skipped:", e)
