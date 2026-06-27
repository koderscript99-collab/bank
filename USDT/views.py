from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse
from django.utils import timezone
from django.conf import settings
from decimal import Decimal
import json, hmac, hashlib

from urllib3 import request

from .models import (
    DepositRequest, User, AdminProfile, Wallet, PaymentAccount,
    ActivationPayment, AdminDeposit, Transaction,
    WithdrawalRequest, AccountControlLog, Notification,
    PaymentDetail,SupportTicket, SupportMessage
)

from django.db import models as db_models





# ======================
# HELPERS
# ======================

def _credit_wallet(user, amount, transaction_type='admin_deposit',
                   description='', performed_by=None):
    wallet, _ = Wallet.objects.get_or_create(user=user)
    wallet.balance += Decimal(str(amount))
    wallet.save()
    Transaction.objects.create(
        user=user,
        amount=amount,
        transaction_type=transaction_type,
        status='success',
        description=description,
        performed_by=performed_by,
    )


def _notify(user, title, message):
    Notification.objects.create(user=user, title=title, message=message)


# ======================
# ADMIN DECORATOR
# ======================
import functools

def admin_required(view_func):
    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('admin-pin-login')
        if not request.user.is_admin_user:
            messages.error(request, 'Access denied.')
            return redirect('dashboard')
        return view_func(request, *args, **kwargs)
    return wrapper


# ======================
# HOME
# ======================

def admin_dashboard(request):
    return render(request, 'admin/admin-dashboard.html')




def home(request):
    if request.user.is_authenticated:
        if request.user.is_admin_user:
            return redirect('admin-dashboard')
        return redirect('dashboard')
    return render(request, 'home.html')


# ======================
# AUTH — USER
# ======================

def login_view(request):
    if request.user.is_authenticated:
        if request.user.is_admin_user:
            return redirect('admin-dashboard')
        return redirect('dashboard')  # ← regular users go here

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')

        user = authenticate(request, username=username, password=password)

        if not user:
            messages.error(request, 'Invalid username or password.')
            return render(request, 'user/login.html')

        if user.is_admin_user:
            messages.error(request, 'Please use the admin PIN login.')
            return redirect('admin-pin-login')

        login(request, user)
        return redirect('dashboard')  # ← must be dashboard not admin-dashboard

    return render(request, 'user/login.html')

def logout_view(request):
    logout(request)
    return redirect('login')
# ======================
# AUTH — ADMIN PIN
 #
 #======================
def admin_pin_login(request):
    if request.user.is_authenticated:
        if request.user.is_admin_user:
            return redirect('admin-dashboard')
        return redirect('dashboard')

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        pin = request.POST.get('pin', '').strip()

        if not pin.isdigit() or len(pin) != 4:
            messages.error(request, 'PIN must be exactly 4 digits.')
            return render(request, 'user/admin_pin_login.html')

        try:
            user = User.objects.get(username=username, role='admin')
        except User.DoesNotExist:
            messages.error(request, 'Invalid admin credentials.')
            return render(request, 'user/admin_pin_login.html')

        try:
            admin_profile = user.admin_profile
        except AdminProfile.DoesNotExist:
            messages.error(request, 'Admin profile not set up.')
            return render(request, 'user/admin_pin_login.html')

        if not admin_profile.check_pin(pin):
            messages.error(request, 'Invalid PIN.')
            return render(request, 'user/admin_pin_login.html')

        user.backend = 'django.contrib.auth.backends.ModelBackend'
        login(request, user)
        return redirect('admin-dashboard')

    return render(request, 'user/admin_pin_login.html')
# ======================
# USER DASHBOARD
# ======================

@login_required
def dashboard(request):
    if request.user.is_admin_user:
        return redirect('admin-dashboard')

    wallet, _ = Wallet.objects.get_or_create(user=request.user)
    notifications = Notification.objects.filter(
        user=request.user).order_by('-created_at')[:10]
    transactions = Transaction.objects.filter(
        user=request.user).order_by('-created_at')[:10]
    payment_accounts = PaymentAccount.objects.filter(
        user=request.user, is_active=True)
    unread_notifications = Notification.objects.filter(
        user=request.user, is_read=False).count()

    return render(request, 'user/dashboard.html', {
        'wallet': wallet,
        'notifications': notifications,
        'transactions': transactions,
        'payment_accounts': payment_accounts,
        'unread_notifications': unread_notifications,
    })


# ======================
# NOTIFICATIONS
# ======================

@login_required
def notifications(request):
    notifs = Notification.objects.filter(
        user=request.user).order_by('-created_at')
    notifs.filter(is_read=False).update(is_read=True)
    return render(request, 'user/notifications.html', {
        'notifications': notifs
    })


# ======================
# ACTIVATION
# ======================

@login_required
def activate_account(request):
    if request.user.is_admin_user:
        return redirect('admin-dashboard')

    if request.user.account_activated:
        messages.info(request, 'Your account is already activated.')
        return redirect('dashboard')

    payment = ActivationPayment.objects.filter(user=request.user).first()

    if not payment:
        return render(request, 'user/activate.html', {
            'payment': None,
            'payment_details': [],
            'activation_amount': None,
        })

    activation_amount = payment.amount_required
    payment_details = PaymentDetail.objects.filter(is_active=True)  # ← must be PaymentDetail

    if request.method == 'POST' and payment.status == 'pending':
        payment.amount_paid = payment.amount_required
        payment.save()
        _notify(
            request.user,
            'Activation Payment Submitted',
            f'Your activation payment of ${activation_amount} has been submitted.'
        )
        messages.success(request, 'Payment submitted. Awaiting admin confirmation.')
        return redirect('dashboard')

    return render(request, 'user/activate.html', {
        'payment': payment,
        'payment_details': payment_details,  # ← PaymentDetail objects
        'activation_amount': activation_amount,
    })


# ======================
# WITHDRAWAL
# ======================

