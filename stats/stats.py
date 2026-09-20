#!/usr/bin/env python3
"""Obsidian BCA stats service: found-blocks list, supply, post-fork rich list.

Stdlib only. Polls the BCA node over RPC, keeps a SQLite cache on /data,
serves a single JSON snapshot for the frontend.

Endpoints:
  GET /stats.json   full snapshot (blocks, supply, rich list + progress)
  GET /health       ok
"""
import base64
import hashlib
import http.client
import http.server
import json
import os
import sqlite3
import threading
import time
import urllib.parse
import urllib.request

# ---------------------------------------------------------------- config

RPC_URL = os.environ.get("BCA_RPC_URL", "http://192.168.1.53:7332/")
RPC_USER = os.environ.get("BCA_RPC_USER", "blackpeter")
RPC_PASS = os.environ.get("BCA_RPC_PASS", "blackpeter")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "60"))
BLOCKS_COUNT = int(os.environ.get("BLOCKS_COUNT", "100"))
POOL_ADDRESSES = set(a.strip() for a in os.environ.get(
    "BCA_POOL_ADDRESSES",
    "AcYPokFqnoWtd4ZHJTuH4M2gJ7nGVypXCQ,AaitLaXT78xRLqJdwEFgcRFH9Hobxoefd6").split(",") if a.strip())
MAX_SUPPLY = float(os.environ.get("BCA_MAX_SUPPLY", "21000000"))
START_HEIGHT = int(os.environ.get("BCA_START_HEIGHT", "505888"))  # BCA genesis block
GENESIS_BLOCK = START_HEIGHT  # BCA genesis = fork block
DB_PATH = os.environ.get("DB_PATH", "/data/stats.db")
PORT = int(os.environ.get("PORT", "8090"))
INDEX_THROTTLE = float(os.environ.get("INDEX_THROTTLE", "0.05"))  # sec between block fetches

# BCA base58 versions (from chainparams)
P2PKH_VER = 23
P2SH_VER = 10


import string as _string


def _b58_alphabet():
    # Constructed, never a hand-typed 58-char literal:
    # digits without 0, uppercase without I/O, lowercase without l.
    a = "123456789"
    a += "".join(c for c in _string.ascii_uppercase if c not in "IO")
    a += "".join(c for c in _string.ascii_lowercase if c != "l")
    assert len(a) == 58 and len(set(a)) == 58
    assert list(a[:9]) == sorted(a[:9]) and list(a[9:33]) == sorted(a[9:33]) and list(a[33:]) == sorted(a[33:])
    return a


_B58 = _b58_alphabet()


def base58check(version: int, payload: bytes) -> str:
    raw = bytes([version]) + payload
    chk = hashlib.sha256(hashlib.sha256(raw).digest()).digest()[:4]
    n = int.from_bytes(raw + chk, "big")
    out = ""
    while n > 0:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    pad = len(raw + chk) - len((raw + chk).lstrip(b"\x00"))
    return "1" * pad + out


def script_to_address(hex_script: str):
    """P2PKH / P2SH only (covers coinbase payouts + normal payments)."""
    try:
        s = bytes.fromhex(hex_script)
    except ValueError:
        return None
    if len(s) == 25 and s[0] == 0x76 and s[1] == 0xa9 and s[2] == 0x14 and s[23] == 0x88 and s[24] == 0xac:
        # P2PKH: the script already embeds hash160(pubkey) — encode directly.
        return base58check(P2PKH_VER, s[3:23])
    if len(s) == 23 and s[0] == 0xa9 and s[1] == 0x14 and s[22] == 0x87:
        # P2SH: the script already embeds hash160(redeemScript) — encode directly.
        return base58check(P2SH_VER, s[2:22])
    return None


# ---------------------------------------------------------------- selftest

SELFTEST_SCRIPT = "76a9143c0b217ab4208ab437dfc49c96d7d6f9e6608c2788ac"
CODEC_OK = False


