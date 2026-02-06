# VOS Authentication System - Migration Guide

## Overview

The VOS authentication system has been upgraded from environment variable-based user management to a production-grade database-backed authentication system with bcrypt password hashing.

## What Changed

### Before (Environment-Based)
- Users stored in `VALID_USERS` environment variable
- Format: `username1:password1,username2:password2`
- Plaintext passwords in docker-compose.yml
- No account security features (lockout, failed attempts tracking)

### After (Database-Based)
- Users stored in PostgreSQL `users` table
- Bcrypt password hashing (cost factor 12)
- Account security features:
  - Failed login attempt tracking
  - Account lockout after 5 failed attempts (15 minutes)
  - Last login timestamp tracking
  - Active/inactive account status
  - Admin role support
- RESTful API endpoints for user management
- User registration capability

## Database Schema

The `users` table includes:

```sql
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(255) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    full_name VARCHAR(255),
    is_active BOOLEAN DEFAULT TRUE,
    is_admin BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    last_login_at TIMESTAMPTZ,
    failed_login_attempts INTEGER DEFAULT 0,
    locked_until TIMESTAMPTZ,
    metadata JSONB DEFAULT '{}'::jsonb
);
```

## Migration Steps

### Step 1: Rebuild Database Schema

The users table is included in the main schema file. To apply it:

**Option A: Fresh Database (Recommended for Development)**
```bash
# Stop all containers
docker compose down

# Remove the database volume (WARNING: This deletes all data!)
docker volume rm vos_postgres_data

# Start containers (will recreate database with new schema)
docker compose up -d postgres

# Wait for postgres to be ready
docker compose logs -f postgres

# The schema will be applied automatically on first start
```

**Option B: Add Users Table to Existing Database**
```bash
# Connect to the running postgres container
docker exec -it vos_postgres psql -U vos_user -d vos_database

# Run the following SQL to add users table:
# (Copy the users table section from vos_sdk_schema.sql)
```

### Step 2: Run User Migration Script

The migration script will:
1. Check if users table exists
2. Migrate users from `VALID_USERS` env var (if present)
3. Create default users if database is empty (admin/user1)

```bash
# Option 1: Run migration inside api_gateway container
docker exec -it vos_api_gateway python /app/migrate_users.py

# Option 2: Run migration locally (requires DATABASE_URL env var)
cd services/api_gateway
export DATABASE_URL="postgresql://vos_user:password@localhost:5432/vos_database"
python migrate_users.py
```

**Migration Output:**
```
============================================================
VOS User Migration Script
============================================================
✅ Connected to database
✅ Users table exists
✅ Migrated user 'admin' (ID: 1, role: admin)
✅ Migrated user 'user1' (ID: 2, role: user)

📊 Migration Summary:
   - Migrated: 2 users
   - Skipped: 0 users

============================================================
✅ Migration completed successfully!
============================================================
```

### Step 3: Update Docker Compose (Already Done)

The `VALID_USERS` environment variable has been commented out in:
- `docker-compose.yml`
- `docker-compose.backend-only.yml`

You can now remove the `VALID_USERS` line from your `.env` file.

### Step 4: Restart Services

```bash
# Rebuild and restart api_gateway to install bcrypt dependency
docker compose up -d --build api_gateway

# Check logs to ensure it started successfully
docker compose logs -f api_gateway
```

### Step 5: Test Authentication

**Test Login:**
```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your_api_key" \
  -d '{"username": "admin", "password": "admin123"}'
```

**Expected Response:**
```json
{
  "access_token": "eyJ0eXAiOiJKV1QiLCJhbGc...",
  "token_type": "bearer",
  "expires_in": 86400,
  "username": "admin",
  "is_admin": true
}
```

**Test Registration:**
```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your_api_key" \
  -d '{
    "username": "newuser",
    "password": "securepassword123",
    "email": "newuser@example.com",
    "full_name": "New User"
  }'
```

## API Endpoints

### POST /api/v1/auth/login
Authenticate user and receive JWT token.

**Request:**
```json
{
  "username": "admin",
  "password": "admin123"
}
```

**Response:**
```json
{
  "access_token": "eyJ0eXAiOiJKV1QiLCJhbGc...",
  "token_type": "bearer",
  "expires_in": 86400,
  "username": "admin",
  "is_admin": true
}
```

**Error Responses:**
- `401 Unauthorized` - Invalid credentials
- `403 Forbidden` - Account inactive or locked

### POST /api/v1/auth/register
Register a new user account.

**Request:**
```json
{
  "username": "newuser",
  "password": "securepassword123",
  "email": "user@example.com",
  "full_name": "John Doe"
}
```

**Response:**
```json
{
  "id": 3,
  "username": "newuser",
  "email": "user@example.com",
  "full_name": "John Doe",
  "is_admin": false,
  "is_active": true,
  "created_at": "2025-11-12T10:30:00Z",
  "last_login_at": null
}
```

**Validation:**
- Username: 3-255 characters, must be unique
- Password: Minimum 8 characters
- Email: Valid email format, must be unique (optional)

**Error Responses:**
- `400 Bad Request` - Username/email already exists or validation failed

