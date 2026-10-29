# Client CSV format

Template: [`examples/client-template.csv`](../examples/client-template.csv) (also downloadable from the
**Upload** page). One row per client project. Keep the header row exactly as in the template.

The same fields make up the **Add client manually** form, and both use the same validation and duplicate rules.

| Column | Needed | What to put | Used for |
|---|---|---|---|
| `Client Name` | **Mandatory** | Company name as it appears on its website, e.g. `Bright Smile Dental` | Search queries, reports, matching search results to the company |
| `Website URL` (or `Project URL`) | **Mandatory** | The live site or app, e.g. `https://www.brightsmile-dental.example`. Not a code repository | Crawled for features; the client's domain when there is no company email |
| `Client ID` | Recommended | Your own id for the client (CRM or accounting), e.g. `BSD-001` | Recognising the client in later uploads, even when its name or website changes |
| `Client Email` | Recommended | A **company** address, e.g. `info@brightsmile-dental.example` (personal addresses such as gmail.com are ignored for the domain) | The email's domain is the client's domain: the website that is researched |
| `Project Name` | Recommended | The project you built or maintain, e.g. `Patient Booking Portal`. When empty, the project is named after the client | Reports; duplicate detection (same client + project) |
| `Description` | Recommended | One or two sentences on what the project does | Evidence, industry and gap analysis |
| `Industry` | Recommended | e.g. `Hospitality`, `Healthcare`, `Logistics` | Competitor discovery, benchmarks, industry profile |
| `Technology` | Recommended | Stack, separated by `;`, e.g. `WordPress; PHP; MySQL` | Technology and modernisation analysis |
| `Repository URL` | Optional | GitHub or GitLab URL; several separated by `;` | Code analysis (private repositories need a connection under **Connections**) |
| `Project Status` | Optional | `Live`, `Maintenance`, `In development`, `Paused`… | Context in reports |
| `Start Date` | Optional | `YYYY-MM-DD`, `YYYY-MM` or `YYYY`, e.g. `2023-04-15` | Context in reports |
| `Existing Features` | Recommended | Features you already delivered, separated by `;`, e.g. `Online booking; contact form` | Not recommended again as gaps; cited as client records |
| `LinkedIn URL` | Optional | `https://www.linkedin.com/company/...` | Company profile link |
| `Notes` | Optional | Anything else; links in the text are picked up | Context and extra sources |

Mandatory fields are never guessed. A row without a client name or a website, or with a value in the wrong format
(an email, web address, repository or date), is flagged: you fill it in or correct it on the review screen
(it is checked again), or skip the row.

## What happens after the upload

Nothing is imported until you have reviewed the records and confirmed.

1. **Columns.** Each CSV column is matched to a field; you can change a match.
2. **Missing or invalid information.** Fill in or correct each flagged row, or skip it. Bulk actions skip every row
   with missing mandatory information, or every row that needs a correction.
3. **Duplicates within the file.**
   - *Confirmed*: identical rows, the same website, the same client ID, or the same client and project name.
   - *Potential*: the same client name, client domain or website domain; they may be separate records (for
     example, two projects of one client).
   - Every row of a group needs **Add** or **Skip**. Values that differ between the rows are highlighted; rows are
     never merged automatically.
4. **Existing clients.** Each record is compared with your clients by client ID, website, domain and name. You
   see the existing and the incoming values side by side and choose:
   - **Update**: values given in the incoming record replace the existing ones; empty incoming fields keep the
     existing value.
   - **Replace**: the existing record takes the incoming values; fields left empty are cleared. Needs an explicit
     confirmation.
   - **Merge**: for every field where the records differ, you choose which value to keep.
   - **Skip**: the existing record is not changed.
   - **Add** is offered only for a *possible* match (a similar name or a related domain), when adding creates no
     duplicate.
   - Update, Replace and Merge keep the existing client's id. When the client matches but none of its projects
     does, the incoming project is added to that client.
   - Bulk actions (Update, Replace, Merge, Skip, for the selected records or all of them) show which records they
     apply to and which need an individual decision, and ask for confirmation.
5. **Review and import.** A summary counts every category and lists what each record still needs.
   - Records awaiting a decision are never imported. You can import the ready ones first only by explicitly
     allowing a partial import.
   - Two rows that would create or change the same project, or give one client different values, must be resolved
     first.
6. **Result.** It shows what was added, updated, replaced, merged and skipped, and any failures with the reason.
   - Each record is applied on its own, so a failure never leaves a record half-written.
   - Every change is recorded in the audit log with the values before and after.
   - The upload remembers which rows were imported, so you can come back later to finish the pending ones.

## Rules that avoid broken rows

- **Put any value that contains a comma in double quotes**: `"Cabins, boat rentals and fishing trips"`.
  An unquoted comma starts a new column and shifts every value after it.
- Separate list items (technology, features, repositories) with `;`.
- Write URLs in full (`https://...`). Leave a cell empty rather than writing `N/A` or `-`.
- Save as **CSV UTF-8**. In Excel: *File → Save As → CSV UTF-8 (Comma delimited)*. In Google Sheets:
  *File → Download → Comma-separated values*. Semicolon- or tab-separated files are also detected.
- At most 5,000 rows and 10 MB per file. The same client on several rows is fine (one row per project).

Other column names are recognised too (e.g. `Company`, `Website`, `Tech Stack`, `Repo`, `Status`); the preview
shows how each column was mapped and lists columns it ignored.
