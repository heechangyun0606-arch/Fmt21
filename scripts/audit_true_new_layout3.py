import csv, json, struct
from pathlib import Path
from collections import Counter, defaultdict

BASE = Path("/storage/emulated/0/Download/fmt21_probe")
RAW = BASE / "2130_people_db.dat.raw"
GEOM = BASE / "official_true_new_full_geometry.csv"

OUTCSV = BASE / "official_true_new_layout3_pairs.csv"
OUTJSON = BASE / "official_true_new_layout3_summary.json"

def u32(b,o):
    return struct.unpack_from("<I",b,o)[0]

data = RAW.read_bytes()

rows=[]
with GEOM.open(encoding="utf-8") as f:
    for r in csv.DictReader(f):
        rows.append({
            "eid":int(r["eid"]),
            "uid":int(r["uid"]),
            "start":int(r["start"]),
            "anchor":int(r["anchor"]),
            "delta":int(r["anchor_delta"]),
            "flags":r["flags3"]
        })

rows.sort(key=lambda r:r["start"])

print("="*70)
print("FMT21 TRUE-NEW LAYOUT PHASE 3")
print("="*70)
print("raw:",f"{len(data):,}")
print("type02:",len(rows))

# ----------------------------------------------------------
# 1. Direct consecutive TRUE-NEW type02 pairs
# ----------------------------------------------------------

pairs=[]

for i in range(len(rows)-1):
    a=rows[i]
    b=rows[i+1]
    gap=b["start"]-a["start"]

    pairs.append({
        "eid1":a["eid"],
        "uid1":a["uid"],
        "start1":a["start"],
        "delta1":a["delta"],
        "flags1":a["flags"],

        "eid2":b["eid"],
        "uid2":b["uid"],
        "start2":b["start"],
        "delta2":b["delta"],
        "flags2":b["flags"],

        "gap":gap,
        "eid_diff":b["eid"]-a["eid"]
    })

gaps=Counter(p["gap"] for p in pairs)

# ----------------------------------------------------------
# 2. Focus on player-sized gaps
# ----------------------------------------------------------

near=[
    p for p in pairs
    if 500 <= p["gap"] <= 650
]

near_gaps=Counter(p["gap"] for p in near)

print("\nConsecutive pairs total:",len(pairs))
print("500..650 gaps:",len(near))

# ----------------------------------------------------------
# 3. Gap vs anchor-delta relationship
# ----------------------------------------------------------

gap_delta1=defaultdict(Counter)
gap_delta2=defaultdict(Counter)
gap_pairdelta=defaultdict(Counter)

for p in near:
    g=p["gap"]

    gap_delta1[g][p["delta1"]]+=1
    gap_delta2[g][p["delta2"]]+=1
    gap_pairdelta[g][
        (p["delta1"],p["delta2"])
    ]+=1

# ----------------------------------------------------------
# 4. Test whether the bytes immediately before next verified
# type02 show a repeatable footer/trailer signature.
#
# No guessed component boundaries here.
# b.start is already a VERIFIED start.
# ----------------------------------------------------------

PRE_SIZES=[4,8,12,16,20,24,28,32]

pre_signatures={
    n:Counter() for n in PRE_SIZES
}

for p in near:
    end=p["start2"]

    for n in PRE_SIZES:
        if end>=n:
            pre_signatures[n][
                data[end-n:end].hex()
            ]+=1

# ----------------------------------------------------------
# 5. Bytes after current anchor
#
# Determine how far anchor sits from verified next start.
# ----------------------------------------------------------

anchor_to_next=Counter()
anchor_to_next_by_gap=defaultdict(Counter)

for p in near:
    dist=p["start2"] - (
        p["start1"] + p["delta1"]
    )

    anchor_to_next[dist]+=1
    anchor_to_next_by_gap[
        p["gap"]
    ][dist]+=1

# ----------------------------------------------------------
# 6. Is gap tied to current record variant?
# ----------------------------------------------------------

delta_gap=defaultdict(Counter)
flags_gap=defaultdict(Counter)

for p in near:
    delta_gap[p["delta1"]][p["gap"]]+=1
    flags_gap[p["flags1"]][p["gap"]]+=1

# ----------------------------------------------------------
# 7. Exact sequences around verified boundaries
#
# Store first examples for common gaps.
# ----------------------------------------------------------

examples=defaultdict(list)

