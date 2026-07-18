I've updated the requirements to reflect the actual product behavior rather than just the technical integration.

# Gmail + Google Calendar Integration Implementation Guide

## Overview

This document describes the implementation for integrating Gmail and Google Calendar.

The system should:

1. Detect new Gmail emails in near real-time.
2. Evaluate each email to determine its importance.
3. Forward important emails to a Telegram admin group.
4. Detect upcoming Google Calendar meetings.
5. Send meeting reminders to a Telegram admin group 30 minutes before the meeting starts.

---

# High-Level Architecture

```text
                    Google OAuth
                          │
        ┌─────────────────┴─────────────────┐
        │                                   │
        ▼                                   ▼
   Gmail API                         Calendar API
        │                                   │
        ▼                                   ▼
 Push Notifications                  Watch Notifications
    (Cloud Pub/Sub)                    (Webhook)
        │                                   │
        └──────────────┬────────────────────┘
                       ▼
                 Application
                       │
          ┌────────────┴────────────┐
          ▼                         ▼
   Email Processing          Meeting Reminder
          │                         │
          └────────────┬────────────┘
                       ▼
               Telegram Admin Group
```

---

# Part 1 — Gmail Integration

## Objective

Listen for new Gmail emails in near real-time.

Every new email should be evaluated to determine whether it is important enough to notify the Telegram admin group.

Routine newsletters, promotional emails, and informational updates should be filtered out, while emails requiring attention should be forwarded.

---

## Recommended Approach

Use:

* Gmail Push Notifications (`users.watch`)
* Cloud Pub/Sub
* Gmail History API (`users.history.list`)
* Gmail Messages API (`users.messages.get`)

This is Google's recommended production architecture.

---

## Google Cloud Setup

Create a Google Cloud project.

Enable:

* Gmail API
* Cloud Pub/Sub API

---

## OAuth

Create an OAuth application.

Request the following scope:

```text
https://www.googleapis.com/auth/gmail.readonly
```

If future functionality requires modifying emails (for example, marking emails as read or applying labels), request:

```text
https://www.googleapis.com/auth/gmail.modify
```

After user authorization, securely store the refresh token.

---

## Cloud Pub/Sub

Create a Pub/Sub topic.

Example:

```text
gmail-events
```

Create a subscription that the application will consume.

Grant the Gmail publisher service account permission to publish to the topic:

```text
gmail-api-push@system.gserviceaccount.com
```

Role:

```text
Pub/Sub Publisher
```

---

## Start Watching

After OAuth completes, call:

```text
users.watch()
```

Example request:

```json
{
  "topicName": "projects/<PROJECT_ID>/topics/gmail-events"
}
```

Store:

* `historyId`
* `expiration`

---

## Notification Flow

```text
New Email
      │
      ▼
 Gmail
      │
      ▼
 Cloud Pub/Sub
      │
      ▼
 Application
      │
      ▼
 users.history.list()
      │
      ▼
 New Message IDs
      │
      ▼
 users.messages.get()
      │
      ▼
 Email Importance Evaluation
      │
      ▼
 Telegram Admin Group (if required)
```

---

## Processing Flow

### Step 1

Receive a Pub/Sub notification.

The notification contains:

* email address
* history ID

It does **not** contain the email content.

---

### Step 2

Call:

```text
users.history.list()
```

using the previously stored history ID.

Determine newly added messages.

---

### Step 3

For every new message ID, call:

```text
users.messages.get()
```

Retrieve:

* Subject
* Sender
* Recipients
* Date
* Body
* HTML body
* Attachments
* Thread ID

---

### Step 4

Evaluate Email Importance

Each email should be processed by an LLM prompt that assigns an importance score.

The prompt should determine whether the email is significant enough to notify the Telegram admin group.

Example categories:

**Low Priority**

* Marketing emails
* Newsletters
* Product announcements
* Community updates
* Promotional content

Examples:

* "New members in Google Developers Space, Singapore!"
* Weekly newsletters
* Product updates

---

**High Priority**

* Personal emails
* Collaboration requests
* Meeting invitations
* Client communications
* Emails requiring action
* Security alerts
* Payment or billing issues
* Approval requests

Examples:

* "Action required: security vulnerabilities detected in your projects"
* Collaboration requests
* Customer enquiries
* Contract discussions

---

The LLM should return:

* importance score
* reasoning
* recommended action

Only emails exceeding the configured threshold should be forwarded.

---

### Step 5

If the email exceeds the importance threshold, send a Telegram notification to the admin group.

The notification should include:

* Sender
* Subject
* Summary
* Importance score
* Reasoning
* Link to the Gmail message (if available)

---

### Step 6

Store the latest history ID returned by Gmail.

This becomes the starting point for the next synchronization.

---

## Renew Watch

A Gmail watch expires after approximately seven days.

Periodically call:

```text
users.watch()
```

before expiration to renew the watch.

---

# Part 2 — Google Calendar Integration

## Objective

Track calendar events and notify the Telegram admin group 30 minutes before a meeting starts.

---

## Recommended Approach

Use:

* Calendar Watch (`events.watch`)
* Incremental synchronization (`events.list` with `syncToken`)
* A scheduled reminder process

---

## OAuth

Request the following scope:

```text
https://www.googleapis.com/auth/calendar.readonly
```

---

## Start Watching

Call:

```text
events.watch()
```

Store:

* channel ID
* resource ID
* expiration
* sync token

---

## Synchronization Flow

```text
Calendar Updated
        │
        ▼
 Calendar Notification
        │
        ▼
 Application
        │
        ▼
 events.list(syncToken)
        │
        ▼
 Local Event Store
```

Whenever an event changes:

* created
* updated
* deleted
* moved

synchronize the local event data.

---

## Event Information

Retrieve and store:

* Event ID
* Title
* Description
* Start time
* End time
* Time zone
* Organizer
* Attendees
* Location
* Meeting link (Google Meet, Zoom, Microsoft Teams, etc.)

---

# Part 3 — Meeting Reminder

Run a scheduled task at a suitable interval.

Identify meetings that:

* begin within the next 30 minutes
* have not already triggered a reminder

For each meeting:

1. Retrieve the latest meeting details.
2. Send a reminder to the Telegram admin group.
3. Mark the reminder as sent.

---

## Telegram Reminder

The reminder should contain:

* Meeting title
* Date
* Start time
* End time
* Meeting link (Google Meet, Zoom, Teams, etc.)

Example:

```text
📅 Upcoming Meeting (30 minutes)

Weekly Product Sync

🗓 18 Jul 2026

🕒 2:00 PM – 3:00 PM

🔗 https://meet.google.com/abc-defg-hij
```
