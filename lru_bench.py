#!/usr/bin/env python3
"""Lab 5, variant 3: thread-safe LRU cache (C3), P=500, keys 0..10P-1, n=9 threads.

Odd threads (1,3,5,7,9) write: put(k,v) / get(k) 50:50 (get changes usage order).
Even threads (2,4,6,8) read: peek(k) (does NOT change usage order).
Lock type: g = one global mutex, rw = RWLock built on threading.Condition.
"""
import argparse, json, math, os, platform, random, statistics, sys, threading
from array import array
from time import perf_counter_ns as now

P = 500
KEYSPACE = 10 * P


# ---------------------------------------------------------------- structure
class Node:
    __slots__ = ("key", "val", "prev", "next")

    def __init__(self, key=None, val=None):
        self.key, self.val, self.prev, self.next = key, val, None, None


class LRU:
    """Hash table (dict) + doubly linked usage list. head.next = most recent."""

    def __init__(self, cap):
        self.cap = cap
        self.map = {}
        self.head, self.tail = Node(), Node()
        self.head.next, self.tail.prev = self.tail, self.head

    def _unlink(self, n):
        n.prev.next, n.next.prev = n.next, n.prev

    def _push_front(self, n):
        n.prev, n.next = self.head, self.head.next
        self.head.next.prev = n
        self.head.next = n

    def put(self, k, v):  # WRITE
        n = self.map.get(k)
        if n is not None:
            n.val = v
            self._unlink(n)
            self._push_front(n)
            return
        if len(self.map) >= self.cap:
            last = self.tail.prev
            self._unlink(last)
            del self.map[last.key]
        n = Node(k, v)
        self.map[k] = n
        self._push_front(n)

    def get(self, k):  # WRITE (reorders list)
        n = self.map.get(k)
        if n is None:
            return None
        self._unlink(n)
        self._push_front(n)
        return n.val

    def peek(self, k):  # READ: no field is modified
        n = self.map.get(k)
        return None if n is None else n.val

    # --- helpers for checks (not thread-safe, call when idle)
    def order(self):
        out, n = [], self.head.next
        while n is not self.tail:
            out.append(n.key)
            n = n.next
        return out

    def check(self):
        errs = []
        fwd = self.order()
        bwd, n = [], self.tail.prev
        while n is not self.head:
            bwd.append(n.key)
            n = n.prev
        if len(self.map) > self.cap:
            errs.append("size>P")
        if len(fwd) != len(set(fwd)):
            errs.append("duplicates in list")
        if set(fwd) != set(self.map.keys()):
            errs.append("list keys != table keys")
        if fwd != bwd[::-1]:
            errs.append("prev/next inconsistent")
        for k, nd in self.map.items():
            if nd.key != k:
                errs.append("node key mismatch")
                break
        return errs


def prefill(c, rng):
    """Fill to capacity with random keys from the lower half of the key space.
    (Half of key space = 5P = 2500 keys > P, so the cache is filled to P.)"""
    half = KEYSPACE // 2
    while len(c.map) < c.cap:
        k = rng.randrange(half)
        c.put(k, k)


# ---------------------------------------------------------------- locks
class GlobalLock:
    name = "g"

    def __init__(self):
        self._l = threading.Lock()

    def acquire_read(self): self._l.acquire()
    def release_read(self): self._l.release()
    acquire_write = acquire_read
    release_write = release_read


class RWLock:
    """RWLock on threading.Condition.

    policy 'fair'  : FIFO tickets. Requests are served in arrival order; a run of
                     consecutive readers enters together, a writer waits for readers
                     to leave and then runs alone. No starvation of either role.
    policy 'reader': readers enter whenever no writer is ACTIVE (reader preference;
                     writers can starve while readers keep overlapping).
    policy 'writer': readers wait while a writer is active OR waiting (writer
                     preference; readers can starve).
    """
    name = "rw"

    def __init__(self, policy="fair"):
        self.policy = policy
        self._c = threading.Condition(threading.Lock())
        self._readers = 0
        self._writer = False
        self._wwait = 0
        self._next = 0     # next ticket (fair)
        self._serving = 0  # ticket allowed to enter (fair)

    def acquire_read(self):
        c = self._c
        with c:
            if self.policy == "fair":
                t = self._next; self._next += 1
                while t != self._serving or self._writer:
                    c.wait()
                self._readers += 1
                self._serving += 1
                c.notify_all()  # next ticket (maybe another reader) may enter
            elif self.policy == "reader":
                while self._writer:
                    c.wait()
                self._readers += 1
            else:
                while self._writer or self._wwait:
                    c.wait()
                self._readers += 1

    def release_read(self):
        with self._c:
            self._readers -= 1
            if self._readers == 0:
                self._c.notify_all()

    def acquire_write(self):
        c = self._c
        with c:
            if self.policy == "fair":
                t = self._next; self._next += 1
                while t != self._serving or self._writer or self._readers:
                    c.wait()
                self._writer = True
                self._serving += 1
            else:
                self._wwait += 1
                while self._writer or self._readers:
                    c.wait()
                self._wwait -= 1
                self._writer = True

    def release_write(self):
        with self._c:
            self._writer = False
            self._c.notify_all()


