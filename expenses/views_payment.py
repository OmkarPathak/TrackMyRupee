import json
import logging
from datetime import timedelta

import razorpay
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from django.contrib.auth.models import User
from django.views.generic import ListView
from django.contrib.auth.mixins import LoginRequiredMixin
from .models import PaymentHistory, SubscriptionPlan, UserProfile
from finance_tracker.plans import PLAN_DETAILS

logger = logging.getLogger(__name__)

@csrf_exempt
@login_required
def start_trial(request):
    if request.method == "POST":
        profile = request.user.profile
        if not profile.can_start_trial:
            return JsonResponse({'error': 'Trial already used or not eligible'}, status=400)
        
        # Activate 7-day Pro trial
        profile.tier = 'PRO'
        profile.subscription_end_date = timezone.now() + timedelta(days=7)
        profile.has_used_trial = True
        profile.expiry_reminder_sent = False
        profile.save()
        
        return JsonResponse({'success': True, 'message': '7-Day Pro Trial activated successfully!'})
    return JsonResponse({'error': 'Invalid method'}, status=405)

@login_required
def create_order(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            plan_type = data.get('plan_type')
            duration = data.get('duration', 'YEARLY')
            is_recurring = data.get('is_recurring', True)

            if duration not in ('MONTHLY', 'YEARLY', 'LIFETIME'):
                return JsonResponse({'error': 'Invalid duration'}, status=400)

            # Check if this is a recurring plan with a linked Razorpay Plan ID
            db_plan = SubscriptionPlan.objects.filter(tier=plan_type, duration=duration, is_active=True).first()
            
            client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))

            if is_recurring and db_plan and db_plan.razorpay_plan_id and duration in ('MONTHLY', 'YEARLY'):
                # Handle RECURRING SUBSCRIPTION
                subscription_data = {
                    'plan_id': db_plan.razorpay_plan_id,
                    'customer_notify': 1,
                    'total_count': 100, # Razorpay max limit is 100 for some plan types
                    'notes': {
                        'plan': plan_type,
                        'duration': duration,
                        'user_id': request.user.id
                    }
                }
                subscription = client.subscription.create(data=subscription_data)
                
                # Save as pending order for tracking
                PaymentHistory.objects.create(
                    user=request.user,
                    order_id=subscription['id'], # Using subscription ID here
                    amount=db_plan.price,
                    tier=plan_type,
                    duration=duration,
                    status='PENDING'
                )
                
                return JsonResponse({
                    'id': subscription['id'],
                    'type': 'SUBSCRIPTION',
                    'customer_id': request.user.profile.razorpay_customer_id
                })

            # Handle ONE-TIME ORDER (Existing logic or fallbacks)
            
            plan_info = PLAN_DETAILS.get(plan_type)
            if not plan_info:
                return JsonResponse({'error': 'Invalid plan'}, status=400)

            price_key = 'price_yearly' if duration == 'YEARLY' else 'price_monthly'
            price = plan_info.get(price_key)
            if price is None:
                 return JsonResponse({'error': 'Pricing not found for this plan'}, status=400)

            amount_in_paise = int(price * 100)
            
            order_data = {
                'amount': amount_in_paise,
                'currency': 'INR',
                'receipt': f'receipt_order_{request.user.id}_{int(timezone.now().timestamp())}',
                'notes': {
                    'plan': plan_type,
                    'duration': duration,
                    'user_id': request.user.id
                }
            }
            
            order = client.order.create(data=order_data)
            
            PaymentHistory.objects.create(
                user=request.user,
                order_id=order['id'],
                amount=price,
                tier=plan_type,
                duration=duration,
                status='PENDING'
            )

            return JsonResponse({
                **order, 
                'type': 'ORDER',
                'customer_id': request.user.profile.razorpay_customer_id
            })
        except Exception as e:
            logger.error(f"Error creating order: {e}")
            return JsonResponse({'error': str(e)}, status=500)
    return JsonResponse({'error': 'Invalid method'}, status=405)

