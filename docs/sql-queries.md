# SQL Queries Reference — NYC Property Intel MCP Tools

All queries use asyncpg `$N` placeholder syntax. Type annotations are in comments.
Every query is designed to be used directly via `asyncpg.Pool.fetch()` or `asyncpg.Pool.fetchrow()`.

---

## Tool 1: `lookup_property`

### Primary: Property profile from materialized view

```sql
-- $1: text — 10-digit BBL (e.g., '1008350001')
SELECT
    bbl, address, borough, block, lot,
    cd, ct2010, council,
    ownername, bldgclass, landuse,
    zonedist1, zonedist2, zonedist3, zonedist4,
    overlay1, overlay2, spdist1, spdist2, ltdheight,
    numbldgs, numfloors, unitsres, unitstotal,
    lotfront, lotdepth, lotarea,
    bldgfront, bldgdepth, bldgarea,
    comarea, resarea, officearea, retailarea,
    garagearea, strgearea, factryarea, otherarea,
    yearbuilt, yearalter1, yearalter2,
    condono, is_condo,
    builtfar, residfar, commfar, facilfar, maxallwfar,
    unused_far_sqft,
    assessland, assesstot, exempttot, exemptland,
    histdist, landmark,
    latitude, longitude, postcode
FROM mv_property_profile
WHERE bbl = $1;
```

### Fallback: Direct PLUTO query (if materialized view not yet built)

```sql
-- $1: text — 10-digit BBL
SELECT
    bbl, address, borough, block, lot,
    ownername, bldgclass, landuse,
    zonedist1, zonedist2,
    overlay1, spdist1,
    numbldgs, numfloors, unitsres, unitstotal,
    lotarea, bldgarea, comarea, resarea, officearea, retailarea,
    yearbuilt, yearalter1, yearalter2,
    condono,
    builtfar, residfar, commfar, facilfar, maxallwfar,
    assessland, assesstot, exempttot,
    histdist, landmark,
    latitude, longitude, postcode
FROM pluto_latest
WHERE bbl = $1;
```

### PAD fallback: Address-to-BBL resolution (when GeoClient is unavailable)

```sql
-- $1: text  — street name (e.g., 'MAIN ST')
-- $2: int   — house number (numeric part only, e.g., 123)
-- $3: text  — borough code ('1'=MN, '2'=BX, '3'=BK, '4'=QN, '5'=SI)
--
-- NOTE: PAD lhnd/hhnd are TEXT columns. For non-Queens addresses, we cast
-- to integer for proper numeric comparison. Queens hyphenated addresses
-- (e.g., '37-10') require separate handling — see Queens variant below.
SELECT bbl
FROM pad_adr
WHERE boro = $3
  AND stname ILIKE '%' || $1 || '%'
  AND lhnd ~ '^\d+$'                          -- only numeric house numbers
  AND hhnd ~ '^\d+$'
  AND lhnd::int <= $2
  AND hhnd::int >= $2
LIMIT 1;
```

### PAD fallback: Queens hyphenated addresses

```sql
-- Queens addresses use format like '37-10' for house numbers.
-- $1: text  — street name
-- $2: text  — full hyphenated house number (e.g., '37-10')
-- $3: text  — borough code (always '4' for Queens)
SELECT bbl
FROM pad_adr
WHERE boro = $3
  AND stname ILIKE '%' || $1 || '%'
  AND lhnd <= $2
  AND hhnd >= $2
LIMIT 1;
```

---

## Tool 2: `get_property_issues`

### Summary: Violation counts from materialized view

```sql
-- $1: text — 10-digit BBL
SELECT
    bbl,
    hpd_total, hpd_class_a, hpd_class_b, hpd_class_c, hpd_open,
    hpd_most_recent,
    dob_total, dob_no_disposition, dob_has_disposition,
    dob_most_recent,
    most_recent_violation
FROM mv_violation_summary
WHERE bbl = $1;
```

### HPD violations detail

