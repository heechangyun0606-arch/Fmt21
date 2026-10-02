import csv, json, struct
from pathlib import Path
from collections import Counter

BASE = Path("/storage/emulated/0/Download/fmt21_probe")

RAW = BASE / "2130_people_db.dat.raw"
GEOM = BASE / "official_true_new_full_geometry.csv"

OUTCSV = BASE / "official_people_chain_2130.csv"
RUNCSV = BASE / "official_true_new_runs_2130.csv"
OUTJSON = BASE / "official_people_chain_2130_summary.json"

NEW_FIRST = 220791
NEW_END   = 227007

def u32(b,o):
    if o < 0 or o+4 > len(b):
        return None
    return struct.unpack_from("<I",b,o)[0]

data = RAW.read_bytes()
PERSON_COUNT = u32(data,0)

# ------------------------------------------------------------
# known verified TYPE-02 records
# ------------------------------------------------------------

known = {}

with GEOM.open(encoding="utf-8") as f:
    for r in csv.DictReader(f):
        start=int(r["start"])
        known[start]={
            "eid":int(r["eid"]),
            "uid":int(r["uid"]),
            "anchor":int(r["anchor"]),
            "delta":int(r["anchor_delta"])
        }

known_starts=sorted(known)

print("="*72)
print("FMT21 PEOPLE_DB CHAIN RECONSTRUCTION - PHASE 5")
print("="*72)

print("raw:",f"{len(data):,}")
print("person_count:",PERSON_COUNT)
print("verified TYPE02 seeds:",len(known_starts))

# ============================================================
# Record decoder
#
# Start is known.
#
# Search terminal:
#
#   [same EID][UID][UID]
#
# Extra validation:
# terminal+12 must look exactly like another record:
#
#   next EID < PERSON_COUNT
#   next type in 1..4
#
# ============================================================

MAX_RECORD = 8192

def decode_record(start):

    if start < 0 or start+5 > len(data):
        return None

    eid=u32(data,start)
    typ=data[start+4]

    if eid is None:
        return None

    if not (0 <= eid < PERSON_COUNT):
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

            # Standard case:
            # terminal is immediately followed by valid next record.
            next_ok=False
            next_eid=None
            next_type=None

            if end+5 <= len(data):

                next_eid=u32(data,end)
                next_type=data[end+4]

                next_ok=(
                    next_eid is not None
                    and 0 <= next_eid < PERSON_COUNT
                    and next_type in (1,2,3,4)
                )

            candidates.append({
                "anchor":q,
                "uid":uid1,
                "end":end,
                "length":end-start,
                "next_ok":next_ok,
                "next_eid":next_eid,
                "next_type":next_type
            })

        search=q+1

    if not candidates:
        return None

    # Prefer terminal whose end directly begins next valid record.
    good=[
        x for x in candidates
        if x["next_ok"]
    ]

    if good:
        best=min(
            good,
            key=lambda x:x["anchor"]
        )
    else:
        # diagnostic fallback
        best=min(
            candidates,
            key=lambda x:x["anchor"]
        )

    return {
        "start":start,
        "eid":eid,
        "type":typ,
        **best
    }

# ============================================================
# First validate decoder against all known type02 starts.
# ============================================================

print("\nValidating generic decoder against 3237 known TYPE02...")

decoder_bad=[]
decoder_good=0

for i,s in enumerate(known_starts,1):

    rec=decode_record(s)

    if rec is None:
        decoder_bad.append(
            [s,"decode_none"]
        )
        continue

    k=known[s]

    if (
        rec["eid"] != k["eid"]
        or rec["anchor"] != k["anchor"]
        or rec["uid"] != k["uid"]
    ):
        decoder_bad.append([
            s,
            rec["eid"],
            rec["anchor"],
            k["anchor"],
            rec["uid"],
            k["uid"]
        ])
    else:
        decoder_good+=1

    if i % 500 == 0:
        print(
            i,"/",len(known_starts),
            "good=",decoder_good,
            "bad=",len(decoder_bad)
        )

print("\nGENERIC DECODER")
print("good:",decoder_good)
print("bad:",len(decoder_bad))

if decoder_bad:
    print("first bad:")
    for x in decoder_bad[:20]:
        print(x)

if decoder_good < 3200:
    raise SystemExit(
        "\nSTOP: generic decoder is not reliable enough."
    )

# ============================================================
# Chain reconstruction
#
# Begin from each verified TYPE02 seed.
# If an earlier chain already reached the seed, skip it.
#
# This naturally reveals separate record-stream segments.
# ============================================================

records={}
segments=[]

MAX_CHAIN_RECORDS=500000

print("\nReconstructing record chains...")

