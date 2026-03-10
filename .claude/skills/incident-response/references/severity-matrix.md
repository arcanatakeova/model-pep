# Severity Classification Matrix

## Severity Levels

| Level | Name | Description | Response Time | Escalation |
|-------|------|-------------|---------------|------------|
| SEV-1 | Critical | Complete service outage, data loss, or security breach | Immediate | VP Engineering + On-call |
| SEV-2 | Major | Significant degradation affecting >25% of users | < 15 min | Engineering Manager + On-call |
| SEV-3 | Minor | Partial degradation affecting <25% of users | < 1 hour | On-call engineer |
| SEV-4 | Low | Cosmetic issue or minor bug with workaround | Next business day | Ticket queue |

## Classification Criteria

### SEV-1 Indicators
- All users unable to access core functionality
- Data corruption or loss confirmed
- Security breach detected
- Revenue-impacting outage
- SLA breach imminent or occurring

### SEV-2 Indicators
- Core functionality degraded but partially available
- Significant latency increase (>5x baseline)
- Error rate exceeds 10% of requests
- Key integration or dependency down

### SEV-3 Indicators
- Non-critical feature unavailable
- Intermittent errors affecting subset of users
- Performance degradation within acceptable bounds
- Workaround available

### SEV-4 Indicators
- UI/cosmetic issues
- Non-user-facing errors in logs
- Documentation inaccuracies
- Feature request misclassified as bug
