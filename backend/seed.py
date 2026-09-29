"""Creates the grading user (idempotent). Run: python seed.py"""
import main

with main.get_conn() as conn:
    main.init_db()
    exists = conn.execute("SELECT 1 FROM users WHERE username = %s", ("NYUgrader",)).fetchone()
    if exists:
        print("NYUgrader already exists")
    else:
        conn.execute(
            "INSERT INTO users (username, email, password_hash) VALUES (%s, %s, %s)",
            ("NYUgrader", "nyugrader@example.com", main.ph.hash("Courant2026!")),
        )
        print("Created NYUgrader")
