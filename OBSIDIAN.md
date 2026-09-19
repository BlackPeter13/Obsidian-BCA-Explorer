# obsidian-BCA-explorer

NBXplorer (minimal UTXO tracker for HD wallets) with **Bitcoin Atom (BCA)**
support, ported to .NET 8, Dockerized like `obsidian-BCA-electrum`.

Upstream: https://github.com/bitcoin-atom/NBXplorer (unmodified upstream has
no BCA chain and targets .NET Core 2.1, which is EOL).

## What changed vs upstream

* `NBXplorer.Client/NBXplorerNetworkProvider.BitcoinAtom.cs` (new) — BCA
  network set: P2PKH=23, P2SH=10, WIF=128, P2P=7333, RPC=7332,
  P2P magic `0xe81dc14f`, bech32 `bca`, BTC-shared genesis. Registered as
  crypto code `BCA` (env: `NBXPLORER_BCA*`).
* Ported `netcoreapp2.1` → `net8.0` (server + nodewaiter; client stays
  `netstandard2.0`). ASP.NET Core 2.1 removals fixed: `IHostingEnvironment`,
  `AddJsonFormatters` (now NewtonsoftJson), `UseMvc` (endpoint routing),
  `*.Internal` namespaces, `MvcJsonOptions`, `EnableRewind`.
* `BitcoinDWaiter`: BCA syncs headers **via RPC**, not P2P. BCA is PoW+PoS
  and its canonical PoS block hash commits to the trailing flags, so a
  vanilla P2P header sync diverges at the first PoS block (mainnet 586956)
  and loops forever. The RPC path links canonical `(height, hash)` pairs
  (batched `getblockhash`, one HTTP round trip per 1024 heights) and also
  avoids NBitcoin's typed `getblockchaininfo` parser, which chokes on BCA's
  `difficulty` object.
* New `Dockerfile` (multi-stage SDK → ASP.NET runtime, non-root,
  healthcheck on `/v1/cryptos/bca/status`), `docker-compose.yml`
  (`nbxplorer-bca` :24444 + `nbxplorer-frontend` :8080, nginx proxies
  `/api/` to the explorer), `.env` (same LAN daemon as electrum:
  `blackpeter:blackpeter@192.168.1.53:7332`, P2P `:7333`), `obsidian.sh`
  (`install|up|down|restart|status|logs`), `frontend/` (fancy monitor with
  sync tiles, fee estimates, track/inspect tool).

## Run

Fully offline install (no internet needed — only the BCA daemon at runtime):

```bash
./install.sh   # loads images/*.tar, uses vendor/ feeds, builds, starts, verifies
```

What lives where (all on disk, only code is committed):

* `images/*.tar` — base images (`docker load`ed, never pulled)
* `vendor/nuget/` — 216 NuGet packages (sole restore source via `NuGet.config`)
* `vendor/debs-runtime/` — curl + closure for the runtime image (dpkg, no apt)
* `data/nbxplorer`, `data/stats` — synced chain cache + stats DB, bind-mounted:
  restart/reinstall resumes and only updates, never resyncs from scratch
* `./obsidian.sh` — daily ops (`up|down|restart|status|logs`)

Online equivalent of the same steps: `cp .env.example .env` (edit daemon),
`docker compose build`, `docker compose up -d`.

API: `http://localhost:24444/v1/cryptos/bca/status`
Monitor: `http://localhost:8080/`

## Known limitations (v1)

* The stats service is deliberately gentle on the production node
  (incremental block cache, ~10 min polls, 3s-paced indexer, hard backoff
  on busy signals). The post-fork rich-list index therefore fills over
  days, not minutes — the UI shows live scan progress.
* Circulating supply (`gettxoutsetinfo` is a minutes-long full UTXO scan)
  is opt-in: `SUPPLY_SCAN=1`. Max emission is fixed at 21,000,000 BCA.
* Fee estimation returns `fee-estimation-unavailable` — the BCA node
  itself reports `Insufficient data or no feerate found`.
* `canScanTxoutSet: false` — BCA core 0.16.2 predates `scantxoutset`.
* PoS relay edge: steady-state P2P tracking was validated on PoW flow;
  a future PoS block may need a restart (the RPC sync then catches up
  deterministically). Restart reloads the cached chain in seconds.
