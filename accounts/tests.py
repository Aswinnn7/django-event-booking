from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class AccountTests(TestCase):
    def setUp(self):
        # Create a user only in the test database.
        self.password = "TestPassword123!"

        self.user = get_user_model().objects.create_user(
            username="account_test_user",
            email="account@example.com",
            password=self.password,
        )

        self.register_url = reverse("register")
        self.login_url = reverse("login")
        self.logout_url = reverse("logout")
        self.destination = reverse("my_bookings")

    def registration_data(self, **changes):
        # Helper: valid registration details.
        data = {
            "username": "new_account_user",
            "email": "newuser@example.com",
            "password1": "FreshPassword742!",
            "password2": "FreshPassword742!",
        }
        data.update(changes)
        return data

    def login_data(self, **changes):
        # Helper: valid login details.
        data = {
            "username": self.user.username,
            "password": self.password,
        }
        data.update(changes)
        return data

    def test_registration_saves_email_and_hashed_password(self):
        response = self.client.post(
            self.register_url,
            self.registration_data(),
        )

        self.assertRedirects(response, reverse("home"))

        new_user = get_user_model().objects.get(
            username="new_account_user",
        )

        self.assertEqual(new_user.email, "newuser@example.com")

        # Django must hash passwords instead of storing plain text.
        self.assertTrue(new_user.check_password("FreshPassword742!"))
        self.assertNotEqual(new_user.password, "FreshPassword742!")

    def test_registration_rejects_missing_email(self):
        # POST directly to verify server-side validation too.
        response = self.client.post(
            self.register_url,
            self.registration_data(email=""),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("email", response.context["form"].errors)

        self.assertFalse(
            get_user_model().objects.filter(
                username="new_account_user",
            ).exists()
        )

    def test_registration_rejects_invalid_email(self):
        response = self.client.post(
            self.register_url,
            self.registration_data(email="not-an-email"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("email", response.context["form"].errors)

        self.assertFalse(
            get_user_model().objects.filter(
                username="new_account_user",
            ).exists()
        )

    def test_login_without_destination_returns_home(self):
        response = self.client.post(
            self.login_url,
            self.login_data(),
        )

        self.assertRedirects(response, reverse("home"))
        self.assertEqual(
            self.client.session["_auth_user_id"],
            str(self.user.pk),
        )

    def test_login_returns_to_requested_page(self):
        # A logged-out visitor first requests a protected page.
        response = self.client.get(self.destination)

        self.assertEqual(response.status_code, 302)

        # Follow the redirect to the login form.
        login_page = self.client.get(response["Location"])

        self.assertEqual(login_page.context["next"], self.destination)

        # The form must preserve the destination in a hidden field.
        self.assertContains(
            login_page,
            (
                '<input type="hidden" name="next" '
                f'value="{self.destination}">'
            ),
            html=True,
        )

        response = self.client.post(
            self.login_url,
            self.login_data(next=self.destination),
        )

        self.assertRedirects(response, self.destination)

    def test_login_rejects_external_redirects(self):
        unsafe_destinations = [
            "https://example.org/",
            "//example.org/",
            "javascript:alert(1)",
        ]

        for destination in unsafe_destinations:
            with self.subTest(destination=destination):
                self.client.logout()

                response = self.client.post(
                    self.login_url,
                    self.login_data(next=destination),
                )

                # Login succeeds, but the unsafe destination is ignored.
                self.assertRedirects(response, reverse("home"))
                self.assertEqual(
                    self.client.session["_auth_user_id"],
                    str(self.user.pk),
                )

    def test_https_login_rejects_http_downgrade(self):
        response = self.client.post(
            self.login_url,
            self.login_data(
                next=f"http://testserver{self.destination}",
            ),
            secure=True,
        )

        # A secure login must not redirect to insecure HTTP.
        self.assertRedirects(
            response,
            reverse("home"),
            fetch_redirect_response=False,
        )

    def test_invalid_login_preserves_destination(self):
        response = self.client.post(
            self.login_url,
            self.login_data(
                password="IncorrectPassword!",
                next=self.destination,
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        self.assertEqual(response.context["next"], self.destination)

        # The user must remain logged out.
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_get_logout_does_not_end_session(self):
        self.client.force_login(self.user)

        response = self.client.get(self.logout_url)

        self.assertRedirects(response, reverse("home"))
        self.assertEqual(
            self.client.session["_auth_user_id"],
            str(self.user.pk),
        )

    def test_post_logout_ends_session(self):
        self.client.force_login(self.user)

        response = self.client.post(self.logout_url)

        self.assertRedirects(response, reverse("home"))
        self.assertNotIn("_auth_user_id", self.client.session)