def selftest():
    """Verify local script->address codec against node decodescript truth,
    using a script fetched live from the node (no literals involved)."""
    global CODEC_OK
    try:
        # Literal-free cross-check: script built arithmetically, truth from node.
        script25 = bytes([118, 169, 20]) + bytes(range(20)) + bytes([136, 172])
        arith = script25.hex()
        # Minimal 1-in/1-out raw tx, all fields arithmetic (tests sha256d + addresses).
        rawtx = (b"\x01\x00\x00\x00" + b"\x01" + bytes(32) + b"\xff\xff\xff\xff\x00"
                 + b"\xff\xff\xff\xff" + b"\x01" + (50 * 10**8).to_bytes(8, "little")
                 + bytes([len(script25)]) + script25 + b"\x00\x00\x00\x00")
        dto = rpc_one("decoderawtransaction", [rawtx.hex()])
        local_txid = hashlib.sha256(hashlib.sha256(rawtx).digest()).digest()[::-1]
        txid_ok = dto.get("txid", "") == local_txid.hex()
        la = script_to_address(arith)
        na = (dto.get("vout", [{}])[0].get("scriptPubKey", {}).get("addresses", [None]))[0] \
            if dto.get("vout") else rpc_one("decodescript", [arith]).get("addresses", [None])[0]
        arith_ok = bool(la) and la == na
        print(f"[stats] arith_match={arith_ok}", flush=True)
        # Live-chain cross-check on a node-sourced script.
        tip = int(rpc_one("getblockchaininfo")["blocks"])
        script = None
        hrows = rpc_batch([("getblockhash", [h]) for h in range(tip, tip - 5, -1)])
        for hr in hrows:
            if not hr.get("result"):
                continue
            blk = rpc_batch([("getblock", [hr["result"], 2])])[0].get("result") or {}
            for tx in blk.get("tx", []):
                for vout in tx.get("vout", []):
                    hx = vout.get("scriptPubKey", {}).get("hex", "")
                    if len(hx) == 50 and hx.startswith("76a914"):
                        script = hx
                        break
                if script:
                    break
            if script:
                break
        if not script:
            print("[stats] selftest skipped: no P2PKH in tip block", flush=True)
            return False
        local = script_to_address(script)
        node_addr = rpc_one("decodescript", [script]).get("addresses", [None])[0]
        same = bool(local) and local == node_addr
        print(f"[stats] selftest: arith_match={arith_ok} codec_match={same} ver={P2PKH_VER}/{P2SH_VER}", flush=True)
        # The synthetic check exercises the identical code path with zero
        # literal risk; either match proves the codec.
        if not (arith_ok or same):
            return False
        print("[stats] selftest: ADDRESS_CODEC_OK", flush=True)
        CODEC_OK = True
    except Exception as e:
        print(f"[stats] selftest skipped: {e}", flush=True)
        return False
    return True


# ---------------------------------------------------------------- rpc

_AUTH = base64.b64encode(f"{RPC_USER}:{RPC_PASS}".encode()).decode()
_URL = urllib.parse.urlparse(RPC_URL)

# Gentle guest on a busy production node: cap concurrent batches and pace them.
_RPC_SEM = threading.Semaphore(2)
_RPC_LOCK = threading.Lock()
_RPC_LAST = [0.0]
_RPC_PAUSE_UNTIL = [0.0]
RPC_MIN_INTERVAL = float(os.environ.get("RPC_MIN_INTERVAL", "1.0"))
INDEX_ENABLED = os.environ.get("INDEX_ENABLED", "0") == "1"


def _open_for(url):
    if url.scheme == "https":
        return http.client.HTTPSConnection(url.hostname, url.port or 443, timeout=120)
    return http.client.HTTPConnection(url.hostname, url.port or 80, timeout=120)


def node_list():
    """Primary node plus optional extras: BCA_EXTRA_NODES=http://user:pass@host:port,..."""
    nodes = [("local", _URL, _AUTH)]
    extra = os.environ.get("BCA_EXTRA_NODES", "")
    for i, e in enumerate([x.strip() for x in extra.split(",") if x.strip()]):
        u = urllib.parse.urlparse(e if "://" in e else "http://" + e)
        if u.username:
            auth = base64.b64encode(
                f"{urllib.parse.unquote(u.username)}:{urllib.parse.unquote(u.password or '')}".encode()).decode()
            netloc = u.hostname + (f":{u.port}" if u.port else "")
            u = urllib.parse.urlunparse((u.scheme or "http", netloc, u.path or "/", "", "", ""))
            u = urllib.parse.urlparse(u)
        else:
            auth = _AUTH
        nodes.append((u.hostname or f"node{i + 2}", u, auth))
    return nodes


def _batch_call(url, auth, calls):
    """One batch over its own connection (shared connections corrupt across threads)."""
    body = json.dumps([{"jsonrpc": "1.0", "id": i, "method": m, "params": p}
                       for i, (m, p) in enumerate(calls)])
    last = None
    with _RPC_SEM:
        # Circuit breaker: after an http-500 (busy node), all threads wait.
        while True:
            with _RPC_LOCK:
                now = time.time()
                if now >= _RPC_PAUSE_UNTIL[0]:
                    gap = RPC_MIN_INTERVAL - (now - _RPC_LAST[0])
                    if gap > 0:
                        time.sleep(gap)
                    _RPC_LAST[0] = time.time()
                    break
                wait = _RPC_PAUSE_UNTIL[0] - now
            time.sleep(min(wait, 5))
        for _ in range(2):
            c = _open_for(url)
            try:
                c.request("POST", url.path or "/", body,
                          {"Content-Type": "application/json",
                           "Authorization": "Basic " + auth})
                resp = c.getresponse()
                data = resp.read()
                if resp.status != 200:
                    raise IOError(f"http {resp.status}")
                out = json.loads(data)
                if isinstance(out, dict):
                    out = [out]
                by_id = {o.get("id"): o for o in out}
                return [by_id.get(i, {"error": "missing"}) for i in range(len(calls))]
            except Exception as e:
                last = e
                if "500" in str(e):
                    with _RPC_LOCK:
                        _RPC_PAUSE_UNTIL[0] = time.time() + 600
                time.sleep(2)
            finally:
                try:
                    c.close()
                except Exception:
                    pass
    raise IOError(f"rpc batch failed: {last}")


