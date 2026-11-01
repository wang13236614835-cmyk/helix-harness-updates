# Cloud metadata synchronization

The desktop keeps its local project and account store. A cloud lead signs in through the local origin-bound bridge; cloud ownership is always derived from the authenticated cloud person, never a client actor ID.

`POST /api/helix-remote/metadata-preview` sends a bounded same-project patch to `/api/desktop-sync/metadata-preview`. The cloud returns an owner-bound token and expected whole-project revision. `metadata-apply` commits only if both the desktop source and cloud target retain their previewed revisions. Lost apply responses are reconciled through a persisted transaction journal; duplicate commits are idempotent. The metadata request limit is 2,000,000 bytes.

Allowed project fields are name, question, direction, weeks, sparse existing-task patches and research_progress. Report destinations are research-progress.json and updates/<ASCII-basename>.md/json. The delta preserves task IDs/status/progress, the ledger, credentials, source/library paths and local-team mappings. Worked or seat-linked task ownership cannot be reassigned through this route.

Cloud source deployment and project-data synchronization are separate operations. A source upgrade preserves /var/lib/helix data volumes and Caddy's existing sites. Source rollbacks retain the previous image and source archive; metadata rollback retains byte-for-byte copies of each changed file.

For large project material migration, the existing file-backed upload path remains available. Metadata updates do not invoke that path or re-upload materials.
