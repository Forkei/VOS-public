#!/usr/bin/env python3
"""
Database migration script to migrate users from environment variables to database.

This script:
1. Creates the users table if it doesn't exist
2. Migrates users from VALID_USERS environment variable to the database
3. Hashes passwords using bcrypt

Usage:
    python migrate_users.py

Environment variables required:
    - DATABASE_URL: PostgreSQL connection URL
    - VALID_USERS: Comma-separated list of username:password pairs (optional, for migration)
"""

import os
import sys
import logging
import secrets
import string
import psycopg2
from psycopg2.extras import RealDictCursor

# Add app directory to path to import password utilities
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.utils.password import hash_password

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_database_connection():
    """Get database connection from environment variable."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL environment variable not set")

    try:
        conn = psycopg2.connect(database_url)
        logger.info("✅ Connected to database")
        return conn
    except Exception as e:
        logger.error(f"❌ Failed to connect to database: {e}")
        raise


def check_users_table_exists(cursor):
    """Check if users table exists."""
    cursor.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables
            WHERE table_schema = 'public'
            AND table_name = 'users'
        ) as exists;
    """)
    result = cursor.fetchone()
    return result['exists'] if result else False


def migrate_env_users_to_database(conn):
    """
    Migrate users from VALID_USERS environment variable to database.

    Expected format: username1:password1,username2:password2
    """
    valid_users_str = os.getenv("VALID_USERS", "")

    if not valid_users_str:
        logger.warning("⚠️  No VALID_USERS environment variable found - skipping env migration")
        logger.info("ℹ️  You can manually create users or use the default admin/user1 accounts")
        return

    valid_users = valid_users_str.split(",")
    logger.info(f"Found {len(valid_users)} users in VALID_USERS environment variable")

    cursor = conn.cursor(cursor_factory=RealDictCursor)

    migrated_count = 0
    skipped_count = 0

    for user_credential in valid_users:
        user_credential = user_credential.strip()
        if not user_credential or ':' not in user_credential:
            logger.warning(f"⚠️  Invalid user credential format: {user_credential}")
            continue

        username, password = user_credential.split(':', 1)
        username = username.strip()
        password = password.strip()

        if not username or not password:
            logger.warning(f"⚠️  Empty username or password in: {user_credential}")
            continue

        try:
            # Check if user already exists
            cursor.execute("SELECT id, username FROM users WHERE username = %s", (username,))
            existing_user = cursor.fetchone()

            if existing_user:
                logger.info(f"⏭️  User '{username}' already exists - skipping")
                skipped_count += 1
                continue

            # Hash password
            password_hash = hash_password(password)

            # Determine if user should be admin (first user or username is 'admin')
            is_admin = (migrated_count == 0) or (username.lower() == 'admin')

            # Insert user
            cursor.execute("""
                INSERT INTO users (username, password_hash, is_admin, is_active, email, full_name)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id, username
            """, (
                username,
                password_hash,
                is_admin,
                True,
                f"{username}@vos.local",  # Default email
                username.capitalize()  # Default full name
            ))

            new_user = cursor.fetchone()
            conn.commit()

            role = "admin" if is_admin else "user"
            logger.info(f"✅ Migrated user '{new_user['username']}' (ID: {new_user['id']}, role: {role})")
            migrated_count += 1

        except Exception as e:
            conn.rollback()
            logger.error(f"❌ Failed to migrate user '{username}': {e}")
            continue

    cursor.close()

    logger.info(f"\n📊 Migration Summary:")
    logger.info(f"   - Migrated: {migrated_count} users")
    logger.info(f"   - Skipped: {skipped_count} users")


def create_default_users(conn):
    """
    Create default admin and user1 accounts if they don't exist.
    Only runs if no users exist in the database.
    """
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    # Check if any users exist
    cursor.execute("SELECT COUNT(*) as count FROM users")
    user_count = cursor.fetchone()['count']

    if user_count > 0:
        logger.info(f"ℹ️  Database already has {user_count} user(s) - skipping default user creation")
        cursor.close()
        return

    logger.info("Creating default users (admin and user1)...")

    def generate_password(length=16):
        alphabet = string.ascii_letters + string.digits
        return ''.join(secrets.choice(alphabet) for _ in range(length))

    default_users = [
        {
            'username': 'admin',
            'password': generate_password(),
            'email': 'admin@vos.local',
            'full_name': 'VOS Administrator',
            'is_admin': True
        },
        {
            'username': 'user1',
            'password': generate_password(),
            'email': 'user1@vos.local',
            'full_name': 'VOS User',
            'is_admin': False
        }
    ]

    created_count = 0

    for user_data in default_users:
        try:
            password_hash = hash_password(user_data['password'])

            cursor.execute("""
                INSERT INTO users (username, password_hash, email, full_name, is_admin, is_active)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id, username
            """, (
                user_data['username'],
                password_hash,
                user_data['email'],
                user_data['full_name'],
                user_data['is_admin'],
                True
            ))

            new_user = cursor.fetchone()
            conn.commit()

            role = "admin" if user_data['is_admin'] else "user"
            logger.info(f"✅ Created default user '{new_user['username']}' (ID: {new_user['id']}, role: {role})")
            logger.info(f"   Password: {user_data['password']}")
            created_count += 1

        except Exception as e:
            conn.rollback()
            logger.error(f"❌ Failed to create default user '{user_data['username']}': {e}")
            continue

    cursor.close()

    if created_count > 0:
        logger.info(f"\n✅ Created {created_count} default user(s)")
        logger.info("⚠️  IMPORTANT: Change default passwords in production!")


def main():
    """Main migration function."""
    logger.info("=" * 60)
    logger.info("VOS User Migration Script")
    logger.info("=" * 60)

    try:
        # Connect to database
        conn = get_database_connection()

        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            # Check if users table exists
            if not check_users_table_exists(cursor):
                logger.error("❌ Users table does not exist!")
                logger.error("Please run the database schema migration first:")
                logger.error("   docker exec vos_postgres psql -U vos_user -d vos_db -f /path/to/vos_sdk_schema.sql")
                sys.exit(1)

            logger.info("✅ Users table exists")

        # Migrate users from environment variable (if any)
        migrate_env_users_to_database(conn)

        # Create default users if database is empty
        create_default_users(conn)

        conn.close()

        logger.info("\n" + "=" * 60)
        logger.info("✅ Migration completed successfully!")
        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"\n❌ Migration failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
