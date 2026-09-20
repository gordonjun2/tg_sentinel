# daily-digest Specification

## Purpose

Turns the archived partner-group messages into one prioritized daily digest: at a fixed local time each day it summarizes all pending messages with an LLM (primary provider with fallback), renders a readable plain-text report, delivers it to the admin Telegram group in ordered parts, and only then marks the underlying messages as consumed — guaranteeing at-least-once delivery with full run auditing.

## Requirements

### Requirement: Daily schedule in a configured timezone

The system SHALL trigger digest generation once per day at the configured local time (default 19:00) in the configured timezone (default Asia/Singapore). If the trigger time has already passed for the current day, the next run SHALL be scheduled for the following day. A run that becomes due while the process was suspended SHALL execute immediately upon wake.

#### Scenario: Fires at the configured time
- **WHEN** the current time in Asia/Singapore reaches 19:00 and pending messages exist
- **THEN** digest generation starts

#### Scenario: Time already passed schedules tomorrow
- **WHEN** the scheduler computes the next run at 20:00 Asia/Singapore with a 19:00 target
- **THEN** the next run is scheduled for 19:00 the following day

### Requirement: Exclusive run ownership

Only one digest run SHALL execute at a time across all service instances; a second concurrent run attempt SHALL be skipped without side effects (protected by a database-level advisory lock).

#### Scenario: Concurrent instance skipped
- **WHEN** a second service instance attempts to start a digest run while another run holds the advisory lock
- **THEN** the second attempt exits without reading messages or sending anything

### Requirement: Pending-message window selection

Each run SHALL summarize exactly the messages in `pending` state, ordered per chat chronologically. The reporting window SHALL run from the end of the last successful run to the trigger time. On a day with zero pending messages, the system SHALL complete the run silently without sending any message to the admin group.

#### Scenario: Only pending messages are summarized
- **WHEN** 10 messages are pending and 5 earlier ones are already `completed`
- **THEN** the run's transcript is built only from the 10 pending messages

#### Scenario: Empty day is silent
- **WHEN** no messages are pending at the trigger time
- **THEN** the run is recorded as successful with zero counts and no report is delivered

### Requirement: Transcript construction with caps and chunking

Transcripts SHALL be built per chat in chronological order with sender/time annotation and reply/forward markers, each message truncated to the configured per-message character cap (default 800). If the combined transcript exceeds the configured total cap (default 120,000 characters), it SHALL be split at chat boundaries into multiple LLM calls and the per-chunk results merged into one digest. Insight group attribution SHALL be restricted to group names present in the transcript.

#### Scenario: Large day is chunked and merged
- **WHEN** the combined transcript exceeds the total character cap
- **THEN** LLM calls are made per chat-boundary chunk and one merged digest is produced containing insights from all chunks

#### Scenario: Attribution limited to transcript groups
- **WHEN** the LLM returns an insight naming a group that does not appear in the transcript
- **THEN** that attribution is not trusted — only group names present in the transcript may appear in the rendered report's source lines

### Requirement: Dual-provider LLM summarization

Digest generation SHALL call the primary LLM provider with a structured-output schema first and, if that fails, retry with the fallback provider using structured output. The provider used and its latency SHALL be recorded on the run. If both providers fail, the run SHALL be marked failed, no report SHALL be sent, and all messages SHALL remain `pending` for the next run.

#### Scenario: Primary provider succeeds
- **WHEN** the primary provider returns a schema-valid digest
- **THEN** it is used and the run records the primary provider and latency

#### Scenario: Fallback engages on primary failure
- **WHEN** the primary provider errors or returns invalid output
- **THEN** the fallback provider is called with bounded retries and its result is used

#### Scenario: Both providers fail leaves messages pending
- **WHEN** both providers fail
- **THEN** the run is recorded as failed with the error, nothing is delivered, and messages remain `pending`

### Requirement: Structured digest content

The digest SHALL contain at most 5 one-line highlights and a list of insights, each with a short title, a 2–4 sentence concrete detail, the exact source group name(s), and an importance score between 0.0 and 1.0. Chatter, greetings, memes, and logistics noise SHALL be excluded; insights SHALL be sorted by importance in the rendered report.

#### Scenario: Digest validates against the schema
- **WHEN** a provider returns a digest
- **THEN** it parses successfully into the structured model with highlights, insights, valid importance range, and non-empty source group names

### Requirement: Plain-text report rendering and splitting

The report SHALL be deterministic plain text (no markup parsing) including the date, reporting window, group/message counts, highlights, numbered insights sorted by importance with source groups, and per-group message coverage. It SHALL be split into ordered parts of at most 4,096 characters, preferring section boundaries over mid-content cuts, with every part after the first labeled `(Part i/N)` when multiple parts exist.

#### Scenario: Long report splits in order
- **WHEN** the rendered report exceeds 4,096 characters
- **THEN** it is split into multiple parts that, concatenated in order, reproduce the full report with part labels and no content loss

#### Scenario: No parse-mode breakage
- **WHEN** a group title contains characters that would break markup parsing
- **THEN** the report still renders and delivers identically because no markup mode is used

### Requirement: Ordered delivery with per-part retry

The report SHALL be delivered to the configured admin group by the bot identity, parts in order. Each part SHALL retry transient failures with bounded exponential backoff and SHALL respect provider rate-limit signals. Delivery SHALL use a send-only channel that does not conflict with the main bot's update polling. A dry-run mode SHALL log the full report instead of sending.

#### Scenario: Rate-limit wait then success
- **WHEN** a part send is rejected with a rate-limit signal indicating a wait
- **THEN** the service waits at least the indicated period and retries the same part in order

#### Scenario: Part exhausts retries fails the delivery
- **WHEN** one part fails all retry attempts
- **THEN** delivery stops, returns failure, and no further parts are sent

#### Scenario: Dry-run does not send
- **WHEN** dry-run mode is enabled at the trigger time
- **THEN** the full report is logged and nothing is sent to Telegram, with the run treated as delivered

### Requirement: Commit-after-delivery state transitions

Messages SHALL be marked `completed` (with processed timestamp and linking run ID) only after all report parts have been delivered successfully. If any part fails or the run errors, ALL messages in the batch SHALL remain `pending` so the next run retries them (at-least-once delivery).

#### Scenario: Successful run commits the batch
- **WHEN** all parts are delivered successfully
- **THEN** every message in the batch transitions to `completed` linked to the run, and the run is recorded as successful with message/chat counts, provider, latency, and part count

#### Scenario: Delivery failure retries whole batch next run
- **WHEN** the third of five parts fails all retries
- **THEN** all batch messages remain `pending`, the run is recorded as failed, and the next run re-summarizes and re-sends them

### Requirement: Run auditing and backlog observability

Every run SHALL be recorded with window start/end, status (`running`/`success`/`failed`), message and chat counts, provider, latency, part count, error, and timestamps. A status CLI SHALL report pending backlog (total, per chat, oldest age), the last runs with outcomes, and the currently monitored chats.

#### Scenario: Run history is queryable
- **WHEN** a run completes (success or failure)
- **THEN** a row with its window, counts, provider, latency, parts, and error exists for audit

#### Scenario: Operator inspects backlog
- **WHEN** the operator runs the status CLI
- **THEN** pending counts per chat, oldest pending message age, recent run outcomes, and monitored chats are printed
