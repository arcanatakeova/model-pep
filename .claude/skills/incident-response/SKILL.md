---
name: incident-response
description: >
  Structured incident response and post-mortem generation.
  Use during production incidents, outages, or service degradations.
  Guides through triage, mitigation, resolution, and post-mortem documentation.
disable-model-invocation: true
---

# Incident Response Runbook

IMPORTANT: This skill is manually invoked only (`/incident-response`).
Production incidents require deliberate human initiation.

## Phase 1: Triage (first 5 minutes)

1. Classify severity using [references/severity-matrix.md](references/severity-matrix.md)
2. Identify affected systems and blast radius
3. Run health checks:

```bash
bash ${CLAUDE_SKILL_DIR}/scripts/check-status.sh
```

4. Document: time detected, reporter, initial symptoms

## Phase 2: Mitigation

1. Identify the most recent deployment or change
2. Determine if rollback is appropriate
3. If not rollback, identify the minimal fix
4. Communicate status to stakeholders per
   [references/escalation-paths.md](references/escalation-paths.md)

## Phase 3: Resolution

1. Implement and verify the fix
2. Monitor for recurrence (minimum 15 minutes)
3. Confirm all affected systems are healthy
4. Send all-clear notification

## Phase 4: Post-Mortem

Generate a post-mortem document with:
- Timeline of events (UTC timestamps)
- Root cause analysis (use 5 Whys technique)
- Impact assessment (users affected, duration, data loss)
- Action items with owners and due dates
- Lessons learned

See [examples/sample-incident.md](examples/sample-incident.md) for format.

## Output

Write the post-mortem to `docs/incidents/YYYY-MM-DD-<brief-description>.md`.