@login_required
def request_withdrawal(request):
    if request.user.is_admin_user:
        return redirect('admin-dashboard')

    if not request.user.account_activated:
        messages.error(request, 'You must activate your account before withdrawing.')
        return redirect('activate-account')

    wallet, _ = Wallet.objects.get_or_create(user=request.user)

    if wallet.is_frozen:
        messages.error(request, 'Your wallet is currently frozen. Contact support.')
        return redirect('dashboard')

    payment_accounts = PaymentAccount.objects.filter(
        user=request.user, is_active=True)

    if request.method == 'POST':
        amount_raw = request.POST.get('amount', '0')
        account_id = request.POST.get('payment_account_id')

        try:
            amount = Decimal(amount_raw)
        except Exception:
            messages.error(request, 'Invalid amount.')
            return redirect('request-withdrawal')

        if amount <= 0:
            messages.error(request, 'Amount must be greater than zero.')
            return redirect('request-withdrawal')

        if wallet.balance < amount:
            messages.error(request, f'Insufficient balance. You have ${wallet.balance}.')
            return redirect('request-withdrawal')

        if not account_id:
            messages.error(request, 'Please select a payment account.')
            return redirect('request-withdrawal')

        payment_account = get_object_or_404(
            PaymentAccount,
            id=account_id,
            user=request.user,
            is_active=True
        )

        wallet.balance -= amount
        wallet.save()

        WithdrawalRequest.objects.create(
            user=request.user,
            amount=amount,
            payment_account=payment_account,
        )

        Transaction.objects.create(
            user=request.user,
            amount=amount,
            transaction_type='withdrawal',
            status='pending',
            description=f'Withdrawal to {payment_account}',
        )

        _notify(
            request.user,
            'Withdrawal Submitted',
            f'Your withdrawal of ${amount} has been submitted and is under review.'
        )

        messages.success(request, 'Withdrawal request submitted successfully.')
        return redirect('dashboard')

    return render(request, 'user/withdrawal.html', {
        'wallet': wallet,
        'payment_accounts': payment_accounts,
    })


# ======================
# ADMIN — DASHBOARD
# ======================


from .models import models
# ======================
# ADMIN — USER DETAIL
# ======================
from django.db import models as db_models
@admin_required
def admin_users(request):
    search = request.GET.get('search', '').strip()
    users = User.objects.filter(role='user').order_by('-date_joined')

    if search:
        users = users.filter(
            models.Q(username__icontains=search) |
            models.Q(email__icontains=search) |
            models.Q(first_name__icontains=search) |
            models.Q(last_name__icontains=search)
        )

    return render(request, 'admin/users.html', {
        'users': users,
        'search': search,
        'total': users.count(),
        'pending_activations_count': ActivationPayment.objects.filter(status='pending').count(),
        'pending_withdrawals_count': WithdrawalRequest.objects.filter(status='pending').count(),
    })

# ======================
# ADMIN — TOGGLE ACTIVATION
# ======================

@admin_required
def admin_toggle_activation(request, user_id):
    target = get_object_or_404(User, id=user_id, role='user')

    if request.method == 'POST':
        new_status = not target.account_activated
        target.account_activated = new_status
        target.save()

        AccountControlLog.objects.create(
            admin=request.user,
            target_user=target,
            action='activate' if new_status else 'deactivate',
            note=request.POST.get('note', '')
        )

        _notify(
            target,
            'Account Status Changed',
            f'Your account has been {"activated" if new_status else "deactivated"} by admin.'
        )

        messages.success(
            request,
            f'Account {"activated" if new_status else "deactivated"} successfully.'
        )

    return redirect('admin-user-detail', user_id=user_id)


# ======================
# ADMIN — TOGGLE FREEZE
# ======================

@admin_required
def admin_toggle_freeze_wallet(request, user_id):
    target = get_object_or_404(User, id=user_id, role='user')
    wallet, _ = Wallet.objects.get_or_create(user=target)

    if request.method == 'POST':
        new_status = not wallet.is_frozen
        wallet.is_frozen = new_status
        wallet.frozen_by = request.user if new_status else None
        wallet.frozen_at = timezone.now() if new_status else None
        wallet.save()

        AccountControlLog.objects.create(
            admin=request.user,
            target_user=target,
            action='freeze_wallet' if new_status else 'unfreeze_wallet',
            note=request.POST.get('note', '')
        )

        _notify(
            target,
            'Wallet Status Changed',
            f'Your wallet has been {"frozen" if new_status else "unfrozen"} by admin.'
        )

        messages.success(
            request,
            f'Wallet {"frozen" if new_status else "unfrozen"} successfully.'
        )

    return redirect('admin-user-detail', user_id=user_id)


# ======================
# ADMIN — FUND USER
# ======================

@admin_required
def admin_fund_user(request, user_id):
    target = get_object_or_404(User, id=user_id, role='user')

    if request.method == 'POST':
        try:
            amount = Decimal(request.POST.get('amount', '0'))
        except Exception:
            messages.error(request, 'Invalid amount.')
            return redirect('admin-user-detail', user_id=user_id)

        if amount <= 0:
            messages.error(request, 'Amount must be greater than zero.')
            return redirect('admin-user-detail', user_id=user_id)

        note = request.POST.get('note', '')

        _credit_wallet(
            user=target,
            amount=amount,
            transaction_type='admin_deposit',
            description=note,
            performed_by=request.user
        )

        AdminDeposit.objects.create(
            user=target,
            deposited_by=request.user,
            amount=amount,
            note=note
        )

        _notify(target, 'Wallet Credited', f'${amount} has been added to your wallet.')
        messages.success(request, f'${amount} credited to {target.username}.')

    return redirect('admin-user-detail', user_id=user_id)


# ======================
# ADMIN — PAYMENT ACCOUNTS
# ======================