```sql
-- $1: text       — 10-digit BBL
-- $2: text|NULL  — HPD class filter ('A', 'B', 'C', or NULL for all)
-- $3: text|NULL  — status filter ('OPEN', 'CLOSE', or NULL for all)
-- $4: date|NULL  — start date filter (violations on or after this date)
-- $5: int        — LIMIT (max rows to return)
SELECT
    violationid,
    boroid, block, lot,
    class,
    inspectiondate,
    approveddate,
    currentstatus,
    violationstatus,
    novdescription,
    novissueddate,
    novtype,
    apartment,
    story,
    rentimpairing
FROM hpd_violations
WHERE bbl = $1
  AND ($2::text IS NULL OR class = $2)
  AND ($3::text IS NULL OR currentstatus = $3)
  AND ($4::date IS NULL OR inspectiondate >= $4)
ORDER BY inspectiondate DESC
LIMIT $5;
```

### DOB violations detail

```sql
-- $1: text       — 10-digit BBL
-- $2: date|NULL  — start date filter (violations on or after this date)
-- $3: int        — LIMIT
SELECT
    isndobbisviol,
    bbl,
    issuedate,
    violationtypecode,
    violationtype,
    violationcategory,
    description,
    dispositiondate,
    dispositioncomments,
    penalityapplied,
    violationnumber
FROM dob_violations
WHERE bbl = $1
  AND ($2::date IS NULL OR issuedate >= $2)
ORDER BY issuedate DESC
LIMIT $3;
```

### DOB permits (Phase C)

```sql
-- $1: text       — 10-digit BBL
-- $2: text|NULL  — job type filter ('NB', 'A1', 'A2', 'A3', 'DM', or NULL for all)
-- $3: int        — LIMIT
--
-- BUG FIX: initialcost in dob_now_jobs may be text with non-numeric values.
-- Use NULLIF + safe cast pattern instead of direct ::bigint.
SELECT * FROM (
    SELECT
        job,
        doc,
        jobtype,
        jobstatus,
        jobstatusdescrp,
        prefilingdate,
        approved,
        signoffdate,
        latestactiondate,
        buildingtype,
        existingoccupancy,
        proposedoccupancy,
        existingheight,
        proposedheight,
        CASE
            WHEN initialcost ~ '^\d+(\.\d+)?$'
            THEN initialcost::numeric
            ELSE NULL
        END AS initialcost,
        totalestfee,
        ownerfirstname,
        ownerlastname,
        ownerbusinessname,
        'BIS' AS source
    FROM dobjobs
    WHERE bbl = $1
      AND ($2::text IS NULL OR jobtype = $2)

    UNION ALL

    SELECT
        jobfilingnumber AS job,
        NULL AS doc,
        jobtype,
        filingstatus AS jobstatus,
        filingstatus AS jobstatusdescrp,
        filingdate AS prefilingdate,
        approveddate AS approved,
        NULL AS signoffdate,
        currentstatusdate AS latestactiondate,
        NULL AS buildingtype,
        NULL AS existingoccupancy,
        NULL AS proposedoccupancy,
        existingstories AS existingheight,
        proposedheight,
        CASE
            WHEN initialcost IS NOT NULL AND initialcost ~ '^\d+(\.\d+)?$'
            THEN initialcost::numeric
            ELSE NULL
        END AS initialcost,
        NULL AS totalestfee,
        ownerfirstname,
        ownerlastname,
        ownerbusinessname,
        'NOW' AS source
    FROM dob_now_jobs
    WHERE bbl = $1
      AND ($2::text IS NULL OR jobtype = $2)
) combined
ORDER BY prefilingdate DESC NULLS LAST
LIMIT $3;
```

### HPD registration (owner/agent info)

```sql
-- $1: text — borough code (1 char, from BBL[0])
-- $2: text — block (from BBL[1:6])
-- $3: text — lot (from BBL[6:10])
SELECT
    r.registrationid,
    r.buildingid,
    r.boroid,
    r.housenumber,
    r.streetname,
    r.zip,
    r.lastregistrationdate,
    r.registrationenddate,
    c.type AS contact_type,
    c.contactdescription,
    c.corporationname,
    c.firstname,
    c.lastname,
    c.businesshousenumber,
    c.businessstreetname,
    c.businesscity,
    c.businessstate,
    c.businesszip
FROM hpd_registrations r
JOIN hpd_contacts c ON r.registrationid = c.registrationid
WHERE r.boroid = $1::smallint
  AND r.block = $2
  AND r.lot = $3
ORDER BY r.lastregistrationdate DESC, c.type;
```

