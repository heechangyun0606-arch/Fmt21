import csv, struct, json
from pathlib import Path
from collections import Counter

BASE = Path("/storage/emulated/0/Download/fmt21_probe")

RAW11 = BASE / "2110_people_db.dat.raw"
CHAIN13 = BASE / "official_people_chain_2130.csv"

OUT11 = BASE / "fast_canonical_suffix_2110.csv"
OUTJSON = BASE / "fast_2110_2130_compare.json"

FOOTER = 28
MAXREC = 8192
NEW_FIRST = 220791
NEW_END = 227007

def u32(d,o):
    if o < 0 or o+4 > len(d):
        return None
    return struct.unpack_from("<I",d,o)[0]

print("="*72)
print("FAST COMMON ANCHOR / SUFFIX CACHE")
print("="*72)

# ------------------------------------------------------------
# Only load 2110 raw.
# 2130 is reused from Phase 5 CSV.
# ------------------------------------------------------------

print("Loading 2110 raw...")
d11 = RAW11.read_bytes()
pc11 = u32(d11,0)

print("2110 raw:",f"{len(d11):,}")
print("persons:",pc11)

# ------------------------------------------------------------
# Load only Phase-5 canonical segment 1.
# ------------------------------------------------------------

print("\nLoading cached 2130 chain...")

c13=[]

with CHAIN13.open(encoding="utf-8") as f:
    for r in csv.DictReader(f):

        if int(r["segment_id"]) != 1:
            continue

        c13.append({
            "start":int(r["start"]),
            "end":int(r["end"]),
            "length":int(r["length"]),
            "eid":int(r["eid"]),
            "uid":int(r["uid"]),
            "type":int(r["type"]),
            "anchor":int(r["anchor"])
        })

print("cached 2130 records:",len(c13))

if len(c13) != 225142:
    print("WARNING: expected segment-1 count 225142")

# ------------------------------------------------------------
# Decoder
# ------------------------------------------------------------

def decode(data,start,pc):

    if start < 0 or start+5 > len(data):
        return None

    eid=u32(data,start)
    typ=data[start+4]

    if (
        eid is None
        or not 0 <= eid < pc
        or typ not in (1,2,3,4)
    ):
        return None

    pat=struct.pack("<I",eid)

    p=start+5
    limit=min(len(data)-12,start+MAXREC)

    while True:

        q=data.find(pat,p,limit)

        if q < 0:
            return None

        x=u32(data,q+4)
        y=u32(data,q+8)

        if (
            x == y
            and x not in (0,0xffffffff)
        ):

            end=q+12

            footer=(
                end == len(data)-FOOTER
            )

            next_ok=False

            if end+5 <= len(data):

                ne=u32(data,end)
                nt=data[end+4]

                next_ok=(
                    ne is not None
                    and 0 <= ne < pc
                    and nt in (1,2,3,4)
                )

            if footer or next_ok:

                return {
                    "start":start,
                    "end":end,
                    "length":end-start,
                    "eid":eid,
                    "uid":x,
                    "type":typ,
                    "anchor":q,
                    "footer":footer
                }

        p=q+1

# ------------------------------------------------------------
# Quick survival test
#
# A random embedded [EID][type] might decode once.
# A true canonical start should survive repeatedly.
# ------------------------------------------------------------

def survives(start,steps=250):

    p=start

    for n in range(steps):

        r=decode(d11,p,pc11)

        if r is None:
            return False,n

        if r["footer"]:
            return True,n+1

        p=r["end"]

    return True,steps

# ------------------------------------------------------------
# Search common OLD type-02 records.
#
# IMPORTANT:
# no terminal-UID equality across versions required.
# ------------------------------------------------------------

print("\nSearching 2110 common anchor using EID + TYPE only...")

targets=[
    r for r in c13
    if (
        r["eid"] < NEW_FIRST
        and r["type"] == 2
    )
]

print("available OLD type02 targets:",len(targets))

selected=None

# Usually should resolve very early.
# Cap at first 1000 candidates.
for ti,r13 in enumerate(targets[:1000],1):

    pattern=(
        struct.pack("<I",r13["eid"])
        + bytes([2])
    )

    pos=0
    hits=[]

    while True:

        q=d11.find(pattern,pos)

        if q < 0:
            break

        rec=decode(d11,q,pc11)

        if rec is not None:

            ok,depth=survives(q,250)

            if ok:
                hits.append((rec,depth))

        pos=q+1

    if hits:

        # Prefer record length nearest to 2130 version.
        hits.sort(
            key=lambda x:
                abs(
                    x[0]["length"]
                    - r13["length"]
                )
        )

        rec,depth=hits[0]

        print(
            "FOUND",
            "target#",ti,
            "EID=",r13["eid"],
            "2130len=",r13["length"],
            "2110start=",rec["start"],
            "2110len=",rec["length"],
            "survive=",depth,
            "candidate_hits=",len(hits)
        )

        selected={
            "r11":rec,
            "r13":r13
        }

        break

    if ti % 50 == 0:
        print("searched:",ti)

if selected is None:
    raise SystemExit(
        "\nNo common EID/type anchor in first 1000 targets."
    )

anchor11=selected["r11"]["start"]
anchor13=selected["r13"]["start"]

print("\nCOMMON ANCHOR")
print("EID:",selected["r13"]["eid"])
print("TYPE:",selected["r13"]["type"])
print("2110:",anchor11)
print("2130:",anchor13)

