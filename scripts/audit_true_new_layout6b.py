import csv, json, struct, math
from pathlib import Path
from collections import Counter

BASE = Path("/storage/emulated/0/Download/fmt21_probe")

RAW2110 = BASE / "2110_people_db.dat.raw"
RAW2130 = BASE / "2130_people_db.dat.raw"
GEOM = BASE / "official_true_new_full_geometry.csv"

OUT2110 = BASE / "canonical_full_2110.csv"
OUT2130 = BASE / "canonical_full_2130.csv"
OUTRUNS = BASE / "official_insert_runs_2110_2130.csv"
OUTJSON = BASE / "official_chain_compare_6b.json"

FOOTER = 28
MAX_RECORD = 16384

NEW_FIRST = 220791
NEW_END = 227007

def u32(b,o):
    if o < 0 or o+4 > len(b):
        return None
    return struct.unpack_from("<I",b,o)[0]

d2110 = RAW2110.read_bytes()
d2130 = RAW2130.read_bytes()

print("="*72)
print("FMT21 PEOPLE_DB PHASE 6B - FOOTER BACKWARD FULL CHAIN")
print("="*72)

print("2110:",f"{len(d2110):,}")
print("2130:",f"{len(d2130):,}")

# ============================================================
# Forward decoder used only for candidate validation
# ============================================================

def decode_record(data,start,person_count):

    if start < 0 or start+5 > len(data):
        return None

    eid=u32(data,start)
    typ=data[start+4]

    if eid is None:
        return None

    if not (0 <= eid < person_count):
        return None

    if typ not in (1,2,3,4):
        return None

    needle=struct.pack("<I",eid)

    p=start+5
    limit=min(
        len(data)-12,
        start+MAX_RECORD
    )

    while True:

        q=data.find(needle,p,limit)

        if q < 0:
            return None

        uid1=u32(data,q+4)
        uid2=u32(data,q+8)

        if (
            uid1 == uid2
            and uid1 not in (0,0xffffffff)
        ):

            end=q+12

            # footer boundary
            footer_ok=(
                end == len(data)-FOOTER
            )

            # next canonical-looking record
            next_ok=False

            if end+5 <= len(data):

                ne=u32(data,end)
                nt=data[end+4]

                next_ok=(
                    ne is not None
                    and 0 <= ne < person_count
                    and nt in (1,2,3,4)
                )

            if footer_ok or next_ok:

                return {
                    "start":start,
                    "end":end,
                    "length":end-start,
                    "eid":eid,
                    "uid":uid1,
                    "type":typ,
                    "anchor":q
                }

        p=q+1


# ============================================================
# Previous record from exact boundary
#
# current_boundary is:
#
#   next record start
# or
#   footer start
#
# Therefore previous terminal is always:
#
#   current_boundary - 12
#
# ============================================================

def find_previous(data,current_boundary,person_count):

    anchor=current_boundary-12

    if anchor < 0:
        return None,[]

    eid=u32(data,anchor)
    uid1=u32(data,anchor+4)
    uid2=u32(data,anchor+8)

    if (
        eid is None
        or uid1 != uid2
        or uid1 in (0,0xffffffff)
    ):
        return None,[]

    needle=struct.pack("<I",eid)

    lo=max(0,current_boundary-MAX_RECORD)

    p=lo
    candidates=[]

    while True:

        q=data.find(
            needle,
            p,
            anchor
        )

        if q < 0:
            break

        if (
            q+5 <= len(data)
            and data[q+4] in (1,2,3,4)
        ):

            rec=decode_record(
                data,
                q,
                person_count
            )

            if (
                rec is not None
                and rec["end"] == current_boundary
                and rec["uid"] == uid1
            ):
                candidates.append(rec)

        p=q+1

    if not candidates:
        return None,[]

    # Embedded false starts are later inside the real record.
    # The real canonical start is the earliest valid candidate.
    candidates.sort(
        key=lambda r:r["start"]
    )

    return candidates[0],candidates


# ============================================================
# Footer -> beginning
# ============================================================