### HPD complaints

```sql
-- $1: text       — 10-digit BBL
-- $2: date|NULL  — start date filter
-- $3: int        — LIMIT
SELECT
    complaintid,
    bbl,
    boroughid,
    block,
    lot,
    apartment,
    receiveddate,
    closeddate,
    status,
    statusdate,
    statusid
FROM hpd_complaints
WHERE bbl = $1
  AND ($2::date IS NULL OR receiveddate >= $2)
ORDER BY receiveddate DESC
LIMIT $3;
```

### HPD litigations

```sql
-- $1: text — 10-digit BBL
SELECT
    litigationid,
    bbl,
    casetype,
    caseopendate,
    casestatus,
    penalty,
    findingofharassment,
    findingdate,
    respondent
FROM hpd_litigations
WHERE bbl = $1
ORDER BY caseopendate DESC;
```

---

## Tool 3: `get_property_history`

### Sales history with dedup (Phase B)

```sql
-- $1: text — 10-digit BBL
-- $2: int  — LIMIT
--
-- BUG FIX: DISTINCT ON (bbl, saledate, saleprice) deduplicates sales that
-- appear in both dof_sales (rolling) and dof_annual_sales (historical).
SELECT DISTINCT ON (bbl, saledate, saleprice)
    bbl,
    saledate,
    saleprice,
    address,
    neighborhood,
    buildingclassattimeofsale,
    buildingclasscategory,
    taxclassattimeofsale,
    residentialunits,
    commercialunits,
    totalunits,
    landsquarefeet,
    grosssquarefeet,
    yearbuilt,
    CASE
        WHEN saleprice IS NOT NULL AND saleprice <= 100
        THEN 'NON_ARMS_LENGTH'
        ELSE 'MARKET'
    END AS sale_type
FROM (
    SELECT bbl, saledate, saleprice, address, neighborhood,
           buildingclassattimeofsale, buildingclasscategory,
           taxclassattimeofsale, residentialunits, commercialunits,
           totalunits, landsquarefeet, grosssquarefeet, yearbuilt
    FROM dof_sales
    WHERE bbl = $1

    UNION ALL

    SELECT bbl, saledate, saleprice, address, neighborhood,
           buildingclassattimeofsale, buildingclasscategory,
           taxclassattimeofsale, residentialunits, commercialunits,
           totalunits, landsquarefeet, grosssquarefeet, yearbuilt
    FROM dof_annual_sales
    WHERE bbl = $1
) combined
ORDER BY bbl, saledate DESC, saleprice DESC
LIMIT $2;
```

### Ownership history from ACRIS (Phase C)

```sql
-- $1: text — borough code (1 char)
-- $2: text — block (5 chars, zero-padded)
-- $3: text — lot (4 chars, zero-padded)
-- $4: int  — LIMIT
--
-- BUG FIX: Whitelist specific doc type codes instead of LIKE '%DEED%'.
-- Uses LATERAL JOIN to aggregate multiple parties per side of transaction.
SELECT
    m.documentid,
    m.doctype,
    dcc.doctypedescription AS doc_type_description,
    m.docdate,
    m.docamount,
    m.recordedfiled,
    sellers.names AS seller_names,
    buyers.names  AS buyer_names
FROM acris_real_property_legals l
JOIN acris_real_property_master m
    ON l.documentid = m.documentid
JOIN acris_document_control_codes dcc
    ON m.doctype = dcc.doctype
LEFT JOIN LATERAL (
    SELECT array_agg(p.name ORDER BY p.name) AS names
    FROM acris_real_property_parties p
    WHERE p.documentid = m.documentid
      AND p.partytype = 1  -- grantor (seller)
) sellers ON true
LEFT JOIN LATERAL (
    SELECT array_agg(p.name ORDER BY p.name) AS names
    FROM acris_real_property_parties p
    WHERE p.documentid = m.documentid
      AND p.partytype = 2  -- grantee (buyer)
) buyers ON true
WHERE l.borough = $1
  AND l.block = $2::int
  AND l.lot = $3::int
  AND m.doctype IN ('DEED', 'DEDL', 'DEDC', 'RPTT', 'CTOR', 'CORRD')
ORDER BY m.docdate DESC
LIMIT $4;
```