@admin_required
def admin_assign_payment_account(request, user_id):
    target = get_object_or_404(User, id=user_id, role='user')

    if request.method == 'POST':
        account_type = request.POST.get('account_type')

        account = PaymentAccount(
            user=target,
            assigned_by=request.user,
            account_type=account_type,
            note=request.POST.get('note', '')
        )

        if account_type == 'bank':
            account.bank_name = request.POST.get('bank_name', '')
            account.account_name = request.POST.get('account_name', '')
            account.account_number = request.POST.get('account_number', '')
            account.bank_country = request.POST.get('bank_country', '')
            account.routing_number = request.POST.get('routing_number', '')
            account.swift_code = request.POST.get('swift_code', '')
            account.iban = request.POST.get('iban', '')
        elif account_type == 'crypto':
            account.crypto_currency = request.POST.get('crypto_currency', '')
            account.crypto_network = request.POST.get('crypto_network', '')
            account.wallet_address = request.POST.get('wallet_address', '')

        account.save()

        AccountControlLog.objects.create(
            admin=request.user,
            target_user=target,
            action='assign_account',
            note=f'Assigned {account_type} payment account'
        )

        _notify(target, 'Payment Account Added',
                f'A new {account_type} payment account has been assigned to you.')
        messages.success(request, 'Payment account assigned successfully.')

    return redirect('admin-user-detail', user_id=user_id)


@admin_required
def admin_remove_payment_account(request, account_id):
    account = get_object_or_404(PaymentAccount, id=account_id)
    user_id = account.user.id

    if request.method == 'POST':
        account.is_active = False
        account.save()

        AccountControlLog.objects.create(
            admin=request.user,
            target_user=account.user,
            action='remove_account',
            note=f'Removed {account.account_type} payment account'
        )

        _notify(account.user, 'Payment Account Removed',
                'A payment account has been removed from your profile.')
        messages.success(request, 'Payment account removed.')

    return redirect('admin-user-detail', user_id=user_id)


# ======================
# ADMIN — WITHDRAWALS
# ======================

@admin_required
def admin_withdrawals(request):
    status_filter = request.GET.get('status', 'pending')
    withdrawals = WithdrawalRequest.objects.select_related(
        'user', 'payment_account', 'reviewed_by'
    ).order_by('-created_at')

    if status_filter in ('pending', 'approved', 'rejected'):
        withdrawals = withdrawals.filter(status=status_filter)

    return render(request, 'admin/withdrawals.html', {
        'withdrawals': withdrawals,
        'status_filter': status_filter,
        'pending_activations_count': ActivationPayment.objects.filter(status='pending').count(),
        'pending_withdrawals_count': WithdrawalRequest.objects.filter(status='pending').count(),
    })


@admin_required
def admin_review_withdrawal(request, withdrawal_id):
    withdrawal = get_object_or_404(WithdrawalRequest, id=withdrawal_id, status='pending')

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'approve':
            withdrawal.status = 'approved'
            withdrawal.reviewed_by = request.user
            withdrawal.reviewed_at = timezone.now()
            withdrawal.save()

            # update the pending transaction to success
            Transaction.objects.filter(
                user=withdrawal.user,
                transaction_type='withdrawal',
                status='pending'
            ).update(status='success')

            _notify(withdrawal.user, 'Withdrawal Approved',
                    f'Your withdrawal of ${withdrawal.amount} has been approved.')
            messages.success(request, 'Withdrawal approved.')

        elif action == 'reject':
            reason = request.POST.get('rejection_reason', '')

            wallet, _ = Wallet.objects.get_or_create(user=withdrawal.user)
            wallet.balance += withdrawal.amount
            wallet.save()

            withdrawal.status = 'rejected'
            withdrawal.reviewed_by = request.user
            withdrawal.reviewed_at = timezone.now()
            withdrawal.rejection_reason = reason
            withdrawal.save()

            Transaction.objects.filter(
                user=withdrawal.user,
                transaction_type='withdrawal',
                status='pending'
            ).update(status='failed')

            _notify(withdrawal.user, 'Withdrawal Rejected',
                    f'Your withdrawal of ${withdrawal.amount} was rejected. '
                    f'Reason: {reason}. Your balance has been refunded.')
            messages.success(request, 'Withdrawal rejected and balance refunded.')

    return redirect('admin-withdrawals')


# ======================
# ADMIN — PAYMENT DETAILS
# ======================

@admin_required
def admin_payment_details(request):
    details = PaymentDetail.objects.all()
    return render(request, 'admin/payment_details.html', {
        'details': details,
        'pending_activations_count': ActivationPayment.objects.filter(status='pending').count(),
        'pending_withdrawals_count': WithdrawalRequest.objects.filter(status='pending').count(),
    })


@admin_required
def admin_add_payment_detail(request):
    if request.method == 'POST':
        payment_type = request.POST.get('payment_type')

        detail = PaymentDetail(
            payment_type=payment_type,
            label=request.POST.get('label', ''),
            is_active=request.POST.get('is_active') == 'on',
            created_by=request.user,
        )

        if payment_type == 'bank':
            detail.bank_name = request.POST.get('bank_name', '')
            detail.account_name = request.POST.get('account_name', '')
            detail.account_number = request.POST.get('account_number', '')
            detail.bank_country = request.POST.get('bank_country', '')
            detail.routing_number = request.POST.get('routing_number', '')
            detail.swift_code = request.POST.get('swift_code', '')
            detail.iban = request.POST.get('iban', '')
        elif payment_type == 'crypto':
            detail.crypto_currency = request.POST.get('crypto_currency', '')
            detail.crypto_network = request.POST.get('crypto_network', '')
            detail.wallet_address = request.POST.get('wallet_address', '')

        detail.save()
        messages.success(request, 'Payment detail added successfully.')
        return redirect('admin-payment-details')

    return render(request, 'admin/add_payment_detail.html', {
        'pending_activations_count': ActivationPayment.objects.filter(status='pending').count(),
        'pending_withdrawals_count': WithdrawalRequest.objects.filter(status='pending').count(),
    })


@admin_required
def admin_edit_payment_detail(request, detail_id):
    detail = get_object_or_404(PaymentDetail, id=detail_id)

    if request.method == 'POST':
        detail.label = request.POST.get('label', detail.label)
        detail.is_active = request.POST.get('is_active') == 'on'

        if detail.payment_type == 'bank':
            detail.bank_name = request.POST.get('bank_name', '')
            detail.account_name = request.POST.get('account_name', '')
            detail.account_number = request.POST.get('account_number', '')
            detail.bank_country = request.POST.get('bank_country', '')
            detail.routing_number = request.POST.get('routing_number', '')
            detail.swift_code = request.POST.get('swift_code', '')
            detail.iban = request.POST.get('iban', '')
        elif detail.payment_type == 'crypto':
            detail.crypto_currency = request.POST.get('crypto_currency', '')
            detail.crypto_network = request.POST.get('crypto_network', '')
            detail.wallet_address = request.POST.get('wallet_address', '')

        detail.save()
        messages.success(request, 'Payment detail updated.')
        return redirect('admin-payment-details')

    return render(request, 'admin/edit_payment_detail.html', {
        'detail': detail,
        'pending_activations_count': ActivationPayment.objects.filter(status='pending').count(),
        'pending_withdrawals_count': WithdrawalRequest.objects.filter(status='pending').count(),
    })


