# Escalation Paths

## Communication Channels

| Severity | Primary Channel | Update Frequency |
|----------|----------------|-----------------|
| SEV-1 | Dedicated incident Slack channel + status page | Every 15 min |
| SEV-2 | Team Slack channel + status page | Every 30 min |
| SEV-3 | Team Slack channel | Every 1 hour |
| SEV-4 | Ticket comments | As resolved |

## Escalation Steps

### Initial Response (0-5 min)
1. Acknowledge the incident in the appropriate channel
2. Assign an Incident Commander (IC)
3. Create an incident tracking document

### First Update (5-15 min)
1. IC posts initial assessment: severity, blast radius, affected systems
2. Tag relevant team leads if SEV-1 or SEV-2
3. Update status page if user-facing

### Ongoing Updates
1. IC provides regular updates at the frequency above
2. Each update includes: current status, actions taken, next steps, ETA
3. Escalate severity if impact is worse than initially assessed

### Resolution
1. Confirm fix is deployed and verified
2. Post all-clear message in incident channel
3. Update status page to resolved
4. Schedule post-mortem within 48 hours

## Stakeholder Notification

| Stakeholder | When to Notify | How |
|-------------|---------------|-----|
| Engineering team | All severities | Slack |
| Product team | SEV-1, SEV-2 | Slack + email |
| Leadership | SEV-1 | Slack + email + phone if after hours |
| Customers | SEV-1, SEV-2 (if user-facing) | Status page + email |
