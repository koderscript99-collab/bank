from .models import ActivationPayment, WithdrawalRequest, SupportTicket

def admin_counts(request):
    if request.user.is_authenticated and request.user.is_admin_user:
        return {
            'pending_activations_count': ActivationPayment.objects.filter(status='pending').count(),
            'pending_withdrawals_count': WithdrawalRequest.objects.filter(status='pending').count(),
            'open_tickets_count': SupportTicket.objects.filter(status='open').count(),
        }
    return {}