@admin_required
def admin_delete_payment_detail(request, detail_id):
    detail = get_object_or_404(PaymentDetail, id=detail_id)
    if request.method == 'POST':
        detail.delete()
        messages.success(request, 'Payment detail deleted.')
    return redirect('admin-payment-details')


# ======================
# ADMIN — ACTIVATIONS
# ======================

@admin_required
def admin_pending_activations(request):
    pending = ActivationPayment.objects.filter(
        status='pending'
    ).select_related('user').order_by('-created_at')

    return render(request, 'admin/pending_activations.html', {
        'pending': pending,
        'pending_activations_count': pending.count(),
        'pending_withdrawals_count': WithdrawalRequest.objects.filter(status='pending').count(),
    })


@admin_required
def admin_approve_activation(request, user_id):
    target = get_object_or_404(User, id=user_id, role='user')
    payment = get_object_or_404(ActivationPayment, user=target)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'approve':
            payment.status = 'paid'
            payment.approved_by = request.user
            payment.approved_at = timezone.now()
            payment.save()

            target.account_activated = True
            target.save()

            Transaction.objects.create(
                user=target,
                amount=payment.amount_required,
                transaction_type='activation_fee',
                status='success',
                performed_by=request.user,
                description='Activation fee confirmed by admin',
            )

            AccountControlLog.objects.create(
                admin=request.user,
                target_user=target,
                action='activate',
                note='Activation payment confirmed manually by admin'
            )

            _notify(target, 'Account Activated!',
                    'Your account has been activated. Welcome!')
            messages.success(request, f"{target.username}'s account has been activated.")

        elif action == 'reject':
            payment.status = 'failed'
            payment.save()

            _notify(target, 'Activation Rejected',
                    'Your activation payment was not confirmed. '
                    'Please contact support or resubmit.')
            messages.warning(request, f"{target.username}'s activation was rejected.")

    return redirect('admin-pending-activations')




@login_required
def profile(request):
    if request.user.is_admin_user:
        return redirect('admin-dashboard')

    if request.method == 'POST':
        user = request.user
        user.first_name = request.POST.get('first_name', '').strip()
        user.last_name = request.POST.get('last_name', '').strip()
        user.email = request.POST.get('email', '').strip()
        user.phone = request.POST.get('phone', '').strip()
        user.address = request.POST.get('address', '').strip()

        dob = request.POST.get('date_of_birth', '').strip()
        if dob:
            try:
                from datetime import datetime
                user.date_of_birth = datetime.strptime(dob, '%Y-%m-%d').date()
            except ValueError:
                messages.error(request, 'Invalid date format.')
                return redirect('profile')

        user.save()
        messages.success(request, 'Profile updated successfully.')
        return redirect('profile')

    return render(request, 'user/profile.html', {
        'user': request.user,
    })


@admin_required
def admin_change_pin(request):
    if request.method == 'POST':
        current_pin = request.POST.get('current_pin', '').strip()
        new_pin = request.POST.get('new_pin', '').strip()
        confirm_pin = request.POST.get('confirm_pin', '').strip()

        try:
            profile = request.user.admin_profile
        except AdminProfile.DoesNotExist:
            profile = AdminProfile(user=request.user)

        # if profile already has a pin verify it first
        if profile.pk and profile.pin:
            if not profile.check_pin(current_pin):
                messages.error(request, 'Current PIN is incorrect.')
                return redirect('admin-change-pin')

        if not new_pin.isdigit() or len(new_pin) != 4:
            messages.error(request, 'PIN must be exactly 4 digits.')
            return redirect('admin-change-pin')

        if new_pin != confirm_pin:
            messages.error(request, 'PINs do not match.')
            return redirect('admin-change-pin')

        profile.set_pin(new_pin)
        profile.save()
        messages.success(request, 'PIN updated successfully.')
        return redirect('admin-dashboard')

    # check if pin already exists
    has_pin = False
    try:
        has_pin = bool(request.user.admin_profile.pin)
    except AdminProfile.DoesNotExist:
        pass

    return render(request, 'admin/change_pin.html', {
        'has_pin': has_pin,
        'pending_activations_count': ActivationPayment.objects.filter(status='pending').count(),
        'pending_withdrawals_count': WithdrawalRequest.objects.filter(status='pending').count(),
    })

# SET ACTIVATION FEE
@admin_required
def admin_set_activation_fee(request, user_id):
    target = get_object_or_404(User, id=user_id, role='user')

    if request.method == 'POST':
        try:
            amount = Decimal(request.POST.get('amount', '0'))
        except Exception:
            messages.error(request, 'Invalid amount.')
            return redirect('admin-user-detail', user_id=user_id)

        if amount <= 0:
            messages.error(request, 'Amount must be greater than zero.')
            return redirect('admin-user-detail', user_id=user_id)

        payment, created = ActivationPayment.objects.get_or_create(
            user=target,
            defaults={'amount_required': amount}
        )

        if not created:
            payment.amount_required = amount
            payment.status = 'pending'  # reset if was rejected
            payment.save()

        _notify(
            target,
            'Activation Fee Set',
            f'Your activation fee has been set to ${amount}. Please visit the activation page to proceed.'
        )

        messages.success(request, f'Activation fee set to ${amount} for {target.username}.')

    return redirect('admin-user-detail', user_id=user_id)


# VIEW ALL TRANSACTIONS
@admin_required
def admin_transactions(request):
    transactions = Transaction.objects.select_related(
        'user', 'performed_by'
    ).order_by('-created_at')

    type_filter = request.GET.get('type', '')
    if type_filter:
        transactions = transactions.filter(transaction_type=type_filter)

    return render(request, 'admin/transactions.html', {
        'transactions': transactions,
        'type_filter': type_filter,
        'pending_activations_count': ActivationPayment.objects.filter(status='pending').count(),
        'pending_withdrawals_count': WithdrawalRequest.objects.filter(status='pending').count(),
    })


