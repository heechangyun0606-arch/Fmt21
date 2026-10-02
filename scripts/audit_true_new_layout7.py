import struct, json, csv
from pathlib import Path
from collections import Counter, defaultdict

BASE = Path("/storage/emulated/0/Download/fmt21_probe")

P2110 = BASE / "2110_people_db.dat.raw"
P2130 = BASE / "2130_people_db.dat.raw"

OUT = BASE / "official_2110_2130_forward_compare.json"
RUNCSV = BASE / "official_true_new_insertion_runs_v2.csv"

FOOTER = 28
MAXREC = 8192

TRUE_FIRST = 220791
TRUE_END = 227007

# Known verified official TRUE-NEW TYPE02 seed from earlier audit
SEED2130 = 162632

def u32(d,o):
    if o < 0 or o+4 > len(d):
        return None
    return struct.unpack_from("<I",d,o)[0]

d11=P2110.read_bytes()
d13=P2130.read_bytes()

pc11=u32(d11,0)
pc13=u32(d13,0)

print("="*72)
print("FMT21 PHASE 7 - COMMON FORWARD ANCHOR")
print("="*72)
print("2110:",f"{len(d11):,}", "persons:",pc11)
print("2130:",f"{len(d13):,}", "persons:",pc13)

# ============================================================
# decoder
# ============================================================

def decode(data,start,person_count):

    if start < 0 or start+5 > len(data):
        return None

    eid=u32(data,start)
    typ=data[start+4]

    if (
        eid is None
        or not 0 <= eid < person_count
        or typ not in (1,2,3,4)
    ):
        return None

    needle=struct.pack("<I",eid)

    p=start+5
    lim=min(len(data)-12,start+MAXREC)

    while True:

        q=data.find(needle,p,lim)

        if q < 0:
            return None

        u1=u32(data,q+4)
        u2=u32(data,q+8)

        if (
            u1 == u2
            and u1 not in (0,0xffffffff)
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
                    and 0 <= ne < person_count
                    and nt in (1,2,3,4)
                )

            if footer or next_ok:
                return {
                    "start":start,
                    "end":end,
                    "len":end-start,
                    "eid":eid,
                    "uid":u1,
                    "type":typ,
                    "anchor":q,
                    "footer":footer
                }

        p=q+1

# ============================================================
# Parse 2130 from the already proven seed.
# ============================================================

def forward(data,start,pc,label):

    out=[]
    p=start
    seen=set()

    while True:

        if p in seen:
            raise RuntimeError(label+" loop")

        seen.add(p)

        r=decode(data,p,pc)

        if r is None:
            raise RuntimeError(
                f"{label}: decode failed at {p}"
            )

        out.append(r)

        if r["footer"]:
            break

        p=r["end"]

        if len(out)%25000==0:
            print(
                label,
                len(out),
                "pos=",
                p
            )

    return out

print("\nParsing 2130 from verified seed...")

chain13=forward(
    d13,
    SEED2130,
    pc13,
    "2130"
)

print("2130 records:",len(chain13))
print("2130 end:",chain13[-1]["end"])

# ============================================================
# Find early OLD records that must already exist in 2110.
# Use several candidates, not only one.
# ============================================================

old_candidates=[]

for r in chain13:

    if r["eid"] < TRUE_FIRST:
        old_candidates.append(r)

    if len(old_candidates) >= 100:
        break

print("\nearly OLD candidates:",len(old_candidates))

# ============================================================
# Find exact same record in 2110 using terminal triple.
#
# anchor signature:
# [EID][UID][UID]
#
# Then search backward for same EID/type and require decoder
# terminal to equal that exact anchor.
# ============================================================

def find_record_by_identity(
    data,
    person_count,
    eid,
    uid,
    typ
):

    anchor_pat=(
        struct.pack("<I",eid)
        + struct.pack("<I",uid)
        + struct.pack("<I",uid)
    )

    anchors=[]
    p=0

    while True:
        q=data.find(anchor_pat,p)

        if q < 0:
            break

        anchors.append(q)
        p=q+1

    hits=[]

    eidpat=struct.pack("<I",eid)

    for anchor in anchors:

        lo=max(0,anchor-MAXREC)

        p=lo

        while True:

            q=data.find(
                eidpat,
                p,
                anchor
            )

            if q < 0:
                break

            if (
                q+5 <= len(data)
                and data[q+4] == typ
            ):
                r=decode(
                    data,
                    q,
                    person_count
                )

                if (
                    r is not None
                    and r["anchor"] == anchor
                    and r["uid"] == uid
                ):
                    hits.append(r)

            p=q+1

    # de-duplicate by start
    uniq={r["start"]:r for r in hits}

    return sorted(
        uniq.values(),
        key=lambda r:r["start"]
    )

