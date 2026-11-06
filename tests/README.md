# Tests

pytest suite of the importer (26 tests): parsing and file handling, failure email, logging, and the database write
against a local test database. The tests never touch the inbox `/nne/data`, never write to production and never send
email.

How to run them, what each file checks and needs, troubleshooting: [RUNNING_TESTS.md](RUNNING_TESTS.md).
