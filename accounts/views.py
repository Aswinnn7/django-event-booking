from django.shortcuts import render, redirect
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.utils.http import url_has_allowed_host_and_scheme

from .forms import RegisterForm


def register(request):
    # Create an account using Django's registration validation.
    if request.method == "POST":
        form = RegisterForm(request.POST)

        if form.is_valid():
            form.save()
            return redirect("home")
    else:
        form = RegisterForm()

    return render(
        request,
        "register.html",
        {"form": form},
    )


def login_view(request):
    # Django's login_required decorator supplies ?next=...
    # Preserve it when the login form is submitted.
    next_url = (
        request.POST.get("next")
        or request.GET.get("next", "")
    )

    # Allow redirects only to this website.
    # This prevents a supplied URL sending users to another site.
    if not url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = ""

    if request.method == "POST":
        form = AuthenticationForm(request, data=request.POST)

        if form.is_valid():
            auth_login(request, form.get_user())

            # Return to the requested page, or home if none is valid.
            if next_url:
                return redirect(next_url)

            return redirect("home")
    else:
        form = AuthenticationForm(request)

    return render(
        request,
        "login.html",
        {
            "form": form,
            "next": next_url,
        },
    )


def logout_view(request):
    # Opening a link must not log someone out.
    # Logout happens only after a POST request.
    if request.method == "POST":
        auth_logout(request)

    return redirect("home")