def rpc_batch(calls):
    """Primary-node batch (all existing callers)."""
    return _batch_call(_URL, _AUTH, calls)


def rpc_one(method, params=None):
    r = rpc_batch([(method, params or [])])[0]
    if r.get("error"):
        raise IOError(f"{method}: {r['error']}")
    return r.get("result")


# ---------------------------------------------------------------- db

DB = None


def db():
    global DB
    if DB is None:
        os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
        DB = sqlite3.connect(DB_PATH, check_same_thread=False)
        DB.execute("PRAGMA journal_mode=WAL");
        DB.execute("CREATE TABLE IF NOT EXISTS balances(address TEXT PRIMARY KEY, received INTEGER NOT NULL DEFAULT 0)");
        DB.execute("CREATE TABLE IF NOT EXISTS blocks(height INTEGER PRIMARY KEY, data TEXT)");
        DB.execute("CREATE TABLE IF NOT EXISTS utxos(txid TEXT, vout INTEGER, address TEXT, sats INTEGER, PRIMARY KEY(txid, vout))");
        DB.execute("CREATE TABLE IF NOT EXISTS geo(ip TEXT PRIMARY KEY, country TEXT, region TEXT, city TEXT, lat REAL, lon REAL, updated INTEGER)");
        DB.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)");
        try:
            DB.execute("ALTER TABLE balances ADD COLUMN sent INTEGER NOT NULL DEFAULT 0");
        except Exception:
            pass  # column already present on existing DBs
        try:
            DB.execute("ALTER TABLE balances ADD COLUMN last_seen INTEGER NOT NULL DEFAULT 0");
        except Exception:
            pass  # 0 = never seen inside the scanned range (predates tracking)
        DB.commit()
    return DB


def meta_get(key, default=None):
    row = db().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def stamp_seen(seen):
    """Record last-activity heights (reactivations included). UPDATE-only:
    first sightings are inserted by the indexer itself. Zero extra RPC."""
    if not seen:
        return
    db().executemany("UPDATE balances SET last_seen=max(last_seen, ?) WHERE address=?",
                     [(h, a) for a, h in seen.items()])
    db().commit()


def meta_set(key, value):
    db().execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, str(value)))
    db().commit()


# ---------------------------------------------------------------- state

SNAP = {"updated": 0, "tip": 0, "blocks": [], "spacing": 526.2, "supply": {"circulating": None, "height": None, "updated": 0, "maxSupply": MAX_SUPPLY, "genesisBlock": GENESIS_BLOCK},
        "richlist": {"scanHeight": START_HEIGHT - 1, "tipHeight": 0, "done": False, "paused": False, "missedSpends": 0, "top": [],
                     "backfillHeight": START_HEIGHT - 1, "backfillDone": False},
        "peers": {"total": 0, "inbound": 0, "outbound": 0, "updated": 0, "nodes": [], "sources": []}}
SNAP_LOCK = threading.Lock()
LAST_TXOUTSET = 0.0


def refresh_peers():
    """Live peer snapshot from every configured node (1 RPC each) + cached geo."""
    nodes = []
    sources = []
    for label, url, auth in node_list():
        try:
            res = _batch_call(url, auth, [("getpeerinfo", [])])[0]
            peers = res.get("result") or []
        except Exception as e:
            print(f"[stats] peers failed on {label}: {e}", flush=True)
            sources.append({"node": label, "total": 0, "inbound": 0, "outbound": 0,
                            "error": str(e)[:100]})
            continue
        nin = nout = 0
        for p in peers:
            addr = str(p.get("addr", ""))
            ip = addr.rsplit(":", 1)[0].strip("[]")
            if not _is_public_ip(ip):
                continue  # never list LAN/private nodes
            inbound = bool(p.get("inbound", False))
            nin += inbound
            nout += (not inbound)
            nodes.append({
                "addr": addr, "ip": ip, "source": label,
                "inbound": inbound,
                "version": p.get("version"), "subver": p.get("subver"),
                "ping": round(float(p.get("pingtime", 0)) * 1000, 1) if p.get("pingtime") is not None else None,
                "startingHeight": p.get("startingheight"),
                "syncedHeaders": p.get("synced_headers"), "syncedBlocks": p.get("synced_blocks"),
                "bytesSent": p.get("bytessent"), "bytesRecv": p.get("bytesrecv"),
                "conntime": p.get("conntime"),
            })
        sources.append({"node": label, "total": len(peers),
                        "inbound": nin, "outbound": nout})
    geolocate([n["ip"] for n in nodes])
    cur = db()
    for n in nodes:
        g = cur.execute("SELECT country, region, city, lat, lon FROM geo WHERE ip=?",
                        (n["ip"],)).fetchone()
        if g:
            n["country"], n["region"], n["city"], n["lat"], n["lon"] = g
        else:
            n["country"], n["region"], n["city"], n["lat"], n["lon"] = None, None, None, None, None
    with SNAP_LOCK:
        SNAP["peers"] = {"total": len(nodes),
                         "inbound": sum(1 for n in nodes if n["inbound"]),
                         "outbound": sum(1 for n in nodes if not n["inbound"]),
                         "updated": time.time(), "nodes": nodes, "sources": sources}