# DELETE USER
@admin_required
def admin_delete_user(request, user_id):
    target = get_object_or_404(User, id=user_id, role='user')

    if request.method == 'POST':
        username = target.username
        target.delete()
        messages.success(request, f'User {username} deleted.')
        return redirect('admin-dashboard')

    return render(request, 'admin/confirm_delete.html', {
        'target': target,
        'pending_activations_count': ActivationPayment.objects.filter(status='pending').count(),
        'pending_withdrawals_count': WithdrawalRequest.objects.filter(status='pending').count(),
    })


# RESET USER PASSWORD
@admin_required
def admin_reset_password(request, user_id):
    target = get_object_or_404(User, id=user_id, role='user')

    if request.method == 'POST':
        new_password = request.POST.get('new_password', '').strip()

        if len(new_password) < 6:
            messages.error(request, 'Password must be at least 6 characters.')
            return redirect('admin-user-detail', user_id=user_id)

        target.set_password(new_password)
        target.save()

        AccountControlLog.objects.create(
            admin=request.user,
            target_user=target,
            action='force_reset',
            note='Password reset by admin'
        )

        _notify(target, 'Password Changed',
                'Your password has been reset by admin. Please log in with your new password.')

        messages.success(request, f'Password reset for {target.username}.')

    return redirect('admin-user-detail', user_id=user_id)


# ======================
# ADMIN — ALL USERS
# ======================

@admin_required
def admin_users(request):
    from django.db import models as db_models
    search = request.GET.get('search', '').strip()
    users = User.objects.filter(role='user').order_by('-date_joined')

    if search:
        users = users.filter(
            db_models.Q(username__icontains=search) |
            db_models.Q(email__icontains=search) |
            db_models.Q(first_name__icontains=search) |
            db_models.Q(last_name__icontains=search)
        )

    return render(request, 'admin/users.html', {
        'users': users,
        'search': search,
        'total': users.count(),
        'pending_activations_count': ActivationPayment.objects.filter(status='pending').count(),
        'pending_withdrawals_count': WithdrawalRequest.objects.filter(status='pending').count(),
    })


# ======================
# ADMIN — SET ACTIVATION FEE
# ======================

@admin_required
def admin_set_activation_fee(request, user_id):
    target = get_object_or_404(User, id=user_id, role='user')

    if request.method == 'POST':
        try:
            amount = Decimal(request.POST.get('amount', '0'))
        except Exception:
            messages.error(request, 'Invalid amount.')
            return redirect('admin-user-detail', user_id=user_id)

        if amount <= 0:
            messages.error(request, 'Amount must be greater than zero.')
            return redirect('admin-user-detail', user_id=user_id)

        payment, created = ActivationPayment.objects.get_or_create(
            user=target,
            defaults={'amount_required': amount}
        )

        if not created:
            payment.amount_required = amount
            payment.status = 'pending'
            payment.save()

        _notify(
            target,
            'Activation Fee Set',
            f'Your activation fee has been set to ${amount}. Please visit the activation page to proceed.'
        )

        messages.success(request, f'Activation fee set to ${amount} for {target.username}.')

    return redirect('admin-user-detail', user_id=user_id)


# ======================
# ADMIN — RESET PASSWORD
# ======================

@admin_required
def admin_reset_password(request, user_id):
    target = get_object_or_404(User, id=user_id, role='user')

    if request.method == 'POST':
        new_password = request.POST.get('new_password', '').strip()

        if len(new_password) < 6:
            messages.error(request, 'Password must be at least 6 characters.')
            return redirect('admin-user-detail', user_id=user_id)

        target.set_password(new_password)
        target.save()

        AccountControlLog.objects.create(
            admin=request.user,
            target_user=target,
            action='force_reset',
            note='Password reset by admin'
        )

        _notify(target, 'Password Changed',
                'Your password has been reset by admin. Please log in with your new password.')

        messages.success(request, f'Password reset for {target.username}.')

    return redirect('admin-user-detail', user_id=user_id)


# ======================
# ADMIN — DELETE USER
# ======================

@admin_required
def admin_delete_user(request, user_id):
    target = get_object_or_404(User, id=user_id, role='user')

    if request.method == 'POST':
        username = target.username
        target.delete()
        messages.success(request, f'User {username} deleted successfully.')
        return redirect('admin-users')

    return render(request, 'admin/confirm_delete.html', {
        'target': target,
        'pending_activations_count': ActivationPayment.objects.filter(status='pending').count(),
        'pending_withdrawals_count': WithdrawalRequest.objects.filter(status='pending').count(),
    })


# ======================
# ADMIN — TRANSACTIONS
# ======================

@admin_required
def admin_transactions(request):
    from django.db import models as db_models
    type_filter = request.GET.get('type', '')
    transactions = Transaction.objects.select_related(
        'user', 'performed_by'
    ).order_by('-created_at')

    if type_filter:
        transactions = transactions.filter(transaction_type=type_filter)

    return render(request, 'admin/transactions.html', {
        'transactions': transactions,
        'type_filter': type_filter,
        'pending_activations_count': ActivationPayment.objects.filter(status='pending').count(),
        'pending_withdrawals_count': WithdrawalRequest.objects.filter(status='pending').count(),
    })


# ======================
# ADMIN — CHANGE PIN
# ======================

