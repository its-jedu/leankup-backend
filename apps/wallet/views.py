from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db import transaction
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
        wallet = self.get_object()
        transactions = wallet.transactions.all()
        
        # Filter by type
        transaction_type = request.query_params.get('type', None)
        if transaction_type:
            transactions = transactions.filter(transaction_type=transaction_type)
        
        page = self.paginate_queryset(transactions)
        if page is not None:
            serializer = TransactionSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        
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
            
            # Create transaction record
            tx_ref = f"WITHDRAW_{uuid.uuid4().hex[:10].upper()}"
            
            # Convert Decimal to float for metadata
            metadata = serializer.validated_data.copy()
            metadata['amount'] = float(amount)
            
            transaction_record = Transaction.objects.create(
                wallet=wallet,
                amount=amount,
                transaction_type='debit',
                status='completed',  # Change to completed for testing
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