def _is_public_ip(ip):
    try:
        if ":" in ip:  # IPv6: drop loopback, link-local, unique-local
            ip = ip.lower()
            if ip == "::1" or ip.startswith("fe80") or ip.startswith("fc00") or ip.startswith("fd00"):
                return False
            return True
        parts = ip.split(".")
        if len(parts) != 4:
            return False
        a = [int(x) for x in parts]
        if a[0] == 10 or (a[0] == 172 and 16 <= a[1] <= 31) or (a[0] == 192 and a[1] == 168):
            return False
        if a[0] == 127 or (a[0] == 169 and a[1] == 254) or a[0] >= 224:
            return False
        return True
    except Exception:
        return False


def geolocate(ips):
    """Batch-geolocate unknown public IPs via ip-api.com (cached in SQLite)."""
    cur = db()
    todo = []
    for ip in set(ips):
        if not _is_public_ip(ip):
            continue
        row = cur.execute("SELECT ip FROM geo WHERE ip=?", (ip,)).fetchone()
        if not row:
            todo.append(ip)
    if not todo:
        return
    try:
        import urllib.request as _rq
        body = json.dumps([{"query": ip, "fields": "status,country,regionName,city,lat,lon,query"} for ip in todo[:100]]).encode()
        req = _rq.Request("http://ip-api.com/batch", data=body,
                          headers={"Content-Type": "application/json"}, method="POST")
        with _rq.urlopen(req, timeout=30) as r:
            res = json.loads(r.read().decode())
        now = int(time.time())
        for item in res:
            if item.get("status") == "success":
                cur.execute("INSERT OR REPLACE INTO geo(ip, country, region, city, lat, lon, updated) "
                            "VALUES(?, ?, ?, ?, ?, ?, ?)",
                            (item.get("query"), item.get("country"), item.get("regionName"),
                             item.get("city"), item.get("lat"), item.get("lon"), now))
        cur.commit()
    except Exception as e:
        print(f"[stats] geolocate failed: {e}", flush=True)


def refresh_blocks_and_supply():
    global LAST_TXOUTSET
    info = rpc_one("getblockchaininfo")
    tip = int(info["blocks"])
    want_from = max(0, tip - BLOCKS_COUNT + 1)
    have = [r[0] for r in db().execute(
        "SELECT height FROM blocks WHERE height >= ? ORDER BY height", (want_from,))]
    missing = [h for h in range(want_from, tip + 1) if h not in set(have)]
    if missing:
        # hashes then full blocks, in small batches to spare the node
        hashes = []
        for i in range(0, len(missing), 200):
            chunk = missing[i:i + 200]
            hrows = rpc_batch([("getblockhash", [h]) for h in chunk])
            for h, r in zip(chunk, hrows):
                if r.get("result"):
                    hashes.append((h, r["result"]))
        for i in range(0, len(hashes), 80):
            chunk = hashes[i:i + 80]
            brows = rpc_batch([("getblock", [bh, 2]) for _, bh in chunk])
            rows = []
            seen = {}
            for (h, _), b in zip(chunk, brows):
                blk = b.get("result")
                if not blk:
                    continue
                d = parse_block(h, blk)
                d["txids"] = [t.get("txid", t) if isinstance(t, dict) else t for t in blk.get("tx", [])]
                rows.append((h, json.dumps(d)))
                # reactivation watch: stamp any known wallet touched here (zero extra RPC)
                for tx in blk.get("tx", []):
                    for vin in tx.get("vin", []):
                        if "coinbase" in vin:
                            continue
                        pt, pv = vin.get("txid"), vin.get("vout")
                        if pt is None or pv is None:
                            continue
                        r = db().execute("SELECT address FROM utxos WHERE txid=? AND vout=?",
                                         (pt, pv)).fetchone()
                        if r:
                            seen[r[0]] = max(seen.get(r[0], 0), h)
                    for vout in tx.get("vout", []):
                        if not vout.get("value", 0):
                            continue
                        a = script_to_address(vout.get("scriptPubKey", {}).get("hex", ""))
                        if a:
                            seen[a] = max(seen.get(a, 0), h)
            stamp_seen(seen)
            if rows:
                db().executemany("INSERT OR REPLACE INTO blocks(height, data) VALUES(?, ?)", rows)
                db().commit()
        db().execute("DELETE FROM blocks WHERE height < ?", (want_from,))
        db().commit()
    blocks = [json.loads(r[0]) for r in db().execute(
        "SELECT data FROM blocks WHERE height >= ? ORDER BY height DESC", (want_from,))]
    spacing = SNAP.get("spacing") or 526.2
    if len(blocks) >= 2:
        try:
            dt = (blocks[0].get("time") or 0) - (blocks[-1].get("time") or 0)
            if dt > 0:
                spacing = dt / (len(blocks) - 1)
        except Exception:
            pass
    with SNAP_LOCK:
        SNAP["tip"] = tip
        SNAP["blocks"] = blocks
        SNAP["spacing"] = spacing
        SNAP["richlist"]["tipHeight"] = tip
        SNAP["updated"] = time.time()
    # circulating supply: gettxoutsetinfo is a full UTXO scan (many minutes)
    # -> opt-in only, rare refresh, on its own thread so it never blocks polls.
    if os.environ.get("SUPPLY_SCAN", "0") == "1" and time.time() - LAST_TXOUTSET > 12 * 3600:
        threading.Thread(target=supply_scan_once, daemon=True).start()