### All ACRIS transactions with party aggregation (Phase C)

```sql
-- $1: text       — borough code
-- $2: text       — block
-- $3: text       — lot
-- $4: text|NULL  — doc class filter (e.g., 'DEED', 'MORTGAGE', 'LIEN', or NULL for all)
-- $5: date|NULL  — start date
-- $6: date|NULL  — end date
-- $7: int        — LIMIT
SELECT
    m.documentid,
    m.doctype,
    dcc.doctypedescription,
    dcc.classcodedescrip AS doc_class,
    m.docdate,
    m.docamount,
    m.recordedfiled,
    parties.party_data
FROM acris_real_property_legals l
JOIN acris_real_property_master m
    ON l.documentid = m.documentid
JOIN acris_document_control_codes dcc
    ON m.doctype = dcc.doctype
LEFT JOIN LATERAL (
    SELECT jsonb_agg(
        jsonb_build_object(
            'name', p.name,
            'party_type', CASE p.partytype
                WHEN 1 THEN 'grantor'
                WHEN 2 THEN 'grantee'
                ELSE 'other'
            END,
            'address', NULLIF(
                concat_ws(', ',
                    NULLIF(p.address1, ''),
                    NULLIF(p.city, ''),
                    NULLIF(p.state, ''),
                    NULLIF(p.zip, '')
                ), ''
            )
        )
        ORDER BY p.partytype, p.name
    ) AS party_data
    FROM acris_real_property_parties p
    WHERE p.documentid = m.documentid
) parties ON true
WHERE l.borough = $1
  AND l.block = $2::int
  AND l.lot = $3::int
  AND ($4::text IS NULL OR dcc.classcodedescrip ILIKE '%' || $4 || '%')
  AND ($5::date IS NULL OR m.docdate >= $5)
  AND ($6::date IS NULL OR m.docdate <= $6)
ORDER BY m.docdate DESC
LIMIT $7;
```

### Current ownership from materialized view (Phase C)

```sql
-- $1: text — 10-digit BBL
SELECT
    bbl,
    doctype,
    doc_type_description,
    docdate,
    docamount,
    owner_name,
    address1,
    city,
    state,
    zip,
    documentid,
    recordedfiled
FROM mv_current_ownership
WHERE bbl = $1;
```

---

## Tool 4: `get_financials`

### Tax assessment (most recent year)

```sql
-- $1: text — 10-digit BBL
SELECT
    bbl,
    boro, block, lot,
    bldgclass, owner, zoning,
    priormktlandval, priormkttotalval,
    tentmktlandval, tentmkttotalval,
    finalmktlandval, finalmkttotalval,
    curmktlandval, curmkttotalval,
    curactlandval, curacttotalval,
    curexmptotalval, curtaxbastotal,
    yrbuilt, units, grosssqft, numfloors
FROM dof_property_valuation_and_assessments
WHERE bbl = $1
ORDER BY reqfiscalyr DESC NULLS LAST
LIMIT 1;
```

### Tax assessment trend (multiple years)

```sql
-- $1: text — 10-digit BBL
-- $2: int  — number of years to look back
SELECT
    bbl,
    reqfiscalyr AS fiscal_year,
    curmktlandval, curmkttotalval,
    curactlandval, curacttotalval,
    curexmptotalval, curtaxbastotal
FROM dof_property_valuation_and_assessments
WHERE bbl = $1
ORDER BY reqfiscalyr DESC
LIMIT $2;
```

### Tax exemptions

```sql
-- $1: text — 10-digit BBL
SELECT
    e.bbl,
    e.exmpcode,
    c.description AS exemption_type,
    e.taxyear AS year,
    e.curexmptot AS exempt_amount,
    e.curexmpland AS exempt_land,
    e.percent1 AS exempt_percent,
    e.exmpstatus AS status
FROM dof_exemptions e
LEFT JOIN dof_exemption_classification_codes c
    ON e.exmpcode = c.exemptcode
WHERE e.bbl = $1
ORDER BY e.taxyear DESC, e.exmpcode;
```

