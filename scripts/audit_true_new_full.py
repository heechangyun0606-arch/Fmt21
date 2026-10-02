import sys
import struct
import json
import csv
import io
import zipfile
import subprocess
from pathlib import Path
from collections import Counter
from statistics import median

# ============================================================
# FMT21 OFFICIAL TRUE-NEW FULL AUDIT
#
# INPUT:
#   /storage/emulated/0/Download/db.zip
#
# Automatically:
#   1. find 2110/2130 people_db.dat
#   2. decode Zstd frames
#   3. verify counts 220791 -> 227007
#   4. audit genuine TRUE NEW EIDs 220791..227006
#   5. scan [EID][UID][UID]
#   6. locate nearest self-EID + component byte 0x02
#   7. analyse geometry/order/flags
#
# OUTPUT:
#   /storage/emulated/0/Download/fmt21_probe/
#       2110_people_db.dat.raw
#       2130_people_db.dat.raw
#       official_true_new_full_geometry.csv
#       official_true_new_full_summary.json
# ============================================================

DBZIP = Path("/storage/emulated/0/Download/db.zip")
OUTDIR = Path("/storage/emulated/0/Download/fmt21_probe")
OUTDIR.mkdir(parents=True, exist_ok=True)

if not DBZIP.exists():
    raise SystemExit(
        "\nERROR: db.zip not found:\n"
        f"{DBZIP}\n"
    )

print("=" * 70)
print("FMT21 OFFICIAL TRUE-NEW FULL AUDIT")
print("=" * 70)
print("db.zip:", DBZIP)
print("size:", f"{DBZIP.stat().st_size:,}", "bytes")

# ------------------------------------------------------------
# zstandard
# ------------------------------------------------------------

try:
    import zstandard as zstd
except ImportError:
    print("\nzstandard not installed.")
    print("Installing zstandard...")
    subprocess.check_call([
        sys.executable, "-m", "pip",
        "install", "zstandard"
    ])
    import zstandard as zstd


def u32(data, off):
    if off < 0 or off + 4 > len(data):
        return None
    return struct.unpack_from("<I", data, off)[0]


# ------------------------------------------------------------
# ZIP entry search
# ------------------------------------------------------------

def find_entry(zf, version, filename):

    version = str(version)
    hits = []

    for name in zf.namelist():

        normalized = name.replace("\\", "/")
        parts = normalized.split("/")

        if (
            version in parts
            and parts[-1].lower() == filename.lower()
        ):
            hits.append(name)

    if not hits:
        # fallback
        for name in zf.namelist():

            n = name.lower().replace("\\", "/")

            if (
                f"/{version}/" in n
                and n.endswith("/" + filename.lower())
            ):
                hits.append(name)

    if not hits:
        raise RuntimeError(
            f"Could not find {version}/{filename}"
        )

    # Prefer paths containing *_fmc
    hits.sort(
        key=lambda x: (
            "_fmc" not in x.lower(),
            len(x)
        )
    )

    if len(hits) > 1:
        print(f"\nMultiple candidates for {version}/{filename}:")
        for h in hits:
            print(" ", h)

    return hits[0]


# ------------------------------------------------------------
# ZSTD frame extraction
#
# We previously confirmed FMT people_db contains many independent
# Zstd frames. Scan frame magic and decompress each frame.
# ------------------------------------------------------------

MAGIC = b"\x28\xb5\x2f\xfd"