matches=[]

print("\nSearching common OLD anchor in 2110...")

for n,r13 in enumerate(old_candidates,1):

    hits=find_record_by_identity(
        d11,
        pc11,
        r13["eid"],
        r13["uid"],
        r13["type"]
    )

    if len(hits)==1:

        r11=hits[0]

        matches.append({
            "r13":r13,
            "r11":r11
        })

        print(
            "MATCH",
            n,
            "EID=",
            r13["eid"],
            "UID=",
            r13["uid"],
            "type=",
            r13["type"],
            "2110 start=",
            r11["start"],
            "2130 start=",
            r13["start"]
        )

        if len(matches)>=10:
            break

print("unique matches:",len(matches))

if not matches:
    raise SystemExit(
        "No common OLD anchor found."
    )

# Pick earliest matched 2130 record
match=min(
    matches,
    key=lambda x:x["r13"]["start"]
)

anchor11=match["r11"]["start"]
anchor13=match["r13"]["start"]

print("\nSELECTED COMMON ANCHOR")
print(
    "EID:",
    match["r13"]["eid"]
)
print(
    "UID:",
    match["r13"]["uid"]
)
print(
    "type:",
    match["r13"]["type"]
)
print(
    "2110:",
    anchor11
)
print(
    "2130:",
    anchor13
)

# ============================================================
# Forward both versions from the SAME logical record.
# ============================================================

print("\nParsing 2110 common suffix...")
c11=forward(
    d11,
    anchor11,
    pc11,
    "2110"
)

print("\nParsing 2130 common suffix...")
c13=forward(
    d13,
    anchor13,
    pc13,
    "2130"
)

print("\nSUFFIX COUNTS")
print("2110:",len(c11))
print("2130:",len(c13))

# ============================================================
# Compare record identity, not merely EID.
# ============================================================

def key(r):
    return (
        r["eid"],
        r["uid"],
        r["type"]
    )

keys11=[key(r) for r in c11]
keys13=[key(r) for r in c13]

set11=set(keys11)
set13=set(keys13)

added=set13-set11
removed=set11-set13
common=set11 & set13

print("\nRECORD KEY SET")
print("added:",len(added))
print("removed:",len(removed))
print("common:",len(common))

# ============================================================
# TRUE NEW official people records in canonical 2130 suffix
# ============================================================

tn=[
    r for r in c13
    if TRUE_FIRST <= r["eid"] < TRUE_END
]

tn_eids=set(r["eid"] for r in tn)
tn_types=Counter(r["type"] for r in tn)

print("\nTRUE NEW IN CANONICAL 2130 SUFFIX")
print("records:",len(tn))
print("unique EIDs:",len(tn_eids))
print("types:",tn_types.most_common())

# ============================================================
# Insertion runs based on keys absent in 2110.
#
# More useful than EID set comparison because existing persons
# can change component type/UID.
# ============================================================

runs=[]

i=0

while i < len(c13):

    if key(c13[i]) not in added:
        i+=1
        continue

    j=i

    while (
        j+1 < len(c13)
        and key(c13[j+1]) in added
    ):
        j+=1

    g=c13[i:j+1]

    tc=Counter(x["type"] for x in g)

    prev=c13[i-1] if i>0 else None
    nxt=c13[j+1] if j+1<len(c13) else None

    true_new_in_run=[
        x for x in g
        if TRUE_FIRST <= x["eid"] < TRUE_END
    ]

    runs.append({
        "start":g[0]["start"],
        "end":g[-1]["end"],
        "records":len(g),
        "bytes":
            g[-1]["end"]-g[0]["start"],

        "true_new_records":
            len(true_new_in_run),

        "true_new_unique_eids":
            len(set(
                x["eid"]
                for x in true_new_in_run
            )),

        "first_eid":
            g[0]["eid"],

        "last_eid":
            g[-1]["eid"],

        "type1":tc[1],
        "type2":tc[2],
        "type3":tc[3],
        "type4":tc[4],

        "prev_eid":
            prev["eid"] if prev else None,

        "prev_uid":
            prev["uid"] if prev else None,

        "prev_type":
            prev["type"] if prev else None,

        "next_eid":
            nxt["eid"] if nxt else None,

        "next_uid":
            nxt["uid"] if nxt else None,

        "next_type":
            nxt["type"] if nxt else None,
    })

    i=j+1

run_sizes=Counter(
    r["records"] for r in runs
)

tn_run_sizes=Counter(
    r["true_new_records"]
    for r in runs
    if r["true_new_records"]
)