### Tax liens

```sql
-- $1: text — 10-digit BBL
SELECT
    bbl,
    borough, block, lot,
    taxclasscode,
    buildingclass,
    housenumber,
    streetname,
    waterdebtonly,
    month,
    cycle
FROM dof_tax_lien_sale_list
WHERE bbl = $1;
```

### Mortgages and liens from ACRIS (Phase C)

```sql
-- $1: text — borough code
-- $2: text — block
-- $3: text — lot
-- $4: int  — LIMIT
SELECT
    m.documentid,
    m.doctype,
    dcc.doctypedescription,
    dcc.classcodedescrip AS doc_class,
    m.docdate,
    m.docamount,
    m.recordedfiled,
    lender.name AS lender_name,
    lender.address1 AS lender_address,
    borrower.name AS borrower_name
FROM acris_real_property_legals l
JOIN acris_real_property_master m
    ON l.documentid = m.documentid
JOIN acris_document_control_codes dcc
    ON m.doctype = dcc.doctype
LEFT JOIN LATERAL (
    SELECT p.name, p.address1
    FROM acris_real_property_parties p
    WHERE p.documentid = m.documentid
      AND p.partytype = 2  -- grantee = lender for mortgages
    ORDER BY p.name
    LIMIT 1
) lender ON true
LEFT JOIN LATERAL (
    SELECT p.name
    FROM acris_real_property_parties p
    WHERE p.documentid = m.documentid
      AND p.partytype = 1  -- grantor = borrower for mortgages
    ORDER BY p.name
    LIMIT 1
) borrower ON true
WHERE l.borough = $1
  AND l.block = $2::int
  AND l.lot = $3::int
  AND (
      dcc.classcodedescrip ILIKE '%MORTGAGE%'
      OR dcc.classcodedescrip ILIKE '%LIEN%'
      OR dcc.classcodedescrip ILIKE '%UCC%'
  )
ORDER BY m.docdate DESC
LIMIT $4;
```

### Rent stabilization

```sql
-- $1: text — 10-digit BBL (mapped to ucbbl)
SELECT
    ucbbl AS bbl,
    address,
    ownername,
    numbldgs,
    numfloors,
    unitsres,
    unitstotal,
    yearbuilt,
    uc2007, uc2008, uc2009, uc2010, uc2011,
    uc2012, uc2013, uc2014, uc2015, uc2016, uc2017,
    est2007, est2008, est2009, est2010, est2011,
    est2012, est2013, est2014, est2015, est2016, est2017
FROM rentstab
WHERE ucbbl = $1;
```

---

## Tool 5: `search_comps`

### Comparable sales search

```sql
-- $1: text|NULL  — reference BBL (to auto-fill zip/class if not overridden)
-- $2: text|NULL  — zip code override
-- $3: text|NULL  — building class prefix (e.g., 'A', 'B', 'R')
-- $4: int|NULL   — min gross square feet
-- $5: int        — months lookback (e.g., 12)
-- $6: int|NULL   — min sale price (default 10000 to exclude nominal sales)
-- $7: int|NULL   — max gross square feet
-- $8: int|NULL   — max sale price
-- $9: int        — LIMIT
--
-- BUG FIX: Uses make_interval(months => $5) instead of ($5 || ' months')::interval
-- which causes a type error in asyncpg (int || text is invalid).
WITH ref AS (
    SELECT postcode, bldgclass, bldgarea
    FROM pluto_latest
    WHERE bbl = $1
)
SELECT
    s.bbl,
    s.address,
    s.neighborhood,
    s.saleprice,
    s.saledate,
    s.grosssquarefeet,
    s.landsquarefeet,
    s.residentialunits,
    s.commercialunits,
    s.totalunits,
    s.buildingclassattimeofsale,
    s.buildingclasscategory,
    s.yearbuilt,
    CASE
        WHEN s.grosssquarefeet > 0
        THEN ROUND(s.saleprice::numeric / s.grosssquarefeet, 2)
        ELSE NULL
    END AS price_per_sqft
FROM dof_sales s
LEFT JOIN ref ON true
WHERE
    s.zipcode = COALESCE($2, ref.postcode)
    AND s.saleprice > COALESCE($6, 10000)
    AND s.saledate >= CURRENT_DATE - make_interval(months => $5)
    AND ($3::text IS NULL OR s.buildingclassattimeofsale LIKE $3 || '%')
    AND ($4::int IS NULL OR s.grosssquarefeet >= $4)
    AND ($7::int IS NULL OR s.grosssquarefeet <= $7)
    AND ($8::int IS NULL OR s.saleprice <= $8)
    AND s.bbl != COALESCE($1, '')
ORDER BY s.saledate DESC
LIMIT $9;
```

