from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db import transaction
from django.shortcuts import redirect
from decimal import Decimal
import uuid
import logging
from django.utils import timezone

from .models import Payment
from .serializers import (
    PaymentSerializer, PaymentInitializeSerializer,
    PaymentVerifySerializer
)
from .services import PaystackService
from apps.wallet.models import Wallet, Transaction
from apps.fundraising.models import Campaign, Contribution


logger = logging.getLogger(__name__)


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

        payment_type = serializer.validated_data['payment_type']
        amount = serializer.validated_data['amount']
        
        # Generate reference
        reference = f"PAY_{uuid.uuid4().hex[:10].upper()}"

        # Create payment record
        payment = Payment.objects.create(
            user=request.user,
            amount=amount,
            payment_type=payment_type,
            reference=reference,
            metadata=serializer.validated_data.get('metadata', {})
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
                'payment_type': payment_type,
                **serializer.validated_data.get('metadata', {})
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
                        # Check if payment is already processed
                        if payment.status == 'success':
                            logger.info(f"Payment {reference} already processed")
                            # Redirect based on payment type
                            if payment.payment_type == 'task_payment':
                                return redirect(f"http://localhost:3000/tasks/create?payment=success&reference={reference}")
                            elif payment.payment_type == 'wallet_funding':
                                return_url = payment.metadata.get('return_url', '/wallet')
                                return redirect(f"http://localhost:3000{return_url}?payment=success&reference={reference}")
                            else:
                                return redirect(f"http://localhost:3000/payment/success?reference={reference}")
                        
                        # Update payment status
                        payment.status = 'success'
                        payment.save()

                        # Handle payment type specific logic
                        if payment.payment_type == 'contribution':
                            self._handle_contribution_payment(payment, reference)
                        elif payment.payment_type == 'task_payment':
                            self._handle_task_payment(payment, reference)
                        elif payment.payment_type == 'wallet_funding':
                            self._handle_wallet_funding(payment, reference)
                        else:
                            # Credit wallet for other payments
                            wallet, _ = Wallet.objects.get_or_create(user=payment.user)
                            wallet.credit(payment.amount)

                            # Create transaction record with unique reference
                            tx_ref = f"TX_{reference}_{uuid.uuid4().hex[:8]}"
                            Transaction.objects.create(
                                wallet=wallet,
                                amount=payment.amount,
                                transaction_type='credit',
                                status='completed',
                                reference=tx_ref,
                                description=f"Payment via Paystack: {payment.payment_type}",
                                metadata={'payment_reference': reference}
                            )

                    # Redirect based on payment type
                    if payment.payment_type == 'task_payment':
                        return redirect(f"http://localhost:3000/tasks/create?payment=success&reference={reference}")
                    elif payment.payment_type == 'wallet_funding':
                        return_url = payment.metadata.get('return_url', '/wallet')
                        return redirect(f"http://localhost:3000{return_url}?payment=success&reference={reference}")
                    else:
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

        # Check if already processed
        if payment.status == 'success':
            return Response({
                'message': 'Payment already verified',
                'payment': PaymentSerializer(payment).data
            })

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
                elif payment.payment_type == 'task_payment':
                    self._handle_task_payment(payment, reference)
                elif payment.payment_type == 'wallet_funding':
                    self._handle_wallet_funding(payment, reference)
                else:
                    # Credit wallet for other payments
                    wallet, _ = Wallet.objects.get_or_create(user=payment.user)
                    wallet.credit(payment.amount)

                    # Create transaction record with unique reference
                    tx_ref = f"TX_{reference}_{uuid.uuid4().hex[:8]}"
                    Transaction.objects.create(
                        wallet=wallet,
                        amount=payment.amount,
                        transaction_type='credit',
                        status='completed',
                        reference=tx_ref,
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
                campaign.escrow_balance += payment.amount
                campaign.save()

                # Create transaction record with unique reference
                wallet, _ = Wallet.objects.get_or_create(user=payment.user)
                tx_ref = f"CONTRIB_{reference}_{uuid.uuid4().hex[:8]}"
                Transaction.objects.create(
                    wallet=wallet,
                    amount=payment.amount,
                    transaction_type='debit',
                    status='completed',
                    reference=tx_ref,
                    description=f"Contribution to campaign: {campaign.title}",
                    metadata={
                        'payment_reference': reference,
                        'campaign_id': campaign_id,
                        'campaign_title': campaign.title,
                        'contribution_id': contribution.id
                    }
                )

            except Campaign.DoesNotExist:
                logger.error(f"Campaign {campaign_id} not found for payment {reference}")

    def _handle_task_payment(self, payment, reference):
        """
        Handle task creation payment - credits wallet and creates task
        """
        from apps.outsourcing.models import Task, TaskPaymentEscrow, Notification
        from decimal import Decimal
        import uuid
        from django.utils import timezone
        
        try:
            with transaction.atomic():
                # Get task data from metadata
                task_data = payment.metadata.get('task_data', {})
                user = payment.user
                
                # Get or create wallet
                wallet, _ = Wallet.objects.get_or_create(user=user)
                
                # Credit wallet with the payment amount
                wallet.credit(payment.amount)
                
                # Create transaction record for credit with unique reference
                credit_ref = f"TASK_FUND_{reference}_{uuid.uuid4().hex[:8]}"
                Transaction.objects.create(
                    wallet=wallet,
                    amount=payment.amount,
                    transaction_type='credit',
                    status='completed',
                    reference=credit_ref,
                    description=f"Wallet funding for task creation",
                    metadata={
                        'payment_reference': reference,
                        'type': 'task_funding'
                    }
                )
                
                # Create the task
                task = Task.objects.create(
                    title=task_data.get('title'),
                    description=task_data.get('description'),
                    category=task_data.get('category'),
                    location=task_data.get('location'),
                    budget=Decimal(str(task_data.get('budget'))),
                    creator=user,
                    status='open'
                )
                
                # Debit wallet for escrow
                wallet.debit(task.budget)
                
                # Create escrow record
                escrow = TaskPaymentEscrow.objects.create(
                    task=task,
                    amount=task.budget,
                    poster_wallet=wallet,
                    worker_wallet=None,
                    status='funded',
                    funded_at=timezone.now()
                )
                
                # Create transaction record for escrow debit with unique reference
                escrow_ref = f"ESCROW_TASK_{task.id}_{uuid.uuid4().hex[:8]}"
                Transaction.objects.create(
                    wallet=wallet,
                    amount=task.budget,
                    transaction_type='debit',
                    status='completed',
                    reference=escrow_ref,
                    description=f"Escrow created for task: {task.title}",
                    metadata={
                        'task_id': task.id,
                        'task_title': task.title,
                        'escrow_id': escrow.id,
                        'type': 'escrow_creation'
                    }
                )
                
                # Create notification
                Notification.objects.create(
                    recipient=user,
                    notification_type='task_completed',
                    title='Task Created Successfully',
                    message=f'Your task "{task.title}" has been created. ₦{task.budget:,.2f} has been moved to escrow.',
                    task=task
                )
                
                logger.info(f"Task {task.id} created successfully from payment {reference}")
                
        except Exception as e:
            logger.error(f"Error handling task creation payment: {str(e)}")
            raise

    def _handle_wallet_funding(self, payment, reference):
        """
        Handle wallet funding payment - check if already processed
        """
        try:
            with transaction.atomic():
                # Get or create wallet
                wallet, _ = Wallet.objects.get_or_create(user=payment.user)
                
                # Check if this transaction was already processed
                existing_tx = Transaction.objects.filter(
                    metadata__payment_reference=reference,
                    transaction_type='credit'
                ).exists()
                
                if existing_tx:
                    logger.info(f"Wallet funding {reference} already processed, skipping")
                    return
                
                # Credit wallet
                wallet.credit(payment.amount)
                
                # Create transaction record with unique reference
                tx_ref = f"WALLET_FUND_{reference}_{uuid.uuid4().hex[:8]}"
                Transaction.objects.create(
                    wallet=wallet,
                    amount=payment.amount,
                    transaction_type='credit',
                    status='completed',
                    reference=tx_ref,
                    description=f"Wallet funding via Paystack",
                    metadata={
                        'payment_reference': reference,
                        'type': 'wallet_funding',
                        'payment_id': payment.id
                    }
                )
                
                logger.info(f"Wallet funded for user {payment.user.id} with amount {payment.amount}")
                
        except Exception as e:
            logger.error(f"Error handling wallet funding: {str(e)}")
            raise

    @action(detail=False, methods=['post'], permission_classes=[permissions.AllowAny])
    def webhook(self, request):
        """
        Paystack webhook handler
        """
        paystack_service = PaystackService()

        # Verify webhook signature
        if not paystack_service.verify_webhook_signature(request):
            logger.warning("Invalid webhook signature received")
            return Response(status=status.HTTP_400_BAD_REQUEST)

        event = request.data.get('event')
        data = request.data.get('data')

        logger.info(f"Webhook received: {event} - {data.get('reference')}")

        if event == 'charge.success':
            reference = data.get('reference')

            try:
                payment = Payment.objects.get(paystack_reference=reference)

                with transaction.atomic():
                    # Only process if payment is still pending
                    if payment.status == 'pending':
                        payment.status = 'success'
                        payment.save()

                        # Handle payment type specific logic
                        if payment.payment_type == 'contribution':
                            self._handle_contribution_payment(payment, reference)
                        elif payment.payment_type == 'task_payment':
                            self._handle_task_payment(payment, reference)
                        elif payment.payment_type == 'wallet_funding':
                            self._handle_wallet_funding(payment, reference)
                        else:
                            # Credit wallet for other payments
                            wallet, _ = Wallet.objects.get_or_create(user=payment.user)
                            wallet.credit(payment.amount)

                            tx_ref = f"TX_{reference}_{uuid.uuid4().hex[:8]}"
                            Transaction.objects.create(
                                wallet=wallet,
                                amount=payment.amount,
                                transaction_type='credit',
                                status='completed',
                                reference=tx_ref,
                                description=f"Payment via Paystack: {payment.payment_type}",
                                metadata={'payment_reference': reference}
                            )
                        
                        logger.info(f"Payment {reference} processed successfully via webhook")
                    else:
                        logger.info(f"Payment {reference} already processed, status: {payment.status}")

            except Payment.DoesNotExist:
                logger.error(f"Payment {reference} not found in webhook")
                # Don't fail the webhook - return 200 to prevent retries
                return Response({'status': 'success'})

        return Response({'status': 'success'})

    @action(detail=False, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    def fund_wallet(self, request):
        """
        Fund wallet through Paystack
        """
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