@admin_required
def admin_change_pin(request):
    if request.method == 'POST':
        current_pin = request.POST.get('current_pin', '').strip()
        new_pin = request.POST.get('new_pin', '').strip()
        confirm_pin = request.POST.get('confirm_pin', '').strip()

        try:
            profile = request.user.admin_profile
        except AdminProfile.DoesNotExist:
            profile = AdminProfile(user=request.user)

        if profile.pk and profile.pin:
            if not profile.check_pin(current_pin):
                messages.error(request, 'Current PIN is incorrect.')
                return redirect('admin-change-pin')

        if not new_pin.isdigit() or len(new_pin) != 4:
            messages.error(request, 'PIN must be exactly 4 digits.')
            return redirect('admin-change-pin')

        if new_pin != confirm_pin:
            messages.error(request, 'PINs do not match.')
            return redirect('admin-change-pin')

        profile.set_pin(new_pin)
        profile.save()
        messages.success(request, 'PIN updated successfully.')
        return redirect('admin-dashboard')

    has_pin = False
    try:
        has_pin = bool(request.user.admin_profile.pin)
    except AdminProfile.DoesNotExist:
        pass

    return render(request, 'admin/change_pin.html', {
        'has_pin': has_pin,
        'pending_activations_count': ActivationPayment.objects.filter(status='pending').count(),
        'pending_withdrawals_count': WithdrawalRequest.objects.filter(status='pending').count(),
    })


@admin_required
def admin_user_detail(request, user_id):
    target = get_object_or_404(User, id=user_id, role='user')
    wallet, _ = Wallet.objects.get_or_create(user=target)
    payment_accounts = PaymentAccount.objects.filter(user=target)
    transactions = Transaction.objects.filter(user=target)[:20]
    control_logs = AccountControlLog.objects.filter(target_user=target)[:20]
    withdrawals = WithdrawalRequest.objects.filter(user=target)
    notifications = Notification.objects.filter(user=target).order_by('-created_at')
    unread_count = notifications.filter(is_read=False).count()

    # get activation payment if exists
    activation_payment = ActivationPayment.objects.filter(user=target).first()

    return render(request, 'admin/user_detail.html', {
        'target': target,
        'wallet': wallet,
        'payment_accounts': payment_accounts,
        'transactions': transactions,
        'control_logs': control_logs,
        'withdrawals': withdrawals,
        'activation_payment': activation_payment,
        'pending_activations_count': ActivationPayment.objects.filter(status='pending').count(),
        'pending_withdrawals_count': WithdrawalRequest.objects.filter(status='pending').count(),
    })

@admin_required
def admin_assign_payment_account(request, user_id):
    target = get_object_or_404(User, id=user_id, role='user')

    if request.method == 'POST':
        account_type = request.POST.get('account_type')

        account = PaymentAccount(
            user=target,
            assigned_by=request.user,
            account_type=account_type,
            note=request.POST.get('note', '')
        )

        if account_type == 'bank':
            account.bank_name = request.POST.get('bank_name', '')
            account.account_name = request.POST.get('account_name', '')
            account.account_number = request.POST.get('account_number', '')
            account.bank_country = request.POST.get('bank_country', '')
            account.routing_number = request.POST.get('routing_number', '')
            account.swift_code = request.POST.get('swift_code', '')
            account.iban = request.POST.get('iban', '')

        elif account_type == 'crypto':
            account.crypto_currency = request.POST.get('crypto_currency', '')
            account.crypto_network = request.POST.get('crypto_network', '')
            account.wallet_address = request.POST.get('wallet_address', '')

        account.save()

        AccountControlLog.objects.create(
            admin=request.user,
            target_user=target,
            action='assign_account',
            note=f'Assigned {account_type} payment account'
        )

        _notify(target, 'Payment Account Added',
                f'A new {account_type} payment account has been assigned to you.')
        messages.success(request, 'Payment account assigned successfully.')

    return redirect('admin-user-detail', user_id=user_id)


@admin_required
def admin_remove_payment_account(request, account_id):
    account = get_object_or_404(PaymentAccount, id=account_id)
    user_id = account.user.id

    if request.method == 'POST':
        account.is_active = False
        account.save()

        AccountControlLog.objects.create(
            admin=request.user,
            target_user=account.user,
            action='remove_account',
            note=f'Removed {account.account_type} payment account'
        )

        _notify(account.user, 'Payment Account Removed',
                'A payment account has been removed from your profile.')
        messages.success(request, 'Payment account removed.')

    return redirect('admin-user-detail', user_id=user_id)

from .models import SupportTicket, SupportMessage

@login_required
def support_create(request):
    if request.method == 'POST':
        subject = request.POST.get('subject', '').strip()
        message = request.POST.get('message', '').strip()

        if subject and message:
            ticket = SupportTicket.objects.create(
                user=request.user,
                subject=subject
            )
            SupportMessage.objects.create(
                ticket=ticket,
                sender=request.user,
                message=message
            )
            messages.success(request, 'Support ticket created successfully.')
            return redirect('support-detail', ticket.id)

    return render(request, 'user/support_create.html')


@login_required
def support_list(request):
    tickets = SupportTicket.objects.filter(
        user=request.user
    ).order_by('-created_at')

    return render(request, 'user/support_list.html', {
        'tickets': tickets
    })


@login_required
def support_detail(request, ticket_id):
    ticket = get_object_or_404(SupportTicket, id=ticket_id, user=request.user)

    if request.method == 'POST':
        msg = request.POST.get('message', '').strip()
        if msg:
            SupportMessage.objects.create(
                ticket=ticket,
                sender=request.user,
                message=msg
            )
            ticket.status = 'open'
            ticket.save()
            return redirect('support-detail', ticket_id)

    return render(request, 'user/support_detail.html', {
        'ticket': ticket
    })


@admin_required
def admin_support_tickets(request):
    status_filter = request.GET.get('status', '')
    tickets = SupportTicket.objects.select_related('user').order_by('-updated_at')

    if status_filter in ('open', 'pending', 'closed'):
        tickets = tickets.filter(status=status_filter)

    return render(request, 'admin/support_tickets.html', {
        'tickets': tickets,
        'status_filter': status_filter,
        'open_tickets_count': SupportTicket.objects.filter(status='open').count(),
        'pending_activations_count': ActivationPayment.objects.filter(status='pending').count(),
        'pending_withdrawals_count': WithdrawalRequest.objects.filter(status='pending').count(),
    })