### Comparable sales from annual sales (extended history)

```sql
-- Same parameters as above.
-- Use this when dof_sales (rolling year) has too few results.
WITH ref AS (
    SELECT postcode, bldgclass, bldgarea
    FROM pluto_latest
    WHERE bbl = $1
)
SELECT
    s.bbl,
    s.address,
    s.neighborhood,
    s.saleprice,
    s.saledate,
    s.grosssquarefeet,
    s.landsquarefeet,
    s.residentialunits,
    s.commercialunits,
    s.totalunits,
    s.buildingclassattimeofsale,
    s.buildingclasscategory,
    s.yearbuilt,
    CASE
        WHEN s.grosssquarefeet > 0
        THEN ROUND(s.saleprice::numeric / s.grosssquarefeet, 2)
        ELSE NULL
    END AS price_per_sqft
FROM dof_annual_sales s
LEFT JOIN ref ON true
WHERE
    s.zipcode = COALESCE($2, ref.postcode)
    AND s.saleprice > COALESCE($6, 10000)
    AND s.saledate >= CURRENT_DATE - make_interval(months => $5)
    AND ($3::text IS NULL OR s.buildingclassattimeofsale LIKE $3 || '%')
    AND ($4::int IS NULL OR s.grosssquarefeet >= $4)
    AND ($7::int IS NULL OR s.grosssquarefeet <= $7)
    AND ($8::int IS NULL OR s.saleprice <= $8)
    AND s.bbl != COALESCE($1, '')
ORDER BY s.saledate DESC
LIMIT $9;
```

### Neighborhood statistics (quarterly aggregation)

```sql
-- $1: text|NULL  — zip code
-- $2: text|NULL  — neighborhood name (ILIKE match)
-- $3: text|NULL  — building class prefix
-- $4: int        — months lookback (e.g., 24)
--
-- BUG FIX: Uses make_interval(months => $4) instead of string concat.
SELECT
    DATE_TRUNC('quarter', saledate) AS quarter,
    COUNT(*) AS num_sales,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY saleprice) AS median_price,
    ROUND(AVG(saleprice)::numeric, 0) AS avg_price,
    ROUND(
        AVG(
            CASE WHEN grosssquarefeet > 0
            THEN saleprice::numeric / grosssquarefeet
            ELSE NULL END
        )::numeric, 2
    ) AS avg_price_per_sqft,
    PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY saleprice) AS q1_price,
    PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY saleprice) AS q3_price,
    MIN(saleprice) AS min_price,
    MAX(saleprice) AS max_price
FROM dof_sales
WHERE saleprice > 10000
  AND saledate >= CURRENT_DATE - make_interval(months => $4)
  AND ($1::text IS NULL OR zipcode = $1)
  AND ($2::text IS NULL OR neighborhood ILIKE '%' || $2 || '%')
  AND ($3::text IS NULL OR buildingclassattimeofsale LIKE $3 || '%')
GROUP BY DATE_TRUNC('quarter', saledate)
ORDER BY quarter DESC;
```

---

## Tool 6: `analyze_property`

This is a compound tool. It does NOT have its own SQL query. Instead, it runs
the following sub-queries concurrently via `asyncio.gather()` and synthesizes
the results in Python.

### Sub-queries executed (all receive BBL as input):

