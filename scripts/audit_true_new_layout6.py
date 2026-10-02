import csv, json, struct, bisect, math
from pathlib import Path
from collections import Counter, defaultdict

BASE = Path("/storage/emulated/0/Download/fmt21_probe")

RAW2110 = BASE / "2110_people_db.dat.raw"
RAW2130 = BASE / "2130_people_db.dat.raw"
GEOM = BASE / "official_true_new_full_geometry.csv"

OUT2130 = BASE / "canonical_chain_2130.csv"
OUT2110 = BASE / "canonical_chain_2110.csv"
OUTRUNS = BASE / "official_2110_2130_insert_runs.csv"
OUTJSON = BASE / "official_2110_2130_compare_summary.json"

FOOTER_SIZE = 28
MAX_RECORD = 8192

def u32(b,o):
    if o < 0 or o+4 > len(b):
        return None
    return struct.unpack_from("<I",b,o)[0]

for p in (RAW2110,RAW2130,GEOM):
    if not p.exists():
        raise SystemExit(f"missing: {p}")

print("Loading raws...")
d2110 = RAW2110.read_bytes()
d2130 = RAW2130.read_bytes()

print("2110:",f"{len(d2110):,}")
print("2130:",f"{len(d2130):,}")

# ============================================================
# generic canonical record decoder
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

    search=start+5
    limit=min(
        len(data)-12,
        start+MAX_RECORD
    )

    candidates=[]

    while True:

        q=data.find(
            needle,
            search,
            limit
        )

        if q < 0:
            break

        uid1=u32(data,q+4)
        uid2=u32(data,q+8)

        if (
            uid1 == uid2
            and uid1 not in (0,0xffffffff)
        ):

            end=q+12

            next_ok=False
            next_eid=None
            next_type=None

            if end+5 <= len(data):

                ne=u32(data,end)
                nt=data[end+4]

                if (
                    ne is not None
                    and 0 <= ne < person_count
                    and nt in (1,2,3,4)
                ):
                    next_ok=True
                    next_eid=ne
                    next_type=nt

            footer_ok = (
                end == len(data)-FOOTER_SIZE
            )

            if next_ok or footer_ok:

                candidates.append({
                    "start":start,
                    "eid":eid,
                    "type":typ,
                    "anchor":q,
                    "uid":uid1,
                    "end":end,
                    "length":end-start,
                    "next_ok":next_ok,
                    "next_eid":next_eid,
                    "next_type":next_type,
                    "footer_end":footer_ok
                })

        search=q+1

    if not candidates:
        return None

    # Earliest valid terminal.
    return min(
        candidates,
        key=lambda x:x["anchor"]
    )

# ============================================================
# read known type02 seeds
# ============================================================

known=[]

with GEOM.open(encoding="utf-8") as f:

    for r in csv.DictReader(f):

        known.append({
            "eid":int(r["eid"]),
            "uid":int(r["uid"]),
            "start":int(r["start"]),
            "anchor":int(r["anchor"])
        })

known.sort(key=lambda r:r["start"])

seed=known[0]["start"]

print("\nfirst known TYPE02 seed:",seed)

# ============================================================
# PHASE A
# Reconstruct backwards from first known canonical-looking seed.
#
# Current record starts at S.
# Previous terminal MUST be:
#
#   S-12:
#   [prev EID][prev UID][prev UID]
#
# Search backward for candidate previous record starts whose
# decoded end is exactly S.
#
# If multiple candidates exist, choose EARLIEST valid start.
# This eliminates embedded false starts.
# ============================================================

