# 11: Next.js frontend — Dashboard, Plan Review, Live Progress, Results

**What to build:** A four-page Next.js web application that provides the complete HITL user interface. Connected to the FastAPI backend via REST and WebSocket. No authentication, no user management. Manually tested for v1.

**Blocked by:** 10 (FastAPI API)

**Status:** complete

- [x] **Dashboard** (`/`): lists all Migration Jobs with status badges (scanning, awaiting approval, migrating, complete, failed). Each job row shows the target library, submission time, and a link to its detail page. Auto-refreshes or uses polling
- [x] **Plan Review** (`/jobs/{id}/plan`): displays the Migration Plan as a table with columns: file path, risk level (color-coded LOW/MEDIUM/HIGH), number of rewrites, matched rules. Each row has a checkbox (pre-checked). An "Approve" button sends the selected files to `POST /api/jobs/{id}/approve`. Shows a clear empty state or loading state when the plan isn't ready yet
- [x] **Live Progress** (`/jobs/{id}/progress`): connects to `WS /api/jobs/{id}/stream` and displays a real-time log of events — which file is being processed, test pass/fail, healing attempt count, completion. Auto-scrolling log viewer
- [x] **Results** (`/jobs/{id}/results`): for each file, shows a Monaco Editor split-diff view (original vs. rewritten) with syntax highlighting. Files marked FAILED are highlighted in red with their traceback shown. Summary stats (total files, succeeded, failed). Copy-pasteable git commands section. Optional "Create PR" button for GitHub-sourced repos
- [x] **Navigation between pages is consistent (breadcrumbs or sidebar)**
- [x] **Responsive layout that works on standard desktop screen sizes**
