# Optimization.md — obsidian-BCA-explorer rework log

Upstream: https://github.com/bitcoin-atom/NBXplorer (2018, .NET Core 2.1, no BCA chain).
Result: a working, Dockerized BCA explorer stack, synced to tip and healthy,
with its own stats layer. Committed locally in 6 commits (see `git log`).

## 1. BCA chain support (new: `NBXplorer.Client/NBXplorerNetworkProvider.BitcoinAtom.cs`)

- Registered Bitcoin Atom as crypto code `BCA` (env: `NBXPLORER_BCA*`).
- Params verified against BCA Core `chainparams`: P2PKH 23 / P2SH 10 / WIF 128,
  P2P 7333 / RPC 7332, bech32 `bca`, BTC-shared genesis block.
- Fixed P2P magic byte order (wire bytes are little-endian uints; first
  handshake failure exposed this).

## 2. .NET 8 port (`netcoreapp2.1` → `net8.0`, client stays `netstandard2.0`)

- `IHostingEnvironment` → `IWebHostEnvironment`
- `AddJsonFormatters` → `AddNewtonsoftJson` (+ `Microsoft.AspNetCore.Mvc.NewtonsoftJson`)
- `UseMvc` → `UseRouting` / `UseAuthentication` / `UseAuthorization` / `UseEndpoints(MapControllers)`
- Removed `*.Internal` namespaces (`Hosting`, `Mvc`, `Http`, logging console/abstractions)
- `MvcJsonOptions` → `MvcNewtonsoftJsonOptions` (3 files)
- `EnableRewind` → `EnableBuffering`
- Console logger rewritten onto public logging APIs (`SimpleSystemConsole`,
  local `NullScope`); dropped EOL `System.Xml.XmlSerializer` /
  `System.Net.WebSockets.Client` package refs.

## 3. PoW+PoS header-sync fix (`NBXplorer/BitcoinDWaiter.cs`)

- Symptom: header sync stalled forever at height 586956, waiter timing out in loops.
- Root cause (proven with packet-level probes): block 586956 is BCA's first
  proof-of-stake block. Its canonical hash commits to trailing flags that
  NBitcoin cannot see, so the local tip diverged from consensus and the next
  header could never link.
- Fix: BCA syncs `(height, canonical hash)` pairs over batched JSON-RPC
  (`getblockhash` batches) instead of P2P — full 1M-header sync in minutes,
  resumable via the slim-chain cache.
