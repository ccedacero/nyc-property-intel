# BiggerPockets NYC forum post draft

**Where:**
- Primary: https://www.biggerpockets.com/forums/721-new-york-city-real-estate-forum
- Also: https://www.biggerpockets.com/forums/574-new-york-real-estate-q-a-discussion-forum
- Brooklyn-specific: https://www.biggerpockets.com/forums/521

**Audience:** Active small-to-medium NYC investors. Many own buildings. Tight community, low tolerance for spam.

**Strategy:** Don't post a launch announcement. Find a recent question about due-diligence / violations / etc. and ANSWER IT, mentioning your tool naturally. THEN post your own thread.

## Helpful-answer post (reply to existing thread)

When someone asks "how do I check for violations on a building" or similar:

```
For NYC, the main public sources are:

  - DOB BIS (https://a810-bisweb.nyc.gov/...) — building violations + permits
  - HPD Online (https://hpdonline.nyc.gov/...) — housing complaints + violations
  - OATH/ECB (https://www1.nyc.gov/site/oath/...) — environmental + fire violations
  - ACRIS (https://a836-acris.nyc.gov/...) — recorded deeds, mortgages, liens
  - DOF NYC.gov sales rolls — recent sales records

Hitting all of those for one building takes 30–45 minutes if you know what
you're doing. I got fed up and built a free tool that queries all of them
at once via natural language: nycpropertyintel.com/chat — 3 free queries
without signup if you want to try it on your address.

Not pitching, just sharing because the question comes up a lot. The data
is the same NYC Open Data; the tool just saves the click-through.
```

## Original thread post (after you've contributed value first)

**Title:**
> Free tool I built — check 22 NYC city databases for any address in one query

**Body:**
```
Building this got out of hand and turned into a real product, so figured
I'd share with the NYC investors here.

It's an AI-powered tool that lets you ask plain-English questions about any
NYC property and pulls the answer from 22+ NYC public databases:

  - DOB violations + permits + complaints
  - HPD complaints, violations, registrations
  - ECB violations
  - ACRIS deeds, mortgages, liens
  - DOF rolling sales + annual sales + assessments
  - 311 complaints
  - FDNY fire incidents
  - NYPD complaint data by radius
  - Marshal eviction records
  - PLUTO (zoning, FAR, building class)
  - Rent stabilization registry

Try it: https://nycpropertyintel.com/chat (3 free queries, no signup)

Examples it handles:
  "Full due diligence on 123 Atlantic Ave, Brooklyn"
  "Open violations on 350 5th Ave Manhattan?"
  "Show me eviction history at this BBL"
  "What 311 complaints have been filed at 170 W 85th in last year?"
  "Comparable sales over $5M in zip 11215 last 12 months"

If it's useful, free trial is 10 queries/day for 30 days (sign up; token
emailed automatically). Self-host is free under MIT license.

Things to know:
  - It's a due diligence tool — pulls public data. Not an appraisal.
  - Data freshness varies by source: DOB/HPD violations daily, sales monthly,
    NYPD/FDNY weekly.
  - Some pre-2020 DOB job filings have a known coverage gap (Socrata API
    limitation, documented in the repo).
  - No tenant screening (Fair Housing compliant — refuses those queries).

Looking for feedback from people doing actual NYC due diligence. What
datasets am I missing? What would make this 10x more useful for you?

Happy to run a specific building in this thread if anyone wants to see
what it pulls.
```

## Tone notes

- BiggerPockets is older/more conservative than Reddit. No memes, no jargon.
- Disclose monetization plans up front — they DO ask "is this paid?" and they appreciate the honest "free trial → paid tiers eventually."
- Engage with replies for a week, not a day. BP threads have long tails.
- DO NOT use PropertyShark, Reonomy, or other paid tools as the comparison — those vendors might have employees here.

## Reply templates

**"Cool, what's it cost?"**
> Right now, free trial: 10 queries/day for 30 days. Self-host is fully free under MIT. Paid tiers for higher rate limits are in development — pricing not announced yet, but will be lower than PropertyShark.

**"Does it handle co-ops / condos correctly?"**
> Mostly yes — co-op shares aren't tracked separately in PLUTO so you'll get the master tax lot. ACRIS does record individual unit deeds. There's a documented edge case for condo billing lots in the repo.

**"What about NJ / LI / Westchester?"**
> NYC only for now. Adding NJ would mean another sync pipeline + different agency APIs. If there's demand here, definitely a candidate.

## Things to NOT do

- Don't post in all 3 BP NYC forums same day. Pick one.
- Don't post until you have at least 5 helpful comments on OTHER people's threads (BP rewards reciprocity)
- Don't compare to BiggerPockets-affiliated tools (RentRedi, Stessa) — these are partners