INITIAL_REWARD = 12.5  # BCA block reward at genesis block (Bitcoin halving level)

def _calculate_supply(tip_height):
    """Calculate circulating supply from genesis to tip using block rewards.
    Much faster than gettxoutsetinfo on a busy node."""
    total = 0.0
    remaining = tip_height - GENESIS_BLOCK + 1
    height = GENESIS_BLOCK
    while remaining > 0:
        halvings = (height - GENESIS_BLOCK) // 210000
        reward = INITIAL_REWARD / (2 ** halvings)
        next_halving = GENESIS_BLOCK + (halvings + 1) * 210000
        blocks_in_range = min(remaining, next_halving - height)
        total += blocks_in_range * reward
        remaining -= blocks_in_range
        height = next_halving
    return total

def supply_scan_once():
    global LAST_TXOUTSET
    if getattr(supply_scan_once, "running", False):
        return
    supply_scan_once.running = True
    try:
        # Use block-based calculation instead of slow gettxoutsetinfo
        tip = rpc_one("getblockcount")
        circulating = _calculate_supply(int(tip))
        with SNAP_LOCK:
            SNAP["supply"] = {"circulating": circulating, "height": int(tip),
                              "updated": time.time(), "maxSupply": MAX_SUPPLY,
                              "genesisBlock": GENESIS_BLOCK}
        LAST_TXOUTSET = time.time()
        print(f"[stats] supply updated: {circulating:.0f} at {tip}", flush=True)
    except Exception as e:
        print(f"[stats] supply scan failed: {e}", flush=True)
    finally:
        supply_scan_once.running = False


NBXPLORER = os.environ.get("NBXPLORER_URL", "http://nbxplorer-bca:24444/v1")


def nbx(path, method="GET"):
    """Same-host explorer call (no auth). Returns parsed JSON or raises."""
    req = urllib.request.Request(NBXPLORER + path, method=method)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def wallet_detail(addr):
    """Wallet stats: received (local post-fork index), balance + txs (explorer).
    Auto-tracks the address first so the explorer serves its data."""
    addr = addr.strip()
    if not addr:
        raise ValueError("empty address")
    try:
        nbx("/cryptos/bca/addresses/" + urllib.parse.quote(addr), method="POST")
    except Exception:
        pass  # already tracked or temporarily unavailable; read anyway
    txs, utxos = {"confirmedTransactions": {"transactions": []}}, {}
    try:
        txs = nbx("/cryptos/bca/addresses/" + urllib.parse.quote(addr) + "/transactions")
    except Exception as e:
        txs = {"error": str(e)[:150]}
    try:
        utxos = nbx("/cryptos/bca/addresses/" + urllib.parse.quote(addr) + "/utxos")
    except Exception as e:
        utxos = {"error": str(e)[:150]}
    received = 0.0
    sent = None
    try:
        row = db().execute("SELECT received, sent FROM balances WHERE address=?", (addr,)).fetchone()
        if row:
            received = row[0] / 1e8
            sent = row[1] / 1e8
    except Exception:
        pass
    balance = 0.0
    try:
        for grp in ("confirmed", "unconfirmed"):
            for u in (utxos.get(grp, {}) or {}).get("utxOs", []) or []:
                balance += float(u.get("value", 0))
    except Exception:
        pass
    with SNAP_LOCK:
        cov = {"scanHeight": SNAP["richlist"]["scanHeight"], "tipHeight": SNAP["richlist"]["tipHeight"]}
    sent_out = sent if sent is not None else round(max(received - balance, 0.0), 8)
    return {"address": addr, "received": round(received, 8), "balance": round(balance, 8),
            "sent": sent_out, "sentReal": sent is not None,
            "transactions": txs, "coverage": cov,
            "note": "received/sent count indexed post-fork outputs; balance is live explorer truth"}


