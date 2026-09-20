# Delta spec: partner-message-ingestion

## Purpose

Captures every message posted in the configured partner Telegram groups into a persistent PostgreSQL archive in real time, with exact-once storage via `(chat_id, message_id)` dedup, bounded recovery of messages missed during downtime, and resilient handling of transient storage failures.

## ADDED Requirements

### Requirement: Partner group discovery by title

The system SHALL, at service startup, scan the user account's dialogs and register every group/supergroup/channel whose title matches the configured partner-group regex (default `^SISC <>`) as a monitored chat, storing its Telegram chat ID, title, and type. Chats whose titles do not match the regex SHALL NOT be monitored.

#### Scenario: Matching groups are monitored
- **WHEN** the account is a member of groups titled `SISC <> AI Builders` and `SISC <> Founders` and the regex is `^SISC <>`
- **THEN** both chats are registered as active monitored chats at startup

#### Scenario: Non-matching groups are ignored
- **WHEN** a dialog's title is `SISC Discussion` (no `<>`) and the regex is `^SISC <>`
- **THEN** that chat is not registered and its messages are not ingested

### Requirement: Real-time message capture with full metadata

While running, the system SHALL store every non-service message posted in a monitored chat with: chat ID and title snapshot, Telegram message ID, sender identity (user ID, username, display name, falling back to sender-chat/author-signature for anonymous admins and channel posts), text or caption content, server-side message date, reply reference, forward origin, and media descriptor (type, file reference, file name — reference only, no binary download).

#### Scenario: Text message ingested with sender metadata
- **WHEN** a member posts a text message in a monitored group
- **THEN** a row is stored containing the chat identity, message ID, sender user ID/username/display name, message text, and the Telegram server-side date, with processing status `pending`

#### Scenario: Media and forward metadata captured
- **WHEN** a message with a document attachment is forwarded into a monitored group from another chat
- **THEN** the stored row records the media type and file reference, `is_forward` is true, and the forward origin chat title and date are captured

#### Scenario: Title rename mid-run stops ingestion for that chat
- **WHEN** a monitored group is renamed so its title no longer matches the regex while the service is running
- **THEN** subsequent messages from that chat are not ingested, and previously stored messages retain the title snapshot captured at ingest time

### Requirement: Message deduplication

The system SHALL store at most one row per `(chat_id, message_id)` pair; repeated delivery of the same Telegram message SHALL NOT create a duplicate row.

#### Scenario: Duplicate message insert is ignored
- **WHEN** the same Telegram message is delivered for storage a second time (e.g., live handler and catch-up overlap)
- **THEN** the database still contains exactly one row for that `(chat_id, message_id)` and the insert reports "not new"

### Requirement: Bounded catch-up after downtime

On startup, for each monitored chat that has at least one previously stored message, the system SHALL fetch recent chat history and ingest messages newer than the last stored message, bounded by the configured per-chat catch-up limit (default 200). A chat with no stored history SHALL start from "now" without importing past history. If more messages were missed than the bound allows, the excess SHALL be logged as permanently missed.

#### Scenario: Missed messages recovered on restart
- **WHEN** the service was down for 30 minutes and 12 messages were posted in a monitored chat, which already has stored history
- **THEN** after restart all 12 messages are stored with their original dates and `pending` status

#### Scenario: First-ever run does not import history
- **WHEN** a newly discovered chat has no previously stored messages
- **THEN** no historical messages are ingested and only messages posted after this startup are captured

#### Scenario: Catch-up bound exceeded
- **WHEN** more than the configured per-chat limit of messages was missed during downtime
- **THEN** only the newest messages up to the limit are ingested and a warning identifying the gap is logged

### Requirement: Resilient ingest against transient storage failures

The system SHALL NOT lose an ingested message due to a transient storage error: failed inserts SHALL be retried automatically from an in-memory retry queue drained on a short periodic cycle.

#### Scenario: Transient database failure is retried
- **WHEN** a message insert fails due to a temporary connectivity error and the database recovers before the process restarts
- **THEN** the message is stored by the retry drain without operator intervention

### Requirement: Secret hygiene

The system SHALL NOT log the PostgreSQL connection string (only a redacted host/database form) and SHALL keep Telegram session files (which grant account access) outside version control.

#### Scenario: Connection string never appears in logs
- **WHEN** the database layer logs connection or startup information
- **THEN** credentials and the full DSN are absent; only a redacted host/database identifier is shown

#### Scenario: Session files are not committed
- **WHEN** the repository is versioned and the user session file exists under the package data directory
- **THEN** the file is matched by `.gitignore` and never appears in version control
