from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils import timezone
from .models import (
    User, AdminProfile, Wallet, PaymentAccount,
    ActivationPayment, AdminDeposit, Transaction,
    WithdrawalRequest, AccountControlLog, Notification
)


# ======================
# USER
# ======================

@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ('username', 'email', 'phone', 'role', 'account_activated', 'date_joined')
    list_filter = ('role', 'account_activated', 'is_staff')
    search_fields = ('username', 'email', 'phone')
    list_editable = ('account_activated', 'role')
    ordering = ('-date_joined',)

    fieldsets = BaseUserAdmin.fieldsets + (
        ('Extra Info', {
            'fields': ('phone', 'role', 'account_activated')
        }),
    )

    actions = ['activate_accounts', 'deactivate_accounts']

    def activate_accounts(self, request, queryset):
        queryset.update(account_activated=True)
        for user in queryset:
            AccountControlLog.objects.create(
                admin=request.user,
                target_user=user,
                action='activate',
                note='Bulk activated via admin panel'
            )
        self.message_user(request, f"{queryset.count()} account(s) activated.")
    activate_accounts.short_description = "Activate selected accounts"

    def deactivate_accounts(self, request, queryset):
        queryset.update(account_activated=False)
        for user in queryset:
            AccountControlLog.objects.create(
                admin=request.user,
                target_user=user,
                action='deactivate',
                note='Bulk deactivated via admin panel'
            )
        self.message_user(request, f"{queryset.count()} account(s) deactivated.")
    deactivate_accounts.short_description = "Deactivate selected accounts"


# ======================
# ADMIN PROFILE
# ======================

@admin.register(AdminProfile)
class AdminProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'is_super_admin', 'created_at')
    list_filter = ('is_super_admin',)
    search_fields = ('user__username',)
    readonly_fields = ('created_at',)

    # Never show raw PIN in admin
    exclude = ('pin',)


# ======================
# WALLET
# ======================

@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ('user', 'balance', 'is_frozen', 'frozen_by', 'frozen_at')
    list_filter = ('is_frozen',)
    search_fields = ('user__username',)
    readonly_fields = ('frozen_at',)

    actions = ['freeze_wallets', 'unfreeze_wallets']

    def freeze_wallets(self, request, queryset):
        queryset.update(is_frozen=True, frozen_by=request.user, frozen_at=timezone.now())
        self.message_user(request, f"{queryset.count()} wallet(s) frozen.")
    freeze_wallets.short_description = "Freeze selected wallets"

    def unfreeze_wallets(self, request, queryset):
        queryset.update(is_frozen=False, frozen_by=None, frozen_at=None)
        self.message_user(request, f"{queryset.count()} wallet(s) unfrozen.")
    unfreeze_wallets.short_description = "Unfreeze selected wallets"


# ======================
# PAYMENT ACCOUNT
# ======================

@admin.register(PaymentAccount)
class PaymentAccountAdmin(admin.ModelAdmin):
    list_display = (
        'user', 'account_type', 'is_active',
        'bank_name', 'account_number',
        'crypto_currency', 'wallet_address', 'assigned_by', 'created_at'
    )
    list_filter = ('account_type', 'is_active', 'crypto_currency')
    search_fields = ('user__username', 'account_number', 'wallet_address', 'bank_name')
    readonly_fields = ('created_at',)

    fieldsets = (
        ('Assignment', {
            'fields': ('user', 'assigned_by', 'account_type', 'is_active', 'note')
        }),
        ('Bank Details', {
            'fields': (
                'bank_name', 'account_name', 'account_number',
                'bank_country', 'routing_number', 'swift_code', 'iban'
            ),
            'classes': ('collapse',)
        }),
        ('Crypto Details', {
            'fields': ('crypto_currency', 'crypto_network', 'wallet_address'),
            'classes': ('collapse',)
        }),
    )

    def save_model(self, request, obj, form, change):
        if not obj.assigned_by:
            obj.assigned_by = request.user
        super().save_model(request, obj, form, change)


# ======================
# ACTIVATION PAYMENT
# ======================

@admin.register(ActivationPayment)
class ActivationPaymentAdmin(admin.ModelAdmin):
    list_display = ('user', 'amount_required', 'amount_paid', 'status', 'approved_by', 'approved_at')
    list_filter = ('status',)
    search_fields = ('user__username',)
    readonly_fields = ('approved_at', 'created_at')

    actions = ['mark_as_paid']

    def mark_as_paid(self, request, queryset):
        for obj in queryset:
            obj.status = 'paid'
            obj.approved_by = request.user
            obj.approved_at = timezone.now()
            obj.save()
            # activate the user account too
            obj.user.account_activated = True
            obj.user.save()
            AccountControlLog.objects.create(
                admin=request.user,
                target_user=obj.user,
                action='activate',
                note='Activated via activation payment approval'
            )
        self.message_user(request, f"{queryset.count()} payment(s) marked as paid and accounts activated.")
    mark_as_paid.short_description = "Mark as paid & activate account"


