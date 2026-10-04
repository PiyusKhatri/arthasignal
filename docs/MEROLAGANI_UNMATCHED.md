# Merolagani company names without a symbol

The Merolagani quarterly-report index has 11,794 rows. After matching names to `companies` and resolving through one Merolagani detail page per name, **63 company-name strings (375 rows) still have no symbol** in `quarterly_report_announcements_resolved`. For each one, `src/scrapers/merolagani_unmatched.py` refetched a Merolagani detail page for the symbol it shows, checked that symbol against `companies` and `symbol_history`, and computed the closest listed company name by string similarity. Full list: `docs/merolagani_unmatched.csv`, one row per name string with its evidence.

**Nothing was merged.** None of the 63 names carries a Merolagani symbol that exists in `companies`. In 13 strings (KIST × 12, SHALIGRAM) the symbol exists only in `symbol_history` as a company later **merged into** another, and a successor's symbol is not the same issuer. The closest-name candidates are, on inspection, different companies: RBBD83 is a debenture, NLICL is National Life rather than National Insurance, and SABSL is Sabaiko rather than Sajilo. A first version of the script did call the two `symbol_history` cases "certain"; that was wrong and was corrected before anything was written to the database.

Grouped by the symbol Merolagani shows:

| Merolagani symbol | Name on Merolagani | Names / rows | Closest listed name (similarity) | Proposal (nothing auto-merged) |
| --- | --- | ---: | --- | --- |
| RBB | Rastriya Banijya Bank Limited | 2 / 46 | RBBD83 (1.0) | none: Rastriya Banijya Bank equity is not in the NEPSE company list; RBBD83 is its debenture, not the equity |
| MALIC | MetLife American Life Insurance Company | 3 / 45 | ILI (0.67) | none: MetLife (foreign insurer branch), not a listed equity |
| ORIENTAL | The Oriental Insurance Company Limited | 2 / 41 | PICL (0.84) | none: Oriental Insurance (foreign insurer branch), not a listed equity |
| NICLL | National Insurance Company Limited | 2 / 33 | NLICL (0.88) | none: National Insurance Company (foreign insurer branch); NLICL is National Life Insurance, a different company |
| NCBL | Rastriya Sahakari Bank Limited | 2 / 22 | RBBD83 (0.74) | none: National Co-operative Bank (Rastriya Sahakari Bank), not a listed equity |
| PMIL | Protective Micro Insurance Limited | 1 / 13 | PIC (0.74) | absent from companies: a delisted or merged issuer missing from the company list (or newly listed); add the symbol after verification; the closest listed name is a different company |
| LMLIC | Liberty Micro Life Insurance Limited | 1 / 11 | CREST (0.85) | absent from companies: a delisted or merged issuer missing from the company list (or newly listed); add the symbol after verification; the closest listed name is a different company |
| RJBCL | Rastriya Jeewan Beema Company Limited | 2 / 12 | RBCL (0.8) | add symbol after checking the listing date; RBCL (Rastriya Beema Company, non-life) is a different company |
| SMIC | Star Micro Insurance Company Limited | 1 / 11 | NMIC (0.83) | absent from companies: a delisted or merged issuer missing from the company list (or newly listed); add the symbol after verification; the closest listed name is a different company |
| GSLBSL | Grameen Swayamsewak Laghubitta Bittiya Sanstha Limited | 1 / 10 | GBLBS (0.84) | absent from companies: a delisted or merged issuer missing from the company list (or newly listed); add the symbol after verification; the closest listed name is a different company |
| MLIL | Mahalaxmi Life Insurance Limited | 3 / 16 | PMLI (0.87) | none as a match: Mahalaxmi Life later became Prabhu Mahalaxmi Life (PMLI) by merger, a successor and not the same issuer |
| TRUST | Trust Micro Insurance Limited | 1 / 10 | CREST (0.81) | absent from companies: a delisted or merged issuer missing from the company list (or newly listed); add the symbol after verification; the closest listed name is a different company |
| SEEL | Seed Energy Limited | 1 / 8 | PURE (0.73) | absent from companies: a delisted or merged issuer missing from the company list (or newly listed); add the symbol after verification; the closest listed name is a different company |
| SUPER | Super Laghubitta Bittiya Sanstha Limited | 1 / 8 | ULBSL (0.92) | absent from companies: a delisted or merged issuer missing from the company list (or newly listed); add the symbol after verification; the closest listed name is a different company |
| NAGARIK | Nagarik Laghubitta Bittiya Sanstha ltd | 1 / 7 | SABSL (0.91) | absent from companies: a delisted or merged issuer missing from the company list (or newly listed); add the symbol after verification; the closest listed name is a different company |
| SAJILO | Sajilo Laghubitta Sanstha Ltd | 1 / 7 | SABSL (0.93) | absent from companies: a delisted or merged issuer missing from the company list (or newly listed); add the symbol after verification; the closest listed name is a different company |
| SHALIGRAM | Shaligram Laghubitta Bittiya Sanstha Limited | 1 / 7 | KMCDB (0.9) | none as a match: merged into CYCL in 2021-06 (symbol_history), a successor and not the same issuer |
| HFHPL | Hulas Finserv Hire Purchase Limited | 2 / 10 | GLICLP (0.51) | absent from companies: a delisted or merged issuer missing from the company list (or newly listed); add the symbol after verification; the closest listed name is a different company |
| GHARELU | Gharelu Laghubitta Bittiya Sanstha Limited | 1 / 5 | SMATA (0.91) | possible predecessor of SMATA (Samata Gharelu Laghubitta) by merger; a successor, so do not map |
| JSLBSL | Janasewi Laghubitta Bittiya Sanstha Limited | 1 / 5 | MLBS (0.9) | absent from companies: a delisted or merged issuer missing from the company list (or newly listed); add the symbol after verification; the closest listed name is a different company |
| DEURALI | Deurali Laghubitta Bittiya Sanstha Limited | 1 / 4 | MSLB (0.9) | absent from companies: a delisted or merged issuer missing from the company list (or newly listed); add the symbol after verification; the closest listed name is a different company |
| ASLSSSL | Aarthik Samriddhi Laghu Bitta Bittiya Sanstha Limited | 1 / 3 | NSLB (0.83) | absent from companies: a delisted or merged issuer missing from the company list (or newly listed); add the symbol after verification; the closest listed name is a different company |
| HFSL | Hulas Fin Serve Limited | 1 / 3 | GLICL (0.5) | absent from companies: a delisted or merged issuer missing from the company list (or newly listed); add the symbol after verification; the closest listed name is a different company |
| SMLBSL | Shree Manushi Laghubitta Bittiya Sanstha Limited | 2 / 4 | MLBS (0.99) | possible same issuer as MLBS (Manushi Laghubitta), symbol differs; verify before any mapping |
| (none) | Nava Kiran Laghubitta Bittiya Sanstha Limited | 1 / 3 | AVYAN (0.9) | absent from companies: a delisted or merged issuer missing from the company list (or newly listed); add the symbol after verification; the closest listed name is a different company |
| TLBSL | Trilok Laghubitta Bittiya Sanstha Limited | 1 / 3 | SWASTIK (0.9) | absent from companies: a delisted or merged issuer missing from the company list (or newly listed); add the symbol after verification; the closest listed name is a different company |
| HBDL | H & B Development Bank Ltd. | 7 / 8 | KHDBL (0.88) | absent from companies: a delisted or merged issuer missing from the company list (or newly listed); add the symbol after verification; the closest listed name is a different company |
| SOLVE | Solve Laghubitta Bittiya Sanstha Limited | 1 / 2 | RSDC (0.89) | absent from companies: a delisted or merged issuer missing from the company list (or newly listed); add the symbol after verification; the closest listed name is a different company |
| KIST | KIST Bank Limited | 12 / 12 | KEF (0.7) | none as a match: merged into PRVU in 2014-08 (symbol_history), a successor and not the same issuer |
| ASLBSL | Arthik Samriddhi Laghubitta Bittiya Sanstha Limited | 1 / 1 | NSLB (0.84) | absent from companies: a delisted or merged issuer missing from the company list (or newly listed); add the symbol after verification; the closest listed name is a different company |
| NSDL | Nabil Stock Dealer Limited | 2 / 2 | NEF (0.61) | none: Nabil Stock Dealer / Nabil Securities, NABIL subsidiaries, not listed |
| NEPS | Nepal Elcetronic Payment Systems Limited | 2 / 2 | NDB (0.56) | none: Nepal Electronic Payment Systems, not listed |
| NEPSE | Nepal Stock Exchange Limited | 1 / 1 | UICPO (0.3) | none: an exchange notice about OTC trading, not a company report |

**In short:**
- 8 symbols (RBB, MALIC, ORIENTAL, NICLL, NCBL, NSDL, NEPS, NEPSE) are entities that are not listed equities: a state bank, a co-operative bank, foreign insurance branches, subsidiaries, a payment company and the exchange itself. They should stay unmatched.
- 4 are predecessors of today's companies by merger (KIST, SHALIGRAM, MLIL, probably GHARELU). They must not be mapped to the successor.
- The rest are delisted, merged or newly listed issuers missing from `companies`. Their symbols should be added to `companies` from an authoritative NEPSE list, not guessed.
- 1 is a possible same-issuer case (SMLBSL vs MLBS) that needs a manual check.
