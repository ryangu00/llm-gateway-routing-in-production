#!/usr/bin/env python3
'Root-owned route applier. Imported tests may inject a temporary root and effective UID.'
import fcntl
import hashlib
import json
import os
import re
import sys
import time

ROOT = "/usr/local/etc/litellm-gateway"
LIVE = os.path.join(ROOT, "config.yaml")
STAGING = os.path.join(ROOT, "staging")
CONSUMED = os.path.join(ROOT, "consumed")
APPLIED = os.path.join(ROOT, "applied.jsonl")
LOCK = os.path.join(ROOT, ".apply.lock")


def sha(b):
    return hashlib.sha256(b).hexdigest()


def die(code, msg):
    print(f"REFUSE: {msg}", file=sys.stderr)
    sys.exit(code)


def main(argv, *, root=None, effective_uid=None):
    # Dependency injection is available to imported tests only, never as a CLI flag.
    global ROOT, LIVE, STAGING, CONSUMED, APPLIED, LOCK
    if root is not None:
        ROOT = os.fspath(root)
        LIVE, STAGING, CONSUMED, APPLIED, LOCK = [os.path.join(ROOT, p) for p in
            ("config.yaml", "staging", "consumed", "applied.jsonl", ".apply.lock")]
    effective_uid = effective_uid or os.geteuid
    if len(argv) != 2 or not re.fullmatch(r"[0-9a-f]{8,32}", argv[1]):
        die(2, " usage : route_apply.py <permit_id>(8–32  hex digits )")
    pid = argv[1]
    if effective_uid() != 0:
        die(1, " must run as  root  run (sudo)")
    pj, pc = os.path.join(STAGING, pid + ".json"), os.path.join(STAGING, pid + ".candidate.yaml")
    for p in (pj, pc):
        if not os.path.isfile(p):
            die(1, f"staging  missing  {p}")
    try:
        permit = json.load(open(pj))
    except Exception as e:
        die(1, f" permit  JSON  unreadable :{e}")
    now = time.time()
    if permit.get("id") != pid:
        die(1, " permit  id  does not match argument ")
    if not isinstance(permit.get("adr"), str) or not permit["adr"].startswith("ADR-"):
        die(1, " permit missing  adr")
    csha = str(permit.get("candidate_sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", csha):
        die(1, " permit  candidate_sha256  is not  64  hex digits ")
    for k in ("created", "expires"):
        if not isinstance(permit.get(k), (int, float)):
            die(1, f" permit missing  {k}")
    if permit["expires"] <= now:
        die(1, f" permit expired ({time.strftime('%F %T', time.localtime(permit['expires']))})")
    cand = open(pc, "rb").read()
    if sha(cand) != csha:
        die(1, f" candidate  sha {sha(cand)[:12]} !=  permit  {csha[:12]}")
    try:
        import yaml
        doc = yaml.safe_load(cand.decode("utf-8"))
        ml = doc.get("model_list") if isinstance(doc, dict) else None
        if not (isinstance(ml, list) and ml):
            die(1, " candidate must have nonempty  model_list  of  LiteLLM config")
    except SystemExit:
        raise
    except Exception as e:
        die(1, f" candidate  YAML  parse failed :{e}")
    os.makedirs(CONSUMED, exist_ok=True)
    with open(LOCK, "a+") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        mark = os.path.join(CONSUMED, pid)
        if os.path.exists(mark):
            die(1, " permit already consumed ( replay protection )")
        live_b = open(LIVE, "rb").read() if os.path.exists(LIVE) else b""
        live_sha = sha(live_b)
        base_sha = permit.get("base_sha256")
        if base_sha:
            if live_sha != base_sha:
                die(1, f" live  sha {live_sha[:12]} !=  permit  base {str(base_sha)[:12]}( base mismatch / concurrent change )")
        elif os.path.exists(APPLIED):
            last = None
            for line in open(APPLIED):
                line = line.strip()
                if line:
                    try:
                        last = json.loads(line).get("applied_sha256")
                    except Exception:
                        pass
            if last and last != live_sha:
                die(1, f" live  sha {live_sha[:12]} !=  last apply  {last[:12]}( bypass write detected )")
        ts = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
        if live_b:
            bak = f"{LIVE}.bak-{ts}-{pid}"
            with open(bak, "wb") as f:
                f.write(live_b)
            os.chmod(bak, 0o644)
        tmp = os.path.join(ROOT, f".config.yaml.tmp-{pid}")
        with open(tmp, "wb") as f:
            f.write(cand)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, LIVE)
        back = sha(open(LIVE, "rb").read())
        if back != csha:
            die(1, f" readback  sha {back[:12]} !=  candidate ( write error )")
        with open(APPLIED, "a") as f:
            f.write(json.dumps({"ts": now, "id": pid, "adr": permit["adr"], "applied_sha256": back,
                                "prev_sha256": live_sha, "by_uid": os.environ.get("SUDO_UID")}) + "\n")
        with open(mark, "w") as f:
            f.write(f"{now}\n")
    print(f"APPLIED {pid} {back[:12]} (prev {live_sha[:12]}) -> {LIVE}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
