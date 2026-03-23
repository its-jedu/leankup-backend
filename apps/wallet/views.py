from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db import transaction, models
from django.shortcuts import get_object_or_404
from decimal import Decimal
import json
from .models import Wallet, Transaction
from .serializers import WalletSerializer, TransactionSerializer, WithdrawalSerializer
from apps.payments.services import PaystackService
import uuid


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


class WalletViewSet(viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = WalletSerializer
    
    def get_queryset(self):
        return Wallet.objects.filter(user=self.request.user)
    
    def get_object(self):
        wallet, created = Wallet.objects.get_or_create(user=self.request.user)
        return wallet
    
    @action(detail=False, methods=['get'])
    def balance(self, request):
        wallet = self.get_object()
        serializer = self.get_serializer(wallet)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def transactions(self, request):
        """Get transactions only for the authenticated user"""
        wallet = self.get_object()
        transactions = wallet.transactions.all()
        
        # Filter by type if specified
        transaction_type = request.query_params.get('type', None)
        if transaction_type:
            transactions = transactions.filter(transaction_type=transaction_type)
        
        # Return all transactions (no pagination for simplicity)
        serializer = TransactionSerializer(transactions, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['post'])
    def withdraw(self, request):
        serializer = WithdrawalSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        
        wallet = self.get_object()
        amount = serializer.validated_data['amount']
        
        with transaction.atomic():
            # Debit wallet
            if not wallet.debit(amount):
                return Response(
                    {'error': 'Insufficient balance'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Create transaction record with DEBIT type
            tx_ref = f"WITHDRAW_{uuid.uuid4().hex[:10].upper()}"
            
            # Convert Decimal to float for metadata
            metadata = serializer.validated_data.copy()
            metadata['amount'] = float(amount)
            
            transaction_record = Transaction.objects.create(
                wallet=wallet,
                amount=amount,
                transaction_type='debit',
                status='completed',
                reference=tx_ref,
                description=f"Withdrawal to {serializer.validated_data['bank_account_name']}",
                metadata=metadata
            )
            
            return Response({
                'message': 'Withdrawal initiated successfully',
                'reference': tx_ref,
                'amount': str(amount),
                'status': 'completed',
                'new_balance': str(wallet.balance)
            })
    
    @action(detail=False, methods=['post'])
    def fund(self, request):
        """Initialize wallet funding through Paystack"""
        amount = request.data.get('amount')
        
        if not amount:
            return Response(
                {'error': 'Amount is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            amount = Decimal(str(amount))
            if amount <= 0:
                raise ValueError
        except:
            return Response(
                {'error': 'Invalid amount'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Generate reference
        reference = f"WALLET_FUND_{request.user.id}_{uuid.uuid4().hex[:8].upper()}"
        
        # Create payment record
        from apps.payments.models import Payment
        payment = Payment.objects.create(
            user=request.user,
            amount=amount,
            payment_type='wallet_funding',
            reference=reference,
            metadata={
                'type': 'wallet_funding',
                'amount': str(amount)
            }
        )
        
        # Initialize Paystack payment
        paystack_service = PaystackService()
        result = paystack_service.initialize_payment(
            email=request.user.email,
            amount=amount,
            reference=reference,
            metadata={
                'payment_id': payment.id,
                'user_id': request.user.id,
                'type': 'wallet_funding'
            }
        )
        
        if result.get('status'):
            payment.paystack_reference = result['data']['reference']
            payment.save()
            
            return Response({
                'payment': {
                    'id': payment.id,
                    'reference': payment.reference,
                    'amount': str(payment.amount),
                    'status': payment.status
                },
                'authorization_url': result['data']['authorization_url'],
                'access_code': result['data']['access_code'],
                'reference': result['data']['reference']
            })
        
        payment.status = 'failed'
        payment.save()
        
        return Response(
            {'error': 'Failed to initialize payment'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    
    @action(detail=False, methods=['get'])
    def stats(self, request):
        """Get wallet statistics for the authenticated user"""
        wallet = self.get_object()
        transactions = wallet.transactions.all()
        
        total_deposits = transactions.filter(
            transaction_type='credit', 
            status='completed'
        ).aggregate(total=models.Sum('amount'))['total'] or Decimal('0')
        
        total_withdrawals = transactions.filter(
            transaction_type='debit', 
            status='completed'
        ).aggregate(total=models.Sum('amount'))['total'] or Decimal('0')
        
        pending_amount = transactions.filter(
            status='pending'
        ).aggregate(total=models.Sum('amount'))['total'] or Decimal('0')
        
        return Response({
            'balance': wallet.balance,
            'total_deposits': total_deposits,
            'total_withdrawals': total_withdrawals,
            'pending_amount': pending_amount,
            'transaction_count': transactions.count()
        })