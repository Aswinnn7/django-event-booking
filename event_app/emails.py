from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone


def send_booking_confirmation(booking):
    # Get the customer's email address.
    recipient = booking.user.email

    # Older accounts might not have an email address.
    if not recipient:
        return

    # Format the event time using the project's timezone.
    event_time = timezone.localtime(booking.event.starts_at)
    formatted_time = event_time.strftime("%d %b %Y, %I:%M %p %Z")

    # Calculate the total reservation value.
    total_price = booking.unit_price * booking.quantity

    subject = f"Booking confirmed: {booking.event.title}"

    message = (
        f"Hi {booking.user.username},\n\n"
        f"Your booking for {booking.event.title} is confirmed.\n\n"
        f"Booking reference: #{booking.pk}\n"
        f"Location: {booking.event.location}\n"
        f"Date and time: {formatted_time}\n"
        f"Number of tickets: {booking.quantity}\n"
        f"Price per ticket: INR {booking.unit_price:.2f}\n"
        f"Total reservation value: INR {total_price:.2f}\n\n"
        "Open My bookings and select View ticket to see your QR code.\n"
        "Everyone included in this booking should arrive together "
        "for check-in.\n\n"
        "This email confirms your reservation, not a payment.\n\n"
        "Thank you,\n"
        "EventBooking"
    )

    # Use the email backend configured in settings.py.
    # Gmail SMTP sends the confirmation to the customer's inbox.
    send_mail(
        subject=subject,
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[recipient],
        fail_silently=False,
    )