for seed_index,seed in enumerate(known_starts,1):

    if seed in records:
        continue

    segment_id=len(segments)+1

    segment={
        "segment_id":segment_id,
        "seed":seed,
        "starts":[],
        "stop_reason":None,
        "stop_pos":None
    }

    pos=seed
    local_seen=set()

    while True:

        if pos in local_seen:
            segment["stop_reason"]="loop"
            segment["stop_pos"]=pos
            break

        if pos in records:
            segment["stop_reason"]="merged_existing_chain"
            segment["stop_pos"]=pos
            break

        local_seen.add(pos)

        rec=decode_record(pos)

        if rec is None:
            segment["stop_reason"]="decode_failed"
            segment["stop_pos"]=pos
            break

        rec["segment_id"]=segment_id
        rec["is_true_new"]=(
            NEW_FIRST <= rec["eid"] < NEW_END
        )

        rec["known_type02"]=(
            pos in known
        )

        if pos in known:
            rec["known_anchor_match"]=(
                rec["anchor"]
                == known[pos]["anchor"]
            )
        else:
            rec["known_anchor_match"]=None

        records[pos]=rec
        segment["starts"].append(pos)

        # Continue exactly at terminal + 12
        next_pos=rec["end"]

        if not rec["next_ok"]:
            segment["stop_reason"]="no_valid_next"
            segment["stop_pos"]=next_pos
            break

        pos=next_pos

        if len(segment["starts"]) >= MAX_CHAIN_RECORDS:
            segment["stop_reason"]="max_chain"
            segment["stop_pos"]=pos
            break

    segments.append(segment)

    print(
        "segment",
        segment_id,
        "seed=",
        seed,
        "records=",
        len(segment["starts"]),
        "stop=",
        segment["stop_reason"],
        segment["stop_pos"]
    )

# ============================================================
# Coverage
# ============================================================

parsed_starts=set(records)

known_covered=sum(
    1 for s in known_starts
    if s in parsed_starts
)

known_missed=[
    s for s in known_starts
    if s not in parsed_starts
]

print("\nCHAIN COVERAGE")
print("parsed records:",len(records))
print("segments:",len(segments))
print(
    "known TYPE02 covered:",
    known_covered,
    "/",
    len(known_starts)
)

# ============================================================
# Ordered physical chain
# ============================================================

ordered=sorted(
    records.values(),
    key=lambda r:r["start"]
)

types=Counter(
    r["type"]
    for r in ordered
)

lengths_by_type={
    t:Counter()
    for t in (1,2,3,4)
}

for r in ordered:
    lengths_by_type[r["type"]][
        r["length"]
    ]+=1

new_count=sum(
    r["is_true_new"]
    for r in ordered
)

old_count=len(ordered)-new_count

# ============================================================
# TRUE-NEW runs
#
# Only records physically contiguous inside same reconstructed
# segment are joined into a run.
# ============================================================

runs=[]

for segment in segments:

    ss=segment["starts"]

    if not ss:
        continue

    recs=[
        records[s]
        for s in ss
    ]

    i=0

    while i < len(recs):

        if not recs[i]["is_true_new"]:
            i+=1
            continue

        j=i

        while (
            j+1 < len(recs)
            and recs[j+1]["is_true_new"]
            and recs[j]["end"]
                == recs[j+1]["start"]
        ):
            j+=1

        run_records=recs[i:j+1]

        prev_rec=(
            recs[i-1]
            if i>0 else None
        )

        next_rec=(
            recs[j+1]
            if j+1<len(recs)
            else None
        )

        type_counter=Counter(
            x["type"]
            for x in run_records
        )

        eid_values=[
            x["eid"]
            for x in run_records
        ]

        runs.append({
            "segment_id":
                segment["segment_id"],

            "start":
                run_records[0]["start"],

            "end":
                run_records[-1]["end"],

            "byte_size":
                run_records[-1]["end"]
                - run_records[0]["start"],

            "record_count":
                len(run_records),

            "unique_eids":
                len(set(eid_values)),

            "min_eid":
                min(eid_values),

            "max_eid":
                max(eid_values),

            "type1":
                type_counter[1],

            "type2":
                type_counter[2],

            "type3":
                type_counter[3],

            "type4":
                type_counter[4],

            "prev_old_eid":
                prev_rec["eid"]
                if prev_rec else None,

            "prev_old_type":
                prev_rec["type"]
                if prev_rec else None,

            "prev_old_uid":
                prev_rec["uid"]
                if prev_rec else None,

            "next_old_eid":
                next_rec["eid"]
                if next_rec else None,

            "next_old_type":
                next_rec["type"]
                if next_rec else None,

            "next_old_uid":
                next_rec["uid"]
                if next_rec else None,
        })

        i=j+1

# ============================================================
# Adjacency properties for TRUE NEW records
# ============================================================

new_to_new=0
new_to_old=0
old_to_new=0

eid_diff=Counter()

for segment in segments:

    ss=segment["starts"]

    for i in range(len(ss)-1):

        a=records[ss[i]]
        b=records[ss[i+1]]

        if a["end"] != b["start"]:
            continue

        if a["is_true_new"] and b["is_true_new"]:

            new_to_new+=1
            eid_diff[
                b["eid"]-a["eid"]
            ]+=1

        elif (
            a["is_true_new"]
            and not b["is_true_new"]
        ):
            new_to_old+=1

        elif (
            not a["is_true_new"]
            and b["is_true_new"]
        ):
            old_to_new+=1

# ============================================================
# CSV full chain
# ============================================================

