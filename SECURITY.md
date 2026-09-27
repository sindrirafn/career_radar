# Public-release and security policy

Career Radar is designed as a private, local, single-user application. Its
source code can be shared publicly, but live job-search data and runtime
configuration must not be included in a public repository or release archive.

## Do not commit

- `.env` files, database URLs, API keys, tokens, private keys, or published
  Sheet URLs that grant access to private data.
- CSV, JSON, database, or export files containing real job listings, candidate
  assessments, applications, notes, employer correspondence, interview
  preparation, or contact information.
- `data/inbox/`, `data/raw/`, `data/market/`, `scratch/`, or private interview
  notes. These locations are ignored by default.

## Before publishing

1. Use synthetic fixtures rather than real job-search data.
2. Check the staged files with `git diff --cached --name-only`.
3. Scan the complete repository history with a secret scanner such as Gitleaks.
4. Confirm that `.env` and all private working folders remain untracked.
5. If sensitive material was ever committed, remove it from every published
   commit (or publish a new sanitized repository) before making the project
   public.

## Reporting a vulnerability

Do not open a public issue containing credentials or personal data. Contact the
repository owner privately and redact sensitive values from all reports.