- Companion fixes: raw-JSON `getblockchaininfo` (NBitcoin's typed parser
  chokes on BCA's `difficulty` object `{"proof-of-work":..,"proof-of-stake":..}`),
  hash-only cache validation (never parses 84-byte PoS header hex),
  `ChainConfiguration.Args` carries RPC creds for the batch client.

## 4. Docker stack (mirrors `obsidian-BCA-electrum` layout)

- Multi-stage `Dockerfile` (SDK build with cached restore layers → ASP.NET
  runtime, non-root user, healthcheck on `/v1/cryptos/bca/status`, log limits).
- `docker-compose.yml`: `nbxplorer-bca` (:24444), `nbxplorer-frontend` (:8080,
  nginx proxies `/api/` → explorer, `/stats/` → stats), `bca-stats` (internal :8090).
- `.env` (same LAN daemon: `blackpeter:blackpeter@192.168.1.53:7332`, P2P `:7333`),
  `.env.example`, `.dockerignore`, `obsidian.sh` (`install|up|down|restart|status|logs`).

## 5. Stats service (`stats/`, stdlib-only Python, no pip)

- **Found blocks**: last N blocks (height/hash/time/txs/size/reward/PoW-PoS/pool tag
  matched against pool payout addresses), SQLite-cached, incremental refresh.
- **Supply**: max 21,000,000 BCA fixed; circulating via opt-in `gettxoutsetinfo`
  (`SUPPLY_SCAN=1`, own thread + 30-min timeout — it is a minutes-long UTXO scan).
- **Rich list**: resumable post-fork index (from 505888) with full **spend tracking**
  (local UTXO map → true current balances, received, sent, % of max/circulating,
  untracked-spend counter). Gated by a boot self-test against node truth.
- **Wallet drill-down**: auto-tracks the address in NBXplorer, returns
  received / balance / sent (+real-vs-approx flag) / tx list / coverage note.
- **Block drill-down**: `/stats/block/<height>` (cache-first, one live fetch fallback).
- **Peer map**: `getpeerinfo` per configured node (`BCA_EXTRA_NODES`), SQLite-cached
  batch geolocation, Leaflet map (green=inbound, blue=outbound) + table;
  LAN/private addresses never listed.
- **Address-codec bug found by the self-test**: scripts already embed the hash,
  so addresses encode it directly — an extra hash160 round produced valid-looking
  but wrong addresses. Fixed; proven against `decodescript` on synthetic and
  live-chain scripts at every boot.

## 6. RPC discipline (production node has no headroom)

- Gateway: max 2 concurrent batches, ≥1s pacing, circuit breaker parks all
  traffic 10 min on any http-500, per-loop backoff (poll 120–600s, indexer 300s).
- Incremental everything: block cache (only new heights fetched), resumable index
  with SQLite progress, geolocation cached (unknown IPs only).
- Heavy jobs are opt-in and sequenced, never parallel: `SUPPLY_SCAN`,
  `INDEX_ENABLED`. Sustained footprint when idle: a few small batches per 10 min.
- Lesson learned twice: `gettxoutsetinfo` pins node CPU for many minutes;
  run it in maintenance windows or against a non-pool node.

## 7. Frontend (`frontend/nbxplorer-monitor.html`, dependency-free except Leaflet CDN)

- Phase 1: static monitor → live dashboard (sync/node/fees/instance tiles,
  track-and-inspect tool, endpoint links).
- Phase 2: found-blocks table + block drill-down, supply tiles, rich-list table
  with progress, wallet card, peer map + table, all same-origin via nginx.
- Rebrand: Obsidian Ecosystem V1 naming, footer `BlackPeter ❤` (pulsing).

## Verified end-to-end

- `isFullySynched: true` at tip, container `healthy`, cache restart in seconds.
- Track → transactions → UTXOs round-trip 200s through the frontend proxy.
- `/stats/block/<h>` and `/stats/wallet/<addr>` return node-consistent data.
- Peer snapshot geolocated; LAN peers filtered; multi-node aggregation present.
- Indexer pace ~15 blocks/s throttled (post-fork backfill is a multi-day job).

## Known limitations (v1)

- Fee estimates 400 (node: "Insufficient data or no feerate found").
- `canScanTxoutSet: false` (BCA Core 0.16.2 predates `scantxoutset`).
- Rich list covers post-fork receipts; pre-fork-funded spends are untracked
  (counted, labeled). Circulating needs the node UTXO scan.
- Steady-state PoS relay edge: a future PoS block may need a container restart
  (RPC tail re-syncs deterministically).

## Tests (Obsidian.Tests, 10/10 green, offline)
`dotnet test Obsidian.Tests/` — no node, no network, runs on the vendored
feed (xunit 2.9.3 + Test SDK 17.8.0 vendored, 216 -> 232 packages).
Encodes all three production bugs as vectors:
* PoS hash rule + body offset: real mainnet blocks 1025208/1025209 bytes.
* Coinbase null-prevout skip + genuine double-spend still throws.
* Stale-branch records type Orphan (SlimChain purges reorgs, proven by test)
  and stay out of the confirmed set; main-chain double-spend still throws.
A fork-eviction resolver was built, proven unreachable by test, and removed
again — dead code deleted, not kept.

## Parked: BCA-aware consensus factory (NBXplorer.Client/BCAConsensus.cs, dormant)
Proven correct in isolation (2000/2000 real wire headers parse+link in 35ms,
both id vectors match) but NOT activated, for two structural reasons found
by packet-level probing:
* NBitcoin's message pipeline parses payloads with the default factory,
  so a network-level factory never takes effect on P2P data.
* `Consensus` freezes at network build, so it can only be swapped pre-seal
  (which breaks the shared 80-byte genesis parse).
Net effect of the episode: zero (all touched code reverted byte-identical).
Revisit only with a custom message pipeline, never with the factory alone.
