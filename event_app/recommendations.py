from django.utils import timezone
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .models import Event, Booking


def get_recommended_events(user, limit=3):
    # Return no suggestions if none are requested.
    if limit <= 0:
        return []

    # Collect events this user has confirmed bookings for.
    # Past bookings can still tell us about their interests.
    booked_events = []

    if user.is_authenticated:
        booked_events = list(
            Event.objects.filter(
                bookings__user=user,
                bookings__status=Booking.Status.CONFIRMED,
            )
            .distinct()
            .order_by("pk")
        )

    booked_ids = [event.pk for event in booked_events]

    # Suggest only upcoming events the user has not booked.
    upcoming_events = (
        Event.objects.filter(starts_at__gt=timezone.now())
        .exclude(pk__in=booked_ids)
        .order_by("starts_at", "pk")
    )

    # Do not recommend sold-out events.
    candidates = [
        event
        for event in upcoming_events
        if event.remaining_seats > 0
    ]

    if not candidates:
        return []

    # New users and logged-out visitors have no booking history.
    # Show the nearest upcoming available events instead.
    if not booked_events:
        return candidates[:limit]

    # Compare event titles and descriptions.
    all_events = booked_events + candidates

    documents = [
        f"{event.title} {event.description}"
        for event in all_events
    ]

    vectorizer = TfidfVectorizer(stop_words="english")

    try:
        # Convert the text into numerical representations.
        text_vectors = vectorizer.fit_transform(documents)
    except ValueError as error:
        # Text containing only common words or punctuation
        # may leave nothing useful to compare.
        if "empty vocabulary" not in str(error):
            raise

        return candidates[:limit]

    history_count = len(booked_events)

    # Split the vectors into booking history and candidates.
    history_vectors = text_vectors[:history_count]
    candidate_vectors = text_vectors[history_count:]

    # Compare each candidate with every previously booked event.
    similarities = cosine_similarity(
        candidate_vectors,
        history_vectors,
    )

    # Use each candidate's strongest match with the user's history.
    scores = similarities.max(axis=1)

    # Higher similarity comes first.
    # Equal scores keep the original upcoming-date order.
    ranked_indices = sorted(
        range(len(candidates)),
        key=lambda index: float(scores[index]),
        reverse=True,
    )

    return [
        candidates[index]
        for index in ranked_indices[:limit]
    ]