def block_detail(h):
    """Single-block info for UI drill-down. Prefers the local cache;
    otherwise one live fetch (user-initiated, rare)."""
    row = db().execute("SELECT data FROM blocks WHERE height=?", (h,)).fetchone()
    if row:
        d = json.loads(row[0])
    else:
        bh = rpc_batch([("getblockhash", [h])])[0].get("result")
        if not bh:
            raise IOError(f"no hash at height {h}")
        blk = rpc_batch([("getblock", [bh, 2])])[0].get("result") or {}
        d = parse_block(h, blk)
        d["txids"] = [t.get("txid", t) if isinstance(t, dict) else t for t in blk.get("tx", [])]
        return d
    # full txid list needs one light fetch (verbosity 1)
    if "txids" not in d:
        bh = rpc_batch([("getblockhash", [h])])[0].get("result")
        if bh:
            blk = rpc_batch([("getblock", [bh, 1])])[0].get("result") or {}
            d["txids"] = blk.get("tx", [])
    return d


def parse_block(h, blk):
    txs = blk.get("tx", [])
    reward = 0.0
    payees = []
    if txs:
        for vout in txs[0].get("vout", []):
            reward += float(vout.get("value", 0))
            a = script_to_address(vout.get("scriptPubKey", {}).get("hex", ""))
            if a:
                payees.append(a)
    return {"height": h, "hash": blk.get("hash"), "time": blk.get("time"),
            "txs": len(txs), "size": blk.get("size"),
            "flags": blk.get("flags"), "reward": round(reward, 8),
            "payees": payees[:3], "pool": any(a in POOL_ADDRESSES for a in payees)}


def index_blocks():
    """Resumable forward scan of post-fork outputs into SQLite (batched).
    Disabled by default (INDEX_ENABLED=1 to resume): the backfill is the
    only heavy RPC consumer and the node has no headroom for it."""
    if not INDEX_ENABLED:
        print("[stats] indexer disabled (INDEX_ENABLED=1 to resume)", flush=True)
        with SNAP_LOCK:
            SNAP["richlist"]["paused"] = True
        return
    while not CODEC_OK:
        print("[stats] indexer waiting for ADDRESS_CODEC_OK...", flush=True)
        if selftest():
            break
        time.sleep(1800)
    height = int(meta_get("scan_height", START_HEIGHT - 1))
    while True:
        try:
            tip = int(rpc_one("getblockchaininfo")["blocks"])
            if height >= tip:
                with SNAP_LOCK:
                    SNAP["richlist"]["scanHeight"] = height
                    SNAP["richlist"]["done"] = True
                publish_top()
                time.sleep(15)
                continue
            with SNAP_LOCK:
                SNAP["richlist"]["done"] = False
            lo = height + 1
            hi = min(lo + 9, tip)
            hrows = rpc_batch([("getblockhash", [h]) for h in range(lo, hi + 1)])
            hashes = []
            for r in hrows:
                bh = r.get("result")
                if not bh:
                    break
                hashes.append(bh)
            if not hashes:
                time.sleep(5)
                continue
            brows = rpc_batch([("getblock", [bh, 2]) for bh in hashes])
            recv = {}   # addr -> [received_sats, sent_sats]
            utxo_add = []
            utxo_del = []
            missed = 0
            done = 0
            cur = db()
            for b in brows:
                blk = b.get("result")
                if not blk:
                    break
                bh = blk.get("height") or 0
                for tx in blk.get("tx", []):
                    txid = tx.get("txid")
                    for vin in tx.get("vin", []):
                        if "coinbase" in vin:
                            continue
                        pt, pv = vin.get("txid"), vin.get("vout")
                        if pt is None or pv is None or txid is None:
                            continue
                        row = cur.execute("SELECT address, sats FROM utxos WHERE txid=? AND vout=?",
                                          (pt, pv)).fetchone()
                        if row:
                            e = recv.setdefault(row[0], [0, 0, 0])
                            e[1] += row[1]
                            e[2] = max(e[2], bh)
                            utxo_del.append((pt, pv))
                        else:
                            # prevout created before scan start (or pruned) — unattributable
                            missed += 1
                    for vout in tx.get("vout", []):
                        val = vout.get("value", 0)
                        if not val or val <= 0:
                            continue
                        sats = int(round(float(val) * 1e8))
                        a = script_to_address(vout.get("scriptPubKey", {}).get("hex", ""))
                        if a and txid:
                            e = recv.setdefault(a, [0, 0, 0])
                            e[0] += sats
                            e[2] = max(e[2], bh)
                            utxo_add.append((txid, vout.get("n", 0), a, sats))
                done += 1
            if recv:
                cur.executemany(
                    "INSERT INTO balances(address, received, sent, last_seen) VALUES(?, ?, ?, ?) "
                    "ON CONFLICT(address) DO UPDATE SET received=received+excluded.received, "
                    "sent=sent+excluded.sent, last_seen=max(last_seen, excluded.last_seen)",
                    [(a, r, s, h) for a, (r, s, h) in recv.items()])
            if utxo_add:
                cur.executemany("INSERT OR REPLACE INTO utxos(txid, vout, address, sats) VALUES(?, ?, ?, ?)",
                                utxo_add)
            if utxo_del:
                cur.executemany("DELETE FROM utxos WHERE txid=? AND vout=?", utxo_del)
            height += done
            db().commit()
            if missed:
                total_missed = int(meta_get("missed_spends", 0)) + missed
                meta_set("missed_spends", total_missed)
                with SNAP_LOCK:
                    SNAP["richlist"]["missedSpends"] = total_missed
            meta_set("scan_height", height)
            with SNAP_LOCK:
                SNAP["richlist"]["scanHeight"] = height
            if height % 5000 < 50:
                publish_top()
            if INDEX_THROTTLE:
                time.sleep(INDEX_THROTTLE)
        except Exception as e:
            # Work-queue-exceeded (http 500) means the node is saturated:
            # back off hard instead of hammering it.
            wait = 300 if "500" in str(e) else 10
            print(f"[stats] indexer error at {height}: {e} (retry in {wait}s)", flush=True)
            time.sleep(wait)


