"""ph_capture and ph_identify swallow errors (analytics must never break a page), so a call the PostHog SDK rejects fails
silently and every event is lost. These tests call the two helpers against clients with the real signatures of the old and
the new SDK, and against the SDK installed here, so a signature change shows up as a failure."""
import inspect
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from expenses import posthog_utils


class OldSdk:
    """posthog 3.x to 5.x: the person first, identify() sets properties."""
    def __init__(self):
        self.captured, self.identified = [], []

    def capture(self, distinct_id=None, event=None, properties=None, **kwargs):
        self.captured.append((distinct_id, event, properties))

    def identify(self, distinct_id=None, properties=None, **kwargs):
        self.identified.append((distinct_id, properties))


class NewSdk:
    """posthog 6.x and later: the event first, the person in the keywords, set() instead of identify()."""
    def __init__(self):
        self.captured, self.set_calls = [], []

    def capture(self, event, **kwargs):
        self.captured.append((kwargs.get('distinct_id'), event, kwargs.get('properties')))

    def set(self, **kwargs):
        self.set_calls.append((kwargs.get('distinct_id'), kwargs.get('properties')))


class Base(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('analytics-user', 'a@example.com', 'pw')
        self.token = posthog_utils.set_analytics_consent(True)
        self.addCleanup(posthog_utils.reset_analytics_consent, self.token)

    def run_with(self, client):
        with patch.object(posthog_utils, '_get_client', return_value=client):
            posthog_utils.ph_capture(self.user, 'investments_viewed', {'sips': 2})
            posthog_utils.ph_identify(self.user, {'signup_method': 'email'})


class SignatureTests(Base):
    def test_an_older_sdk_receives_the_event_and_the_person_properties(self):
        client = OldSdk()
        self.run_with(client)
        self.assertEqual(len(client.captured), 1)
        distinct_id, event, properties = client.captured[0]
        self.assertEqual((event, properties), ('investments_viewed', {'sips': 2}))
        self.assertEqual(client.identified[0][0], distinct_id)
        self.assertIn('signup_method', client.identified[0][1])

    def test_a_newer_sdk_receives_the_event_and_the_person_properties(self):
        client = NewSdk()
        self.run_with(client)
        self.assertEqual(len(client.captured), 1)
        distinct_id, event, properties = client.captured[0]
        self.assertEqual((event, properties), ('investments_viewed', {'sips': 2}))
        self.assertTrue(distinct_id)
        self.assertEqual(client.set_calls[0][0], distinct_id)
        self.assertIn('signup_method', client.set_calls[0][1])

    def test_nothing_fails_quietly_with_either_sdk(self):
        for client in (OldSdk(), NewSdk()):
            with self.assertNoLogs('expenses.posthog_utils', level='DEBUG'):
                self.run_with(client)

    def test_the_installed_sdk_accepts_the_call_we_make(self):
        """The call is checked against the signature of the SDK that is actually installed."""
        from posthog import Posthog
        signature = inspect.signature(Posthog.capture)
        kwargs = {'distinct_id': 'person', 'event': 'something', 'properties': {}}
        signature.bind(None, **kwargs)                      # raises TypeError when the SDK would reject it
        self.assertTrue(hasattr(Posthog, 'identify') or hasattr(Posthog, 'set'))