def decode_zstd_frames(packed, label):

    starts = []

    pos = 0

    while True:

        pos = packed.find(MAGIC, pos)

        if pos < 0:
            break

        starts.append(pos)
        pos += 4

    print(f"\n{label}")
    print("  packed:", f"{len(packed):,}")
    print("  Zstd frame candidates:", len(starts))

    if not starts:
        raise RuntimeError(
            f"No Zstd frames found in {label}"
        )

    dctx = zstd.ZstdDecompressor()

    chunks = []

    good = 0
    bad = 0

    for i, start in enumerate(starts):

        # Bound input at next magic candidate.
        # If this is a genuine independent frame this avoids
        # accidentally reading following frames.
        if i + 1 < len(starts):
            end = starts[i + 1]
        else:
            end = len(packed)

        frame = packed[start:end]

        try:
            raw = dctx.decompress(
                frame,
                max_output_size=256 * 1024 * 1024
            )

            if raw:
                chunks.append(raw)
                good += 1

        except Exception:
            # fallback stream reader
            try:
                bio = io.BytesIO(packed[start:])

                with dctx.stream_reader(
                    bio,
                    read_across_frames=False
                ) as reader:
                    raw = reader.read()

                if raw:
                    chunks.append(raw)
                    good += 1
                else:
                    bad += 1

            except Exception:
                bad += 1

        if (i + 1) % 100 == 0:
            print(
                f"  frames {i+1}/{len(starts)} "
                f"good={good} bad={bad}"
            )

    result = b"".join(chunks)

    print("  decoded frames:", good)
    print("  failed:", bad)
    print("  raw:", f"{len(result):,}")

    if not result:
        raise RuntimeError(
            f"Decoded output is empty: {label}"
        )

    return result


# ------------------------------------------------------------
# Load/decode people_db
# ------------------------------------------------------------

def get_people_raw(zf, version):

    raw_path = OUTDIR / f"{version}_people_db.dat.raw"

    if raw_path.exists():
        size = raw_path.stat().st_size

        if size > 100_000_000:
            print(
                f"\nReuse existing raw: {raw_path}"
                f" ({size:,})"
            )
            return raw_path.read_bytes()

    entry = find_entry(
        zf,
        version,
        "people_db.dat"
    )

    print("\nZIP ENTRY:")
    print(" ", entry)

    packed = zf.read(entry)

    raw = decode_zstd_frames(
        packed,
        f"{version} people_db"
    )

    raw_path.write_bytes(raw)

    print("  saved:", raw_path)

    return raw


# ------------------------------------------------------------
# Open ZIP
# ------------------------------------------------------------

if not zipfile.is_zipfile(DBZIP):
    raise SystemExit(
        "\nERROR: db.zip is not a valid ZIP.\n"
        f"size={DBZIP.stat().st_size:,}\n"
    )

with zipfile.ZipFile(DBZIP, "r") as zf:

    print("\nZIP OK")
    print("entries:", len(zf.namelist()))

    p2110 = get_people_raw(zf, 2110)
    p2130 = get_people_raw(zf, 2130)


# ------------------------------------------------------------
# Verify official raw
# ------------------------------------------------------------

print("\n" + "=" * 70)
print("RAW VERIFICATION")
print("=" * 70)

print("2110 raw:", f"{len(p2110):,}")
print("2130 raw:", f"{len(p2130):,}")

c2110 = u32(p2110, 0)
c2130 = u32(p2130, 0)

plus4_2110 = u32(p2110, 4)
plus4_2130 = u32(p2130, 4)

print("\n2110 +0:", c2110)
print("2110 +4:", plus4_2110)

print("2130 +0:", c2130)
print("2130 +4:", plus4_2130)

EXPECTED_2110 = 220791
EXPECTED_2130 = 227007

if (
    c2110 != EXPECTED_2110
    or c2130 != EXPECTED_2130
):
    raise SystemExit(
        "\nSTOP: decoded raw header does not match "
        "known official values.\n"
        f"Expected 2110={EXPECTED_2110}, got {c2110}\n"
        f"Expected 2130={EXPECTED_2130}, got {c2130}\n"
        "Do not continue with bad decode."
    )

print("\nOfficial header verification: PASS")

FIRST_NEW = c2110
END_NEW = c2130
TOTAL_NEW = END_NEW - FIRST_NEW

print("\nTRUE NEW EID:")
print(
    f"  {FIRST_NEW} .. {END_NEW - 1}"
)
print("  total:", TOTAL_NEW)


