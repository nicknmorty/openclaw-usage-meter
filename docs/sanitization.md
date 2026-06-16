# Sanitization And Release Model

This repository starts as a generic public baseline. It is not an export of a
private deployment.

## Product And Overlay

- **Public product:** generic collector code, examples, docs, tests, templates,
  tags, and releases.
- **Private overlay:** real contact labels, local paths, billing actuals,
  calibration notes, cron entries, private fixtures, and deployment docs.

Private overlays should be tracked in private source control and ignored by the
public repo.

## Release Rule

Private-to-public changes are reviewed promotions. Port generic changes with a
patch or cherry-pick. Do not raw-merge private history into the public repo.

Public-to-private updates may be pulled into a private deployment, then combined
with that deployment's private overlay.

## Publication Proof

Maintainers should keep publication proof privately for each public release:

- public commit/tag
- private source commit, if any
- scans and tests run
- removed or generalized private data
- reviewer and final identity-leak judgment

The proof report itself should not be committed here unless it is fully generic.