# ------------------------------------------------------------
# 2110 forward ONCE.
# This output will be cached forever.
# ------------------------------------------------------------

print("\nParsing 2110 suffix ONCE...")

c11=[]
p=anchor11
seen=set()

while True:

    if p in seen:
        raise RuntimeError("loop")

    seen.add(p)

    r=decode(d11,p,pc11)

    if r is None:
        raise RuntimeError(
            f"decode failed at {p}"
        )

    c11.append(r)

    if r["footer"]:
        break

    p=r["end"]

    if len(c11)%25000==0:
        print(
            len(c11),
            "pos=",
            p
        )

print("2110 suffix:",len(c11))

# ------------------------------------------------------------
# Cut cached 2130 chain at same common record
# ------------------------------------------------------------

index13=None

for i,r in enumerate(c13):
    if r["start"] == anchor13:
        index13=i
        break

if index13 is None:
    raise RuntimeError("2130 anchor not in cache")

s13=c13[index13:]

print("2130 suffix:",len(s13))

# ------------------------------------------------------------
# Save 2110 suffix cache
# ------------------------------------------------------------

fields=[
    "start","end","length",
    "eid","uid","type","anchor"
]

with OUT11.open(
    "w",
    newline="",
    encoding="utf-8"
) as f:

    w=csv.DictWriter(
        f,
        fieldnames=fields, extrasaction="ignore"
    )

    w.writeheader()
    w.writerows(c11)

print("saved 2110 cache:",OUT11)

# ------------------------------------------------------------
# Compare using (EID, TYPE), not terminal value.
# ------------------------------------------------------------

def key(r):
    return (
        r["eid"],
        r["type"]
    )

k11=[key(r) for r in c11]
k13=[key(r) for r in s13]

set11=set(k11)
set13=set(k13)

added=set13-set11
removed=set11-set13
common=set11 & set13

print("\nKEY SET (EID,TYPE)")
print("added:",len(added))
print("removed:",len(removed))
print("common:",len(common))

# ------------------------------------------------------------
# Common sequence order
# ------------------------------------------------------------

common11=[
    k for k in k11
    if k in common
]

common13=[
    k for k in k13
    if k in common
]

exact=(
    common11 == common13
)

adj11=set(zip(common11,common11[1:]))
adj13=set(zip(common13,common13[1:]))

preserved=len(adj11 & adj13)

print("\nCOMMON ORDER")
print("exact:",exact)

print(
    "adjacency:",
    preserved,
    "/",
    len(adj11),
    "=",
    (
        preserved/len(adj11)*100
        if adj11 else 0
    ),
    "%"
)

# ------------------------------------------------------------
# TRUE NEW records in 2130 suffix
# ------------------------------------------------------------

tn=[
    r for r in s13
    if NEW_FIRST <= r["eid"] < NEW_END
]

tn_eids=set(r["eid"] for r in tn)
tn_types=Counter(r["type"] for r in tn)

print("\nTRUE NEW")
print("records:",len(tn))
print("unique EIDs:",len(tn_eids))
print("types:",tn_types.most_common())

# ------------------------------------------------------------
# New-key insertion runs
# ------------------------------------------------------------

runs=[]

i=0

while i<len(s13):

    if key(s13[i]) not in added:
        i+=1
        continue

    j=i

    while (
        j+1<len(s13)
        and key(s13[j+1]) in added
    ):
        j+=1

    g=s13[i:j+1]

    true_new=[
        x for x in g
        if NEW_FIRST <= x["eid"] < NEW_END
    ]

    runs.append({
        "records":len(g),
        "true_new":len(true_new),
        "first_eid":g[0]["eid"],
        "last_eid":g[-1]["eid"],
        "prev_eid":
            s13[i-1]["eid"]
            if i else None,
        "next_eid":
            s13[j+1]["eid"]
            if j+1<len(s13)
            else None
    })

    i=j+1

run_counter=Counter(
    r["records"] for r in runs
)

tn_counter=Counter(
    r["true_new"]
    for r in runs
    if r["true_new"]
)

print("\nINSERT RUNS:",len(runs))

print("\nRUN SIZE")
for k,v in run_counter.most_common(30):
    print(k,v)

print("\nTRUE NEW PER RUN")
for k,v in tn_counter.most_common(30):
    print(k,v)

print("\nFIRST 20 RUNS")
for r in runs[:20]:
    print(r)

summary={
    "anchor":{
        "eid":selected["r13"]["eid"],
        "type":selected["r13"]["type"],
        "2110_start":anchor11,
        "2130_start":anchor13,
        "2110_length":
            selected["r11"]["length"],
        "2130_length":
            selected["r13"]["length"]
    },

    "suffix":{
        "2110":len(c11),
        "2130":len(s13)
    },

    "key_set":{
        "added":len(added),
        "removed":len(removed),
        "common":len(common)
    },

    "order":{
        "exact":exact,
        "adjacency_total":len(adj11),
        "adjacency_preserved":preserved,
        "pct":
            preserved/len(adj11)*100
            if adj11 else None
    },

    "true_new":{
        "records":len(tn),
        "unique_eids":len(tn_eids),
        "types":tn_types.most_common()
    },

    "runs":{
        "count":len(runs),
        "sizes":run_counter.most_common(),
        "true_new_per_run":
            tn_counter.most_common(),
        "first100":runs[:100]
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

print("\nOUTPUT")
print(OUT11)
print(OUTJSON)
print("\nDONE")