def find_previous(data,current,person_count):

    if current < 12:
        return None, []

    anchor=current-12

    peid=u32(data,anchor)
    uid1=u32(data,anchor+4)
    uid2=u32(data,anchor+8)

    if (
        peid is None
        or uid1 != uid2
        or uid1 in (0,0xffffffff)
    ):
        return None, []

    lo=max(0,current-MAX_RECORD)

    needle=struct.pack("<I",peid)

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
                rec
                and rec["end"] == current
                and rec["uid"] == uid1
            ):
                candidates.append(rec)

        p=q+1

    if not candidates:
        return None, []

    # canonical start should be the outer/earliest record start
    best=min(
        candidates,
        key=lambda r:r["start"]
    )

    return best,candidates

def reconstruct_backward(data,seed,person_count):

    back=[]
    current=seed
    ambiguities=[]

    while True:

        prev,candidates=find_previous(
            data,
            current,
            person_count
        )

        if prev is None:
            break

        if len(candidates)>1:

            ambiguities.append({
                "current":current,
                "candidates":[
                    {
                        "start":x["start"],
                        "eid":x["eid"],
                        "type":x["type"],
                        "length":x["length"]
                    }
                    for x in candidates
                ]
            })

        back.append(prev)
        current=prev["start"]

        if len(back)%250==0:
            print(
                "backward:",
                len(back),
                "first=",
                current
            )

        if len(back)>10000:
            raise RuntimeError(
                "backward runaway"
            )

    back.reverse()

    return back,ambiguities

pc2130=u32(d2130,0)

print("\n"+"="*72)
print("BACKWARD RECONSTRUCTION 2130")
print("="*72)

back2130,back_ambig = reconstruct_backward(
    d2130,
    seed,
    pc2130
)

print("\nbackward records:",len(back2130))
print(
    "earliest start:",
    back2130[0]["start"]
    if back2130 else seed
)
print(
    "ambiguities:",
    len(back_ambig)
)

# ============================================================
# PHASE B
# Full canonical forward chain
# ============================================================

def forward_chain(data,first,person_count):

    records=[]
    pos=first
    seen=set()

    while True:

        if pos in seen:
            return records,"loop",pos

        seen.add(pos)

        rec=decode_record(
            data,
            pos,
            person_count
        )

        if rec is None:
            return records,"decode_failed",pos

        records.append(rec)

        if rec["footer_end"]:
            return records,"footer",rec["end"]

        if not rec["next_ok"]:
            return records,"no_next",rec["end"]

        pos=rec["end"]

        if len(records)%25000==0:
            print(
                " forward:",
                len(records),
                "pos=",
                pos
            )

        if len(records)>500000:
            return records,"runaway",pos

first2130 = (
    back2130[0]["start"]
    if back2130
    else seed
)

print("\n"+"="*72)
print("FULL CANONICAL CHAIN 2130")
print("="*72)

chain2130,stop2130,end2130 = forward_chain(
    d2130,
    first2130,
    pc2130
)

print("\n2130 chain:",len(chain2130))
print("first:",first2130)
print("stop:",stop2130)
print("end:",end2130)
print(
    "footer start:",
    len(d2130)-FOOTER_SIZE
)

# ============================================================
# Find first start for 2110.
#
# Normally header layout should match 2130.
# Try same start first.
# ============================================================

pc2110=u32(d2110,0)

def test_chain_steps(data,start,pc,steps=20):

    pos=start

    for _ in range(steps):

        r=decode_record(
            data,pos,pc
        )

        if r is None:
            return False

        if r["footer_end"]:
            return True

        if not r["next_ok"]:
            return False

        pos=r["end"]

    return True

def find_first_start(data,preferred,pc):

    if test_chain_steps(
        data,preferred,pc,20
    ):
        return preferred

    print(
        "preferred first start failed, scanning..."
    )

    for p in range(0,65536):

        if p+5>len(data):
            break

        eid=u32(data,p)

        if (
            eid is None
            or not (0 <= eid < pc)
            or data[p+4] not in (1,2,3,4)
        ):
            continue

        if test_chain_steps(
            data,p,pc,20
        ):
            return p

    return None

first2110=find_first_start(
    d2110,
    first2130,
    pc2110
)

