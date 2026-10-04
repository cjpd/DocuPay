# DocuPay invoice pipeline: offline evaluation

> Written by the Evaluator agent (independent from the Builder). Sections 1 to 7 are its report on commit `416ddfd`, saved verbatim. The Builder's follow-up is in the addendum at the end and has not been re-scored by the Evaluator.

**Readiness: 58/100 (was 45 at 1ce57b8). Verdict for "ready to pilot": PARTIAL.** It is ready for a supervised pilot (shadow mode, or auto-approval only for known vendors under a low amount cap). It is not ready for unattended auto-approval. The gate now handles every negative document in the corpus correctly, and ingest no longer drops pages. But LLM accuracy is still unmeasured. A bad extraction from a new vendor still auto-approves 46% of the time. The vendor-history check catches only gross (100x) errors. One new false-approve path was introduced. Integrations and learning from corrections are still missing.

Commit evaluated: `416ddfd`. Threshold: 0.92 (the default). All results were verified independently by the evaluator.

## 1. Method

```
backend/.venv/bin/python backend/benchmark/generate.py   # deterministic, byte-stable
backend/.venv/bin/python backend/benchmark/run.py        # writes results.json, ~5 s
```

- **Corpus:** 40 labeled cases plus 7 ingest-only files, 0.8 MB. Ground truth is what the document prints.
  - 28 clean cases: 21 digital PDFs and text files, and 7 scans or images.
  - 12 negative cases (must not auto-approve): wrong totals, lines that don't sum, qty × price error, missing number, due date before invoice date, future date, duplicate, credit note, letter, quotation, purchase order.
  - Coverage: USD/EUR/GBP, decimal comma, discount/shipping/tax, VAT-inclusive pricing, 2–3 page invoices, 7 date formats.
  - The 4 real samples from `af53d92` are used for robustness only.
- **Extractor accuracy:** the offline HeuristicProvider (regex, no OCR), with exact field match after normalization.
- **Gate safety:** ground truth fed through FakeProvider (the oracle), plus 321 injected LLM-style errors (13 types).
- **Ingest:** routing, rejections and content loss.
- **Extra fairness tests by the evaluator** (scratch script, not in run.py): 2x/5x/10x scale errors, wide vendor history, cold start (no history), org name stored without a legal suffix.

## 2. Results: before (1ce57b8) and after (416ddfd)

| Metric | Before | After |
|---|---|---|
| Oracle STP (clean) | 96.4% (27/28) | **100%** (28/28) |
| Oracle false-approve (negatives) | 16.7% (2/12) | **0%** (0/12) |
| Negative false-approve at threshold 0.91 | 41.7% | **0%** (gate fails closed at any threshold) |
| Corruptions false-approve, no vendor history | 44.9% | 46.1% (148/321) |
| … arithmetic-detectable errors | 0.6% | 0.6% |
| … not arithmetic-detectable | 91.1% | 93.6% |
| Corruptions, Builder's "steady state" simulation | — | 19.9% (see §3) |
| Duplicate variants caught | 4/8 | **7/8** (abbreviation "Ind." still missed) |
| Ingest content loss | 3 files | **0** (mixed PDF, TIFF frames, EXIF rotation verified) |
| Latin-1 text | rejected | decoded |
| Heuristic micro accuracy (all / digital) | 54.4% / 65.5% | 53.6% / 66.0% |
| Heuristic STP / negative false-approve | 35.7% / 16.7% | 28.6% / **0%** |

Per error type, without vendor history (after):

| Injected error | False-approve |
|---|---|
| Wrong total, total = subtotal, dropped or misread line, missed tax or discount, model flags uncertain | 0–4% |
| Swapped day/month (ISO output) | 47% |
| Wrong currency, wrong invoice number, vendor = customer, ×100 scale, no line items | 96–100% |

Notes:

- The heuristic now fails the mixed PDF outright (no Tesseract) instead of sending it to review. This matters for offline development only.
- Ambiguous-date flagging works only on raw `dd/mm` strings. LLMs are told to return ISO dates, so for them the swapped-date rate is unchanged.

## 3. Is the steady-state simulation fair? Partly. It flatters the results.

The simulation gives every vendor 3 approved invoices, with totals within ±30% of the *true* total. It also sets the org name to the exact printed customer name. The evaluator's re-tests:

| Scenario | False-approve |
|---|---|
| ×100 scale, steady state (as simulated) | 0/28 |
| ×2, ×5 or **×10** scale (one lost decimal place), steady or wide history | **28/28** |
| Cold start: vendor's first invoice, wrong currency or ×100 | **28/28** |
| Vendor = customer, org name exact (as simulated) | 0/28 |
| Vendor = customer, org stored as "Globex", printed "Globex Corporation" | **28/28** |

Also, history includes *auto-approved* documents, so one wrong approval widens the range for that vendor permanently. The vendor checks are real, but the 19.9% figure is a best case.

