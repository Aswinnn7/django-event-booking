import logging
from functools import partial
from io import BytesIO

import qrcode

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.http import HttpResponse
from django.utils import timezone

from .models import Event, Booking
from .forms import (
    EventForm,
    BookingForm,
    EventFilterForm,
    TicketCheckInForm,
)
from .emails import send_booking_confirmation
from .recommendations import get_recommended_events


# Record email errors without cancelling successful bookings.
logger = logging.getLogger(__name__)


def home(request):
    # Read the event-name search.
    search = request.GET.get("search", "").strip()

    # Validate optional location, date and price filters.
    filter_form = EventFilterForm(request.GET)

    events = Event.objects.all().order_by("starts_at", "pk")

    if search:
        events = events.filter(title__icontains=search)

    if filter_form.is_valid():
        location = filter_form.cleaned_data["location"]
        event_date = filter_form.cleaned_data["date"]
        price = filter_form.cleaned_data["price"]

        if location:
            events = events.filter(location__icontains=location)

        if event_date:
            events = events.filter(starts_at__date=event_date)

        if price == "free":
            events = events.filter(ticket_price=0)
        elif price == "paid":
            events = events.filter(ticket_price__gt=0)
    else:
        # The template displays the filter errors.
        events = events.none()

    # Show five matching events per page.
    paginator = Paginator(events, 5)
    page_obj = paginator.get_page(request.GET.get("page"))

    # Preserve filters when changing pages.
    query_params = request.GET.copy()
    query_params.pop("page", None)

    # Display suggestions only on the first unfiltered page.
    filters_active = any(
        request.GET.get(name, "").strip()
        for name in ("search", "location", "date", "price")
    )

    recommended_events = []

    if not filters_active and page_obj.number == 1:
        recommended_events = get_recommended_events(
            request.user,
            limit=3,
        )

    return render(
        request,
        "eventapp/home.html",
        {
            "page_title": "Discover your next event",
            "page_obj": page_obj,
            "search": search,
            "filter_form": filter_form,
            "filter_query": query_params.urlencode(),
            "recommended_events": recommended_events,
        },
    )


def event_detail(request, pk):
    # Find the requested event or return a 404.
    event = get_object_or_404(Event, pk=pk)

    return render(
        request,
        "eventapp/event_detail.html",
        {"event": event},
    )


@login_required
def event_create(request):
    if request.method == "POST":
        # Receive text fields and any uploaded cover image.
        form = EventForm(request.POST, request.FILES)

        if form.is_valid():
            # Set the logged-in user as the organizer.
            event = form.save(commit=False)
            event.organizer = request.user
            event.save()

            return redirect("event_detail", pk=event.pk)
    else:
        form = EventForm()

    return render(
        request,
        "eventapp/event_form.html",
        {"form": form},
    )


@login_required
@transaction.atomic
def event_update(request, pk):
    # Only the organizer can edit this event.
    # Keep the capacity check and update together.
    event = get_object_or_404(
        Event,
        pk=pk,
        organizer=request.user,
    )

    if request.method == "POST":
        # Receive text fields and an optional replacement image.
        # The form also supports clearing the current image.
        form = EventForm(
            request.POST,
            request.FILES,
            instance=event,
        )

        if form.is_valid():
            # Cancelled bookings do not use seats.
            booked_seats = sum(
                booking.quantity
                for booking in event.bookings.filter(
                    status=Booking.Status.CONFIRMED
                )
            )

            if form.cleaned_data["capacity"] < booked_seats:
                form.add_error(
                    "capacity",
                    f"Capacity must be at least {booked_seats}.",
                )
            else:
                form.save()
                return redirect("event_detail", pk=event.pk)
    else:
        # Fill the form with the event's current details.
        form = EventForm(instance=event)

    return render(
        request,
        "eventapp/event_form.html",
        {"form": form},
    )


@login_required
@transaction.atomic
def event_delete(request, pk):
    # Only the organizer can delete this event.
    event = get_object_or_404(
        Event,
        pk=pk,
        organizer=request.user,
    )

    error = None

    if request.method == "POST":
        try:
            event.delete()
        except ProtectedError:
            # Preserve events that have booking history.
            error = "This event has booking history and cannot be deleted."
        else:
            return redirect("home")

    return render(
        request,
        "eventapp/event_confirm_delete.html",
        {
            "event": event,
            "error": error,
        },
    )


def send_confirmation_safely(booking):
    # An email failure must not undo a successful booking.
    try:
        send_booking_confirmation(booking)
    except Exception:
        logger.exception(
            "Could not send confirmation for booking %s",
            booking.pk,
        )


@login_required
def booking_create(request, pk):
    event = get_object_or_404(Event, pk=pk)

    if request.method == "POST":
        form = BookingForm(request.POST)
    else:
        form = BookingForm()

    if request.method == "POST" and form.is_valid():
        quantity = form.cleaned_data["quantity"]

        # Check availability and save in one transaction.
        # SQLite's IMMEDIATE setting makes another writer wait.
        with transaction.atomic():
            event = get_object_or_404(Event, pk=pk)

            # Only confirmed bookings use seats.
            booked_seats = sum(
                booking.quantity
                for booking in Booking.objects.filter(
                    event=event,
                    status=Booking.Status.CONFIRMED,
                )
            )

            # Prevent another confirmed booking by the same user.
            already_booked = Booking.objects.filter(
                event=event,
                user=request.user,
                status=Booking.Status.CONFIRMED,
            ).exists()

            if event.starts_at <= timezone.now():
                form.add_error(
                    None,
                    "Booking has closed for this event.",
                )
            elif already_booked:
                form.add_error(
                    None,
                    "You already booked this event.",
                )
            elif quantity > event.capacity - booked_seats:
                form.add_error(
                    "quantity",
                    "Not enough seats available.",
                )
            else:
                # Store the customer and price at booking time.
                booking = form.save(commit=False)
                booking.event = event
                booking.user = request.user
                booking.unit_price = event.ticket_price

                # Confirm the reservation, not a payment.
                booking.status = Booking.Status.CONFIRMED
                booking.save()

                # Send email only after the transaction succeeds.
                # If the transaction rolls back, this will not run.
                transaction.on_commit(
                    partial(send_confirmation_safely, booking)
                )

                return redirect("event_detail", pk=event.pk)

    return render(
        request,
        "eventapp/booking_form.html",
        {
            "form": form,
            "event": event,
        },
    )