# ======================
# ADMIN DEPOSIT
# ======================

@admin.register(AdminDeposit)
class AdminDepositAdmin(admin.ModelAdmin):
    list_display = ('user', 'amount', 'deposited_by', 'note', 'created_at')
    search_fields = ('user__username',)
    readonly_fields = ('created_at',)

    def save_model(self, request, obj, form, change):
        is_new = not obj.pk  # only credit on CREATE not edit
        if not obj.deposited_by:
            obj.deposited_by = request.user
        super().save_model(request, obj, form, change)

        if is_new:  # only run on new deposits
            wallet, _ = Wallet.objects.get_or_create(user=obj.user)
            wallet.balance += obj.amount
            wallet.save()

            Transaction.objects.create(
                user=obj.user,
                amount=obj.amount,
                transaction_type='admin_deposit',
                status='success',
                performed_by=request.user,
                description=obj.note or 'Admin deposit'
            )

            Notification.objects.create(
                user=obj.user,
                title='Wallet Credited',
                message=f'${obj.amount} has been added to your wallet by admin.'
            )

# ======================
# TRANSACTION
# ======================

@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ('user', 'transaction_type', 'amount', 'status', 'reference', 'performed_by', 'created_at')
    list_filter = ('transaction_type', 'status')
    search_fields = ('user__username', 'reference')
    readonly_fields = ('reference', 'created_at')


# ======================
# WITHDRAWAL
# ======================

@admin.register(WithdrawalRequest)
class WithdrawalRequestAdmin(admin.ModelAdmin):
    list_display = ('user', 'amount', 'payment_account', 'status', 'reviewed_by', 'reviewed_at', 'created_at')
    list_filter = ('status',)
    search_fields = ('user__username',)
    readonly_fields = ('reviewed_at', 'created_at')

    actions = ['approve_withdrawals', 'reject_withdrawals']

    def approve_withdrawals(self, request, queryset):
        for obj in queryset.filter(status='pending'):
            wallet = Wallet.objects.get(user=obj.user)
            if wallet.balance >= obj.amount:
                wallet.balance -= obj.amount
                wallet.save()
                obj.status = 'approved'
                obj.reviewed_by = request.user
                obj.reviewed_at = timezone.now()
                obj.save()
                Transaction.objects.create(
                    user=obj.user,
                    amount=obj.amount,
                    transaction_type='withdrawal',
                    status='success',
                    performed_by=request.user,
                    description='Withdrawal approved'
                )
        self.message_user(request, "Selected withdrawals approved and balances deducted.")
    approve_withdrawals.short_description = "Approve selected withdrawals"

    def reject_withdrawals(self, request, queryset):
        queryset.filter(status='pending').update(
            status='rejected',
            reviewed_by=request.user,
            reviewed_at=timezone.now()
        )
        self.message_user(request, "Selected withdrawals rejected.")
    reject_withdrawals.short_description = "Reject selected withdrawals"


# ======================
# ACCOUNT CONTROL LOG
# ======================

@admin.register(AccountControlLog)
class AccountControlLogAdmin(admin.ModelAdmin):
    list_display = ('admin', 'target_user', 'action', 'note', 'created_at')
    list_filter = ('action',)
    search_fields = ('admin__username', 'target_user__username')
    readonly_fields = ('created_at',)

    def has_add_permission(self, request):
        return False  # logs are auto-created, not manually added

    def has_change_permission(self, request, obj=None):
        return False  # immutable audit trail


# ======================
# NOTIFICATION
# ======================

@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('user', 'title', 'is_read', 'created_at')
    list_filter = ('is_read',)
    search_fields = ('user__username', 'title')
    readonly_fields = ('created_at',)


from .models import PaymentDetail

@admin.register(PaymentDetail)
class PaymentDetailAdmin(admin.ModelAdmin):
    list_display = ('label', 'payment_type', 'is_active', 'created_by', 'created_at')
    list_filter = ('payment_type', 'is_active')
    list_editable = ('is_active',)
    readonly_fields = ('created_at',)

    def save_model(self, request, obj, form, change):
        if not obj.created_by:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)