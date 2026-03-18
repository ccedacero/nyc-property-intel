# NYC Property Due Diligence Tool — Market Validation Research

**Date:** 2026-03-18
**Status:** VALIDATED — Strong demand signal across all channels

---

## Executive Summary

The pain point of fragmented NYC property due diligence data is real, well-documented, and already monetized by multiple partial solutions. No single tool combines ACRIS + HPD + DOB + DOF + DHCR + zoning into one AI-queryable interface for small/mid-size NYC multifamily investors.

---

## 1. The Problem (Quantified)

- **22+ separate websites** needed for full NYC property due diligence (NestApple, UNHP, ProPublica independently confirm)
- **4-6 hours per property** for manual research across all databases (RegWatch quantifies at $250-500 labor cost)
- **70% of NYC buildings** have open violations (ViolationWatch)
- **~1,200 multifamily transactions/year** in NYC, $8.9B in 2024 dollar volume (Ariel Property Advisors)
- **Open violations transfer to new owners** at closing — including all corrective obligations and financial exposure

### Databases Investors Must Check (Each is a Separate System)

| Database | Agency | Data |
|----------|--------|------|
| ACRIS | DOF | Deeds, mortgages, liens, UCC filings |
| DOB BIS | DOB | Permits, violations, complaints, C of O |
| DOB NOW | DOB | Newer permits/applications (separate from BIS!) |
| HPD Online | HPD | Housing violations (Class A/B/C), complaints, registrations |
| OATH/ECB | OATH | Environmental control board fines, hearings |
| DOF Property Tax | DOF | Tax bills, arrears, assessed values |
| DOF CityPay | DOF | Outstanding charges, lien info |
| HCR/DHCR | NYS HCR | Rent stabilization status, rent histories |
| RGB | RGB | Rent stabilized building lists |
| ZoLa | DCP | Zoning, special districts, land use |
| 311 | 311 | Complaint history |
| FEMA Flood Maps | FEMA | Flood zone designation |
| AG Offering Plans | NY AG | Condo/co-op offering plans |
| NYS Corp DB | DOS | LLC ownership info |
| Tax Lien Sale Lists | DOF | Properties with unpaid charges |

### Deal-Killing Case Studies

- **Crown Heights brownstone:** $2.1M deal collapsed after discovering 14 open violations days before closing; buyers lost $15,000 in fees
- **Flushing two-family:** $87,000 in ECB judgment liens prevented closing
- **Upper East Side co-op:** FDNY standpipe violations halted a $1.4M transaction
- **8-unit Brooklyn building:** Itkowitz due diligence report saved buyer $400,000 by discovering units claimed as free-market were still rent-stabilized (price reduced from $2.2M to $1.8M)

---

## 2. Existing Solutions and Their Gaps

### Paid Tools (Partial Solutions)

| Tool | Slice It Solves | Price | Key Limitation |
|------|----------------|-------|----------------|
| PropertyShark (Yardi) | Ownership, comps, some violations, zoning | $60-170/mo | No rent stab, no AI, no full violation coverage, no ACRIS depth |
| RegWatch | 86+ govt sources aggregated | $5-15/report, $100/mo Pro | No AI synthesis, lookup only |
| ViolationWatch | DOB + HPD + ECB + FDNY violations | $10/mo/address | Violations only — no ownership, tax, zoning |
| DOBGuard | 11 NYC databases for violations | $15-60/mo | Monitoring for existing owners, not buyer due diligence |
| Reonomy (Altus Group) | Commercial CRE ownership, debt | $299+/mo | Not NYC-deep, accuracy complaints, commercial only |
| CoStar | Commercial listings + analytics | $485+/mo | Enterprise pricing, priced out small investors |
| CompStak | Commercial lease comps | $499/mo | Lease comps only, not due diligence |
| Itkowitz (attorney) | Rent stabilization verification | $5,000+ per building | Manual, weeks-long turnaround, attorney service |

### Free/Nonprofit Tools

| Tool | Purpose | Limitation |
|------|---------|------------|
| JustFix / WhoOwnsWhat | Landlord portfolio tracker for tenants | Tenant advocacy, not investor-facing |
| NYCDB (open source) | PostgreSQL dump of NYC housing data | Raw data, requires technical skills |
| UNHP Building Indicator Project | 120+ data points on 60,000+ rentals | Academic/nonprofit, not investor workflow |
| DAP Portal | Displacement risk indicators | Community organizing, not investment |
| NYC Open Data portals | Raw datasets | 15+ separate systems, no cross-referencing |

### AI Competitors

| Tool | What It Does | Gap |
|------|-------------|-----|
| Diald AI ($3.75M raised Dec 2025) | AI due diligence + underwriting nationally | National/general, not NYC-deep on ACRIS/HPD/DOB |
| Cherre ($105M funded) | Enterprise data management + AI | $3.3T AUM customers, enterprise pricing, not SMB |
| HouseCanary | Residential AVMs, CanaryAI | Valuations, not regulatory/violation data |
| BatchData MCP | Property data via MCP/Claude | National, not NYC-specific databases |

