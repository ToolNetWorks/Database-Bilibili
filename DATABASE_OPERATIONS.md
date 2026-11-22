# Dual Neon PostgreSQL - Configuration & Operations Guide

## Overview

This system implements automatic failover and safe failback between two independent Neon PostgreSQL databases (Primary + Standby) running on the Free tier (100 CU-hours/month each).

## Architecture

```
┌─────────────────┐     ┌─────────────────┐
│   Primary DB    │────▶│   Standby DB    │
│  (Read/Write)   │Sync │   (Read/Write)  │
│   Singapore     │     │   Singapore     │
└─────────────────┘     └─────────────────┘
        │                       │
        └───────────┬───────────┘
                    ▼
         ┌─────────────────┐
         │  API / Workers  │
         │  (Render Free)  │
         └─────────────────┘
```

## Environment Variables

### Required

| Variable | Description | Example |
|----------|-------------|---------|
| `DATABASE_PRIMARY_URL` | Primary Neon connection string | `postgresql://user:pass@ep-xxx.neon.tech/db?sslmode=require` |
| `DATABASE_STANDBY_URL` | Standby Neon connection string | `postgresql://user:pass@ep-yyy.neon.tech/db?sslmode=require` |

### Failover Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_FAILOVER_ENABLED` | `true` | Enable automatic failover |
| `DATABASE_FAILBACK_MODE` | `manual` | `manual` or `auto` - failback mode |
| `DATABASE_HEALTH_TIMEOUT` | `5` | Health check timeout (seconds) |
| `DATABASE_FAILURE_THRESHOLD` | `3` | Consecutive failures before failover |

### Sync Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_SYNC_ENABLED` | `true` | Enable background sync |
| `DATABASE_SYNC_INTERVAL` | `60` | Sync interval (seconds) |
| `DATABASE_SYNC_BATCH_SIZE` | `100` | Max changes per sync cycle |
| `DATABASE_MAX_REPLICATION_LAG_SECONDS` | `30` | Max lag for safe failover |

### Connection Pool

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_POOL_MIN` | `1` | Minimum pool connections |
| `DB_POOL_MAX` | `5` | Maximum pool connections |
| `DB_CONNECT_TIMEOUT` | `10` | Connection timeout (seconds) |
| `DB_COMMAND_TIMEOUT` | `30` | Query timeout (seconds) |

### Monitoring

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_HEALTH_CHECK_INTERVAL` | `30` | Health check interval (seconds) |
| `DB_ALERT_LATENCY_MS` | `5000` | Alert threshold for latency |

### Authentication

| Variable | Description |
|----------|-------------|
| `BILIBILI_API_KEY` | API key for public endpoints |
| `INTERNAL_API_KEY` | API key for internal monitoring endpoints |

## Deployment on Render

### 1. Create Two Neon Projects

1. Go to Neon Console → Create Project
2. Region: **AWS Singapore (ap-southeast-1)**
3. PostgreSQL 18
4. Plan: **Free**
5. Note connection strings for both projects

### 2. Configure Render Service

1. Connect repository to Render
2. Use `render.yaml` (already configured)
3. Add environment variables in Render dashboard:
   - `DATABASE_PRIMARY_URL` - Primary Neon connection string
   - `DATABASE_STANDBY_URL` - Standby Neon connection string
   - `BILIBILI_API_KEY` - Your API key
   - `INTERNAL_API_KEY` - Generate a secure random key

### 3. Run Initial Migrations

```bash
# Set environment variables locally
export DATABASE_PRIMARY_URL="postgresql://..."
export DATABASE_STANDBY_URL="postgresql://..."

# Run migrations on both databases
python scripts/run_migrations.py
```

## Operations

### Check Status

```bash
# Public health check
curl https://your-api.onrender.com/health

# Internal status (requires INTERNAL_API_KEY)
curl -H "X-Internal-API-Key: your-key" \
  https://your-api.onrender.com/internal/database/status

# Health for load balancer
curl -H "X-Internal-API-Key: your-key" \
  https://your-api.onrender.com/internal/database/health

# Sync status
curl -H "X-Internal-API-Key: your-key" \
  https://your-api.onrender.com/internal/database/sync-status
```

### Manual Failover

```bash
# Failover to standby
curl -X POST -H "X-Internal-API-Key: your-key" \
  https://your-api.onrender.com/internal/database/failover \
  -d '{"target": "standby", "reason": "Planned maintenance"}'

# Failback to primary (when ready)
curl -X POST -H "X-Internal-API-Key: your-key" \
  https://your-api.onrender.com/internal/database/failback
```

### Trigger Manual Sync

```bash
curl -X POST -H "X-Internal-API-Key: your-key" \
  https://your-api.onrender.com/internal/database/sync/trigger
```

### View Failover History

```bash
curl -H "X-Internal-API-Key: your-key" \
  https://your-api.onrender.com/internal/database/failover-history
```

## Monitoring & Alerts

### Key Metrics to Watch

1. **Replication Lag** - Should be < 30s for safe failover
2. **Consecutive Failures** - Triggers failover at threshold (default 3)
3. **Database Health** - healthy / degraded / unavailable
4. **CU Hours Usage** - Check Neon dashboard (not tracked precisely in-app)

### Resource Warnings