# ------------------------------------------------------------
# Anchor scan
#
# Pattern:
#
#   EID u32
#   UID u32
#   UID u32
#
# We intentionally do NOT know UID beforehand.
# ------------------------------------------------------------

print("\n" + "=" * 70)
print("STEP 1 - [EID][UID][UID] ANCHOR SCAN")
print("=" * 70)

anchors = []
missing = []
multi = []

for index, eid in enumerate(
    range(FIRST_NEW, END_NEW),
    1
):

    needle = struct.pack("<I", eid)

    pos = 0
    hits = []

    while True:

        pos = p2130.find(needle, pos)

        if pos < 0:
            break

        if pos + 12 <= len(p2130):

            uid1 = u32(p2130, pos + 4)
            uid2 = u32(p2130, pos + 8)

            if (
                uid1 == uid2
                and uid1 not in (
                    0,
                    0xFFFFFFFF
                )
            ):
                hits.append(
                    (pos, uid1)
                )

        pos += 1

    if not hits:

        missing.append(eid)

    else:

        if len(hits) > 1:
            multi.append({
                "eid": eid,
                "hits": len(hits)
            })

        for pos, uid in hits:

            anchors.append({
                "eid": eid,
                "uid": uid,
                "anchor": pos
            })

    if index % 500 == 0:

        print(
            f"{index}/{TOTAL_NEW} "
            f"anchor_rows={len(anchors)} "
            f"missing={len(missing)}"
        )


print("\nANCHOR RESULT")
print(" rows:", len(anchors))
print(" missing EIDs:", len(missing))
print(" multi:", len(multi))


# ------------------------------------------------------------
# Component start search
#
# Important correction from previous analysis:
#
#   record:
#       [EID 4 bytes]
#       [component type 1 byte]
#       [flags / fields ...]
#
# Therefore:
#
#   p2130[start+4] == 0x02
#
# NOT:
#
#   u32(start+4) == 2
#
# ------------------------------------------------------------

print("\n" + "=" * 70)
print("STEP 2 - TYPE-02 COMPONENT START SEARCH")
print("=" * 70)

BACK = 2048

selected = []
no_type02 = []

for index, a in enumerate(
    anchors,
    1
):

    eid = a["eid"]
    uid = a["uid"]
    anchor = a["anchor"]

    needle = struct.pack("<I", eid)

    lo = max(
        0,
        anchor - BACK
    )

    pos = lo

    candidates = []

    while True:

        q = p2130.find(
            needle,
            pos,
            anchor
        )

        if q < 0:
            break

        if q + 8 <= len(p2130):

            component_type = (
                p2130[q + 4]
            )

            flags3 = (
                p2130[
                    q + 5:
                    q + 8
                ].hex()
            )

            candidates.append({
                "start": q,
                "component_type":
                    component_type,
                "flags3":
                    flags3,
                "type_flags_u32":
                    u32(p2130, q + 4),
                "anchor_delta":
                    anchor - q
            })

        pos = q + 1

    type02 = [
        x for x in candidates
        if x["component_type"] == 2
    ]

    if type02:

        # closest type-02 self-EID
        best = min(
            type02,
            key=lambda x:
                x["anchor_delta"]
        )

        selected.append({
            "eid": eid,
            "uid": uid,
            "anchor": anchor,
            **best
        })

    else:

        no_type02.append({
            "eid": eid,
            "uid": uid,
            "anchor": anchor
        })

    if index % 500 == 0:

        print(
            f"{index}/{len(anchors)} "
            f"type02={len(selected)}"
        )


print("\nTYPE-02 RESULT")
print(" selected:", len(selected))
print(" no type02:", len(no_type02))


# ------------------------------------------------------------
# Sort by physical position
# ------------------------------------------------------------

selected.sort(
    key=lambda x: x["start"]
)