**Key finding:** No MCP server integrates NYC-specific public databases (ACRIS, HPD, DOB, DOF, PLUTO). Clear gap.

---

## 3. Target Customer

### Primary: Small/Mid Multifamily Investors (5-50 unit portfolios)

- Evaluating 5-20 properties per month
- Currently paying PropertyShark ($60-170/mo) + manual research (4-6 hrs/property)
- Price-sensitive enough to care about a cheaper/faster alternative
- Tech-forward enough to try a new tool
- Estimated 10,000-30,000 active NYC-focused investors

### Secondary: RE Attorneys Doing Closings

- Bill $300-600/hr, spend 2+ hours on ACRIS/public records per deal
- Title searches: $250-350 per ACRIS search
- General due diligence fees: $2,000-5,000 per transaction
- A tool that pre-assembles data saves them hours per deal

### Tertiary: Property Managers / Developers

- Developers seeking distressed assets with air rights
- Property managers doing acquisition screening

---

## 4. Competitive Positioning

**What we are NOT building:**
- An appraisal tool (RPR already does this free with NAR membership)
- A CMA/comps tool (MLS already does this)
- A listings platform (StreetEasy/Zillow already do this)

**What we ARE building:**
NYC Property Due Diligence in 30 Seconds — the step BETWEEN finding a property and making an offer. Violations, liens, permits, ownership chain, rent stabilization, zoning — from one query.

**Differentiation:**
1. AI synthesis (not just data display — contextual analysis)
2. Cross-database correlation (ACRIS + HPD + DOB + DOF in one query)
3. NYC-specific depth (not a national tool with shallow NYC coverage)
4. Affordable ($50-100/mo vs $5,000 attorney reports)
5. Instant (30 seconds vs 4-6 hours manual)

---

## 5. Pricing Benchmarks

| Model | Price Point | Rationale |
|-------|-----------|-----------|
| Per-report | $25-75 | Below PropertyShark subscription, far below attorney cost |
| Monthly sub (individual) | $49-99/mo | Competitive with PropertyShark Pro |
| Monthly sub (team) | $199-299/mo | Below Reonomy, above PropertyShark |
| API access | $0.10-0.50/query, $200/mo minimum | For PropTech developers |

---

## 6. Market Size

- ~1,200-1,400 multifamily transactions/year in NYC ($8.9B-$10B volume)
- Estimated 10,000-30,000 active NYC-focused investors
- Data tool spending sweet spot: $25-100/month for small/mid investors
- PropertyShark has ~4 million registered users (not all paying)
- NYC RE attorneys: thousands of firms doing closings, at $2,000-5,000 per transaction

---

## Sources

### BiggerPockets Forums
- Is Property Shark Worth it? (biggerpockets.com/forums/12/topics/7297)
- Alternative to PropertyShark (biggerpockets.com/forums/80/topics/1018052)
- Buying property with HPD Violations (biggerpockets.com/forums/721/topics/952738)
- Advice on buying Rent Stabilized multifamily in Brooklyn (biggerpockets.com/forums/52/topics/1102496)
- NYC Rent Stabilized Multifamily - Is it worth it? (biggerpockets.com/forums/311/topics/1144976)
- How to Remove NYC DOB Violations (biggerpockets.com/forums/899/topics/1237334)
- CoStar vs Reonomy vs Crexi (biggerpockets.com/forums/92/topics/1070560)
- Is CoStar Worth the Money? (biggerpockets.com/forums/32/topics/594186)

### Industry / Professional Sources
- UNHP: Researching a Building (unhp.org/researching-a-building/)
- Itkowitz: Rent Stabilization Due Diligence (itkowitz.com/due-diligence)
- Itkowitz: $400K Savings Case Study (itkowitz.com/blog/2025/08/)
- New York Multifamily: Due Diligence Changes (newyorkmultifamily.com)
- Scarinci Hollenbeck: NYC Multi-Family Due Diligence
- NestApple: DIY Due Diligence in NYC (nestapple.com)
- ProPublica/THE CITY: How to Search Property Records

### Competitor Sources
- PropertyShark 2026 Review (CRE Daily)
- Reonomy 2026 Review (CRE Daily)
- ViolationWatch (violationwatch.nyc)
- DOBGuard (dobguard.com)
- RegWatch (regwatch.nyc)
- Diald AI $3.75M raise (BusinessWire, Dec 2025)
- Cherre $30M Series C (Commercial Observer, Sept 2024)
- BatchData MCP Server (batchdata.io/mcp-server)

### Market Data
- NYC Multifamily $8.9B in 2024 (GREA)
- NYC Multifamily Q3 2025 (Ariel Property Advisors)
- Proptech Funding Down 42% in 2023 (Bisnow)
