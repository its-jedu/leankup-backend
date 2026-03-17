from django.shortcuts import render

# Create your views here.
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db import transaction
from django.shortcuts import get_object_or_404
from .models import Payment
from .serializers import (
    PaymentSerializer, PaymentInitializeSerializer,
    PaymentVerifySerializer
)
from .services import PaystackService
from apps.wallet.models import Wallet, Transaction
from apps.fundraising.models import Campaign, Contribution
from apps.outsourcing.models import Task
import uuid

class PaymentViewSet(viewsets.GenericViewSet):
    queryset = Payment.objects.all()
    serializer_class = PaymentSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        return self.queryset.filter(user=self.request.user)
    
    @action(detail=False, methods=['post'])
    def initialize(self, request):
        serializer = PaymentInitializeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        # Generate reference
        reference = f"PAY_{uuid.uuid4().hex[:10].upper()}"
        
        # Create payment record
        payment = Payment.objects.create(
            user=request.user,
            amount=serializer.validated_data['amount'],
            payment_type=serializer.validated_data['payment_type'],
            reference=reference,
            metadata=serializer.validated_data
        )
        
        # Initialize Paystack payment
        paystack_service = PaystackService()
        result = paystack_service.initialize_payment(
            email=request.user.email,
            amount=serializer.validated_data['amount'],
            reference=reference,
            metadata={
                'payment_id': payment.id,
                'user_id': request.user.id,
                **serializer.validated_data
            }
        )
        
        if result.get('status'):
            payment.paystack_reference = result['data']['reference']
            payment.save()
            
            return Response({
                'payment': PaymentSerializer(payment).data,
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
    
    @action(detail=False, methods=['post'])
    def verify(self, request):
        serializer = PaymentVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        reference = serializer.validated_data['reference']
        
        try:
            payment = Payment.objects.get(reference=reference)
        except Payment.DoesNotExist:
            return Response(
                {'error': 'Payment not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Verify with Paystack
        paystack_service = PaystackService()
        result = paystack_service.verify_payment(reference)
        
        if result.get('status') and result['data']['status'] == 'success':
            with transaction.atomic():
                # Update payment status
                payment.status = 'success'
                payment.save()
                
                # Credit user's wallet
                wallet, _ = Wallet.objects.get_or_create(user=payment.user)
                wallet.credit(payment.amount)
                
                # Create transaction record
                Transaction.objects.create(
                    wallet=wallet,
                    amount=payment.amount,
                    transaction_type='credit',
                    status='completed',
                    reference=f"TX_{reference}",
                    description=f"Payment via Paystack: {payment.payment_type}",
                    metadata={'payment_reference': reference}
                )
                
                # Handle payment type specific logic
                if payment.payment_type == 'contribution':
                    self._handle_contribution_payment(payment)
                elif payment.payment_type == 'task_payment':
                    self._handle_task_payment(payment)
            
            return Response({
                'message': 'Payment verified successfully',
                'payment': PaymentSerializer(payment).data
            })
        
        return Response(
            {'error': 'Payment verification failed'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    def _handle_contribution_payment(self, payment):
        """Handle contribution payment logic"""
        campaign_id = payment.metadata.get('campaign_id')
        if campaign_id:
            campaign = Campaign.objects.get(id=campaign_id)
            
            # Update contribution status
            contribution = Contribution.objects.create(
                campaign=campaign,
                contributor=payment.user,
                amount=payment.amount,
                status='paid',
                payment_reference=payment.reference,
                message=payment.metadata.get('message', '')
            )
            
            # Update campaign raised amount
            campaign.raised_amount += payment.amount
            campaign.save()
    
    def _handle_task_payment(self, payment):
        """Handle task payment logic"""
        # Implement task payment logic
        pass
    
    @action(detail=False, methods=['post'])
    def webhook(self, request):
        """
        Paystack webhook handler
        """
        paystack_service = PaystackService()
        
        # Verify webhook signature
        if not paystack_service.verify_webhook_signature(request):
            return Response(status=status.HTTP_400_BAD_REQUEST)
        
        event = request.data.get('event')
        data = request.data.get('data')
        
        if event == 'charge.success':
            reference = data.get('reference')
            
            try:
                payment = Payment.objects.get(reference=reference)
                
                with transaction.atomic():
                    payment.status = 'success'
                    payment.save()
                    
                    # Credit wallet
                    wallet, _ = Wallet.objects.get_or_create(user=payment.user)
                    wallet.credit(payment.amount)
                    
                    # Handle contribution if applicable
                    if payment.payment_type == 'contribution':
                        campaign_id = payment.metadata.get('campaign_id')
                        if campaign_id:
                            campaign = Campaign.objects.get(id=campaign_id)
                            campaign.raised_amount += payment.amount
                            campaign.save()
                            
                            Contribution.objects.update_or_create(
                                payment_reference=reference,
                                defaults={
                                    'campaign': campaign,
                                    'contributor': payment.user,
                                    'amount': payment.amount,
                                    'status': 'paid',
                                    'message': payment.metadata.get('message', '')
                                }
                            )
                
            except Payment.DoesNotExist:
                logger.error(f"Payment not found for reference: {reference}")
        
        return Response({'status': 'success'})