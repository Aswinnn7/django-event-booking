from django import forms

from .models import Event, Booking


class EventForm(forms.ModelForm):
    # Django checks that uploaded files contain a valid image.
    image = forms.ImageField(
        required=False,
        label="Event cover image",
        help_text=(
            "Optional. Upload a JPEG, PNG or WebP image "
            "up to 5 MB. A landscape image works best."
        ),
        widget=forms.ClearableFileInput(
            attrs={
                "accept": "image/jpeg,image/png,image/webp",
            }
        ),
    )

    class Meta:
        model = Event
        fields = [
            "title",
            "description",
            "image",
            "location",
            "starts_at",
            "ticket_price",
            "capacity",
        ]

    def clean_image(self):
        image = self.cleaned_data.get("image")

        # Allow no image or removal of an existing image.
        if not image:
            return image

        # When editing without a new upload, keep the existing image.
        if not self.files.get(self.add_prefix("image")):
            return image

        # Limit new uploads to 5 MB.
        if image.size > 5 * 1024 * 1024:
            raise forms.ValidationError(
                "The image must be 5 MB or smaller."
            )

        # Check the actual image format identified by Pillow.
        # The filename and browser-provided type are not enough.
        if image.image.format not in {"JPEG", "PNG", "WEBP"}:
            raise forms.ValidationError(
                "Upload a JPEG, PNG or WebP image."
            )

        return image


class BookingForm(forms.ModelForm):
    class Meta:
        model = Booking
        fields = ["quantity"]


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