import uuid
import secrets
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
from .models import Task, Application, ChatMessage, Notification, TaskPaymentEscrow, PaymentProof, EscrowRelease
from .serializers import (
    TaskSerializer, TaskDetailSerializer, ApplicationSerializer, 
    ChatMessageSerializer, NotificationSerializer, PaymentProofSerializer, PaymentProofVerifySerializer,
    TaskPaymentInitiateSerializer, EscrowStatusSerializer
)
from apps.users.models import Profile
from apps.users.serializers import ProfileSerializer
from apps.core.permissions import IsCreatorOrReadOnly
from apps.wallet.models import Wallet, Transaction


class TaskViewSet(viewsets.ModelViewSet):
    queryset = Task.objects.filter(deleted_at__isnull=True)
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
        queryset = Task.objects.filter(deleted_at__isnull=True)
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
            return Response({
                'error': 'Insufficient wallet balance',
                'requires_payment': True,
                'required_amount': str(budget),
                'current_balance': str(wallet.balance),
                'shortfall': str(budget - wallet.balance),
                'message': f'You need ₦{budget:,.2f} to post this task. Your current balance is ₦{wallet.balance:,.2f}. Please fund your wallet to continue.',
                'task_data': serializer.validated_data
            }, status=status.HTTP_402_PAYMENT_REQUIRED)
        
        with transaction.atomic():
            # Generate completion key
            completion_key = secrets.token_urlsafe(32)
            
            # Create the task
            task = serializer.save(
                creator=request.user,
                completion_key=completion_key
            )
            
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
            
            # Create transaction record
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
                    'completion_key': completion_key,
                    'type': 'escrow_creation'
                }
            )
            
            # Create notification
            Notification.objects.create(
                recipient=request.user,
                notification_type='escrow_funded',
                title='Task Created with Escrow',
                message=f'Your task "{task.title}" has been created. ₦{budget:,.2f} has been moved to escrow. Keep your completion key safe: {completion_key}',
                task=task
            )
        
        return Response({
            'message': 'Task created successfully',
            'task': TaskSerializer(task).data,
            'completion_key': completion_key,
            'escrow': {
                'id': escrow.id,
                'amount': str(escrow.amount),
                'status': escrow.status,
                'funded_at': escrow.funded_at
            },
            'wallet_balance': str(wallet.balance)
        }, status=status.HTTP_201_CREATED)
    
    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    def delete_task(self, request, pk=None):
        """Soft delete a task - only if no accepted applications or not in progress"""
        task = self.get_object()
        
        if request.user != task.creator:
            return Response(
                {'error': 'Only the task creator can delete this task'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Check if task can be deleted
        if task.status == 'in_progress':
            return Response(
                {'error': 'Cannot delete a task that is in progress'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check if there are accepted applications
        accepted_apps = task.applications.filter(status='accepted')
        if accepted_apps.exists():
            return Response(
                {'error': 'Cannot delete a task with accepted applications'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        with transaction.atomic():
            # Soft delete the task
            task.deleted_at = timezone.now()
            task.status = 'cancelled'
            task.save()
            
            # If escrow exists and is funded, refund to poster
            if hasattr(task, 'escrow') and task.escrow.status == 'funded':
                escrow = task.escrow
                poster_wallet = escrow.poster_wallet
                poster_wallet.credit(escrow.amount)
                
                Transaction.objects.create(
                    wallet=poster_wallet,
                    amount=escrow.amount,
                    transaction_type='credit',
                    status='completed',
                    reference=f"ESCROW_REFUND_DELETE_{task.id}_{uuid.uuid4().hex[:8].upper()}",
                    description=f"Escrow refund for deleted task: {task.title}",
                    metadata={
                        'task_id': task.id,
                        'task_title': task.title,
                        'escrow_id': escrow.id,
                        'type': 'escrow_refund'
                    }
                )
                
                escrow.status = 'refunded'
                escrow.save()
                
                Notification.objects.create(
                    recipient=request.user,
                    notification_type='task_completed',
                    title='Task Deleted - Escrow Refunded',
                    message=f'Your task "{task.title}" has been deleted. ₦{escrow.amount:,.2f} has been refunded to your wallet.',
                    task=task
                )
        
        return Response({
            'message': 'Task deleted successfully',
            'refunded': str(escrow.amount) if hasattr(task, 'escrow') else None
        }, status=status.HTTP_200_OK)
    
    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    def mark_complete(self, request, pk=None):
        """
        Mark task as complete using completion key (BYbit style)
        Both poster and worker can mark complete with the key
        Escrow is released only when BOTH parties have confirmed
        """
        task = self.get_object()
        completion_key = request.data.get('completion_key')
        
        if not completion_key:
            return Response(
                {'error': 'Completion key is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Verify completion key
        if task.completion_key != completion_key:
            return Response(
                {'error': 'Invalid completion key'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if task.status == 'completed':
            return Response(
                {'error': 'Task is already completed'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if task.status != 'in_progress':
            return Response(
                {'error': f'Task must be in progress to complete. Current status: {task.status}'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get the user marking completion
        user = request.user
        
        # Check if user is part of this task
        is_poster = task.creator == user
        is_accepted_applicant = task.applications.filter(applicant=user, status='accepted').exists()
        
        if not is_poster and not is_accepted_applicant:
            return Response(
                {'error': 'You are not authorized to complete this task'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        with transaction.atomic():
            # Track completion status before update
            poster_completed_before = task.completed_by_poster
            worker_completed_before = task.completed_by_worker
            
            # Update task completion status
            if is_poster and not task.completed_by_poster:
                task.completed_by_poster = True
                task.poster_completed_at = timezone.now()
            elif is_accepted_applicant and not task.completed_by_worker:
                task.completed_by_worker = True
                task.worker_completed_at = timezone.now()
            
            task.save()
            
            # Create notification for the other party
            if is_poster and not poster_completed_before:
                # Notify workers that poster marked complete
                for app in task.applications.filter(status='accepted'):
                    Notification.objects.create(
                        recipient=app.applicant,
                        sender=user,
                        task=task,
                        notification_type='task_completed',
                        title='Task Marked Complete by Poster',
                        message=f'{user.username} has marked task "{task.title}" as complete. Please confirm completion to release escrow.'
                    )
            elif is_accepted_applicant and not worker_completed_before:
                # Notify poster that worker marked complete
                Notification.objects.create(
                    recipient=task.creator,
                    sender=user,
                    task=task,
                    notification_type='task_completed',
                    title='Task Marked Complete by Worker',
                    message=f'{user.username} has marked task "{task.title}" as complete. Please confirm to release escrow.'
                )
            
            # Check if both parties have now completed
            if task.completed_by_poster and task.completed_by_worker:
                # Both parties confirmed - release escrow
                return self._release_escrow(task)
        
        # Return waiting for other party
        return Response({
            'message': 'Task marked as complete. Waiting for other party to confirm.',
            'task_status': task.status,
            'poster_completed': task.completed_by_poster,
            'worker_completed': task.completed_by_worker
        }, status=status.HTTP_200_OK)
    
    def _release_escrow(self, task):
        """Release escrow to all accepted workers"""
        escrow = task.escrow
        
        if escrow.status != 'funded':
            return Response({
                'error': f'Cannot release escrow. Status: {escrow.status}'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        accepted_apps = task.applications.filter(status='accepted')
        
        if not accepted_apps.exists():
            return Response({
                'error': 'No accepted applicants found'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Calculate amount per worker (equal distribution)
        amount_per_worker = escrow.amount / len(accepted_apps)
        
        with transaction.atomic():
            for app in accepted_apps:
                worker_wallet, _ = Wallet.objects.get_or_create(user=app.applicant)
                
                # Credit worker's wallet
                worker_wallet.credit(amount_per_worker)
                
                # Create transaction record
                tx_ref = f"ESCROW_RELEASE_{task.id}_{app.id}_{uuid.uuid4().hex[:8].upper()}"
                Transaction.objects.create(
                    wallet=worker_wallet,
                    amount=amount_per_worker,
                    transaction_type='credit',
                    status='completed',
                    reference=tx_ref,
                    description=f"Payment for task: {task.title}",
                    metadata={
                        'task_id': task.id,
                        'task_title': task.title,
                        'escrow_id': escrow.id,
                        'application_id': app.id,
                        'type': 'escrow_release'
                    }
                )
                
                # Create EscrowRelease record
                EscrowRelease.objects.create(
                    escrow=escrow,
                    wallet=worker_wallet,
                    amount=amount_per_worker,
                    completion_key_used=app.completion_key_used or task.completion_key,
                    completed_by=app.applicant
                )
                
                app.escrow_released = True
                app.save()
                
                # Create notification for worker
                Notification.objects.create(
                    recipient=app.applicant,
                    sender=task.creator,
                    task=task,
                    notification_type='payment_released',
                    title='Payment Released',
                    message=f'Payment of ₦{amount_per_worker:,.2f} has been released for task: {task.title}'
                )
                
                # Update user stats
                app.applicant.profile.total_earned += amount_per_worker
                app.applicant.profile.total_tasks_completed += 1
                app.applicant.profile.save()
            
            # Update escrow and task status
            escrow.status = 'released'
            escrow.released_at = timezone.now()
            escrow.save()
            
            task.status = 'completed'
            task.completed_at = timezone.now()
            task.save()
        
        return Response({
            'message': f'Escrow released to {len(accepted_apps)} worker(s)',
            'amount_per_worker': str(amount_per_worker),
            'total_workers': len(accepted_apps),
            'task_status': task.status,
            'escrow_status': escrow.status
        }, status=status.HTTP_200_OK)
    
    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    def apply(self, request, pk=None):
        """Apply for a task - prevent duplicate applications"""
        task = self.get_object()
        
        # Check if user has already applied
        if Application.objects.filter(task=task, applicant=request.user).exists():
            return Response(
                {'error': 'You have already applied to this task', 'already_applied': True},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if task.status != 'open':
            return Response(
                {'error': 'This task is not accepting applications'},
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
                message=f'{request.user.username} has applied for your task. Click to view their profile and application.'
            )
            
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=True, methods=['get'], permission_classes=[permissions.IsAuthenticated])
    def applications(self, request, pk=None):
        """Get applications for a task with applicant profiles"""
        task = self.get_object()
        
        if request.user != task.creator and not request.user.is_staff:
            return Response(
                {'error': 'You do not have permission to view these applications'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        applications = task.applications.all()
        
        # Add profile data to each application
        result = []
        for app in applications:
            app_data = ApplicationSerializer(app).data
            profile = Profile.objects.get(user=app.applicant)
            app_data['applicant_profile'] = ProfileSerializer(profile).data
            app_data['applicant_stats'] = {
                'total_completed_tasks': profile.total_tasks_completed,
                'total_earned': str(profile.total_earned),
                'response_rate': profile.response_rate
            }
            result.append(app_data)
        
        return Response(result)
    
    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    def accept_applications(self, request, pk=None):
        """Accept multiple applications for a task"""
        task = self.get_object()
        
        if request.user != task.creator:
            return Response(
                {'error': 'Only the task creator can accept applications'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        if task.status != 'open':
            return Response(
                {'error': f'Task must be open to accept applications. Current status: {task.status}'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        application_ids = request.data.get('application_ids', [])
        
        if not application_ids:
            return Response(
                {'error': 'No application IDs provided'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        with transaction.atomic():
            accepted_count = 0
            for app_id in application_ids:
                try:
                    app = Application.objects.get(id=app_id, task=task)
                    if app.status == 'pending':
                        app.status = 'accepted'
                        app.save()
                        accepted_count += 1
                        
                        # Create notification for applicant
                        Notification.objects.create(
                            recipient=app.applicant,
                            sender=request.user,
                            task=task,
                            application=app,
                            notification_type='application_accepted',
                            title=f'Application Accepted for "{task.title}"',
                            message=f'{request.user.username} has accepted your application. You can now start working on the task.'
                        )
                except Application.DoesNotExist:
                    continue
            
            # Update task status if at least one application accepted
            if accepted_count > 0:
                task.status = 'in_progress'
                task.save()
        
        return Response({
            'message': f'Accepted {accepted_count} application(s)',
            'accepted_count': accepted_count
        }, status=status.HTTP_200_OK)
    
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
            'completion_key': task.completion_key,
            'poster_completed': task.completed_by_poster,
            'worker_completed': task.completed_by_worker,
            'can_complete': task.status == 'in_progress' and not (task.completed_by_poster and task.completed_by_worker),
            'can_fund': task.status == 'open' and escrow.status == 'pending',
            'can_release': task.status == 'completed' and escrow.status == 'funded',
            'can_refund': task.status == 'cancelled' and escrow.status == 'funded',
        }
        
        return Response(data)


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
    
    @action(detail=True, methods=['get'])
    def profile_summary(self, request, pk=None):
        """Get applicant profile summary for poster"""
        application = self.get_object()
        
        # Check if user is task creator
        if request.user != application.task.creator:
            return Response(
                {'error': 'Only the task creator can view applicant profiles'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        profile = Profile.objects.get(user=application.applicant)
        data = {
            'user': {
                'id': application.applicant.id,
                'username': application.applicant.username,
                'first_name': application.applicant.first_name,
                'last_name': application.applicant.last_name,
                'email': application.applicant.email,
                'date_joined': application.applicant.date_joined,
            },
            'profile': ProfileSerializer(profile).data,
            'application': {
                'id': application.id,
                'message': application.message,
                'portfolio_link': application.portfolio_link,
                'proposed_budget': application.proposed_budget,
                'created_at': application.created_at,
            },
            'stats': {
                'total_completed_tasks': profile.total_tasks_completed,
                'total_earned': str(profile.total_earned),
                'response_rate': profile.response_rate,
                'total_tasks_posted': profile.total_tasks_posted,
                'total_campaigns_created': profile.total_campaigns_created,
            }
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
    
    @action(detail=False, methods=['get'])
    def recent(self, request):
        """Get top 5 latest notifications"""
        notifications = self.get_queryset()[:5]
        serializer = self.get_serializer(notifications, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def unread_count(self, request):
        """Get count of unread notifications"""
        count = self.get_queryset().filter(is_read=False).count()
        return Response({'unread_count': count})
    
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