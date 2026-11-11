# Technical design

The complete user-facing design and evidence are in [the plan](../../../plan/continuous-generation-preview.md), sections 2–6.

Use a front-end preview group containing ordered immutable submission identities and per-job state. Reserve normalized n slots synchronously before POST; attach responses by clientSubmissionId and events by jobId. Derive stable slots and aggregate pending work. Preserve selected image identity and its owning job metadata.

Replace singleton tracked-job resources with per-job controllers behind jobsStore; retain one global jobs feed. Preserve epoch/source guards, unique polling tokens, visibility handling and existing polling intervals. Clear/navigation invalidates group epochs before disposing resources. A late accepted POST stays a backend job but cannot attach to a replacement group. Submission acceptance is separate from list refresh success.

Reuse the current image and streaming components. Once a final image exists, use the main-image view and pending/completed/failed/cancelled thumbnails. Keep edit and history entry points compatible. No server protocol or storage migration is required.

Risk: multiple per-job SSE connections may encounter deployment connection limits. Validate concurrent event delivery during implementation; investigate multiplexing only if evidence requires it. Roll back the UI/store/tracker changes together.
