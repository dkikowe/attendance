QR attendance — Flask

Local development
  python -m venv .venv
  source .venv/bin/activate
  python -m pip install -r requirements.txt
  python app.py

Windows: activate .venv\Scripts\activate instead.
A local students.db is created automatically. An existing local database is
preserved. Student records and credentials must never be committed to Git.

QR check-in
- Teachers open /attendance, choose a course, and create a timed session.
- Students scan the QR with their phone camera and sign in or register.
- Check-in resumes after login and is accepted only for the assigned group
  during the scheduled interval. Repeated scans do not create duplicate marks.
- Teachers refresh the lesson page to see attendance and can download CSV.
- With local hosting, use the same Wi-Fi network. The QR uses a LAN IP when
  the teacher opens localhost. Set PUBLIC_BASE_URL to override the address.
- Class times use ATTENDANCE_TIMEZONE, default Asia/Almaty.

Vercel project
  Account: dkikowe / Didar's projects (didars-projects-60e4e918)
  Project: attendance
  Framework: Flask (app.py)
  Python: 3.12

Production provisioning is in progress. Persistent storage must be connected
before release: a serverless deployment cannot use a writable students.db
in the source bundle. The original student database stays on the local machine;
the cloud site starts with a separate database. Do not copy it into public Git.

Production environment variables
  SECRET_KEY                 Required, stable random session signing key
  BOOTSTRAP_ADMIN_PASSWORD   Optional initial admin account password
  BOOTSTRAP_TEACHER_PASSWORD Optional initial teacher account password
  BOOTSTRAP_STUDENT_PASSWORD Optional initial student account password
  PUBLIC_BASE_URL            Public HTTPS origin, optional override
  ATTENDANCE_TIMEZONE        School timezone; default Asia/Almaty
  GEMINI_API_KEY             Optional, only for AI assistant

Bootstrap passwords only create absent accounts. Changing the environment
variable does not reset an existing account's password. On Vercel the local
demo accounts are disabled; supplied initial passwords are hashed with scrypt.
Existing local MD5 hashes remain compatible for login. Account access details
are stored outside the repository and are not included in source or logs.

Verification
  python -m pip install -r requirements-test.txt
  python -m pytest -q
Tests use temporary databases and never write attendance into the original DB.
