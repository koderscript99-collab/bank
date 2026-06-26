# ============================================================
# Replace your existing admin_dashboard function in views.py
# with this one. All other views remain unchanged.
# ============================================================

from django.db.models import Sum, Count, Q
from django.db.models.functions import TruncDate
from django.utils import timezone
from datetime import timedelta
import json

from crypto.USDT.views import admin_required

from .models import (
    DepositRequest, User, AdminProfile, Wallet, PaymentAccount,
    ActivationPayment, AdminDeposit, Transaction,
    WithdrawalRequest, AccountControlLog, Notification,
    PaymentDetail,SupportTicket, SupportMessage
)
  

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
    month_labels = [d.strftime('%-m/%-d') for d in month_dates]

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