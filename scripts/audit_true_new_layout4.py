import csv, json, struct
from pathlib import Path
from collections import Counter, defaultdict
import math

BASE = Path("/storage/emulated/0/Download/fmt21_probe")

RAW = BASE / "2130_people_db.dat.raw"
GEOM = BASE / "official_true_new_full_geometry.csv"

OUTCSV = BASE / "official_true_new_layout4.csv"
OUTJSON = BASE / "official_true_new_layout4_summary.json"

def u32(b,o):
    if o < 0 or o+4 > len(b):
        return None
    return struct.unpack_from("<I",b,o)[0]

data = RAW.read_bytes()
PERSON_COUNT = u32(data,0)

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

rows.sort(key=lambda x:x["start"])

print("="*72)
print("FMT21 TRUE-NEW LAYOUT PHASE 4")
print("="*72)
print("raw:",f"{len(data):,}")
print("person count:",PERSON_COUNT)
print("type02:",len(rows))

# ============================================================
# 1. Exact record length
#
# Phase 3 strongly established:
#
#   terminal anchor = [EID][UID][UID] = 12 bytes
#
# record length:
#
#   anchor - start + 12
# ============================================================

lengths = Counter()

for r in rows:
    r["record_len"] = r["delta"] + 12
    r["end"] = r["anchor"] + 12

    lengths[r["record_len"]] += 1

print("\nTOP EXACT RECORD LENGTHS")
for L,c in lengths.most_common(40):
    print(L,c)

# ============================================================
# 2. Confirm terminal anchor itself
# ============================================================

terminal_bad=[]

for r in rows:

    a=r["anchor"]

    eid=u32(data,a)
    uid1=u32(data,a+4)
    uid2=u32(data,a+8)

    if not (
        eid == r["eid"]
        and uid1 == r["uid"]
        and uid2 == r["uid"]
    ):
        terminal_bad.append(r["eid"])

print("\nTERMINAL ANCHOR")
print("bad:",len(terminal_bad))

# ============================================================
# 3. Validate exact next component at record end
#
# At end=anchor+12:
#
# read:
#   next EID
#   next component type byte
#
# Then search forward for a terminal [same EID][X][X].
#
# This avoids the bogus broad "plausible component" test.
# ============================================================

def find_terminal_for_eid(eid, start, limit=2500):

    end=min(len(data)-12,start+limit)

    needle=struct.pack("<I",eid)

    p=start

    while True:

        q=data.find(needle,p,end)

        if q < 0:
            return None

        if q+12 <= len(data):

            x=u32(data,q+4)
            y=u32(data,q+8)

            if (
                x == y
                and x not in (0,0xffffffff)
            ):
                return {
                    "anchor":q,
                    "uid":x,
                    "length_from_start":
                        q-start+12
                }

        p=q+1

# ============================================================
# 4. Next record statistics
# ============================================================

next_type_counter=Counter()
next_same_eid_counter=Counter()
next_length_counter=Counter()

validated_next=0
invalid_next=0

for i,r in enumerate(rows,1):

    p=r["end"]

    r["next_eid"]=None
    r["next_type"]=None
    r["next_valid"]=False
    r["next_uid"]=None
    r["next_record_len"]=None
    r["next_same_eid"]=False

    if p+5 <= len(data):

        ne=u32(data,p)
        nt=data[p+4]

        r["next_eid"]=ne
        r["next_type"]=nt

        if (
            ne is not None
            and 0 <= ne < PERSON_COUNT
            and nt < 64
        ):

            tail=find_terminal_for_eid(
                ne,
                p+5,
                2500
            )

            if tail:

                r["next_valid"]=True
                r["next_uid"]=tail["uid"]

                # start is p, tail anchor is tail["anchor"]
                r["next_record_len"] = (
                    tail["anchor"] - p + 12
                )

                r["next_same_eid"] = (
                    ne == r["eid"]
                )

                validated_next += 1

                next_type_counter[nt]+=1
                next_same_eid_counter[
                    r["next_same_eid"]
                ]+=1

                next_length_counter[
                    r["next_record_len"]
                ]+=1

            else:
                invalid_next+=1

        else:
            invalid_next+=1

    if i % 500 == 0:
        print(
            "next validation",
            i,
            "/",
            len(rows),
            "valid=",
            validated_next
        )

