import json
import logging
import os
from email.mime.image import MIMEImage
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import EmailMultiAlternatives
from django.core.management.base import BaseCommand
from django.template import Context as DjangoContext
from django.template import Template
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Sends highly customizable product/feature update emails (HTML or templates) with dynamic context to users'

    def add_arguments(self, parser):
        parser.add_argument('--subject', type=str, required=True, help='Subject line of the email')
        parser.add_argument('--heading', type=str, help='Heading inside the email body (optional)')
        parser.add_argument('--body', type=str, help='Main text content of the update (optional)')
        parser.add_argument('--template', type=str, default='email/product_update.html', help='Django template path to render')
        parser.add_argument('--html-file', type=str, help='Local path to a raw HTML file to send (overrides --template)')
        parser.add_argument('--context', type=str, help='JSON string or path to a JSON file containing template variables')
        parser.add_argument('--image', type=str, help='Local file path to embed inline, or remote hosted image URL')
        parser.add_argument('--cta-link', type=str, help='URL for the main action button')
        parser.add_argument('--cta-text', type=str, default='Try it now', help='Text for the action button')
        parser.add_argument('--user-id', type=int, help='Send to a specific user ID only')
        parser.add_argument('--user-email', type=str, help='Send to a specific user email only')
        parser.add_argument('--username', type=str, help='Send to a specific username only')
        parser.add_argument('--dry-run', action='store_true', help='Perform a dry run (logs output, does not send)')

    def handle(self, *args, **options):
        subject = options['subject']
        heading = options.get('heading')
        body = options.get('body')
        template_path = options['template']
        html_file = options.get('html_file')
        context_arg = options.get('context')
        image_path_or_url = options.get('image')
        cta_link = options.get('cta_link')
        cta_text = options.get('cta_text')
        user_id = options.get('user_id')
        user_email = options.get('user_email')
        username = options.get('username')
        dry_run = options['dry_run']

        # Parse Custom Context
        custom_context = {}
        if context_arg:
            if os.path.exists(context_arg):
                with open(context_arg, 'r', encoding='utf-8') as f:
                    try:
                        custom_context = json.load(f)
                    except json.JSONDecodeError as e:
                        self.stdout.write(self.style.ERROR(f"Failed to parse JSON file {context_arg}: {e}"))
                        return
            else:
                try:
                    custom_context = json.loads(context_arg)
                except json.JSONDecodeError as e:
                    self.stdout.write(self.style.ERROR(f"Failed to parse JSON string: {e}"))
                    return

        # Load Raw HTML File if provided
        raw_html_template = None
        if html_file:
            if not os.path.exists(html_file):
                self.stdout.write(self.style.ERROR(f"HTML file not found at: {html_file}"))
                return
            with open(html_file, 'r', encoding='utf-8') as f:
                raw_html_template = f.read()

        # Determine target users
        users = User.objects.filter(is_active=True).exclude(email="")
        if user_id:
            users = users.filter(id=user_id)
            if not users.exists():
                self.stdout.write(self.style.ERROR(f"User with ID {user_id} not found."))
                return
        elif user_email:
            users = users.filter(email__iexact=user_email)
            if not users.exists():
                self.stdout.write(self.style.ERROR(f"Active user with email '{user_email}' not found."))
                return
        elif username:
            users = users.filter(username__iexact=username)
            if not users.exists():
                self.stdout.write(self.style.ERROR(f"Active user with username '{username}' not found."))
                return
        
        self.stdout.write(f"Targeting {users.count()} users...")

        # Setup image handling variables
        image_cid = None
        image_url = None
        is_local_image = False
        img_data = None
        img_filename = None

        if image_path_or_url:
            if image_path_or_url.startswith(('http://', 'https://')):
                image_url = image_path_or_url
                self.stdout.write(f"Using hosted remote image: {image_url}")
            else:
                if not os.path.exists(image_path_or_url):
                    self.stdout.write(self.style.ERROR(f"Local image file not found at: {image_path_or_url}"))
                    return
                
                is_local_image = True
                image_cid = "promo_image_cid"
                img_filename = os.path.basename(image_path_or_url)
                self.stdout.write(f"Preparing to embed local image: {image_path_or_url} (CID: {image_cid})")
                
                with open(image_path_or_url, 'rb') as f:
                    img_data = f.read()

        # Get domain from settings.SITE_URL
        site_url = getattr(settings, 'SITE_URL', 'https://trackmyrupee.com')
        domain = urlparse(site_url).netloc or 'trackmyrupee.com'

        sent_count = 0
        for user in users:
            # Build Context dictionary
            context_data = {
                'user': user,
                'subject': subject,
                'heading': heading,
                'body_text': body,
                'image_cid': image_cid,
                'image_url': image_url,
                'cta_link': cta_link,
                'cta_text': cta_text,
                'domain': domain,
            }
            # Merge custom context parameters
            context_data.update(custom_context)

            # Compile / Render the HTML content
            if raw_html_template:
                # Compile raw HTML using Django's template engine to allow template tags/variables
                template_obj = Template(raw_html_template)
                html_message = template_obj.render(DjangoContext(context_data))
            else:
                # Use standard Django template path loader
                html_message = render_to_string(template_path, context_data)

            # Build plain text message fallback
            plain_text_message = f"{heading or subject}\n\nHi {user.username},\n\n{body or ''}\n\nVisit: {cta_link if cta_link else 'TrackMyRupee'}"

            if dry_run:
                self.stdout.write(f"[Dry Run] Would send email to: {user.email} (Subject: '{subject}')")
                sent_count += 1
                continue

            try:
                msg = EmailMultiAlternatives(
                    subject=subject,
                    body=plain_text_message,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    to=[user.email]
                )
                msg.attach_alternative(html_message, "text/html")

                # Attach inline image if it is local
                if is_local_image and img_data:
                    msg_img = MIMEImage(img_data)
                    msg_img.add_header('Content-ID', f'<{image_cid}>')
                    msg_img.add_header('Content-Disposition', 'inline', filename=img_filename)
                    msg.attach(msg_img)

                sent_result = msg.send()
                if sent_result > 0:
                    sent_count += 1
                    self.stdout.write(self.style.SUCCESS(f"Sent email to {user.email}"))
                else:
                    self.stdout.write(self.style.WARNING(f"Failed to send email to {user.email} (Backend returned 0)"))
            except Exception as e:
                logger.error(f"Failed to send update email to {user.email}: {e}")
                self.stdout.write(self.style.ERROR(f"Failed to send to {user.email}: {e}"))

        self.stdout.write(self.style.SUCCESS(f"Finished. Sent {sent_count} emails."))