for i, r in enumerate(selected):

    r["position_pct"] = (
        r["start"]
        / len(p2130)
        * 100.0
    )

    if i + 1 < len(selected):

        r["next_type02_delta"] = (
            selected[i + 1]["start"]
            - r["start"]
        )

    else:

        r["next_type02_delta"] = None


# ------------------------------------------------------------
# Known geometry checks
#
# Earlier official observations:
#   553 bytes
#   556 bytes
#
# This does NOT assume those are the only lengths.
# ------------------------------------------------------------

def inspect_boundary(start, length):

    p = start + length

    if p + 5 > len(p2130):
        return False, None, None

    eid = u32(p2130, p)
    typ = p2130[p + 4]

    # broad sanity only
    plausible = (
        eid is not None
        and eid < 10_000_000
        and typ < 64
    )

    return (
        plausible,
        eid,
        typ
    )


for r in selected:

    for L in (553, 556):

        ok, eid, typ = (
            inspect_boundary(
                r["start"],
                L
            )
        )

        r[f"boundary_{L}"] = ok
        r[f"boundary_{L}_eid"] = eid
        r[f"boundary_{L}_type"] = typ


# ------------------------------------------------------------
# Statistics
# ------------------------------------------------------------

flags = Counter(
    r["flags3"]
    for r in selected
)

type_flags = Counter(
    r["type_flags_u32"]
    for r in selected
)

anchor_deltas = Counter(
    r["anchor_delta"]
    for r in selected
)

next_deltas = Counter(
    r["next_type02_delta"]
    for r in selected
    if r["next_type02_delta"]
    is not None
)

positions = [
    r["position_pct"]
    for r in selected
]

b553 = sum(
    bool(r["boundary_553"])
    for r in selected
)

b556 = sum(
    bool(r["boundary_556"])
    for r in selected
)


# ------------------------------------------------------------
# Spearman rank
# ------------------------------------------------------------

def ranks(values):

    order = sorted(
        range(len(values)),
        key=lambda i:
            values[i]
    )

    result = [0.0] * len(values)

    i = 0

    while i < len(order):

        j = i + 1

        while (
            j < len(order)
            and values[order[j]]
            == values[order[i]]
        ):
            j += 1

        rank = (
            (i + 1) + j
        ) / 2.0

        for k in range(i, j):
            result[order[k]] = rank

        i = j

    return result


def correlation(a, b):

    if len(a) < 2:
        return None

    ma = sum(a) / len(a)
    mb = sum(b) / len(b)

    num = sum(
        (x - ma) * (y - mb)
        for x, y in zip(a, b)
    )

    da = sum(
        (x - ma) ** 2
        for x in a
    )

    db = sum(
        (y - mb) ** 2
        for y in b
    )

    if da == 0 or db == 0:
        return None

    return num / (
        (da * db) ** 0.5
    )


if len(selected) >= 2:

    spearman = correlation(
        ranks([
            r["eid"]
            for r in selected
        ]),
        ranks([
            r["start"]
            for r in selected
        ])
    )

else:

    spearman = None


# ------------------------------------------------------------
# CSV
# ------------------------------------------------------------

CSVOUT = (
    OUTDIR /
    "official_true_new_full_geometry.csv"
)

FIELDS = [
    "eid",
    "uid",
    "start",
    "anchor",
    "anchor_delta",
    "component_type",
    "flags3",
    "type_flags_u32",
    "position_pct",
    "next_type02_delta",
    "boundary_553",
    "boundary_553_eid",
    "boundary_553_type",
    "boundary_556",
    "boundary_556_eid",
    "boundary_556_type"
]

with CSVOUT.open(
    "w",
    newline="",
    encoding="utf-8"
) as f:

    writer = csv.DictWriter(
        f,
        fieldnames=FIELDS
    )

    writer.writeheader()

    for r in selected:

        writer.writerow({
            key: r.get(key)
            for key in FIELDS
        })


# ------------------------------------------------------------
# JSON summary
# ------------------------------------------------------------

JSONOUT = (
    OUTDIR /
    "official_true_new_full_summary.json"
)