@login_required
def my_bookings(request):
    # Users see only their own bookings.
    bookings = Booking.objects.filter(
        user=request.user
    ).order_by("-created_at")

    return render(
        request,
        "eventapp/my_bookings.html",
        {"bookings": bookings},
    )


@login_required
@transaction.atomic
def booking_cancel(request, pk):
    # Users can cancel only their own bookings.
    booking = get_object_or_404(
        Booking,
        pk=pk,
        user=request.user,
    )

    error = None

    if request.method == "POST":
        # Block cancellation after attendance has been recorded.
        if booking.checked_in_at is not None:
            error = "A checked-in booking cannot be cancelled."

        # Cancellation also closes when the event starts.
        elif booking.event.starts_at <= timezone.now():
            error = "Cancellation has closed for this event."

        else:
            # Preserve the booking history.
            booking.status = Booking.Status.CANCELLED
            booking.save(update_fields=["status"])

            return redirect("my_bookings")

    return render(
        request,
        "eventapp/booking_confirm_cancel.html",
        {
            "booking": booking,
            "error": error,
        },
    )


@login_required
def organizer_dashboard(request):
    # Show only this organizer's events.
    events = Event.objects.filter(
        organizer=request.user
    ).order_by("starts_at", "pk")

    # Include bookings only for those events.
    bookings = Booking.objects.filter(
        event__organizer=request.user
    )

    confirmed_bookings = bookings.filter(
        status=Booking.Status.CONFIRMED
    )

    total_tickets = sum(
        booking.quantity
        for booking in confirmed_bookings
    )

    return render(
        request,
        "eventapp/organizer_dashboard.html",
        {
            "events": events,
            "total_events": events.count(),
            "total_bookings": confirmed_bookings.count(),
            "total_tickets": total_tickets,
            "total_cancellations": bookings.filter(
                status=Booking.Status.CANCELLED
            ).count(),
        },
    )


@login_required
def booking_ticket(request, pk):
    # Only the booking owner can open this ticket page.
    booking = get_object_or_404(
        Booking.objects.select_related("event", "user"),
        pk=pk,
        user=request.user,
    )

    return render(
        request,
        "eventapp/booking_ticket.html",
        {"booking": booking},
    )


@login_required
def booking_ticket_qr(request, pk):
    # Only the owner of a confirmed booking can access its QR.
    booking = get_object_or_404(
        Booking,
        pk=pk,
        user=request.user,
        status=Booking.Status.CONFIRMED,
    )

    # Encode the ticket code without personal information.
    qr_image = qrcode.make(str(booking.ticket_code))

    # Generate the PNG in memory.
    image_buffer = BytesIO()
    qr_image.save(image_buffer, format="PNG")

    response = HttpResponse(
        image_buffer.getvalue(),
        content_type="image/png",
    )

    # Avoid caching this protected image response.
    response["Cache-Control"] = "private, no-store"

    return response


@login_required
def ticket_check_in(request, pk):
    # Only this event's organizer can access check-in.
    event = get_object_or_404(
        Event,
        pk=pk,
        organizer=request.user,
    )

    checked_booking = None

    if request.method == "POST":
        form = TicketCheckInForm(request.POST)

        if form.is_valid():
            ticket_code = form.cleaned_data["ticket_code"]

            # Check and update together to prevent repeat check-ins.
            # This uses the project's SQLite IMMEDIATE setting.
            with transaction.atomic():
                # Recheck ownership inside the transaction.
                event = get_object_or_404(
                    Event,
                    pk=pk,
                    organizer=request.user,
                )

                # The ticket must belong to the selected event.
                booking = Booking.objects.filter(
                    event=event,
                    ticket_code=ticket_code,
                ).first()

                if booking is None:
                    form.add_error(
                        "ticket_code",
                        "No matching ticket was found for this event.",
                    )

                elif booking.status != Booking.Status.CONFIRMED:
                    form.add_error(
                        "ticket_code",
                        "This booking is not confirmed and cannot be checked in.",
                    )

                elif booking.checked_in_at is not None:
                    form.add_error(
                        "ticket_code",
                        "This booking has already been checked in.",
                    )

                else:
                    # Record attendance for everyone in this booking.
                    booking.checked_in_at = timezone.now()
                    booking.save(update_fields=["checked_in_at"])

                    checked_booking = booking

            # Show an empty form after success for the next ticket.
            if checked_booking is not None:
                form = TicketCheckInForm()
    else:
        form = TicketCheckInForm()

    return render(
        request,
        "eventapp/ticket_check_in.html",
        {
            "event": event,
            "form": form,
            "checked_booking": checked_booking,
        },
    )