| # | Query | Source Tool | Purpose |
|---|-------|------------|---------|
| 1 | mv_property_profile lookup | `lookup_property` primary | Building details, zoning, FAR, unused air rights |
| 2 | mv_violation_summary lookup | `get_property_issues` summary | Risk profile: open violations, Class C count |
| 3 | Sales history (deduped) | `get_property_history` sales | Last sale price, price trend |
| 4 | Tax assessment (latest year) | `get_financials` assessment | Assessed vs market value, tax basis |
| 5 | Tax exemptions | `get_financials` exemptions | Active 421a, J-51, STAR, etc. |
| 6 | Tax liens | `get_financials` liens | Outstanding tax debt |
| 7 | Rent stabilization | `get_financials` rentstab | Unit count trend, deregulation risk |
| 8 | Comparable sales (12 months) | `search_comps` comps | Median price/sqft in same zip |

### Concurrency pattern:

```python
# All 8 queries run concurrently. Each is independent (no data dependencies).
# Total wall-clock time = slowest single query, not sum of all queries.
results = await asyncio.gather(
    fetch_one(PROPERTY_PROFILE_SQL, bbl),           # 1
    fetch_one(VIOLATION_SUMMARY_SQL, bbl),           # 2
    fetch_all(SALES_HISTORY_SQL, bbl, 5),            # 3
    fetch_one(TAX_ASSESSMENT_SQL, bbl),              # 4
    fetch_all(TAX_EXEMPTIONS_SQL, bbl),              # 5
    fetch_all(TAX_LIENS_SQL, bbl),                   # 6
    fetch_one(RENT_STAB_SQL, bbl),                   # 7
    fetch_all(COMPS_SQL, bbl, None, None, None, 12, None, None, None, 10),  # 8
    return_exceptions=True,
)
# Wrap in asyncio.wait_for(timeout=45) to prevent exceeding MCP timeout.
```

### Development potential calculation (done in Python, not SQL):

```
unused_far_sqft = (maxallwfar * lotarea) - bldgarea  # from mv_property_profile
air_rights_est = unused_far_sqft * median_price_per_sqft  # from comps
```

### Key observations logic (Python, not SQL):

The `analyze_property` tool generates bullet-point observations by checking:

- `hpd_class_c > 0` => "Property has {N} immediately hazardous (Class C) HPD violations"
- `hpd_open > 10` => "High open violation count ({N}) may indicate deferred maintenance"
- `saleprice <= 100` in last sale => "Last sale was non-arm's-length ($0/$1), actual market value unknown"
- `unused_far_sqft > 0` => "Development potential: {N} sq ft of unused FAR"
- Tax lien exists => "CAUTION: Property has outstanding tax liens"
- Rent-stabilized units declining year-over-year => "Rent-stabilized unit count declining (potential deregulation)"
- `exempttot > 0` => "Property has active tax exemptions totaling ${N}"
- `condono IS NOT NULL` => "This is a condominium; ownership data may differ between unit and parent lot"

---

## Notes on BBL Parsing

All tools that accept a 10-digit BBL string need to decompose it for ACRIS queries
(which use separate borough/block/lot columns):

```python
def parse_bbl(bbl: str) -> tuple[str, str, str]:
    """Parse 10-digit BBL into (borough, block, lot) components."""
    # bbl = '3012340056'
    #        ^         borough = '3'
    #         ^^^^^    block   = '01234' (or int 1234)
    #              ^^^^ lot    = '0056'  (or int 56)
    return bbl[0], bbl[1:6], bbl[6:10]
```

## Notes on BBL Validation

Before running any query, validate the BBL:

```python
import re

def validate_bbl(bbl: str) -> bool:
    """Validate 10-digit BBL format. Borough must be 1-5."""
    return bool(re.match(r'^[1-5]\d{9}$', bbl))
```

## Notes on Data Freshness

Every tool response should include a `data_as_of` field. For materialized views,
query the view's last refresh time:

```sql
-- Get last refresh time for a materialized view
SELECT
    schemaname,
    matviewname,
    last_refresh
FROM pg_stat_user_tables
WHERE relname = 'mv_property_profile';
```

If this is not available (depends on pg_stat configuration), fall back to:

```sql
-- Alternative: check when the view was last populated
SELECT
    last_autoanalyze,
    last_analyze
FROM pg_stat_user_tables
WHERE relname = 'mv_property_profile';
```