if first2110 is None:
    raise SystemExit(
        "Could not find 2110 first canonical start"
    )

print("\n"+"="*72)
print("FULL CANONICAL CHAIN 2110")
print("="*72)

print("first2110:",first2110)

chain2110,stop2110,end2110 = forward_chain(
    d2110,
    first2110,
    pc2110
)

print("\n2110 chain:",len(chain2110))
print("stop:",stop2110)
print("end:",end2110)
print(
    "footer start:",
    len(d2110)-FOOTER_SIZE
)

# ============================================================
# canonical chain analysis
# ============================================================

def analyse_chain(chain):

    eid_counter=Counter(
        r["eid"]
        for r in chain
    )

    type_counter=Counter(
        r["type"]
        for r in chain
    )

    duplicates={
        eid:c
        for eid,c in eid_counter.items()
        if c>1
    }

    return {
        "count":len(chain),
        "unique_eids":
            len(eid_counter),

        "duplicate_eid_count":
            len(duplicates),

        "duplicate_record_excess":
            sum(c-1 for c in duplicates.values()),

        "duplicate_examples":
            list(duplicates.items())[:100],

        "types":
            type_counter
    }

a2110=analyse_chain(chain2110)
a2130=analyse_chain(chain2130)

# ============================================================
# Classify the original 3237 TYPE02 seeds:
#
# canonical boundary or embedded false candidate?
# ============================================================

canonical_starts_2130={
    r["start"]
    for r in chain2130
}

embedded=[]

starts2130=[
    r["start"]
    for r in chain2130
]

for k in known:

    if k["start"] in canonical_starts_2130:
        continue

    i=bisect.bisect_right(
        starts2130,
        k["start"]
    )-1

    container=None

    if 0 <= i < len(chain2130):

        r=chain2130[i]

        if (
            r["start"]
            < k["start"]
            < r["end"]
        ):
            container=r

    embedded.append({
        "seed_eid":
            k["eid"],
        "seed_start":
            k["start"],
        "seed_anchor":
            k["anchor"],

        "container_eid":
            container["eid"]
            if container else None,

        "container_type":
            container["type"]
            if container else None,

        "container_start":
            container["start"]
            if container else None,

        "container_end":
            container["end"]
            if container else None,

        "offset_inside":
            (
                k["start"]
                - container["start"]
            )
            if container else None
    })

# ============================================================
# Compare canonical EID sets
# ============================================================

seq2110=[
    r["eid"]
    for r in chain2110
]

seq2130=[
    r["eid"]
    for r in chain2130
]

set2110=set(seq2110)
set2130=set(seq2130)

added=set2130-set2110
removed=set2110-set2130
common=set2110 & set2130

print("\nComparing EID sets...")

# official BASIC new range
BASIC_NEW_FIRST=220791
BASIC_NEW_END=227007

numeric_new2130=[
    r for r in chain2130
    if (
        BASIC_NEW_FIRST
        <= r["eid"]
        < BASIC_NEW_END
    )
]

numeric_new_types=Counter(
    r["type"]
    for r in numeric_new2130
)

# ============================================================
# Common-record order preservation
# ============================================================

common2110=[
    eid
    for eid in seq2110
    if eid in common
]

common2130=[
    eid
    for eid in seq2130
    if eid in common
]

same_common_order=(
    common2110 == common2130
)

first_mismatch=None

if not same_common_order:

    m=min(
        len(common2110),
        len(common2130)
    )

    for i in range(m):

        if common2110[i] != common2130[i]:

            first_mismatch={
                "index":i,
                "2110":
                    common2110[i],
                "2130":
                    common2130[i]
            }
            break

# Spearman physical ranks of common EIDs
rank2110={
    eid:i
    for i,eid
    in enumerate(common2110)
}

rank2130={
    eid:i
    for i,eid
    in enumerate(common2130)
}