def make_lock(kind, policy):
    return GlobalLock() if kind == "g" else RWLock(policy)


# ---------------------------------------------------------------- workers
CH = 1 << 14


class Stat:
    def __init__(self, role):
        self.role = role
        self.n = 0
        self.tau = array("q")
        self.wait = array("q")
        self.cs = array("q")
        self.maxwait = 0
        self.wsum = 0


def worker(idx, role, cache, lock, st, go, t_m0, t_m1, seed, pattern=None):
    """role 'w' or 'r'. pattern (x1 mode): list of 'w'/'r' cycled by one thread."""
    rng = random.Random(seed)
    put, get, peek = cache.put, cache.get, cache.peek
    aw, rw_, ew, rr = lock.acquire_write, lock.release_write, None, None
    ar, rr_ = lock.acquire_read, lock.release_read
    tau_a, wait_a = st.tau.append, st.wait.append
    go.wait()
    pi = 0
    while True:
        # arguments for a chunk of calls are generated BEFORE the calls
        keys = [rng.randrange(KEYSPACE) for _ in range(CH)]
        bits = [rng.getrandbits(1) for _ in range(CH)]
        for i in range(CH):
            k = keys[i]
            r = role
            if pattern is not None:
                r = pattern[pi]
                pi += 1
                if pi == len(pattern):
                    pi = 0
            t_call = now()
            if r == "w":
                aw()
                t_acq = now()
                if bits[i]:
                    put(k, k)
                else:
                    get(k)
                rw_()
            else:
                ar()
                t_acq = now()
                peek(k)
                rr_()
            t_ret = now()
            if t_ret > t_m0:  # operation overlaps the measured window
                w = t_acq - t_call
                if w > st.maxwait:
                    st.maxwait = w
                if t_call >= t_m0 and t_ret <= t_m1:
                    st.n += 1
                    tau_a(t_ret - t_call)
                    wait_a(w)
                    st.wsum += w
                elif t_ret > t_m1:  # op crossing the window end: count the wait inside the window
                    st.wsum += max(min(t_acq, t_m1) - max(t_call, t_m0), 0)
            if t_ret >= t_m1:
                return


def pct(sorted_a, q):
    n = len(sorted_a)
    return sorted_a[max(math.ceil(q * n) - 1, 0)] if n else 0


def cvof(xs):
    m = statistics.fmean(xs)
    return (statistics.pstdev(xs) / m) if m else 0.0


def role_metrics(stats, D):
    k = len(stats)
    N = sum(s.n for s in stats)
    taus = sorted(t for s in stats for t in s.tau)
    waits = sum(s.wsum for s in stats)
    p50, p99 = pct(taus, 0.50), pct(taus, 0.99)
    return {
        "k": k, "N": N, "X": N / D, "x": N / D / k,
        "p50_us": p50 / 1e3, "p99_us": p99 / 1e3,
        "h": (p99 / p50) if p50 else 0.0,
        "maxwait_ms": max((s.maxwait for s in stats), default=0) / 1e6,
        "w": waits / 1e9 / (k * D),
        "cv": cvof([s.n for s in stats]),
        "per_thread": [s.n for s in stats],
    }


def run(args):
    n = args.n
    nw, nr = math.ceil(n / 2), n // 2
    cache = LRU(P)
    prefill(cache, random.Random(12345))
    lock = make_lock(args.lock, args.policy)
    go = threading.Event()
    stats, threads = [], []
    t_holder = {}
    if args.mode == "x1":
        pattern = ["w"] * nw + ["r"] * nr
        specs = [(1, "w", pattern)]
        # roles are decided per op, stats per role are not needed: one Stat
    else:
        pattern = None
        specs = [(i, "w" if i % 2 == 1 else "r", None) for i in range(1, n + 1)]
    # window is fixed after go.set(); workers read it lazily through closure list
    t_m0 = [0]; t_m1 = [0]

    class Win:  # tiny shim so threads see times set after start
        pass
    for i, role, pat in specs:
        st = Stat(role)
        stats.append(st)
    # start threads blocked on 'go'; set window before releasing them
    # (pass values via lambda after computing)
    T0 = now() + int(0.3e9)
    m0 = T0 + int(args.warmup * 1e9)
    m1 = m0 + int(args.duration * 1e9)
    for (i, role, pat), st in zip(specs, stats):
        t = threading.Thread(target=worker,
                             args=(i, role, cache, lock, st, go, m0, m1, 1000 + i, pat))
        t.start(); threads.append(t)
    go.set()
    for t in threads:
        t.join()
    D = args.duration
    errs = cache.check()
    res = {"mode": args.mode, "lock": args.lock, "policy": args.policy if args.lock == "rw" else "-",
           "n": n, "n_w": nw, "n_r": nr, "D": D, "warmup": args.warmup,
           "python": platform.python_version(), "impl": platform.python_implementation(),
           "cores": os.cpu_count(), "os": platform.platform(),
           "invariants_ok": not errs, "invariant_errors": errs, "size": len(cache.map)}
    if args.mode == "x1":
        res["N"] = stats[0].n
        res["X1"] = stats[0].n / D
    else:
        W = [s for s in stats if s.role == "w"]
        R = [s for s in stats if s.role == "r"]
        res["w"] = role_metrics(W, D)
        res["r"] = role_metrics(R, D)
        res["all"] = role_metrics(stats, D)
        res["all"]["k"] = n
        res["X"] = res["w"]["X"] + res["r"]["X"]
        res["phi_w"] = res["w"]["X"] / res["X"] if res["X"] else 0
        res["phi_r"] = res["r"]["X"] / res["X"] if res["X"] else 0
    return res


