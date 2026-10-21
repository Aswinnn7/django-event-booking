from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier

from django.contrib.auth import get_user_model
from django.db import connections
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from .models import Event, Booking
from unittest.mock import patch

from django.core import mail
from django.test import override_settings

from django.contrib.auth.models import AnonymousUser
from .recommendations import get_recommended_events

class BookingTests(TestCase):
    def setUp(self):
        # These users and records exist only in the test database.
        User = get_user_model()

        self.organizer = User.objects.create_user(
            username="test_organizer",
            password="TestPassword123!",
        )
        self.customer = User.objects.create_user(
            username="test_customer",
            password="TestPassword123!",
        )
        self.other_customer = User.objects.create_user(
            username="other_customer",
            password="TestPassword123!",
        )

        self.event = Event.objects.create(
            title="Test Event",
            description="An event for automated testing.",
            location="Kochi",
            starts_at=timezone.now() + timedelta(days=7),
            ticket_price=Decimal("100.00"),
            capacity=2,
            organizer=self.organizer,
        )

        # Most tests run as the customer.
        self.client.force_login(self.customer)

    def create_booking(self, user=None, quantity=1, status=None):
        # Helper: create a booking needed for a particular test.
        return Booking.objects.create(
            event=self.event,
            user=user or self.customer,
            quantity=quantity,
            unit_price=self.event.ticket_price,
            status=status or Booking.Status.CONFIRMED,
        )

    def event_data(self, **changes):
        # Helper: valid event form data with optional changes.
        data = {
            "title": self.event.title,
            "description": self.event.description,
            "location": self.event.location,
            "starts_at": (
                timezone.now() + timedelta(days=7)
            ).strftime("%Y-%m-%d %H:%M:%S"),
            "ticket_price": "100.00",
            "capacity": 2,
        }
        data.update(changes)
        return data

    def make_event_past(self):
        # Helper: move the event into the past.
        self.event.starts_at = timezone.now() - timedelta(days=1)
        self.event.save()

    def test_protected_actions_require_login(self):
        booking = self.create_booking()
        self.client.logout()

        # Logged-out visitors should be redirected to login.
        urls = [
            reverse("event_create"),
            reverse("event_update", args=[self.event.pk]),
            reverse("event_delete", args=[self.event.pk]),
            reverse("booking_create", args=[self.event.pk]),
            reverse("booking_cancel", args=[booking.pk]),
            reverse("my_bookings"),
        ]

        for url in urls:
            for method in ("get", "post"):
                with self.subTest(url=url, method=method):
                    response = getattr(self.client, method)(url)
                    self.assertEqual(response.status_code, 302)
                    self.assertIn("next=", response["Location"])

        # None of those requests should change existing records.
        self.assertTrue(
            Event.objects.filter(pk=self.event.pk).exists()
        )
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)
        self.assertEqual(Booking.objects.count(), 1)

    def test_create_event_sets_logged_in_user_as_organizer(self):
        response = self.client.post(
            reverse("event_create"),
            self.event_data(title="Customer Created Event"),
        )

        self.assertEqual(response.status_code, 302)

        event = Event.objects.get(title="Customer Created Event")
        self.assertEqual(event.organizer, self.customer)

    def test_organizer_can_edit_own_event(self):
        self.client.force_login(self.organizer)

        response = self.client.post(
            reverse("event_update", args=[self.event.pk]),
            self.event_data(title="Updated Event"),
        )

        self.assertRedirects(
            response,
            reverse("event_detail", args=[self.event.pk]),
        )
        self.event.refresh_from_db()
        self.assertEqual(self.event.title, "Updated Event")

    def test_customer_cannot_edit_another_users_event(self):
        response = self.client.post(
            reverse("event_update", args=[self.event.pk]),
            self.event_data(title="Unauthorized Change"),
        )

        self.assertEqual(response.status_code, 404)
        self.event.refresh_from_db()
        self.assertEqual(self.event.title, "Test Event")

    def test_customer_cannot_delete_another_users_event(self):
        response = self.client.post(
            reverse("event_delete", args=[self.event.pk]),
        )

        self.assertEqual(response.status_code, 404)
        self.assertTrue(
            Event.objects.filter(pk=self.event.pk).exists()
        )

    def test_valid_booking_saves_correct_details(self):
        response = self.client.post(
            reverse("booking_create", args=[self.event.pk]),
            {"quantity": 2},
        )

        self.assertRedirects(
            response,
            reverse("event_detail", args=[self.event.pk]),
        )

        booking = Booking.objects.get(event=self.event)
        self.assertEqual(booking.user, self.customer)
        self.assertEqual(booking.quantity, 2)
        self.assertEqual(booking.unit_price, Decimal("100.00"))
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)

    def test_cannot_book_more_than_capacity(self):
        response = self.client.post(
            reverse("booking_create", args=[self.event.pk]),
            {"quantity": 3},
        )

        self.assertContains(response, "Not enough seats available.")
        self.assertFalse(
            Booking.objects.filter(event=self.event).exists()
        )

    def test_existing_bookings_reduce_available_seats(self):
        # One of the event's two seats is already taken.
        self.create_booking(user=self.other_customer, quantity=1)

        response = self.client.post(
            reverse("booking_create", args=[self.event.pk]),
            {"quantity": 2},
        )

        self.assertContains(response, "Not enough seats available.")
        self.assertEqual(
            Booking.objects.filter(event=self.event).count(),
            1,
        )
        self.assertFalse(
            Booking.objects.filter(
                event=self.event,
                user=self.customer,
            ).exists()
        )

    def test_invalid_quantities_are_rejected(self):
        # Test zero, negative, fractional, nonnumeric and empty values.
        for quantity in (0, -1, "1.5", "abc", ""):
            with self.subTest(quantity=quantity):
                response = self.client.post(
                    reverse("booking_create", args=[self.event.pk]),
                    {"quantity": quantity},
                )

                self.assertEqual(response.status_code, 200)
                self.assertIn(
                    "quantity",
                    response.context["form"].errors,
                )
                self.assertFalse(
                    Booking.objects.filter(event=self.event).exists()
                )

    def test_duplicate_booking_is_rejected(self):
        self.create_booking()

        response = self.client.post(
            reverse("booking_create", args=[self.event.pk]),
            {"quantity": 1},
        )

        self.assertContains(response, "You already booked this event.")
        self.assertEqual(
            Booking.objects.filter(
                event=self.event,
                user=self.customer,
            ).count(),
            1,
        )

    def test_past_event_booking_is_rejected(self):
        self.make_event_past()

        response = self.client.post(
            reverse("booking_create", args=[self.event.pk]),
            {"quantity": 1},
        )

        self.assertContains(
            response,
            "Booking has closed for this event.",
        )
        self.assertFalse(
            Booking.objects.filter(event=self.event).exists()
        )

    def test_capacity_cannot_drop_below_booked_seats(self):
        self.create_booking(quantity=2)
        self.client.force_login(self.organizer)

        response = self.client.post(
            reverse("event_update", args=[self.event.pk]),
            self.event_data(capacity=1),
        )

        self.assertContains(response, "Capacity must be at least 2.")
        self.event.refresh_from_db()
        self.assertEqual(self.event.capacity, 2)

    def test_cancelled_bookings_do_not_limit_capacity(self):
        self.create_booking(
            quantity=2,
            status=Booking.Status.CANCELLED,
        )
        self.client.force_login(self.organizer)

        response = self.client.post(
            reverse("event_update", args=[self.event.pk]),
            self.event_data(capacity=1),
        )

        self.assertEqual(response.status_code, 302)
        self.event.refresh_from_db()
        self.assertEqual(self.event.capacity, 1)

    def test_cancellation_releases_seats_for_another_customer(self):
        booking = self.create_booking(quantity=2)

        response = self.client.post(
            reverse("booking_cancel", args=[booking.pk]),
        )

        self.assertRedirects(response, reverse("my_bookings"))
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CANCELLED)

        # A different customer can now reserve the released seats.
        self.client.force_login(self.other_customer)

        response = self.client.post(
            reverse("booking_create", args=[self.event.pk]),
            {"quantity": 2},
        )

        self.assertEqual(response.status_code, 302)
        new_booking = Booking.objects.get(
            event=self.event,
            user=self.other_customer,
            status=Booking.Status.CONFIRMED,
        )
        self.assertEqual(new_booking.quantity, 2)

    def test_past_event_cancellation_is_rejected(self):
        booking = self.create_booking()
        self.make_event_past()

        response = self.client.post(
            reverse("booking_cancel", args=[booking.pk]),
        )

        self.assertContains(
            response,
            "Cancellation has closed for this event.",
        )
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)

    def test_customer_cannot_cancel_another_users_booking(self):
        booking = self.create_booking(user=self.other_customer)

        response = self.client.post(
            reverse("booking_cancel", args=[booking.pk]),
        )

        self.assertEqual(response.status_code, 404)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)

    def test_my_bookings_contains_only_current_users_records(self):
        own_booking = self.create_booking()
        self.create_booking(user=self.other_customer)

        response = self.client.get(reverse("my_bookings"))

        self.assertEqual(response.status_code, 200)
        visible_ids = [
            booking.pk
            for booking in response.context["bookings"]
        ]
        self.assertEqual(visible_ids, [own_booking.pk])

    def test_get_requests_do_not_cancel_or_delete(self):
        booking = self.create_booking()

        # Opening the cancellation page must not cancel anything.
        response = self.client.get(
            reverse("booking_cancel", args=[booking.pk]),
        )
        self.assertEqual(response.status_code, 200)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)

        # Use an event without bookings to check deletion confirmation.
        empty_event = Event.objects.create(
            title="Empty Event",
            description="No bookings yet.",
            location="Kochi",
            starts_at=timezone.now() + timedelta(days=7),
            ticket_price=Decimal("0.00"),
            capacity=10,
            organizer=self.organizer,
        )
        self.client.force_login(self.organizer)

        response = self.client.get(
            reverse("event_delete", args=[empty_event.pk]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            Event.objects.filter(pk=empty_event.pk).exists()
        )

    def test_event_with_booking_history_cannot_be_deleted(self):
        # Even cancelled bookings must preserve the event's history.
        booking = self.create_booking(
            status=Booking.Status.CANCELLED,
        )
        self.client.force_login(self.organizer)

        response = self.client.post(
            reverse("event_delete", args=[self.event.pk]),
        )

        self.assertContains(
            response,
            "This event has booking history and cannot be deleted.",
        )
        self.assertTrue(
            Event.objects.filter(pk=self.event.pk).exists()
        )
        self.assertTrue(
            Booking.objects.filter(pk=booking.pk).exists()
        )

    def test_organizer_can_delete_event_without_bookings(self):
        self.client.force_login(self.organizer)
        event_pk = self.event.pk

        response = self.client.post(
            reverse("event_delete", args=[event_pk]),
        )

        self.assertRedirects(response, reverse("home"))
        self.assertFalse(
            Event.objects.filter(pk=event_pk).exists()
        )


class SimultaneousBookingTests(TransactionTestCase):
    def setUp(self):
        # Allow real commits across separate database connections.
        User = get_user_model()

        organizer = User.objects.create_user(
            username="organizer",
            password="TestPassword123!",
        )
        customer_one = User.objects.create_user(
            username="customer_one",
            password="TestPassword123!",
        )
        customer_two = User.objects.create_user(
            username="customer_two",
            password="TestPassword123!",
        )

        self.event = Event.objects.create(
            title="Last Seat Test",
            description="Testing simultaneous bookings.",
            location="Kochi",
            starts_at=timezone.now() + timedelta(days=7),
            ticket_price=Decimal("100.00"),
            capacity=1,
            organizer=organizer,
        )

        # Separate sessions for the two customers.
        self.client_one = Client()
        self.client_one.force_login(customer_one)

        self.client_two = Client()
        self.client_two.force_login(customer_two)

    def test_only_one_customer_gets_last_seat(self):
        url = reverse("booking_create", args=[self.event.pk])

        # Start both booking requests together.
        start_together = Barrier(2)

        def book_ticket(client):
            try:
                start_together.wait(timeout=10)
                response = client.post(url, {"quantity": 1})

                return (
                    response.status_code,
                    response.content.decode(),
                )
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(book_ticket, self.client_one)
            second = workers.submit(book_ticket, self.client_two)

            results = [
                first.result(timeout=30),
                second.result(timeout=30),
            ]

        # One succeeds; the other returns the form with an error.
        status_codes = sorted(
            status for status, content in results
        )
        self.assertEqual(status_codes, [200, 302])

        unsuccessful_page = next(
            content
            for status, content in results
            if status == 200
        )
        self.assertIn(
            "Not enough seats available.",
            unsuccessful_page,
        )

        # Only one booking and one reserved seat may exist.
        bookings = Booking.objects.filter(
            event=self.event,
            status=Booking.Status.CONFIRMED,
        )
        self.assertEqual(bookings.count(), 1)
        self.assertEqual(
            sum(booking.quantity for booking in bookings),
            1,
        )

class AvailabilityAndFilterTests(TestCase):
    def setUp(self):
        User = get_user_model()

        self.organizer = User.objects.create_user(
            username="filter_organizer",
            password="TestPassword123!",
        )

        self.customer = User.objects.create_user(
            username="filter_customer",
            password="TestPassword123!",
        )

        # Use future dates so tests keep working over time.
        self.event_date = timezone.now() + timedelta(days=7)

        self.free_event = Event.objects.create(
            title="Python Workshop",
            description="Learn Python.",
            location="Kochi",
            starts_at=self.event_date,
            ticket_price=Decimal("0.00"),
            capacity=10,
            organizer=self.organizer,
        )

        self.paid_event = Event.objects.create(
            title="Music Night",
            description="An evening of music.",
            location="Bengaluru",
            starts_at=self.event_date + timedelta(days=1),
            ticket_price=Decimal("250.00"),
            capacity=20,
            organizer=self.organizer,
        )

    def result_ids(self, response):
        # Helper: get the IDs of events displayed on this page.
        return {
            event.pk
            for event in response.context["page_obj"]
        }

    def create_booking(self, quantity, status):
        # Helper: create a booking for the free event.
        return Booking.objects.create(
            event=self.free_event,
            user=self.customer,
            quantity=quantity,
            unit_price=Decimal("0.00"),
            status=status,
        )

    def test_remaining_seats_excludes_cancelled_bookings(self):
        self.create_booking(3, Booking.Status.CONFIRMED)
        self.create_booking(2, Booking.Status.CANCELLED)

        # Only the three confirmed tickets use seats.
        self.assertEqual(self.free_event.remaining_seats, 7)

    def test_sold_out_hides_booking_link(self):
        self.create_booking(10, Booking.Status.CONFIRMED)
        self.client.force_login(self.customer)

        response = self.client.get(
            reverse("event_detail", args=[self.free_event.pk]),
        )

        self.assertEqual(self.free_event.remaining_seats, 0)
        self.assertContains(response, "Sold out")
        self.assertNotContains(
            response,
            reverse("booking_create", args=[self.free_event.pk]),
        )

    def test_past_event_shows_booking_closed(self):
        self.free_event.starts_at = (
            timezone.now() - timedelta(days=1)
        )
        self.free_event.save()
        self.client.force_login(self.customer)

        response = self.client.get(
            reverse("event_detail", args=[self.free_event.pk]),
        )

        self.assertTrue(self.free_event.booking_closed)
        self.assertContains(response, "Booking closed")
        self.assertNotContains(
            response,
            reverse("booking_create", args=[self.free_event.pk]),
        )

    def test_available_event_shows_booking_link(self):
        self.client.force_login(self.customer)

        response = self.client.get(
            reverse("event_detail", args=[self.free_event.pk]),
        )

        self.assertFalse(self.free_event.booking_closed)
        self.assertContains(
            response,
            reverse("booking_create", args=[self.free_event.pk]),
        )

    def test_title_search_ignores_capital_letters(self):
        response = self.client.get(
            reverse("home"),
            {"search": "PYTHON"},
        )

        self.assertEqual(
            self.result_ids(response),
            {self.free_event.pk},
        )

    def test_location_filter(self):
        response = self.client.get(
            reverse("home"),
            {"location": "kochi"},
        )

        self.assertEqual(
            self.result_ids(response),
            {self.free_event.pk},
        )

    def test_date_filter(self):
        # Match the date in the project's current timezone.
        selected_date = timezone.localtime(
            self.paid_event.starts_at
        ).date().isoformat()

        response = self.client.get(
            reverse("home"),
            {"date": selected_date},
        )

        self.assertEqual(
            self.result_ids(response),
            {self.paid_event.pk},
        )

    def test_free_filter(self):
        response = self.client.get(
            reverse("home"),
            {"price": "free"},
        )

        self.assertEqual(
            self.result_ids(response),
            {self.free_event.pk},
        )

    def test_paid_filter(self):
        response = self.client.get(
            reverse("home"),
            {"price": "paid"},
        )

        self.assertEqual(
            self.result_ids(response),
            {self.paid_event.pk},
        )

    def test_filters_work_together(self):
        selected_date = timezone.localtime(
            self.free_event.starts_at
        ).date().isoformat()

        response = self.client.get(
            reverse("home"),
            {
                "search": "python",
                "location": "Kochi",
                "date": selected_date,
                "price": "free",
            },
        )

        self.assertEqual(
            self.result_ids(response),
            {self.free_event.pk},
        )

        # Changing one filter to a mismatch should remove the result.
        response = self.client.get(
            reverse("home"),
            {
                "search": "python",
                "location": "Kochi",
                "date": selected_date,
                "price": "paid",
            },
        )

        self.assertEqual(self.result_ids(response), set())

    def test_invalid_filters_show_errors(self):
        for filters, field in [
            ({"date": "not-a-date"}, "date"),
            ({"price": "unknown"}, "price"),
        ]:
            with self.subTest(filters=filters):
                response = self.client.get(
                    reverse("home"),
                    filters,
                )

                self.assertEqual(response.status_code, 200)
                self.assertIn(
                    field,
                    response.context["filter_form"].errors,
                )
                self.assertEqual(self.result_ids(response), set())
                self.assertContains(
                    response,
                    "Please correct the filter errors",
                )

    def test_clear_filters_shows_all_events(self):
        # The Clear filters link opens home without query parameters.
        response = self.client.get(reverse("home"))

        self.assertEqual(
            self.result_ids(response),
            {self.free_event.pk, self.paid_event.pk},
        )

    def test_pagination_preserves_filters(self):
        from urllib.parse import parse_qs, urlsplit
        from html import unescape
        import re

        # Create enough matching events for two pages.
        for number in range(6):
            Event.objects.create(
                title=f"Workshop {number}",
                description="Pagination test.",
                location="Kochi",
                starts_at=self.event_date,
                ticket_price=Decimal("0.00"),
                capacity=10,
                organizer=self.organizer,
            )

        selected_date = timezone.localtime(
            self.event_date
        ).date().isoformat()

        filters = {
            "search": "Workshop",
            "location": "Kochi",
            "date": selected_date,
            "price": "free",
        }

        response = self.client.get(reverse("home"), filters)
        self.assertTrue(response.context["page_obj"].has_next())

        # Find the actual Next link rendered by the template.
        match = re.search(
            r'href="([^"]+)"[^>]*>\s*Next\s*</a>',
            response.content.decode(),
        )
        self.assertIsNotNone(match)

        next_url = unescape(match.group(1))
        next_query = parse_qs(urlsplit(next_url).query)

        # Every filter must remain in that link.
        for key, value in filters.items():
            self.assertEqual(next_query[key], [value])

        self.assertEqual(next_query["page"], ["2"])

        # Follow the link and check the second page.
        second_response = self.client.get(
            reverse("home") + next_url
        )
        self.assertEqual(
            second_response.context["page_obj"].number,
            2,
        )
        self.assertEqual(
            second_response.context["page_obj"].paginator.count,
            7,
        )

        # Events from page one should not repeat on page two.
        self.assertTrue(
            self.result_ids(response).isdisjoint(
                self.result_ids(second_response)
            )
        )

class OrganizerDashboardTests(TestCase):
    def setUp(self):
        User = get_user_model()

        self.organizer = User.objects.create_user(
            username="dashboard_organizer",
            password="TestPassword123!",
        )

        self.other_organizer = User.objects.create_user(
            username="other_organizer",
            password="TestPassword123!",
        )

        self.customer = User.objects.create_user(
            username="dashboard_customer",
            password="TestPassword123!",
        )

        # Create one event for each organizer.
        self.own_event = Event.objects.create(
            title="My Dashboard Event",
            description="Owned by the first organizer.",
            location="Kochi",
            starts_at=timezone.now() + timedelta(days=7),
            ticket_price=Decimal("100.00"),
            capacity=10,
            organizer=self.organizer,
        )

        self.other_event = Event.objects.create(
            title="Other Organizer Event",
            description="Owned by a different organizer.",
            location="Bengaluru",
            starts_at=timezone.now() + timedelta(days=7),
            ticket_price=Decimal("200.00"),
            capacity=20,
            organizer=self.other_organizer,
        )

        # One confirmed booking containing three tickets.
        Booking.objects.create(
            event=self.own_event,
            user=self.customer,
            quantity=3,
            unit_price=Decimal("100.00"),
            status=Booking.Status.CONFIRMED,
        )

        # Cancelled tickets must not count as reserved tickets.
        Booking.objects.create(
            event=self.own_event,
            user=self.customer,
            quantity=2,
            unit_price=Decimal("100.00"),
            status=Booking.Status.CANCELLED,
        )

        # This booking belongs to the other organizer's event.
        Booking.objects.create(
            event=self.other_event,
            user=self.customer,
            quantity=5,
            unit_price=Decimal("200.00"),
            status=Booking.Status.CONFIRMED,
        )

        self.url = reverse("organizer_dashboard")
        self.client.force_login(self.organizer)

    def test_dashboard_requires_login(self):
        self.client.logout()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn("next=", response["Location"])

    def test_dashboard_shows_only_own_events(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)

        event_ids = {
            event.pk for event in response.context["events"]
        }
        self.assertEqual(event_ids, {self.own_event.pk})

        self.assertContains(response, "My Dashboard Event")
        self.assertNotContains(response, "Other Organizer Event")

    def test_dashboard_totals_are_correct(self):
        response = self.client.get(self.url)

        # Count only this organizer's events and bookings.
        self.assertEqual(response.context["total_events"], 1)
        self.assertEqual(response.context["total_bookings"], 1)
        self.assertEqual(response.context["total_tickets"], 3)
        self.assertEqual(response.context["total_cancellations"], 1)

    def test_user_without_events_sees_empty_dashboard(self):
        # The customer has bookings but has not organized any events.
        self.client.force_login(self.customer)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_events"], 0)
        self.assertEqual(response.context["total_bookings"], 0)
        self.assertEqual(response.context["total_tickets"], 0)
        self.assertEqual(response.context["total_cancellations"], 0)
        self.assertEqual(list(response.context["events"]), [])

class TicketAndCheckInTests(TestCase):
    def setUp(self):
        # Create sample data only in the test database.
        User = get_user_model()

        self.organizer = User.objects.create_user(
            username="ticket_organizer",
        )
        self.customer = User.objects.create_user(
            username="ticket_customer",
        )
        self.other_user = User.objects.create_user(
            username="ticket_other_user",
        )

        self.event = Event.objects.create(
            title="QR Test Event",
            description="An event for testing tickets.",
            location="Kochi",
            starts_at=timezone.now() + timedelta(days=7),
            ticket_price=Decimal("100.00"),
            capacity=10,
            organizer=self.organizer,
        )

        self.booking = Booking.objects.create(
            event=self.event,
            user=self.customer,
            quantity=3,
            unit_price=Decimal("100.00"),
            status=Booking.Status.CONFIRMED,
        )

        # Save the URLs used by these tests.
        self.ticket_url = reverse(
            "booking_ticket",
            args=[self.booking.pk],
        )
        self.qr_url = reverse(
            "booking_ticket_qr",
            args=[self.booking.pk],
        )
        self.check_in_url = reverse(
            "ticket_check_in",
            args=[self.event.pk],
        )

        self.client.force_login(self.customer)

    def submit_ticket(self):
        # Helper: submit this booking's ticket code.
        return self.client.post(
            self.check_in_url,
            {"ticket_code": str(self.booking.ticket_code)},
        )

    def test_ticket_and_check_in_require_login(self):
        self.client.logout()

        for url in (
            self.ticket_url,
            self.qr_url,
            self.check_in_url,
        ):
            with self.subTest(url=url):
                response = self.client.get(url)

                self.assertEqual(response.status_code, 302)
                self.assertIn("next=", response["Location"])

        # A logged-out visitor cannot submit a check-in either.
        response = self.submit_ticket()
        self.assertEqual(response.status_code, 302)

        self.booking.refresh_from_db()
        self.assertIsNone(self.booking.checked_in_at)

    def test_owner_can_view_ticket_and_qr_link(self):
        response = self.client.get(self.ticket_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["booking"].pk,
            self.booking.pk,
        )
        self.assertContains(
            response,
            str(self.booking.ticket_code),
        )
        self.assertContains(response, self.qr_url)

    def test_other_users_cannot_view_ticket_or_qr(self):
        # Even the organizer cannot open another user's private
        # ticket page. Organizers use the check-in page instead.
        for user in (self.other_user, self.organizer):
            self.client.force_login(user)

            for url in (self.ticket_url, self.qr_url):
                with self.subTest(user=user.username, url=url):
                    response = self.client.get(url)
                    self.assertEqual(response.status_code, 404)

    def test_qr_returns_a_valid_png(self):
        from io import BytesIO
        from PIL import Image

        response = self.client.get(self.qr_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertIn("no-store", response["Cache-Control"])

        # Check that the returned data is a valid PNG image.
        with Image.open(BytesIO(response.content)) as image:
            self.assertEqual(image.format, "PNG")
            image.verify()

    def test_unconfirmed_bookings_have_no_qr(self):
        for status in (
            Booking.Status.CANCELLED,
            Booking.Status.PENDING,
        ):
            with self.subTest(status=status):
                self.booking.status = status
                self.booking.save(update_fields=["status"])

                # The ticket page must not display the QR image.
                response = self.client.get(self.ticket_url)
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, self.qr_url)

                # Opening the image URL directly must also fail.
                response = self.client.get(self.qr_url)
                self.assertEqual(response.status_code, 404)

    def test_only_event_organizer_can_check_in(self):
        for user in (self.customer, self.other_user):
            self.client.force_login(user)

            with self.subTest(user=user.username):
                response = self.client.get(self.check_in_url)
                self.assertEqual(response.status_code, 404)

                response = self.submit_ticket()
                self.assertEqual(response.status_code, 404)

        self.booking.refresh_from_db()
        self.assertIsNone(self.booking.checked_in_at)

    def test_opening_check_in_page_does_not_check_in(self):
        self.client.force_login(self.organizer)

        # A GET request must not record attendance,
        # even if a ticket code is included.
        response = self.client.get(
            self.check_in_url,
            {"ticket_code": str(self.booking.ticket_code)},
        )

        self.assertEqual(response.status_code, 200)

        self.booking.refresh_from_db()
        self.assertIsNone(self.booking.checked_in_at)

    def test_successful_check_in_records_group_attendance(self):
        self.client.force_login(self.organizer)
        before = timezone.now()

        response = self.submit_ticket()

        after = timezone.now()
        self.assertContains(response, "Check-in successful")

        self.booking.refresh_from_db()

        # Attendance has a timestamp within this request's time.
        self.assertIsNotNone(self.booking.checked_in_at)
        self.assertGreaterEqual(self.booking.checked_in_at, before)
        self.assertLessEqual(self.booking.checked_in_at, after)

        # The booking still covers all three people.
        self.assertEqual(self.booking.quantity, 3)
        self.assertEqual(
            self.booking.status,
            Booking.Status.CONFIRMED,
        )
        self.assertEqual(
            response.context["checked_booking"].pk,
            self.booking.pk,
        )

        # Checking in does not release the reserved seats.
        self.assertEqual(self.event.remaining_seats, 7)

        # The customer's ticket page now hides the QR.
        self.client.force_login(self.customer)
        response = self.client.get(self.ticket_url)

        self.assertContains(response, "Already checked in")
        self.assertNotContains(response, self.qr_url)

    def test_repeat_check_in_is_rejected(self):
        self.client.force_login(self.organizer)

        first_response = self.submit_ticket()
        self.assertContains(first_response, "Check-in successful")

        self.booking.refresh_from_db()
        original_time = self.booking.checked_in_at

        # Submit the same ticket again.
        response = self.submit_ticket()

        self.assertContains(
            response,
            "This booking has already been checked in.",
        )
        self.assertIsNone(response.context["checked_booking"])

        # The original attendance time must remain unchanged.
        self.booking.refresh_from_db()
        self.assertEqual(
            self.booking.checked_in_at,
            original_time,
        )

    def test_invalid_or_unknown_ticket_codes_are_rejected(self):
        from uuid import uuid4

        self.client.force_login(self.organizer)

        # Empty, badly formatted and valid-but-unknown codes.
        for code in ("", "not-a-ticket-code", str(uuid4())):
            with self.subTest(code=code):
                response = self.client.post(
                    self.check_in_url,
                    {"ticket_code": code},
                )

                self.assertEqual(response.status_code, 200)
                self.assertIn(
                    "ticket_code",
                    response.context["form"].errors,
                )
                self.assertIsNone(
                    response.context["checked_booking"],
                )

                self.booking.refresh_from_db()
                self.assertIsNone(self.booking.checked_in_at)

    def test_ticket_for_another_event_is_rejected(self):
        # Give the same organizer another event.
        other_event = Event.objects.create(
            title="Different Event",
            description="This event needs its own tickets.",
            location="Kochi",
            starts_at=timezone.now() + timedelta(days=8),
            ticket_price=Decimal("100.00"),
            capacity=10,
            organizer=self.organizer,
        )

        self.client.force_login(self.organizer)

        # Try using the first event's ticket at the other event.
        response = self.client.post(
            reverse("ticket_check_in", args=[other_event.pk]),
            {"ticket_code": str(self.booking.ticket_code)},
        )

        self.assertContains(
            response,
            "No matching ticket was found for this event.",
        )

        self.booking.refresh_from_db()
        self.assertIsNone(self.booking.checked_in_at)

    def test_unconfirmed_bookings_cannot_check_in(self):
        self.client.force_login(self.organizer)

        for status in (
            Booking.Status.CANCELLED,
            Booking.Status.PENDING,
        ):
            with self.subTest(status=status):
                self.booking.status = status
                self.booking.save(update_fields=["status"])

                response = self.submit_ticket()

                self.assertContains(
                    response,
                    "This booking is not confirmed and cannot be checked in.",
                )

                self.booking.refresh_from_db()
                self.assertIsNone(self.booking.checked_in_at)
                self.assertEqual(self.booking.status, status)

    def test_checked_in_booking_cannot_be_cancelled(self):
        # First record attendance through the organizer's page.
        self.client.force_login(self.organizer)
        response = self.submit_ticket()
        self.assertContains(response, "Check-in successful")

        self.booking.refresh_from_db()
        original_time = self.booking.checked_in_at

        # Then try cancelling as the customer.
        self.client.force_login(self.customer)

        response = self.client.post(
            reverse("booking_cancel", args=[self.booking.pk]),
        )

        self.assertContains(
            response,
            "A checked-in booking cannot be cancelled.",
        )

        self.booking.refresh_from_db()
        self.assertEqual(
            self.booking.status,
            Booking.Status.CONFIRMED,
        )
        self.assertEqual(
            self.booking.checked_in_at,
            original_time,
        )
        self.assertEqual(self.event.remaining_seats, 7)

@override_settings(
    # Store test emails in memory instead of sending real emails.
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="EventBooking <noreply@example.com>",
)
class BookingEmailTests(TestCase):
    def setUp(self):
        User = get_user_model()

        self.organizer = User.objects.create_user(
            username="email_organizer",
        )

        self.customer = User.objects.create_user(
            username="email_customer",
            email="customer@example.com",
        )

        self.event = Event.objects.create(
            title="Email Test Event",
            description="Testing booking confirmations.",
            location="Kochi",
            starts_at=timezone.now() + timedelta(days=7),
            ticket_price=Decimal("100.00"),
            capacity=10,
            organizer=self.organizer,
        )

        self.booking_url = reverse(
            "booking_create",
            args=[self.event.pk],
        )

        self.client.force_login(self.customer)

    def test_successful_booking_sends_confirmation(self):
        # TestCase normally keeps transactions open.
        # This runs the on_commit callbacks for this request.
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                self.booking_url,
                {"quantity": 2},
            )

        self.assertEqual(response.status_code, 302)

        booking = Booking.objects.get(
            event=self.event,
            user=self.customer,
        )

        # Exactly one email should be prepared.
        self.assertEqual(len(mail.outbox), 1)

        email = mail.outbox[0]

        self.assertEqual(
            email.to,
            ["customer@example.com"],
        )
        self.assertEqual(
            email.subject,
            "Booking confirmed: Email Test Event",
        )
        self.assertIn(
            f"Booking reference: #{booking.pk}",
            email.body,
        )
        self.assertIn("Location: Kochi", email.body)
        self.assertIn("Number of tickets: 2", email.body)
        self.assertIn(
            "Total reservation value: INR 200.00",
            email.body,
        )

    def test_email_waits_for_commit_callback(self):
        # Capture the callback without running it yet.
        with self.captureOnCommitCallbacks(
            execute=False
        ) as callbacks:
            response = self.client.post(
                self.booking_url,
                {"quantity": 1},
            )

            # No email should be sent while saving the booking.
            self.assertEqual(len(mail.outbox), 0)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(len(mail.outbox), 0)

        # Simulate Django running the callback after commit.
        callbacks[0]()

        self.assertEqual(len(mail.outbox), 1)

    def test_rejected_booking_does_not_send_email(self):
        # The event has only ten seats.
        with self.captureOnCommitCallbacks(
            execute=True
        ) as callbacks:
            response = self.client.post(
                self.booking_url,
                {"quantity": 11},
            )

        self.assertContains(
            response,
            "Not enough seats available.",
        )

        self.assertFalse(
            Booking.objects.filter(
                event=self.event,
                user=self.customer,
            ).exists()
        )

        self.assertEqual(len(callbacks), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_missing_email_does_not_stop_booking(self):
        # Older accounts may have no email address.
        self.customer.email = ""
        self.customer.save(update_fields=["email"])

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                self.booking_url,
                {"quantity": 1},
            )

        self.assertEqual(response.status_code, 302)

        self.assertTrue(
            Booking.objects.filter(
                event=self.event,
                user=self.customer,
                status=Booking.Status.CONFIRMED,
            ).exists()
        )

        self.assertEqual(len(mail.outbox), 0)

    def test_email_failure_keeps_booking_confirmed(self):
        # Simulate an email service failure.
        with patch(
            "event_app.emails.send_mail",
            side_effect=RuntimeError("Email service unavailable"),
        ) as mocked_send:
            with self.assertLogs(
                "event_app.views",
                level="ERROR",
            ) as logs:
                with self.captureOnCommitCallbacks(execute=True):
                    response = self.client.post(
                        self.booking_url,
                        {"quantity": 1},
                    )

        self.assertEqual(response.status_code, 302)
        mocked_send.assert_called_once()

        # The booking must remain saved and confirmed.
        booking = Booking.objects.get(
            event=self.event,
            user=self.customer,
        )

        self.assertEqual(
            booking.status,
            Booking.Status.CONFIRMED,
        )
        self.assertEqual(booking.quantity, 1)

        # The failure should be recorded in the application log.
        self.assertIn(
            "Could not send confirmation",
            "\n".join(logs.output),
        )
        self.assertEqual(len(mail.outbox), 0)