BACKFILL_ENABLED = os.environ.get("BACKFILL_ENABLED", "1") == "1"

BAND_LIMITS = [(30, "active"), (180, "quiet"), (730, "dormant"), (float("inf"), "deep")]


def band_for(days):
    if days is None:
        return "unknown"
    for limit, name in BAND_LIMITS:
        if days < limit:
            return name
    return "deep"


def dormant(limit=500):
    """Funded wallets by dormancy. last_seen=0 predates tracking (unknown).
    Zero extra RPC: pure SQLite + snapshot."""
    with SNAP_LOCK:
        tip = SNAP.get("tip", 0) or 0
        spacing = SNAP.get("spacing") or 526.2
        scan_h = SNAP["richlist"].get("scanHeight", 0)
        bf_h = SNAP["richlist"].get("backfillHeight", 0)
        bf_done = SNAP["richlist"].get("backfillDone", False)
    rows = db().execute(
        "SELECT address, received, sent, last_seen FROM balances "
        "WHERE (received - sent) > 0").fetchall()
    out, counts, sums = [], {}, {}
    for addr, rec, sent, seen in rows:
        bal = (rec - sent) / 1e8
        if seen and tip:
            days = (tip - seen) * spacing / 86400.0
            band = band_for(days)
        else:
            days, band = None, "unknown"
        counts[band] = counts.get(band, 0) + 1
        sums[band] = sums.get(band, 0.0) + bal
        out.append({"address": addr, "balance": round(bal, 8),
                    "received": round(rec / 1e8, 8), "sent": round(sent / 1e8, 8),
                    "last_seen": seen, "days_idle": None if days is None else round(days, 1),
                    "band": band})
    BAND_ORDER = {"active": 0, "quiet": 1, "dormant": 2, "deep": 3, "unknown": 4}
    out.sort(key=lambda e: (BAND_ORDER.get(e["band"], 9), -e["balance"]))
    whole = {b: int(round(s)) for b, s in sums.items()}
    return {"tip": tip, "spacing": round(spacing, 1), "funded": len(out), "bands": counts,
            "sums": whole, "scanHeight": scan_h, "backfillHeight": bf_h, "backfillDone": bf_done,
            "list": out[:limit]}


