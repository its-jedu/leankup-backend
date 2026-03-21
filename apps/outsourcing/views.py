from django.shortcuts import render
from django.db import models
from rest_framework import viewsets, permissions, status, filters, generics
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from django.shortcuts import get_object_or_404
from django.contrib.auth.models import User
from .models import Task, Application, ChatMessage, Notification
from .serializers import (
    TaskSerializer, TaskDetailSerializer, ApplicationSerializer, 
    ChatMessageSerializer, NotificationSerializer
)
from apps.users.models import Profile
from apps.users.serializers import ProfileSerializer
from apps.core.permissions import IsCreatorOrReadOnly, IsAdminOrReadOnly

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
    
    def perform_create(self, serializer):
        serializer.save(creator=self.request.user)
    
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
    
    @action(detail=True, methods=['get'])
    def messages(self, request, pk=None):
        task = self.get_object()
        
        if not request.user.is_authenticated:
            return Response({'error': 'Authentication required'}, status=status.HTTP_401_UNAUTHORIZED)
        
        messages = task.messages.filter(
            models.Q(sender=request.user) | models.Q(receiver=request.user)
        )
        
        # Mark messages as read
        messages.filter(receiver=request.user, is_read=False).update(is_read=True)
        
        serializer = ChatMessageSerializer(messages, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def send_message(self, request, pk=None):
        task = self.get_object()
        content = request.data.get('content')
        
        if not content:
            return Response({'error': 'Message content is required'}, status=status.HTTP_400_BAD_REQUEST)
        
        serializer = ChatMessageSerializer(
            data={'task': task.id, 'content': content},
            context={'request': request}
        )
        
        if serializer.is_valid():
            message = serializer.save(sender=request.user)
            
            # Create notification for receiver
            Notification.objects.create(
                recipient=message.receiver,
                sender=request.user,
                task=task,
                notification_type='message',
                title=f'New Message about "{task.title}"',
                message=f'{request.user.username} sent you a message: {content[:100]}...'
            )
            
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

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