@admin_required
def admin_support_detail(request, ticket_id):
    ticket = get_object_or_404(SupportTicket, id=ticket_id)

    if request.method == 'POST':
        msg = request.POST.get('message', '').strip()
        if msg:
            SupportMessage.objects.create(
                ticket=ticket,
                sender=request.user,
                message=msg
            )
            ticket.status = 'pending'
            ticket.save()

            _notify(
                ticket.user,
                f'Support Reply — #{ticket.id}',
                f'Admin replied to your ticket: {ticket.subject}'
            )
            return redirect('admin-support-detail', ticket_id)

    return render(request, 'admin/support_detail.html', {
        'ticket': ticket,
        'open_tickets_count': SupportTicket.objects.filter(status='open').count(),
        'pending_activations_count': ActivationPayment.objects.filter(status='pending').count(),
        'pending_withdrawals_count': WithdrawalRequest.objects.filter(status='pending').count(),
    })


@admin_required
def admin_close_ticket(request, ticket_id):
    ticket = get_object_or_404(SupportTicket, id=ticket_id)
    ticket.status = 'closed'
    ticket.save()

    _notify(
        ticket.user,
        f'Ticket Closed — #{ticket.id}',
        f'Your support ticket "{ticket.subject}" has been closed.'
    )

    messages.success(request, f'Ticket #{ticket.id} closed.')
    return redirect('admin-support-detail', ticket_id)



from decimal import Decimal
from django.utils import timezone
@admin_required
def approve_deposit(request, pk):
    deposit = get_object_or_404(DepositRequest, pk=pk)
    approve_deposit(deposit, request.user)
    messages.success(request, f'Deposit of ${deposit.amount} approved.')
    return redirect('admin-deposits')


@admin_required
def reject_deposit(request, pk):
    deposit = get_object_or_404(DepositRequest, pk=pk)
    reason = request.GET.get('reason', 'Rejected by admin')
    reject_deposit(deposit, request.user, reason)
    messages.success(request, f'Deposit rejected.')
    return redirect('admin-deposits')
@login_required
def deposit_request(request):

    payment_details = PaymentDetail.objects.filter(is_active=True)

    if request.method == "POST":

        DepositRequest.objects.create(
            user=request.user,
            payment_detail_id=request.POST.get("payment_detail"),
            amount=request.POST.get("amount"),
            transaction_id=request.POST.get("transaction_id"),
            note=request.POST.get("note"),
            proof=request.FILES.get("proof")
        )

        return redirect("deposit-history")

    return render(
        request,
        "user/deposit_form.html",
        {
            "payment_details": payment_details
        }
    )


@login_required
def deposit_history(request):

    deposits = DepositRequest.objects.filter(
        user=request.user
    )

    return render(
        request,
        "user/deposit_history.html",
        {
            "deposits": deposits
        }
    )


@admin_required
def admin_deposit_detail(request, pk):
    deposit = get_object_or_404(DepositRequest, pk=pk)
    return render(request, 'admin/deposit_detail.html', {
        'deposit': deposit,
        'pending_activations_count': ActivationPayment.objects.filter(status='pending').count(),
        'pending_withdrawals_count': WithdrawalRequest.objects.filter(status='pending').count(),
        'open_tickets_count': SupportTicket.objects.filter(status='open').count(),
    })



def deposit_detail(request, pk):
    pass


def admin_deposits(request):
    pass


def approve_deposit_view(request, pk):
    pass


def reject_deposit_view(request, pk):
    pass

@admin_required
def admin_deposits(request):
    status_filter = request.GET.get('status', 'pending')
    deposits = DepositRequest.objects.select_related('user', 'payment_detail').order_by('-created_at')

    if status_filter in ('pending', 'approved', 'rejected'):
        deposits = deposits.filter(status=status_filter)

    return render(request, 'admin/deposits.html', {
        'deposits': deposits,
        'status_filter': status_filter,
        'pending_activations_count': ActivationPayment.objects.filter(status='pending').count(),
        'pending_withdrawals_count': WithdrawalRequest.objects.filter(status='pending').count(),
        'open_tickets_count': SupportTicket.objects.filter(status='open').count(),
    })


from datetime import timedelta
from django.db.models import Sum, Count
from django.db.models.functions import TruncDate
import json



