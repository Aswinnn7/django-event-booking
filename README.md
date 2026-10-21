# EventBooking

A Django event reservation and ticket-booking platform.

Users can discover events, reserve tickets, receive email confirmations
and access QR tickets. Organizers can manage their events and check in
attendees.

**This project does not collect online payments.**
Displayed prices represent ticket prices and reservation values,
not payments received.

## Features

- User registration, login and logout
- Event creation and management by the organizer
- Search by event name and filters for location, date and price
- Seat availability and protection against overbooking
- Booking history and cancellation before the event starts
- Organizer dashboard with booking and ticket counts
- QR tickets with organizer-controlled group check-in
- Duplicate check-in prevention
- Email booking confirmations
- Content-based recommendations using TF-IDF and cosine similarity
- Responsive pages for desktop and mobile

## Technology

- Python and Django
- SQLite
- Django templates, HTML and CSS
- scikit-learn
- qrcode and Pillow
- python-dotenv
- Gmail SMTP for email delivery

## Local setup on Windows

Run these commands from the folder containing `manage.py`.

### 1. Create and activate a virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 2. Install dependencies

```powershell
python -m pip install -r requirements.txt
```

### 3. Configure environment variables

Copy the example file:

```powershell
Copy-Item .env.example .env
```

Generate your own Django secret key:

```powershell
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Edit `.env` and replace the placeholders with:

- Your generated Django secret key
- Your Gmail sender address
- A Google App Password for that account

Never commit `.env` or real credentials.

For local development without sending real emails, change
`EMAIL_BACKEND` in `event_project/settings.py` to:

```python
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
```

This prints outgoing emails in the server terminal.

### 4. Apply migrations

```powershell
python manage.py migrate
```

### 5. Start the development server

```powershell
python manage.py runserver
```

Open http://127.0.0.1:8000/ in your browser.

Register an account and create an event with a future date.
Use another account to try booking and viewing a ticket.

The local database is excluded from Git, so a fresh installation
does not contain the developer's accounts or sample events.

## Tests

```powershell
python manage.py test
```

The latest recorded local run passed 79 tests.

Tests cover registration, permissions, bookings, simultaneous requests,
cancellation, filters, dashboard totals, tickets, check-in, email and
recommendations. Tests use a separate test database.

## Booking and check-in rules

- Only confirmed bookings use seats.
- Booking and cancellation close when the event starts.
- Checked-in bookings cannot be cancelled.
- One QR ticket covers the entire booking group.
- Only the event organizer can record check-in.
- Repeat check-in is rejected.
- The organizer currently controls when check-in occurs;
  there is no event-date check-in window.
- A separate QR reader can read the code for entry into the check-in
  form. There is no built-in camera scanner.

## Email behaviour

Confirmation emails are sent after the booking transaction succeeds.
If email sending fails, the booking remains confirmed and an error
is logged. Automatic email retries are not implemented.

## Recommendations

The recommender compares event titles and descriptions with the user's
confirmed booking history.

It excludes past, sold-out and already-booked events. Similar events
rank first; unrelated events may appear lower down. Users without
booking history see upcoming available events.

## Current limitations

- No online payments or refunds
- No scheduled email reminders
- No email-address ownership verification
- Recommendations are recalculated for each eligible homepage request
- Concurrent booking protection relies on SQLite IMMEDIATE transactions
  and must be reviewed before changing the database backend

## Deployment

The current settings are for local development.
Review production settings before deployment, including DEBUG,
ALLOWED_HOSTS, HTTPS and static-file serving.