xs=[]
ys=[]

for eid in common:
    xs.append(rank2110[eid])
    ys.append(rank2130[eid])

def pearson(a,b):

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

common_rank_corr=pearson(xs,ys)

# Common adjacency preservation
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

preserved_adj=len(
    adj2110 & adj2130
)

# ============================================================
# True insertion runs:
#
# Records in 2130 whose EID does not exist in 2110.
# ============================================================

insert_runs=[]

i=0

while i<len(chain2130):

    if chain2130[i]["eid"] not in added:
        i+=1
        continue

    j=i

    while (
        j+1<len(chain2130)
        and chain2130[j+1]["eid"] in added
    ):
        j+=1

    group=chain2130[i:j+1]

    prev_common=(
        chain2130[i-1]
        if i>0
        and chain2130[i-1]["eid"] in common
        else None
    )

    next_common=(
        chain2130[j+1]
        if j+1<len(chain2130)
        and chain2130[j+1]["eid"] in common
        else None
    )

    tc=Counter(
        r["type"]
        for r in group
    )

    insert_runs.append({
        "start":
            group[0]["start"],

        "end":
            group[-1]["end"],

        "records":
            len(group),

        "bytes":
            group[-1]["end"]
            - group[0]["start"],

        "first_eid":
            group[0]["eid"],

        "last_eid":
            group[-1]["eid"],

        "min_eid":
            min(r["eid"] for r in group),

        "max_eid":
            max(r["eid"] for r in group),

        "type1":tc[1],
        "type2":tc[2],
        "type3":tc[3],
        "type4":tc[4],

        "prev_common_eid":
            prev_common["eid"]
            if prev_common else None,

        "next_common_eid":
            next_common["eid"]
            if next_common else None
    })

    i=j+1

run_sizes=Counter(
    r["records"]
    for r in insert_runs
)

# ============================================================
# Write canonical CSVs
# ============================================================