# ============================================================
# 5. Same-EID occurrences surrounding known type02 start
#
# Exact same EID only.
#
# This is much safer than Phase 2's arbitrary u32 scan.
# ============================================================

WINDOW_BACK=3000
WINDOW_FORWARD=3000

same_offset_type=Counter()
same_before_type=Counter()
same_after_type=Counter()

same_occurrence_counts=Counter()

for idx,r in enumerate(rows,1):

    eid=r["eid"]
    s=r["start"]

    needle=struct.pack("<I",eid)

    lo=max(0,s-WINDOW_BACK)
    hi=min(len(data),s+WINDOW_FORWARD)

    p=lo
    occ=[]

    while True:

        q=data.find(needle,p,hi)

        if q < 0:
            break

        off=q-s

        typ=(
            data[q+4]
            if q+4 < len(data)
            else None
        )

        occ.append((off,typ))

        if off != 0:
            same_offset_type[(off,typ)] += 1

            if off < 0:
                same_before_type[typ] += 1
            else:
                same_after_type[typ] += 1

        p=q+1

    r["same_eid_occurrences"]=len(occ)
    same_occurrence_counts[len(occ)] += 1

    # compact diagnostic representation
    r["same_eid_offsets"]=";".join(
        f"{off}:{typ}"
        for off,typ in occ
    )

    if idx % 500 == 0:
        print(
            "same-EID scan",
            idx,
            "/",
            len(rows)
        )

# ============================================================
# 6. UID-position Spearman
# ============================================================

def rankdata(v):

    order=sorted(
        range(len(v)),
        key=lambda i:v[i]
    )

    ranks=[0.0]*len(v)

    i=0

    while i<len(order):

        j=i+1

        while (
            j<len(order)
            and v[order[j]]
            == v[order[i]]
        ):
            j+=1

        rr=((i+1)+j)/2.0

        for k in range(i,j):
            ranks[order[k]]=rr

        i=j

    return ranks

def corr(a,b):

    if len(a)<2:
        return None

    ma=sum(a)/len(a)
    mb=sum(b)/len(b)

    num=sum(
        (x-ma)*(y-mb)
        for x,y in zip(a,b)
    )

    da=sum((x-ma)**2 for x in a)
    db=sum((y-mb)**2 for y in b)

    if da==0 or db==0:
        return None

    return num/math.sqrt(da*db)

starts=[r["start"] for r in rows]
eids=[r["eid"] for r in rows]
uids=[r["uid"] for r in rows]

eid_position_spearman=corr(
    rankdata(eids),
    rankdata(starts)
)

uid_position_spearman=corr(
    rankdata(uids),
    rankdata(starts)
)

eid_uid_spearman=corr(
    rankdata(eids),
    rankdata(uids)
)

# ============================================================
# 7. Physical consecutive TRUE-NEW pairs:
# prove exact record length relation across all gaps
# ============================================================

adjacency=Counter()

exact_next_true_new=0

start_to_row={
    r["start"]:r
    for r in rows
}

for r in rows:

    expected_end=r["end"]

    if expected_end in start_to_row:

        exact_next_true_new+=1

        nr=start_to_row[expected_end]

        adjacency[
            (
                r["record_len"],
                nr["record_len"]
            )
        ]+=1

# ============================================================
# 8. Output CSV
# ============================================================

fields=[
    "eid",
    "uid",
    "start",
    "anchor",
    "delta",
    "record_len",
    "end",
    "flags",

    "next_eid",
    "next_type",
    "next_valid",
    "next_uid",
    "next_record_len",
    "next_same_eid",

    "same_eid_occurrences",
    "same_eid_offsets"
]

