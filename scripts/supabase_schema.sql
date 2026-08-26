"""Supabase SQL migration - Run this to set up PostgreSQL schema."""
# Copy-paste this into Supabase SQL Editor (https://supabase.com)
# Or run: psql "$DATABASE_URL" -f scripts/supabase_schema.sql

SCHEMA = """
-- Jobs table (main table with 742K+ rows)
CREATE TABLE IF NOT EXISTS jobs (
    id SERIAL PRIMARY KEY,
    title TEXT,
    company TEXT,
    location TEXT,
    description TEXT,
    salary_min REAL,
    salary_max REAL,
    job_type TEXT,
    url TEXT,
    source TEXT,
    skills TEXT,
    tags TEXT,
    company_url TEXT,
    remote INTEGER DEFAULT 0,
    date_posted TEXT,
    date_scraped TEXT,
    dedupe_key TEXT UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company);
CREATE INDEX IF NOT EXISTS idx_jobs_location ON jobs(location);
CREATE INDEX IF NOT EXISTS idx_jobs_date ON jobs(date_posted);
CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source);
CREATE INDEX IF NOT EXISTS idx_jobs_dedup ON jobs(dedupe_key);
CREATE INDEX IF NOT EXISTS idx_jobs_title ON jobs USING gin(to_tsvector('english', coalesce(title, '')));

-- Users table
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    full_name TEXT DEFAULT '',
    skills TEXT DEFAULT '',
    location_pref TEXT DEFAULT '',
    role TEXT DEFAULT 'user',
    is_active BOOLEAN DEFAULT TRUE,
    email_verified BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    last_login TIMESTAMP
);

-- Sessions
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMP DEFAULT NOW(),
    expires_at TIMESTAMP NOT NULL
);

-- Saved Jobs
CREATE TABLE IF NOT EXISTS saved_jobs (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    job_id INTEGER REFERENCES jobs(id),
    job_slug TEXT,
    notes TEXT DEFAULT '',
    priority INTEGER DEFAULT 0,
    tags TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, job_slug)
);

-- Job Applications
CREATE TABLE IF NOT EXISTS applications (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    job_id INTEGER REFERENCES jobs(id),
    job_slug TEXT,
    job_title TEXT,
    company TEXT,
    status TEXT DEFAULT 'saved',
    notes TEXT DEFAULT '',
    applied_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Job Alerts
CREATE TABLE IF NOT EXISTS job_alerts (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    name TEXT DEFAULT '',
    keywords TEXT NOT NULL,
    location TEXT DEFAULT '',
    min_salary REAL,
    frequency TEXT DEFAULT 'daily',
    is_active BOOLEAN DEFAULT TRUE,
    last_sent TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Employers
CREATE TABLE IF NOT EXISTS employers (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    company_name TEXT NOT NULL,
    company_url TEXT,
    industry TEXT,
    company_size TEXT,
    description TEXT DEFAULT '',
    logo_url TEXT,
    is_verified BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Posted Jobs (by employers)
CREATE TABLE IF NOT EXISTS posted_jobs (
    id SERIAL PRIMARY KEY,
    employer_id INTEGER REFERENCES employers(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    location TEXT DEFAULT '',
    job_type TEXT DEFAULT 'full-time',
    salary_min REAL,
    salary_max REAL,
    skills TEXT DEFAULT '',
    is_remote BOOLEAN DEFAULT FALSE,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
"""

if __name__ == "__main__":
    print(SCHEMA)
    print("\n-- Copy the above SQL and paste into Supabase SQL Editor")
