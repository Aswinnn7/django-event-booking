import uuid

from django.db import migrations, models


def assign_ticket_codes(apps, schema_editor):
    # Use the Booking model as it exists during this migration.
    Booking = apps.get_model("event_app", "Booking")
    database = schema_editor.connection.alias

    # Generate a different code for each existing booking.
    for booking in Booking.objects.using(database).all().iterator():
        booking.ticket_code = uuid.uuid4()
        booking.save(
            using=database,
            update_fields=["ticket_code"],
        )


class Migration(migrations.Migration):

    dependencies = [
        ("event_app", "0005_booking"),
    ]

    operations = [
        # Record check-in time when attendance is confirmed later.
        migrations.AddField(
            model_name="booking",
            name="checked_in_at",
            field=models.DateTimeField(blank=True, null=True),
        ),

        # Add the code field without a default for existing rows.
        migrations.AddField(
            model_name="booking",
            name="ticket_code",
            field=models.UUIDField(editable=False, null=True),
        ),

        # Give every existing booking its own code.
        migrations.RunPython(
            assign_ticket_codes,
            reverse_code=migrations.RunPython.noop,
        ),

        # New bookings automatically receive a random code.
        migrations.AlterField(
            model_name="booking",
            name="ticket_code",
            field=models.UUIDField(
                default=uuid.uuid4,
                editable=False,
                null=True,
            ),
        ),
    ]