chain_fields=[
    "segment_id",
    "start",
    "end",
    "length",
    "eid",
    "uid",
    "type",
    "anchor",
    "next_eid",
    "next_type",
    "next_ok",
    "is_true_new",
    "known_type02"
]

with OUTCSV.open(
    "w",
    newline="",
    encoding="utf-8"
) as f:

    w=csv.DictWriter(
        f,
        fieldnames=chain_fields
    )

    w.writeheader()

    for r in ordered:
        w.writerow({
            k:r.get(k)
            for k in chain_fields
        })

# ============================================================
# CSV new runs
# ============================================================

run_fields=[
    "segment_id",
    "start",
    "end",
    "byte_size",
    "record_count",
    "unique_eids",
    "min_eid",
    "max_eid",
    "type1",
    "type2",
    "type3",
    "type4",
    "prev_old_eid",
    "prev_old_type",
    "prev_old_uid",
    "next_old_eid",
    "next_old_type",
    "next_old_uid"
]

with RUNCSV.open(
    "w",
    newline="",
    encoding="utf-8"
) as f:

    w=csv.DictWriter(
        f,
        fieldnames=run_fields
    )

    w.writeheader()
    w.writerows(runs)

# ============================================================
# JSON
# ============================================================

run_sizes=Counter(
    r["record_count"]
    for r in runs
)

run_unique_eids=Counter(
    r["unique_eids"]
    for r in runs
)

summary={

    "generic_decoder":{
        "good":decoder_good,
        "bad":len(decoder_bad),
        "bad_examples":decoder_bad[:100]
    },

    "chain":{
        "record_count":len(records),
        "segments":len(segments),
        "known_type02_covered":
            known_covered,
        "known_type02_missed":
            len(known_missed),
        "known_type02_missed_examples":
            known_missed[:100],

        "type_counts":
            types.most_common(),

        "true_new_records":
            new_count,

        "old_records":
            old_count
    },

    "lengths_by_type":{
        str(t):
            lengths_by_type[t].most_common(100)
        for t in (1,2,3,4)
    },

    "true_new_runs":{
        "count":len(runs),

        "record_count_distribution":
            run_sizes.most_common(100),

        "unique_eid_distribution":
            run_unique_eids.most_common(100),

        "new_to_new_edges":
            new_to_new,

        "new_to_old_edges":
            new_to_old,

        "old_to_new_edges":
            old_to_new,

        "eid_diff_top100":
            eid_diff.most_common(100)
    },

    "segments":[
        {
            "segment_id":
                s["segment_id"],
            "seed":
                s["seed"],
            "record_count":
                len(s["starts"]),
            "stop_reason":
                s["stop_reason"],
            "stop_pos":
                s["stop_pos"]
        }
        for s in segments
    ]
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
print("PHASE 5 RESULT")
print("="*72)

print("\nGENERIC DECODER")
print(
    "good:",
    decoder_good
)
print(
    "bad:",
    len(decoder_bad)
)

print("\nCHAIN")
print(
    "records:",
    len(records)
)
print(
    "segments:",
    len(segments)
)
print(
    "known TYPE02 coverage:",
    known_covered,
    "/",
    len(known_starts)
)

print("\nCOMPONENT TYPES")
for t,c in types.most_common():
    print(t,c)

print("\nTRUE NEW / OLD")
print(
    "TRUE NEW records:",
    new_count
)
print(
    "old records:",
    old_count
)

print("\nTYPE 1 LENGTHS")
for L,c in lengths_by_type[1].most_common(20):
    print(L,c)

print("\nTYPE 2 LENGTHS")
for L,c in lengths_by_type[2].most_common(30):
    print(L,c)

print("\nTYPE 3 LENGTHS")
for L,c in lengths_by_type[3].most_common(20):
    print(L,c)

print("\nTYPE 4 LENGTHS")
for L,c in lengths_by_type[4].most_common(20):
    print(L,c)

print("\nTRUE-NEW RUNS")
print(
    "runs:",
    len(runs)
)

print("\nRUN SIZE DISTRIBUTION")
for k,v in run_sizes.most_common(30):
    print(k,v)

print("\nRUN UNIQUE-EID DISTRIBUTION")
for k,v in run_unique_eids.most_common(30):
    print(k,v)

print("\nADJACENCY")
print(
    "new -> new:",
    new_to_new
)
print(
    "new -> old:",
    new_to_old
)
print(
    "old -> new:",
    old_to_new
)

print("\nTRUE-NEW ADJACENT EID DIFF")
for d,c in eid_diff.most_common(40):
    print(d,c)

print("\nFIRST 20 TRUE-NEW RUNS")
for r in runs[:20]:
    print(
        "records=",
        r["record_count"],
        "unique_eids=",
        r["unique_eids"],
        "types=",
        (
            r["type1"],
            r["type2"],
            r["type3"],
            r["type4"]
        ),
        "prev=",
        (
            r["prev_old_eid"],
            r["prev_old_type"]
        ),
        "next=",
        (
            r["next_old_eid"],
            r["next_old_type"]
        )
    )

print("\nOUTPUT")
print(OUTCSV)
print(RUNCSV)
print(OUTJSON)

print("\nDONE")