## 4. Not measured offline: LLM accuracy

There are no API keys in this environment. To measure:

```
cd backend && ANTHROPIC_API_KEY=sk-... .venv/bin/python benchmark/run.py --provider anthropic --out benchmark/results_anthropic.json
# or OPENAI_API_KEY=... ... --provider openai --out benchmark/results_openai.json
```

The run reports field accuracy, STP, false-approve, wrong-but-approved, escalations and USD cost for about 40 documents. A missing key now gives a clean `PermanentProcessingError` (verified). Add 100+ labeled real invoices before the pilot.

## 5. Feature comparison

Sources: Azure docs on GitHub (MicrosoftDocs, 2024-11-30 GA invoice schema); Rossum from product pages and reviews. *(unverified)* means not checked against a primary source.

| Capability | DocuPay | Azure DI prebuilt-invoice | Rossum (Aurora) |
|---|---|---|---|
| Header fields | 17 | ~40 (adds addresses, AmountDue, IBAN/SWIFT, per-rate tax, service period) | configurable, 30–60 *(unverified)* |
| Line items | desc, qty, unit, amount | adds product code, unit, date, tax, rate | yes, plus PO matching |
| Confidence | proved by checks, fail-closed gate | model per-field scores, no validation | per-field, automation thresholds |
| Business rules | arithmetic, sign, dates, currency, duplicates, amount cap, vendor history, not-self | none | rules engine, master data, PO match, approvals |
| Review UI | yes (Next.js) | none for production | mature validation screen |
| Learning from corrections | stored, **not used** | manual retraining | continuous |
| Languages | LLM-dependent (untested) | 27 (invoice) | 270+ *(unverified)* |
| Formats | PDF ≤20 pages (text, scan or mixed), images incl. multi-frame TIFF, text | PDF/images/TIFF, 2,000 pages | PDF, images, email, e-invoice XML *(unverified)* |
| ERP / webhooks | `send_webhook` still a placeholder | none built in (Logic Apps) | SAP, Oracle, NetSuite, Dynamics, Coupa |
| Pricing | LLM tokens | ~$10 per 1,000 pages *(unverified)* | from ~$18k/yr |
| Deployment | self-hosted | cloud plus containers | SaaS *(on-prem unverified)* |

## 6. Remaining gaps, most impactful first

1. **New false-approve path (regression from the VAT-inclusive fix).** `validation.py:142` passes when the lines equal the total even when tax > 0. If the model drops the subtotal and reports the net as the total, the invoice auto-approves (19/19 cases, history or not). The previous code sent these to review. Fix: accept lines = total only when the document says tax is included, or when tax ≈ total × r/(1+r).
2. **Measure real LLM accuracy** (§4). STP and false-approve in production are unknown until then.
3. **Errors no rule catches.** Wrong invoice number 100%, swapped dates 47%, missing line items 96%. Vendor history misses ×2–×10 errors and all first invoices. Add vendor-master validation (tax ID, bank details, default currency), PO matching, a tighter range once a vendor has history, and default `is_new_vendor` and `max_amount` for the pilot.
4. **Own-name check too strict.** `vendor_not_self` (`validation.py:254`) compares exact normalized names against `(org.name,)` only, with no suffix stripping and no aliases, unlike `normalize_vendor`.
5. **Integrations and learning.** Implement webhook delivery and an ERP/CSV export. Use `CorrectionExample`s. Add payment fields (IBAN, AmountDue) and per-rate tax. Raise the 20-page limit.

## 7. Score justification

- **+13 over the last run:** the fail-closed gate, the sign check, VAT-inclusive invoices that STP, complete ingest, stronger dedupe, and a clean error for a missing key. All verified.
- **Why it stays below 70:** LLM accuracy is unmeasured; a new hole was introduced; 93.6% of errors arithmetic can't detect still auto-approve without history; no ERP integration or learning loop. The tenant-isolation commit (e203a76) was not evaluated beyond the passing test suite.

---

## Addendum: Builder follow-up after this evaluation (not re-scored)

Changes since `416ddfd`, addressing gaps 1, 3 (partly) and 4:

- **Gap 1 fixed.** Lines = total with tax > 0 proves the amounts only when the model reports `prices_include_tax` (the document says "incl. VAT"). The new `net_reported_as_total` corruption in `run.py` is caught 24/24 (0% approved). The VAT-inclusive clean case still auto-approves (oracle STP 28/28).
- **Gap 3, partly.** Vendor history uses the **median** of the last 50 approved totals (one wrong approval cannot widen the range), a **5x** range instead of 10x, and needs 3 invoices before checking amounts. A new `consistent_x10_scale` corruption is caught once a vendor has history. First invoices from a vendor are still not covered; enable `review_new_vendors` and set `auto_approve_max_amount` for the pilot.
- **Gap 4 fixed.** `vendor_not_self` compares names without legal suffixes and also checks `Organization.other_names` (trade names).
- **Fairer steady-state simulation.** History median is 0.5x to 2x off the true total (deterministic per vendor), and the org name is stored without the printed legal suffix.

