"""The public DNS-over-HTTPS (DNS-AID) endpoint answers every well-formed query, and every malformed one with a 4xx: a
query in a class other than IN (CH, ANY) used to make it raise, so anyone could cause a 500 with one request."""
import base64
import json

import dns.message
import dns.name
import dns.rdataclass
import dns.rdatatype
from django.test import Client, TestCase

URL = '/dns-query'


def wire(name, rdtype='SVCB', rdclass='IN'):
    return dns.message.make_query(dns.name.from_text(name), rdtype, rdclass).to_wire()


class WireQueryTests(TestCase):
    def setUp(self):
        self.client = Client(raise_request_exception=False)

    def ask(self, name, rdtype='SVCB', rdclass='IN', method='post'):
        data = wire(name, rdtype, rdclass)
        if method == 'post':
            return self.client.post(URL, data=data, content_type='application/dns-message')
        return self.client.get(URL, {'dns': base64.urlsafe_b64encode(data).decode().rstrip('=')})

    def test_an_agent_name_in_the_internet_class_gets_its_record(self):
        for method in ('post', 'get'):
            response = self.ask('_mcp._agents.example.com.', method=method)
            self.assertEqual(response.status_code, 200)
            answer = dns.message.from_wire(response.content).answer
            self.assertEqual(len(answer), 1)
            self.assertEqual(answer[0].rdtype, dns.rdatatype.SVCB)
            self.assertIn('example.com.', answer[0].to_text())

    def test_a_query_in_another_class_gets_an_empty_answer_not_an_error(self):
        for rdclass in ('ANY', 'CH', 'HS'):
            for method in ('post', 'get'):
                response = self.ask('_mcp._agents.example.com.', rdclass=rdclass, method=method)
                self.assertEqual(response.status_code, 200, (rdclass, method))
                self.assertEqual(dns.message.from_wire(response.content).answer, [])

    def test_names_with_odd_characters_never_cause_a_server_error(self):
        for name in (r'_a._agents.exa\.mple.com.', r'_a._agents.exa\032mple.com.', r'_a._agents.exa\\mple.com.',
                     '_a._agents.', '_a._agents.ex"ample.com.', '_a._agents.ex;ample.com.',
                     '_a._agents.' + '.'.join(['a' * 60] * 3) + '.'):
            self.assertLess(self.ask(name).status_code, 500, name)

    def test_type_any_returns_both_records(self):
        response = self.ask('_a._agents.example.com.', rdtype=255)
        self.assertEqual({rrset.rdtype for rrset in dns.message.from_wire(response.content).answer},
                         {dns.rdatatype.SVCB, dns.rdatatype.HTTPS})

    def test_a_name_that_is_not_an_agent_name_gets_no_records(self):
        response = self.ask('example.com.', rdtype='A')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(dns.message.from_wire(response.content).answer, [])

    def test_garbage_and_oversized_bodies_are_a_client_error(self):
        self.assertEqual(self.client.post(URL, data=b'\x00\x01garbage', content_type='application/dns-message').status_code, 400)
        self.assertEqual(self.client.post(URL, data=b'x' * 3_000_000, content_type='application/dns-message').status_code, 400)

    def test_other_methods_are_refused(self):
        self.assertEqual(self.client.put(URL, data=b'x', content_type='application/dns-message').status_code, 405)


class JsonQueryTests(TestCase):
    def setUp(self):
        self.client = Client(raise_request_exception=False)

    def test_json_queries_answer_whatever_they_are_given(self):
        for params in ({'name': '_a._agents.example.com', 'type': 'SVCB'},
                       {'name': '_a._agents.example.com', 'type': '99999999999999'},
                       {'name': '_a._agents.exämple.com', 'type': 'SVCB'},
                       {'name': '_a._agents.example.com', 'type': 'NOPE'},
                       {'name': '_a._agents.' + 'a' * 300, 'type': 'SVCB'}):
            self.assertEqual(self.client.get(URL, params).status_code, 200, params)

    def test_a_json_agent_query_returns_the_record(self):
        response = self.client.get(URL, {'name': '_mcp._agents.example.com', 'type': 'SVCB'})
        self.assertEqual(json.loads(response.content)['Answer'][0]['type'], 64)     # application/dns-json is not application/json
