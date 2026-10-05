# Data licences and permissions

| Source | Status | Confirmed | Scope | Evidence |
|---|---|---|---|---|
| MeroLagani (merolagani.com) | **Written agreement held by the owner** | 2026-10-05, confirmed by the owner in the Phase 2 instructions | Automated collection, bulk floorsheet history, and commercial use | The agreement is held by the owner and is not stored in this repository. This record replaces the terms concern raised in data phase A, where the public Disclaimer/Terms page prohibits automated collection without such an agreement |
| Sharesansar (sharesansar.com) | No agreement. Personal research build | — | Rate-limited collection (one request per 3 s per host) under robots.txt (allows all); the Terms & Conditions have no anti-automation clause | `docs/PHASE_LOG.md` data phase A |
| NEPSE (nepalstock.com) | No licence | — | Live prices through `nepse_scraper` (the website's anonymous token handshake). The notice API answers 401 and is not used | Before a commercial launch an official NEPSE data licence is needed (`TODOS.md`) |
| Nepal Rastra Bank (nrb.org.np) | Public government publications | — | robots.txt allows everything except `/wp-admin/` | — |
| SEBON, CDSC | Public publications | — | robots.txt allows everything | — |
| Bizmandu, Arthasarokar, Kathmandu Post | Live news collection only | — | Terms for bulk back-collection not reviewed | `TODOS.md` |

This build is a personal research project, not a product. Polite rate limits and robots.txt apply to every collector.