The `/internal/database/resource-warnings` endpoint returns:
- High replication lag (> 80% of threshold)
- Consecutive failures approaching threshold

### Uptime Statistics

```bash
curl -H "X-Internal-API-Key: your-key" \
  "https://your-api.onrender.com/internal/database/uptime?window_minutes=60"
```

## Failover Behavior

### Automatic Failover Triggers

1. Primary connection fails `DATABASE_FAILURE_THRESHOLD` times consecutively
2. Standby is healthy and reachable
3. Replication lag ≤ `DATABASE_MAX_REPLICATION_LAG_SECONDS`
4. Sync status is `IN_SYNC` or `LAGGING` (not `OUT_OF_SYNC`)

### Failover Safety Checks

Before failover, system verifies:
- ✅ Standby responds to health check
- ✅ Replication lag within threshold
- ✅ No unresolved sync errors
- ✅ Standby has processed recent changes

### What Happens During Failover

1. Active role switches to STANDBY
2. New connections routed to standby pool
3. Failover event logged with timestamp, reason, lag
4. Primary health checks continue in background

### Failback Process (Manual by Default)

1. Primary must be healthy for sustained period
2. Replication lag must be minimal (< 5s)
3. No sync errors or conflicts
4. Operator triggers failback via API
5. Active role switches back to PRIMARY
6. Failback event logged

## Cost Optimization (Neon Free Tier)

### CU-Hours Management

- **Pool size**: Keep `DB_POOL_MAX=5` (Neon Free allows up to 10 connections)
- **Health checks**: Every 30s (not every few seconds)
- **Sync interval**: 60s default (adjust based on RPO needs)
- **Autosuspend**: Neon auto-suspends after 5 min inactivity

### Estimated Usage

| Configuration | Est. CU-hours/month |
|---------------|---------------------|
| Pool max=5, sync=60s, health=30s | ~40-60 CU-hours |
| Pool max=2, sync=300s, health=60s | ~15-25 CU-hours |

### Tips

1. Use `DB_POOL_MIN=1`, `DB_POOL_MAX=2` for very low usage
2. Increase `DATABASE_SYNC_INTERVAL` to 300s (5 min) if RPO allows
3. Monitor Neon dashboard for actual usage
4. Set up Neon budget alerts

## Migration from Single Database

### If Primary Has Existing Data

1. **Do not delete or reset Primary data**
2. Run `pg_dump` on Primary:
   ```bash
   pg_dump -h primary-host -U user -d db --no-owner --no-privileges > backup.sql
   ```
3. Restore to Standby:
   ```bash
   psql -h standby-host -U user -d db < backup.sql
   ```
4. Verify row counts, constraints, sequences match
5. Run migrations on both databases
6. Switch application to use `DATABASE_PRIMARY_URL` and `DATABASE_STANDBY_URL`

### If Primary Is Inaccessible (CU Exhausted)

1. Wait for CU reset (monthly) or upgrade Primary
2. If urgent, promote Standby as new Primary:
   - Update `DATABASE_PRIMARY_URL` to current Standby URL
   - Create new Standby project
   - Run migrations on new Standby

## Troubleshooting

### Failover Not Triggering

- Check `DATABASE_FAILOVER_ENABLED=true`
- Verify `DATABASE_FAILURE_THRESHOLD` not too high
- Check logs for "Failover aborted" messages
- Verify standby health: `GET /internal/database/health`

### High Replication Lag

- Check sync worker running: `GET /internal/database/sync-status`
- Increase `DATABASE_SYNC_INTERVAL` if too frequent
- Check for long-running transactions on primary
- Manual sync: `POST /internal/database/sync/trigger`

### Failback Failing

- Check `GET /internal/database/sync-status` for errors
- Verify replication lag < 5s
- Check for schema differences between databases
- Review failover history for recent conflicts

### Connection Pool Exhaustion

- Increase `DB_POOL_MAX` (max 10 on Neon Free)
- Check for connection leaks in application code
- Reduce `DB_CONNECT_TIMEOUT` if slow connections

## Rollback Plan

If issues arise:

1. **Disable failover**: Set `DATABASE_FAILOVER_ENABLED=false`
2. **Force primary**: Set `DATABASE_PRIMARY_URL` only, remove standby
3. **Revert code**: Deploy previous version without dual-DB logic
4. **Data recovery**: Use `pg_dump`/`pg_restore` from healthy database

## Known Limitations

1. **RPO > 0**: Async replication means potential data loss up to `DATABASE_SYNC_INTERVAL`
2. **No native Neon replication**: Using application-level outbox pattern
3. **Manual failback default**: Prevents automatic flip-flop
4. **CU-hours estimation**: Approximate only; use Neon dashboard for accuracy
5. **Schema changes**: Must run migrations on both databases manually

## Support Commands

```bash
# Run migrations
python scripts/run_migrations.py

# Test primary connection
python -c "import psycopg; psycopg.connect(os.getenv('DATABASE_PRIMARY_URL')).execute('SELECT 1')"

# Test standby connection
python -c "import psycopg; psycopg.connect(os.getenv('DATABASE_STANDBY_URL')).execute('SELECT 1')"

# Check sync tables
psql $DATABASE_PRIMARY_URL -c "SELECT * FROM sync_status;"
psql $DATABASE_STANDBY_URL -c "SELECT * FROM sync_checkpoint;"
```