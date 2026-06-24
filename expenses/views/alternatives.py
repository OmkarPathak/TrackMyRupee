from django.views.generic import TemplateView
from django.shortcuts import redirect
from django.utils.translation import gettext_lazy as _

class AlternativePageView(TemplateView):
    template_name = 'alternative_landing.html'
    competitor = None

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect('home')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        competitors_data = {
            'axio': {
                'competitor_id': 'axio',
                'competitor_name': 'Axio',
                'meta_title': _('Best Axio Alternative in India (Privacy-First) - TrackMyRupee'),
                'meta_description': _("Looking for an Axio alternative? Track your expenses manually without SMS permission. TrackMyRupee is the best secure manual budgeting app in India."),
                'meta_keywords': _("Axio alternative, expense tracker India without SMS permission, best secure manual budgeting app India, personal finance tracker India"),
                'h1_title': _('The Secure, Privacy-First Axio Alternative'),
                'subtitle': _('Take back control of your financial data. Track your budget manually, securely, and without giving any app permission to read your personal messages.'),
                'problem_title': _('The Privacy Concern with SMS-based Trackers'),
                'problem_text': _("Axio automatically tracks your transactions by scanning your SMS inbox. While convenient, this requires giving the app full reading permissions to all your text messages. This means personal conversations, bank OTPs, and private messages are accessible. In a world where data leaks are common, sharing your SMS inbox is a significant privacy risk."),
                'solution_title': _('The TrackMyRupee Difference: Zero Permissions Required'),
                'solution_text': _("TrackMyRupee is built from the ground up to respect your privacy. We never ask for SMS reading permissions, bank login credentials, or email access. You are in complete control: you only track what you choose to log. With our 3-second rapid entry and automated recurring transaction logs, manual budgeting is faster and safer than ever."),
                'faqs': [
                    {
                        'question': _('Why should I choose a manual expense tracker over Axio?'),
                        'answer': _('Manual tracking gives you complete control and ensures privacy. Axio reads your personal SMS messages to automate tracking, which exposes your private data. TrackMyRupee does not require any SMS permissions, keeping your financial life completely secure.')
                    },
                    {
                        'question': _('Is manual logging time-consuming compared to Axio\'s auto-tracking?'),
                        'answer': _('Not at all. With our rapid 3-second logging and automatic recurring transactions (like rent, subscriptions, and monthly SIPs), most users spend less than 30 seconds a day managing their budget.')
                    },
                    {
                        'question': _('Can I import my existing data if I switch?'),
                        'answer': _('Yes! TrackMyRupee supports importing your transactions via standard Excel and CSV files. You can export your data from your bank or Axio and upload it directly to get started instantly.')
                    }
                ],
                'keywords_used': [
                    _('Expense tracker India without SMS permission'),
                    _('Best secure manual budgeting app India'),
                    _('Privacy-First Alternative')
                ]
            },
            'walnut': {
                'competitor_id': 'walnut',
                'competitor_name': 'Walnut',
                'meta_title': _('Best Walnut Alternative (No SMS Permission) - TrackMyRupee'),
                'meta_description': _("Discover the best Walnut alternative with no SMS reading permission. Secure your financial data with TrackMyRupee, India's premium manual expense tracker."),
                'meta_keywords': _("Walnut alternative no sms, expense tracker India without SMS permission, best secure manual budgeting app India, privacy expense tracker"),
                'h1_title': _('Best Walnut Alternative: Zero SMS Permissions'),
                'subtitle': _('Tired of apps reading your personal financial SMS messages? TrackMyRupee is the ultimate manual expense tracker built for complete data privacy.'),
                'problem_title': _('Why SMS Reading Apps Pose a Risk'),
                'problem_text': _("Like other automated apps, Walnut reads your incoming SMS alerts to extract transaction details. This gives the app access to highly sensitive data, including account balances, transaction histories, and OTP codes. For users seeking true financial privacy, letting a third-party app monitor their messaging inbox is an unacceptable security compromise."),
                'solution_title': _('TrackMyRupee: Secure Manual Budgeting'),
                'solution_text': _("TrackMyRupee requires absolutely zero invasive device permissions. We don't read your messages, scrape your bank statements, or sell your financial behavior to advertisers. Supported by transparent subscriptions and a fully open-source codebase, we are built solely to provide a secure space for your financial planning."),
                'faqs': [
                    {
                        'question': _('Why is TrackMyRupee the best Walnut alternative for Indians?'),
                        'answer': _('TrackMyRupee is designed specifically for the Indian market, supporting English, Hindi, and Marathi, along with native Rupee formatting. It provides a clean, premium dashboard without the constant spam, ads, or intrusive SMS access associated with Walnut.')
                    },
                    {
                        'question': _('Does TrackMyRupee show a detailed view of my spending categories?'),
                        'answer': _('Yes, TrackMyRupee comes with clean, beautiful charts and AI-driven insights that analyze your manual entries. You get a complete breakdown of where your money goes, helping you spot spending leaks easily.')
                    },
                    {
                        'question': _('Can I use TrackMyRupee offline?'),
                        'answer': _('Yes! TrackMyRupee is a Progressive Web App (PWA). You can install it on your Android or iPhone home screen. It works offline, launching in under 2 seconds, allowing you to log transactions even without an internet connection.')
                    }
                ],
                'keywords_used': [
                    _('Expense tracker India without SMS permission'),
                    _('Best secure manual budgeting app India'),
                    _('Zero SMS reading permission')
                ]
            },
            'indmoney': {
                'competitor_id': 'indmoney',
                'competitor_name': 'INDmoney',
                'meta_title': _('Best INDmoney Alternative for Complete Privacy - TrackMyRupee'),
                'meta_description': _("Looking for an INDmoney alternative that respects your privacy? Track your net worth, mutual funds, and assets manually without linking your email or bank accounts."),
                'meta_keywords': _("INDmoney alternative privacy, best secure manual budgeting app India, track net worth manually, secure personal finance tracker India"),
                'h1_title': _('Privacy-First INDmoney Alternative'),
                'subtitle': _('Track your net worth, stocks, mutual funds, and loans manually. No Gmail scraping, no bank account linking, and absolutely no data sharing.'),
                'problem_title': _('The Gmail Scraping & Bank Account Access Concern'),
                'problem_text': _("INDmoney tracks your mutual funds, stock investments, and bank balances by linking to your Gmail account to scan CAS statements and trade notifications, or by using direct credentials via aggregators. This gives them deep access to your inbox and financial credentials. Many privacy-conscious users find this level of data aggregation highly intrusive and risky."),
                'solution_title': _('Manual Net Worth & Investment Tracking'),
                'solution_text': _("TrackMyRupee offers a unified net worth tracker where you can safely log your assets (savings, FDs, mutual funds, PPF) and liabilities (credit cards, loans) manually. You get a beautiful, aggregated net worth number and historical trajectory without exposing your email passwords, investment portfolios, or bank login details to external scrapers."),
                'faqs': [
                    {
                        'question': _('How does TrackMyRupee compare to INDmoney for wealth tracking?'),
                        'answer': _('INDmoney automatically tracks wealth by scanning your emails and linking your accounts. TrackMyRupee lets you log and track your mutual funds, stocks, fixed deposits, and loans manually. You get identical net worth analytics and historical trends, but with 100% data privacy.')
                    },
                    {
                        'question': _('Do I need to link my Gmail to track my mutual funds?'),
                        'answer': _('No. TrackMyRupee will never ask for Gmail access. You can update your mutual fund and asset balances manually at your own pace (e.g., once a month) to keep your net worth dashboard updated.')
                    },
                    {
                        'question': _('Is my data secure on TrackMyRupee?'),
                        'answer': _('Extremely secure. All data is encrypted in transit using HTTPS and at rest using bank-grade AES-256 encryption. Best of all, TrackMyRupee is fully open source on GitHub, meaning our codebase is fully transparent and verified by the community.')
                    }
                ],
                'keywords_used': [
                    _('Best secure manual budgeting app India'),
                    _('INDmoney alternative privacy'),
                    _('No Gmail or Bank scraping')
                ]
            }
        }
        
        # Default to axio if competitor is not set or not found
        comp_info = competitors_data.get(self.competitor, competitors_data['axio'])
        context.update(comp_info)
        return context
