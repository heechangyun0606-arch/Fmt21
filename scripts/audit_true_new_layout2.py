import csv, json, struct, statistics
from pathlib import Path
from collections import Counter, defaultdict

BASE = Path("/storage/emulated/0/Download/fmt21_probe")

RAW = BASE / "2130_people_db.dat.raw"
GEOM = BASE / "official_true_new_full_geometry.csv"

OUTCSV = BASE / "official_true_new_layout2.csv"
OUTJSON = BASE / "official_true_new_layout2_summary.json"

def u32(b, o):
    if o < 0 or o + 4 > len(b):
        return None
    return struct.unpack_from("<I", b, o)[0]

if not RAW.exists():
    raise SystemExit(f"missing: {RAW}")

if not GEOM.exists():
    raise SystemExit(f"missing: {GEOM}")

print("Loading 2130 raw...")
data = RAW.read_bytes()
print("raw:", f"{len(data):,}")

rows = []

with GEOM.open(encoding="utf-8") as f:
    for r in csv.DictReader(f):
        rows.append({
            "eid": int(r["eid"]),
            "uid": int(r["uid"]),
            "start": int(r["start"]),
            "anchor": int(r["anchor"]),
            "anchor_delta": int(r["anchor_delta"]),
            "flags3": r["flags3"],
        })

print("official TRUE-NEW type02:", len(rows))

if len(rows) != 3237:
    print("WARNING: expected 3237")

# ============================================================
# 1. Validate known starts
# ============================================================

valid = []

for r in rows:
    s = r["start"]

    if u32(data, s) != r["eid"]:
        continue

    if data[s+4] != 2:
        continue

    valid.append(r)

print("validated:", len(valid))

# ============================================================
# 2. Search local neighbourhood
#
# For each known type02 start, search ±2048 bytes for structures
# that look like:
#
#   [plausible EID u32][component type byte]
#
# We deliberately do NOT yet claim every candidate is a real
# component boundary.
#
# The point is to discover repeated relative offsets.
# ============================================================

WINDOW = 2048

# person EIDs in this DB are below header count
PERSON_COUNT = u32(data, 0)

def plausible_component(p):
    if p < 8 or p + 8 > len(data):
        return False

    eid = u32(data, p)
    typ = data[p+4]

    if eid is None:
        return False

    if not (0 <= eid < PERSON_COUNT):
        return False

    # observed component IDs are small.
    if typ > 32:
        return False

    return True


offset_counts = Counter()
offset_type_counts = Counter()

local_candidates = {}

print("\nScanning local candidate boundaries...")

for n, r in enumerate(valid, 1):
    s = r["start"]

    lo = max(8, s-WINDOW)
    hi = min(len(data)-8, s+WINDOW)

    cand = []

    for p in range(lo, hi+1):
        if plausible_component(p):
            off = p-s
            typ = data[p+4]
            eid = u32(data,p)

            cand.append((off,p,eid,typ))
            offset_counts[off] += 1
            offset_type_counts[(off,typ)] += 1

    local_candidates[s] = cand

    if n % 500 == 0:
        print(n, "/", len(valid))

# ============================================================
# 3. Strong repeated offsets
#
# If a relative offset occurs around a large fraction of the
# 3237 official records, it is structural rather than random.
# ============================================================

threshold = max(20, int(len(valid)*0.05))

strong_offsets = [
    (off,count)
    for off,count in offset_counts.items()
    if count >= threshold and off != 0
]

strong_offsets.sort(
    key=lambda x:(-x[1], abs(x[0]))
)

print("\nStrong repeated relative offsets:", len(strong_offsets))

for x in strong_offsets[:50]:
    print(x)

# ============================================================
# 4. Immediate plausible candidate before/after each type02
#
# This is diagnostic only.
# ============================================================

results = []

for r in valid:
    s = r["start"]

    candidates = local_candidates[s]

    before = [
        x for x in candidates
        if x[0] < 0
    ]

    after = [
        x for x in candidates
        if x[0] > 4
    ]

    nearest_before = (
        max(before, key=lambda x:x[0])
        if before else None
    )

    nearest_after = (
        min(after, key=lambda x:x[0])
        if after else None
    )

    out = dict(r)

    if nearest_before:
        off,p,eid,typ = nearest_before
        out["prev_candidate_offset"] = off
        out["prev_candidate_pos"] = p
        out["prev_candidate_eid"] = eid
        out["prev_candidate_type"] = typ
    else:
        out["prev_candidate_offset"] = None
        out["prev_candidate_pos"] = None
        out["prev_candidate_eid"] = None
        out["prev_candidate_type"] = None

    if nearest_after:
        off,p,eid,typ = nearest_after
        out["next_candidate_offset"] = off
        out["next_candidate_pos"] = p
        out["next_candidate_eid"] = eid
        out["next_candidate_type"] = typ
    else:
        out["next_candidate_offset"] = None
        out["next_candidate_pos"] = None
        out["next_candidate_eid"] = None
        out["next_candidate_type"] = None

    results.append(out)

# ============================================================
# 5. Anchor-relative byte signatures
#
# Earlier results showed anchor deltas such as:
# 534,539,527,549...
#
# Group records by anchor_delta and inspect the bytes around:
#   start
#   anchor-16
#   anchor
#
# This can reveal record variants.
# ============================================================

