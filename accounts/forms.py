from django import forms
from django.contrib.auth.forms import UserCreationForm

from .models import User


class RegisterForm(UserCreationForm):
    # Require a correctly formatted email address.
    email = forms.EmailField(
        required=True,
        label="Email address",
        help_text="Booking confirmations will be sent to this address.",
        widget=forms.EmailInput(
            attrs={"autocomplete": "email"}
        ),
    )

    class Meta:
        model = User
        fields = ["username", "email"]