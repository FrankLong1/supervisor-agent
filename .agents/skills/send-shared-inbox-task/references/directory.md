# Shared inbox directory

| Human/workstation | Agent address and PostgreSQL `session_user` | Immutable agent ID |
| --- | --- | --- |
| Alice (`alice@`) | `alice@gravitationalventures.com` | `7a3fa6fa-2f49-42c9-bb6a-d4a9eafed720` |
| Frank (`frank@`) | `frank@gravitationalventures.com` | `fa212e75-7581-457b-a918-4ac8bc617bbc` |

Canonical routing:

- Resolve Alice to exactly `alice@gravitationalventures.com`.
- Resolve Frank to exactly `frank@gravitationalventures.com`.
- Alice may send only to Frank; Frank may send only to Alice.

These are the fixed v0 deployment outputs. No other address aliases are
accepted. The script requires the sender's `SUPERVISOR_INBOX_AGENT_ADDRESS` and
`SUPERVISOR_INBOX_AGENT_ID` to exactly match one row. A partial or stale match
fails before the supervisor or database is called.

The workstation login is human ownership metadata. PostgreSQL authenticates the
actual Google user through Cloud SQL IAM authentication. A separate keyless
migrator service account owns schema changes and is not a runtime sender. The
database derives the sender principal from `session_user` and verifies that
`SUPERVISOR_INBOX_AGENT_ID` belongs to it.

Sending creates a `TASK_PROPOSAL`. It does not mean the recipient accepted the
task. Misaddressed, self-addressed, or unknown principals must fail before any
database call.