- **Line items are required to auto-approve.** "Subtotal + tax = total" alone no longer approves: a model that misses the line items (or a document whose lines do not add up) passed it. Found while running the UI demo, where the offline extractor approved `neg_lines_dont_sum`. Invoices without line items go to review, with the reason shown.

Numbers from `run.py` after these changes (373 injected errors, 15 types):

| Metric | Value |
|---|---|
| Oracle STP / negative false-approve | 100% / 0% |
| Corruptions false-approve, no history (cold start) | 39.9% (149/373) |
| Corruptions false-approve, steady state (fair simulation) | 9.9% (37/373) |
| No line items extracted | 0% approved (was 96%) |
| Heuristic extractor: STP / negative false-approve | 28.6% / 0% |
| Still approved in steady state | wrong invoice number 100%, swapped day/month 47%, missed tax 4% |

Still open: real LLM accuracy (DP-21), vendor master and PO matching (DP-22), learning from corrections (DP-23), webhook and ERP delivery (DP-16).

---

## Real LLM run (OpenAI)

Command: `run.py --provider openai`. Output: `results_openai.json`. Models: the repo defaults, `gpt-5-mini` (fast) with `gpt-5` (strong) as escalation. The run used the repo defaults for `OPENAI_FAST_MODEL` and `OPENAI_STRONG_MODEL`; the models were not logged per call. Threshold 0.92.

| Metric | OpenAI | Offline heuristic |
|---|---|---|
| Micro field accuracy, all invoices | **99.0%** | 62.3% |
| Micro field accuracy, digital / scans | 98.8% / 100% | 76.7% / 0% |
| Docs with all core fields right | **37/37** | 24/37 |
| Straight-through (auto-approval) rate, clean invoices | **89.3%** (25/28) | 28.6% |
| False auto-approvals on negatives (12 bad documents) | **0%** (0/12) | 0% |
| Wrong-but-approved (a core field wrong, still approved) | **none** | n/a |
| Escalated to the strong model | 12 of 40 | 0 |
| Failed documents | 0 | |
| API cost | **not measured** (see caveats) | $0 |

Weakest fields (digital docs, 30 docs): `vendor_tax_id` 93.3%, `purchase_order` 93.3%, `invoice_number` 96.7% (core view), `due_date` 96.7% (core view), `line_items` 96.7%. All other fields were 100%. Each miss is one or two documents.

The 3 clean documents that did not auto-approve (`clean_us_shipping`, `clean_eu_slash_date`, `scan_png_us`) had correct fields. The model flagged itself as uncertain (`model_uncertain`, score about 0.94), so they went to a person. That is a safe failure.

Fraud-style and error checks (the gate fed wrong values, 429 cases). Two views:

| Corruption | No vendor history (first invoice) | With vendor history |
|---|---|---|
| `wrong_vendor_tax_id` | 100% approved | **0%** (caught by `vendor_master`) |
| `changed_bank_account` | **0%** (caught by `bank_account`) | 0% |
| `vendor_is_customer`, `wrong_currency`, `consistent_x100_scale` | 100% approved | 0% |
| `consistent_x10_scale` | 100% approved | caught (not in the history list; see JSON) |
| `wrong_invoice_number` | 100% approved | 100% approved |
| `swapped_day_month` | 47% approved | 47% approved |
| `missed_tax`, wrong total, dropped line, misread line, no line items | 0 to 4% | 0 to 4% |
| **Overall** | **41.3%** (177/429) | **8.6%** (37/429) |

Reading this:
- No bad document in the 12 negatives was auto-approved. No wrong extraction was approved.
- A vendor with no history is the weak point. A new vendor with a wrong tax ID, currency or scale is auto-approved. Vendor master data (DP-22) and the pilot settings `review_new_vendors` and `auto_approve_max_amount` close this gap.
- A wrong invoice number and a swapped day/month are never caught by any rule. Only a person or the PO/ERP match can find them.
- The OpenAI extraction itself was not wrong on the invoice number in the clean set (`invoice_number` 100% in the all-field view), so these corruptions are the gate test, not model errors.

Caveats:
- The test set is synthetic: 40 labeled documents (28 clean, 12 negative) from `generate.py`, not real customer invoices. Layouts are clean and few in number. Real accuracy will be lower.
- 28 clean documents is a small sample. 89.3% STP has a wide margin of error (roughly 72% to 98% at 95% confidence).
- Cost was reported as $0. The script had no OpenAI price table (`OPENAI_PRICING` unset), so no cost was calculated. Token counts were not saved. Cost is unknown. Set the price JSON in settings and run again to measure it.
- The 4 real sample files could not be run through OpenAI by this harness (images need local Tesseract in this path; text samples are not invoices). They are not part of the scores.
- This was one run. There was no repeat to measure run-to-run variation.
