import uuid
from django.shortcuts import render, redirect
from django.db import models, transaction
from rest_framework import viewsets, permissions, status, filters, generics
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from django.shortcuts import get_object_or_404
from django.contrib.auth.models import User
from django.utils import timezone
from decimal import Decimal
from .models import Task, Application, ChatMessage, Notification, TaskPaymentEscrow, PaymentProof
from .serializers import (
    TaskSerializer, TaskDetailSerializer, ApplicationSerializer, 
    ChatMessageSerializer, NotificationSerializer, PaymentProofSerializer, PaymentProofVerifySerializer,
    TaskPaymentInitiateSerializer, EscrowStatusSerializer
)
from apps.users.models import Profile
from apps.users.serializers import ProfileSerializer
from apps.core.permissions import IsCreatorOrReadOnly
from apps.wallet.models import Wallet, Transaction
from apps.payments.models import Payment
from apps.payments.services import PaystackService


class TaskViewSet(viewsets.ModelViewSet):
    queryset = Task.objects.all()
    serializer_class = TaskSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly, IsCreatorOrReadOnly]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['category', 'status', 'creator']
    search_fields = ['title', 'description', 'location']
    ordering_fields = ['created_at', 'budget']
    
    def get_serializer_class(self):
        if self.action == 'retrieve':
            return TaskDetailSerializer
        return TaskSerializer
    
    def get_queryset(self):
        queryset = Task.objects.all()
        user_tasks = self.request.query_params.get('user_tasks', None)
        if user_tasks and self.request.user.is_authenticated:
            queryset = queryset.filter(creator=self.request.user)
        return queryset
    
    @transaction.atomic
    def create(self, request, *args, **kwargs):
        """Custom create method with automatic escrow creation"""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        budget = serializer.validated_data.get('budget')
        
        # Get or create user's wallet
        wallet, created = Wallet.objects.get_or_create(user=request.user)
        
        # Check if user has sufficient balance
        if wallet.balance < budget:
            # Return response with payment requirement
            return Response({
                'error': 'Insufficient wallet balance',
                'requires_payment': True,
                'required_amount': str(budget),
                'current_balance': str(wallet.balance),
                'shortfall': str(budget - wallet.balance),
                'message': f'You need ₦{budget:,.2f} to post this task. Your current balance is ₦{wallet.balance:,.2f}. Please fund your wallet to continue.',
                'task_data': serializer.validated_data
            }, status=status.HTTP_402_PAYMENT_REQUIRED)
        
        # Sufficient balance - create task and escrow
        with transaction.atomic():
            # Create the task
            task = serializer.save(creator=request.user)
            
            # Debit wallet
            wallet.debit(budget)
            
            # Create escrow record
            escrow = TaskPaymentEscrow.objects.create(
                task=task,
                amount=budget,
                poster_wallet=wallet,
                worker_wallet=None,
                status='funded',
                funded_at=timezone.now()
            )
            
            # Create transaction record for the debit
            Transaction.objects.create(
                wallet=wallet,
                amount=budget,
                transaction_type='debit',
                status='completed',
                reference=f"ESCROW_TASK_{task.id}_{uuid.uuid4().hex[:8].upper()}",
                description=f"Escrow created for task: {task.title}",
                metadata={
                    'task_id': task.id,
                    'task_title': task.title,
                    'escrow_id': escrow.id,
                    'type': 'escrow_creation'
                }
            )
            
            # Create notification for task creator
            Notification.objects.create(
                recipient=request.user,
                notification_type='task_completed',
                title='Task Created with Escrow',
                message=f'Your task "{task.title}" has been created. ₦{budget:,.2f} has been moved to escrow and will be held until task completion.',
                task=task
            )
        
        # Return success response with escrow info
        return Response({
            'message': 'Task created successfully with escrow funding',
            'task': TaskSerializer(task).data,
            'escrow': {
                'id': escrow.id,
                'amount': str(escrow.amount),
                'status': escrow.status,
                'funded_at': escrow.funded_at
            },
            'wallet_balance': str(wallet.balance)
        }, status=status.HTTP_201_CREATED)
    
    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    def apply(self, request, pk=None):
        task = self.get_object()
        
        if task.status != 'open':
            return Response(
                {'error': 'This task is not accepting applications'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if Application.objects.filter(task=task, applicant=request.user).exists():
            return Response(
                {'error': 'You have already applied to this task'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        serializer = ApplicationSerializer(
            data={'task': task.id, **request.data},
            context={'request': request}
        )
        
        if serializer.is_valid():
            application = serializer.save(applicant=request.user, task=task)
            
            # Create notification for task creator
            Notification.objects.create(
                recipient=task.creator,
                sender=request.user,
                task=task,
                application=application,
                notification_type='application',
                title=f'New Application for "{task.title}"',
                message=f'{request.user.username} has applied for your task.'
            )
            
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=True, methods=['get'])
    def applications(self, request, pk=None):
        task = self.get_object()
        
        if request.user != task.creator and not request.user.is_staff:
            return Response(
                {'error': 'You do not have permission to view these applications'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        applications = task.applications.all()
        serializer = ApplicationSerializer(applications, many=True, context={'request': request})
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'], url_path='my-applications')
    def my_applications(self, request):
        """Get all tasks the current user has applied to"""
        applications = Application.objects.filter(
            applicant=request.user
        ).select_related('task', 'task__creator').prefetch_related('task__messages')
        
        tasks_data = []
        for app in applications:
            task = app.task
            task_data = TaskSerializer(task).data
            task_data['application_status'] = app.status
            task_data['application_id'] = app.id
            task_data['applied_at'] = app.created_at
            task_data['application_message'] = app.message
            
            # Get messages between user and task creator
            messages = task.messages.filter(
                models.Q(sender=request.user, receiver=task.creator) |
                models.Q(sender=task.creator, receiver=request.user)
            ).order_by('created_at')
            
            task_data['messages'] = ChatMessageSerializer(messages, many=True).data
            task_data['unread_count'] = messages.filter(receiver=request.user, is_read=False).count()
            
            tasks_data.append(task_data)
        
        return Response(tasks_data)
    
    @action(detail=True, methods=['get'], url_path='messages', permission_classes=[permissions.IsAuthenticated])
    def get_messages(self, request, pk=None):
        """Get messages for a task"""
        task = self.get_object()
        
        if not request.user.is_authenticated:
            return Response({'error': 'Authentication required'}, status=status.HTTP_401_UNAUTHORIZED)
        
        # Check if user is authorized to view messages
        is_creator = task.creator == request.user
        is_accepted_applicant = task.applications.filter(
            applicant=request.user, status='accepted'
        ).exists()
        
        if not is_creator and not is_accepted_applicant:
            return Response(
                {'error': 'You are not authorized to view messages for this task. Only the task creator and accepted applicants can view messages.'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        messages = task.messages.filter(
            models.Q(sender=request.user) | models.Q(receiver=request.user)
        )
        
        # Mark messages as read
        messages.filter(receiver=request.user, is_read=False).update(is_read=True)
        
        serializer = ChatMessageSerializer(messages, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'], url_path='send-message', permission_classes=[permissions.IsAuthenticated])
    def send_message(self, request, pk=None):
        """Send a message for a task"""
        task = self.get_object()
        content = request.data.get('content')
        
        if not content:
            return Response({'error': 'Message content is required'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Check if user is authorized to send messages
        is_creator = task.creator == request.user
        user_application = task.applications.filter(applicant=request.user).first()
        is_accepted = user_application and user_application.status == 'accepted'
        
        if not is_creator and not is_accepted:
            return Response(
                {'error': 'You are not authorized to send messages for this task. Only the task creator and accepted applicants can chat.'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Determine receiver
        if is_creator:
            # Creator sending to the accepted applicant
            accepted_app = task.applications.filter(status='accepted').first()
            if not accepted_app:
                return Response(
                    {'error': 'No accepted applicant found. Please accept an application first.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            receiver = accepted_app.applicant
        else:
            # Applicant sending to creator
            receiver = task.creator
        
        # Create message
        message = ChatMessage.objects.create(
            task=task,
            sender=request.user,
            receiver=receiver,
            content=content
        )
        
        # Create notification for receiver
        Notification.objects.create(
            recipient=receiver,
            sender=request.user,
            task=task,
            notification_type='message',
            title=f'New Message about "{task.title}"',
            message=f'{request.user.username} sent you a message: {content[:100]}...'
        )
        
        serializer = ChatMessageSerializer(message)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    
    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    @transaction.atomic
    def fund_escrow(self, request, pk=None):
        """
        Fund the escrow for a task from poster's wallet
        """
        task = self.get_object()
        
        # Check if user is the task creator
        if request.user != task.creator:
            return Response(
                {'error': 'Only the task creator can fund the escrow'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Check if task is still open
        if task.status != 'open':
            return Response(
                {'error': f'Task is not open for funding. Current status: {task.status}'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check if escrow already exists and is funded
        if hasattr(task, 'escrow') and task.escrow.status == 'funded':
            return Response(
                {'error': 'Escrow is already funded for this task'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate amount
        serializer = TaskPaymentInitiateSerializer(
            data=request.data,
            context={'task': task}
        )
        serializer.is_valid(raise_exception=True)
        
        amount = serializer.validated_data['amount']
        
        # Get poster's wallet
        poster_wallet, _ = Wallet.objects.get_or_create(user=request.user)
        
        # Check if poster has sufficient balance
        if poster_wallet.balance < amount:
            return Response(
                {'error': f'Insufficient wallet balance. Current balance: ₦{poster_wallet.balance:,.2f}'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        with transaction.atomic():
            # Create or update escrow
            escrow, created = TaskPaymentEscrow.objects.get_or_create(
                task=task,
                defaults={
                    'amount': amount,
                    'poster_wallet': poster_wallet,
                    'worker_wallet': None,
                    'status': 'pending',
                }
            )
            
            if not created:
                escrow.amount = amount
                escrow.status = 'pending'
                escrow.save()
            
            # Debit poster's wallet
            poster_wallet.debit(amount)
            
            # Create transaction record
            Transaction.objects.create(
                wallet=poster_wallet,
                amount=amount,
                transaction_type='debit',
                status='completed',
                reference=f"ESCROW_FUND_{task.id}_{uuid.uuid4().hex[:8].upper()}",
                description=f"Escrow funding for task: {task.title}",
                metadata={
                    'task_id': task.id,
                    'task_title': task.title,
                    'escrow_id': escrow.id,
                    'type': 'escrow_funding'
                }
            )
            
            # Mark escrow as funded
            escrow.status = 'funded'
            escrow.funded_at = timezone.now()
            escrow.save()
            
            # Create notification for the task creator
            Notification.objects.create(
                recipient=request.user,
                notification_type='escrow_funded',
                title='Escrow Funded',
                message=f'You have successfully funded ₦{amount:,.2f} into escrow for task: {task.title}',
                task=task
            )
            
            return Response({
                'message': 'Escrow funded successfully',
                'escrow': {
                    'id': escrow.id,
                    'amount': str(escrow.amount),
                    'status': escrow.status,
                    'funded_at': escrow.funded_at
                },
                'wallet_balance': str(poster_wallet.balance)
            }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    @transaction.atomic
    def release_payment(self, request, pk=None):
        """
        Release escrow payment to the worker after task completion
        """
        task = self.get_object()
        
        # Check if user is the task creator
        if request.user != task.creator:
            return Response(
                {'error': 'Only the task creator can release payment'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Check if escrow exists and is funded
        if not hasattr(task, 'escrow'):
            return Response(
                {'error': 'No escrow found for this task'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        escrow = task.escrow
        
        if escrow.status != 'funded':
            return Response(
                {'error': f'Cannot release payment. Escrow status: {escrow.status}'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check if task is completed
        if task.status != 'completed':
            return Response(
                {'error': 'Task must be marked as completed before releasing payment'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get accepted applicant
        accepted_app = task.applications.filter(status='accepted').first()
        if not accepted_app:
            return Response(
                {'error': 'No accepted applicant found for this task'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        worker = accepted_app.applicant
        
        with transaction.atomic():
            # Get or create worker's wallet
            worker_wallet, _ = Wallet.objects.get_or_create(user=worker)
            escrow.worker_wallet = worker_wallet
            
            # Credit worker's wallet
            worker_wallet.credit(escrow.amount)
            
            # Create transaction record for worker
            Transaction.objects.create(
                wallet=worker_wallet,
                amount=escrow.amount,
                transaction_type='credit',
                status='completed',
                reference=f"ESCROW_RELEASE_{task.id}_{uuid.uuid4().hex[:8].upper()}",
                description=f"Payment received for task: {task.title}",
                metadata={
                    'task_id': task.id,
                    'task_title': task.title,
                    'escrow_id': escrow.id,
                    'type': 'escrow_release'
                }
            )
            
            # Update escrow status
            escrow.status = 'released'
            escrow.released_at = timezone.now()
            escrow.save()
            
            # Create notification for worker
            Notification.objects.create(
                recipient=worker,
                notification_type='payment_released',
                title='Payment Released',
                message=f'Payment of ₦{escrow.amount:,.2f} has been released for task: {task.title}',
                task=task
            )
            
            # Update user stats
            worker.profile.total_earned += escrow.amount
            worker.profile.total_tasks_completed += 1
            worker.profile.save()
            
            return Response({
                'message': 'Payment released successfully',
                'amount': str(escrow.amount),
                'recipient': worker.username,
                'task_status': task.status,
                'escrow_status': escrow.status
            }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    @transaction.atomic
    def refund_payment(self, request, pk=None):
        """
        Refund escrow payment back to poster (if task is cancelled)
        """
        task = self.get_object()
        
        # Check if user is the task creator
        if request.user != task.creator:
            return Response(
                {'error': 'Only the task creator can request refund'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Check if escrow exists
        if not hasattr(task, 'escrow'):
            return Response(
                {'error': 'No escrow found for this task'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        escrow = task.escrow
        
        # Can only refund if task is cancelled
        if task.status != 'cancelled':
            return Response(
                {'error': 'Task must be cancelled to request refund'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if escrow.status != 'funded':
            return Response(
                {'error': f'Cannot refund. Escrow status: {escrow.status}'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        with transaction.atomic():
            # Credit poster's wallet
            poster_wallet = escrow.poster_wallet
            poster_wallet.credit(escrow.amount)
            
            # Create transaction record
            Transaction.objects.create(
                wallet=poster_wallet,
                amount=escrow.amount,
                transaction_type='credit',
                status='completed',
                reference=f"ESCROW_REFUND_{task.id}_{uuid.uuid4().hex[:8].upper()}",
                description=f"Escrow refund for cancelled task: {task.title}",
                metadata={
                    'task_id': task.id,
                    'task_title': task.title,
                    'escrow_id': escrow.id,
                    'type': 'escrow_refund'
                }
            )
            
            # Update escrow status
            escrow.status = 'refunded'
            escrow.save()
            
            # Create notification
            Notification.objects.create(
                recipient=request.user,
                notification_type='task_completed',
                title='Escrow Refunded',
                message=f'₦{escrow.amount:,.2f} has been refunded to your wallet for task: {task.title}',
                task=task
            )
            
            return Response({
                'message': 'Escrow refunded successfully',
                'amount': str(escrow.amount),
                'wallet_balance': str(poster_wallet.balance),
                'escrow_status': escrow.status
            }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    def upload_payment_proof(self, request, pk=None):
        """
        Upload picture proof of payment
        """
        task = self.get_object()
        
        # Check if user is authorized
        is_poster = task.creator == request.user
        is_accepted_applicant = task.applications.filter(
            applicant=request.user, status='accepted'
        ).exists()
        
        if not is_poster and not is_accepted_applicant:
            return Response(
                {'error': 'You are not authorized to upload payment proof for this task'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Check if image was uploaded
        if 'image' not in request.FILES:
            return Response(
                {'error': 'Image file is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Prepare data for serializer
        data = {
            'task': task.id,
            'image': request.FILES['image'],
            'caption': request.data.get('caption', ''),
            'amount': request.data.get('amount')
        }
        
        # Validate amount
        if not data['amount']:
            return Response(
                {'error': 'Amount is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            amount_decimal = Decimal(str(data['amount']))
            if amount_decimal != task.budget:
                return Response(
                    {'error': f'Amount must match task budget: ₦{task.budget:,.2f}'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        except:
            return Response(
                {'error': 'Invalid amount format'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        serializer = PaymentProofSerializer(
            data=data,
            context={'request': request}
        )
        
        if serializer.is_valid():
            payment_proof = serializer.save(sender=request.user)
            
            # Create notification for receiver
            Notification.objects.create(
                recipient=payment_proof.receiver,
                sender=request.user,
                task=task,
                notification_type='payment_proof',
                title='Payment Proof Uploaded',
                message=f'{request.user.username} has uploaded a payment proof for task: {task.title}'
            )
            
            return Response({
                'message': 'Payment proof uploaded successfully',
                'payment_proof': serializer.data
            }, status=status.HTTP_201_CREATED)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    def verify_payment_proof(self, request, pk=None):
        """
        Verify or reject a payment proof
        """
        task = self.get_object()
        proof_id = request.data.get('proof_id')
        
        if not proof_id:
            return Response(
                {'error': 'proof_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check if user is authorized to verify (only receiver can verify)
        payment_proof = PaymentProof.objects.filter(
            id=proof_id,
            task=task
        ).first()
        
        if not payment_proof:
            return Response(
                {'error': 'Payment proof not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        if request.user != payment_proof.receiver:
            return Response(
                {'error': 'Only the receiver can verify this payment proof'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Verify serializer
        verify_serializer = PaymentProofVerifySerializer(data=request.data)
        verify_serializer.is_valid(raise_exception=True)
        
        status_choice = verify_serializer.validated_data['status']
        notes = verify_serializer.validated_data.get('notes', '')
        
        with transaction.atomic():
            # Update payment proof status
            payment_proof.status = status_choice
            payment_proof.verified_at = timezone.now()
            payment_proof.save()
            
            # If verified, mark task as completed
            if status_choice == 'verified':
                task.status = 'completed'
                task.save()
                
                # Also update escrow to release funds
                if hasattr(task, 'escrow'):
                    escrow = task.escrow
                    if escrow.status == 'funded':
                        # Get worker's wallet
                        accepted_app = task.applications.filter(status='accepted').first()
                        if accepted_app:
                            worker_wallet, _ = Wallet.objects.get_or_create(user=accepted_app.applicant)
                            escrow.worker_wallet = worker_wallet
                            
                            # Credit worker's wallet
                            worker_wallet.credit(escrow.amount)
                            
                            # Create transaction record
                            Transaction.objects.create(
                                wallet=worker_wallet,
                                amount=escrow.amount,
                                transaction_type='credit',
                                status='completed',
                                reference=f"ESCROW_RELEASE_{task.id}_{uuid.uuid4().hex[:8].upper()}",
                                description=f"Payment released after verification for task: {task.title}",
                                metadata={
                                    'task_id': task.id,
                                    'task_title': task.title,
                                    'escrow_id': escrow.id,
                                    'payment_proof_id': payment_proof.id,
                                    'type': 'escrow_release'
                                }
                            )
                            
                            # Update escrow status
                            escrow.status = 'released'
                            escrow.released_at = timezone.now()
                            escrow.payment_proof = payment_proof
                            escrow.save()
                            
                            # Update user stats
                            accepted_app.applicant.profile.total_earned += escrow.amount
                            accepted_app.applicant.profile.total_tasks_completed += 1
                            accepted_app.applicant.profile.save()
            
            # Create notification for sender
            status_text = 'verified' if status_choice == 'verified' else 'rejected'
            Notification.objects.create(
                recipient=payment_proof.sender,
                sender=request.user,
                task=task,
                notification_type='payment_verified',
                title=f'Payment Proof {status_text.title()}',
                message=f'Your payment proof for task: {task.title} has been {status_text}. {notes}'
            )
            
            return Response({
                'message': f'Payment proof {status_choice} successfully',
                'payment_proof': {
                    'id': payment_proof.id,
                    'status': payment_proof.status,
                    'verified_at': payment_proof.verified_at
                },
                'task_status': task.status,
                'notes': notes
            }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['get'], permission_classes=[permissions.IsAuthenticated])
    def payment_proofs(self, request, pk=None):
        """
        Get all payment proofs for a task
        """
        task = self.get_object()
        
        # Check if user is authorized to view
        is_poster = task.creator == request.user
        is_accepted_applicant = task.applications.filter(
            applicant=request.user, status='accepted'
        ).exists()
        
        if not is_poster and not is_accepted_applicant:
            return Response(
                {'error': 'You are not authorized to view payment proofs for this task'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        payment_proofs = task.payment_proofs.all()
        serializer = PaymentProofSerializer(payment_proofs, many=True, context={'request': request})
        
        return Response(serializer.data)

    @action(detail=True, methods=['get'], permission_classes=[permissions.IsAuthenticated])
    def escrow_info(self, request, pk=None):
        """
        Get escrow information for a task - only visible to poster
        """
        task = self.get_object()
        
        # Only the task creator can view escrow info
        if request.user != task.creator:
            return Response(
                {'error': 'Only the task creator can view escrow information'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        if not hasattr(task, 'escrow'):
            return Response({
                'has_escrow': False,
                'message': 'No escrow has been set up for this task yet'
            })
        
        escrow = task.escrow
        data = {
            'has_escrow': True,
            'id': escrow.id,
            'amount': str(escrow.amount),
            'status': escrow.status,
            'funded_at': escrow.funded_at,
            'released_at': escrow.released_at,
            'created_at': escrow.created_at,
            'can_fund': task.status == 'open' and escrow.status == 'pending',
            'can_release': task.status == 'completed' and escrow.status == 'funded',
            'can_refund': task.status == 'cancelled' and escrow.status == 'funded',
        }
        
        return Response(data)

    @action(detail=True, methods=['post'])
    def accept(self, request, pk=None):
        application = self.get_object()
        
        if request.user != application.task.creator and not request.user.is_staff:
            return Response(
                {'error': 'You do not have permission to accept this application'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        application.status = 'accepted'
        application.task.status = 'in_progress'
        application.task.save()
        application.save()
        
        # Update escrow with worker's wallet if escrow exists
        if hasattr(application.task, 'escrow'):
            escrow = application.task.escrow
            if escrow.status == 'funded':
                worker_wallet, _ = Wallet.objects.get_or_create(user=application.applicant)
                escrow.worker_wallet = worker_wallet
                escrow.save()
        
        # Reject other applications
        application.task.applications.exclude(id=application.id).update(status='rejected')
        
        # Create notification for applicant
        Notification.objects.create(
            recipient=application.applicant,
            sender=request.user,
            task=application.task,
            application=application,
            notification_type='application_accepted',
            title=f'Your application for "{application.task.title}" was accepted!',
            message=f'{request.user.username} has accepted your application. You can now start working on the task.'
        )
        
        return Response({'message': 'Application accepted'})


class ApplicationViewSet(viewsets.ModelViewSet):
    queryset = Application.objects.all()
    serializer_class = ApplicationSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        user = self.request.user
        if user.is_staff:
            return Application.objects.all()
        return Application.objects.filter(
            models.Q(applicant=user) | models.Q(task__creator=user)
        ).distinct()
    
    @action(detail=True, methods=['post'])
    def accept(self, request, pk=None):
        application = self.get_object()
        
        if request.user != application.task.creator and not request.user.is_staff:
            return Response(
                {'error': 'You do not have permission to accept this application'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        application.status = 'accepted'
        application.task.status = 'in_progress'
        application.task.save()
        application.save()
        
        # Reject other applications
        application.task.applications.exclude(id=application.id).update(status='rejected')
        
        # Create notification for applicant
        Notification.objects.create(
            recipient=application.applicant,
            sender=request.user,
            task=application.task,
            application=application,
            notification_type='application_accepted',
            title=f'Your application for "{application.task.title}" was accepted!',
            message=f'{request.user.username} has accepted your application. You can now start chatting about the task details.'
        )
        
        return Response({'message': 'Application accepted'})
    
    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        application = self.get_object()
        
        if request.user != application.task.creator and not request.user.is_staff:
            return Response(
                {'error': 'You do not have permission to reject this application'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        application.status = 'rejected'
        application.save()
        
        # Create notification for applicant
        Notification.objects.create(
            recipient=application.applicant,
            sender=request.user,
            task=application.task,
            application=application,
            notification_type='application_rejected',
            title=f'Application for "{application.task.title}" was not selected',
            message=f'Thank you for your interest. The task creator has chosen another applicant.'
        )
        
        return Response({'message': 'Application rejected'})


class NotificationViewSet(viewsets.ModelViewSet):
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        return Notification.objects.filter(recipient=self.request.user)
    
    @action(detail=False, methods=['post'])
    def mark_all_read(self, request):
        self.get_queryset().update(is_read=True)
        return Response({'message': 'All notifications marked as read'})
    
    @action(detail=True, methods=['post'])
    def mark_read(self, request, pk=None):
        notification = self.get_object()
        notification.is_read = True
        notification.save()
        return Response({'message': 'Notification marked as read'})


class UserStatsView(generics.RetrieveAPIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request, user_id=None):
        user_id = user_id or request.user.id
        user = get_object_or_404(User, id=user_id)
        profile, created = Profile.objects.get_or_create(user=user)
        
        # Calculate response rate
        total_received = Application.objects.filter(task__creator=user).count()
        total_responded = Application.objects.filter(task__creator=user).exclude(status='pending').count()
        
        if total_received > 0:
            profile.response_rate = (total_responded / total_received) * 100
            profile.save()
        
        serializer = ProfileSerializer(profile)
        return Response(serializer.data)