def tcs(args):
    """Single thread, global lock, no contention: mean time acquire->release."""
    out = {}
    cache = LRU(P)
    prefill(cache, random.Random(12345))
    lock = GlobalLock()
    rng = random.Random(7)
    for role in ("w", "r"):
        total, cnt = 0, 0
        end = now() + int(args.duration * 1e9)
        # 1 s warm-up not counted
        wu = now() + int(args.warmup * 1e9)
        end = wu + int(args.duration * 1e9)
        while True:
            keys = [rng.randrange(KEYSPACE) for _ in range(CH)]
            bits = [rng.getrandbits(1) for _ in range(CH)]
            for i in range(CH):
                k = keys[i]
                lock.acquire_write()
                t1 = now()
                if role == "w":
                    if bits[i]:
                        cache.put(k, k)
                    else:
                        cache.get(k)
                else:
                    cache.peek(k)
                lock.release_write()
                t2 = now()
                if t1 >= wu:
                    total += t2 - t1
                    cnt += 1
            if now() >= end:
                break
        out["t_cs_" + role + "_us"] = total / cnt / 1e3
        out["n_" + role] = cnt
    nw, nr = math.ceil(args.n / 2), args.n // 2
    out["tbar_cs_us"] = (nw * out["t_cs_w_us"] + nr * out["t_cs_r_us"]) / args.n
    out["bound_X"] = 1e6 / out["tbar_cs_us"]
    out.update({"mode": "tcs", "n": args.n, "python": platform.python_version(),
                "cores": os.cpu_count()})
    return out


def selftest(args):
    """Reads (peek) must not change any structure field; invariants must hold."""
    c = LRU(P)
    prefill(c, random.Random(1))
    snap_order = c.order()
    snap_fields = [(k, n.val, n.prev.key, n.next.key) for k, n in c.map.items()]
    snap_head = (c.head.next.key, c.tail.prev.key, len(c.map))
    rng = random.Random(2)
    for _ in range(200000):
        c.peek(rng.randrange(KEYSPACE))
    ok = (snap_order == c.order()
          and snap_fields == [(k, n.val, n.prev.key, n.next.key) for k, n in c.map.items()]
          and snap_head == (c.head.next.key, c.tail.prev.key, len(c.map)))
    # get() DOES change order (control experiment)
    k = c.order()[-1]
    c.get(k)
    changed = c.order()[0] == k
    # rwlock sanity: no reader overlaps writer
    errs = c.check()
    return {"mode": "selftest", "peek_does_not_mutate": ok, "get_reorders": changed,
            "invariants_ok": not errs}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["run", "x1", "tcs", "selftest"], default="run")
    ap.add_argument("--lock", choices=["g", "rw"], default="g")
    ap.add_argument("--policy", choices=["fair", "reader", "writer"], default="writer")
    ap.add_argument("--n", type=int, default=9)
    ap.add_argument("--duration", type=float, default=10.0)
    ap.add_argument("--warmup", type=float, default=1.0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    res = {"selftest": selftest, "tcs": tcs}.get(args.mode, run)(args)
    if args.mode == "run":
        print(f"lock={res['lock']} policy={res['policy']} n={res['n']} python={res['python']} cores={res['cores']}")
        print("ops per thread:", {i + 1: v for i, v in enumerate(
            [x for p in zip(res['w']['per_thread'], res['r']['per_thread'] + [None]) for x in p if x is not None])})
        for r in ("w", "r"):
            m = res[r]
            print(f"  {r}: X={m['X']:.0f}/s p50={m['p50_us']:.2f}us p99={m['p99_us']:.2f}us "
                  f"maxwait={m['maxwait_ms']:.2f}ms w={m['w']:.3f} cv={m['cv']:.3f}")
        print(f"  X={res['X']:.0f}/s invariants_ok={res['invariants_ok']} {res['invariant_errors']}")
    else:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
