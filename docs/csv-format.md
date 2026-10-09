# Client CSV format

Template: [`examples/client-template.csv`](../examples/client-template.csv) (also downloadable from the
**Upload** page). One row per client project. Keep the header row exactly as in the template.

| Column | Needed | What to put | Used for |
|---|---|---|---|
| `Client Name` | **Required** | Company name as it appears on its website, e.g. `Bright Smile Dental` | Search queries, reports, matching search results to the company |
| `Client Email` | Strongly recommended | A **company** address, e.g. `info@brightsmile-dental.example` (personal addresses such as gmail.com are ignored for the domain) | The email's domain is the client's domain: the website that is researched |
| `Project Name` | **Required** | The project you built or maintain, e.g. `Patient Booking Portal` | Reports; duplicate detection (same client + project) |
| `Project URL` | **Required** | The live site or app, with `https://`, e.g. `https://www.brightsmile-dental.example` | Crawled for features; the client's domain when there is no company email |
| `Description` | Recommended | One or two sentences on what the project does | Evidence, industry and gap analysis |
| `Industry` | Recommended | e.g. `Hospitality`, `Healthcare`, `Logistics` | Competitor discovery, benchmarks, industry profile |
| `Technology` | Recommended | Stack, separated by `;`, e.g. `WordPress; PHP; MySQL` | Technology and modernisation analysis |
| `Repository URL` | Optional | GitHub or GitLab URL; several separated by `;` | Code analysis (private repositories need a connection under **Connections**) |
| `Project Status` | Optional | `Live`, `Maintenance`, `In development`, `Paused`… | Context in reports |
| `Start Date` | Optional | `YYYY-MM-DD`, e.g. `2023-04-15` | Context in reports |
| `Existing Features` | Recommended | Features you already delivered, separated by `;`, e.g. `Online booking; contact form` | Not recommended again as gaps; cited as client records |
| `LinkedIn URL` | Optional | `https://www.linkedin.com/company/...` | Company profile link |
| `Notes` | Optional | Anything else; links in the text are picked up | Context and extra sources |

A row is accepted as long as it has a client name, project name, project URL or company email; whatever is missing
is inferred where possible and shown as an issue in the preview. The more of the recommended columns are filled,
the better the analysis.

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