fields=[
    "start",
    "end",
    "length",
    "eid",
    "uid",
    "type",
    "anchor",
    "next_eid",
    "next_type"
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
    "prev_common_eid",
    "next_common_eid"
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

    "headers":{
        "2110":{
            "plus0":u32(d2110,0),
            "plus4":u32(d2110,4)
        },
        "2130":{
            "plus0":u32(d2130,0),
            "plus4":u32(d2130,4)
        }
    },

    "canonical":{
        "2110":{
            "first_start":
                first2110,
            "records":
                len(chain2110),
            "footer_end":
                end2110,
            "stop":
                stop2110,
            "unique_eids":
                a2110["unique_eids"],
            "duplicate_eids":
                a2110["duplicate_eid_count"],
            "types":
                a2110["types"].most_common()
        },

        "2130":{
            "first_start":
                first2130,
            "records":
                len(chain2130),
            "footer_end":
                end2130,
            "stop":
                stop2130,
            "unique_eids":
                a2130["unique_eids"],
            "duplicate_eids":
                a2130["duplicate_eid_count"],
            "types":
                a2130["types"].most_common()
        }
    },

    "header_plus4_vs_chain":{
        "2110_difference":
            u32(d2110,4)
            - len(chain2110),

        "2130_difference":
            u32(d2130,4)
            - len(chain2130)
    },

    "known_type02":{
        "original_candidates":
            len(known),

        "canonical":
            len(known)-len(embedded),

        "embedded":
            len(embedded),

        "embedded_examples":
            embedded[:100]
    },

    "numeric_basic_new_range":{
        "range":[
            BASIC_NEW_FIRST,
            BASIC_NEW_END-1
        ],

        "canonical_records":
            len(numeric_new2130),

        "types":
            numeric_new_types.most_common()
    },

    "version_compare":{
        "added_eids":
            len(added),

        "removed_eids":
            len(removed),

        "common_eids":
            len(common),

        "common_order_exact":
            same_common_order,

        "first_common_order_mismatch":
            first_mismatch,

        "common_rank_correlation":
            common_rank_corr,

        "common_adjacencies_2110":
            len(adj2110),

        "common_adjacencies_preserved":
            preserved_adj,

        "common_adjacency_preservation_pct":
            (
                preserved_adj
                / len(adj2110)*100
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
    },

    "backward2130":{
        "records_before_seed":
            len(back2130),

        "ambiguities":
            len(back_ambig),

        "ambiguity_examples":
            back_ambig[:100]
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
print("PHASE 6 RESULT")
print("="*72)

print("\n2130 BACKWARD")
print(
    "records before first seed:",
    len(back2130)
)
print(
    "first canonical start:",
    first2130
)
print(
    "ambiguities:",
    len(back_ambig)
)

print("\nCANONICAL CHAIN COUNTS")

print(
    "2110 header +4:",
    u32(d2110,4)
)
print(
    "2110 canonical:",
    len(chain2110)
)
print(
    "difference:",
    u32(d2110,4)-len(chain2110)
)

print()

print(
    "2130 header +4:",
    u32(d2130,4)
)
print(
    "2130 canonical:",
    len(chain2130)
)
print(
    "difference:",
    u32(d2130,4)-len(chain2130)
)

print("\nFOOTER")

print(
    "2110 chain end:",
    end2110,
    "expected:",
    len(d2110)-FOOTER_SIZE,
    stop2110
)

print(
    "2130 chain end:",
    end2130,
    "expected:",
    len(d2130)-FOOTER_SIZE,
    stop2130
)

print("\nUNIQUE EIDS")

print(
    "2110 records / unique:",
    len(chain2110),
    a2110["unique_eids"],
    "duplicates:",
    a2110["duplicate_eid_count"]
)

print(
    "2130 records / unique:",
    len(chain2130),
    a2130["unique_eids"],
    "duplicates:",
    a2130["duplicate_eid_count"]
)

print("\nCOMPONENT TYPES 2110")
for k,v in a2110["types"].most_common():
    print(k,v)

print("\nCOMPONENT TYPES 2130")
for k,v in a2130["types"].most_common():
    print(k,v)

print("\nOLD 3237 TYPE02 CANDIDATES")
print(
    "canonical:",
    len(known)-len(embedded)
)
print(
    "embedded/false:",
    len(embedded)
)

for x in embedded[:30]:
    print(
        " embedded:",
        x["seed_eid"],
        "seed=",
        x["seed_start"],
        "inside=",
        x["container_eid"],
        "type=",
        x["container_type"],
        "offset=",
        x["offset_inside"]
    )

print("\nOFFICIAL BASIC-NEW RANGE IN CANONICAL PEOPLE_DB")
print(
    "canonical:",
    len(numeric_new2130),
    "/",
    BASIC_NEW_END-BASIC_NEW_FIRST
)
print(
    "types:",
    numeric_new_types.most_common()
)

print("\n2110 -> 2130 EID SET")
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
    same_common_order
)
print(
    "rank correlation:",
    common_rank_corr
)
print(
    "adjacency:",
    preserved_adj,
    "/",
    len(adj2110),
    "=",
    (
        preserved_adj/len(adj2110)*100
        if adj2110 else None
    ),
    "%"
)

if first_mismatch:
    print(
        "first mismatch:",
        first_mismatch
    )

print("\nACTUAL 2130 INSERT RUNS")
print(
    "runs:",
    len(insert_runs)
)

print("\nRUN SIZE DISTRIBUTION")
for n,c in run_sizes.most_common(40):
    print(n,c)

print("\nFIRST 20 INSERT RUNS")
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
            r["prev_common_eid"],
            r["next_common_eid"]
        )
    )

print("\nOUTPUT")
print(OUT2110)
print(OUT2130)
print(OUTRUNS)
print(OUTJSON)

print("\nDONE")
