from django.urls import path
from . import views


urlpatterns = [
    # Home page
    path("", views.home, name="home"),

    # Create event
    path("events/create/", views.event_create, name="event_create"),

    # Event details
    path("events/<int:pk>/", views.event_detail, name="event_detail"),

    # Update event
    path("events/<int:pk>/edit/", views.event_update, name="event_update"),

    # Delete event
    path("events/<int:pk>/delete/", views.event_delete, name="event_delete"),

    # Book event
    path("events/<int:pk>/book/", views.booking_create, name="booking_create"),

    # Show logged-in user's bookings
    path("my-bookings/", views.my_bookings, name="my_bookings"),

    # Cancel booking
    path("bookings/<int:pk>/cancel/", views.booking_cancel, name="booking_cancel"),

    # Organizer dashboard
    path("organizer/dashboard/", views.organizer_dashboard, name="organizer_dashboard"),

    # Display the logged-in user's ticket
    path("bookings/<int:pk>/ticket/", views.booking_ticket, name="booking_ticket"),

    # Generate the protected QR ticket image
    path("bookings/<int:pk>/ticket/qr/", views.booking_ticket_qr, name="booking_ticket_qr"),

    # Organizer checks tickets for their own event.
    path("events/<int:pk>/check-in/", views.ticket_check_in, name="ticket_check_in"),
]