def reconstruct_full(data,label):

    pc=u32(data,0)
    expected=u32(data,4)

    boundary=len(data)-FOOTER

    rev=[]
    ambiguities=[]

    print("\n"+"="*72)
    print(label,"BACKWARD")
    print("="*72)

    print("person +0:",pc)
    print("header +4:",expected)
    print("footer start:",boundary)

    while True:

        rec,cands=find_previous(
            data,
            boundary,
            pc
        )

        if rec is None:
            break

        if len(cands)>1:

            ambiguities.append({
                "boundary":boundary,
                "chosen":rec["start"],
                "candidates":[
                    {
                        "start":x["start"],
                        "eid":x["eid"],
                        "type":x["type"],
                        "length":x["length"]
                    }
                    for x in cands
                ]
            })

        rev.append(rec)

        boundary=rec["start"]

        if len(rev)%25000==0:
            print(
                "records=",
                len(rev),
                "current start=",
                boundary
            )

        if len(rev)>500000:
            raise RuntimeError("runaway")

    chain=list(reversed(rev))

    print("\n",label,"RESULT")
    print("records:",len(chain))
    print("expected +4:",expected)
    print("difference:",expected-len(chain))

    if chain:
        print("first start:",chain[0]["start"])
        print("first EID:",chain[0]["eid"])
        print("last end:",chain[-1]["end"])

    print("stopped boundary:",boundary)
    print("ambiguities:",len(ambiguities))

    return chain,ambiguities,boundary


chain2110,amb2110,stop2110 = reconstruct_full(
    d2110,
    "2110"
)

chain2130,amb2130,stop2130 = reconstruct_full(
    d2130,
    "2130"
)

# ============================================================
# Validate continuous physical chain
# ============================================================

def continuity(chain,data):

    bad=[]

    for i in range(len(chain)-1):

        if chain[i]["end"] != chain[i+1]["start"]:

            bad.append({
                "index":i,
                "end":chain[i]["end"],
                "next":chain[i+1]["start"]
            })

    footer_ok=(
        bool(chain)
        and chain[-1]["end"]
        == len(data)-FOOTER
    )

    return bad,footer_ok

bad2110,footer2110 = continuity(
    chain2110,d2110
)

bad2130,footer2130 = continuity(
    chain2130,d2130
)

# ============================================================
# Statistics
# ============================================================

def stats(chain):

    tc=Counter(r["type"] for r in chain)
    lc=Counter(r["length"] for r in chain)

    eids=[r["eid"] for r in chain]

    ec=Counter(eids)

    return {
        "types":tc,
        "lengths":lc,
        "unique":len(ec),
        "duplicates":{
            eid:c
            for eid,c in ec.items()
            if c>1
        }
    }

s2110=stats(chain2110)
s2130=stats(chain2130)

# ============================================================
# Check old 3237 candidate starts against TRUE canonical chain
# ============================================================

known=[]

with GEOM.open(encoding="utf-8") as f:

    for r in csv.DictReader(f):

        known.append({
            "eid":int(r["eid"]),
            "start":int(r["start"]),
            "uid":int(r["uid"])
        })

canon_starts={
    r["start"]:r
    for r in chain2130
}

known_canonical=[]
known_false=[]

for k in known:

    if k["start"] in canon_starts:
        known_canonical.append(k)
    else:
        known_false.append(k)

# ============================================================
# TRUE NEW numeric range in canonical 2130
# ============================================================

true_new_records=[
    r for r in chain2130
    if NEW_FIRST <= r["eid"] < NEW_END
]

true_new_types=Counter(
    r["type"]
    for r in true_new_records
)

true_new_eids=set(
    r["eid"]
    for r in true_new_records
)

missing_people_db=[
    eid
    for eid in range(NEW_FIRST,NEW_END)
    if eid not in true_new_eids
]

# ============================================================
# Compare 2110 vs 2130 canonical EID sequences
# ============================================================

seq2110=[r["eid"] for r in chain2110]
seq2130=[r["eid"] for r in chain2130]

set2110=set(seq2110)
set2130=set(seq2130)

added=set2130-set2110
removed=set2110-set2130
common=set2110 & set2130

print("\n"+"="*72)
print("VERSION COMPARISON")
print("="*72)

print("added:",len(added))
print("removed:",len(removed))
print("common:",len(common))

# ============================================================
# Common order
# ============================================================

common2110=[
    x for x in seq2110
    if x in common
]

common2130=[
    x for x in seq2130
    if x in common
]

same_order=(
    common2110 == common2130
)

print("common exact order:",same_order)

first_mismatch=None

