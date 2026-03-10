# Post-Mortem: API Gateway Timeout Incident

**Date**: 2025-11-15
**Severity**: SEV-2
**Duration**: 47 minutes (14:23 - 15:10 UTC)
**Incident Commander**: Alex Chen

## Executive Summary

The API gateway experienced cascading timeouts due to a connection pool exhaustion in the user authentication service. Approximately 35% of API requests failed during the incident window. Root cause was a missing connection timeout on a newly added database query.

## Timeline (UTC)

| Time | Event |
|------|-------|
| 14:23 | Monitoring alert: API error rate >10% |
| 14:25 | On-call engineer acknowledges, begins triage |
| 14:28 | Identified: auth-service returning 503s |
| 14:32 | Escalated to SEV-2, incident channel created |
| 14:35 | Root cause identified: connection pool exhaustion in auth-service |
| 14:38 | Correlated with deploy of auth-service v2.14.0 at 13:45 |
| 14:42 | Decision: rollback to v2.13.2 |
| 14:48 | Rollback deployed |
| 14:55 | Error rates returning to baseline |
| 15:10 | All-clear declared after 15-min monitoring period |

## Root Cause Analysis (5 Whys)

1. **Why did requests fail?** API gateway timed out waiting for auth-service
2. **Why did auth-service time out?** Connection pool was exhausted (100/100 connections in use)
3. **Why was the pool exhausted?** New database query in v2.14.0 held connections for 30+ seconds
4. **Why did the query take 30+ seconds?** Missing index on `user_sessions.last_active` column, full table scan
5. **Why was there no timeout?** The new query was added without a connection timeout (team convention is 5s)

## Impact

- **Users affected**: ~12,000 (35% of active users during the window)
- **Failed requests**: ~8,400 (503 errors)
- **Duration**: 47 minutes
- **Data loss**: None
- **Revenue impact**: Estimated $2,100 in failed checkout attempts

## Action Items

| Action | Owner | Due Date | Status |
|--------|-------|----------|--------|
| Add index on `user_sessions.last_active` | Sarah K. | 2025-11-17 | Done |
| Add 5s connection timeout to new query | Sarah K. | 2025-11-17 | Done |
| Add connection pool monitoring alert | Dev Ops | 2025-11-22 | Pending |
| Add query timeout linting rule to CI | Platform | 2025-11-29 | Pending |
| Update deploy checklist: verify DB query plans | Alex C. | 2025-11-22 | Pending |

## Lessons Learned

1. **What went well**: Monitoring caught the issue within 2 minutes; rollback was smooth
2. **What went poorly**: No pre-deploy check for slow queries; connection pool had no monitoring
3. **Where we got lucky**: The incident occurred during low-traffic hours