class RecommendationTests(TestCase):
    def setUp(self):
        # All these records exist only in the test database.
        User = get_user_model()

        self.organizer = User.objects.create_user(
            username="recommendation_organizer",
        )
        self.customer = User.objects.create_user(
            username="recommendation_customer",
        )
        self.other_customer = User.objects.create_user(
            username="recommendation_other_customer",
        )

        # A past confirmed booking provides the customer's interests.
        self.history_event = self.create_event(
            title="Python Django Workshop",
            description="Python Django programming web applications",
            days=-7,
        )

        self.create_booking(self.history_event)

        # The unrelated event happens sooner.
        self.music_event = self.create_event(
            title="Music Concert",
            description="Music singing guitars band performance",
            days=2,
        )

        # The related event should rank first despite happening later.
        self.python_event = self.create_event(
            title="Python Django Coding",
            description="Python Django programming web applications",
            days=5,
        )

    def create_event(self, title, description, days, capacity=10):
        # Helper: create an event relative to today's date.
        return Event.objects.create(
            title=title,
            description=description,
            location="Kochi",
            starts_at=timezone.now() + timedelta(days=days),
            ticket_price=Decimal("0.00"),
            capacity=capacity,
            organizer=self.organizer,
        )

    def create_booking(
        self,
        event,
        user=None,
        quantity=1,
        status=Booking.Status.CONFIRMED,
    ):
        # Create sample bookings directly without sending emails.
        return Booking.objects.create(
            event=event,
            user=user or self.customer,
            quantity=quantity,
            unit_price=event.ticket_price,
            status=status,
        )

    def recommendation_ids(self, user=None, limit=3):
        # Helper: return IDs in recommendation order.
        events = get_recommended_events(
            user if user is not None else self.customer,
            limit=limit,
        )
        return [event.pk for event in events]

    def test_similar_event_ranks_before_earlier_unrelated_event(self):
        self.assertEqual(
            self.recommendation_ids(),
            [self.python_event.pk, self.music_event.pk],
        )

    def test_past_events_are_excluded(self):
        past_event = self.create_event(
            title="Previous Python Class",
            description="Python Django programming",
            days=-1,
        )

        recommended_ids = self.recommendation_ids()

        self.assertNotIn(past_event.pk, recommended_ids)
        self.assertNotIn(self.history_event.pk, recommended_ids)

    def test_already_booked_events_are_excluded(self):
        self.create_booking(self.python_event)

        self.assertEqual(
            self.recommendation_ids(),
            [self.music_event.pk],
        )

    def test_sold_out_events_are_excluded(self):
        # Another customer takes every seat in the Python event.
        self.create_booking(
            self.python_event,
            user=self.other_customer,
            quantity=10,
        )

        self.assertEqual(
            self.recommendation_ids(),
            [self.music_event.pk],
        )

    def test_cancelled_booking_does_not_exclude_event(self):
        # Cancelled tickets neither use seats nor count as interests.
        self.create_booking(
            self.python_event,
            quantity=10,
            status=Booking.Status.CANCELLED,
        )

        self.assertIn(
            self.python_event.pk,
            self.recommendation_ids(),
        )

    def test_new_user_gets_upcoming_events_in_date_order(self):
        # This customer has no bookings.
        self.assertEqual(
            self.recommendation_ids(user=self.other_customer),
            [self.music_event.pk, self.python_event.pk],
        )

    def test_logged_out_visitor_gets_upcoming_events(self):
        self.assertEqual(
            self.recommendation_ids(user=AnonymousUser()),
            [self.music_event.pk, self.python_event.pk],
        )

    def test_recommendation_limit_is_respected(self):
        self.assertEqual(
            self.recommendation_ids(limit=1),
            [self.python_event.pk],
        )
        self.assertEqual(self.recommendation_ids(limit=0), [])

    def test_no_eligible_events_returns_empty_list(self):
        # Both future events are already booked by this customer.
        self.create_booking(self.python_event)
        self.create_booking(self.music_event)

        self.assertEqual(self.recommendation_ids(), [])

        # The home page should also hide the empty section.
        self.client.force_login(self.customer)
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(
            response,
            'id="recommendations-heading"',
        )

    def test_text_without_useful_words_falls_back_to_date_order(self):
        # These words are removed by the English stop-word filter.
        for event in (
            self.history_event,
            self.python_event,
            self.music_event,
        ):
            event.title = "the and"
            event.description = "or the"
            event.save(update_fields=["title", "description"])

        # The page must still work when no useful vocabulary remains.
        self.assertEqual(
            self.recommendation_ids(),
            [self.music_event.pk, self.python_event.pk],
        )

    def test_home_page_receives_and_displays_recommendations(self):
        self.client.force_login(self.customer)

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [
                event.pk
                for event in response.context["recommended_events"]
            ],
            [self.python_event.pk, self.music_event.pk],
        )

        self.assertContains(
            response,
            'id="recommendations-heading"',
        )

        # Check the actual suggestion section, not the main event list.
        html = response.content.decode()
        section = html.split(
            'aria-labelledby="recommendations-heading"',
            1,
        )[1].split("</section>", 1)[0]

        self.assertIn(self.python_event.title, section)
        self.assertIn(self.music_event.title, section)
        self.assertLess(
            section.index(self.python_event.title),
            section.index(self.music_event.title),
        )

    def test_search_and_filters_hide_recommendations(self):
        self.client.force_login(self.customer)

        event_date = timezone.localtime(
            self.python_event.starts_at
        ).date().isoformat()

        filters_to_check = [
            {"search": "Python"},
            {"location": "Kochi"},
            {"date": event_date},
            {"price": "free"},
            {"date": "invalid-date"},
        ]

        for filters in filters_to_check:
            with self.subTest(filters=filters):
                response = self.client.get(
                    reverse("home"),
                    filters,
                )

                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.context["recommended_events"],
                    [],
                )
                self.assertNotContains(
                    response,
                    'id="recommendations-heading"',
                )

    def test_second_page_hides_recommendations(self):
        # Add enough events to create a second page.
        for number in range(4):
            self.create_event(
                title=f"Extra Event {number}",
                description="An additional upcoming event.",
                days=10 + number,
            )

        self.client.force_login(self.customer)

        response = self.client.get(
            reverse("home"),
            {"page": 2},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_obj"].number, 2)
        self.assertEqual(
            response.context["recommended_events"],
            [],
        )
        self.assertNotContains(
            response,
            'id="recommendations-heading"',
        )