@csrf_exempt
@login_required
def verify_payment(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            razorpay_payment_id = data.get('razorpay_payment_id')
            razorpay_signature = data.get('razorpay_signature')
            
            # Can be either order_id or subscription_id
            razorpay_order_id = data.get('razorpay_order_id')
            razorpay_subscription_id = data.get('razorpay_subscription_id')
            
            target_id = razorpay_order_id or razorpay_subscription_id

            client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
            
            # Verify Signature
            try:
                if razorpay_subscription_id:
                    params_dict = {
                        'razorpay_subscription_id': razorpay_subscription_id,
                        'razorpay_payment_id': razorpay_payment_id,
                        'razorpay_signature': razorpay_signature
                    }
                    client.utility.verify_subscription_payment_signature(params_dict)
                else:
                    params_dict = {
                        'razorpay_order_id': razorpay_order_id,
                        'razorpay_payment_id': razorpay_payment_id,
                        'razorpay_signature': razorpay_signature
                    }
                    client.utility.verify_payment_signature(params_dict)
            except razorpay.errors.SignatureVerificationError:
                PaymentHistory.objects.filter(order_id=target_id).update(status='FAILED')
                return JsonResponse({'error': 'Signature Verification Failed'}, status=400)

            # Payment Successful
            payment_record = PaymentHistory.objects.get(order_id=target_id)
            payment_record.payment_id = razorpay_payment_id
            payment_record.status = 'SUCCESS'
            payment_record.save()
            
            # Update User Subscription
            profile = request.user.profile
            profile.tier = payment_record.tier
            
            if razorpay_subscription_id:
                profile.razorpay_subscription_id = razorpay_subscription_id
            else:
                profile.razorpay_order_id = razorpay_order_id
            
            # Set end date (Initially 30/365 days, then managed by webhooks)
            days = 30 if payment_record.duration == 'MONTHLY' else 365
            if payment_record.duration == 'LIFETIME':
                profile.is_lifetime = True
            else:
                profile.subscription_end_date = timezone.now() + timedelta(days=days)
            
            profile.expiry_reminder_sent = False
            profile.save()

            return JsonResponse({'success': True})
        except PaymentHistory.DoesNotExist:
             return JsonResponse({'error': 'Order not found'}, status=404)
        except Exception as e:
            logger.error(f"Error verifying payment: {e}")
            return JsonResponse({'error': str(e)}, status=500)
    return JsonResponse({'error': 'Invalid method'}, status=405)

@csrf_exempt
def razorpay_webhook(request):
    if request.method == "POST":
        try:
            webhook_secret = getattr(settings, 'RAZORPAY_WEBHOOK_SECRET', None)
            if not webhook_secret:
                logger.error("RAZORPAY_WEBHOOK_SECRET not set")
                return JsonResponse({'status': 'error'}, status=400)

            client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
            
            # Verify Webhook Signature
            payload = request.body
            signature = request.headers.get('X-Razorpay-Signature')
            
            try:
                client.utility.verify_webhook_signature(payload.decode('utf-8'), signature, webhook_secret)
            except razorpay.errors.SignatureVerificationError:
                return JsonResponse({'status': 'invalid signature'}, status=400)

            event_data = json.loads(payload)
            event = event_data.get('event')
            
            # Handle Subscription Events
            if event == 'subscription.charged':
                sub_id = event_data['payload']['subscription']['entity']['id']
                profile = UserProfile.objects.filter(razorpay_subscription_id=sub_id).first()
                if profile:
                    # Extend subscription
                    # The payload contains current subscription details
                    current_end = event_data['payload']['subscription']['entity']['current_end']
                    profile.subscription_end_date = timezone.datetime.fromtimestamp(current_end)
                    profile.save()
                    
                    # Log to PaymentHistory for records
                    PaymentHistory.objects.create(
                        user=profile.user,
                        order_id=sub_id,
                        payment_id=event_data['payload']['payment']['entity']['id'],
                        amount=event_data['payload']['payment']['entity']['amount'] / 100,
                        tier=profile.tier,
                        duration='RECURRING',
                        status='SUCCESS'
                    )
            
            elif event in ['subscription.cancelled', 'subscription.halted']:
                 sub_id = event_data['payload']['subscription']['entity']['id']
                 profile = UserProfile.objects.filter(razorpay_subscription_id=sub_id).first()
                 if profile:
                     # Reset flag as it's now fully cancelled
                     profile.cancel_at_cycle_end = False
                     profile.save()
                     logger.info(f"Subscription confirmed cancelled for {sub_id}")

            return JsonResponse({'status': 'ok'})
        except Exception as e:
            logger.error(f"Webhook Error: {e}")
            return JsonResponse({'status': 'error'}, status=500)
    return JsonResponse({'error': 'Invalid method'}, status=405)

@csrf_exempt
@login_required
def cancel_subscription(request):
    if request.method == "POST":
        try:
            profile = request.user.profile
            sub_id = profile.razorpay_subscription_id
            
            if not sub_id:
                return JsonResponse({'error': 'No active subscription found'}, status=400)
            
            client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
            
            # Cancel at the end of the billing cycle
            # This ensures the user gets what they paid for until the expiry date
            client.subscription.cancel(sub_id, {'cancel_at_cycle_end': 1})
            
            # Update local state immediately for UI feedback
            profile.cancel_at_cycle_end = True
            profile.save()
            
            return JsonResponse({'success': True, 
                'message': 'Subscription cancelled successfully. You will have access until the end of your current cycle.'
            })
        except Exception as e:
            logger.error(f"Error cancelling subscription: {e}")
            return JsonResponse({'error': str(e)}, status=500)
    return JsonResponse({'error': 'Invalid method'}, status=405)

class PaymentHistoryView(LoginRequiredMixin, ListView):
    model = PaymentHistory
    template_name = 'expenses/payment_history.html'
    context_object_name = 'payments'
    paginate_by = 10

    def get_queryset(self):
        return PaymentHistory.objects.filter(user=self.request.user).order_by('-created_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['profile'] = self.request.user.profile
        return context
