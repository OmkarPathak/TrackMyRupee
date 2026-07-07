import base64
import json

import dns.flags
import dns.message
import dns.rdatatype
from django.test import Client, TestCase, override_settings
from django.urls import reverse


@override_settings(RECAPTCHA_PUBLIC_KEY=None, RECAPTCHA_PRIVATE_KEY=None)
class StaticPageTest(TestCase):
    def setUp(self):
        self.client = Client()

    def test_landing_page(self):
        url = reverse('landing')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Link', response)
        self.assertIn('rel="service-doc"', response['Link'])

    def test_pricing_page(self):
        url = reverse('pricing')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_contact_form_submission(self):
        url = reverse('contact')
        data = {
            'name': 'Test',
            'email': 'test@example.com',
            'subject': 'Hello',
            'message': 'This is a test message with sufficient length.',
            'website': '' # Honeypot
        }
        # Assuming email backend is setup for testing else it might fail or send real email?
        # Usually tests use locmem backend.
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)

    def test_demo_login(self):
        from django.contrib.auth.models import User
        # Create demo user
        User.objects.create_user(username='demo', password='password')
        url = reverse('demo_login')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        # Check logged in
        self.assertIn('_auth_user_id', self.client.session)

    def test_contact_page(self):
        url = reverse('contact')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_robots_txt(self):
         url = reverse('robots_txt')
         response = self.client.get(url)
         self.assertEqual(response.status_code, 200)
         self.assertTrue(response['Content-Type'].startswith('text/plain'))
         self.assertEqual(response['Access-Control-Allow-Origin'], '*')
         self.assertIn("Content-Signal: ai-train=no, search=yes, ai-input=no", response.content.decode('utf-8'))

    def test_llms_txt(self):
         response = self.client.get('/llms.txt')
         self.assertEqual(response.status_code, 200)
         self.assertTrue(response['Content-Type'].startswith('text/plain'))
         self.assertEqual(response['Access-Control-Allow-Origin'], '*')
         self.assertIn('# TrackMyRupee', response.content.decode('utf-8'))

    def test_doh_json_get(self):
        # 1. Test JSON GET queries for SVCB
        url = reverse('dns-query-short')
        response = self.client.get(url, {'name': '_a2a._agents.localhost', 'type': 'SVCB'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/dns-json')
        data = json.loads(response.content.decode('utf-8'))
        self.assertEqual(data['Status'], 0)
        self.assertTrue(data['AD'])
        self.assertEqual(len(data['Answer']), 1)
        self.assertEqual(data['Answer'][0]['type'], 64)
        self.assertIn('alpn=a2a', data['Answer'][0]['data'])
        self.assertIn('localhost.', data['Answer'][0]['data'])

    def test_doh_json_post(self):
        # 2. Test JSON POST queries for HTTPS
        url = reverse('dns-query-wellknown')
        post_data = {'name': '_index._agents.example.com', 'type': 'HTTPS'}
        response = self.client.post(
            url,
            json.dumps(post_data),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/dns-json')
        data = json.loads(response.content.decode('utf-8'))
        self.assertEqual(data['Status'], 0)
        self.assertEqual(len(data['Answer']), 1)
        self.assertEqual(data['Answer'][0]['type'], 65)
        self.assertIn('example.com.', data['Answer'][0]['data'])

    def test_doh_binary_get(self):
        # 3. Test Binary GET query using base64url encoding
        msg = dns.message.make_query('_api-catalog._agents.localhost', dns.rdatatype.SVCB)
        wire = msg.to_wire()
        dns_param = base64.urlsafe_b64encode(wire).decode('utf-8').rstrip('=')
        
        url = reverse('dns-query-short-slash')
        response = self.client.get(url, {'dns': dns_param})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/dns-message')
        
        res_msg = dns.message.from_wire(response.content)
        self.assertTrue(res_msg.flags & dns.flags.AD)
        self.assertEqual(len(res_msg.answer), 1)
        rrset = res_msg.answer[0]
        self.assertEqual(rrset.rdtype, dns.rdatatype.SVCB)
        self.assertIn('localhost.', rrset[0].to_text())

    def test_doh_binary_post(self):
        # 4. Test Binary POST query with raw dns message body
        msg = dns.message.make_query('_service-desc._agents.trackmyrupee.com', dns.rdatatype.HTTPS)
        wire = msg.to_wire()
        
        url = reverse('dns-query-wellknown-slash')
        response = self.client.post(
            url,
            wire,
            content_type='application/dns-message'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/dns-message')
        
        res_msg = dns.message.from_wire(response.content)
        self.assertTrue(res_msg.flags & dns.flags.AD)
        self.assertEqual(len(res_msg.answer), 1)
        rrset = res_msg.answer[0]
        self.assertEqual(rrset.rdtype, dns.rdatatype.HTTPS)
        self.assertIn('trackmyrupee.com.', rrset[0].to_text())

    def test_markdown_negotiation(self):
        url = reverse('landing')
        
        # Request without text/markdown (default browser request)
        response_html = self.client.get(url)
        self.assertEqual(response_html.status_code, 200)
        self.assertTrue(response_html['Content-Type'].startswith('text/html'))
        self.assertIn('<html', response_html.content.decode('utf-8').lower())

        # Request with Accept: text/markdown
        response_md = self.client.get(url, HTTP_ACCEPT='text/markdown')
        self.assertEqual(response_md.status_code, 200)
        self.assertEqual(response_md['Content-Type'], 'text/markdown')
        self.assertIn('X-Markdown-Tokens', response_md)
        self.assertIn('Vary', response_md)
        self.assertIn('Accept', response_md['Vary'])
        
        md_body = response_md.content.decode('utf-8')
        # Should not contain HTML layout tags
        self.assertNotIn('<html', md_body.lower())
        self.assertNotIn('<body', md_body.lower())
        # Should contain page content in markdown structure (e.g. headers or links)
        self.assertTrue(len(md_body) > 0)
        token_count = int(response_md['X-Markdown-Tokens'])
        self.assertEqual(token_count, len(md_body) // 4)

    def test_axio_alternative_page(self):
        url = reverse('axio-alternative')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'alternative_landing.html')
        content = response.content.decode('utf-8')
        self.assertIn('Axio', content)
        self.assertIn('Expense tracker India without SMS permission', content)
        self.assertIn('Best secure manual budgeting app India', content)

    def test_walnut_alternative_page(self):
        url = reverse('walnut-alternative-no-sms')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'alternative_landing.html')
        content = response.content.decode('utf-8')
        self.assertIn('Walnut', content)
        self.assertIn('Expense tracker India without SMS permission', content)
        self.assertIn('Best secure manual budgeting app India', content)

    def test_indmoney_alternative_page(self):
        url = reverse('indmoney-alternative-privacy')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'alternative_landing.html')
        content = response.content.decode('utf-8')
        self.assertIn('INDmoney', content)
        self.assertIn('Best secure manual budgeting app India', content)
        self.assertIn('INDmoney alternative privacy', content)

    def test_alternative_pages_redirect_authenticated_user(self):
        from django.contrib.auth.models import User
        user = User.objects.create_user(username='testuser', password='password')
        profile = user.profile
        profile.consent_granted = True
        profile.has_seen_tutorial = True
        profile.save()
        
        self.client.login(username='testuser', password='password')
        
        for url_name in ['axio-alternative', 'walnut-alternative-no-sms', 'indmoney-alternative-privacy']:
            url = reverse(url_name)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertRedirects(response, reverse('home'))



 
