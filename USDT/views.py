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

from .models import (
    User, AdminProfile, Wallet, PaymentAccount,
    ActivationPayment, AdminDeposit, Transaction,
    WithdrawalRequest, AccountControlLog, Notification,
    PaymentDetail
)


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

def admin_required(view_func):
    @login_required
    def wrapper(request, *args, **kwargs):
        if not request.user.is_admin_user:
            messages.error(request, 'Access denied.')
            return redirect('dashboard')
        return view_func(request, *args, **kwargs)
    return wrapper


# ======================
# HOME
# ======================

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
# ======================

def admin_pin_login(request):
    if request.user.is_authenticated:
        if request.user.is_admin_user:
            return redirect('admin-dashboard')
        return redirect('dashboard')

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        pin = request.POST.get('pin', '').strip()

        print(f"DEBUG: username={username}, pin={pin}")

        if not pin.isdigit() or len(pin) != 4:
            messages.error(request, 'PIN must be exactly 4 digits.')
            return render(request, 'user/admin_pin_login.html')

        try:
            user = User.objects.get(username=username, role='admin')
            print(f"DEBUG: found user {user.username}")
        except User.DoesNotExist:
            print("DEBUG: user not found")
            messages.error(request, 'Invalid admin credentials.')
            return render(request, 'user/admin_pin_login.html')

        try:
            admin_profile = user.admin_profile
            print("DEBUG: found profile, checking pin")
        except AdminProfile.DoesNotExist:
            print("DEBUG: no admin profile found")
            messages.error(request, 'Admin profile not set up.')
            return render(request, 'user/admin_pin_login.html')

        if not admin_profile.check_pin(pin):
            print("DEBUG: wrong PIN")
            messages.error(request, 'Invalid PIN.')
            return render(request, 'user/admin_pin_login.html')

        print("DEBUG: login successful")
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

    # get existing payment or None — admin must create it
    payment = ActivationPayment.objects.filter(user=request.user).first()

    if not payment:
        return render(request, 'user/activate.html', {
            'payment': None,
            'payment_details': [],
            'activation_amount': None,
        })

    activation_amount = payment.amount_required
    payment_details = PaymentDetail.objects.filter(is_active=True)

    if request.method == 'POST' and payment.status == 'pending':
        payment.amount_paid = payment.amount_required
        payment.save()

        _notify(
            request.user,
            'Activation Payment Submitted',
            f'Your activation payment of ${activation_amount} has been submitted. '
            'Admin will confirm and activate your account shortly.'
        )

        messages.success(
            request,
            'Payment submitted. Your account will be activated once admin confirms.'
        )
        return redirect('dashboard')

    return render(request, 'user/activate.html', {
        'payment': payment,
        'payment_details': payment_details,
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

@admin_required
def admin_dashboard(request):
    users = User.objects.filter(role='user').order_by('-date_joined')
    pending_activations = ActivationPayment.objects.filter(status='pending').count()
    pending_withdrawals = WithdrawalRequest.objects.filter(status='pending').count()

    return render(request, 'admin/dashboard.html', {
        'users': users,
        'total_users': users.count(),
        'active_users': users.filter(account_activated=True).count(),
        'pending_activations': pending_activations,
        'pending_withdrawals': pending_withdrawals,
        'pending_activations_count': pending_activations,
        'pending_withdrawals_count': pending_withdrawals,
    })


# ======================
# ADMIN — USER DETAIL
# ======================

@admin_required
def admin_user_detail(request, user_id):
    target = get_object_or_404(User, id=user_id, role='user')
    wallet, _ = Wallet.objects.get_or_create(user=target)
    payment_accounts = PaymentAccount.objects.filter(user=target)
    transactions = Transaction.objects.filter(user=target)[:20]
    control_logs = AccountControlLog.objects.filter(target_user=target)[:20]
    withdrawals = WithdrawalRequest.objects.filter(user=target)

    return render(request, 'admin/user_detail.html', {
        'target': target,
        'wallet': wallet,
        'payment_accounts': payment_accounts,
        'transactions': transactions,
        'control_logs': control_logs,
        'withdrawals': withdrawals,
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