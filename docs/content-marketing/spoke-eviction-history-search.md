---
title: "NYC Eviction History Search by Address (Buyer's Guide)"
slug: /nyc-eviction-history-search
breadcrumb: "Eviction History Search"
meta_description: "Search any NYC building's executed-eviction history by address. What marshal eviction records reveal about tenant risk — and the filings they don't show."
primary_keyword: nyc building eviction history search
secondary_keywords:
  - nyc eviction records by address
  - marshal evictions nyc
  - how to check evictions before buying nyc
  - nyc building tenant distress signal
  - executed evictions vs eviction filings nyc
author: NYC Property Intel
date: 2026-06-06
---

# NYC Building Eviction History Search by Address (the Investor Distress Signal)

Before you buy an occupied NYC building, its eviction history tells you something the rent roll won't: how often the relationship between this building and its tenants has broken down all the way to a marshal at the door. You can search it by address in NYC's public records — and reading it correctly is one of the cheapest, fastest distress checks in pre-offer due diligence. This page is one check in the complete [NYC property due-diligence checklist](/nyc-property-due-diligence); here we go deep on evictions specifically: what the data is, how to search it, how to read it, and — just as important — what it cannot tell you.

> ### TL;DR / Key Takeaways
> - **NYC's public eviction data covers *executed* evictions** — cases where a City Marshal actually removed the occupant — citywide from **2017 to the present** (NYC Open Data dataset `6z8x-wfk4`, ~126K records).
> - **Executed evictions are a floor, not the full picture.** Cases that settled, were dismissed, or where the tenant left before the marshal arrived never appear. For the complete case record you need the housing-court (OCA) docket.
> - **For a buyer, a cluster of executed evictions is a distress signal** — it can flag turnover-driven cash-flow risk, distressed management, or an owner who has been pushing tenants out (which carries its own rent-stabilization and litigation risk).
> - **Search by address or BBL.** NYC Property Intel's eviction-history check returns the residential/commercial split, the units affected, and the executed dates in one query.
> - **It is a signal, not a verdict.** Eviction history is not a title search, not a current-tenant status report, and not legal advice — verify the live docket and talk to counsel before you act on it.

*Data source & last updated: NYC Open Data "Evictions" (`6z8x-wfk4`), City Marshal executions reported by the NYC Department of Investigation, 2017–present, updated regularly.*

---

## Quick Answer: What a NYC Eviction History Search Shows

**A NYC eviction history search returns the *executed* residential and commercial evictions recorded at an address — cases where a City Marshal physically removed the occupant — citywide from 2017 to the present.** It is sourced from NYC Open Data's "Evictions" dataset (`6z8x-wfk4`), compiled from City Marshal filings by the Department of Investigation. Each record carries the eviction address, apartment number, executed date, marshal name, borough, ZIP, and a residential-or-commercial flag.

What it is **not**: a list of every eviction *case* ever filed against the building. Most landlord-tenant disputes never reach a marshal execution — they settle, get dismissed, or end with the tenant leaving first. So an eviction-history search shows you the conflicts that went all the way, not every conflict that happened.

---

## Why Eviction History Is a Pre-Offer Distress Signal

**For a buyer, a building's executed-eviction history is a leading indicator of cash-flow and management risk — read it *before* you make an offer, while the price is still negotiable.** Tenant turnover is expensive: vacancy, legal fees, make-ready costs, and months of lost rent. A building that has needed marshals repeatedly is telling you the seller's "stable, fully-occupied" narrative may be hiding a churn problem you'd inherit.

It cuts more than one way, and the direction matters:

- **Many residential evictions in a rent-stabilized building** can signal an owner working to clear regulated tenants — which often travels with overcharge exposure, HPD complaints, and housing-court litigation you'd step into. (See the pillar's [rent-stabilization and overcharge section](/nyc-property-due-diligence#check-3-rent-stabilization-overcharge-risk-the-pro-forma-killer).)
- **Commercial evictions** at a mixed-use property flag tenant instability in the income you may be underwriting at a premium.
- **Zero executed evictions** is *not* automatically a clean bill of health — a building can have heavy unrecorded conflict (filings, harassment complaints, buyouts) that never reached a marshal.

> ### 🔎 Run a free eviction scan on any NYC address
> See the executed-eviction count, the residential/commercial split, and the units affected — across the full 2017–present record — in one query.
>
> **[→ Scan an address free](#scan)**

---

## Executed Evictions vs. Housing-Court Filings: The Distinction That Changes the Read

**This is the single most important thing to understand about NYC eviction data: the public "Evictions" dataset records *executed* evictions (a marshal carried out a warrant), while the much larger universe of eviction *filings* lives in the state housing-court system (OCA), which is a separate record.** Treating the two as the same number is the most common mistake — and it cuts both ways.

- An **executed eviction** means the case ran its full course: a judgment, a warrant of eviction, and a marshal who showed up and removed the occupant.
- An **eviction filing** is the start of a case. Most filings never end in an execution — the parties settle, the case is dismissed, ERAP or a hardship stay intervenes, or the tenant moves out first.

So the executed-eviction count is a **floor** on a building's tenant conflict, not a ceiling. A building showing two executed evictions may have had twenty filings behind them. If you need the full case history — every filing, the current docket, who's still in active litigation — that comes from the [NYS Unified Court System](https://www.nycourts.gov/courts/nyc/housing/) (OCA) records and, ultimately, your attorney pulling the docket. The public eviction dataset gives you the high-signal floor fast; it does not replace the docket.

This honesty is the point. We show you the executed record and tell you exactly where it stops — because a number you misread is worse than no number.

---

## How to Search NYC Eviction History by Address (Manual Method)

You can do this yourself for free on NYC Open Data. Here's the manual path, then the one-query shortcut.

1. **Open the [NYC Open Data "Evictions" dataset](https://data.cityofnewyork.us/City-Government/Evictions/6z8x-wfk4/data)** (`6z8x-wfk4`).
2. **Filter by `EVICTION_ADDRESS`.** Match the building's street address. Note: addresses are free-text and not normalized to a BBL, so spelling and house-number variants ("123 Main St" vs "123 MAIN STREET") can split or miss records — try more than one form.
3. **Check `RESIDENTIAL_COMMERCIAL_IND`** to separate residential from commercial executions.
4. **Read `EXECUTED_DATE`** for recency and clustering, and `EVICTION_APT_NUM` to see how many distinct units are involved.
5. **Repeat for address variants** and, for a corner or multi-entrance building, alternate street addresses.

*Or skip the address-string guesswork: NYC Property Intel's eviction-history check queries by canonical **BBL** (exact, indexed) or address, returns the residential/commercial split and units affected, and stamps the data vintage — in [one query](#scan).*

The advantage of the BBL path is precision: free-text address matching on the open dataset is approximate, so a manual search can both miss real records and surface a neighbor's. Resolving the address to its BBL first removes that ambiguity.

---

## How to Read the Results: Patterns That Matter

**Don't just count evictions — read the pattern.** The same total means very different things depending on the building's size, the recency, and whether the same unit recurs. A few signals worth weighing:

- **Volume relative to unit count.** Three executed evictions in a 6-unit building is a very different story than three in a 200-unit building.
- **Recency and clustering.** A burst of executions in the last 12–24 months points to an active situation; scattered events a decade apart may be ordinary churn.
- **The same unit repeating.** Repeat executions at one apartment can flag a chronically contested unit — often a rent-stabilized tenancy the owner has tried to clear.
- **Residential vs. commercial mix.** Commercial executions speak to the durability of any commercial income; residential executions speak to occupancy and regulatory risk.

For example: say a 6-unit building shows four executed residential evictions in the last 18 months, three of them at the same apartment. That pattern — high volume for the building's size, recent, and concentrated on one unit — is worth raising with the seller and checking against the live docket; it's exactly the kind of churn a rent roll won't show. (Illustrative, not a real building.)

None of these is a verdict. They are questions to bring to the seller, the live docket, and your attorney — not conclusions to price on blindly.

---

## What an Eviction-History Search Cannot Tell You (Read This)

We earn trust by being explicit about the limits. An eviction-history search does **not**:

- **Show eviction *filings* or the live docket.** It records executed evictions only (2017–present). Filings, dismissals, settlements, and pending cases live in the OCA/court record, not here.
- **Reveal current tenant status.** It cannot tell you who lives there now, who is paying, or anyone's ERAP/hardship posture.
- **Cover pre-2017 history.** The dataset begins in 2017; older executions aren't included.
- **Guarantee a perfect address match.** Open-data addresses are free text; matching is approximate unless you query by BBL.
- **Replace a title search, a DHCR rent history, or a physical inspection** — and it is **not legal advice**. It's a public-record distress signal you verify before you act.

> **This is a distress signal, not a clean bill of health. Verify the live docket and talk to counsel before you wire money.**

---

## FAQ: NYC Eviction History Search

### How do I look up a building's eviction history in NYC?

Search NYC Open Data's "Evictions" dataset (`6z8x-wfk4`) by the building's `EVICTION_ADDRESS`, or query by BBL for an exact match. The records show City Marshal executed evictions — residential and commercial — citywide from 2017 to the present, with executed date, apartment number, borough, and ZIP. NYC Property Intel runs this lookup by address or BBL and returns the residential/commercial split and the units affected in one query.

### Does a NYC eviction search show eviction filings or only executed evictions?

Only executed evictions — cases where a City Marshal actually removed the occupant. The public "Evictions" dataset does not include eviction *filings*, settlements, or dismissals; most filed cases never reach an execution. For the full case history and the live docket you need the New York State court (OCA) record and, typically, your attorney. Treat the executed-eviction count as a floor on a building's tenant conflict, not the complete picture.

### How far back does NYC eviction data go?

NYC's public "Evictions" dataset covers City Marshal executions from 2017 to the present and is updated regularly. Evictions executed before 2017 are not in the dataset, so a building with a long pre-2017 history may show fewer records than its full timeline would suggest.

### Is a high eviction count a reason not to buy a NYC building?

Not by itself. A cluster of executed evictions is a distress signal worth investigating — it can flag turnover-driven cash-flow risk, distressed management, or an owner clearing rent-stabilized tenants (which carries overcharge and litigation exposure). But the right response is to ask the seller, pull the live docket, and consult counsel — not to assume the worst or, conversely, to treat a zero count as a guarantee of stability.

### Can I check evictions before I make an offer?

Yes — that's the point. Eviction history is public, so you can read it during pre-offer due diligence while the price is still negotiable, rather than discovering a churn problem after you've closed. Run it alongside the rest of the [pre-offer due-diligence checklist](/nyc-property-due-diligence) — violations, liens, and rent-stabilization signals — to see the full risk picture before you bid.

### What does an eviction-history search NOT replace?

It does not replace a title search, an official DHCR rent-history request, a physical inspection, or the live housing-court docket — and it is not legal advice. It surfaces the executed-eviction record from public data as one distress signal among many; you verify the current docket and the building's condition, and your attorney closes it out.

---

*Data sources & last updated: NYC Open Data "Evictions" (`6z8x-wfk4`), City Marshal executions reported by the NYC Department of Investigation, 2017–present, updated regularly. NYC Property Intel surfaces and flags public records; it is not a title search, an appraisal, a housing-court docket, or legal advice. Verify before you wire money.*

```json
{
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "Article",
      "headline": "NYC Building Eviction History Search by Address (the Investor Distress Signal)",
      "description": "How to search a NYC building's executed-eviction history by address before you buy: what the marshal-eviction record shows, how to read it, and the housing-court filings it does not include.",
      "author": { "@type": "Organization", "name": "NYC Property Intel" },
      "publisher": { "@type": "Organization", "name": "NYC Property Intel" },
      "datePublished": "2026-06-06",
      "dateModified": "2026-06-06",
      "mainEntityOfPage": { "@type": "WebPage", "@id": "https://nycpropertyintel.com/nyc-eviction-history-search" },
      "keywords": "nyc building eviction history search, nyc eviction records by address, marshal evictions nyc, how to check evictions before buying nyc, executed evictions vs eviction filings nyc"
    },
    {
      "@type": "HowTo",
      "name": "How to Search NYC Eviction History by Address",
      "description": "Search a NYC building's executed-eviction history using NYC Open Data's Evictions dataset.",
      "step": [
        { "@type": "HowToStep", "position": 1, "name": "Open the Evictions dataset", "text": "Open NYC Open Data's Evictions dataset (6z8x-wfk4) at data.cityofnewyork.us." },
        { "@type": "HowToStep", "position": 2, "name": "Filter by address", "text": "Filter by EVICTION_ADDRESS, trying more than one spelling and house-number variant because addresses are free text and not normalized to a BBL." },
        { "@type": "HowToStep", "position": 3, "name": "Separate residential and commercial", "text": "Use RESIDENTIAL_COMMERCIAL_IND to separate residential executions from commercial ones." },
        { "@type": "HowToStep", "position": 4, "name": "Read dates and units", "text": "Read EXECUTED_DATE for recency and clustering and EVICTION_APT_NUM for how many distinct units are involved." },
        { "@type": "HowToStep", "position": 5, "name": "Check address variants", "text": "Repeat for alternate address forms and, for multi-entrance buildings, alternate street addresses." }
      ]
    },
    {
      "@type": "FAQPage",
      "mainEntity": [
        {
          "@type": "Question",
          "name": "How do I look up a building's eviction history in NYC?",
          "acceptedAnswer": {
            "@type": "Answer",
            "text": "Search NYC Open Data's Evictions dataset (6z8x-wfk4) by the building's EVICTION_ADDRESS, or query by BBL for an exact match. The records show City Marshal executed evictions, residential and commercial, citywide from 2017 to the present, with executed date, apartment number, borough, and ZIP. NYC Property Intel runs this lookup by address or BBL and returns the residential/commercial split and the units affected in one query."
          }
        },
        {
          "@type": "Question",
          "name": "Does a NYC eviction search show eviction filings or only executed evictions?",
          "acceptedAnswer": {
            "@type": "Answer",
            "text": "Only executed evictions, where a City Marshal actually removed the occupant. The public Evictions dataset does not include eviction filings, settlements, or dismissals, and most filed cases never reach an execution. For the full case history and the live docket you need the New York State court (OCA) record and typically your attorney. Treat the executed-eviction count as a floor on a building's tenant conflict, not the complete picture."
          }
        },
        {
          "@type": "Question",
          "name": "How far back does NYC eviction data go?",
          "acceptedAnswer": {
            "@type": "Answer",
            "text": "NYC's public Evictions dataset covers City Marshal executions from 2017 to the present and is updated regularly. Evictions executed before 2017 are not in the dataset, so a building with a long pre-2017 history may show fewer records than its full timeline would suggest."
          }
        },
        {
          "@type": "Question",
          "name": "Is a high eviction count a reason not to buy a NYC building?",
          "acceptedAnswer": {
            "@type": "Answer",
            "text": "Not by itself. A cluster of executed evictions is a distress signal worth investigating: it can flag turnover-driven cash-flow risk, distressed management, or an owner clearing rent-stabilized tenants, which carries overcharge and litigation exposure. The right response is to ask the seller, pull the live docket, and consult counsel, not to assume the worst or to treat a zero count as a guarantee of stability."
          }
        },
        {
          "@type": "Question",
          "name": "Can I check evictions before I make an offer?",
          "acceptedAnswer": {
            "@type": "Answer",
            "text": "Yes. Eviction history is public, so you can read it during pre-offer due diligence while the price is still negotiable, rather than discovering a churn problem after you close. Run it alongside the rest of the pre-offer due-diligence checklist, including violations, liens, and rent-stabilization signals, to see the full risk picture before you bid."
          }
        },
        {
          "@type": "Question",
          "name": "What does a NYC eviction-history search NOT replace?",
          "acceptedAnswer": {
            "@type": "Answer",
            "text": "It does not replace a title search, an official DHCR rent-history request, a physical inspection, or the live housing-court docket, and it is not legal advice. It surfaces the executed-eviction record from public data as one distress signal among many; you verify the current docket and the building's condition, and your attorney closes it out."
          }
        }
      ]
    }
  ]
}
```