with OUTCSV.open(
    "w",
    newline="",
    encoding="utf-8"
) as f:

    w=csv.DictWriter(
        f,
        fieldnames=fields
    )

    w.writeheader()

    for r in rows:
        w.writerow({
            k:r.get(k)
            for k in fields
        })

# ============================================================
# 9. JSON
# ============================================================

summary={

    "raw_size":len(data),
    "person_count":PERSON_COUNT,
    "type02_count":len(rows),

    "terminal_anchor_bad":
        terminal_bad,

    "record_lengths":
        lengths.most_common(),

    "next_component":{
        "validated":
            validated_next,

        "invalid":
            invalid_next,

        "type_counts":
            next_type_counter.most_common(),

        "same_eid":
            [
                [str(k),v]
                for k,v
                in next_same_eid_counter.items()
            ],

        "record_lengths":
            next_length_counter.most_common(100)
    },

    "same_eid":{
        "occurrence_count_distribution":
            same_occurrence_counts.most_common(),

        "before_type_counts":
            same_before_type.most_common(),

        "after_type_counts":
            same_after_type.most_common(),

        "relative_offset_type_top200":[
            [off,typ,count]
            for (off,typ),count
            in same_offset_type.most_common(200)
        ]
    },

    "correlations":{
        "eid_vs_position_spearman":
            eid_position_spearman,

        "uid_vs_position_spearman":
            uid_position_spearman,

        "eid_vs_uid_spearman":
            eid_uid_spearman
    },

    "exact_true_new_adjacency":{
        "count":
            exact_next_true_new,

        "length_pair_top100":[
            [list(k),v]
            for k,v
            in adjacency.most_common(100)
        ]
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
# 10. Report
# ============================================================

print("\n"+"="*72)
print("TRUE-NEW LAYOUT PHASE 4 RESULT")
print("="*72)

print("\nTERMINAL ANCHOR")
print(
    "valid:",
    len(rows)-len(terminal_bad)
)
print(
    "bad:",
    len(terminal_bad)
)

print("\nEXACT RECORD LENGTHS")
for L,c in lengths.most_common(40):
    print(L,c)

print("\nNEXT RECORD VALIDATION")
print(
    "validated:",
    validated_next
)
print(
    "invalid:",
    invalid_next
)

print("\nNEXT COMPONENT TYPES")
for t,c in next_type_counter.most_common(30):
    print(t,c)

print("\nNEXT SAME EID")
for k,v in next_same_eid_counter.items():
    print(k,v)

print("\nNEXT RECORD LENGTHS")
for L,c in next_length_counter.most_common(30):
    print(L,c)

print("\nSAME-EID OCCURRENCE COUNTS")
for k,v in same_occurrence_counts.most_common(30):
    print(k,v)

print("\nSAME-EID BEFORE TYPES")
for k,v in same_before_type.most_common(30):
    print(k,v)

print("\nSAME-EID AFTER TYPES")
for k,v in same_after_type.most_common(30):
    print(k,v)

print("\nTOP SAME-EID RELATIVE OFFSETS")
for (off,typ),count in same_offset_type.most_common(50):
    print(
        "offset",
        off,
        "type",
        typ,
        "count",
        count
    )

print("\nCORRELATIONS")
print(
    "EID vs position:",
    eid_position_spearman
)
print(
    "UID vs position:",
    uid_position_spearman
)
print(
    "EID vs UID:",
    eid_uid_spearman
)

print("\nEXACT TRUE-NEW -> TRUE-NEW ADJACENCY")
print(
    "count:",
    exact_next_true_new
)

print("\nTOP LENGTH PAIRS")
for k,v in adjacency.most_common(30):
    print(k,v)

print("\nOUTPUT")
print(OUTCSV)
print(OUTJSON)

print("\nDONE")
