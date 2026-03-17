from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db import transaction
from django.shortcuts import redirect
from decimal import Decimal
from .models import Payment
from .serializers import (
    PaymentSerializer, PaymentInitializeSerializer,
    PaymentVerifySerializer
)
from .services import PaystackService
from apps.wallet.models import Wallet, Transaction
from apps.fundraising.models import Campaign, Contribution
import uuid


class PaymentViewSet(viewsets.GenericViewSet):
    queryset = Payment.objects.all()
    serializer_class = PaymentSerializer
    permission_classes = [permissions.IsAuthenticated]  # Default requires auth

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

    @action(detail=False, methods=['get', 'post'], permission_classes=[permissions.AllowAny])
    def verify(self, request):
        """
        Handle Paystack redirect (GET) and manual verification (POST)
        """
        # Handle GET request from Paystack redirect
        if request.method == 'GET':
            reference = request.query_params.get('reference')
            if not reference:
                return Response(
                    {'error': 'No reference provided'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Verify with Paystack
            paystack_service = PaystackService()
            result = paystack_service.verify_payment(reference)

            if result.get('status') and result['data']['status'] == 'success':
                try:
                    payment = Payment.objects.get(paystack_reference=reference)

                    with transaction.atomic():
                        # Update payment status
                        payment.status = 'success'
                        payment.save()

                        # Handle payment type specific logic
                        if payment.payment_type == 'contribution':
                            self._handle_contribution_payment(payment, reference)
                        else:
                            # Only credit wallet for non-contribution payments
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

                    # Redirect to frontend success page
                    return redirect(f"http://localhost:3000/payment/success?reference={reference}")

                except Payment.DoesNotExist:
                    return Response(
                        {'error': 'Payment not found'},
                        status=status.HTTP_404_NOT_FOUND
                    )

            # Redirect to frontend failure page
            return redirect(f"http://localhost:3000/payment/failed?reference={reference}")

        # Handle POST request for manual verification
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

                # Handle payment type specific logic
                if payment.payment_type == 'contribution':
                    self._handle_contribution_payment(payment, reference)
                else:
                    # Only credit wallet for non-contribution payments
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

            return Response({
                'message': 'Payment verified successfully',
                'payment': PaymentSerializer(payment).data
            })

        return Response(
            {'error': 'Payment verification failed'},
            status=status.HTTP_400_BAD_REQUEST
        )

    def _handle_contribution_payment(self, payment, reference):
        """
        Handle contribution payment logic with escrow
        Money goes to campaign escrow, NOT to contributor's wallet
        """
        campaign_id = payment.metadata.get('campaign_id')
        message = payment.metadata.get('message', '')
        is_anonymous = payment.metadata.get('is_anonymous', False)

        if campaign_id:
            try:
                campaign = Campaign.objects.get(id=campaign_id)

                # Create contribution record
                contribution = Contribution.objects.create(
                    campaign=campaign,
                    contributor=payment.user,
                    amount=payment.amount,
                    status='paid',
                    payment_reference=reference,
                    message=message,
                    is_anonymous=is_anonymous
                )

                # Update campaign raised amount AND escrow balance
                campaign.raised_amount += payment.amount
                campaign.escrow_balance += payment.amount  # Add to escrow
                campaign.save()

                # Create a transaction record for the contributor (for their records)
                wallet, _ = Wallet.objects.get_or_create(user=payment.user)
                Transaction.objects.create(
                    wallet=wallet,
                    amount=payment.amount,
                    transaction_type='debit',  # It's a debit from their perspective
                    status='completed',
                    reference=f"CONTRIB_{reference}",
                    description=f"Contribution to campaign: {campaign.title}",
                    metadata={
                        'payment_reference': reference,
                        'campaign_id': campaign_id,
                        'campaign_title': campaign.title,
                        'contribution_id': contribution.id
                    }
                )

            except Campaign.DoesNotExist:
                # Log error but don't fail the payment
                import logging
                logging.error(f"Campaign {campaign_id} not found for payment {reference}")

    @action(detail=False, methods=['post'], permission_classes=[permissions.AllowAny])
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
                payment = Payment.objects.get(paystack_reference=reference)

                with transaction.atomic():
                    payment.status = 'success'
                    payment.save()

                    # Handle contribution with escrow
                    if payment.payment_type == 'contribution':
                        self._handle_contribution_payment(payment, reference)
                    else:
                        # Credit wallet for non-contribution payments
                        wallet, _ = Wallet.objects.get_or_create(user=payment.user)
                        wallet.credit(payment.amount)

                        Transaction.objects.create(
                            wallet=wallet,
                            amount=payment.amount,
                            transaction_type='credit',
                            status='completed',
                            reference=f"TX_{reference}",
                            description=f"Payment via Paystack: {payment.payment_type}",
                            metadata={'payment_reference': reference}
                        )

            except Payment.DoesNotExist:
                # Log but don't fail
                import logging
                logging.error(f"Payment {reference} not found in webhook")

        return Response({'status': 'success'})