@admin_required
def admin_dashboard(request):
    today = timezone.now().date()
    week_ago = today - timedelta(days=6)   # 7-day window inclusive
    month_ago = today - timedelta(days=29) # 30-day window inclusive

    # ----------------------------------------------------------
    # STAT CARDS
    # ----------------------------------------------------------
    total_users        = User.objects.filter(role='user').count()
    active_users       = User.objects.filter(role='user', account_activated=True).count()
    pending_activations = ActivationPayment.objects.filter(status='pending').count()
    pending_withdrawals = WithdrawalRequest.objects.filter(status='pending').count()
    open_tickets        = SupportTicket.objects.filter(status='open').count()
    pending_deposits    = DepositRequest.objects.filter(status='pending').count()

    # Total wallet balances across all users
    total_balance = Wallet.objects.aggregate(total=Sum('balance'))['total'] or 0

    # Total approved withdrawal volume (all time)
    total_withdrawn = (
        WithdrawalRequest.objects
        .filter(status='approved')
        .aggregate(total=Sum('amount'))['total'] or 0
    )

    # ----------------------------------------------------------
    # WEEKLY BAR CHART — deposits vs withdrawals (last 7 days)
    # ----------------------------------------------------------
    # Build a list of the last 7 dates so gaps show as 0
    week_dates = [week_ago + timedelta(days=i) for i in range(7)]
    date_labels = [d.strftime('%a') for d in week_dates]  # Mon, Tue …

    # Approved deposit requests grouped by day
    dep_by_day = (
        DepositRequest.objects
        .filter(status='approved', reviewed_at__date__gte=week_ago)
        .annotate(day=TruncDate('reviewed_at'))
        .values('day')
        .annotate(total=Sum('amount'))
    )
    dep_map = {str(r['day']): float(r['total']) for r in dep_by_day}

    # Approved withdrawals grouped by day
    wd_by_day = (
        WithdrawalRequest.objects
        .filter(status='approved', reviewed_at__date__gte=week_ago)
        .annotate(day=TruncDate('reviewed_at'))
        .values('day')
        .annotate(total=Sum('amount'))
    )
    wd_map = {str(r['day']): float(r['total']) for r in wd_by_day}

    chart_deposits    = [dep_map.get(str(d), 0) for d in week_dates]
    chart_withdrawals = [wd_map.get(str(d), 0) for d in week_dates]

    # ----------------------------------------------------------
    # USER GROWTH LINE CHART — new users per day (last 30 days)
    # ----------------------------------------------------------
    month_dates = [month_ago + timedelta(days=i) for i in range(30)]
    month_labels = [f"{d.month}/{d.day}" for d in month_dates]

    new_users_by_day = (
        User.objects
        .filter(role='user', date_joined__date__gte=month_ago)
        .annotate(day=TruncDate('date_joined'))
        .values('day')
        .annotate(count=Count('id'))
    )
    nu_map = {str(r['day']): r['count'] for r in new_users_by_day}

    # Cumulative growth starting from count before the window
    base_count = User.objects.filter(
        role='user', date_joined__date__lt=month_ago
    ).count()
    running = base_count
    chart_user_growth = []
    for d in month_dates:
        running += nu_map.get(str(d), 0)
        chart_user_growth.append(running)

    # ----------------------------------------------------------
    # PIE / DOUGHNUT — transaction type breakdown (all time)
    # ----------------------------------------------------------
    tx_breakdown = (
        Transaction.objects
        .filter(status='success')
        .values('transaction_type')
        .annotate(total=Sum('amount'))
        .order_by('-total')
    )
    pie_labels = [r['transaction_type'].replace('_', ' ').title() for r in tx_breakdown]
    pie_values = [float(r['total']) for r in tx_breakdown]

    # ----------------------------------------------------------
    # SUPPORT MESSAGES (last 7 days) — for the messages chart tab
    # ----------------------------------------------------------
    msg_by_day = (
        SupportMessage.objects
        .filter(created_at__date__gte=week_ago)
        .annotate(day=TruncDate('created_at'))
        .values('day')
        .annotate(count=Count('id'))
    )
    msg_map = {str(r['day']): r['count'] for r in msg_by_day}
    chart_messages = [msg_map.get(str(d), 0) for d in week_dates]

    # ----------------------------------------------------------
    # LIVE ACTIVITY FEED — last 20 events across all models
    # ----------------------------------------------------------
    # Collect recent events from transactions, withdrawals, deposits, tickets
    recent_transactions = (
        Transaction.objects
        .select_related('user')
        .order_by('-created_at')[:8]
    )
    recent_withdrawals = (
        WithdrawalRequest.objects
        .select_related('user')
        .order_by('-created_at')[:4]
    )
    recent_deposit_requests = (
        DepositRequest.objects
        .select_related('user')
        .order_by('-created_at')[:4]
    )
    recent_tickets = (
        SupportTicket.objects
        .select_related('user')
        .order_by('-created_at')[:4]
    )

    # Build unified feed list and sort by time
    feed = []
    for t in recent_transactions:
        feed.append({
            'type': t.transaction_type,
            'user': t.user.username,
            'user_id': t.user.id,
            'amount': float(t.amount),
            'status': t.status,
            'time': t.created_at,
            'description': t.description or t.get_transaction_type_display(),
        })
    for w in recent_withdrawals:
        feed.append({
            'type': 'withdrawal_request',
            'user': w.user.username,
            'user_id': w.user.id,
            'amount': float(w.amount),
            'status': w.status,
            'time': w.created_at,
            'description': f'Withdrawal — {w.status}',
        })
    for d in recent_deposit_requests:
        feed.append({
            'type': 'deposit_request',
            'user': d.user.username,
            'user_id': d.user.id,
            'amount': float(d.amount),
            'status': d.status,
            'time': d.created_at,
            'description': f'Deposit request — {d.status}',
        })
    for tk in recent_tickets:
        feed.append({
            'type': 'support_ticket',
            'user': tk.user.username,
            'user_id': tk.user.id,
            'amount': None,
            'status': tk.status,
            'time': tk.created_at,
            'description': tk.subject,
        })

    feed.sort(key=lambda x: x['time'], reverse=True)
    feed = feed[:20]

    # ----------------------------------------------------------
    # CONTEXT
    # ----------------------------------------------------------
    return render(request, 'admin/dashboard.html', {
        # Sidebar badge counts (used by base template)
        'pending_activations_count': pending_activations,
        'pending_withdrawals_count': pending_withdrawals,
        'open_tickets_count':        open_tickets,

        # Stat cards
        'total_users':          total_users,
        'active_users':         active_users,
        'pending_activations':  pending_activations,
        'pending_withdrawals':  pending_withdrawals,
        'open_tickets':         open_tickets,
        'pending_deposits':     pending_deposits,
        'total_balance':        total_balance,
        'total_withdrawn':      total_withdrawn,

        # Charts (pass as JSON so the template can inline them safely)
        'chart_labels':       json.dumps(date_labels),
        'chart_deposits':     json.dumps(chart_deposits),
        'chart_withdrawals':  json.dumps(chart_withdrawals),
        'chart_messages':     json.dumps(chart_messages),
        'month_labels':       json.dumps(month_labels),
        'chart_user_growth':  json.dumps(chart_user_growth),
        'pie_labels':         json.dumps(pie_labels),
        'pie_values':         json.dumps(pie_values),

        # Feed
        'feed': feed,
    })


# ── Add this view to views.py ─────────────────────────────

@admin_required
def admin_create_user(request):
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()

        if not username or not password:
            messages.error(request, 'Username and password are required.')
            return redirect('admin-create-user')

        if len(password) < 6:
            messages.error(request, 'Password must be at least 6 characters.')
            return redirect('admin-create-user')

        if User.objects.filter(username=username).exists():
            messages.error(request, f'Username "{username}" is already taken.')
            return redirect('admin-create-user')

        user = User.objects.create_user(
            username=username,
            password=password,
            role='user',
        )

        # Create a wallet for the user automatically
        Wallet.objects.create(user=user)

        messages.success(request, f'User "{username}" created successfully.')
        return redirect('admin-user-detail', user_id=user.id)

    return render(request, 'admin/create_user.html', {
        'pending_activations_count': ActivationPayment.objects.filter(status='pending').count(),
        'pending_withdrawals_count': WithdrawalRequest.objects.filter(status='pending').count(),
        'open_tickets_count': SupportTicket.objects.filter(status='open').count(),
    })