for p in near:
    g=p["gap"]

    if len(examples[g]) >= 5:
        continue

    s1=p["start1"]
    s2=p["start2"]

    examples[g].append({
        "eid1":p["eid1"],
        "eid2":p["eid2"],
        "start1":s1,
        "start2":s2,
        "delta1":p["delta1"],
        "delta2":p["delta2"],

        "start1_hex":
            data[s1:s1+32].hex(),

        "before_next_32":
            data[max(0,s2-32):s2].hex(),

        "next_start_32":
            data[s2:s2+32].hex()
    })

# ----------------------------------------------------------
# 8. Check exact start header validity
# ----------------------------------------------------------

bad=[]

for r in rows:
    if (
        u32(data,r["start"]) != r["eid"]
        or data[r["start"]+4] != 2
    ):
        bad.append(r["eid"])

# ----------------------------------------------------------
# 9. CSV
# ----------------------------------------------------------

fields=[
    "eid1","uid1","start1",
    "delta1","flags1",
    "eid2","uid2","start2",
    "delta2","flags2",
    "gap","eid_diff"
]

with OUTCSV.open(
    "w",newline="",encoding="utf-8"
) as f:

    w=csv.DictWriter(f,fieldnames=fields)
    w.writeheader()
    w.writerows(pairs)

# ----------------------------------------------------------
# 10. JSON
# ----------------------------------------------------------

top_gaps=gaps.most_common(100)

summary={
    "verified_type02":len(rows),
    "bad_verified_headers":bad,

    "consecutive_pairs":len(pairs),
    "pairs_gap_500_650":len(near),

    "top_all_gaps":top_gaps,

    "near_gap_counts":
        sorted(
            near_gaps.items(),
            key=lambda x:(-x[1],x[0])
        ),

    "gap_current_delta":{
        str(g):c.most_common(30)
        for g,c in gap_delta1.items()
        if near_gaps[g] >= 3
    },

    "gap_next_delta":{
        str(g):c.most_common(30)
        for g,c in gap_delta2.items()
        if near_gaps[g] >= 3
    },

    "gap_delta_pairs":{
        str(g):[
            [list(k),v]
            for k,v in c.most_common(30)
        ]
        for g,c in gap_pairdelta.items()
        if near_gaps[g] >= 3
    },

    "anchor_to_next":
        anchor_to_next.most_common(100),

    "anchor_to_next_by_gap":{
        str(g):c.most_common(30)
        for g,c in anchor_to_next_by_gap.items()
        if near_gaps[g] >= 3
    },

    "current_delta_to_gap":{
        str(d):c.most_common(30)
        for d,c in delta_gap.items()
        if sum(c.values()) >= 5
    },

    "current_flags_to_gap":{
        fl:c.most_common(30)
        for fl,c in flags_gap.items()
        if sum(c.values()) >= 5
    },

    "pre_boundary_signatures":{
        str(n):c.most_common(30)
        for n,c in pre_signatures.items()
    },

    "examples":{
        str(g):v
        for g,v in examples.items()
        if near_gaps[g] >= 3
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

# ----------------------------------------------------------
# REPORT
# ----------------------------------------------------------

print("\n"+"="*70)
print("PHASE 3 RESULT")
print("="*70)

print("\nVERIFIED HEADERS")
print("bad:",len(bad))

print("\nTOP ALL VERIFIED TYPE02 GAPS")
for g,c in gaps.most_common(40):
    print(g,c)

print("\n500..650 GAP DISTRIBUTION")
for g,c in sorted(
    near_gaps.items(),
    key=lambda x:(-x[1],x[0])
)[:50]:
    print(g,c)

print("\nGAP -> CURRENT ANCHOR_DELTA")

for g,cnt in near_gaps.most_common(20):
    print(
        "gap",g,
        "n",cnt,
        "delta1",
        gap_delta1[g].most_common(12)
    )

print("\nGAP -> NEXT ANCHOR_DELTA")

for g,cnt in near_gaps.most_common(20):
    print(
        "gap",g,
        "n",cnt,
        "delta2",
        gap_delta2[g].most_common(12)
    )

print("\nANCHOR -> NEXT VERIFIED START DISTANCE")
for d,c in anchor_to_next.most_common(40):
    print(d,c)

print("\nTOP 16-BYTE PRE-BOUNDARY SIGNATURES")
for sig,c in pre_signatures[16].most_common(20):
    print(c,sig)

print("\nTOP 28-BYTE PRE-BOUNDARY SIGNATURES")
for sig,c in pre_signatures[28].most_common(20):
    print(c,sig)

print("\nOUTPUT")
print(OUTCSV)
print(OUTJSON)
print("\nDONE")
