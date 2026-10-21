from django import forms
from .models import Event, Booking


class EventForm(forms.ModelForm):
    class Meta:
        model = Event
        fields = [
            "title",
            "description",
            "location",
            "starts_at",
            "ticket_price",
            "capacity",
        ]

class BookingForm(forms.ModelForm):
    class Meta:
        model=Booking
        fields=["quantity"]

class EventFilterForm(forms.Form):
    # All filters are optional.
    location = forms.CharField(
        required=False,
        label="Location",
        widget=forms.TextInput(
            attrs={"placeholder": "Enter a city or venue"}
        ),
    )

    date = forms.DateField(
        required=False,
        label="Event date",
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    price = forms.ChoiceField(
        required=False,
        label="Ticket price",
        choices=[
            ("", "All prices"),
            ("free", "Free"),
            ("paid", "Paid"),
        ],
    )

class TicketCheckInForm(forms.Form):
    # Accept only a correctly formatted UUID ticket code.
    ticket_code = forms.UUIDField(
        label="Ticket code",
        widget=forms.TextInput(
            attrs={
                "placeholder": "Scan or paste the ticket code",
                "autocomplete": "off",
            }
        ),
    )