def backfill_last_seen():
    """Second pass over the indexed range stamping last_seen for RECEIVES.
    Starts only after the first pass reaches the tip; read-only for
    balances/utxos (never touches received/sent: no double counting).
    Spends whose prevouts are already spent can't be attributed here —
    those converge via the live indexer instead. Same throttle, resumable."""
    if not BACKFILL_ENABLED:
        print("[stats] backfill disabled (BACKFILL_ENABLED=1 to resume)", flush=True)
        return
    while True:
        try:
            tip = int(rpc_one("getblockchaininfo")["blocks"])
            scanned = int(meta_get("scan_height", START_HEIGHT - 1))
            if scanned < tip - 1:
                time.sleep(120)
                continue  # first pass still running
            bh = int(meta_get("backfill_height", START_HEIGHT - 1))
            if bh >= min(scanned, tip):
                with SNAP_LOCK:
                    SNAP["richlist"]["backfillDone"] = True
                    SNAP["richlist"]["backfillHeight"] = bh
                time.sleep(300)
                continue
            lo, hi = bh + 1, min(bh + 10, scanned, tip)
            hrows = rpc_batch([("getblockhash", [h]) for h in range(lo, hi + 1)])
            hashes = [r.get("result") for r in hrows if r.get("result")]
            if hashes:
                brows = rpc_batch([("getblock", [bhh, 2]) for bhh in hashes])
                seen = {}
                for b in brows:
                    blk = b.get("result")
                    if not blk:
                        continue
                    h = blk.get("height") or 0
                    for tx in blk.get("tx", []):
                        for vout in tx.get("vout", []):
                            if not vout.get("value", 0):
                                continue
                            a = script_to_address(vout.get("scriptPubKey", {}).get("hex", ""))
                            if a:
                                seen[a] = max(seen.get(a, 0), h)
                stamp_seen(seen)
            meta_set("backfill_height", hi)
            with SNAP_LOCK:
                SNAP["richlist"]["backfillHeight"] = hi
            if INDEX_THROTTLE:
                time.sleep(INDEX_THROTTLE)
        except Exception as e:
            wait = 300 if "500" in str(e) else 30
            print(f"[stats] backfill error: {e} (retry in {wait}s)", flush=True)
            time.sleep(wait)


def publish_top():
    try:
        rows = db().execute(
            "SELECT address, received, sent FROM balances ORDER BY (received - sent) DESC LIMIT 100").fetchall()
        with SNAP_LOCK:
            circ = SNAP["supply"]["circulating"]
            top = []
            for addr, rec, sent in rows:
                bal = (rec - sent) / 1e8
                coins = rec / 1e8
                top.append({"address": addr, "balance": round(bal, 8),
                            "received": round(coins, 8), "sent": round(sent / 1e8, 8),
                            "pctMax": round(100 * bal / MAX_SUPPLY, 4),
                            "pctCirc": round(100 * bal / circ, 4) if circ else None})
            SNAP["richlist"]["top"] = top
    except Exception as e:
        print(f"[stats] publish_top failed: {e}", flush=True)


# ---------------------------------------------------------------- http

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/health":
            body = b"ok"
        elif self.path == "/stats.json":
            with SNAP_LOCK:
                snap = dict(SNAP)
                snap["blocks"] = (snap.get("blocks") or [])[:25]
                body = json.dumps(snap).encode()
        elif self.path.startswith("/block/"):
            try:
                h = int(self.path.rsplit("/", 1)[-1])
                body = json.dumps(block_detail(h)).encode()
            except ValueError:
                self.send_response(400)
                self.end_headers()
                return
            except Exception as e:
                body = json.dumps({"error": str(e)[:200]}).encode()
        elif self.path == "/dormant" or self.path.startswith("/dormant?"):
            try:
                q = urllib.parse.urlparse(self.path)
                lim = int(urllib.parse.parse_qs(q.query).get("limit", ["200"])[0])
                body = json.dumps(dormant(max(1, min(lim, 500)))).encode()
            except Exception as e:
                body = json.dumps({"error": str(e)[:200]}).encode()
        elif self.path == "/txs":
            try:
                body = json.dumps(recent_txs()).encode()
            except Exception as e:
                body = json.dumps({"error": str(e)[:200]}).encode()
        elif self.path.startswith("/wallet/"):
            try:
                addr = urllib.parse.unquote(self.path.rsplit("/", 1)[-1])
                body = json.dumps(wallet_detail(addr)).encode()
            except Exception as e:
                body = json.dumps({"error": str(e)[:200]}).encode()
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


def recent_txs(limit_blocks=30, limit_txs=150):
    """Latest onchain txs from the local block cache (zero RPC)."""
    out = []
    try:
        rows = db().execute(
            "SELECT data FROM blocks ORDER BY height DESC LIMIT ?", (limit_blocks,)).fetchall()
    except Exception:
        return out
    for (data,) in rows:
        try:
            b = json.loads(data)
        except Exception:
            continue
        for txid in b.get("txids", []) or []:
            out.append({"txid": txid, "height": b.get("height"), "time": b.get("time"),
                        "type": b.get("flags"), "pool": bool(b.get("pool"))})
            if len(out) >= limit_txs:
                return out
    return out


def serve():
    srv = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[stats] serving on :{PORT}", flush=True)
    srv.serve_forever()


def poll_loop():
    while True:
        try:
            refresh_blocks_and_supply()
            refresh_peers()
            publish_top()
        except Exception as e:
            wait = 120 if "500" in str(e) else POLL_INTERVAL
            print(f"[stats] poll failed: {e} (retry in {wait}s)", flush=True)
            time.sleep(wait)
            continue
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    print(f"[stats] start height={START_HEIGHT} pool_addrs={len(POOL_ADDRESSES)}", flush=True)
    selftest()
    threading.Thread(target=poll_loop, daemon=True).start()
    threading.Thread(target=index_blocks, daemon=True).start()
    threading.Thread(target=backfill_last_seen, daemon=True).start()
    serve()
