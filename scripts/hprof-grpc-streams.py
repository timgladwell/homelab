#!/usr/bin/env python3
# Count the gRPC streams a UniFi Network app heap dump holds, split by method.
# The MAT-free reading step of docs/runbooks/unifi-network-heap-dump.md:
#
#   python3 -I scripts/hprof-grpc-streams.py ~/heapdumps/svm-heapdump-*.hprof
#
# The dump holds admin sessions, API tokens and Wi-Fi passphrases. This prints
# only class names, counts and gRPC method names, never other field contents,
# so its output is safe to paste into an issue or a support ticket.
#
# Stdlib only; ~20 s for a 340 MB dump. It reads the HPROF 1.0.2 records the
# GraalVM native image writes and exits on any heap sub-record it doesn't know,
# rather than miscounting.
import collections
import mmap
import struct
import sys

f = open(sys.argv[1], 'rb')
m = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
i = m.find(b'\0') + 1
ids = struct.unpack_from('>I', m, i)[0]
start = i + 12
fmt = {4: '>I', 8: '>Q'}[ids]
rid = lambda o: struct.unpack_from(fmt, m, o)[0]
TS = {2: ids, 4: 1, 5: 2, 6: 4, 7: 8, 8: 1, 9: 2, 10: 4, 11: 8}  # basic type sizes
strings, cls_name, cdump = {}, {}, {}


def walk(first, on_inst=None, on_arr=None):
    """One pass over the file. The first pass records strings, class names and
    field layouts; later passes hand instances and primitive arrays to callbacks."""
    o, n = start, len(m)
    while o < n:
        tag = m[o]
        ln = struct.unpack_from('>I', m, o + 5)[0]
        b = o + 9
        e = b + ln
        if first and tag == 0x01:
            strings[rid(b)] = bytes(m[b + ids:e])
        elif first and tag == 0x02:
            cls_name[rid(b + 4)] = rid(b + 8 + ids)
        elif tag in (0x0c, 0x1c):
            p = b
            while p < e:
                t = m[p]
                p += 1
                if t in (0xff, 0x05, 0x07):
                    p += ids
                elif t == 0x01:
                    p += 2 * ids
                elif t in (0x02, 0x03, 0x08):
                    p += ids + 8
                elif t in (0x04, 0x06):
                    p += ids + 4
                elif t == 0x20:  # class dump
                    cid = rid(p)
                    sup = rid(p + ids + 4)
                    q = p + ids + 4 + 6 * ids + 4
                    cp = struct.unpack_from('>H', m, q)[0]
                    q += 2
                    for _ in range(cp):
                        q += 3 + TS[m[q + 2]]
                    sc = struct.unpack_from('>H', m, q)[0]
                    q += 2
                    for _ in range(sc):
                        q += ids + 1 + TS[m[q + ids]]
                    fc = struct.unpack_from('>H', m, q)[0]
                    q += 2
                    fl = []
                    for _ in range(fc):
                        fl.append((rid(q), m[q + ids]))
                        q += ids + 1
                    if first:
                        cdump[cid] = (sup, fl)
                    p = q
                elif t == 0x21:  # instance dump
                    nb = struct.unpack_from('>I', m, p + 2 * ids + 4)[0]
                    d = p + 2 * ids + 8
                    if on_inst:
                        on_inst(rid(p), rid(p + ids + 4), d)
                    p = d + nb
                elif t == 0x22:  # object array
                    p += ids + 8 + ids + struct.unpack_from('>I', m, p + ids + 4)[0] * ids
                elif t == 0x23:  # primitive array
                    nel = struct.unpack_from('>I', m, p + ids + 4)[0]
                    ty = m[p + ids + 8]
                    if on_arr:
                        on_arr(rid(p), ty, p + ids + 9, nel)
                    p += ids + 9 + nel * TS[ty]
                else:
                    sys.exit(f'unknown heap sub-record {t:#x} at offset {p - 1}')
        o = e


def field(c, d, name):
    """An object field's value (an object ID) from an instance's data. HPROF lays
    out the class's own fields first, then each superclass's."""
    off = 0
    while c:
        sup, fl = cdump[c]
        for nid, ty in fl:
            if strings.get(nid) == name:
                return rid(d + off) if ty == 2 else None
            off += TS[ty]
        c = sup
    raise KeyError(name)


walk(True)
name = {c: strings.get(s, b'?').decode(errors='replace') for c, s in cls_name.items()}
is_cls = lambda c, cls: name.get(c, '').replace('/', '.') == cls
counts, by_method, descs = collections.Counter(), collections.Counter(), {}


def count(o, c, d):
    if 'okhttp/OkHttpClient' in name.get(c, ''):
        counts[name[c]] += 1
    if is_cls(c, 'io.grpc.okhttp.OkHttpClientStream'):
        by_method[field(c, d, b'method')] += 1
    if is_cls(c, 'io.grpc.MethodDescriptor'):
        descs[o] = (c, d)


walk(False, count)
for n, k in counts.most_common():
    print(f'{k:7d}  {n}')

# Method names: MethodDescriptor.fullMethodName -> String.value -> byte[] or char[].
sid = {dsc: field(*descs[dsc], b'fullMethodName') for dsc in by_method if dsc in descs}
need = set(sid.values())
svals = {}
walk(False, lambda o, c, d: svals.__setitem__(o, field(c, d, b'value')) if o in need else None)
need = set(svals.values())
arrs = {}
walk(False, None, lambda o, ty, d, n: arrs.__setitem__(
    o, bytes(m[d:d + n * TS[ty]]).decode('latin-1' if ty == 8 else 'utf-16-be')) if o in need else None)
print('-- OkHttpClientStream by method')
for dsc, k in by_method.most_common():
    print(f'{k:7d}  {arrs.get(svals.get(sid.get(dsc)), hex(dsc))}')