if not same_order:

    for i,(a,b) in enumerate(
        zip(common2110,common2130)
    ):

        if a != b:
            first_mismatch={
                "index":i,
                "2110":a,
                "2130":b
            }
            break

# ============================================================
# rank correlation
# ============================================================

r2110={
    eid:i
    for i,eid
    in enumerate(common2110)
}

r2130={
    eid:i
    for i,eid
    in enumerate(common2130)
}

xs=[]
ys=[]

for eid in common:

    xs.append(r2110[eid])
    ys.append(r2130[eid])

def corr(a,b):

    if len(a)<2:
        return None

    ma=sum(a)/len(a)
    mb=sum(b)/len(b)

    num=sum(
        (x-ma)*(y-mb)
        for x,y in zip(a,b)
    )

    da=sum(
        (x-ma)**2 for x in a
    )

    db=sum(
        (y-mb)**2 for y in b
    )

    if not da or not db:
        return None

    return num/math.sqrt(da*db)

rank_corr=corr(xs,ys)

# adjacency preservation
adj2110=set(
    zip(
        common2110,
        common2110[1:]
    )
)

adj2130=set(
    zip(
        common2130,
        common2130[1:]
    )
)

preserved=len(
    adj2110 & adj2130
)

# ============================================================
# Actual insertion runs:
# 2130 EIDs absent from 2110
# ============================================================

insert_runs=[]

i=0

while i < len(chain2130):

    r=chain2130[i]

    if r["eid"] not in added:
        i+=1
        continue

    j=i

    while (
        j+1 < len(chain2130)
        and chain2130[j+1]["eid"] in added
    ):
        j+=1

    group=chain2130[i:j+1]

    tc=Counter(
        x["type"]
        for x in group
    )

    prev=(
        chain2130[i-1]
        if i>0 else None
    )

    nxt=(
        chain2130[j+1]
        if j+1<len(chain2130)
        else None
    )

    insert_runs.append({
        "start":group[0]["start"],
        "end":group[-1]["end"],
        "records":len(group),
        "bytes":
            group[-1]["end"]
            - group[0]["start"],

        "first_eid":
            group[0]["eid"],

        "last_eid":
            group[-1]["eid"],

        "min_eid":
            min(x["eid"] for x in group),

        "max_eid":
            max(x["eid"] for x in group),

        "type1":tc[1],
        "type2":tc[2],
        "type3":tc[3],
        "type4":tc[4],

        "prev_eid":
            prev["eid"] if prev else None,

        "prev_type":
            prev["type"] if prev else None,

        "next_eid":
            nxt["eid"] if nxt else None,

        "next_type":
            nxt["type"] if nxt else None
    })

run_sizes=Counter(
    r["records"]
    for r in insert_runs
)

# ============================================================
# CSV
# ============================================================

fields=[
    "start","end","length",
    "eid","uid","type","anchor"
]

