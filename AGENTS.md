# Repository Guidelines

## Project Structure & Module Organization

- `backend/app/` contains the FastAPI service. Route modules live in `api/`; domain code is grouped under `agent/`, `gateway/`, `knowledge/`, `metadata/`, `reports/`, and `retrieval/`; database models and sessions live in `db/`.
- `backend/tests/` holds pytest tests, while `backend/scripts/` contains database and reliability utilities.
- `frontend-new/src/` contains the Vite/React client. Put shared UI in `components/`, feature logic in `features/`, route pages in `pages/`, API clients in `api/`, and static files in `assets/`.
- `database/` contains repeatable MySQL/PostgreSQL setup scripts. Design and acceptance evidence belongs in `docs/`.

## Build, Test, and Development Commands

Run backend commands from `backend/`:

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m uvicorn app.main:app --reload
.venv\Scripts\python -m pytest -q
```

Copy `.env.example` to `.env` before starting the API. From the repository root, initialize both databases with `backend/.venv/Scripts/python.exe database/bootstrap.py`.

Run frontend commands from `frontend-new/`:

```powershell
npm ci              # install the locked dependency set
npm run dev         # start Vite on 127.0.0.1:5173
npm test            # run Vitest once
npm run build       # create the production bundle
```

## Coding Style & Naming Conventions

Use four spaces in Python and follow existing PEP 8 conventions: `snake_case` functions/modules, `PascalCase` classes, type hints for public interfaces, and short module docstrings. React code uses two spaces, double quotes, semicolons, `PascalCase` component files, and `camelCase` helpers. Keep API access in `src/api/` instead of embedding requests in page components. No repository-wide formatter or linter is configured, so match nearby code and keep imports organized.

All frontend pages must follow the mandatory visual baseline in `docs/ui/FRONTEND_STYLE_BASELINE.md`: one bordered workspace container per page with 1px hairline dividers (never stacked rounded cards), slim toolbar bands, conclusion-and-fact-strip first, flat sections instead of nested cards, cards only as list items or chart tiles, docked side rails, the fixed type scale (10px reserved for versions/IDs), real states only, and filters synced to the URL. Reference implementations already accepted: `OverviewPage` (business dashboard), `ReportsPage`, `ReportComposerPage`.

## Testing Guidelines

Backend tests use pytest and follow `test_*.py` / `test_*` naming. Frontend tests use Vitest with jsdom and colocated `*.test.js(x)` files. Add focused tests for behavior changes, especially SQL safety, API contracts, reducers, and layout algorithms. Run both suites before requesting review; there is currently no declared coverage threshold.

## Commit & Pull Request Guidelines

Recent history mixes release labels and concise `feat:` messages. Prefer clear imperative Conventional Commit subjects, for example `fix: reject unsafe gateway queries`. Keep commits scoped to one concern. Pull requests should explain the user-visible change, list validation commands, link the relevant issue or phase document, and include screenshots for UI changes. Call out schema, environment-variable, or API-contract changes explicitly.

## AI Development Ownership

- GPT is the single implementation and review owner for the Q, M, R, V, N, E, U, and T task series. Do not delegate repository changes to another AI unless the project owner explicitly changes this rule first.
- Follow `docs/tasks/GPT_DEVELOPMENT_WORKFLOW.md` for task order, branch scope, checkpoints, review evidence, and takeover history.
- Keep one large task per branch and Draft PR. Finish backend contracts before dependent UI, PDF, or email-template integration, but keep all layers under the same owner.
- Record an explicit self-review after CI. The project owner remains the only person who decides whether to merge.

## Security & Configuration

Never commit `backend/.env`, credentials, webhook secrets, or local database dumps. Preserve the business database's read-only access model, and route environment-specific values through `app/config.py` and `.env`.
