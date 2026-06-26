from django.urls import path
from . import views

urlpatterns = [
    # ── Auth ──────────────────────────────────────────────────
    path('', views.home, name='home'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('admin-login/', views.admin_pin_login, name='admin-pin-login'),

    # ── User ──────────────────────────────────────────────────
    path('dashboard/', views.dashboard, name='dashboard'),
    path('activate/', views.activate_account, name='activate-account'),
    path('withdraw/', views.request_withdrawal, name='request-withdrawal'),
    path('notifications/', views.notifications, name='notifications'),
    path('profile/', views.profile, name='profile'),
    path('support/', views.support_list, name='support-list'),
    path('support/create/', views.support_create, name='support-create'),
    path('support/<int:ticket_id>/', views.support_detail, name='support-detail'),
    path('deposit/', views.deposit_request, name='deposit-request'),
    path('deposit/history/', views.deposit_history, name='deposit-history'),
    path('deposit/<int:pk>/', views.deposit_detail, name='deposit-detail'),

    # ── Admin — dashboard ─────────────────────────────────────
    path('admin-panel/', views.admin_dashboard, name='admin-dashboard'),
    path('admin-panel/dashboard/', views.admin_dashboard, name='admin-dashboard-1'),

    # ── Admin — users ─────────────────────────────────────────
    path('admin-panel/users/', views.admin_users, name='admin-users'),
    path('admin-panel/users/<int:user_id>/', views.admin_user_detail, name='admin-user-detail'),
    path('admin-panel/users/<int:user_id>/toggle-activation/', views.admin_toggle_activation, name='admin-toggle-activation'),
    path('admin-panel/users/<int:user_id>/toggle-freeze/', views.admin_toggle_freeze_wallet, name='admin-toggle-freeze'),
    path('admin-panel/users/<int:user_id>/fund/', views.admin_fund_user, name='admin-fund-user'),
    path('admin-panel/users/<int:user_id>/assign-account/', views.admin_assign_payment_account, name='admin-assign-account'),
    path('admin-panel/users/<int:user_id>/set-fee/', views.admin_set_activation_fee, name='admin-set-activation-fee'),
    path('admin-panel/users/<int:user_id>/reset-password/', views.admin_reset_password, name='admin-reset-password'),
    path('admin-panel/users/<int:user_id>/delete/', views.admin_delete_user, name='admin-delete-user'),
    path('admin-panel/users/create/', views.admin_create_user, name='admin-create-user'),

    # ── Admin — accounts ──────────────────────────────────────
    path('admin-panel/accounts/<int:account_id>/remove/', views.admin_remove_payment_account, name='admin-remove-account'),

    # ── Admin — withdrawals ───────────────────────────────────
    path('admin-panel/withdrawals/', views.admin_withdrawals, name='admin-withdrawals'),
    path('admin-panel/withdrawals/<int:withdrawal_id>/review/', views.admin_review_withdrawal, name='admin-review-withdrawal'),

    # ── Admin — payment details ───────────────────────────────
    path('admin-panel/payment-details/', views.admin_payment_details, name='admin-payment-details'),
    path('admin-panel/payment-details/add/', views.admin_add_payment_detail, name='admin-add-payment-detail'),
    path('admin-panel/payment-details/<int:detail_id>/edit/', views.admin_edit_payment_detail, name='admin-edit-payment-detail'),
    path('admin-panel/payment-details/<int:detail_id>/delete/', views.admin_delete_payment_detail, name='admin-delete-payment-detail'),

    # ── Admin — activations ───────────────────────────────────
    path('admin-panel/activations/', views.admin_pending_activations, name='admin-pending-activations'),
    path('admin-panel/activations/<int:user_id>/review/', views.admin_approve_activation, name='admin-approve-activation'),

    # ── Admin — transactions ──────────────────────────────────
    path('admin-panel/transactions/', views.admin_transactions, name='admin-transactions'),

    # ── Admin — PIN ───────────────────────────────────────────
    path('admin-panel/change-pin/', views.admin_change_pin, name='admin-change-pin'),

    # ── Admin — support ───────────────────────────────────────
    path('admin-panel/support/', views.admin_support_tickets, name='admin-support-tickets'),
    path('admin-panel/support/<int:ticket_id>/', views.admin_support_detail, name='admin-support-detail'),
    path('admin-panel/support/<int:ticket_id>/close/', views.admin_close_ticket, name='admin-close-ticket'),

    # ── Admin — deposits ──────────────────────────────────────
    path('admin-panel/deposits/', views.admin_deposits, name='admin-deposits'),
    path('admin/deposits/<int:pk>/approve/', views.approve_deposit_view, name='approve-deposit'),
    path('admin/deposits/<int:pk>/reject/', views.reject_deposit_view, name='reject-deposit'),
    path('admin/deposits/<int:pk>/', views.admin_deposit_detail, name='admin-deposit-detail'),
]