# ============================================================
# Common-record order preservation
# ============================================================

common11=[
    k for k in keys11
    if k in common
]

common13=[
    k for k in keys13
    if k in common
]

exact_order=(
    common11 == common13
)

# adjacency
adj11=set(zip(common11,common11[1:]))
adj13=set(zip(common13,common13[1:]))

preserved=len(adj11 & adj13)

print("\nCOMMON RECORD ORDER")
print("exact:",exact_order)
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

# ============================================================
# TRUE NEW placement neighbours
# ============================================================

new_neighbour_pairs=Counter()

for i,r in enumerate(c13):

    if not (
        TRUE_FIRST <= r["eid"] < TRUE_END
    ):
        continue

    prev=c13[i-1] if i>0 else None
    nxt=c13[i+1] if i+1<len(c13) else None

    new_neighbour_pairs[(
        prev["type"] if prev else None,
        r["type"],
        nxt["type"] if nxt else None
    )]+=1

# ============================================================
# outputs
# ============================================================

fields=[
    "start","end","records","bytes",
    "true_new_records",
    "true_new_unique_eids",
    "first_eid","last_eid",
    "type1","type2","type3","type4",
    "prev_eid","prev_uid","prev_type",
    "next_eid","next_uid","next_type"
]

with RUNCSV.open(
    "w",
    newline="",
    encoding="utf-8"
) as f:

    w=csv.DictWriter(
        f,
        fieldnames=fields
    )

    w.writeheader()
    w.writerows(runs)

summary={
    "selected_common_anchor":{
        "eid":
            match["r13"]["eid"],
        "uid":
            match["r13"]["uid"],
        "type":
            match["r13"]["type"],
        "2110_start":
            anchor11,
        "2130_start":
            anchor13
    },

    "suffix":{
        "2110_records":
            len(c11),
        "2130_records":
            len(c13)
    },

    "record_keys":{
        "added":
            len(added),
        "removed":
            len(removed),
        "common":
            len(common)
    },

    "true_new":{
        "records":
            len(tn),
        "unique_eids":
            len(tn_eids),
        "types":
            tn_types.most_common()
    },

    "order":{
        "common_exact":
            exact_order,
        "common_adjacency":
            len(adj11),
        "preserved":
            preserved,
        "preserved_pct":
            (
                preserved/len(adj11)*100
                if adj11 else None
            )
    },

    "insert_runs":{
        "count":
            len(runs),
        "size_distribution":
            run_sizes.most_common(100),
        "true_new_record_distribution":
            tn_run_sizes.most_common(100),
        "first100":
            runs[:100]
    },

    "true_new_neighbour_types":
        [
            [list(k),v]
            for k,v
            in new_neighbour_pairs.most_common(100)
        ]
}

OUT.write_text(
    json.dumps(
        summary,
        ensure_ascii=False,
        indent=2
    ),
    encoding="utf-8"
)

# ============================================================
# report
# ============================================================

print("\n"+"="*72)
print("PHASE 7 RESULT")
print("="*72)

print("\nCOMMON ANCHOR")
print(
    match["r13"]["eid"],
    match["r13"]["uid"],
    match["r13"]["type"]
)
print("2110 start:",anchor11)
print("2130 start:",anchor13)

print("\nSUFFIX")
print("2110:",len(c11))
print("2130:",len(c13))

print("\nRECORD KEYS")
print("added:",len(added))
print("removed:",len(removed))
print("common:",len(common))

print("\nTRUE NEW")
print("records:",len(tn))
print("unique EIDs:",len(tn_eids))
print("types:",tn_types.most_common())

print("\nCOMMON ORDER")
print("exact:",exact_order)
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

print("\nINSERT RUNS")
print("count:",len(runs))

print("\nRUN SIZE DISTRIBUTION")
for k,v in run_sizes.most_common(30):
    print(k,v)

print("\nTRUE-NEW RECORDS PER RUN")
for k,v in tn_run_sizes.most_common(30):
    print(k,v)

print("\nTRUE-NEW NEIGHBOUR TYPE PATTERNS")
for k,v in new_neighbour_pairs.most_common(30):
    print(k,v)

print("\nFIRST 20 INSERT RUNS")
for r in runs[:20]:
    print(
        "records=",
        r["records"],
        "true_new=",
        r["true_new_records"],
        "EIDs=",
        r["first_eid"],
        "..",
        r["last_eid"],
        "between=",
        (
            r["prev_eid"],
            r["next_eid"]
        )
    )

print("\nOUTPUT")
print(RUNCSV)
print(OUT)
print("\nDONE")