summary = {

    "source": str(DBZIP),

    "raw": {
        "2110_people_size":
            len(p2110),
        "2130_people_size":
            len(p2130)
    },

    "headers": {
        "2110_plus0":
            c2110,
        "2110_plus4":
            plus4_2110,
        "2130_plus0":
            c2130,
        "2130_plus4":
            plus4_2130
    },

    "true_new": {
        "first_eid":
            FIRST_NEW,
        "last_eid":
            END_NEW - 1,
        "count":
            TOTAL_NEW
    },

    "anchors": {
        "rows":
            len(anchors),
        "missing_count":
            len(missing),
        "missing_first100":
            missing[:100],
        "multi_count":
            len(multi),
        "multi_first100":
            multi[:100]
    },

    "type02": {
        "count":
            len(selected),

        "no_type02_count":
            len(no_type02),

        "position_min_pct":
            min(positions)
            if positions else None,

        "position_median_pct":
            median(positions)
            if positions else None,

        "position_max_pct":
            max(positions)
            if positions else None,

        "spearman_eid_position":
            spearman,

        "boundary_553_count":
            b553,

        "boundary_556_count":
            b556,

        "top_anchor_deltas":
            anchor_deltas.most_common(50),

        "top_next_type02_deltas":
            next_deltas.most_common(50),

        "top_flags3":
            flags.most_common(50),

        "top_type_flags_u32":
            type_flags.most_common(50)
    }
}

JSONOUT.write_text(
    json.dumps(
        summary,
        indent=2,
        ensure_ascii=False
    ),
    encoding="utf-8"
)


# ------------------------------------------------------------
# Final report
# ------------------------------------------------------------

print("\n")
print("=" * 70)
print("OFFICIAL TRUE-NEW FULL AUDIT RESULT")
print("=" * 70)

print("\nRAW")
print(
    "2110:",
    f"{len(p2110):,}"
)
print(
    "2130:",
    f"{len(p2130):,}"
)

print("\nHEADER")
print(
    "2110:",
    c2110,
    plus4_2110
)
print(
    "2130:",
    c2130,
    plus4_2130
)

print("\nTRUE NEW")
print(
    FIRST_NEW,
    "..",
    END_NEW - 1
)
print(
    "count:",
    TOTAL_NEW
)

print("\nANCHORS")
print(
    "rows:",
    len(anchors)
)
print(
    "missing:",
    len(missing)
)
print(
    "multi:",
    len(multi)
)

print("\nTYPE-02")
print(
    "count:",
    len(selected)
)
print(
    "no type02:",
    len(no_type02)
)

if positions:

    print(
        "position %:",
        min(positions),
        median(positions),
        max(positions)
    )

print(
    "EID-position Spearman:",
    spearman
)

print("\nBOUNDARY")
print(
    "553:",
    b553
)
print(
    "556:",
    b556
)

print("\nTOP ANCHOR DELTAS")
for k, v in anchor_deltas.most_common(20):
    print(k, v)

print("\nTOP FLAGS3")
for k, v in flags.most_common(20):
    print(k, v)

print("\nTOP NEXT TYPE02 DELTAS")
for k, v in next_deltas.most_common(20):
    print(k, v)

print("\nFIRST 10 TYPE02")

for r in selected[:10]:

    print(
        r["eid"],
        r["uid"],
        "start=",
        r["start"],
        "delta=",
        r["anchor_delta"],
        "flags=",
        r["flags3"],
        "pos=",
        round(
            r["position_pct"],
            6
        )
    )

print("\nLAST 10 TYPE02")

for r in selected[-10:]:

    print(
        r["eid"],
        r["uid"],
        "start=",
        r["start"],
        "delta=",
        r["anchor_delta"],
        "flags=",
        r["flags3"],
        "pos=",
        round(
            r["position_pct"],
            6
        )
    )

print("\nOUTPUT")
print(CSVOUT)
print(JSONOUT)

print("\nDONE")