delta_groups = defaultdict(list)

for r in valid:
    delta_groups[r["anchor_delta"]].append(r)

delta_summary = []

for delta, group in delta_groups.items():

    start_flags = Counter(
        data[r["start"]+4:r["start"]+12].hex()
        for r in group
    )

    anchor_pre = Counter(
        data[r["anchor"]-16:r["anchor"]].hex()
        for r in group
        if r["anchor"] >= 16
    )

    anchor_sig = Counter(
        data[r["anchor"]:r["anchor"]+16].hex()
        for r in group
        if r["anchor"]+16 <= len(data)
    )

    delta_summary.append({
        "delta": delta,
        "count": len(group),
        "top_start_signature":
            start_flags.most_common(5),
        "top_anchor_pre":
            anchor_pre.most_common(5),
        "top_anchor_signature":
            anchor_sig.most_common(5),
    })

delta_summary.sort(
    key=lambda x:-x["count"]
)

# ============================================================
# 6. Physical neighbours among the 3237 TRUE-NEW players
#
# This is NOT record length.
# It tells us how official new players are interleaved with
# other components.
# ============================================================

physical = sorted(valid, key=lambda x:x["start"])

gaps = []

for i in range(len(physical)-1):
    gap = physical[i+1]["start"] - physical[i]["start"]
    gaps.append(gap)

gap_counter = Counter(gaps)

# ============================================================
# 7. Test candidate end offsets
#
# Earlier records often appeared around 553/556 bytes.
# Test a wider range and ask:
#
# At start + L, how often is there a plausible component?
#
# This gives us evidence for actual end boundaries without
# assuming 553/556 in advance.
# ============================================================

length_hits = Counter()
length_type_hits = defaultdict(Counter)

MINLEN = 400
MAXLEN = 750

print("\nTesting possible record lengths 400..750...")

for r in valid:
    s = r["start"]

    for L in range(MINLEN,MAXLEN+1):
        p = s+L

        if plausible_component(p):
            length_hits[L] += 1
            length_type_hits[L][data[p+4]] += 1

best_lengths = sorted(
    length_hits.items(),
    key=lambda x:-x[1]
)

# ============================================================
# 8. Look specifically at exact next boundaries for strongest
# candidate lengths.
# ============================================================

TOP_LENGTHS = [
    L for L,c in best_lengths[:30]
]

for r in results:
    s = r["start"]

    exact = []

    for L in TOP_LENGTHS:
        p=s+L

        if plausible_component(p):
            exact.append({
                "length": L,
                "next_eid": u32(data,p),
                "next_type": data[p+4]
            })

    r["exact_boundary_candidates"] = exact

# ============================================================
# 9. CSV
# ============================================================

fields = [
    "eid",
    "uid",
    "start",
    "anchor",
    "anchor_delta",
    "flags3",
    "prev_candidate_offset",
    "prev_candidate_pos",
    "prev_candidate_eid",
    "prev_candidate_type",
    "next_candidate_offset",
    "next_candidate_pos",
    "next_candidate_eid",
    "next_candidate_type",
]

with OUTCSV.open("w",newline="",encoding="utf-8") as f:
    w=csv.DictWriter(f,fieldnames=fields)
    w.writeheader()

    for r in results:
        w.writerow({
            k:r.get(k)
            for k in fields
        })

# ============================================================
# 10. JSON
# ============================================================

summary = {
    "raw_size": len(data),
    "person_count": PERSON_COUNT,
    "input_type02_count": len(rows),
    "validated_type02_count": len(valid),

    "strong_relative_offsets": [
        [off,count]
        for off,count in strong_offsets[:100]
    ],

    "possible_record_lengths": [
        {
            "length": L,
            "hits": count,
            "next_type_counts":
                length_type_hits[L].most_common(20)
        }
        for L,count in best_lengths[:100]
    ],

    "anchor_delta_groups":
        delta_summary[:100],

    "physical_new_player_gap_top100":
        gap_counter.most_common(100),

    "physical_gap": {
        "min": min(gaps) if gaps else None,
        "median": statistics.median(gaps) if gaps else None,
        "max": max(gaps) if gaps else None,
    }
}

OUTJSON.write_text(
    json.dumps(
        summary,
        ensure_ascii=False,
        indent=2
    ),
    encoding="utf-8"
)

# ============================================================
# REPORT
# ============================================================

print("\n"+"="*70)
print("TRUE-NEW LAYOUT PHASE 2")
print("="*70)

print("\nvalidated:",len(valid))

print("\nTOP POSSIBLE RECORD LENGTHS")
for L,c in best_lengths[:30]:
    print(
        L,
        c,
        "next types:",
        length_type_hits[L].most_common(8)
    )

print("\nTOP PHYSICAL TRUE-NEW GAPS")
for k,v in gap_counter.most_common(30):
    print(k,v)

print("\nTOP ANCHOR DELTA GROUPS")
for x in delta_summary[:30]:
    print(
        x["delta"],
        x["count"],
        "start_sig=",
        x["top_start_signature"][:3]
    )

print("\nSTRONG RELATIVE OFFSETS")
for off,c in strong_offsets[:50]:
    print(off,c)

print("\nOUTPUT")
print(OUTCSV)
print(OUTJSON)

print("\nDONE")