## Security Features

### Password Hashing
- Algorithm: bcrypt with cost factor 12
- Passwords are never stored in plaintext
- Hash format: `$2b$12$...` (60 characters)

### Account Lockout
- Threshold: 5 failed login attempts
- Lockout Duration: 15 minutes
- Counter resets on successful login
- Lockout expires automatically

### JWT Tokens
- Algorithm: HS256
- Expiry: 24 hours
- Payload includes: username (`sub`), issued at (`iat`), expiration (`exp`)
- Secret stored in `JWT_SECRET` environment variable

### Rate Limiting
Currently not implemented. Recommended for production:
- Use nginx/traefik rate limiting
- Or implement FastAPI rate limiting middleware

## Default Users

After migration, you'll have these default users:

| Username | Password | Role | Email |
|----------|----------|------|-------|
| admin | admin123 | Admin | admin@vos.local |
| user1 | password1 | User | user1@vos.local |

**⚠️ IMPORTANT:** Change these passwords in production!

## Troubleshooting

### Migration Script Fails with "Users table does not exist"
**Solution:** Run the database schema migration first:
```bash
docker exec -it vos_postgres psql -U vos_user -d vos_database -f /path/to/vos_sdk_schema.sql
```

### Login Returns 401 with Correct Credentials
**Check:**
1. User exists in database: `docker exec -it vos_postgres psql -U vos_user -d vos_database -c "SELECT * FROM users;"`
2. Account is active: Check `is_active` column
3. Account not locked: Check `locked_until` column
4. Password was hashed correctly during migration

### "Cannot connect to server" Error from Frontend
**Check:**
1. API Gateway is running: `docker compose ps api_gateway`
2. Health check passes: `curl http://localhost:8000/health`
3. CORS is configured: Check `ALLOWED_ORIGINS` in environment

### Import Error: No module named 'bcrypt'
**Solution:** Rebuild the api_gateway container:
```bash
docker compose up -d --build api_gateway
```

## Production Recommendations

### 1. Change Default Passwords
```sql
-- Connect to database
docker exec -it vos_postgres psql -U vos_user -d vos_database

-- Update admin password (use migration script to hash)
-- Or use the registration endpoint to create new admin user
```

### 2. Use Strong JWT Secret
Generate a secure random secret:
```bash
openssl rand -hex 32
```

Update in `.env`:
```bash
JWT_SECRET=your_generated_secret_here
```

### 3. Enable HTTPS
Ensure all authentication requests go over HTTPS in production.

### 4. Implement Rate Limiting
Add rate limiting middleware or use reverse proxy rate limiting.

### 5. Add Refresh Tokens
Implement refresh token mechanism for better security (shorter access token expiry).

### 6. Add Email Verification
Verify email addresses before activating accounts.

### 7. Add Password Reset Flow
Implement "forgot password" with email-based reset.

### 8. Monitor Failed Login Attempts
Set up alerts for unusual login patterns.

## Database Management

### View All Users
```sql
docker exec -it vos_postgres psql -U vos_user -d vos_database -c "SELECT id, username, email, is_admin, is_active, created_at, last_login_at FROM users;"
```

### Make User Admin
```sql
UPDATE users SET is_admin = true WHERE username = 'someuser';
```

### Deactivate User
```sql
UPDATE users SET is_active = false WHERE username = 'someuser';
```

### Reset Failed Login Attempts
```sql
UPDATE users SET failed_login_attempts = 0, locked_until = NULL WHERE username = 'someuser';
```

### Delete User
```sql
DELETE FROM users WHERE username = 'someuser';
```

## Files Changed

### New Files
- `services/api_gateway/app/routers/auth.py` - Authentication router
- `services/api_gateway/app/utils/password.py` - Password hashing utilities
- `services/api_gateway/migrate_users.py` - Migration script
- `services/api_gateway/AUTH_MIGRATION_GUIDE.md` - This file

### Modified Files
- `services/api_gateway/app/sql/vos_sdk_schema.sql` - Added users table
- `services/api_gateway/app/main.py` - Removed old login endpoint, added auth router
- `services/api_gateway/requirements.txt` - Added bcrypt dependency
- `docker-compose.yml` - Commented out VALID_USERS
- `docker-compose.backend-only.yml` - Commented out VALID_USERS

## Rollback Instructions

If you need to rollback to environment-based authentication:

1. Uncomment `VALID_USERS` in docker-compose files
2. Revert `app/main.py` to use the old login endpoint
3. Remove `auth` router import
4. Restart containers

**Note:** This is not recommended. Fix issues with the new system instead.

## Support

For issues or questions:
1. Check the troubleshooting section above
2. Review application logs: `docker compose logs api_gateway`
3. Check database state: `docker exec -it vos_postgres psql -U vos_user -d vos_database`

## Summary

The new authentication system provides:
- ✅ Production-grade security with bcrypt password hashing
- ✅ Database-backed user management
- ✅ Account lockout protection
- ✅ User registration capability
- ✅ Admin role support
- ✅ RESTful API for user management
- ✅ Backward compatible migration path

No changes required to the frontend - it continues to work seamlessly with the new backend authentication system.
