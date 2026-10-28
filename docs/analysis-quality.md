# How the analysis stays accurate

Every tab of an analysis run is built from one structured dataset, and every significant finding is tied
to the source it came from. When the evidence is not good enough, the platform says so rather than
filling the gap. This page explains the labels you see in the tabs and the rules behind them.

## Evidence

- **Quotes are checked.** Facts extracted by the AI must come with a verbatim quote, and the quote must
  be found in the page it cites. If it is not found, the fact is dropped or marked *unverified*. It is
  never shown as fact.
- **Interpretation is labelled.** The analyst's interpretation (a feature's purpose, intended users,
  benefits, a market assessment) is stored and shown separately, marked *analysis* or *estimate*.
- **Every source is traceable.** Each source records its URL and the date it was accessed. Click
  **sources** next to any finding to see the claim, the quote and the page.
- **Snippets are not sources.** Search-result snippets are not enough for a complex claim. The market
  analysis reads the pages behind the results, and a trend that could only be seen in a snippet is
  labelled "page not read".
- **Sources are tiered.** Source quality runs from 1 (the company's own product, documentation or
  pricing page) to 5 (a search result). Market pages count as publications, tier 4, or tier 3 for
  official statistics and research bodies.

## Client tab: contacts and official profiles

Contacts and profiles are collected from the official website only: its pages, email and phone links,
address blocks and schema.org data. Links found in the CSV or by search are checked but never trusted
on their own. Facebook, Instagram, X and YouTube, personal LinkedIn profiles and share links are never
collected.

| Status | Meaning |
|---|---|
| Verified | Published on the official website. For links, the destination opened and names the company or links back to its domain. A page that merely loads is never enough. |
| Official link, not checked | Published on the official website, but the destination could not be checked (sign-in wall, robots rules, blocked access). |
| Redirected | The link leads to another site or a generic page. |
| Inaccessible | The destination did not open (not found, server error). |
| Possibly wrong account | The destination opened but does not identify this company. |
| Unverified | Not published on the official website and not confirmed. |

## Features: status and evidence grade

Each capability has a **status**:

- **Available**: evidence confirms it is offered.
- **Partially available**: offered, with documented limits or only a basic version.
- **Not publicly identified**: the evidence does not establish whether it exists. This is never treated
  as missing.
- **Confirmed unavailable**: the company's own pages explicitly say it is not offered, in a quote that
  actually negates it.

Each capability also has an **evidence grade**:

- **Documented**: stated on the company's own pages, with a verified quote.
- **Evidenced**: there is independent support. That can be:
  - a mention on a page where the company states its offering (home, product, features, pricing, docs,
    help, integrations);
  - several keywords on one page, or the same capability on two pages;
  - a code dependency, the client's own records, a product announcement, or an app-store listing.
- **Mentioned only**: one keyword on one other page. The capability stays *not publicly identified*,
  and the mention is shown as a note.

Pages that cannot describe an offering never count as evidence: blog posts, news, job adverts, legal
pages and sign-in pages. The company's own product announcements do count. A sentence that negates
availability ("not available yet", "coming soon", "no video calls") does not evidence the capability.

## Workflows

Workflows are built from the steps the company's pages document. Each step is either grounded in a
quote or labelled *inferred*, and a workflow with no documented step is not shown. The catalogue
workflows ("typical workflows for this kind of product") are a separate, inferred view.

## Pricing

- **A price must be on the page.** A plan's price is kept only when its quote is on the official
  pricing page and contains the price, or says the plan is free or contact-sales. Plans that fail this
  check are listed as not verified, with the reason.
- **Conflicts are flagged.** Plans that the price parser read at a different price are marked.
- **Quote-based plans have no price.** Custom and contact-sales plans are never given one.
- **Conversions are approximate.** Prices are converted to a monthly figure and to a base currency
  with configured rates. The published price is always kept alongside.

## Market

- **Figures need their context.** A market size, growth rate or share must contain its number in the
  quote, and records the metric, period, geography, definition and original source. Missing context
  is shown as *not stated*.
- **No figure means a qualitative assessment.** When no reliable figure is found, the assessment is
  qualitative and labelled as an estimate.

## Gaps: six checks and a verdict

A competitor offering something does not, on its own, make it a gap to build. Each gap is assessed on
six checks:

1. The competitor's capability (or the issue) is verified.
2. The client's status is established.
3. It is relevant to the client's customers.
4. It has meaningful business value.
5. It gives a competitive advantage.
6. The benefit justifies the effort.

Checks 1–3 come from the evidence. Checks 4–6 come from the deterministic scoring factors. The checks
lead to a verdict:

- **Confirmed**: the three evidence checks pass, and the value and effort checks pass. A gap that gives no
  competitive advantage (a security or accessibility fix, for example) can still be confirmed; the failed
  check is shown.
- **Potential, needs validation**: the value is there, but the evidence is uncertain. Process gaps,
  which rest on assumptions about the client's internal processes, are never higher than this.
- **Optional**: the value or the effort check fails.

A reviewer's decision closes the loop: **approving** a gap makes it confirmed ("validated by a reviewer"),
**rework** makes it potential, and **reject** removes it. A reviewer can also set a capability's status
(shown as *Reviewed*); this is the only other way a capability becomes *confirmed unavailable*.

### What the client is told

The sales summary and the outreach email state only **confirmed** gaps. **Potential** gaps are listed on
the Sales tab under *To validate on the call*, with the checks still open; they are questions to ask,
never claims. **Optional** gaps are not pitched.

## Cross-tab consistency

The quality check on the Pipeline tab verifies that the tabs agree:

- the comparison against the client's inventory and the competitor profiles;
- gaps against the client's capabilities and the competitors that offer them;
- the sales summary against the current gaps;
- the two readings of competitor prices;
- the market analysis against the client's industry.

A contradiction is reported with the stages to re-run, never silently kept.

## Settings that control depth (Configuration → Analysis)

| Setting | Default | Effect |
|---|---|---|
| Pages crawled per client site | 20 | Product, feature, docs and pricing pages are read first. |
| Pages per deep-analysed competitor | 15 | |
| Feature extraction batches | 3 | Batches of whole client pages read, one AI call each. |
| Feature extraction per competitor | 2 | The same, per deep-analysed competitor. |
| Market analysis pages | 8 | Third-party pages read; 0 turns the market research off. |

Exchange rates for the pricing comparison are set with `CIP_PRICING_FX_RATES`, `CIP_PRICING_FX_DATE`
and `CIP_PRICING_BASE_CURRENCY` (see `.env.example`).

Deeper research uses more AI calls. With the defaults, an analysis makes about 20 more calls than
before: features, workflows and pricing for the client and each of the three deep-analysed
competitors, plus the market research.
