#!/usr/bin/env python3
"""Runs the whole Lab 5 measurement series (about 6-7 minutes). Usage: python run_all.py [--quick]"""
import os, subprocess, sys, time

quick = "--quick" in sys.argv
D, WU = ("2", "0.5") if quick else ("10", "1")
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "results" + ("_quick" if quick else ""))
os.makedirs(OUT, exist_ok=True)
B = os.path.join(HERE, "lru_bench.py")


def go(name, *extra):
    p = os.path.join(OUT, name + ".json")
    cmd = [sys.executable, B, "--duration", D, "--warmup", WU, "--out", p, *extra]
    print(">>", name, flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if r.returncode:
        print(r.stderr); sys.exit(1)
    if "--mode" in extra and extra[extra.index("--mode") + 1] == "run":
        print(r.stdout, flush=True)
    time.sleep(1)


go("selftest", "--mode", "selftest")
for i in (1, 2, 3):
    go(f"tcs_{i}", "--mode", "tcs")
for lock in ("g", "rw"):
    for i in (1, 2, 3):
        go(f"run_{lock}_{i}", "--mode", "run", "--lock", lock, "--policy", "writer")
for lock in ("g", "rw"):
    for i in (1, 2, 3):
        go(f"x1_{lock}_{i}", "--mode", "x1", "--lock", lock, "--policy", "writer")
for pol in ("reader", "fair"):
    for i in (1, 2, 3):
        go(f"run_rw-{pol}_{i}", "--mode", "run", "--lock", "rw", "--policy", pol)
print("DONE ->", OUT)