def write_chain(path,chain):

    with path.open(
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        w=csv.DictWriter(
            f,
            fieldnames=fields
        )

        w.writeheader()

        for r in chain:
            w.writerow({
                k:r.get(k)
                for k in fields
            })

write_chain(
    OUT2110,
    chain2110
)

write_chain(
    OUT2130,
    chain2130
)

run_fields=[
    "start","end","records","bytes",
    "first_eid","last_eid",
    "min_eid","max_eid",
    "type1","type2","type3","type4",
    "prev_eid","prev_type",
    "next_eid","next_type"
]

with OUTRUNS.open(
    "w",
    newline="",
    encoding="utf-8"
) as f:

    w=csv.DictWriter(
        f,
        fieldnames=run_fields
    )

    w.writeheader()
    w.writerows(insert_runs)

# ============================================================
# JSON
# ============================================================

summary={

    "2110":{
        "plus0":u32(d2110,0),
        "plus4":u32(d2110,4),

        "canonical_count":
            len(chain2110),

        "difference":
            u32(d2110,4)-len(chain2110),

        "first_start":
            chain2110[0]["start"]
            if chain2110 else None,

        "footer_ok":
            footer2110,

        "continuity_bad":
            len(bad2110),

        "ambiguities":
            len(amb2110),

        "types":
            s2110["types"].most_common()
    },

    "2130":{
        "plus0":u32(d2130,0),
        "plus4":u32(d2130,4),

        "canonical_count":
            len(chain2130),

        "difference":
            u32(d2130,4)-len(chain2130),

        "first_start":
            chain2130[0]["start"]
            if chain2130 else None,

        "footer_ok":
            footer2130,

        "continuity_bad":
            len(bad2130),

        "ambiguities":
            len(amb2130),

        "types":
            s2130["types"].most_common()
    },

    "old_3237":{
        "candidate_count":
            len(known),

        "canonical_count":
            len(known_canonical),

        "false_embedded_count":
            len(known_false),

        "false_examples":
            known_false[:100]
    },

    "true_new_numeric_range":{
        "person_count":
            NEW_END-NEW_FIRST,

        "people_db_records":
            len(true_new_records),

        "missing_people_db":
            len(missing_people_db),

        "type_counts":
            true_new_types.most_common(),

        "missing_first200":
            missing_people_db[:200]
    },

    "version_compare":{
        "added":
            len(added),

        "removed":
            len(removed),

        "common":
            len(common),

        "common_order_exact":
            same_order,

        "first_mismatch":
            first_mismatch,

        "rank_correlation":
            rank_corr,

        "common_adjacency_total":
            len(adj2110),

        "common_adjacency_preserved":
            preserved,

        "common_adjacency_pct":
            (
                preserved
                / len(adj2110)
                * 100
            )
            if adj2110 else None
    },

    "insert_runs":{
        "count":
            len(insert_runs),

        "size_distribution":
            run_sizes.most_common(100),

        "first100":
            insert_runs[:100]
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

print("\n"+"="*72)
print("PHASE 6B RESULT")
print("="*72)

print("\nHEADER +4 VS CANONICAL")

print(
    "2110:",
    u32(d2110,4),
    "canonical=",
    len(chain2110),
    "diff=",
    u32(d2110,4)-len(chain2110)
)

print(
    "2130:",
    u32(d2130,4),
    "canonical=",
    len(chain2130),
    "diff=",
    u32(d2130,4)-len(chain2130)
)

print("\nFIRST START")
print(
    "2110:",
    chain2110[0]["start"]
    if chain2110 else None
)
print(
    "2130:",
    chain2130[0]["start"]
    if chain2130 else None
)

print("\nFOOTER / CONTINUITY")
print(
    "2110 footer:",
    footer2110,
    "bad links:",
    len(bad2110)
)
print(
    "2130 footer:",
    footer2130,
    "bad links:",
    len(bad2130)
)

print("\nCOMPONENT TYPES")

print("2110")
for t,c in s2110["types"].most_common():
    print(" ",t,c)

print("2130")
for t,c in s2130["types"].most_common():
    print(" ",t,c)

print("\nOLD 3237 TYPE02 CANDIDATES")
print(
    "canonical:",
    len(known_canonical)
)
print(
    "embedded false:",
    len(known_false)
)

print("\nTRUE NEW 6216")
print(
    "people_db records:",
    len(true_new_records)
)
print(
    "no people_db record:",
    len(missing_people_db)
)
print(
    "types:",
    true_new_types.most_common()
)

print("\n2110 -> 2130")
print(
    "added:",
    len(added)
)
print(
    "removed:",
    len(removed)
)
print(
    "common:",
    len(common)
)

print("\nCOMMON ORDER")
print(
    "exact:",
    same_order
)
print(
    "rank correlation:",
    rank_corr
)
print(
    "adjacency:",
    preserved,
    "/",
    len(adj2110),
    "=",
    (
        preserved/len(adj2110)*100
        if adj2110 else None
    ),
    "%"
)

if first_mismatch:
    print(
        "first mismatch:",
        first_mismatch
    )

print("\nINSERT RUNS")
print(
    "count:",
    len(insert_runs)
)

print("\nRUN SIZE DISTRIBUTION")
for n,c in run_sizes.most_common(30):
    print(n,c)

print("\nFIRST 20 RUNS")
for r in insert_runs[:20]:
    print(
        "records=",
        r["records"],
        "EIDs=",
        r["first_eid"],
        "..",
        r["last_eid"],
        "types=",
        (
            r["type1"],
            r["type2"],
            r["type3"],
            r["type4"]
        ),
        "between=",
        (
            r["prev_eid"],
            r["next_eid"]
        )
    )

print("\nOUTPUT")
print(OUT2110)
print(OUT2130)
print(OUTRUNS)
print(OUTJSON)

print("\nDONE")
