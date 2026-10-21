import uuid

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class Event(models.Model):
    # Basic event details.
    title = models.CharField(max_length=200)
    description = models.TextField()
    location = models.CharField(max_length=200)
    starts_at = models.DateTimeField()

    # Ticket price cannot be negative.
    ticket_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )

    # Each event must have at least one seat.
    capacity = models.PositiveIntegerField(
        default=50,
        validators=[MinValueValidator(1)],
    )

    # Connect the event to its organizer.
    organizer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
    )

    @property
    def remaining_seats(self):
        # Only confirmed bookings use seats.
        booked_seats = sum(
            booking.quantity
            for booking in self.bookings.filter(status="confirmed")
        )

        # Never display a negative number of remaining seats.
        return max(self.capacity - booked_seats, 0)

    @property
    def booking_closed(self):
        # Booking closes when the event starts.
        return self.starts_at <= timezone.now()

    def __str__(self):
        return self.title


class Booking(models.Model):
    # These statuses describe reservations, not payments.
    # The booking view currently creates confirmed reservations.
    class Status(models.TextChoices):
        PENDING = "pending", "Pending confirmation"
        CONFIRMED = "confirmed", "Confirmed"
        CANCELLED = "cancelled", "Cancelled"

    # Preserve events that have booking history.
    event = models.ForeignKey(
        Event,
        on_delete=models.PROTECT,
        related_name="bookings",
    )

    # Connect the booking to its customer.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="bookings",
    )

    # A booking must contain at least one ticket.
    quantity = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
    )

    # Store the price per ticket when the booking is made.
    unit_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )

    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
    )

    # Automatically record when the booking was created.
    created_at = models.DateTimeField(auto_now_add=True)

    # Generate a unique code for every booking.
    # Keep this field out of normal model forms.
    ticket_code = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )

    # Empty until the organizer confirms entry.
    # One check-in covers every ticket in this booking.
    checked_in_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    def __str__(self):
        return f"Booking {self.pk} - {self.event.title}"